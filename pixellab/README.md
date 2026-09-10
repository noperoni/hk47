# PixelLab notes for the HK-47 sprite pack

Measured against `GET /balance` either side of every call on 2026-09-10, on a
Tier 1 "Pixel Apprentice" subscription (2000 generations/month). **The cost table
in `~/.claude/skills/pixel/SKILL.md` is wrong for the character endpoints** —
it was written from the image endpoints and never re-measured against these.

## Measured costs

| Endpoint | Outputs | Measured cost |
|---|---|---|
| `create-character-v3` | **8 rotations** | **1–2 generations** |
| `characters/animations`, 8 frames | 9 frames | **4 generations** |
| `characters/animations`, 16 frames | 17 frames | **8 generations** |
| `create-character-state` | 8 rotations, edited | **40 generations** |
| `create-image-pixflux-background` @336x256 | 1 image | **1 generation** |
| `animate-with-text-v3`, 8 frames | 9 frames | 1 generation |

`create-character-state` is the only expensive call here by an order of
magnitude. Everything else is nearly free; iterate freely on prompts rather than
agonising over the first attempt.

The documented ">170px costs 20–40 generations" cliff did **not** apply to
`create-image-pixflux-background`: a 336x256 room cost 1, the same as a 168x128
one. Do not shrink a background to dodge a cliff that is not there.

## Gotchas that cost real generations to find

**`POST /characters/animations` returns `background_job_ids` — plural, a list**,
one per requested direction. Every other async endpoint here returns a singular
`background_job_id`. Reading the singular key returns nothing while the
generation bills anyway.

**`animate-with-text-v3` is the wrong tool for an existing character.** It has
only a picture to work from, so it reinterprets the subject into a different
character entirely, and it *flattens alpha*: an input with 4955 transparent
pixels came back fully opaque with a palette colour baked in behind, and one
frame came back on plain white. Animate the *character* instead — that endpoint
has the skeleton and the rotations, and it preserves transparency.

**`create-character-v3` passes the south rotation through nearly untouched.**
Feed it a photographic reference and you get seven generated pixel-art rotations
standing next to one muddy photograph. The reference must *already* be good
pixel art. Here that meant running the local `build-hk47-sprites.py` pipeline
with `LOGICAL_H` raised to fill the canvas, so the figure is ~122px rather than
the pack's 88 and the model has something to read.

**Result images arrive in two shapes.** Branch on the magic bytes: `89 50 4e 47`
is a PNG container, anything else is raw RGBA needing `Image.frombytes` with the
reported width and height. Guessing wrong either raises "not enough image data"
or silently writes a broken file, and the generation is already paid for. If a
save fails, re-fetch `GET /background-jobs/{id}` — that is free and still holds
the images. Never re-run a generation to recover from a save-side bug.

**The full endpoint list is at `GET /v2/openapi.json`** and is far richer than
the skill documents (~90 paths). `dumpschema.py` here prints any endpoint's
request schema.

## Assets produced

| Path | What |
|---|---|
| `assets/rot-base/` | 8 rotations, base character, 96x128 |
| `assets/rot-armed/` | 8 rotations holding a blaster rifle, 96x128 |
| `assets/anim-raw/<state>/` | animation frames, 172x172, alpha intact |
| `assets/room-raw/` | candidate diorama interiors, 336x256 |
| `pixellab/character-ids.json` | the character UUIDs — **do not lose these**, they are what makes further states and animations cheap |

`assets/anim-raw/scan_left/` is the failed `animate-with-text-v3` output, kept
only as evidence of the failure mode. It is not part of the pack.

`assets/anim-raw/combat/08.png` is a muzzle-flash frame. The brief says he
decides *not* to shoot, so that frame is dropped at assembly time.
