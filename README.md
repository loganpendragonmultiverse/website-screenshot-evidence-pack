# Website Screenshot Evidence Pack

Website Screenshot Evidence Pack turns screenshots you already captured into a portable, locally verifiable evidence folder. It records operator-supplied context, copies the original pixels without re-encoding them, calculates SHA-256 fingerprints, and produces both a machine-readable manifest and a simple HTML index.

It is intended for QA handoffs, change records, support cases, and personal research where a loose screenshot is not enough.

## Version 1.1.0

Verify supplied ZIP packs directly without extraction, including screenshot hashes and image metadata. Reject unsafe or duplicate archive paths, symlinks, ambiguous manifests, and excessive decompression.

## Three-minute use

Python 3.11 or newer is required. Current release: **v2.0.1**. Python 3.10 is no longer supported.

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

The release supports the documented formats and manifest schema. Focused improvements are welcome through reviewed pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [MIT License](LICENSE).

## More open-source projects

This project is part of the [Logan Pendragon Forge open-source collection](https://www.loganpendragonforge.com/open-source/). Browse the catalog for other released tools, source repositories, live demos, and downloads.

## Verify a supplied ZIP

Run `screenshot-evidence-pack verify supplied.zip` to verify an archive directly, without extraction or opening its HTML. The manifest may be at the ZIP root or inside one top-level folder. Verification checks screenshot bytes, hashes, formats, and dimensions and rejects unsafe paths, duplicate names (including case collisions), symlinks/special files, encrypted members, duplicate manifest references, and ambiguous manifests. Defaults limit archives to 4,096 members, 64 MiB per uncompressed member, 256 MiB total declared uncompressed data, and a 1 MiB manifest. `verify_zip(path, max_total_bytes=..., max_member_bytes=..., max_members=...)` supports smaller or larger positive limits for a trusted workflow. Verification reads bounded screenshot data into memory, never writes archive members, and does not authenticate operator-supplied URLs, timestamps, titles, browser details, or notes. Matching data and manifest can still be fabricated together; this is integrity checking, not provenance or legal admissibility.

ZIP verification performs a metadata preflight on the same open file before constructing Python's ZipFile: by default, 512 MiB archive bytes, 8 MiB central-directory bytes, 64 KiB filename/extra/comment bytes per entry, and 4,096 entries. It reads at most a 65,557-byte end-record search window, then scans fixed 46-byte directory headers and seeks over variable metadata. Forged entry counts are checked against the streamed directory. The Python API accepts positive max_archive_bytes, max_directory_bytes, and max_entry_metadata_bytes budgets in addition to the existing decompressed-data limits. Only classic single-disk ZIP directory layouts and Stored/Deflate compression are accepted. ZIP64 directory records, prepended/extended directory layouts, BZIP2, LZMA, and other compression methods are refused. This policy avoids unbounded legacy BZIP2/LZMA decompression paths on older supported Python patch releases; the existing screenshot and aggregate decompression limits remain enforced. Generated Deflate ZIPs and folder packs remain supported.

A ZIP64 locator immediately before the classic end record is refused even when the classic fields contain no ZIP64 sentinels. The parsed directory start and member count must agree with the bounded preflight before member processing.


## Version 2.0.0: bounded, consistent verification

Folder and ZIP verification use the same typed manifest contract. Duplicate JSON keys,
non-finite numbers, excessive nesting, empty or duplicate evidence lists and boolean
stand-ins for numeric measurements are rejected. Folder verification refuses linked
roots, manifests, screenshots and internal directory components, including Windows
reparse points. Supplied ZIP members are checked against their complete Stored or
Deflate stream, actual size and CRC, rather than trusting a forged declared prefix.

Each supplied image is read once into a bounded buffer; those same bytes determine
its copied payload, hash and metadata. PNG, JPEG and WebP are the only image plugins
used. The first image frame is verified and decoded after its dimensions are checked.
Animated or multipage frame-by-frame validation is outside scope. No image is
re-encoded.

Creation and folder/ZIP verification default to 64 MiB per screenshot, 256 MiB
total screenshot bytes, 4,096 entries, 40 million pixels per image, and a 1 MiB
manifest with at most 128 JSON nesting levels. ZIP creation reserves two entries for
its manifest and HTML. Existing ZIP metadata budgets still apply. Use positive
integer overrides for a trusted larger workflow:

    screenshot-evidence-pack verify evidence --max-image-bytes 134217728 --max-total-bytes 536870912 --max-pixels 80000000

The Python APIs expose the same limits as max_member_bytes, max_total_bytes,
max_members and max_pixels. These bounds do not fully bound codec CPU time or native
metadata allocations. Keep inputs stable; observations are sequential, not an atomic
snapshot of all screenshots.

Existing files, dangling output links and raced empty directories are preserved.
Output files and optional ZIPs use exclusive creation. Folder publication and ZIP
creation are separate operations: an I/O failure can leave a partial new folder or
ZIP for inspection. Ordinary parent-path aliases are allowed; these checks do not
promise safety against hostile concurrent filesystem mutation.

Pillow 13 is required. Its upstream conditional Arrow-export advisory is separately
documented; this tool uses no Arrow import or export path. This is not a claim that
all Pillow capabilities are free of vulnerabilities. See upstream dependency
advisories when using Pillow outside this tool's shipped CLI and API.

Installed wheels and source distributions include README, DEVELOPMENT, TESTING,
CHANGELOG, the evidence model and a synthetic example under share/doc.

Resource budgets must be integers from 1 through `sys.maxsize - 1` on the active Python runtime. Larger values are rejected with a validation error before reading input or creating output.

ZIP verification validates the complete bounded compressed stream, size and CRC of every supplied member, including extra HTML/files. Directory members with payloads are refused. Image decoding and manifest SHA-256 comparisons apply to referenced screenshots; CRC validation of other files does not authenticate their authorship or safety.

Folder creation and verification count screenshot bytes for the screenshot budgets. ZIP verification counts every member, including manifest and HTML; ZIP creation checks those metadata members and the complete byte total against the same budgets before publishing a destination. Increase the ZIP member/total budgets when metadata would not fit. The generated manifest is always limited to 1 MiB.
