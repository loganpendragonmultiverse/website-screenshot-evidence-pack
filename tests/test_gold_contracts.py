from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import zipfile
import zlib
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from screenshot_evidence_pack import core
from screenshot_evidence_pack.cli import main


def sample(tmp_path: Path) -> tuple[Path, Path, dict[str, bytes]]:
    source = tmp_path / "sample.png"
    Image.new("RGB", (1, 1), "red").save(source)
    pack = core.create_pack([source], tmp_path / "pack", url="https://example.test")
    files = {p.relative_to(pack).as_posix(): p.read_bytes() for p in pack.rglob("*") if p.is_file()}
    return source, pack, files


def supplied(tmp_path: Path, pack: Path, files: dict[str, bytes], mode: str) -> Path:
    if mode == "folder":
        for relative, content in files.items():
            (pack / relative).write_bytes(content)
        return pack
    target = tmp_path / "supplied.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, content in files.items():
            archive.writestr(relative, content)
    return target


@pytest.mark.parametrize("mode", ["folder", "zip"])
@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("schemaVersion", True, "schema"),
        ("schemaVersion", 1.0, "schema"),
        ("items", [], "nonempty"),
        ("items", [None], "not an object"),
        ("width", True, "dimensions"),
        ("height", 1.0, "dimensions"),
        ("bytes", True, "size"),
        ("image_format", ["PNG"], "format"),
        ("sha256", None, "SHA-256"),
        ("original_name", None, "string"),
    ],
)
def test_folder_and_zip_share_typed_contract(
    tmp_path: Path, mode: str, field: str, value: Any, expected: str
) -> None:
    _, pack, files = sample(tmp_path)
    manifest = json.loads(files["manifest.json"])
    if field in {"schemaVersion", "items"}:
        manifest[field] = value
    else:
        manifest["items"][0][field] = value
    files["manifest.json"] = json.dumps(manifest).encode()
    errors = core.verify_pack(supplied(tmp_path, pack, files, mode))
    assert any(expected in error for error in errors)


@pytest.mark.parametrize("mode", ["folder", "zip"])
@pytest.mark.parametrize("kind", ["duplicate", "nan", "overflow", "depth", "oversized"])
def test_strict_bounded_manifest_json(tmp_path: Path, mode: str, kind: str) -> None:
    _, pack, files = sample(tmp_path)
    raw = files["manifest.json"]
    if kind == "duplicate":
        raw = raw.replace(b'"schemaVersion": 1', b'"schemaVersion": 1, "schemaVersion": 1')
    elif kind in {"nan", "overflow"}:
        value = b"NaN" if kind == "nan" else b"1e9999"
        raw = raw.replace(b"{", b'{"extra": ' + value + b",", 1)
    elif kind == "depth":
        raw = raw.replace(b"{", b'{"extra": ' + b"[" * 130 + b"0" + b"]" * 130 + b",", 1)
    else:
        raw = b" " * (1024 * 1024 + 1)
    files["manifest.json"] = raw
    assert core.verify_pack(supplied(tmp_path, pack, files, mode))


@pytest.mark.parametrize("mode", ["folder", "zip"])
def test_duplicate_manifest_references(tmp_path: Path, mode: str) -> None:
    _, pack, files = sample(tmp_path)
    manifest = json.loads(files["manifest.json"])
    manifest["items"].append(manifest["items"][0])
    files["manifest.json"] = json.dumps(manifest).encode()
    assert any(
        "duplicate" in error for error in core.verify_pack(supplied(tmp_path, pack, files, mode))
    )


