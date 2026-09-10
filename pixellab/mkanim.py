#!/usr/bin/env python3
"""Animate the chosen character through /characters/animations and dump contact sheets.

Animating the character rather than a loose image is what keeps every frame
on-model: the endpoint has the character's own skeleton and rotations to work
from, where animate-with-text-v3 had only a picture and reinterpreted him into a
different droid entirely.

`directions` is pinned to south because that is the only view the companion ever
shows head-on; the other seven exist for the scan, which is composed locally from
the rotation sheet and costs nothing.
"""
import base64, io, json, os, sys, time
from pathlib import Path
import requests
from PIL import Image

API = "https://api.pixellab.ai/v2"
T = os.popen("fish -c 'echo $PIXELLAB_API_KEY'").read().strip()
H = {"Authorization": f"Bearer {T}", "Content-Type": "application/json"}
TMP = Path("/tmp/hk47")
OUT = Path("/path/to/hk47/assets/anim-raw")

CHAR = json.loads((TMP / "char_ids.json").read_text())["d_pixelref"]

SHOTS: dict[str, tuple[str, int]] = {
    "idle": ("standing still at attention, minimal mechanical servo settle, arms at sides", 8),
    "stance": ("shifting weight from one leg to the other and resettling the stance", 8),
    "combat": ("raising a blaster rifle to aim forward, then lowering it back down", 16),
    "attentive": ("straightening up sharply to attention and locking on", 8),
    "thinking": ("walking forward, full walk cycle", 8),
    "error": ("malfunctioning, head jerking, servos spasming, body destabilising", 8),
    "question": ("tilting head to one side and raising one hand palm up questioningly", 8),
    "permission": ("crossing arms and tapping one foot impatiently while waiting", 8),
}


def bal():
    return requests.get(f"{API}/balance", headers=H, timeout=30).json()["subscription"]["generations"]


def b64(im):
    b = io.BytesIO(); im.save(b, format="PNG"); return base64.b64encode(b.getvalue()).decode()


def poll(jid, timeout=1200):
    done, fail = {"completed", "done", "success", "finished", "succeeded"}, {"failed", "cancelled", "canceled", "error"}
    last, end = None, time.time() + timeout
    while time.time() < end:
        j = requests.get(f"{API}/background-jobs/{jid}", headers=H, timeout=30).json()
        s = str(j.get("status", "")).lower()
        if s != last: print("     ", s, flush=True); last = s
        if s in done: return j
        if s in fail: raise RuntimeError(f"{s}: {json.dumps(j)[:500]}")
        time.sleep(5)
    raise TimeoutError(jid)


def as_img(o):
    raw = base64.b64decode(o["base64"])
    if raw[:4] == b"\x89PNG":
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    return Image.frombytes("RGBA", (o["width"], o["height"]), raw)


def palette_image() -> Image.Image:
    """The pack's own palette as a strip, handed to force_colors so generated
    frames cannot drift off the colours the rest of the diorama is built from."""
    from importlib.machinery import SourceFileLoader
    bhs = SourceFileLoader("bhs", "/path/to/hk47/build-hk47-sprites.py").load_module()
    pal = bhs.HK47_PALETTE
    im = Image.new("RGB", (len(pal), 1))
    im.putdata([tuple(c) for c in pal])
    return im.resize((len(pal) * 8, 8), Image.NEAREST)


def animate(name: str, action: str, frames: int) -> list[Image.Image]:
    payload = {
        "character_id": CHAR,
        "animation_name": f"hk47_{name}",
        "action_description": action,
        "mode": "v3",
        "frame_count": frames,
        "directions": ["south"],
        "keep_first_frame": True,
        "seed": 7,
    }
    r = requests.post(f"{API}/characters/animations", headers=H, json=payload, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"{name}: HTTP {r.status_code} {r.text[:600]}")
    d = r.json()
    # Note the plural: this endpoint returns background_job_ids as a LIST, one
    # per requested direction, unlike every other async endpoint here which
    # returns a singular background_job_id. Reading the singular key silently
    # yields nothing while the generation bills anyway.
    jids = d.get("background_job_ids") or ([d["background_job_id"]] if d.get("background_job_id") else [])
    out: list[Image.Image] = []
    for jid in jids:
        job = poll(jid)
        resp = job.get("last_response") or {}
        cost = (job.get("usage") or {}).get("generations")
        print(f"      job {jid[:8]} → {len(resp.get('images') or [])} frames, {cost} generations")
        out += [as_img(o) for o in (resp.get("images") or [])]
    return out


def sheet(name: str, ims: list[Image.Image]) -> None:
    if not ims:
        return
    S, (w, h) = 4, ims[0].size
    s = Image.new("RGBA", (w * len(ims) * S, h * S), (40, 40, 46, 255))
    for i, im in enumerate(ims):
        big = im.resize((w * S, h * S), Image.NEAREST)
        s.paste(big, (i * w * S, 0), big)
    s.save(TMP / f"anim_{name}.png")
    print(f"     sheet -> anim_{name}.png {s.size}")


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(SHOTS)
    before = bal()
    print(f"character {CHAR}")
    print(f"balance before: {before:.0f}")
    for name in wanted:
        action, frames = SHOTS[name]
        dest = OUT / name
        if dest.exists() and len(list(dest.glob("*.png"))) >= frames:
            print(f"[{name}] already on disk, skipping")
            continue
        print(f"[{name}] {frames} frames — {action}")
        ims = animate(name, action, frames)
        if ims:
            dest.mkdir(parents=True, exist_ok=True)
            for i, im in enumerate(ims):
                im.save(dest / f"{i:02d}.png")
            print(f"     saved {len(ims)} frames -> {dest}")
            sheet(name, ims)
    print(f"balance after : {bal():.0f}  (spent {before - bal():.0f})")
