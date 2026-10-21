# Development scope

Version 1 preserves supplied screenshot bytes, records explicit operator metadata, exports JSON and HTML, optionally packages a ZIP, and verifies hashes, sizes, image formats, and dimensions.

It deliberately does not automate browser capture, notarize evidence, timestamp through a third party, infer legal admissibility, edit pixels, or contact recorded URLs.

## 1.1.0 - 2026-10-16

Verify supplied ZIP packs directly without extraction, including screenshot hashes and image metadata. Reject unsafe or duplicate archive paths, symlinks, ambiguous manifests, and excessive decompression.

See README.md for interface details and TESTING.md for the verification gate.


## 2.0.0 - 2026-10-21

Python 3.11+/Pillow 13 is an explicit compatibility break. Preserve all six native
OS/Python cells, the 90% branch-aware coverage gate, strict typing, lint, audits and
CodeQL. Only the three required Python 3.10 test contexts migrate to Python 3.11; every other
protection remains unchanged and is independently verified.

Use one bounded source-byte observation for payload/hash/image metadata. Folder and
ZIP manifests share strict typed validation; complete raw Stored/Deflate stream
validation must detect underdeclared payloads rather than inherit ZipExtFile prefix
semantics. Keep the existing bounded central-directory preflight and all original
regressions. Reports authenticate neither operator context nor website provenance.

Preserve existing output paths with exclusive creation; the new directory reservation
must never replace an empty directory created by another process. Publication may be
partial after a filesystem failure and is not a hostile-concurrency guarantee.


## 2.0.1 release workflow closure

Tagged releases repeat the existing formatting, lint, strict typing, complete test
suite and dependency audit before building. A strict stable tag must match both
project metadata and the single literal runtime version. Verify wheel METADATA
and sdist PKG-INFO Name, Version and Requires-Python against pyproject.toml, then
require exactly those two distributions before writing SHA256SUMS. Exercise tag,
runtime and built-metadata mismatch refusal controls alongside successful builds.
Python support, all six native CI cells, coverage and branch protections stay
unchanged. Installed artifacts and generated reports still require acceptance.
