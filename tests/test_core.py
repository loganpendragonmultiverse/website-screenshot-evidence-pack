from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from screenshot_evidence_pack.core import (
    create_pack,
    normalize_timestamp,
    unique_name,
    verify_pack,
)


def image(path: Path, size: tuple[int, int] = (20, 12), format_: str = "PNG") -> Path:
    Image.new("RGB", size, "#246b8e").save(path, format=format_)
    return path


def test_creates_pack_with_manifest_html_and_zip(tmp_path: Path) -> None:
    first = image(tmp_path / "Home Screen.png")
    second = image(tmp_path / "detail.jpg", (8, 9), "JPEG")
    output = tmp_path / "evidence"
    create_pack(
        [first, second],
        output,
        url="https://site.test/page",
        title="<Review>",
        browser="Firefox 140",
        captured_at="2026-07-22T09:30:00-04:00",
        notes=["Menu open", "<script>"],
        make_zip=True,
    )
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert [item["image_format"] for item in manifest["items"]] == ["PNG", "JPEG"]
    assert manifest["items"][0]["width"] == 20
    rendered = (output / "index.html").read_text(encoding="utf-8")
    assert "&lt;Review&gt;" in rendered
    assert "<script>" not in rendered
    assert verify_pack(output) == []
    with zipfile.ZipFile(output.with_suffix(".zip")) as archive:
        assert "evidence/manifest.json" in archive.namelist()


def test_rejects_bad_inputs_and_existing_output(tmp_path: Path) -> None:
    screenshot = image(tmp_path / "shot.png")
    with pytest.raises(ValueError, match="at least one"):
        create_pack([], tmp_path / "out", url="https://site.test")
    with pytest.raises(ValueError, match="http"):
        create_pack([screenshot], tmp_path / "out", url="file:///private")
    with pytest.raises(ValueError, match="not found"):
        create_pack([tmp_path / "missing.png"], tmp_path / "out", url="https://site.test")
    (tmp_path / "out").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        create_pack([screenshot], tmp_path / "out", url="https://site.test")


def test_rejects_invalid_timestamp_and_image(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="ISO 8601"):
        normalize_timestamp("yesterday")
    with pytest.raises(ValueError, match="UTC offset"):
        normalize_timestamp("2026-07-22T12:00:00")
    bad = tmp_path / "fake.png"
    bad.write_text("not an image", encoding="utf-8")
    with pytest.raises(ValueError, match="readable image"):
        create_pack([bad], tmp_path / "out", url="https://site.test")


def test_verify_detects_tampering_missing_files_and_bad_manifest(tmp_path: Path) -> None:
    screenshot = image(tmp_path / "shot.png")
    output = create_pack([screenshot], tmp_path / "pack", url="https://site.test")
    packaged = next((output / "screenshots").iterdir())
    packaged.write_bytes(packaged.read_bytes() + b"tamper")
    errors = verify_pack(output)
    assert any("size" in error for error in errors)
    assert any("SHA-256" in error for error in errors)
    packaged.unlink()
    assert any("missing" in error for error in verify_pack(output))
    (output / "manifest.json").write_text("{", encoding="utf-8")
    assert any("unreadable" in error for error in verify_pack(output))
    (output / "manifest.json").unlink()
    assert verify_pack(output) == ["manifest.json is missing"]


def test_verify_rejects_path_escape_and_wrong_schema(tmp_path: Path) -> None:
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.json").write_text(
        json.dumps({"schemaVersion": 2, "items": []}), encoding="utf-8"
    )
    assert verify_pack(pack) == ["manifest schema version is unsupported"]
    manifest = {"schemaVersion": 1, "items": [{"packaged_path": "../outside.png"}]}
    (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "escapes" in verify_pack(pack)[0]


def test_unique_name_is_safe_and_collision_aware() -> None:
    used: set[str] = set()
    assert unique_name("../../A weird shot.PNG", used, 1) == "A-weird-shot.png"
    assert unique_name("a WEIRD shot.png", used, 2) == "a-WEIRD-shot-2.png"
    assert unique_name("...", used, 3) == "screenshot-3.png"
