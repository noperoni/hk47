#!/usr/bin/env python3
"""Build the HK-47 sprite theme for Alfred (PERS-3, piece 1) from the KOTOR render.

No PixelLab credits required: this is a local Pillow/numpy pipeline. The
generated pack is a drop-in Alfred theme, so it installs to
~/.config/alfred/sprites/hk47/ and is selected with `theme = "hk47"` under
[sprite] in ~/.config/alfred/config.toml.

Alfred's theme loader (buddy/src/overlay/theme.rs) resolves a missing
(state, dir) through (state, S) then (Idle, S), so a pack carrying only idle
is already valid; the richer states here are an upgrade, not a requirement.

Background separation exploits the one clean signal in the source render:
HK-47's rust plating is warm (R > B) while the blast-door backdrop and floor
grating are cool (B >= R). The silver limb segments sit near zero, so they are
rescued by a luminance clause, and the mask is then closed morphologically and
flood-filled from the border with PIL alone (scipy is not installed).

ponytail: local downscale-and-quantize, not a diffusion model. Regenerate the
animation strips through /pixel's animate-with-text-v3 when the PixelLab
subscription is renewed; the frame layout and theme.toml stay as they are.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).parent
REFERENCE = ROOT / "assets" / "hk47-reference.png"
OUT_DIR = ROOT / "sprites" / "hk47"

FRAME = 128  # px, matches Alfred's frame_size
# Measured against the three-variant comparison: BLOCK=2 halves the logical
# width to 22px, at which HK-47's forearms fuse into the torso and the silver
# limb segments fall below one pixel. Native resolution keeps the silhouette
# legible, and the flat designed palette supplies the pixel-art read instead.
BLOCK = 1  # nearest-neighbour block size
LOGICAL_H = 88  # figure height; must clear FEET_Y so the feet pin to the diorama floor
MEDIAN = 0  # speckle filter at logical resolution; 0 disables it (it eats the silver)
OUTLINE = (26, 18, 16, 255)  # warm near-black, keyed to the rust plating
EYE_CORE = (255, 120, 105, 255)
EYE_GLOW = (206, 48, 40, 255)

# A designed palette beats median-cut here: left to itself the quantizer folds
# the silver limb segments into the rust and the sprite reads as one brown blob.
HK47_PALETTE = [
    (36, 26, 22),  # deepest shadow
    (74, 44, 30),  # rust dark
    (108, 62, 38),  # rust mid
    (140, 82, 48),  # rust light
    (176, 106, 62),  # copper
    (208, 140, 88),  # copper highlight
    (58, 58, 66),  # silver shadow
    (96, 96, 106),  # silver dark
    (140, 140, 152),  # silver mid
    (186, 186, 198),  # silver light
    (22, 20, 20),  # panel black
]


def cutout(img: Image.Image) -> Image.Image:
    """Separate HK-47 from the blast-door backdrop, returning RGBA."""
    a = np.asarray(img.convert("RGB")).astype(int)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    warm = R - B
    lum = 0.299 * R + 0.587 * G + 0.114 * B

    # Confident plating, plus a luminance clause that rescues the silver limbs.
    keep = (warm > 2) | ((lum > 45) & (warm > -6))

    m = Image.fromarray((keep * 255).astype(np.uint8), "L")
    # Close then open: seal seams between plates, then drop speckle.
    m = m.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    m = m.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))

    # Everything reachable from the border is backdrop; whatever survives is
    # the figure plus its interior holes, which the flood then leaves filled.
    inv = Image.eval(m, lambda v: 255 - v)
    flood = inv.copy()
    w, h = flood.size
    seeds = (
        [(x, 0) for x in range(0, w, 8)]
        + [(x, h - 1) for x in range(0, w, 8)]
        + [(0, y) for y in range(0, h, 8)]
        + [(w - 1, y) for y in range(0, h, 8)]
    )
    for seed in seeds:
        if flood.getpixel(seed) == 255:
            ImageDraw.floodfill(flood, seed, 128, thresh=0)
    background = np.asarray(flood) == 128

    alpha = np.where(background, 0, 255).astype(np.uint8)
    out = img.convert("RGBA")
    out.putalpha(Image.fromarray(alpha, "L"))
    return out


def find_eyes(src: Image.Image) -> list[tuple[int, int]]:
    """Locate HK-47's two photoreceptors in source coordinates.

    They are the only near-saturated red in the head band; the rust plating is
    red-dominant too, so the threshold has to be tight and spatially bounded.
    """
    a = np.asarray(src.convert("RGB")).astype(int)
    R, G = a[..., 0], a[..., 1]
    band = np.zeros(R.shape, bool)
    band[100:220, 240:480] = True
    eye = band & (R > 200) & ((R - G) > 80)
    ys, xs = np.nonzero(eye)
    if len(xs) == 0:
        return []
    mid = (xs.min() + xs.max()) / 2
    out = []
    for sel in (xs < mid, xs >= mid):
        if sel.sum():
            out.append((round(xs[sel].mean()), round(ys[sel].mean())))
    return out


def to_logical(cut: Image.Image) -> Image.Image:
    """Crop to the figure and reduce to a flat, low-resolution pixel-art sprite."""
    body = cut.crop(cut.getbbox())

    # BOX is an area average, so the downscale is itself the denoise. Filtering
    # at full resolution first would smear the narrow silver forearms and shins
    # into the surrounding rust and leave them as single-pixel speckle.
    w = max(1, round(body.width * LOGICAL_H / body.height))
    small = body.resize((w, LOGICAL_H), Image.BOX)

    # Push saturation and contrast before quantizing, so rust and steel land in
    # different palette bins instead of averaging into the same brown.
    punchy = ImageEnhance.Color(small.convert("RGB")).enhance(1.7)
    punchy = ImageEnhance.Contrast(punchy).enhance(1.25)
    if MEDIAN >= 3:
        punchy = punchy.filter(ImageFilter.MedianFilter(MEDIAN))

    pal_img = Image.new("P", (1, 1))
    flatpal = [c for rgb in HK47_PALETTE for c in rgb]
    pal_img.putpalette(flatpal + [0] * (768 - len(flatpal)))
    rgb = punchy.quantize(palette=pal_img, dither=Image.NONE).convert("RGB")

    alpha = small.getchannel("A").point(lambda v: 255 if v > 128 else 0)
    rgb.putalpha(alpha)
    return rgb


def add_outline(sprite: Image.Image) -> Image.Image:
    """Ring the silhouette in near-black, which is what makes it read as a sprite."""
    a = sprite.getchannel("A")
    grown = a.filter(ImageFilter.MaxFilter(3))
    ring = np.asarray(grown).astype(int) - np.asarray(a).astype(int)

    out = Image.new("RGBA", (sprite.width + 2, sprite.height + 2), (0, 0, 0, 0))
    ring_img = Image.new("RGBA", sprite.size, OUTLINE)
    ring_img.putalpha(Image.fromarray((ring > 0).astype(np.uint8) * 255, "L"))
    out.paste(ring_img, (1, 1), ring_img)
    out.paste(sprite, (1, 1), sprite)
    return out


EYE_DIM = (88, 24, 20, 255)  # banked, not extinguished
EYE_HOT = (255, 236, 226, 255)  # full attention, near white


def _lerp(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def paint_eyes(
    sprite: Image.Image, eyes_logical: list[tuple[int, int]], level: float = 1.0
) -> None:
    """Burn the photoreceptors back in; the downscale washes them out otherwise.

    level 0..1 fades toward banked, above 1 drives toward white-hot. This is the
    only thing on the sprite that can act, so it carries the whole performance.
    """
    if level <= 1.0:
        glow, core = _lerp(EYE_DIM, EYE_GLOW, level), _lerp(EYE_DIM, EYE_CORE, level)
    else:
        t = level - 1.0
        glow, core = _lerp(EYE_GLOW, EYE_HOT, t), _lerp(EYE_CORE, EYE_HOT, t)

    d = ImageDraw.Draw(sprite)
    for x, y in eyes_logical:
        d.point([(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)], fill=glow)
        d.point([(x, y)], fill=core)


def build_sprite(src: Image.Image) -> tuple[Image.Image, list[tuple[int, int]]]:
    """The outlined logical sprite plus its photoreceptor coordinates."""
    cut = cutout(src)
    box = cut.getbbox()
    sprite = to_logical(cut)

    scale = LOGICAL_H / (box[3] - box[1])
    eyes = [
        (round((ex - box[0]) * scale) + 1, round((ey - box[1]) * scale) + 1)
        for ex, ey in find_eyes(src)
    ]
    return add_outline(sprite), eyes


def render(
    sprite: Image.Image,
    eyes: list[tuple[int, int]],
    dx: int = 0,
    dy: int = 0,
    eye_level: float = 1.0,
) -> Image.Image:
    """Compose one 128x128 frame: offset the body, set photoreceptor intensity."""
    lit = sprite.copy()
    paint_eyes(lit, eyes, eye_level)

    big = lit.resize((lit.width * BLOCK, lit.height * BLOCK), Image.NEAREST)
    frame = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
    # Bottom-align on FEET_Y rather than on the frame edge: main.rs pins that row
    # to the diorama floor, so the sprite must put its soles exactly there.
    frame.paste(
        big,
        ((FRAME - big.width) // 2 + dx, FEET_Y - big.height + dy),
        big,
    )
    return frame


def strip(frames: list[Image.Image]) -> Image.Image:
    """Lay frames out as the horizontal strip Alfred's SpriteSheet expects."""
    sheet = Image.new("RGBA", (FRAME * len(frames), FRAME), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        sheet.paste(f, (i * FRAME, 0), f)
    return sheet


# Each entry: (dx, dy, eye_level) per frame. Movement stays within a pixel or
# two because the body is a fixed render: this animates presence, not limbs.
# ponytail: real per-limb cycles need generated frames, see the module docstring.
ANIMATIONS: dict[str, dict] = {
    # Powered down but watching: a slow servo settle and a dimming photoreceptor.
    "idle": {
        "tick_divisor": 3,
        "keys": [
            (0, 0, 1.00), (0, 0, 0.92), (0, -1, 0.84), (0, -1, 0.78),
            (0, -1, 0.80), (0, 0, 0.88), (0, 0, 0.96), (0, 0, 1.00),
        ],
    },
    # The alternate idle Alfred flips to: a weight shift with one bright scan.
    "idle_alt": {
        "tick_divisor": 3,
        "keys": [
            (0, 0, 0.90), (1, 0, 0.88), (1, 0, 0.86), (1, -1, 1.35),
            (1, 0, 1.10), (0, 0, 0.92), (0, 0, 0.88), (0, 0, 0.90),
        ],
    },
    # Addressed directly: draws up, photoreceptors go hot and stay hot.
    "attentive": {
        "tick_divisor": 2,
        "keys": [
            (0, 0, 1.00), (0, -1, 1.20), (0, -2, 1.45),
            (0, -2, 1.50), (0, -2, 1.42), (0, -2, 1.46),
        ],
    },
    # Working: paired with the pacing translation sprite.rs applies in Thinking.
    "thinking": {
        "tick_divisor": 2,
        "keys": [
            (0, 0, 1.10), (1, -1, 1.15), (1, 0, 1.05),
            (0, 0, 1.10), (-1, -1, 1.15), (-1, 0, 1.05),
        ],
    },
}


# --- Diorama backdrop -------------------------------------------------------
#
# main.rs pins the sprite with hardcoded constants: the feet sit at FEET_Y_NATIVE
# (93) of the frame and land on FLOOR_Y_NATIVE (133) of the backdrop, at
# ALFRED_SCALE 1.35. Matching Alfred's 200x160 backdrop and putting HK-47's feet
# at y=93 makes that geometry line up with no Rust change at all.
#
# chat_ui.rs additionally 9-slices backdrop.png at 16px for the chat panel frame
# and tiles chat_wood.png inside it, so both filenames are load-bearing.
BACKDROP = (200, 160)
BORDER = 16  # must stay 16: chat_ui.rs slices the frame ring at exactly this
FLOOR_Y = 133  # FLOOR_Y_NATIVE in main.rs
FEET_Y = 93  # FEET_Y_NATIVE in main.rs

# Source patches in the reference render, chosen to avoid the figure itself.
PATCH_DOOR = (0, 250, 130, 700)
PATCH_PANEL = (560, 620, 720, 940)
PATCH_GRATING = (0, 990, 260, 1270)
PATCH_RUST = (300, 250, 420, 400)

DOOR_PALETTE = [
    (18, 22, 28),
    (34, 40, 50),
    (48, 56, 68),
    (64, 74, 88),
    (86, 98, 114),
]


def _quantize(img: Image.Image, palette: list[tuple]) -> Image.Image:
    pal_img = Image.new("P", (1, 1))
    flat = [c for rgb in palette for c in rgb]
    pal_img.putpalette(flat + [0] * (768 - len(flat)))
    return img.convert("RGB").quantize(palette=pal_img, dither=Image.NONE).convert("RGB")


def pixelate(
    src: Image.Image, patch: tuple, size: tuple, block: int, palette: list[tuple]
) -> Image.Image:
    """Crop a patch of the render and reduce it to chunky, palette-locked texture."""
    crop = src.crop(patch)
    lw, lh = max(1, size[0] // block), max(1, size[1] // block)
    small = _quantize(crop.resize((lw, lh), Image.BOX), palette)
    return small.resize(size, Image.NEAREST).crop((0, 0, *size))


def build_backdrop(src: Image.Image) -> Image.Image:
    """A rusted blast-door alcove: HK-47's own scenery, framed in plated steel.

    Structure is drawn rather than sampled. At 200x160 a downscaled photograph
    reads as murk: the seams, the floor slots and the plate bevels only survive
    if they are placed deliberately. The render still supplies every colour.
    """
    w, h = BACKDROP
    x0, y0, x1, y1 = BORDER, BORDER, w - BORDER, h - BORDER
    bd = Image.new("RGBA", BACKDROP, (0, 0, 0, 255))
    d = ImageDraw.Draw(bd)

    void, wall_dk, wall, wall_lt, wall_hi = DOOR_PALETTE
    shadow, rust_dk, rust, rust_lt, copper, copper_hi = HK47_PALETTE[:6]

    # --- Wall: a blast door, seamed vertically and panelled on the right.
    d.rectangle((x0, y0, x1 - 1, FLOOR_Y - 1), fill=wall)
    for sx in (x0 + 26, x0 + 27, x0 + 96, x0 + 97):
        d.line((sx, y0, sx, FLOOR_Y - 1), fill=wall_dk)
    d.line((x0 + 28, y0, x0 + 28, FLOOR_Y - 1), fill=wall_lt)
    d.line((x0 + 98, y0, x0 + 98, FLOOR_Y - 1), fill=wall_lt)
    d.rectangle((x0, y0, x1 - 1, y0 + 3), fill=wall_lt)  # lintel catches the light

    for py in (y0 + 34, y0 + 54, y0 + 74):  # recessed drawer fronts, as in the render
        d.rectangle((x1 - 40, py, x1 - 8, py + 11), fill=wall_dk)
        d.rectangle((x1 - 40, py, x1 - 8, py), fill=void)
        d.rectangle((x1 - 40, py + 11, x1 - 8, py + 11), fill=wall_lt)
        d.rectangle((x1 - 27, py + 5, x1 - 21, py + 6), fill=wall_hi)

    # --- Floor: deck grating, lit along its leading edge so it reads as ground.
    d.rectangle((x0, FLOOR_Y, x1 - 1, y1 - 1), fill=wall_dk)
    d.line((x0, FLOOR_Y, x1 - 1, FLOOR_Y), fill=wall_hi)
    d.line((x0, FLOOR_Y + 1, x1 - 1, FLOOR_Y + 1), fill=wall_lt)
    for row, gy in enumerate(range(FLOOR_Y + 4, y1 - 1, 4)):
        for gx in range(x0 + (row % 2) * 5, x1 - 3, 10):
            d.line((gx, gy, gx + 5, gy), fill=void)

    # --- Frame: plated ring, each plate bevelled, bolted at the seams.
    ring = Image.new("L", BACKDROP, 255)
    ImageDraw.Draw(ring).rectangle((x0, y0, x1 - 1, y1 - 1), fill=0)
    plates = Image.new("RGBA", BACKDROP, rust)
    pd = ImageDraw.Draw(plates)
    for i, px in enumerate(range(0, w, 25)):
        pd.rectangle((px, 0, px + 24, h), fill=rust if i % 2 else rust_dk)
        pd.line((px, 0, px, h), fill=shadow)
        pd.line((px + 1, 0, px + 1, h), fill=rust_lt)
    # Corrosion: a deterministic speckle, so rebuilds are byte-identical.
    for n in range(700):
        sx, sy = (n * 71) % w, (n * 137) % h
        pd.point((sx, sy), fill=(copper if n % 3 else shadow))
    bd.paste(plates, (0, 0), ring)

    d.rectangle((0, 0, w - 1, h - 1), outline=shadow)
    d.rectangle((1, 1, w - 2, h - 2), outline=copper)
    d.rectangle((x0 - 1, y0 - 1, x1, y1), outline=shadow)
    d.rectangle((x0 - 2, y0 - 2, x1 + 1, y1 + 1), outline=copper_hi)

    def bolt(bx: int, by: int) -> None:
        d.ellipse((bx - 3, by - 3, bx + 3, by + 3), fill=shadow)
        d.ellipse((bx - 2, by - 2, bx + 2, by + 2), fill=copper)
        d.point((bx - 1, by - 1), fill=copper_hi)

    for bx in range(BORDER // 2, w, 25):
        bolt(bx, BORDER // 2)
        bolt(bx, h - BORDER // 2 - 1)
    for by in range(BORDER // 2 + 25, h - BORDER, 25):
        bolt(BORDER // 2, by)
        bolt(w - BORDER // 2 - 1, by)

    return bd


def build_chat_fill(src: Image.Image) -> Image.Image:
    """The tileable rust the chat panel is filled with, replacing Alfred's hardwood."""
    return pixelate(src, PATCH_RUST, (160, 120), 4, HK47_PALETTE).convert("RGB")


def theme_toml() -> str:
    lines = [
        "[meta]",
        'name = "HK-47"',
        'author = "hk47"',
        f"frame_size = {FRAME}",
        "",
        "# Rusted blast-door alcove, cut from the same KOTOR render as the sprite.",
        "# The 16px plated ring is what chat_ui.rs 9-slices for the chat panel frame.",
        "[backdrop]",
        'file = "backdrop.png"',
        "",
    ]
    for state, spec in ANIMATIONS.items():
        lines += [
            f"[animations.{state}]",
            f'file = "{state}.png"',
            f"frames = {len(spec['keys'])}",
            f"tick_divisor = {spec['tick_divisor']}",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    src = Image.open(REFERENCE)
    sprite, eyes = build_sprite(src)

    for state, spec in ANIMATIONS.items():
        frames = [render(sprite, eyes, dx, dy, lvl) for dx, dy, lvl in spec["keys"]]
        sheet = strip(frames)
        sheet.save(OUT_DIR / f"{state}.png")
        print(f"{state:10s} {len(frames)} frames  {sheet.size}")

    backdrop = build_backdrop(src)
    backdrop.save(OUT_DIR / "backdrop.png")
    print(f"backdrop   {backdrop.size}  floor y={FLOOR_Y}  ring={BORDER}px")

    fill = build_chat_fill(src)
    fill.save(OUT_DIR / "chat_wood.png")
    print(f"chat fill  {fill.size}")

    (OUT_DIR / "theme.toml").write_text(theme_toml())
    print(f"\nwrote {OUT_DIR / 'theme.toml'}")
    print(f"eyes (source): {find_eyes(src)}")


if __name__ == "__main__":
    main()
