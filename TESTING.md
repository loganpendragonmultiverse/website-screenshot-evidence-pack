# Testing

Use an isolated Python environment and install `python -m pip install -e ".[dev]"`.
Run `ruff format --check .`, `ruff check .`, `mypy src`, `pytest`, and `python -m build`.
The repository CI matrix covers Python 3.11 and 3.14 on Linux, Windows, and macOS.
New regressions live alongside the existing core/CLI tests.

Unsafe ZIP filename fixtures patch both ZIP headers directly where needed: Windows zipfile normalizes backslashes during creation, which would otherwise hide the supplied-name case.

Small ZIP fixtures inject low byte/count budgets and instrument reads; tests fail if ZipFile is constructed before early rejection. No resource-exhaustion archive is generated. Stored/Deflate and comment fixtures remain accepted; BZIP2/LZMA are rejected before construction or decompression.

A sub-300-byte fixture places a ZIP64 record/locator and one shadow directory in an entry comment with ordinary classic EOCD fields. Instrumentation proves rejection before ZipFile construction. Separate mismatch fixtures verify parsed-directory start/count agreement; no archive member is decompressed by the shadow-index fixture.


## 2.0.0 acceptance

Run every original and new test with the 90% branch-aware coverage threshold unchanged,
then build and audit shipped dependencies. New synthetic regressions cover typed
manifest parity across folder/ZIP inputs, duplicate keys/non-finite/deep JSON, real
links and Windows junctions, preserved dangling/raced destinations, coherent copied
bytes after a source changes, actual JPEG decoding, image/read budgets and a forged
ZIP member whose complete deflate payload is longer than its declared prefix.

Install each exact-tag wheel and source distribution in separate environments outside
the checkout; confirm site-packages import/version, complete tests, CLI example,
all shipped documents and examples, and published SHA256SUMS. Run actual generated
HTML in Chromium, Firefox and WebKit at desktop and narrow widths with long escaped
metadata and image names. Record any platform-specific skips explicitly; a fixture
substitute does not prove a native junction or codec behavior.

Each of the six native CI matrix cells also runs `python tests/verify_installed_distributions.py` after building. This installs the wheel and source distribution in separate fresh virtual environments, verifies module and packaged-guidance bytes against the reviewed source, runs the full unchanged coverage-gated suite outside the checkout, and exercises the installed example and console commands. Windows junction cases therefore execute against actual installed distributions.
