#!/usr/bin/env python3
"""Generate candidate diorama interiors and dump them for comparison.

Size is 336x256: the interior of a 400x320 backdrop with a 32px frame ring. It
has to be this big because the generated figure is 122px tall, and a 128px-tall
interior would stand him floor-to-ceiling. Downscaling him instead is not an
option: resampling is exactly the pixel-density mismatch being fixed.

The palette strip from the sprite pipeline is passed as color_image so the room
cannot drift away from the colours HK-47 himself is built from, which is what
makes a composite read as one picture rather than two.
"""
import base64, io, json, os, sys, time
from pathlib import Path
import requests
from PIL import Image

API = "https://api.pixellab.ai/v2"
T = os.popen("fish -c 'echo $PIXELLAB_API_KEY'").read().strip()
H = {"Authorization": f"Bearer {T}", "Content-Type": "application/json"}
TMP = Path("/tmp/hk47")
OUT = Path("/path/to/hk47/assets/room-raw")

W, HGT = 336, 256


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
        if s in fail: raise RuntimeError(f"{s}: {json.dumps(j)[:400]}")
        time.sleep(5)
    raise TimeoutError(jid)


def as_img(o):
    raw = base64.b64decode(o["base64"])
    if raw[:4] == b"\x89PNG":
        return Image.open(io.BytesIO(raw)).convert("RGBA")
    return Image.frombytes("RGBA", (o["width"], o["height"]), raw)


def palette_image() -> Image.Image:
    from importlib.machinery import SourceFileLoader
    bhs = SourceFileLoader("bhs", "/path/to/hk47/build-hk47-sprites.py").load_module()
    pal = list(bhs.HK47_PALETTE) + list(bhs.DOOR_PALETTE)
    im = Image.new("RGB", (len(pal), 1))
    im.putdata([tuple(c) for c in pal])
    return im.resize((len(pal) * 8, 8), Image.NEAREST)


# The brief, restated as prompts. Every one of these has to put a receding floor
# under his feet and walls with depth: a flat wall meeting a flat floor at a hard
# horizontal line is the exact fault being fixed.
ROOMS = {
    "lit_alcove": (
        "Interior of a derelict starship maintenance alcove in receding three-quarter "
        "perspective, brightly lit. Overhead strip lights and amber work lamps throw warm "
        "light down the walls and pool across a deck-plate floor that recedes toward a "
        "sealed blast door. Angled side bulkheads of rusted orange hull plating, exposed "
        "conduit runs, a wall power junction, a lit status screen, warning stencils. "
        "Clear empty floor in the centre. Well lit, readable, mid-tone, not dark."
    ),
    "workshop": (
        "Bright starship droid workshop in receding three-quarter perspective. Ceiling "
        "light panels blazing white, warm amber glow from a wall console, a grated deck "
        "floor receding toward a bulkhead door, converging side walls of corroded orange "
        "plating with pipework, a toolrack, a coolant drum, hanging cables. Mid-tone "
        "lighting, strong readable contrast, clear empty centre floor."
    ),
    "hangar": (
        "Starship hangar alcove interior, receding three-quarter perspective, lit by bright "
        "overhead floodlights. Wide metal deck plating running away from the viewer with "
        "painted guide stripes, converging rust-orange bulkhead walls with rivets and access "
        "panels, a large closed pressure door at the back, warm light spilling across the "
        "floor. Bright, clean contrast, empty centre floor, no characters."
    ),
}


def generate(name: str, description: str) -> list[Image.Image]:
    payload = {
        "description": description,
        "image_size": {"width": W, "height": HGT},
        "view": "low top-down",
        "detail": "highly detailed",
        "shading": "detailed shading",
        "outline": "selective outline",
        "text_guidance_scale": 9,
        "color_image": {"base64": b64(palette_image())},
        "no_background": False,
        "seed": 11,
    }
    r = requests.post(f"{API}/create-image-pixflux-background", headers=H, json=payload, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"{name}: HTTP {r.status_code} {r.text[:600]}")
    d = r.json()
    jids = d.get("background_job_ids") or ([d["background_job_id"]] if d.get("background_job_id") else [])
    out = []
    for jid in jids:
        job = poll(jid)
        resp = job.get("last_response") or {}
        cost = (job.get("usage") or {}).get("generations")
        imgs = resp.get("images") or ([resp["image"]] if resp.get("image") else [])
        print(f"      job {jid[:8]} → {len(imgs)} images, {cost} generations")
        out += [as_img(o) for o in imgs]
    if not out and d.get("image"):
        out = [as_img(d["image"])]
    return out


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(ROOMS)
    OUT.mkdir(parents=True, exist_ok=True)
    before = bal()
    print(f"balance before: {before:.0f}")
    for name in wanted:
        print(f"[{name}]")
        ims = generate(name, ROOMS[name])
        for i, im in enumerate(ims):
            im.save(OUT / f"{name}_{i}.png")
        if ims:
            S = 3
            sh = Image.new("RGBA", (W * len(ims) * S, HGT * S), (20, 20, 24, 255))
            for i, im in enumerate(ims):
                sh.paste(im.resize((W * S, HGT * S), Image.NEAREST), (i * W * S, 0))
            sh.save(TMP / f"room_{name}.png")
            print(f"      -> room_{name}.png {sh.size}")
    print(f"balance after : {bal():.0f}  (spent {before - bal():.0f})")
