from __future__ import annotations

import hashlib
import html
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def inspect_image(path: Path) -> tuple[str, int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            if image_format not in SUPPORTED_FORMATS:
                raise ValueError(f"unsupported image format: {image_format or 'unknown'}")
            width, height = image.size
    except (OSError, UnidentifiedImageError) as exc:
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
