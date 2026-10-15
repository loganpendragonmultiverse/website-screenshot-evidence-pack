# Development scope

Version 1 preserves supplied screenshot bytes, records explicit operator metadata, exports JSON and HTML, optionally packages a ZIP, and verifies hashes, sizes, image formats, and dimensions.

It deliberately does not automate browser capture, notarize evidence, timestamp through a third party, infer legal admissibility, edit pixels, or contact recorded URLs.

## 1.1.0 candidate

Verify supplied ZIP packs directly without extraction, including screenshot hashes and image metadata. Reject unsafe or duplicate archive paths, symlinks, ambiguous manifests, and excessive decompression.

See README.md for interface details and TESTING.md for the verification gate.
Merge and release await independent review.
