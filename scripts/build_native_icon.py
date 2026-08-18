"""Draw the SignalSpace app mark and build the Tauri icon source.

The mark used to be a hand-supplied raster that this script only cropped and
centred. That was fine until it had to survive 32px on a Windows taskbar: the
old compass had tick marks finer than a pixel at that size, so it turned to
grey mush and stopped being recognisable.

So the mark is drawn here instead, from geometry, at 4x and downsampled. That
gives clean edges at every size and means the icon is reproducible from source
rather than being a binary nobody can edit. `branding/spendshape-icon.svg` is
the same design kept as vector reference for Ben and Seraphine; this module is
what the build actually consumes, and the two are meant to stay in step.

The design is the name. Three bars are the spending, the curve over their tops
is the shape, and the open arc frames it like a lens. Three fat bars rather
than five thin ones, one arc rather than a ring plus ticks, and a heavy curve
stroke: every one of those is the 32px constraint talking.

Pillow only, deliberately. Adding cairosvg would drag a native cairo build into
a Windows release pipeline for one PNG.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

REPO_ROOT = Path(__file__).resolve().parents[1]
# Kept as the documented vector twin of the geometry below.
VECTOR_REFERENCE = REPO_ROOT / "branding" / "spendshape-icon.svg"

# Drawn at 4x then downsampled; Pillow has no antialiased primitives.
SCALE = 4
SIZE = 1024
CANVAS = SIZE * SCALE

# SignalSpace palette. These are the same values the app's CSS tokens use, so
# the icon and the header lockup cannot drift apart.
INK_TOP = (19, 36, 55)
INK_MID = (13, 26, 40)
INK_BOTTOM = (9, 18, 28)
RIM = (31, 122, 77)
BLUE = (30, 159, 255)
BLUE_DEEP = (22, 104, 176)
GREEN = (0, 208, 132)
GREEN_DEEP = (14, 138, 90)
GREEN_LIT = (74, 222, 128)
GREEN_DEEP2 = (22, 163, 74)
MINT = (91, 229, 154)
DOT = (124, 240, 174)


def _s(value: float) -> int:
    """Scale a 1024-space coordinate into the supersampled canvas."""
    return int(round(value * SCALE))


def _linear_gradient(size, start, end, horizontal=False):
    """A one-directional RGB ramp the size of the canvas."""
    width, height = size
    gradient = Image.new("RGB", size)
    draw = ImageDraw.Draw(gradient)
    steps = width if horizontal else height
    for i in range(steps):
        t = i / max(steps - 1, 1)
        colour = tuple(int(round(a + (b - a) * t)) for a, b in zip(start, end))
        if horizontal:
            draw.line([(i, 0), (i, height)], fill=colour)
        else:
            draw.line([(0, i), (width, i)], fill=colour)
    return gradient


def _paste_through(base, gradient, mask):
    """Composite a gradient wherever the mask is opaque."""
    layer = gradient.convert("RGBA")
    layer.putalpha(mask)
    base.alpha_composite(layer)


def _bezier(p0, p1, p2, p3, steps=240):
    """Sample a cubic bezier. Used for the curve over the bar tops."""
    points = []
    for i in range(steps + 1):
        t = i / steps
        u = 1 - t
        x = (u ** 3 * p0[0] + 3 * u * u * t * p1[0]
             + 3 * u * t * t * p2[0] + t ** 3 * p3[0])
        y = (u ** 3 * p0[1] + 3 * u * u * t * p1[1]
             + 3 * u * t * t * p2[1] + t ** 3 * p3[1])
        points.append((x, y))
    return points


def _thick_polyline(draw, points, width):
    """A round-capped, round-joined stroke.

    Pillow's line joins are mitred and show notches on a tight curve, so each
    vertex gets an explicit round cap.
    """
    draw.line(points, fill=255, width=width, joint="curve")
    radius = width // 2
    for x, y in (points[0], points[-1]):
        draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=255)


def draw_mark(size: int = SIZE) -> Image.Image:
    """Render the SignalSpace mark on a transparent square canvas."""
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))

    # ── Field ────────────────────────────────────────────────────────────
    # A rounded square, not a free-floating mark. On a dark taskbar a
    # transparent mark loses its silhouette against the background.
    field_mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(field_mask).rounded_rectangle(
        [_s(64), _s(64), _s(960), _s(960)], radius=_s(232), fill=255)
    top = _linear_gradient((CANVAS, CANVAS // 2), INK_TOP, INK_MID)
    bottom = _linear_gradient((CANVAS, CANVAS - CANVAS // 2), INK_MID, INK_BOTTOM)
    field = Image.new("RGB", (CANVAS, CANVAS))
    field.paste(top, (0, 0))
    field.paste(bottom, (0, CANVAS // 2))
    _paste_through(canvas, field, field_mask)

    # A faint green rim keeps the icon from dissolving into a dark background.
    rim_mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(rim_mask).rounded_rectangle(
        [_s(64), _s(64), _s(960), _s(960)], radius=_s(232),
        outline=190, width=_s(7))
    _paste_through(canvas, Image.new("RGB", (CANVAS, CANVAS), RIM), rim_mask)

    # ── Arc ──────────────────────────────────────────────────────────────
    # Open, not a closed ring: the gap on the lower right is where the tallest
    # bar breaks out, so the mark reads as one object instead of a chart
    # sitting inside a circle.
    arc_mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(arc_mask).arc(
        [_s(512 - 322), _s(512 - 322), _s(512 + 322), _s(512 + 322)],
        start=142, end=30, fill=255, width=_s(60))
    arc_gradient = _linear_gradient((CANVAS, CANVAS), BLUE, GREEN)
    _paste_through(canvas, arc_gradient, arc_mask)

    # ── Bars ─────────────────────────────────────────────────────────────
    # Three, wide, well separated. Five thin bars close up at small sizes.
    bars = [
        ((330, 566, 424, 748), BLUE_DEEP, BLUE),
        ((459, 474, 553, 748), GREEN_DEEP, GREEN),
        ((588, 358, 682, 748), GREEN_DEEP2, GREEN_LIT),
    ]
    for (x0, y0, x1, y1), low, high in bars:
        bar_mask = Image.new("L", (CANVAS, CANVAS), 0)
        ImageDraw.Draw(bar_mask).rounded_rectangle(
            [_s(x0), _s(y0), _s(x1), _s(y1)], radius=_s(32), fill=255)
        _paste_through(canvas, _linear_gradient((CANVAS, CANVAS), high, low),
                       bar_mask)

    # ── The shape ────────────────────────────────────────────────────────
    # Over the bar tops rather than behind them. This is the part the name is
    # about, so it should be the last thing drawn and the first thing read.
    curve_mask = Image.new("L", (CANVAS, CANVAS), 0)
    curve_draw = ImageDraw.Draw(curve_mask)
    points = [(_s(x), _s(y)) for x, y in _bezier(
        (300, 636), (386, 620), (410, 548), (470, 516))]
    points += [(_s(x), _s(y)) for x, y in _bezier(
        (470, 516), (540, 486), (572, 442), (632, 396))]
    _thick_polyline(curve_draw, points, _s(30))
    curve_draw.ellipse(
        [_s(632 - 27), _s(396 - 27), _s(632 + 27), _s(396 + 27)], fill=255)
    curve_gradient = _linear_gradient((CANVAS, CANVAS), BLUE, MINT,
                                      horizontal=True)
    _paste_through(canvas, curve_gradient, curve_mask)

    # The terminal dot is flat mint so the curve has a definite end.
    dot_mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(dot_mask).ellipse(
        [_s(632 - 24), _s(396 - 24), _s(632 + 24), _s(396 + 24)], fill=255)
    _paste_through(canvas, Image.new("RGB", (CANVAS, CANVAS), DOT), dot_mask)

    mark = canvas.resize((size, size), Image.Resampling.LANCZOS)
    # A whisper of sharpening restores the edge crispness LANCZOS softens at
    # the very small sizes, without haloing at 1024.
    return mark.filter(ImageFilter.UnsharpMask(radius=1.2, percent=42,
                                               threshold=3))


def build_icon(destination: Path, source: Path | None = None) -> Path:
    """Write the 1024px icon source Tauri rasterizes from.

    `source` is accepted for compatibility with the existing build script
    invocation. When given an existing raster it is normalized as before;
    otherwise the mark is drawn.
    """
    if source is not None and source.is_file() and source.suffix != ".svg":
        image = Image.open(source).convert("RGBA")
        alpha_bbox = image.getchannel("A").getbbox()
        if not alpha_bbox:
            raise ValueError("SignalSpace icon source is fully transparent.")
        mark = image.crop(alpha_bbox)
        mark.thumbnail((900, 900), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        x = (canvas.width - mark.width) // 2
        y = (canvas.height - mark.height) // 2
        canvas.alpha_composite(mark, (x, y))
        result = canvas
    else:
        result = draw_mark()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.save(destination, format="PNG")
    return destination


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=None)
    args = parser.parse_args()
    source = args.source.resolve() if args.source else None
    output = build_icon(args.out.resolve(), source)
    print(f"Built SignalSpace Tauri icon source: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
