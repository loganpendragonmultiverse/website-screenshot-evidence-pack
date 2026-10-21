from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import venv
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
GUIDANCE = (
    "README.md",
    "DEVELOPMENT.md",
    "TESTING.md",
    "CHANGELOG.md",
    "LICENSE",
    "docs/EVIDENCE-MODEL.md",
    "examples/README.md",
    "examples/create_example.py",
)


def run(arguments: list[str], directory: Path, environment: dict[str, str]) -> None:
    subprocess.run(arguments, cwd=directory, env=environment, check=True)


def main() -> None:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (REPOSITORY / "src/screenshot_evidence_pack").glob("*.py")
    }
    for label, pattern in (("wheel", "*.whl"), ("sdist", "*.tar.gz")):
        artifacts = list((REPOSITORY / "dist").glob(pattern))
        assert len(artifacts) == 1, f"Expected one {label} distribution"
        artifact = artifacts[0]
        with tempfile.TemporaryDirectory(prefix=f"screenshot-installed-{label}-") as temporary:
            root = Path(temporary)
            prefix = root / "environment"
            venv.EnvBuilder(with_pip=True).create(prefix)
            binaries = prefix / ("Scripts" if os.name == "nt" else "bin")
            python = binaries / ("python.exe" if os.name == "nt" else "python")
            console = binaries / (
                "screenshot-evidence-pack.exe" if os.name == "nt" else "screenshot-evidence-pack"
            )
            run([str(python), "-m", "pip", "install", str(artifact) + "[dev]"], root, environment)
            binding = (
                "from pathlib import Path; import hashlib, screenshot_evidence_pack; "
                "p=Path(screenshot_evidence_pack.__file__).resolve().parent; "
                f"assert not p.is_relative_to(Path({str(REPOSITORY)!r})); "
                "assert 'site-packages' in str(p); "
                "actual={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in p.glob('*.py')}; "
                f"assert actual == {expected!r}; print('INSTALLED_MODULE', p)"
            )
            run([str(python), "-c", binding], root, environment)
            tests = root / "tests"
            tests.mkdir()
            for source in (REPOSITORY / "tests").glob("test_*.py"):
                shutil.copyfile(source, tests / source.name)
            shutil.copyfile(REPOSITORY / "pyproject.toml", root / "pyproject.toml")
            run(
                [str(python), "-m", "pytest", "-c", str(root / "pyproject.toml"), str(tests)],
                root,
                environment,
            )
            run([str(python), "-m", "pip", "check"], root, environment)
            docs = prefix / "share/doc/website-screenshot-evidence-pack"
            for relative in GUIDANCE:
                assert (docs / relative).read_bytes() == (REPOSITORY / relative).read_bytes(), (
                    relative
                )
            example = root / "example"
            run(
                [str(python), str(docs / "examples/create_example.py"), str(example)],
                root,
                environment,
            )
            for target in (example / "evidence", example / "evidence.zip"):
                run([str(console), "verify", str(target)], root, environment)
            print(
                json.dumps(
                    {
                        "installed_distribution": label,
                        "artifact": artifact.name,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                        "full_suite_and_source_parity": "passed",
                        "packaged_guidance_examples_console": "passed",
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
