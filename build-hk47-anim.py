#!/usr/bin/env python3
"""SUPERSEDED 2026-09-10. Do not run this. See pixellab/README.md.

This drives `animate-with-text-v3`, which was tried and rejected: it has only a
picture to work from, so it reinterprets HK-47 into a different droid entirely,
and it flattens alpha, returning fully opaque frames with a palette colour baked
in behind him. `assets/anim-raw/scan_left/` is its output, kept as evidence.

The working path is `pixellab/mkchar.py` + `pixellab/mkanim.py`, which animate
the *character* via `POST /characters/animations`. That endpoint has the
character's skeleton and rotations, stays on-model, and preserves transparency.

Only `repin()` below is still worth reading: bbox-bottom-pinning each frame is
what stops the generator's re-centring from making him bob, and the assembly
script still needs it.

Original docstring follows.
---
Generate HK-47's animation strips through PixelLab, then fit them to the theme.

Why this exists. build-hk47-sprites.py animates a single fixed render by nudging
it a pixel and modulating the photoreceptors: presence, not limbs. Nothing that
needs a head turned 45 degrees, a stance change or a raised rifle can come out of
that, because those are new poses. This script buys the poses.

Budget. animate-with-text-v3 costs roughly one generation per frame, so the whole
shot list below is ~88 of the monthly pool. `GET /balance` is free; it is checked
either side of every batch and the delta is printed, so the spend is auditable
rather than asserted.

Geometry. Generation happens on a tight ANIM canvas (the figure plus room for a
weapon) because a 128x128 frame holding a 33x88 figure wastes the model's
resolution on emptiness. Each returned frame is then re-pinned by its own bbox so
the soles land on FEET_Y of the 128px theme frame, which is what main.rs pins to
the diorama floor. Without that re-pin the character floats or sinks whenever the
model re-centres it, which it does.
"""

import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import requests
from PIL import Image

API = "https://api.pixellab.ai/v2"
TOKEN = os.popen("fish -c 'echo $PIXELLAB_API_KEY'").read().strip()
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}

ROOT = Path(__file__).parent
SPRITES = ROOT / "sprites" / "hk47"
RAW = ROOT / "assets" / "anim-raw"  # generated frames, kept so nothing is ever re-bought

FRAME = 128  # theme frame size
FEET_Y = 93  # FEET_Y_NATIVE in main.rs
ANIM_W, ANIM_H = 72, 96  # generation canvas: figure plus weapon clearance

# Each entry: (action prompt, frame_count). Cost in generations == frame_count.
#
# The prompts are written as *motion*, not as description: animate-with-text-v3
# is being asked what changes between frames, and a prompt that describes the
# character instead of the movement returns eight near-identical frames.
SHOTS: dict[str, tuple[str, int]] = {
    # Sentry idle. Deliberately near-still: this plays most of the time, and a
    # bobbing assassin droid is the exact fault this whole pass is fixing.
    "idle": (
        "standing perfectly still at attention, only the faintest servo settle, "
        "head level, arms at sides, mechanical and motionless",
        8,
    ),
    # The two scans. Separate strips rather than one long one so the animator can
    # pick a side at random and they do not always play in the same order.
    "scan_left": (
        "robotically turning only the head 45 degrees to the left to scan the room, "
        "holding briefly, then snapping back to face forward, body still",
        8,
    ),
    "scan_right": (
        "robotically turning only the head 45 degrees to the right to scan the room, "
        "holding briefly, then snapping back to face forward, body still",
        8,
    ),
    # Weight shift, so consecutive idles are not identical.
    "stance": (
        "shifting weight from one leg to the other, resettling the stance, "
        "shoulders rolling back, mechanical and deliberate",
        8,
    ),
    # The set piece. One 16-frame arc: raise, aim, hold, look up, stand down.
    "combat": (
        "raising a blaster rifle into a two-handed combat stance and aiming forward, "
        "holding the aim, then lifting the head to look over the sights, "
        "lowering the weapon and relaxing back to standing",
        16,
    ),
    # Addressed directly: draws up and locks on.
    "attentive": (
        "straightening up sharply to attention, head lifting to lock on, "
        "photoreceptors brightening, alert posture",
        8,
    ),
    # Paired with the pacing translation sprite.rs applies in Thinking.
    "thinking": ("walking forward at a steady pace, side view, full walk cycle", 8),
    # Something he was asked to do failed.
    "error": (
        "malfunctioning violently, head jerking and twitching, servos failing, "
        "sparks, one arm spasming, body destabilising",
        8,
    ),
    # A session is waiting on an answer.
    "question": (
        "tilting the head to one side inquisitively, raising one hand palm up "
        "in a questioning gesture, then lowering it",
        8,
    ),
    # A session is stopped at a permission prompt.
    "permission": (
        "standing with arms crossed waiting impatiently, tapping one foot, "
        "head tilting down then back up, visibly unimpressed",
        8,
    ),
}


