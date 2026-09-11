#!/usr/bin/env python3
"""Assemble sprites/hk47 from the PixelLab raw frames, at 1:1 pixel density.

Provenance. The figure art comes from `assets/anim-raw/<state>/`, generated via
`pixellab/mkchar.py` + `pixellab/mkanim.py` (see `pixellab/README.md`). The room
comes from `assets/room-raw/lit_alcove_0.png`. Neither is produced here; this
script only cuts, pins and frames them, so it is cheap to re-run and spends no
generations.

The whole point is that nothing is resampled. Frames are 128px, the theme's
`scale` is 1.0 and `config.sprite.size` is 128, so `diorama_scale` in main.rs
works out to exactly 1.0 and every asset pixel lands on one screen pixel. Change
any one of those three and the pack goes soft.

Two pinning rules, and they are different on purpose:

* Vertically, each frame is pinned by its bounding box BOTTOM to `FEET_Y`. The
  lowest pixel is always the grounded foot, and bottom-pinning is what stops the
  generator's per-frame re-centring from making him bob.
* Horizontally, each frame is pinned by its FOOTPRINT centre, measured from the
  lowest `SAMPLE_ROWS` rows only. Centring on the full bounding box would slide
  the whole body sideways the moment an arm or a rifle extends: in the combat
  strip the bounding box is 112px wide against idle's 49, and bbox-centring
  would walk him half a body-width across the floor as he shoulders it.

The same footprint measurement drives the contact shadow in `sprite.rs`, so the
ellipse tracks the stance rather than the silhouette.

States. The theme names four: `idle`, `idle_alt`, `attentive`, `thinking`.
`stance` is wired to `idle_alt` deliberately, because main.rs already
alternates Idle and IdleAlt on every entry to idle, which is the stance switch
asked for and costs nothing. `question`, `permission` and `combat` are written
to disk but not referenced: they need `AnimState` variants that do not exist
yet, and an unknown key in theme.toml warns on every launch. Note that the
`[readouts]` block names two of those same words; that is a coincidence of
subject matter, not a wiring. The readouts are wall consoles that count other
sessions, and they drive no animation at all.

`error` used to be a fifth state and is gone from the pack entirely as of
2026-09-11: no strip, no theme key, no `AnimState` variant. An error clears
too quickly to be caught on a sprite nobody is watching, and the raw frames
are still at assets/anim-raw/error, so restoring it is one line in STATES.
"""

import colorsys
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).parent
RAW = ROOT / "assets" / "anim-raw"
ROT = ROOT / "assets" / "rot-base"
ROOM = ROOT / "assets" / "room-raw" / "lit_alcove_0.png"
OUT = ROOT / "sprites" / "hk47"

# --- Geometry. These five numbers are the pack's contract with main.rs, and the
# --- theme.toml written at the bottom is how they get there. Do not duplicate
# --- them into the Rust: that mistake is why the old pack had to be built to
# --- match Alfred's backdrop rather than to suit HK-47.
FRAME = 128  # square frame; also config.sprite.size
FEET_Y = 128  # soles land on the frame's bottom edge
BORDER = 4  # ring width; chat_ui.rs 9-slices backdrop.png at exactly this
ROOM_CROP = (40, 32, 296, 256)  # -> 256x224 interior, chosen 2026-09-10
ROOM_FEET_Y = 236  # floor line, in the ORIGINAL room's coordinates
FLOOR_Y = BORDER + (ROOM_FEET_Y - ROOM_CROP[1])  # -> 236 in backdrop coordinates

# Figure size as a multiple of config.sprite.size. 4/3 against a sprite.size of
# 96 works out to exactly one screen pixel per sprite pixel, so the *droid* is at
# native density and only the room is reduced: the crispness ends up where the
# eye actually goes. Bigger also means nearer: FLOOR_Y moved down with it, or a
# 33% taller figure standing on the old floor line reads as a giant at the far
# end of the corridor rather than as a droid a step closer to the camera.
SPRITE_SCALE = 4 / 3

