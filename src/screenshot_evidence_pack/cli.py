from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .core import create_pack, verify_pack


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="screenshot-evidence-pack",
        description="Package supplied screenshots with hashes and operator-recorded metadata.",
    )
    root.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = root.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create a new evidence pack")
    create.add_argument("screenshots", nargs="+", type=Path)
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--url", required=True)
    create.add_argument("--title", default="")
    create.add_argument("--browser", default="")
    create.add_argument("--captured-at")
    create.add_argument("--note", action="append", default=[])
    create.add_argument("--zip", action="store_true", dest="make_zip")
    verify = commands.add_parser("verify", help="verify a folder or ZIP against its manifest")
    verify.add_argument("pack", type=Path)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            destination = create_pack(
                args.screenshots,
                args.output,
                url=args.url,
                title=args.title,
                browser=args.browser,
                captured_at=args.captured_at,
                notes=args.note,
                make_zip=args.make_zip,
            )
            print(f"Created {destination}")
            return 0
        errors = verify_pack(args.pack)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        print("Pack verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