def balance() -> dict:
    r = requests.get(f"{API}/balance", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()


def generations_left() -> float:
    return balance().get("subscription", {}).get("generations", 0.0)


def poll_job(job_id: str, interval: int = 5, timeout: int = 600) -> dict:
    """Poll to completion. Live API goes 'processing' -> 'completed'."""
    done = {"completed", "done", "success", "finished", "succeeded"}
    fail = {"failed", "cancelled", "canceled", "error"}
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = requests.get(f"{API}/background-jobs/{job_id}", headers=HEADERS, timeout=30)
        r.raise_for_status()
        job = r.json()
        status = str(job.get("status", "")).lower()
        if status != last:
            print(f"    job {job_id[:8]}: {status}", flush=True)
            last = status
        if status in done:
            return job
        if status in fail:
            raise RuntimeError(f"job {job_id} ended {status}: {job.get('error')}")
        time.sleep(interval)
    raise TimeoutError(f"job {job_id} still running after {timeout}s")


def job_image(obj: dict) -> Image.Image:
    """Async results arrive in two shapes; branch on the magic bytes, never guess.

    A wrong guess either raises 'not enough image data' or silently writes a
    broken file, and the generation is already paid for by then.
    """
    raw = base64.b64decode(obj["base64"])
    if raw[:4] == b"\x89PNG":
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    return Image.frombytes("RGBA", (obj["width"], obj["height"]), raw)


def make_first_frame() -> Image.Image:
    """Crop the existing idle pose onto the generation canvas, feet near the floor.

    The source is the pack already on disk rather than the reference render, so
    the generated poses inherit the exact palette and outline the sprite already
    has and do not need re-quantizing back into agreement with it.
    """
    strip = Image.open(SPRITES / "idle.png").convert("RGBA")
    f0 = strip.crop((0, 0, FRAME, FRAME))
    box = f0.getbbox()
    body = f0.crop(box)
    canvas = Image.new("RGBA", (ANIM_W, ANIM_H), (0, 0, 0, 0))
    canvas.paste(body, ((ANIM_W - body.width) // 2, ANIM_H - 2 - body.height), body)
    return canvas


def animate(name: str, action: str, frame_count: int, first: Image.Image) -> list[Image.Image]:
    buf = io.BytesIO()
    first.save(buf, format="PNG")
    payload = {
        "first_frame": {"base64": base64.b64encode(buf.getvalue()).decode()},
        "action": action,
        "frame_count": frame_count,
    }
    r = requests.post(f"{API}/animate-with-text-v3", headers=HEADERS, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"{name}: HTTP {r.status_code} {r.text[:300]}")
    job = poll_job(r.json()["background_job_id"])
    return [job_image(o) for o in job["last_response"]["images"]]


def repin(frame: Image.Image) -> Image.Image:
    """Centre horizontally and pin the soles to FEET_Y in a 128px theme frame.

    The model re-centres and rescales between frames, so pinning by each frame's
    own bbox is what stops the character bobbing and sliding for reasons that have
    nothing to do with the animation being asked for.
    """
    out = Image.new("RGBA", (FRAME, FRAME), (0, 0, 0, 0))
    box = frame.getbbox()
    if box is None:
        return out
    body = frame.crop(box)
    out.paste(body, ((FRAME - body.width) // 2, FEET_Y - body.height), body)
    return out


def run(names: list[str]) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    first = make_first_frame()
    first.save(RAW / "_first_frame.png")

    planned = sum(SHOTS[n][1] for n in names)
    before = generations_left()
    print(f"balance before: {before:.0f} generations")
    print(f"planned spend : ~{planned} ({', '.join(names)})\n")

    for name in names:
        action, count = SHOTS[name]
        out_dir = RAW / name
        if out_dir.exists() and len(list(out_dir.glob("*.png"))) == count:
            print(f"{name:11s} already on disk, skipping (never re-buy)")
            continue
        print(f"{name:11s} {count} frames  → ~{count} generations")
        frames = animate(name, action, count, first)
        out_dir.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            f.save(out_dir / f"{i:02d}.png")
        print(f"{'':11s} saved {len(frames)} frames to {out_dir}")

    after = generations_left()
    print(f"\nbalance after : {after:.0f} generations   (spent {before - after:.0f})")


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SHOTS)
    unknown = [n for n in wanted if n not in SHOTS]
    if unknown:
        sys.exit(f"unknown shot(s): {unknown}\navailable: {list(SHOTS)}")
    run(wanted)
