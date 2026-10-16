from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import stat
import struct
import tempfile
import zipfile
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError

SCHEMA_VERSION = 1
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP"}


@dataclass(frozen=True)
class EvidenceItem:
    original_name: str
    packaged_path: str
    sha256: str
    bytes: int
    image_format: str
    width: int
    height: int


def normalize_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    candidate = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError("captured-at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("captured-at must include a UTC offset or Z")
    return parsed.isoformat(timespec="seconds")


def create_pack(
    sources: list[Path],
    output: Path,
    *,
    url: str,
    title: str = "",
    browser: str = "",
    captured_at: str | None = None,
    notes: list[str] | None = None,
    make_zip: bool = False,
) -> Path:
    if not sources:
        raise ValueError("at least one screenshot is required")
    destination = output.expanduser().resolve()
    if destination.exists():
        raise ValueError(f"output already exists: {destination}")
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ValueError("url must use http:// or https://")

    resolved_sources = [source.expanduser().resolve() for source in sources]
    for source in resolved_sources:
        if not source.is_file():
            raise ValueError(f"screenshot not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        screenshots_dir = staging / "screenshots"
        screenshots_dir.mkdir()
        items: list[EvidenceItem] = []
        used_names: set[str] = set()
        for index, source in enumerate(resolved_sources, start=1):
            image_format, width, height = inspect_image(source)
            safe_name = unique_name(source.name, used_names, index)
            target = screenshots_dir / safe_name
            shutil.copyfile(source, target)
            items.append(
                EvidenceItem(
                    original_name=source.name,
                    packaged_path=f"screenshots/{safe_name}",
                    sha256=sha256_file(target),
                    bytes=target.stat().st_size,
                    image_format=image_format,
                    width=width,
                    height=height,
                )
            )

        manifest: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "capture": {
                "url": url,
                "title": title,
                "browser": browser,
                "capturedAt": normalize_timestamp(captured_at),
                "notes": notes or [],
            },
            "items": [asdict(item) for item in items],
            "limitations": [
                "The pack proves the packaged files and recorded metadata, not what was live at the URL.",
                "Capture metadata is supplied by the operator and is not independently authenticated.",
            ],
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (staging / "index.html").write_text(render_html(manifest), encoding="utf-8")
        staging.replace(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if make_zip:
        zip_path = destination.with_suffix(".zip")
        if zip_path.exists():
            raise ValueError(f"ZIP output already exists: {zip_path}")
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(destination.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(destination.parent))
    return destination


def verify_pack(pack: Path) -> list[str]:
    if pack.is_file():
        return verify_zip(pack)
    root = pack.expanduser().resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        return ["manifest.json is missing"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"manifest.json is unreadable: {exc}"]
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        return ["manifest schema version is unsupported"]

    errors: list[str] = []
    items = manifest.get("items")
    if not isinstance(items, list):
        return ["manifest items must be a list"]
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            errors.append(f"item {position} is not an object")
            continue
        relative = str(item.get("packaged_path", ""))
        candidate = (root / relative).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"item {position} escapes the pack directory")
            continue
        if not candidate.is_file():
            errors.append(f"{relative or f'item {position}'} is missing")
            continue
        if candidate.stat().st_size != item.get("bytes"):
            errors.append(f"{relative} size does not match")
        if sha256_file(candidate) != item.get("sha256"):
            errors.append(f"{relative} SHA-256 does not match")
        try:
            image_format, width, height = inspect_image(candidate)
        except ValueError as exc:
            errors.append(f"{relative} is not a valid supported image: {exc}")
            continue
        if [image_format, width, height] != [
            item.get("image_format"),
            item.get("width"),
            item.get("height"),
        ]:
            errors.append(f"{relative} image metadata does not match")
    return errors


def _safe_zip_path(name: str) -> bool:
    parts = name.rstrip("/").split("/")
    return (
        bool(name)
        and not any(part in {"", ".", ".."} or ":" in part for part in parts)
        and "\\" not in name
        and "\x00" not in name
    )


def _read_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, limit: int) -> bytes:
    with archive.open(member) as handle:
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise ValueError(f"{member.filename} exceeds decompression limit")
    return content


def _preflight_zip(
    source: BinaryIO,
    *,
    max_archive_bytes: int,
    max_directory_bytes: int,
    max_entry_metadata_bytes: int,
    max_members: int,
) -> tuple[int, int]:
    """Bound classic ZIP metadata before ZipFile eagerly materializes it."""
    source.seek(0, 2)
    size = source.tell()
    if size > max_archive_bytes:
        raise ValueError("ZIP archive size exceeds limit")
    if size < 22:
        raise ValueError("ZIP end record is missing")
    source.seek(size - 22)
    end = source.read(22)
    end_position = size - 22
    if end[:4] != b"PK\x05\x06" or end[20:22] != b"\x00\x00":
        # Archive comments are at most 65,535 bytes in a classic ZIP.
        tail_size = min(size, 65535 + 22)
        source.seek(size - tail_size)
        tail = source.read(tail_size)
        position = tail.rfind(b"PK\x05\x06")
        if position < 0 or len(tail) - position < 22:
            raise ValueError("ZIP end record is missing")
        end = tail[position : position + 22]
        comment_size = struct.unpack_from("<H", end, 20)[0]
        if position + 22 + comment_size != len(tail):
            raise ValueError("ZIP end record comment length is invalid")
        end_position = size - tail_size + position
    _, disk, directory_disk, disk_count, count, directory_size, offset, _ = struct.unpack(
        "<4s4H2LH", end
    )
    if count == 65535 or directory_size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
        raise ValueError("ZIP64 directory records are unsupported")
    if disk or directory_disk or disk_count != count:
        raise ValueError("multi-disk ZIP directories are unsupported")
    if count > max_members:
        raise ValueError("ZIP member count exceeds limit")
    if directory_size > max_directory_bytes:
        raise ValueError("ZIP central-directory metadata exceeds limit")
    if end_position >= 20:
        source.seek(end_position - 20)
        if source.read(20).startswith(b"PK\x06\x07"):
            raise ValueError("ZIP64 locator is unsupported")
    if offset + directory_size != end_position:
        raise ValueError("ZIP central-directory layout is unsupported")
    source.seek(offset)
    consumed = 0
    actual_count = 0
    while consumed < directory_size:
        if actual_count >= max_members:
            raise ValueError("ZIP member count exceeds limit")
        if directory_size - consumed < 46:
            raise ValueError("ZIP central directory is truncated")
        header = source.read(46)
        if len(header) != 46 or header[:4] != b"PK\x01\x02":
            raise ValueError("ZIP central-directory header is invalid")
        method = struct.unpack_from("<H", header, 10)[0]
        if method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ValueError("unsupported ZIP compression; only Stored and Deflate are accepted")
        name_size, extra_size, comment_size = struct.unpack_from("<3H", header, 28)
        metadata_size = name_size + extra_size + comment_size
        if metadata_size > max_entry_metadata_bytes:
            raise ValueError("ZIP per-entry metadata exceeds limit")
        consumed += 46 + metadata_size
        if consumed > directory_size:
            raise ValueError("ZIP central-directory entry is truncated")
        source.seek(metadata_size, 1)
        actual_count += 1
    if actual_count != count:
        raise ValueError("ZIP central-directory member count does not match")
    source.seek(0)
    return offset, actual_count


def verify_zip(
    pack: Path,
    *,
    max_total_bytes: int = 256 * 1024 * 1024,
    max_member_bytes: int = 64 * 1024 * 1024,
    max_members: int = 4096,
    max_archive_bytes: int = 512 * 1024 * 1024,
    max_directory_bytes: int = 8 * 1024 * 1024,
    max_entry_metadata_bytes: int = 64 * 1024,
) -> list[str]:
    """Verify a supplied ZIP in memory, without extracting or opening its HTML."""
    if (
        min(
            max_total_bytes,
            max_member_bytes,
            max_members,
            max_archive_bytes,
            max_directory_bytes,
            max_entry_metadata_bytes,
        )
        <= 0
    ):
        raise ValueError("ZIP limits must be positive")
    try:
        with pack.open("rb") as source:
            directory = _preflight_zip(
                source,
                max_archive_bytes=max_archive_bytes,
                max_directory_bytes=max_directory_bytes,
                max_entry_metadata_bytes=max_entry_metadata_bytes,
                max_members=max_members,
            )
            with zipfile.ZipFile(source) as archive:
                members = archive.infolist()
                if (
                    getattr(archive, "start_dir", None) != directory[0]
                    or len(members) != directory[1]
                ):
                    return ["ZIP parsed directory does not match preflight"]
                if len(members) > max_members:
                    return ["ZIP member count exceeds limit"]
                seen: set[str] = set()
                total = 0
                files: dict[str, zipfile.ZipInfo] = {}
                for member in members:
                    name = member.filename
                    if not _safe_zip_path(member.orig_filename):
                        return [f"unsafe ZIP path: {name}"]
                    key = name.rstrip("/").casefold()
                    if key in seen:
                        return [f"duplicate ZIP member: {name}"]
                    seen.add(key)
                    mode = stat.S_IFMT(member.external_attr >> 16)
                    if mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                        return [f"unsupported ZIP member type (including symlinks): {name}"]
                    if member.flag_bits & 1:
                        return [f"encrypted ZIP member is unsupported: {name}"]
                    total += member.file_size
                    if member.file_size > max_member_bytes or total > max_total_bytes:
                        return ["ZIP declared decompressed size exceeds limit"]
                    if not member.is_dir():
                        files[name] = member
                manifests = [
                    name
                    for name in files
                    if PurePosixPath(name).name == "manifest.json" and name.count("/") <= 1
                ]
                if len(manifests) != 1:
                    return ["ZIP must contain exactly one root or single-folder manifest.json"]
                manifest_name = manifests[0]
                prefix = manifest_name[: -len("manifest.json")]
                manifest = json.loads(
                    _read_member(
                        archive, files[manifest_name], min(max_member_bytes, 1024 * 1024)
                    ).decode("utf-8")
                )
                if (
                    not isinstance(manifest, dict)
                    or manifest.get("schemaVersion") != SCHEMA_VERSION
                ):
                    return ["manifest schema version is unsupported"]
                items = manifest.get("items")
                if not isinstance(items, list) or not items:
                    return ["manifest items must be a nonempty list"]
                errors: list[str] = []
                referenced: set[str] = set()
                for position, item in enumerate(items, 1):
                    if not isinstance(item, dict):
                        errors.append(f"item {position} is not an object")
                        continue
                    relative = item.get("packaged_path")
                    if (
                        not isinstance(relative, str)
                        or not _safe_zip_path(relative)
                        or relative.endswith("/")
                    ):
                        errors.append(f"item {position} has an unsafe packaged path")
                        continue
                    if relative.casefold() in referenced:
                        errors.append(f"duplicate manifest item: {relative}")
                        continue
                    referenced.add(relative.casefold())
                    image_member = files.get(prefix + relative)
                    if image_member is None:
                        errors.append(f"{relative} is missing")
                        continue
                    content = _read_member(archive, image_member, max_member_bytes)
                    if len(content) != item.get("bytes"):
                        errors.append(f"{relative} size does not match")
                    if hashlib.sha256(content).hexdigest() != item.get("sha256"):
                        errors.append(f"{relative} SHA-256 does not match")
                    try:
                        image_format, width, height = inspect_image(BytesIO(content))
                    except ValueError as exc:
                        errors.append(f"{relative} is not a valid supported image: {exc}")
                        continue
                    if [image_format, width, height] != [
                        item.get("image_format"),
                        item.get("width"),
                        item.get("height"),
                    ]:
                        errors.append(f"{relative} image metadata does not match")
                return errors
    except (
        OSError,
        ValueError,
        UnicodeError,
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        return [f"ZIP is unreadable: {exc}"]


def inspect_image(path: Path | BytesIO) -> tuple[str, int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            if image_format not in SUPPORTED_FORMATS:
                raise ValueError(f"unsupported image format: {image_format or 'unknown'}")
            width, height = image.size
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("file is not a readable image") from exc
    return image_format, width, height


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_name(name: str, used: set[str], index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).name).strip(".-")
    if not cleaned:
        cleaned = f"screenshot-{index}.png"
    stem, suffix = Path(cleaned).stem, Path(cleaned).suffix.lower()
    candidate = f"{stem}{suffix}"
    counter = 2
    while candidate.casefold() in used:
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    used.add(candidate.casefold())
    return candidate


def render_html(manifest: dict[str, Any]) -> str:
    capture = manifest["capture"]
    cards = []
    for item in manifest["items"]:
        cards.append(
            "<figure>"
            f'<img src="{html.escape(item["packaged_path"], quote=True)}" '
            f'alt="{html.escape(item["original_name"], quote=True)}">'
            f"<figcaption>{html.escape(item['original_name'])} · "
            f"{item['width']}×{item['height']} · {item['sha256']}</figcaption>"
            "</figure>"
        )
    notes = "".join(f"<li>{html.escape(note)}</li>" for note in capture["notes"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Screenshot evidence pack</title>
<style>body{{font:16px system-ui;max-width:72rem;margin:auto;padding:2rem;background:#f6f4ef;color:#17202a}}
header,figure{{background:white;border:1px solid #d8d3c8;border-radius:12px;padding:1rem}}
figure{{margin:1rem 0}}img{{max-width:100%;height:auto}}code{{overflow-wrap:anywhere}}</style></head>
<body><header><h1>{html.escape(capture["title"] or "Screenshot evidence pack")}</h1>
<p><strong>URL:</strong> <code>{html.escape(capture["url"])}</code></p>
<p><strong>Captured:</strong> {html.escape(capture["capturedAt"])}</p>
<p><strong>Browser:</strong> {html.escape(capture["browser"] or "Not recorded")}</p>
<ul>{notes}</ul></header>
<main>{"".join(cards)}</main>
<p>This index displays supplied evidence. Verify hashes with the command-line tool.</p></body></html>
"""
