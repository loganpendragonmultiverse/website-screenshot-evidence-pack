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
        ({"max_entry_metadata_bytes": 2}, "per-entry metadata", 68),
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
    assert all(size in {22, 46} for _, size in reader.reads)


def test_forged_low_entry_count_cannot_bypass_streamed_member_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = bytearray(small_zip())
    struct.pack_into("<2H", data, len(data) - 22 + 8, 1, 1)
    reader = reject_before_zipfile(
        tmp_path, monkeypatch, bytes(data), {"max_members": 1}, "member count"
    )
    assert [size for _, size in reader.reads] == [22, 46]


@pytest.mark.parametrize("method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_unbounded_legacy_compression_methods_rejected_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    method: int,
) -> None:
    data = small_zip(count=1, method=method)
    reader = reject_before_zipfile(tmp_path, monkeypatch, data, {}, "unsupported ZIP compression")
    assert [size for _, size in reader.reads] == [22, 46]


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
