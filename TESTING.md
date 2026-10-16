# Testing

Use an isolated Python environment and install `python -m pip install -e ".[dev]"`.
Run `ruff format --check .`, `ruff check .`, `mypy src`, `pytest`, and `python -m build`.
The repository CI matrix covers Python 3.10 and 3.14 on Linux, Windows, and macOS.
New regressions live alongside the existing core/CLI tests.

Unsafe ZIP filename fixtures patch both ZIP headers directly where needed: Windows zipfile normalizes backslashes during creation, which would otherwise hide the supplied-name case.

Small ZIP fixtures inject low byte/count budgets and instrument reads; tests fail if ZipFile is constructed before early rejection. No resource-exhaustion archive is generated. Stored/Deflate and comment fixtures remain accepted; BZIP2/LZMA are rejected before construction or decompression.

A sub-300-byte fixture places a ZIP64 record/locator and one shadow directory in an entry comment with ordinary classic EOCD fields. Instrumentation proves rejection before ZipFile construction. Separate mismatch fixtures verify parsed-directory start/count agreement; no archive member is decompressed by the shadow-index fixture.
