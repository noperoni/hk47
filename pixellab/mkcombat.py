#!/usr/bin/env python3
"""Animate the armed state: raise, aim, decide it is not worth shooting, stand down.

Run against the 'armed' character rather than the base one, because the base has
no weapon and asking an animation to conjure one mid-sequence is what produced
the off-model mess on the first attempt.
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
CHAR = json.loads((TMP / "char_ids.json").read_text())["armed"]

SHOTS = {
    "combat": ("raising the blaster rifle to the shoulder and aiming forward down the sights, "
               "holding the aim, then lowering the weapon back across the body", 16),
}


def bal():
    return requests.get(f"{API}/balance", headers=H, timeout=30).json()["subscription"]["generations"]


def poll(jid, timeout=1800):
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
    return Image.open(io.BytesIO(raw)).convert("RGBA") if raw[:4] == b"\x89PNG" \
        else Image.frombytes("RGBA", (o["width"], o["height"]), raw)


for name, (action, frames) in SHOTS.items():
    dest = OUT / name
    if dest.exists() and len(list(dest.glob("*.png"))) >= frames:
        print(f"[{name}] already on disk"); continue
    before = bal()
    print(f"[{name}] {frames} frames on armed character {CHAR}")
    p = {"character_id": CHAR, "animation_name": f"hk47_{name}", "action_description": action,
         "mode": "v3", "frame_count": frames, "directions": ["south"], "keep_first_frame": True, "seed": 7}
    r = requests.post(f"{API}/characters/animations", headers=H, json=p, timeout=90)
    if r.status_code >= 400:
        sys.exit(f"HTTP {r.status_code} {r.text[:500]}")
    d = r.json()
    ims = []
    for jid in d.get("background_job_ids") or [d.get("background_job_id")]:
        job = poll(jid)
        resp = job.get("last_response") or {}
        print(f"      {jid[:8]} → {len(resp.get('images') or [])} frames, {(job.get('usage') or {}).get('generations')} gen")
        ims += [as_img(o) for o in (resp.get("images") or [])]
    dest.mkdir(parents=True, exist_ok=True)
    for i, im in enumerate(ims):
        im.save(dest / f"{i:02d}.png")
    S, (w, h) = 3, ims[0].size
    sh = Image.new("RGBA", (w * len(ims) * S, h * S), (40, 40, 46, 255))
    for i, im in enumerate(ims):
        b = im.resize((w * S, h * S), Image.NEAREST); sh.paste(b, (i * w * S, 0), b)
    sh.save(TMP / f"anim_{name}.png")
    print(f"     saved {len(ims)} frames; spent {before - bal():.0f}")
