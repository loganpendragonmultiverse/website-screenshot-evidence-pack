# Website Screenshot Evidence Pack

Website Screenshot Evidence Pack turns screenshots you already captured into a portable, locally verifiable evidence folder. It records operator-supplied context, copies the original pixels without re-encoding them, calculates SHA-256 fingerprints, and produces both a machine-readable manifest and a simple HTML index.

It is intended for QA handoffs, change records, support cases, and personal research where a loose screenshot is not enough.

## Version 1.1.0

Verify supplied ZIP packs directly without extraction, including screenshot hashes and image metadata. Reject unsafe or duplicate archive paths, symlinks, ambiguous manifests, and excessive decompression.

## Three-minute use

Python 3.10 or newer is required.

    python -m pip install .
    screenshot-evidence-pack create home.png checkout.png --output evidence-20260722 --url "https://site.test/" --title "Checkout review" --browser "Firefox" --captured-at "2026-07-22T09:30:00-04:00" --note "Test account; no production submission" --zip
    screenshot-evidence-pack verify evidence-20260722

The output contains copied screenshots, manifest.json, and index.html. PNG, JPEG, and WebP are accepted. Existing output folders are refused instead of overwritten.

## Trust boundary

The tool does not open the URL, capture a browser, alter screenshots, authenticate timestamps, or prove what was live on a website. It proves whether the packaged files still match their recorded bytes and image metadata. URL, browser, title, capture time, and notes are supplied by the operator and remain claims requiring human review.

All processing is local. There are no accounts, uploads, analytics, cookies, or network calls.

## Development and maintenance

    python -m pip install -e ".[dev]"
    ruff format --check .
    ruff check .
    mypy src tests
    pytest
    python -m build

The original release is feature complete for the documented formats and manifest schema. Focused improvements are welcome through reviewed pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [MIT License](LICENSE).

## More open-source projects

This project is part of the [Logan Pendragon Forge open-source collection](https://www.loganpendragonforge.com/open-source/). Browse the catalog for other released tools, source repositories, live demos, and downloads.

## Verify a supplied ZIP

Run `screenshot-evidence-pack verify supplied.zip` to verify an archive directly, without extraction or opening its HTML. The manifest may be at the ZIP root or inside one top-level folder. Verification checks screenshot bytes, hashes, formats, and dimensions and rejects unsafe paths, duplicate names (including case collisions), symlinks/special files, encrypted members, duplicate manifest references, and ambiguous manifests. Defaults limit archives to 4,096 members, 64 MiB per uncompressed member, 256 MiB total declared uncompressed data, and a 1 MiB manifest. `verify_zip(path, max_total_bytes=..., max_member_bytes=..., max_members=...)` supports smaller or larger positive limits for a trusted workflow. Verification reads bounded screenshot data into memory, never writes archive members, and does not authenticate operator-supplied URLs, timestamps, titles, browser details, or notes. Matching data and manifest can still be fabricated together; this is integrity checking, not provenance or legal admissibility.

ZIP verification performs a metadata preflight on the same open file before constructing Python's ZipFile: by default, 512 MiB archive bytes, 8 MiB central-directory bytes, 64 KiB filename/extra/comment bytes per entry, and 4,096 entries. It reads at most a 65,557-byte end-record search window, then scans fixed 46-byte directory headers and seeks over variable metadata. Forged entry counts are checked against the streamed directory. The Python API accepts positive max_archive_bytes, max_directory_bytes, and max_entry_metadata_bytes budgets in addition to the existing decompressed-data limits. Only classic single-disk ZIP directory layouts and Stored/Deflate compression are accepted. ZIP64 directory records, prepended/extended directory layouts, BZIP2, LZMA, and other compression methods are refused. This policy avoids unbounded legacy BZIP2/LZMA decompression paths on older supported Python patch releases; the existing screenshot and aggregate decompression limits remain enforced. Folder verification and the tool's generated Deflate ZIP format are unchanged.
