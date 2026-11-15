"""Create the app icon files used by the operator screen and the desktop shortcut.

    python scripts/make_icon.py                 # draw the built-in placeholder logo
    python scripts/make_icon.py my-logo.png     # use your own picture instead

Writes:
    static/icon.png   256x256, shown in the title bar and as the window/tab icon
    static/app.ico    Windows icon (16-256 px) for the desktop shortcut

Your own picture should be roughly square; it is centred, cropped to a square and
given rounded corners. A transparent PNG works best.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SIZE = 1024                                   # drawn large, then scaled down for smooth edges
BLUE = (15, 108, 189, 255)                    # the app's accent colour
WHITE = (255, 255, 255, 255)


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    return mask


def sky_gradient() -> Image.Image:
    """The logo's background: bright blue, a cyan glow bottom-left, deep navy on the right."""
    ys, xs = np.mgrid[0:SIZE, 0:SIZE] / (SIZE - 1)
    blue, navy, cyan = np.array([13, 84, 255.0]), np.array([6, 9, 62.0]), np.array([79, 240, 220.0])
    to_navy = np.clip((xs * 0.85 + (1 - ys) * 0.25 - 0.30) / 0.75, 0, 1)[..., None]
    colour = blue * (1 - to_navy) + navy * to_navy
    glow = np.clip(1 - np.hypot(xs - 0.08, ys - 1.05) / 0.85, 0, 1)[..., None] ** 1.6
    colour = colour * (1 - glow) + cyan * glow
    rgba = np.dstack([colour, np.full((SIZE, SIZE), 255.0)]).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def placeholder() -> Image.Image:
    """The built-in mark: the "ò" of the Ouranòs wordmark (ring + accent) on the sky gradient."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    image.paste(sky_gradient(), (0, 0), rounded_mask(SIZE, int(SIZE * 0.22)))
    draw = ImageDraw.Draw(image)
    stroke = int(SIZE * 0.095)
    cx, cy, radius = SIZE // 2, int(SIZE * 0.59), int(SIZE * 0.245)
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=WHITE, width=stroke)
    # grave accent: a short stroke with round ends, above the ring
    start, end = (int(SIZE * 0.40), int(SIZE * 0.135)), (int(SIZE * 0.525), int(SIZE * 0.235))
    draw.line([start, end], fill=WHITE, width=stroke)
    for x, y in (start, end):
        draw.ellipse((x - stroke // 2, y - stroke // 2, x + stroke // 2, y + stroke // 2), fill=WHITE)
    return image


def from_picture(path: Path) -> Image.Image:
    """Centre-crop the user's picture to a square with rounded corners."""
    picture = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    picture = ImageOps.fit(picture, (SIZE, SIZE), Image.LANCZOS, centering=(0.5, 0.5))
    # Keep the picture's own transparency, and round the corners on top of it.
    alpha = Image.composite(picture.getchannel("A"), Image.new("L", (SIZE, SIZE), 0),
                            rounded_mask(SIZE, int(SIZE * 0.22)))
    picture.putalpha(alpha)
    return picture


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if source is not None and not source.is_file():
        raise SystemExit(f"ERROR: picture not found: {source}")
    image = from_picture(source) if source else placeholder()
    STATIC.mkdir(exist_ok=True)
    image.resize((256, 256), Image.LANCZOS).save(STATIC / "icon.png")
    image.save(STATIC / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"Icon written to {STATIC / 'icon.png'} and {STATIC / 'app.ico'}"
          + (f" (from {source.name})" if source else " (placeholder)"))


if __name__ == "__main__":
    main()
