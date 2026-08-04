"""Build Ledger's deterministic multi-size Windows icon."""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


def build_icon(destination: Path) -> Path:
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((20, 20, 236, 236), radius=48, fill="#0D1117")
    draw.rounded_rectangle(
        (59, 45, 202, 213),
        radius=18,
        fill="#161B22",
        outline="#34D058",
        width=12,
    )
    draw.rounded_rectangle((44, 45, 82, 213), radius=15, fill="#34D058")
    for y in (91, 128, 165):
        draw.rounded_rectangle((101, y, 177, y + 11), radius=5, fill="#E6EDF3")
    destination.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(
        destination,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = build_icon(args.out.resolve())
    print(f"Built Windows icon: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