# --- Attention readouts. Three of the corridor's own bezelled screens double as
# --- the companion's counters: a live count repaints the glass inside one, and a
# --- dead one leaves the room exactly as painted. Held in the ORIGINAL room's
# --- coordinates and translated on the way out, the same trick FLOOR_Y uses, so
# --- a change to ROOM_CROP or BORDER moves them instead of stranding them.
# --- Rects are (x0, y0, x1, y1) with x1/y1 exclusive, measured off the room's own
# --- luminance map rather than eyeballed. Position is the identity: that panel
# --- always means that counter, which is what makes icons unnecessary.
ROOM_READOUTS = {
    # tan notice board across the corridor, at his shoulder, the largest face
    "question": ((214, 123, 227, 144), (214, 56, 46)),
    # bright glyph screen on the left wall
    "permission": ((116, 112, 127, 128), (226, 158, 44)),
    # small two-cell panel directly beneath it, the least urgent and the smallest
    "waiting": ((115, 130, 128, 145), (96, 196, 190)),
}

# Rows above the bounding box's bottom edge that count as "feet". Wide enough to
# catch both soles in a mid-stride frame, short enough to exclude the knees.
SAMPLE_ROWS = 12

# theme key -> (raw dir, tick_divisor, frames dropped by index)
STATES = {
    "idle": ("idle", 3, ()),
    "idle_alt": ("stance", 3, ()),
    "attentive": ("attentive", 2, ()),
    "thinking": ("thinking", 2, ()),
}

# --- Scan poses: a head turn spliced from the rotations, for nothing ---------
#
# An earlier attempt cut straight to the 8-way sheet, which pivoted the entire
# droid like a turret. Only the head should turn. The rotations share a skeleton,
# so the head band of an adjacent 45° rotation drops onto the forward-facing body
# with no visible neck seam, which buys the turn at zero generations against the
# 40 that `create-character-state` would have cost.
#
# Each strip is two frames: forward, then turned. sprite.rs plays it once and
# holds, so the turn is a single-frame snap. That is deliberate: a droid re-aims
# its head, it does not ease into a glance.
SCAN_POSES = {
    "scan_l": "south-west",
    "scan_r": "south-east",
}
SCAN_DIVISOR = 3
# Rows below the crown that count as head. Measured off the 122px figure: the
# neck joint sits at about 26.
HEAD_ROWS = 26
# Assembled and shipped, but not yet named in theme.toml: see the docstring.
# combat frame 8 is the muzzle flash, and the brief says he decides not to shoot.
EXTRA = {
    "question": ("question", 2, ()),
    "permission": ("permission", 2, ()),
    "combat": ("combat", 2, (8,)),
}

HK47_PALETTE = [
    (36, 26, 22),  # deepest shadow
    (74, 44, 30),  # rust dark
    (108, 62, 38),  # rust mid
    (140, 82, 48),  # rust light
    (176, 106, 62),  # copper
    (208, 140, 88),  # copper highlight
]


def footprint(im: Image.Image, bbox: tuple[int, int, int, int]) -> tuple[float, int]:
    """Centre and width of the figure's contact with the floor, in `im` coords.

    Falls back to the full bounding box when the bottom band is empty, which
    only happens for a frame with fewer than SAMPLE_ROWS rows of content.
    """
    band = im.crop((bbox[0], max(bbox[1], bbox[3] - SAMPLE_ROWS), bbox[2], bbox[3]))
    bb = band.getbbox()
    if bb is None:
        return (bbox[0] + bbox[2]) / 2.0, bbox[2] - bbox[0]
    left, right = bbox[0] + bb[0], bbox[0] + bb[2]
    return (left + right) / 2.0, right - left


def pin(path: Path) -> Image.Image:
    return pin_image(Image.open(path).convert("RGBA"), str(path))


def pin_image(im: Image.Image, label: str = "<image>") -> Image.Image:
    """One raw frame -> one FRAME x FRAME theme frame, footprint-centred and floor-pinned."""
    path = label
    bbox = im.getbbox()
    if bbox is None:
        raise SystemExit(f"{path}: frame is fully transparent")
    body = im.crop(bbox)
    if body.width > FRAME or body.height > FEET_Y:
        raise SystemExit(
            f"{path}: figure is {body.width}x{body.height}, which does not fit "
            f"a {FRAME}px frame with feet at {FEET_Y}"
        )
    fp_cx, _ = footprint(im, bbox)
    x = round(FRAME / 2.0 - (fp_cx - bbox[0]))
    # Clamp so an extended limb is never clipped; the footprint drifts off centre
    # instead, which is the lesser evil and only bites in the widest combat frames.
    x = max(0, min(FRAME - body.width, x))
    frame = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
    frame.alpha_composite(body, (x, FEET_Y - body.height))
    return frame


