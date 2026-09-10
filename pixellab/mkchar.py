#!/usr/bin/env python3
"""Generate candidate base characters and dump their rotation sheets for comparison.

One create-character-v3 call buys all eight rotations for one generation, so the
cheap move is to try several descriptions and pick, rather than to argue with the
first one. Every candidate is kept on disk under its own id.
"""
import base64, io, json, os, sys, time
from pathlib import Path
import requests
from PIL import Image

sys.path.insert(0, "/path/to/hk47")
from importlib.machinery import SourceFileLoader
bhs = SourceFileLoader("bhs", "/path/to/hk47/build-hk47-sprites.py").load_module()

API = "https://api.pixellab.ai/v2"
T = os.popen("fish -c 'echo $PIXELLAB_API_KEY'").read().strip()
H = {"Authorization": f"Bearer {T}", "Content-Type": "application/json"}
TMP = Path("/tmp/hk47")
OUT = TMP / "chars"
ORDER = ["south", "south-west", "west", "north-west", "north", "north-east", "east", "south-east"]


def bal():
    return requests.get(f"{API}/balance", headers=H, timeout=30).json()["subscription"]["generations"]


def b64(im):
    b = io.BytesIO(); im.save(b, format="PNG"); return base64.b64encode(b.getvalue()).decode()


def poll(jid, timeout=900):
    done, fail = {"completed", "done", "success", "finished", "succeeded"}, {"failed", "cancelled", "canceled", "error"}
    last, end = None, time.time() + timeout
    while time.time() < end:
        j = requests.get(f"{API}/background-jobs/{jid}", headers=H, timeout=30).json()
        s = str(j.get("status", "")).lower()
        if s != last: print("     ", s, flush=True); last = s
        if s in done: return j
        if s in fail: raise RuntimeError(f"{s}: {j.get('error')}")
        time.sleep(5)
    raise TimeoutError(jid)


def build_reference(w=96, h=128) -> Image.Image:
    """Run the existing local pipeline at full canvas height and return the sprite.

    Feeding the raw render instead was the first mistake: create-character-v3
    passes the south rotation through nearly untouched, so a photographic
    reference yields a photographic south view sitting next to seven generated
    pixel-art ones. Quantizing and outlining first, through the very pipeline
    that produced the pack hk47 approved, keeps all eight in the same idiom.

    LOGICAL_H is raised from the pack's 88 only for this reference: at 88 the
    figure is 33px wide and the model has nothing to read.
    """
    bhs.LOGICAL_H = h - 6
    src = Image.open("/path/to/hk47/assets/hk47-reference.png")
    sprite, eyes = bhs.build_sprite(src)
    bhs.paint_eyes(sprite, eyes, 1.0)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(sprite, ((w - sprite.width) // 2, h - 2 - sprite.height), sprite)
    return canvas


CANDIDATES = {
    "a_skeletal": dict(
        description=(
            "HK-47 assassination droid, Star Wars KOTOR. Rust-orange corroded armour plates "
            "bolted over a bare polished-silver metal endoskeleton. Exposed silver pistons and "
            "steel rods at the forearms, shins and knees. Angular insectoid head, flat wedge "
            "faceplate with a dark horizontal visor slit and two glowing red photoreceptors. "
            "Narrow shoulders, thin articulated limbs, digitigrade legs, gaunt and skeletal."
        ),
        detail="highly detailed", outline="selective outline",
    ),
    "b_render_faithful": dict(
        description=(
            "Weathered copper-and-steel protocol assassin droid standing at attention. "
            "Oxidised orange plating over silver machined joints, dark recessed chest panel, "
            "segmented abdomen, angular helmet-like head with a narrow dark eye band and two "
            "small red glowing lenses. Grim, still, military."
        ),
        detail="highly detailed", outline="single color black outline",
    ),
    "d_pixelref": dict(
        description=(
            "HK-47 assassination droid, Star Wars KOTOR. Rust-orange corroded armour plates "
            "bolted over a bare polished-silver metal endoskeleton. Exposed silver pistons and "
            "steel rods at the forearms, shins and knees. Angular insectoid head, flat wedge "
            "faceplate with a dark horizontal visor slit and two glowing red photoreceptors. "
            "Narrow shoulders, thin articulated limbs, digitigrade legs, gaunt and skeletal."
        ),
        detail="highly detailed", outline="single color black outline",
    ),
    "c_high_contrast": dict(
        description=(
            "Skeletal war droid, rust-red armour panels contrasting against bright bare-steel "
            "limb segments, sharp angular head with red optical sensors glowing in a black visor "
            "recess, exposed cabling at the joints, battered and pitted with corrosion, "
            "menacing upright stance, high contrast pixel art"
        ),
        detail="highly detailed", outline="selective outline",
    ),
}


def make(name: str, spec: dict, ref: Image.Image) -> str:
    payload = {
        "description": spec["description"],
        "reference_image": {"base64": b64(ref)},
        "image_size": {"width": ref.width, "height": ref.height},
        "view": "low top-down",
        "detail": spec.get("detail", "highly detailed"),
        "outline": spec.get("outline", "selective outline"),
        "no_background": True,
        "seed": spec.get("seed", 7),
    }
    r = requests.post(f"{API}/create-character-v3", headers=H, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"{name}: HTTP {r.status_code} {r.text[:400]}")
    d = r.json()
    cid = d["character_id"]
    print(f"  {name}: character {cid}")
    if d.get("background_job_id"):
        poll(d["background_job_id"])
    return cid


def fetch_sheet(name: str, cid: str) -> None:
    d = requests.get(f"{API}/characters/{cid}", headers=H, timeout=60).json()
    urls = d.get("rotation_urls") or {}
    dest = OUT / name
    dest.mkdir(parents=True, exist_ok=True)
    ims = []
    for k in ORDER:
        if k not in urls:
            continue
        rr = requests.get(urls[k], timeout=60)
        if rr.status_code != 200:
            print(f"    {k}: HTTP {rr.status_code}")
            continue
        im = Image.open(io.BytesIO(rr.content)).convert("RGBA")
        im.save(dest / f"{k}.png")
        ims.append(im)
    if not ims:
        return
    S, (w, h) = 4, ims[0].size
    sheet = Image.new("RGBA", (w * len(ims) * S, h * S), (40, 40, 46, 255))
    for i, im in enumerate(ims):
        big = im.resize((w * S, h * S), Image.NEAREST)
        sheet.paste(big, (i * w * S, 0), big)
    sheet.save(TMP / f"cand_{name}.png")
    print(f"    sheet -> cand_{name}.png {sheet.size}")


if __name__ == "__main__":
    wanted = sys.argv[1:] or list(CANDIDATES)
    OUT.mkdir(parents=True, exist_ok=True)
    ref = build_reference()
    ref.save(TMP / "hi_reference.png")
    print(f"reference {ref.size} -> hi_reference.png")
    before = bal()
    print(f"balance before: {before:.0f}")
    ids = {}
    idfile = TMP / "char_ids.json"
    if idfile.exists():
        ids = json.loads(idfile.read_text())
    for name in wanted:
        print(f"[{name}]")
        cid = make(name, CANDIDATES[name], ref)
        ids[name] = cid
        idfile.write_text(json.dumps(ids, indent=2))
        fetch_sheet(name, cid)
    print(f"balance after : {bal():.0f}  (spent {before - bal():.0f})")
    print(json.dumps(ids, indent=2))
