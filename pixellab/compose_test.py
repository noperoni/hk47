#!/usr/bin/env python3
"""Composite HK-47 into the room at several floor depths, with and without a shadow.

The point of the exercise is the shadow. The old diorama had none, and a figure
with no contact shadow reads as pasted onto a picture rather than standing in a
room no matter how good the perspective behind it is.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

ROOM = Path("/path/to/hk47/assets/room-raw/lit_alcove_0.png")
POSE = Path("/path/to/hk47/assets/anim-raw/idle/00.png")
TMP = Path("/tmp/hk47")


def shadow_for(body: Image.Image, squash: float = 0.16, spread: float = 1.15) -> Image.Image:
    """An elliptical contact shadow sized from the figure's own footprint.

    Derived from the silhouette rather than hardcoded so it stays right when the
    pose widens: the combat stance is 112px across against idle's 46, and a fixed
    ellipse would float free of his feet the moment he shoulders the rifle.
    """
    w = int(body.width * spread)
    h = max(3, int(w * squash))
    pad = 6
    sh = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(sh)
    d.ellipse((pad, pad, pad + w, pad + h), fill=(8, 6, 10, 170))
    return sh.filter(ImageFilter.GaussianBlur(2.2))


def compose(feet_y: int, with_shadow: bool) -> Image.Image:
    room = Image.open(ROOM).convert("RGBA")
    pose = Image.open(POSE).convert("RGBA")
    body = pose.crop(pose.getbbox())
    x = (room.width - body.width) // 2
    if with_shadow:
        sh = shadow_for(body)
        room.alpha_composite(sh, (x + body.width // 2 - sh.width // 2, feet_y - sh.height // 2 - 2))
    room.alpha_composite(body, (x, feet_y - body.height))
    return room


if __name__ == "__main__":
    variants = [(232, False), (232, True), (244, True), (256, True)]
    tiles = [compose(y, s) for y, s in variants]
    w, h = tiles[0].size
    sheet = Image.new("RGB", (w * len(tiles) + 8 * (len(tiles) - 1), h), (20, 20, 24))
    d = ImageDraw.Draw(sheet)
    for i, t in enumerate(tiles):
        sheet.paste(t.convert("RGB"), (i * (w + 8), 0))
        y, s = variants[i]
        d.text((i * (w + 8) + 4, 4), f"feet_y={y} shadow={'yes' if s else 'no'}", fill=(255, 255, 255))
    sheet.save(TMP / "compose_test.png")
    print(sheet.size, variants)
