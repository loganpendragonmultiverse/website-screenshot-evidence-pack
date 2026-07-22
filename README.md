# Website Screenshot Evidence Pack

Website Screenshot Evidence Pack turns screenshots you already captured into a portable, locally verifiable evidence folder. It records operator-supplied context, copies the original pixels without re-encoding them, calculates SHA-256 fingerprints, and produces both a machine-readable manifest and a simple HTML index.

It is intended for QA handoffs, change records, support cases, and personal research where a loose screenshot is not enough.

## Three-minute use

Python 3.10 or newer is required.

    python -m pip install "website-screenshot-evidence-pack @ https://github.com/loganpendragonmultiverse/website-screenshot-evidence-pack/releases/download/v1.0.0/website_screenshot_evidence_pack-1.0.0-py3-none-any.whl"
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

Version 1.0.0 is feature complete for the documented formats and manifest schema. Focused improvements are welcome through reviewed pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and the [MIT License](LICENSE).
