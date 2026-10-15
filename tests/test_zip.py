import json
import stat
import warnings
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from screenshot_evidence_pack.cli import main
from screenshot_evidence_pack.core import create_pack, verify_pack, verify_zip


def payload(tmp_path: Path) -> dict[str, bytes]:
    image = tmp_path / "shot.png"
    Image.new("RGB", (8, 4)).save(image)
    folder = create_pack([image], tmp_path / "pack", url="https://example.test", make_zip=True)
    assert verify_pack(folder.with_suffix(".zip")) == []
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def archive(tmp_path: Path, files: dict[str, bytes], prefix: str = "") -> Path:
    target = tmp_path / "supplied.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(prefix + name, data)
    return target


def test_root_and_folder_zips_without_extraction(tmp_path: Path) -> None:
    files = payload(tmp_path)
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    for prefix in ("", "evidence/"):
        path = archive(tmp_path, files, prefix)
        assert verify_zip(path) == []
        assert main(["verify", str(path)]) == 0
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert after == sorted([*before, "supplied.zip"])


@pytest.mark.parametrize(
    "change,expected",
    [
        ("tampered", "SHA-256"),
        ("missing", "missing"),
        ("metadata", "image metadata"),
        ("badimage", "valid supported image"),
        ("badsize", "size"),
        ("badjson", "unreadable"),
        ("schema", "schema"),
        ("items", "nonempty"),
        ("scalar", "schema"),
        ("baditem", "not an object"),
        ("traversalitem", "unsafe"),
        ("duplicateitem", "duplicate manifest"),
    ],
)
def test_zip_tampering_and_manifest_errors(tmp_path: Path, change: str, expected: str) -> None:
    files = payload(tmp_path)
    manifest = json.loads(files["manifest.json"])
    shot = manifest["items"][0]["packaged_path"]
    if change == "tampered":
        files[shot] += b"tamper"
    elif change == "missing":
        del files[shot]
    elif change == "metadata":
        manifest["items"][0]["width"] = 999
    elif change == "badimage":
        files[shot] = b"invalid"
    elif change == "badsize":
        manifest["items"][0]["bytes"] = -1
    elif change == "schema":
        manifest["schemaVersion"] = 99
    elif change == "items":
        manifest["items"] = []
    elif change == "scalar":
        manifest = []
    elif change == "baditem":
        manifest["items"] = [None]
    elif change == "traversalitem":
        manifest["items"][0]["packaged_path"] = "../outside.png"
    elif change == "duplicateitem":
        manifest["items"].append(manifest["items"][0])
    files["manifest.json"] = b"{" if change == "badjson" else json.dumps(manifest).encode()
    assert any(expected in e for e in verify_zip(archive(tmp_path, files)))


@pytest.mark.parametrize(
    "name", ["../evil", "/absolute", "C:/evil", "a/../evil", "a\\evil", "a//evil", "./evil"]
)
def test_rejects_unsafe_member_paths(tmp_path: Path, name: str) -> None:
    assert "unsafe" in verify_zip(archive(tmp_path, {name: b"x"}))[0]


def test_duplicate_case_collision_and_symlink_members(tmp_path: Path) -> None:
    target = tmp_path / "duplicate.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(target, "w") as z:
            z.writestr("manifest.json", b"{}")
            z.writestr("manifest.json", b"{}")
    assert "duplicate" in verify_zip(target)[0]
    assert "duplicate" in verify_zip(archive(tmp_path, {"A": b"", "a": b""}))[0]
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(target, "w") as z:
        z.writestr(info, "../outside")
    assert "symlinks" in verify_zip(target)[0]


def test_limits_missing_ambiguous_and_corrupt_zip(tmp_path: Path) -> None:
    path = archive(tmp_path, {"large": b"a" * 1000})
    assert "size" in verify_zip(path, max_member_bytes=10)[0]
    assert "size" in verify_zip(path, max_total_bytes=10)[0]
    path = archive(tmp_path, {"a": b"", "b": b""})
    assert "count" in verify_zip(path, max_members=1)[0]
    assert "exactly one" in verify_zip(path)[0]
    path = archive(tmp_path, {"manifest.json": b"{}", "pack/manifest.json": b"{}"})
    assert "exactly one" in verify_zip(path)[0]
    path.write_bytes(b"not ZIP")
    assert "unreadable" in verify_zip(path)[0]
    with pytest.raises(ValueError, match="positive"):
        verify_zip(path, max_members=0)


def test_capture_claims_are_not_authenticated(tmp_path: Path) -> None:
    files = payload(tmp_path)
    manifest = json.loads(files["manifest.json"])
    manifest["capture"]["url"] = "https://changed-claim.test"
    manifest["capture"]["capturedAt"] = "operator says yesterday"
    files["manifest.json"] = json.dumps(manifest).encode()
    assert verify_zip(archive(tmp_path, files)) == []


def test_manifest_read_limit_invalid_utf8_and_directory_entries(tmp_path: Path) -> None:
    files = payload(tmp_path)
    files["manifest.json"] = b" " * (1024 * 1024 + 1)
    assert "decompression limit" in verify_zip(archive(tmp_path, files))[0]
    files["manifest.json"] = b"\xff"
    assert "unreadable" in verify_zip(archive(tmp_path, files))[0]
    files = {"pack/" + k: v for k, v in payload_for_existing(tmp_path).items()}
    files["pack/"] = b""
    files["pack/screenshots/"] = b""
    assert verify_zip(archive(tmp_path, files)) == []


def payload_for_existing(tmp_path: Path) -> dict[str, bytes]:
    folder = tmp_path / "pack"
    return {
        p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()
    }


def test_crc_corruption_and_encryption_are_reported(tmp_path: Path) -> None:
    files = payload(tmp_path)
    target = tmp_path / "stored.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    content = bytearray(target.read_bytes())
    with zipfile.ZipFile(target) as z:
        shot = next(i for i in z.infolist() if i.filename.startswith("screenshots/"))
        offset = shot.header_offset + 30 + len(shot.filename.encode()) + len(shot.extra)
    content[offset] ^= 1
    target.write_bytes(content)
    assert "unreadable" in verify_zip(target)[0]
    target = archive(tmp_path, files)
    content = bytearray(target.read_bytes())
    cursor = 0
    while True:
        cursor = content.find(b"PK\x01\x02", cursor)
        if cursor < 0:
            break
        content[cursor + 8] |= 1
        cursor += 4
    target.write_bytes(content)
    assert "encrypted" in verify_zip(target)[0]


def test_corrupt_compressed_member_is_reported(tmp_path: Path) -> None:
    path = archive(tmp_path, payload(tmp_path))
    content = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as z:
        shot = next(i for i in z.infolist() if i.filename.startswith("screenshots/"))
        offset = shot.header_offset + 30 + len(shot.filename.encode()) + len(shot.extra)
    content[offset : offset + 4] = b"\xff" * 4
    path.write_bytes(content)
    assert "unreadable" in verify_zip(path)[0]