def strip_from(files: list[Path]) -> tuple[Image.Image, int]:
    if not files:
        raise SystemExit("no frames to assemble")
    frames = [pin(f) for f in files]
    strip = Image.new("RGBA", (FRAME * len(frames), FRAME), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        strip.paste(f, (i * FRAME, 0))
    return strip, len(frames)


def build_strip(state_dir: str, drop: tuple[int, ...]) -> tuple[Image.Image, int]:
    src = RAW / state_dir
    files = [f for i, f in enumerate(sorted(src.glob("[0-9]*.png"))) if i not in drop]
    return strip_from(files)


def splice_head(rotation: str) -> Image.Image:
    """The forward-facing body wearing another rotation's head."""
    body = Image.open(ROT / "south.png").convert("RGBA")
    turned = Image.open(ROT / f"{rotation}.png").convert("RGBA")
    bb, hbb = body.getbbox(), turned.getbbox()
    out = body.copy()
    # Clear the forward head, then drop the turned one in, aligned by the crown
    # row and by each figure's own horizontal centre.
    ImageDraw.Draw(out).rectangle(
        (bb[0], bb[1], bb[2] - 1, bb[1] + HEAD_ROWS - 1), fill=(0, 0, 0, 0)
    )
    head = turned.crop((hbb[0], hbb[1], hbb[2], hbb[1] + HEAD_ROWS))
    out.alpha_composite(head, ((bb[0] + bb[2]) // 2 - head.width // 2, bb[1]))
    return out


def build_scan(rotation: str) -> tuple[Image.Image, int]:
    """Two frames: facing front, then head turned 45°."""
    frames = [pin_image(Image.open(ROT / "south.png").convert("RGBA")), pin_image(splice_head(rotation))]
    strip = Image.new("RGBA", (FRAME * len(frames), FRAME), (0, 0, 0, 0))
    for i, f in enumerate(frames):
        strip.paste(f, (i * FRAME, 0))
    return strip, len(frames)


# --- Interior separation ----------------------------------------------------
#
# HK-47 is rust and copper, and so were the corridor's ribs, lit panels and floor
# strips, so his silhouette dissolved into his own set. Chosen 2026-09-10 from a
# five-treatment audition: keep the hue, take the light. Separation by luminance
# rather than by colour, which leaves the room recognisably the same room instead
# of turning it into a differently-lit set, and makes him the brightest warm
# thing on screen by construction.
#
# Only genuinely warm, genuinely saturated pixels move. The blue-grey structure,
# the white overhead lamp and every neutral are left exactly as generated.
WARM_LO, WARM_HI = 8.0, 55.0  # degrees; the accent band, not the neutrals
WARM_SAT_MIN = 0.18
DIM_VALUE = 0.55
DIM_SAT = 0.55


def dim_warm(img: Image.Image) -> Image.Image:
    out = img.copy()
    px = out.load()
    for y in range(out.height):
        for x in range(out.width):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            if s < WARM_SAT_MIN or not (WARM_LO <= h * 360.0 <= WARM_HI):
                continue
            nr, ng, nb = colorsys.hsv_to_rgb(h, s * DIM_SAT, v * DIM_VALUE)
            px[x, y] = (round(nr * 255), round(ng * 255), round(nb * 255), a)
    return out


# The ship's own warm accent, measured off the dim-warmed interior: the mean of
# the brightest decile of its warm band (hue 8-55, saturation >= 0.15), and a
# darker tone from the same band for the two edge pixels.
SHIP_ACCENT = (96, 78, 64, 255)
SHIP_ACCENT_DK = (48, 38, 32, 255)


def build_backdrop() -> Image.Image:
    """The 256x224 interior in a thin ship-accent line -> 264x232.

    Master 2026-09-10: the 32px plated ring is gone. It was the same rust as
    HK-47 himself at higher saturation, so it fought him for attention and won.
    What replaces it is one warm-brown line in the ship's own accent colour, no
    bolts and no panels, thin enough to read as a mount rather than a subject.
    BORDER still feeds chat_ui's 9-slice, which is why the band is a flat fill:
    a uniform ring stretches without artefact at any chat panel size.
    """
    room = dim_warm(Image.open(ROOM).convert("RGBA").crop(ROOM_CROP))
    w, h = room.width + BORDER * 2, room.height + BORDER * 2

    bd = Image.new("RGBA", (w, h), SHIP_ACCENT)
    bd.alpha_composite(room, (BORDER, BORDER))

    # One darker pixel at each edge of the band: outside so the window has an
    # edge against the desktop, inside so the room does not bleed into it.
    d = ImageDraw.Draw(bd)
    d.rectangle((0, 0, w - 1, h - 1), outline=SHIP_ACCENT_DK)
    d.rectangle((BORDER - 1, BORDER - 1, w - BORDER, h - BORDER), outline=SHIP_ACCENT_DK)

    return bd


def theme_toml(counts: dict[str, int]) -> str:
    lines = [
        "[meta]",
        'name = "HK-47"',
        'author = "hk47"',
        "",
        "# Geometry the draw loop reads instead of hardcoding. With",
        "# config.sprite.size = 96 the room is drawn at 0.75 and the figure at",
        "# 0.75 * 4/3 = 1.0, so HK-47 sits at exactly native pixel density.",
        "[geometry]",
        f"frame_size = {FRAME}",
        f"feet_y = {FEET_Y}",
        f"floor_y = {FLOOR_Y}",
        f"scale = {SPRITE_SCALE:.6f}",
        f"border = {BORDER}",
        "",
        "# 256x224 corridor interior in a 4px ship-accent line. chat_ui.rs 9-slices",
        "# this file at [geometry] border to dress the chat panel in the same frame.",
        "[backdrop]",
        'file = "backdrop.png"',
        "",
        "# Wall consoles that double as attention counters. Rects are in backdrop",
        "# pixels, x1/y1 exclusive; badge.rs repaints the glass inside them when a",
        "# count is live and draws nothing at all when it is zero.",
    ]
    for key, (rect, colour) in ROOM_READOUTS.items():
        x0, y0, x1, y1 = rect
        bd_rect = (
            x0 - ROOM_CROP[0] + BORDER,
            y0 - ROOM_CROP[1] + BORDER,
            x1 - ROOM_CROP[0] + BORDER,
            y1 - ROOM_CROP[1] + BORDER,
        )
        lines += [
            f"[readouts.{key}]",
            f"rect = [{', '.join(str(v) for v in bd_rect)}]",
            f"colour = [{', '.join(str(v) for v in colour)}]",
            "",
        ]
    for state in SCAN_POSES:
        lines += [
            f"[animations.{state}]",
            f'file = "{state}.png"',
            f"frames = {counts[state]}",
            f"tick_divisor = {SCAN_DIVISOR}",
            "",
        ]
    for state, (_, divisor, _) in STATES.items():
        lines += [
            f"[animations.{state}]",
            f'file = "{state}.png"',
            f"frames = {counts[state]}",
            f"tick_divisor = {divisor}",
            "",
        ]
    lines += [
        "# Assembled but unreferenced: these need AnimState variants that do not",
        "# exist yet, and an unknown key here warns on every launch.",
    ]
    lines += [f"#   {name}.png ({counts[name]} frames)" for name in EXTRA]
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    for name, rotation in SCAN_POSES.items():
        strip, n = build_scan(rotation)
        strip.save(OUT / f"{name}.png")
        counts[name] = n
        print(f"{name:11} {n:2} frames  <- assets/rot-base/south + {rotation} head")

    for name, (state_dir, _, drop) in {**STATES, **EXTRA}.items():
        strip, n = build_strip(state_dir, drop)
        strip.save(OUT / f"{name}.png")
        counts[name] = n
        print(f"{name:11} {n:2} frames  <- assets/anim-raw/{state_dir}/")

    bd = build_backdrop()
    bd.save(OUT / "backdrop.png")
    print(f"backdrop    {bd.width}x{bd.height}  floor_y={FLOOR_Y}  border={BORDER}")

    # chat_wood.png is the tileable rust the chat panel is filled with. It is
    # built from the KOTOR render by build-hk47-sprites.py and is unaffected by
    # the geometry change, so it is carried through rather than rebuilt.
    wood = OUT / "chat_wood.png"
    if not wood.is_file():
        print(f"warn: {wood} missing: run build-hk47-sprites.py to regenerate it", file=sys.stderr)

    (OUT / "theme.toml").write_text(theme_toml(counts))
    print(f"theme.toml  frame={FRAME} feet_y={FEET_Y} floor_y={FLOOR_Y} scale=1.0")


if __name__ == "__main__":
    main()
