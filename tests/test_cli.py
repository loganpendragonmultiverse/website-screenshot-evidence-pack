from __future__ import annotations

from pathlib import Path

from PIL import Image

from screenshot_evidence_pack.cli import main


def test_cli_create_and_verify(tmp_path: Path) -> None:
    source = tmp_path / "shot.png"
    Image.new("RGB", (4, 3)).save(source)
    output = tmp_path / "pack"
    assert main(["create", str(source), "--output", str(output), "--url", "https://site.test"]) == 0
    assert main(["verify", str(output)]) == 0


def test_cli_reports_create_and_verify_errors(tmp_path: Path) -> None:
    assert (
        main(
            [
                "create",
                str(tmp_path / "missing.png"),
                "--output",
                str(tmp_path / "pack"),
                "--url",
                "https://site.test",
            ]
        )
        == 2
    )
    pack = tmp_path / "pack"
    pack.mkdir()
    assert main(["verify", str(pack)]) == 1