def link(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege unavailable; native junction test is separate")
        raise


@pytest.mark.parametrize("which", ["root", "manifest", "screenshot", "parent"])
def test_folder_verification_refuses_links(tmp_path: Path, which: str) -> None:
    source, pack, files = sample(tmp_path)
    if which == "root":
        root = tmp_path / "alias"
        link(root, pack, directory=True)
        pack = root
    elif which == "manifest":
        metadata = tmp_path / "external.json"
        metadata.write_bytes(files["manifest.json"])
        (pack / "manifest.json").unlink()
        link(pack / "manifest.json", metadata)
    elif which == "screenshot":
        screenshot = next((pack / "screenshots").iterdir())
        screenshot.unlink()
        link(screenshot, source)
    else:
        directory = pack / "screenshots"
        external = tmp_path / "external"
        directory.rename(external)
        link(directory, external, directory=True)
    assert any("link" in error or "reparse" in error for error in core.verify_pack(pack))
    assert source.read_bytes() == files["screenshots/sample.png"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction behavior")
def test_native_windows_junction_is_refused(tmp_path: Path) -> None:
    _, pack, _ = sample(tmp_path)
    alias = tmp_path / "junction"
    result = subprocess.run(
        ["cmd", "/d", "/c", "mklink", "/J", str(alias), str(pack)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert any("reparse" in error for error in core.verify_pack(alias))


def test_create_preserves_dangling_paths_and_zip_collision(tmp_path: Path) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    dangling = tmp_path / "dangling"
    link(dangling, tmp_path / "missing")
    with pytest.raises(ValueError, match="exists"):
        core.create_pack([source], dangling, url="https://example.test")
    assert dangling.is_symlink()
    zip_path = tmp_path / "pack.zip"
    zip_path.write_bytes(b"existing")
    with pytest.raises(ValueError, match="exists"):
        core.create_pack([source], tmp_path / "pack", url="https://example.test", make_zip=True)
    assert zip_path.read_bytes() == b"existing"
    assert not (tmp_path / "pack").exists()


def test_create_preserves_dangling_zip_path(tmp_path: Path) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    zip_path = tmp_path / "pack.zip"
    link(zip_path, tmp_path / "missing.zip")
    with pytest.raises(ValueError, match="exists"):
        core.create_pack([source], tmp_path / "pack", url="https://example.test", make_zip=True)
    assert zip_path.is_symlink()
    assert not (tmp_path / "missing.zip").exists()
    assert not (tmp_path / "pack").exists()


def test_raced_empty_directory_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    output = tmp_path / "pack"
    original = tempfile.mkdtemp

    def race(*, prefix: str, dir: Path) -> str:
        staging = original(prefix=prefix, dir=dir)
        output.mkdir()
        return staging

    monkeypatch.setattr(tempfile, "mkdtemp", race)
    with pytest.raises(FileExistsError):
        core.create_pack([source], output, url="https://example.test")
    assert output.is_dir()
    assert list(output.iterdir()) == []


def test_one_source_observation_drives_pixels_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2), "red").save(source)
    before = source.read_bytes()
    original = core.inspect_image
    changed = False

    def change_after_inspection(*args: Any, **kwargs: Any) -> tuple[str, int, int]:
        nonlocal changed
        result = original(*args, **kwargs)
        if not changed:
            changed = True
            Image.new("RGB", (8, 9), "blue").save(source)
        return result

    monkeypatch.setattr(core, "inspect_image", change_after_inspection)
    pack = core.create_pack([source], tmp_path / "pack", url="https://example.test")
    assert (pack / "screenshots/image.png").read_bytes() == before
    assert core.verify_pack(pack) == []
    assert source.read_bytes() != before


def test_actual_jpeg_decode_and_explicit_pixel_budget(tmp_path: Path) -> None:
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "red").save(buffer, format="JPEG")
    with pytest.raises(ValueError, match="readable"):
        core.inspect_image(BytesIO(buffer.getvalue()[:-10]))
    with pytest.raises(ValueError, match="pixel"):
        core.inspect_image(BytesIO(buffer.getvalue()), max_pixels=100)
    source = tmp_path / "large.png"
    Image.new("RGB", (10, 10)).save(source)
    with pytest.raises(ValueError, match="limit"):
        core.create_pack(
            [source], tmp_path / "pack", url="https://example.test", max_member_bytes=10
        )
    assert not (tmp_path / "pack").exists()


@pytest.mark.parametrize("value", [True, 0, -1, 1.5, "10", 10**100])
def test_resource_limits_are_positive_integers(tmp_path: Path, value: Any) -> None:
    source, pack, _ = sample(tmp_path)
    with pytest.raises(ValueError, match="positive"):
        core.verify_pack(pack, max_pixels=value)
    with pytest.raises(ValueError, match="positive"):
        core.verify_zip(tmp_path / "unused.zip", max_members=value)
    with pytest.raises(ValueError, match="positive"):
        core.create_pack(
            [source], tmp_path / "other", url="https://example.test", max_total_bytes=value
        )


@pytest.mark.parametrize(
    "option,value",
    [
        ("url", False),
        ("title", []),
        ("browser", 1),
        ("notes", "note"),
        ("captured_at", 1),
        ("make_zip", "false"),
    ],
)
def test_direct_api_metadata_validation(tmp_path: Path, option: str, value: Any) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    args: dict[str, Any] = {"url": "https://example.test", option: value}
    with pytest.raises(TypeError):
        core.create_pack([source], tmp_path / "pack", **args)
    assert not (tmp_path / "pack").exists()


@pytest.mark.parametrize("kind", ["referenced-image", "extra-file"])
def test_complete_deflate_stream_is_checked(tmp_path: Path, kind: str) -> None:
    _, _, files = sample(tmp_path)
    manifest = json.loads(files["manifest.json"])
    relative = manifest["items"][0]["packaged_path"] if kind == "referenced-image" else "extra.bin"
    original = files.get(relative, b"prefix")
    files[relative] = original + b"HIDDEN_TRAILING_PAYLOAD"
    path = tmp_path / "forged.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    content = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        offset = archive.getinfo(relative).header_offset
    struct.pack_into("<L", content, offset + 14, zlib.crc32(original))
    struct.pack_into("<L", content, offset + 22, len(original))
    cursor = 0
    while True:
        cursor = content.find(b"PK\x01\x02", cursor)
        assert cursor >= 0
        name_size, extra_size, comment_size = struct.unpack_from("<3H", content, cursor + 28)
        name = bytes(content[cursor + 46 : cursor + 46 + name_size]).decode()
        if name == relative:
            struct.pack_into("<L", content, cursor + 16, zlib.crc32(original))
            struct.pack_into("<L", content, cursor + 24, len(original))
            break
        cursor += 46 + name_size + extra_size + comment_size
    path.write_bytes(content)
    assert core.verify_zip(path)
    assert main(["verify", str(path)]) == 1


def test_portable_names_and_readable_long_html() -> None:
    assert core.unique_name("CON.png", set(), 1) == "screenshot-CON.png"
    assert len(core.unique_name("x" * 250 + ".png", set(), 1)) < 200


def test_huge_cli_budget_is_an_actionable_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "image.png"
    Image.new("RGB", (2, 2)).save(source)
    output = tmp_path / "output"
    assert (
        main(
            [
                "create",
                str(source),
                "--output",
                str(output),
                "--url",
                "https://example.test",
                "--max-image-bytes",
                str(10**100),
            ]
        )
        == 2
    )
    assert "positive integers below" in capsys.readouterr().err
    assert not output.exists()


@pytest.mark.parametrize(
    "name", ["CON.archive.png", "aux.extra.jpg", "COM1.extra.webp", "LPT9.notes.png"]
)
def test_multidot_windows_device_names_are_portable(name: str) -> None:
    assert core.unique_name(name, set(), 1).startswith("screenshot-")


@pytest.mark.parametrize("name", ["index.html", "unknown.dat"])
def test_unreferenced_member_crc_is_verified(tmp_path: Path, name: str) -> None:
    _, _, files = sample(tmp_path)
    files[name] = b"untrusted extra member"
    target = tmp_path / "corrupt-extra.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        for relative, raw in files.items():
            archive.writestr(relative, raw)
    with zipfile.ZipFile(target) as archive:
        info = archive.getinfo(name)
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
    content = bytearray(target.read_bytes())
    content[offset] ^= 1
    target.write_bytes(content)
    assert any("CRC" in error for error in core.verify_zip(target))


def test_zip_directory_payload_is_refused(tmp_path: Path) -> None:
    _, _, files = sample(tmp_path)
    target = tmp_path / "directory-payload.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, raw in files.items():
            archive.writestr(relative, raw)
        archive.writestr("extra/", b"hidden directory content")
    assert any("directory members" in error for error in core.verify_zip(target))


@pytest.mark.parametrize("offset,value", [(6, 0x800), (8, zipfile.ZIP_DEFLATED)])
def test_local_header_method_and_flags_match_directory(
    tmp_path: Path, offset: int, value: int
) -> None:
    _, _, files = sample(tmp_path)
    target = tmp_path / "contradictory-header.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as archive:
        for relative, raw in files.items():
            archive.writestr(relative, raw)
    content = bytearray(target.read_bytes())
    struct.pack_into("<H", content, offset, value)
    target.write_bytes(content)
    assert any("local header" in error for error in core.verify_zip(target))


def test_consistent_data_descriptor_zip_is_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, files = sample(tmp_path)
    buffer = BytesIO()

    def unavailable_seek(*args: Any, **kwargs: Any) -> int:
        raise OSError("nonseekable ZIP writer")

    monkeypatch.setattr(buffer, "seek", unavailable_seek)
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for relative, raw in files.items():
            archive.writestr(relative, raw)
    target = tmp_path / "descriptor.zip"
    target.write_bytes(buffer.getvalue())
    with zipfile.ZipFile(target) as archive:
        assert all(member.flag_bits & 8 for member in archive.infolist())
    assert core.verify_zip(target) == []
