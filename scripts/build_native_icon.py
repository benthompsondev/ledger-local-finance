"""Build the Tauri icon source from Northstar Ledger's approved mark."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "branding" / "northstar-ledger-icon.png"


def build_icon(destination: Path, source: Path = DEFAULT_SOURCE) -> Path:
    """Normalize the transparent approved mark onto a square 1024px canvas."""
    if not source.is_file():
        raise FileNotFoundError(f"Northstar Ledger icon source not found: {source}")
    image = Image.open(source).convert("RGBA")
    alpha_bbox = image.getchannel("A").getbbox()
    if not alpha_bbox:
        raise ValueError("Northstar Ledger icon source is fully transparent.")
    mark = image.crop(alpha_bbox)
    mark.thumbnail((900, 900), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    x = (canvas.width - mark.width) // 2
    y = (canvas.height - mark.height) // 2
    canvas.alpha_composite(mark, (x, y))
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(destination, format="PNG")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    output = build_icon(args.out.resolve(), args.source.resolve())
    print(f"Built Northstar Ledger Tauri icon source: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
