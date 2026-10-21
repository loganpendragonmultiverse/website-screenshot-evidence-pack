from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import warnings
import zipfile
import zlib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from PIL import Image, UnidentifiedImageError

SCHEMA_VERSION = 1
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP"}


MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 4096
MAX_PIXELS = 40_000_000
MAX_MANIFEST_BYTES = 1024 * 1024


def _limits(**values: int) -> None:
    if any(type(value) is not int or not 0 < value < sys.maxsize for value in values.values()):
        raise ValueError(f"limits must be positive integers below {sys.maxsize}")


def _linked(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read_file(path: Path, limit: int) -> bytes:
    if _linked(path):
        raise ValueError("selected file must not be a symlink or reparse point")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("input must be a regular file")
    if before.st_size > limit:
        raise ValueError("file size exceeds limit")
    with path.open("rb") as handle:
        opened = os.fstat(handle.fileno())
        if _identity(opened) != _identity(before):
            raise ValueError("input changed before reading")
        content = handle.read(limit + 1)
        if len(content) > limit:
            raise ValueError("file size exceeds limit")
        if _identity(os.fstat(handle.fileno())) != _identity(opened):
            raise ValueError("input changed while reading")
    if (
        _linked(path)
        or _identity(path.stat()) != _identity(before)
        or len(content) != before.st_size
    ):
        raise ValueError("input changed while reading")
    return content


def _new_path(path: Path) -> None:
    if os.path.lexists(path):
        raise ValueError(f"output already exists: {path}")


def _write_new(path: Path, content: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(content)


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _json_number(value: str) -> int:
    if len(value) > 128:
        raise ValueError("JSON integer is too long")
    return int(value)


def _json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("JSON numbers must be finite")
    return result


def _json_constant(value: str) -> None:
    raise ValueError(f"nonstandard JSON number is unsupported: {value}")


def _manifest_json(raw: bytes) -> Any:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest decompression limit exceeded")
    text = raw.decode("utf-8")
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 128:
                raise ValueError("JSON nesting exceeds 128 levels")
        elif char in "]}":
            depth -= 1
    try:
        return json.loads(
            text,
            object_pairs_hook=_json_pairs,
            parse_int=_json_number,
            parse_float=_json_float,
            parse_constant=_json_constant,
        )
    except RecursionError as exc:
        raise ValueError("JSON nesting is unsupported") from exc


def _manifest_items(manifest: Any, max_members: int) -> list[Any]:
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schemaVersion")) is not int
        or manifest["schemaVersion"] != SCHEMA_VERSION
    ):
        raise ValueError("manifest schema version is unsupported")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest items must be a nonempty list")
    if len(items) > max_members:
        raise ValueError("manifest item count exceeds limit")
    return items


def _item_error(item: Any, position: int) -> str | None:
    if not isinstance(item, dict):
        return f"item {position} is not an object"
    relative = item.get("packaged_path")
    if not isinstance(relative, str) or not _safe_zip_path(relative) or relative.endswith("/"):
        return f"item {position} has an unsafe path that escapes the pack directory"
    if type(item.get("bytes")) is not int or item["bytes"] < 0:
        return f"item {position} size must be a nonnegative integer"
    if any(type(item.get(key)) is not int or item[key] <= 0 for key in ("width", "height")):
        return f"item {position} image metadata dimensions must be positive integers"
    if (
        not isinstance(item.get("image_format"), str)
        or item["image_format"] not in SUPPORTED_FORMATS
    ):
        return f"item {position} image metadata format is unsupported"
    digest = item.get("sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        return f"item {position} SHA-256 is invalid"
    if not isinstance(item.get("original_name"), str):
        return f"item {position} original name must be a string"
    return None


def _item_integrity(content: bytes, item: dict[str, Any], *, max_pixels: int) -> list[str]:
    relative = item["packaged_path"]
    errors: list[str] = []
    if len(content) != item["bytes"]:
        errors.append(f"{relative} size does not match")
    if hashlib.sha256(content).hexdigest() != item["sha256"]:
        errors.append(f"{relative} SHA-256 does not match")
    try:
        image_format, width, height = inspect_image(
            BytesIO(content), max_bytes=len(content) or 1, max_pixels=max_pixels
        )
    except ValueError as exc:
        errors.append(f"{relative} is not a valid supported image: {exc}")
        return errors
    if [image_format, width, height] != [item["image_format"], item["width"], item["height"]]:
        errors.append(f"{relative} image metadata does not match")
    return errors


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
        return datetime.now(UTC).isoformat(timespec="seconds")
    if not isinstance(value, str):
        raise TypeError("captured-at must be an ISO 8601 timestamp string")
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
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_members: int = MAX_MEMBERS,
    max_pixels: int = MAX_PIXELS,
) -> Path:
    _limits(
        max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes,
        max_members=max_members,
        max_pixels=max_pixels,
    )
    if not isinstance(sources, list):
        raise TypeError("sources must be a list of Path objects")
    if not sources:
        raise ValueError("at least one screenshot is required")
    if len(sources) > max_members:
        raise ValueError("screenshot count exceeds limit")
    if not isinstance(output, Path) or any(not isinstance(source, Path) for source in sources):
        raise TypeError("filesystem inputs must be Path objects")
    destination = output.expanduser().absolute()
    _new_path(destination)
    zip_path = destination.with_suffix(".zip")
    if make_zip:
        if zip_path == destination:
            raise ValueError("pack directory and ZIP output must be different paths")
        _new_path(zip_path)
    if not isinstance(url, str):
        raise TypeError("url must be a string")
    if not re.match(r"^https?://[^/\s]+", url, re.IGNORECASE):
        raise ValueError("url must use http:// or https:// with a host")
    if not isinstance(title, str) or not isinstance(browser, str):
        raise TypeError("title and browser must be strings")
    if notes is not None and (
        not isinstance(notes, list) or any(not isinstance(note, str) for note in notes)
    ):
        raise TypeError("notes must be a list of strings")
    if type(make_zip) is not bool or (captured_at is not None and not isinstance(captured_at, str)):
        raise TypeError("make_zip must be boolean and captured-at must be a string")
    timestamp = normalize_timestamp(captured_at)

    if make_zip and len(sources) + 2 > max_members:
        raise ValueError("ZIP member count exceeds limit")
    resolved_sources = [source.expanduser().absolute() for source in sources]
    for source in resolved_sources:
        if _linked(source):
            raise ValueError("selected screenshot must not be a symlink or reparse point")
        if not source.is_file():
            raise ValueError(f"screenshot not found: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        screenshots_dir = staging / "screenshots"
        screenshots_dir.mkdir()
        items: list[EvidenceItem] = []
        used_names: set[str] = set()
        total = 0
        for index, source in enumerate(resolved_sources, start=1):
            content = _read_file(source, min(max_member_bytes, max_total_bytes - total))
            total += len(content)
            image_format, width, height = inspect_image(
                BytesIO(content), max_bytes=max_member_bytes, max_pixels=max_pixels
            )
            safe_name = unique_name(source.name, used_names, index)
            target = screenshots_dir / safe_name
            _write_new(target, content)
            items.append(
                EvidenceItem(
                    original_name=source.name,
                    packaged_path=f"screenshots/{safe_name}",
                    sha256=hashlib.sha256(content).hexdigest(),
                    bytes=len(content),
                    image_format=image_format,
                    width=width,
                    height=height,
                )
            )

        manifest: dict[str, Any] = {
            "schemaVersion": SCHEMA_VERSION,
            "createdAt": datetime.now(UTC).isoformat(timespec="seconds"),
            "capture": {
                "url": url,
                "title": title,
                "browser": browser,
                "capturedAt": timestamp,
                "notes": notes or [],
            },
            "items": [asdict(item) for item in items],
            "limitations": [
                "The pack proves the packaged files and recorded metadata, not what was live at the URL.",
                "Capture metadata is supplied by the operator and is not independently authenticated.",
            ],
        }
        raw_manifest = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if len(raw_manifest) > MAX_MANIFEST_BYTES:
            raise ValueError("manifest size exceeds limit")
        _write_new(staging / "manifest.json", raw_manifest)
        (staging / "index.html").write_text(render_html(manifest), encoding="utf-8")
        # Reserve without replacing an existing empty directory. Each file is exclusive.
        destination.mkdir()
        (destination / "screenshots").mkdir()
        for member in sorted(staging.rglob("*")):
            if member.is_file():
                _write_new(destination / member.relative_to(staging), member.read_bytes())
        shutil.rmtree(staging)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    if make_zip:
        zip_path = destination.with_suffix(".zip")
        if zip_path.exists():
            raise ValueError(f"ZIP output already exists: {zip_path}")
        with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(destination.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(destination.parent))
    return destination


def verify_pack(
    pack: Path,
    *,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_members: int = MAX_MEMBERS,
    max_pixels: int = MAX_PIXELS,
) -> list[str]:
    _limits(
        max_member_bytes=max_member_bytes,
        max_total_bytes=max_total_bytes,
        max_members=max_members,
        max_pixels=max_pixels,
    )
    try:
        if not isinstance(pack, Path):
            raise TypeError("pack must be a Path")
        root = pack.expanduser().absolute()
        if _linked(root):
            return ["pack root must not be a symlink or reparse point"]
        if root.is_file():
            return verify_zip(
                root,
                max_member_bytes=max_member_bytes,
                max_total_bytes=max_total_bytes,
                max_members=max_members,
                max_pixels=max_pixels,
            )
        if not root.is_dir():
            return ["pack directory is missing"]
        root = root.resolve()
        manifest_path = root / "manifest.json"
        if not manifest_path.is_file() and not _linked(manifest_path):
            return ["manifest.json is missing"]
        try:
            manifest = _manifest_json(_read_file(manifest_path, MAX_MANIFEST_BYTES))
        except (OSError, UnicodeError, ValueError) as exc:
            return [f"manifest.json is unreadable: {exc}"]
        try:
            items = _manifest_items(manifest, max_members)
        except ValueError as exc:
            return [str(exc)]
        errors: list[str] = []
        referenced: set[str] = set()
        total = 0
        for position, item in enumerate(items, 1):
            error = _item_error(item, position)
            if error:
                errors.append(error)
                continue
            relative = item["packaged_path"]
            if relative.casefold() in referenced:
                errors.append(f"duplicate manifest item: {relative}")
                continue
            referenced.add(relative.casefold())
            candidate = root
            try:
                for part in relative.split("/"):
                    candidate = candidate / part
                    if _linked(candidate):
                        raise ValueError("pack member path contains a symlink or reparse point")
                if not candidate.is_file():
                    errors.append(f"{relative} is missing")
                    continue
                remaining = max_total_bytes - total
                if remaining <= 0:
                    raise ValueError("total screenshot bytes exceed limit")
                content = _read_file(candidate, min(max_member_bytes, remaining))
                total += len(content)
                errors.extend(_item_integrity(content, item, max_pixels=max_pixels))
            except (OSError, ValueError) as exc:
                errors.append(f"{relative} is unreadable: {exc}")
        return errors
    except (OSError, ValueError) as exc:
        return [f"pack is unreadable: {exc}"]


def _safe_zip_path(name: str) -> bool:
    parts = name.rstrip("/").split("/")
    return (
        bool(name)
        and not any(part in {"", ".", ".."} or ":" in part for part in parts)
        and "\\" not in name
        and not any(ord(char) < 32 or ord(char) == 127 for char in name)
    )


def _read_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo, limit: int) -> bytes:
    """Validate the whole bounded stream, including bytes hidden by a forged size."""
    with archive.open(member):
        pass  # Let ZipFile validate local-name agreement and overlapping entries.
    source = archive.fp
    assert source is not None
    source.seek(member.header_offset)
    header = source.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise ValueError("ZIP local header is invalid")
    local_flags, local_method = struct.unpack_from("<2H", header, 6)
    if local_flags != member.flag_bits or local_method != member.compress_type:
        raise ValueError("ZIP local header method or flags do not match the central directory")
    name_size, extra_size = struct.unpack_from("<2H", header, 26)
    source.seek(member.header_offset + 30 + name_size + extra_size)
    remaining = member.compress_size
    content = bytearray()
    decoder = zlib.decompressobj(-15) if member.compress_type == zipfile.ZIP_DEFLATED else None
    while remaining:
        chunk = source.read(min(65536, remaining))
        if not chunk:
            raise ValueError("ZIP member compressed stream is truncated")
        remaining -= len(chunk)
        if decoder is None:
            if len(content) + len(chunk) > limit:
                raise ValueError(f"{member.filename} exceeds decompression limit")
            content.extend(chunk)
        else:
            pending = chunk
            while pending:
                decoded = decoder.decompress(pending, limit - len(content) + 1)
                content.extend(decoded)
                if len(content) > limit:
                    raise ValueError(f"{member.filename} exceeds decompression limit")
                pending = decoder.unconsumed_tail
                if decoder.eof:
                    if decoder.unused_data or pending or remaining:
                        raise ValueError("ZIP member has trailing compressed data")
                    break
    if decoder is not None and not decoder.eof:
        raise ValueError("ZIP member compressed stream is unfinished")
    if len(content) != member.file_size or zlib.crc32(content) & 0xFFFFFFFF != member.CRC:
        raise ValueError("ZIP member size or CRC does not match its complete stream")
    return bytes(content)


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
    max_pixels: int = MAX_PIXELS,
) -> list[str]:
    """Verify a supplied ZIP in memory, without extracting or opening its HTML."""
    _limits(
        max_total_bytes=max_total_bytes,
        max_member_bytes=max_member_bytes,
        max_members=max_members,
        max_archive_bytes=max_archive_bytes,
        max_directory_bytes=max_directory_bytes,
        max_entry_metadata_bytes=max_entry_metadata_bytes,
        max_pixels=max_pixels,
    )
    if not isinstance(pack, Path):
        raise TypeError("ZIP input must be a Path")
    try:
        if _linked(pack) or not stat.S_ISREG(pack.stat().st_mode):
            return ["ZIP input must be a regular file without a selected link"]
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
                actual_total = 0
                raw_manifest = b""
                for member in members:
                    limit = min(max_member_bytes, max_total_bytes - actual_total)
                    if member.filename == manifest_name:
                        limit = min(limit, MAX_MANIFEST_BYTES)
                    content = _read_member(archive, member, limit)
                    actual_total += len(content)
                    if member.is_dir() and content:
                        return ["ZIP directory members must not contain payloads"]
                    if member.filename == manifest_name:
                        raw_manifest = content
                manifest = _manifest_json(raw_manifest)
                items = _manifest_items(manifest, max_members)
                errors: list[str] = []
                referenced: set[str] = set()
                for position, item in enumerate(items, 1):
                    error = _item_error(item, position)
                    if error:
                        errors.append(error)
                        continue
                    relative = item["packaged_path"]
                    if relative.casefold() in referenced:
                        errors.append(f"duplicate manifest item: {relative}")
                        continue
                    referenced.add(relative.casefold())
                    image_member = files.get(prefix + relative)
                    if image_member is None:
                        errors.append(f"{relative} is missing")
                        continue
                    content = _read_member(archive, image_member, max_member_bytes)
                    errors.extend(_item_integrity(content, item, max_pixels=max_pixels))
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


