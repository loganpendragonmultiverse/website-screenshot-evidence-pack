from __future__ import annotations

import json
import struct
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO, NoReturn

import pytest
from PIL import Image

from screenshot_evidence_pack import core


class TrackedReader(BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.reads: list[tuple[int, int]] = []

    def read(self, size: int | None = -1) -> bytes:
        assert size is not None and 0 <= size <= 65557, "preflight attempted an unbounded read"
        self.reads.append((self.tell(), size))
        return super().read(size)


def small_zip(*, count: int = 4, name: str = "entry", method: int = zipfile.ZIP_STORED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=method) as archive:
        for index in range(count):
            archive.writestr(f"{name}{index}", b"")
    return buffer.getvalue()


def reject_before_zipfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: bytes,
    budgets: dict[str, int],
    expected: str,
) -> TrackedReader:
    reader = TrackedReader(data)
    path = tmp_path / "supplied.zip"

    def supplied_open(target: Path, *args: Any, **kwargs: Any) -> BinaryIO:
        assert target == path
        return reader

    def forbidden_constructor(*args: Any, **kwargs: Any) -> NoReturn:
        pytest.fail("ZipFile was constructed before the preflight rejected metadata")

    monkeypatch.setattr(Path, "open", supplied_open)
    monkeypatch.setattr(zipfile, "ZipFile", forbidden_constructor)
    errors = core.verify_zip(path, **budgets)
    assert any(expected in error for error in errors), errors
    return reader


@pytest.mark.parametrize(
    "budgets,expected,read_bytes",
    [
        ({"max_archive_bytes": 16}, "archive size", 0),
        ({"max_members": 2}, "member count", 22),
        ({"max_directory_bytes": 16}, "central-directory metadata", 22),
        ({"max_entry_metadata_bytes": 2}, "per-entry metadata", 88),
    ],
)
def test_small_budgets_reject_before_directory_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    budgets: dict[str, int],
    expected: str,
    read_bytes: int,
) -> None:
    data = small_zip()
    reader = reject_before_zipfile(tmp_path, monkeypatch, data, budgets, expected)
    assert sum(size for _, size in reader.reads) == read_bytes
    assert all(size in {22, 20, 46} for _, size in reader.reads)


def test_forged_low_entry_count_cannot_bypass_streamed_member_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = bytearray(small_zip())
    struct.pack_into("<2H", data, len(data) - 22 + 8, 1, 1)
    reader = reject_before_zipfile(
        tmp_path, monkeypatch, bytes(data), {"max_members": 1}, "member count"
    )
    assert [size for _, size in reader.reads] == [22, 20, 46]


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_unbounded_legacy_compression_methods_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: int,
) -> None:
    data = small_zip(count=1, method=method)
    reader = reject_before_zipfile(tmp_path, monkeypatch, data, {}, "unsupported ZIP compression")
    assert [size for _, size in reader.reads] == [22, 20, 46]


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_safe_compression_and_archive_comments_remain_supported(
    tmp_path: Path,
    method: int,
) -> None:
    image = tmp_path / "shot.png"
    Image.new("RGB", (2, 2)).save(image)
    folder = core.create_pack([image], tmp_path / "pack", url="https://example.test")
    target = tmp_path / "supplied.zip"
    with zipfile.ZipFile(target, "w", compression=method) as archive:
        archive.comment = b"small operator comment"
        for path in folder.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(folder))
    assert core.verify_zip(target) == []
    assert json.loads((folder / "manifest.json").read_text())["schemaVersion"] == 1


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("empty", "end record"),
        ("noend", "end record"),
        ("comment", "comment length"),
        ("zip64", "ZIP64"),
        ("disk", "multi-disk"),
        ("layout", "layout"),
        ("short", "truncated"),
        ("header", "header"),
        ("entry", "entry is truncated"),
        ("count", "count does not match"),
    ],
)
def test_malformed_directory_rejected_before_zipfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    damage: str,
    expected: str,
) -> None:
    data = bytearray(small_zip())
    end = len(data) - 22
    directory = struct.unpack_from("<L", data, end + 16)[0]
    if damage == "empty":
        data = bytearray()
    elif damage == "noend":
        data = bytearray(b"x" * 30)
    elif damage == "comment":
        struct.pack_into("<H", data, end + 20, 1)
    elif damage == "zip64":
        struct.pack_into("<2H", data, end + 8, 65535, 65535)
    elif damage == "disk":
        struct.pack_into("<H", data, end + 4, 1)
    elif damage == "layout":
        struct.pack_into("<L", data, end + 16, directory + 1)
    elif damage == "short":
        struct.pack_into("<2L", data, end + 12, 1, end - 1)
    elif damage == "header":
        data[directory : directory + 4] = b"FAIL"
    elif damage == "entry":
        struct.pack_into("<H", data, directory + 28, 2000)
    elif damage == "count":
        struct.pack_into("<2H", data, end + 8, 1, 1)
    reject_before_zipfile(tmp_path, monkeypatch, bytes(data), {}, expected)


def zip64_comment_shadow_directory() -> bytes:
    def central(name: bytes, method: int, comment: bytes = b"") -> bytes:
        header = struct.pack(
            "<4s6H3L5H2L",
            b"PK\x01\x02",
            20,
            20,
            0,
            method,
            0,
            0,
            0,
            0,
            0,
            len(name),
            0,
            len(comment),
            0,
            0,
            0,
            0,
        )
        return header + name + comment

    name = b"manifest.json"
    shadow = central(b"shadow", zipfile.ZIP_BZIP2)
    shadow_offset = 46 + len(name)
    zip64_offset = shadow_offset + len(shadow)
    record = struct.pack(
        "<4sQ2H2L4Q",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        1,
        1,
        len(shadow),
        shadow_offset,
    )
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, zip64_offset, 1)
    directory = central(name, zipfile.ZIP_STORED, shadow + record + locator)
    end = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 1, 1, len(directory), 0, 0)
    return directory + end


def test_nonsentinel_zip64_locator_rejected_before_directory_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = zip64_comment_shadow_directory()
    assert len(data) < 300
    classic = struct.unpack("<4s4H2LH", data[-22:])
    assert classic[3:5] == (1, 1)
    assert classic[5] < 0xFFFFFFFF and classic[6] == 0
    # Demonstrate the parser mismatch with a tiny index, without opening a member.
    with zipfile.ZipFile(BytesIO(data)) as archive:
        assert archive.start_dir == 46 + len(b"manifest.json")
        assert [item.compress_type for item in archive.infolist()] == [zipfile.ZIP_BZIP2]
    reader = reject_before_zipfile(tmp_path, monkeypatch, data, {}, "ZIP64 locator")
    assert reader.reads == [(len(data) - 22, 22), (len(data) - 42, 20)]


@pytest.mark.parametrize("mismatch", ["start", "count"])
def test_parsed_directory_must_agree_with_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    path = tmp_path / "supplied.zip"
    path.write_bytes(small_zip(count=1))
    real_constructor = zipfile.ZipFile

    def redirected(*args: Any, **kwargs: Any) -> zipfile.ZipFile:
        archive = real_constructor(*args, **kwargs)
        if mismatch == "start":
            archive.start_dir += 1
        else:
            archive.infolist().clear()
        return archive

    monkeypatch.setattr(zipfile, "ZipFile", redirected)
    assert "parsed directory does not match preflight" in core.verify_zip(path)[0]
