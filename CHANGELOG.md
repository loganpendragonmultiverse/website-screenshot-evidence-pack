# Changelog

## 2.0.0 - 2026-10-21

- Require Python 3.11+ and Pillow 13 while preserving the full six-cell native CI matrix and all existing quality gates.
- Apply strict bounded typed manifests consistently to folders and ZIPs; reject linked folder evidence and complete-stream ZIP size/CRC mismatches.
- Measure and copy the same bounded image bytes, enforce image pixel limits before decoding, and preserve existing or raced output paths.
- Expose explicit CLI/API resource budgets, improve long-text HTML wrapping, and ship complete installed guidance and a synthetic example.

## 1.1.0 - 2026-10-16

- Reject ZIP64 locators independently of classic EOCD sentinels and require parsed-directory agreement with preflight.

- Preflight archive and central-directory metadata before constructing ZipFile.
- Restrict supplied archives to Stored/Deflate compression for bounded reads across supported Python patch versions.

- Verify supplied ZIP packs directly without extraction, including screenshot hashes and image metadata.
- Reject unsafe or duplicate archive paths, symlinks, ambiguous manifests, and excessive decompression.

## 1.0.0 - 2026-07-22

- Create byte-preserving evidence folders from supplied PNG, JPEG, and WebP screenshots.
- Record operator context and measured SHA-256, size, format, and dimensions.
- Export a JSON manifest, escaped HTML index, optional ZIP, and verification command.