def inspect_image(
    path: Path | BytesIO,
    *,
    max_bytes: int = MAX_MEMBER_BYTES,
    max_pixels: int = MAX_PIXELS,
) -> tuple[str, int, int]:
    _limits(max_bytes=max_bytes, max_pixels=max_pixels)
    if isinstance(path, Path):
        raw = _read_file(path, max_bytes)
    elif isinstance(path, BytesIO):
        raw = path.getvalue()
        if len(raw) > max_bytes:
            raise ValueError("image bytes exceed limit")
    else:
        raise TypeError("image input must be a Path or BytesIO")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=sorted(SUPPORTED_FORMATS)) as image:
                if image.format not in SUPPORTED_FORMATS:
                    raise ValueError("unsupported image format")
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > max_pixels:
                    raise ValueError("image pixel count exceeds limit")
                image.verify()
            with Image.open(BytesIO(raw), formats=sorted(SUPPORTED_FORMATS)) as image:
                image.load()
                image_format = (image.format or "").upper()
    except (
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        SyntaxError,
        OverflowError,
    ) as exc:
        raise ValueError("file is not a readable image in a supported format") from exc
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
    stem, suffix = Path(cleaned).stem[:120], Path(cleaned).suffix.lower()[:16]
    device_base = stem.split(".", 1)[0].upper()
    if device_base in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(
        r"(?:COM|LPT)[1-9]", device_base
    ):
        stem = "screenshot-" + stem
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
<style>body{{overflow-wrap:anywhere;font:16px system-ui;max-width:72rem;margin:auto;padding:2rem;background:#f6f4ef;color:#17202a}}
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
