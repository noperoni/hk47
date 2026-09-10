#!/usr/bin/env python3
"""Build and install the HK-47 CESP sound pack from hk47-wiring.tsv.

The wiring file is the decision record: one row per output clip, carrying the
source clip, the op (whole / cut / splice / graft), the spans and the exact
spoken text. This script turns those 96 rows into an installed peon-ping pack.

It does not re-decide anything and it does not re-cut anything. The actual audio
work lives in hk47-render.py, whose helpers are imported here, because a second
implementation of the splice arithmetic is a second place for it to be wrong.

Two compatibility notes, both deliberate:

  * peon-ping only routes seven category names today. The other eighteen in the
    wiring file are written into the manifest anyway and sit inert until the
    peon.sh seams exist: an unrouted key is a dict lookup that never happens,
    not an error.
  * task.complete.self and task.complete.master are a split that needs a
    classifier nobody has written. Until then the manifest also carries a plain
    task.complete key aliasing the .self clips, because that is the one peon.sh
    fires on Stop and an empty category means silence where there used to be a
    line. ponytail: drop the alias once the classifier can tell the two apart.

Audio stays local: extracted from the game you own, into your own config dir,
and never leaving this machine.
"""

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PACK_NAME = "hk47"
WIRING = os.path.join(HERE, "hk47-wiring.tsv")

# The category peon.sh fires on Stop, and the one it should draw from until the
# .self / .master split has a driver.
ALIAS_SOURCE = "task.complete.self"
ALIAS_TARGET = "task.complete"


def load_renderer():
    """hk47-render.py is not an importable module name, so load it by path."""
    spec = importlib.util.spec_from_file_location(
        "hk47_render", os.path.join(HERE, "hk47-render.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def parts_for(row, r, waves, gaps, mp3):
    """Translate one wiring row into hk47-render.py's parts list."""
    ref, op, spans = row[1], row[2], row[3]
    if op == "whole":
        return [(mp3(ref), 0, 99)]
    if op == "cut":
        a, b = r.parse_spans(spans)[0]
        return [(mp3(ref), a, b)]
    if op not in ("splice", "graft"):
        raise ValueError(f"unknown op {op}")

    donor, spans = spans.split(":", 1) if op == "graft" else (ref, spans)
    toks = r.parse_spans(spans)
    first, rest = toks[0], toks[1:]
    parts = [(mp3(donor), first[0], first[1])]
    prev_end, src = first[1], donor
    for i in range(0, len(rest), 2):
        join, span = rest[i], rest[i + 1]
        if span[0] == "pause":
            parts.append((None, span[1]))
            continue
        if join == "+":
            parts.append((None, r.pause_after(gaps, src, prev_end)))
        parts.append((mp3(ref), span[0], span[1]))
        prev_end, src = span[1], ref
    return parts


def read_wiring(path):
    """[(category, row), ...] in file order, so numbering is stable across runs."""
    rows = []
    with open(path) as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or row[0].startswith("#") or len(row) < 6:
                continue
            if row[2] == "AUDITION":
                continue
            rows.append(row)
    return rows


def build(packs_dir, rows, r, waves, gaps):
    """Render every row into a fresh pack dir, preserving the old one."""
    out = os.path.join(packs_dir, PACK_NAME)
    icon = None
    if os.path.exists(out):
        icon_path = os.path.join(out, "icon.png")
        if os.path.isfile(icon_path):
            icon = open(icon_path, "rb").read()
        backup = os.path.join(
            os.path.dirname(packs_dir),
            f".{PACK_NAME}-pack-backup-{time.strftime('%Y%m%d-%H%M%S')}",
        )
        shutil.move(out, backup)
        print(f"  previous pack moved to {backup}")
    os.makedirs(os.path.join(out, "sounds"))
    if icon is not None:
        # The notification icon is a supported seam, not a code change: losing it
        # here would put the green orc back on every HK-47 popup.
        with open(os.path.join(out, "icon.png"), "wb") as fh:
            fh.write(icon)
    else:
        print("  WARNING: no icon.png carried over, notifications fall back to the orc",
              file=sys.stderr)

    mp3s = {}

    def mp3(ref):
        if ref not in mp3s:
            mp3s[ref] = r.to_mp3(waves[ref.lower()])
        return mp3s[ref]

    categories, seq, failed = {}, {}, []
    for row in rows:
        cat, ref, label = row[0], row[1], row[5]
        if ref.lower() not in waves:
            failed.append(f"{cat}/{ref} (no audio)")
            continue
        seq[cat] = seq.get(cat, 0) + 1
        name = f"{cat}-{seq[cat]:02d}-{ref.rstrip('_')}.wav"
        dst = os.path.join(out, "sounds", name)
        try:
            r.render(parts_for(row, r, waves, gaps, mp3), dst)
        except Exception as exc:  # a bad span must name itself, not vanish
            failed.append(f"{cat}/{ref} ({exc})")
            continue
        categories.setdefault(cat, {"sounds": []})["sounds"].append(
            {"file": f"sounds/{name}", "label": label, "sha256": sha256(dst)}
        )

    for path in mp3s.values():
        os.unlink(path)

    if ALIAS_SOURCE in categories and ALIAS_TARGET not in categories:
        categories[ALIAS_TARGET] = {"sounds": list(categories[ALIAS_SOURCE]["sounds"])}

    manifest = {
        "cesp_version": "1.0",
        "name": PACK_NAME,
        "display_name": "HK-47 (KOTOR)",
        "version": "2.0.0",
        "author": {"name": "hk47", "github": "local"},
        # Extracted from a locally owned copy for personal use. Not for redistribution.
        "license": "proprietary-local",
        "language": "en",
        "categories": categories,
    }
    with open(os.path.join(out, "openpeon.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")

    total = sum(len(c["sounds"]) for c in categories.values())
    print(f"  {total} clips across {len(categories)} categories -> {out}")
    if failed:
        print("  FAILED: " + ", ".join(failed), file=sys.stderr)
    return failed


def main():
    r = load_renderer()
    if not os.path.isdir(r.KOTOR):
        raise SystemExit(f"KOTOR install not found: {r.KOTOR} (set KOTOR_DIR)")
    waves = r.index_streamwaves(r.KOTOR)
    gaps = r.load_gaps(os.path.join(HERE, "hk47-gaps.tsv"))
    rows = read_wiring(WIRING)
    print(f"wiring rows: {len(rows)}; streamwaves indexed: {len(waves)}")

    home = os.path.expanduser("~")
    problems = []
    for cfg in (".claude-personal", ".claude-work"):
        packs = os.path.join(home, cfg, "hooks", "peon-ping", "packs")
        if not os.path.isdir(packs):
            print(f"skip {cfg}: no peon-ping install")
            continue
        print(cfg)
        problems += build(packs, rows, r, waves, gaps)
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
