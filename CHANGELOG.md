# Changelog

## 1.1.0 - Unreleased

- Verify supplied ZIP packs directly without extraction, including screenshot hashes and image metadata.
- Reject unsafe or duplicate archive paths, symlinks, ambiguous manifests, and excessive decompression.

## 1.0.0 - 2026-07-22

- Create byte-preserving evidence folders from supplied PNG, JPEG, and WebP screenshots.
- Record operator context and measured SHA-256, size, format, and dimensions.
- Export a JSON manifest, escaped HTML index, optional ZIP, and verification command.
