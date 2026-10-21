import argparse
from pathlib import Path

from PIL import Image

from screenshot_evidence_pack.core import create_pack, verify_pack

parser = argparse.ArgumentParser(description="Create a synthetic screenshot evidence example")
parser.add_argument("directory", type=Path)
args = parser.parse_args()
args.directory.mkdir(parents=True, exist_ok=False)
image = args.directory / "synthetic.png"
Image.new("RGB", (1, 1), "red").save(image)
pack = create_pack(
    [image],
    args.directory / "evidence",
    url="https://example.invalid/",
    title="Synthetic local example",
    captured_at="2026-10-21T00:00:00Z",
    make_zip=True,
)
assert verify_pack(pack) == []
assert verify_pack(pack.with_suffix(".zip")) == []
print(pack)
