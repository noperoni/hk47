#!/usr/bin/env python3
"""Re-probe every cut and span edge in hk47-wiring.tsv with a 10ms RMS envelope.

Why this exists. The wiring's spans were chosen from hk47-gaps.tsv, which is
ffmpeg silencedetect at -38dB with a 0.20s minimum. Clip 07122 proved that pass
can be blind: the boundary that mattered was a dip too short and too shallow for
silencedetect to call it silence, so the span end landed inside a word and the
clip clipped a syllable. A 10ms RMS envelope found it immediately.

The test, which is the lesson 07122 taught stated as a rule: a cut is clean when
it LANDS AT A DIP. Not when the discarded side is quiet, because loud audio after
a cut is the ordinary reason for cutting there: the verified 07122 boundary has
the next sentence starting immediately outside it and is entirely correct. And
not when the kept side is loud, because a span start is supposed to open on a
word.

So for each boundary this finds the dips, meaning RUN_MS or more of audio at
least QUIET_DB below that source's own speech level, and reports how far the cut
sits from the nearest edge of the nearest one. That distance is the length of
audio misplaced: a cut 150ms from its dip opened 150ms into a word. Two numbers
come with it:

    inside    loudest 10ms frame in the AT_MS immediately kept
    outside   loudest 10ms frame in the AT_MS immediately discarded

A span start of 0.00 and a span end at or past the file's duration are not cuts
and are not judged. `whole` rows have no cuts at all.

The floor is measured, never assumed: it is the given percentile of every 10ms
frame across every source the wiring actually touches, so a remaster or a
different extraction recalibrates itself.

RESULT OF THE FIRST FULL SWEEP, 2026-09-11. 200 boundaries across 96 wiring
rows: 195 clean, 4 CLIPPED, 1 TIGHT. Master then listened to all five against
re-cut alternatives and overruled every one of them, so the shipped wiring is
correct by the only instrument that decides, and the five below are known false
positives rather than outstanding work:

    07319 start 6.10   07031 end 5.58   07244 start 5.55
    07029 end 3.32     07426 end 1.48 (tight)

What that calibrates: the rule is sound but still stricter than an ear. A cut
100 to 260ms off a dip is measurable and inaudible, which means this sweep is a
tripwire for a NEW cut, not an audit of a decided one. Anything it flags goes to
a rendered A/B before it goes into the wiring.

Read-only. It decodes from the owned install, reports, and writes nothing.
"""

import argparse
import csv
import importlib.util
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
FRAME_MS = 10      # envelope resolution, the thing silencedetect did not have
AT_MS = 20         # "at the boundary" is this much either side of it
PEAK_PCT = 95.0    # percentile of a source's own frames taken as its speech level
FLOOR_PCT = 20.0   # percentile of all frames taken as the noise floor, reported only

# "Quiet" is relative to the source's OWN speech level, never an absolute dBFS
# line. MEASURED 2026-09-11: these recordings differ by more than 10dB file to
# file, and four sources carry room tone only 16 to 20dB under speech, which an
# absolute threshold reads as a clipped word when it is simply a quiet onset.
QUIET_DB = 30.0    # this far under a source's speech level counts as quiet
RUN_MS = 40        # and for this long before it counts as a dip
SLACK_MS = 60      # a dip this close to the cut is the dip the cut was aiming at
TIGHT_MS = 120     # past that and up to here: worth an ear, not yet a defect


def load_render():
    """Borrow the renderer's own span parsing so the two can never disagree."""
    path = os.path.join(HERE, "hk47-render.py")
    spec = importlib.util.spec_from_file_location("hk47_render", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def envelope(mp3_path):
    """10ms RMS envelope in dBFS, plus the duration in seconds."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", mp3_path,
         "-ac", "1", "-ar", "44100", "-f", "s16le", "-"],
        check=True, stdout=subprocess.PIPE,
    ).stdout
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    hop = int(44100 * FRAME_MS / 1000)
    n = len(pcm) // hop
    if n == 0:
        return np.array([]), 0.0
    frames = pcm[: n * hop].reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    # -120dB stands in for a frame of true digital silence.
    db = 20.0 * np.log10(np.maximum(rms, 1e-6))
    return db, len(pcm) / 44100.0


def audio_spans(mod, row, gaps):
    """[(ref, a, b), ...] for one wiring row: the audio parts only, in order.

    Mirrors hk47-render's part building, minus the pauses, which are generated
    silence and have no boundary to judge.
    """
    cat, ref, op, spans = row[0], row[1], row[2], row[3]
    if op == "whole":
        return []
    if op == "cut":
        a, b = mod.parse_spans(spans)[0]
        return [(ref, a, b)]
    if op in ("splice", "graft"):
        donor = ref
        if op == "graft":
            donor, spans = spans.split(":", 1)
        toks = mod.parse_spans(spans)
        first, rest = toks[0], toks[1:]
        out = [(donor, first[0], first[1])]
        for i in range(1, len(rest), 2):
            span = rest[i]
            if span[0] == "pause":
                continue
            out.append((ref, span[0], span[1]))
        return out
    return []


def boundaries(db, dur, a, b):
    """Yield (which, inside, outside) for each boundary of one span that is a cut."""
    f = lambda t: int(round(t * 1000 / FRAME_MS))
    w = max(1, int(round(AT_MS / FRAME_MS)))

    def band(lo, hi):
        lo, hi = max(0, lo), min(len(db), hi)
        return float(db[lo:hi].max()) if hi > lo else None

    # A start at the very top of the file cut nothing.
    if a > 0.001:
        yield "start", f(a), band(f(a), f(a) + w), band(f(a) - w, f(a))
    # An end at or past the file's duration cut nothing either.
    if b < dur - 0.011:
        yield "end", f(b), band(f(b) - w, f(b)), band(f(b), f(b) + w)


def quiet_runs(db, peak):
    """[(first_frame, last_frame_exclusive), ...] of every dip in one source."""
    quiet = db < (peak - QUIET_DB)
    need = max(1, int(round(RUN_MS / FRAME_MS)))
    out, start = [], None
    for i, q in enumerate(quiet):
        if q:
            start = i if start is None else start
        elif start is not None:
            if i - start >= need:
                out.append((start, i))
            start = None
    if start is not None and len(quiet) - start >= need:
        out.append((start, len(quiet)))
    return out


def judge(idx, runs, inside, quiet_level):
    """verdict, and the ms from the cut to the nearest edge of any dip.

    That distance IS the length of audio misplaced: a cut 150ms from the dip it
    was aiming at either opened 150ms into a word or left 150ms of one behind.
    Which side the dip sits on does not change the size of the error, so the
    rule does not ask, and a start and an end are judged the same way.
    """
    # Nothing audible can have been severed if the kept edge is itself silent,
    # however short that silence is. This catches the span that opens on 20ms of
    # room tone, which is too brief to be a dip by RUN_MS but is plainly clean.
    if inside is not None and inside < quiet_level:
        return "clean", 0.0
    # A cut that lands INSIDE a dip is as clean as one that lands on its edge.
    if any(s <= idx < e for s, e in runs):
        return "clean", 0.0
    edges = [t for run in runs for t in run]
    if not edges:
        return "CLIPPED", float("inf")
    dist = min(abs(t - idx) for t in edges) * FRAME_MS
    if dist <= SLACK_MS:
        return "clean", float(dist)
    if dist <= TIGHT_MS:
        return "TIGHT", float(dist)
    return "CLIPPED", float(dist)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wiring", default=os.path.join(HERE, "hk47-wiring.tsv"))
    ap.add_argument("--all", action="store_true",
                    help="list every boundary, not only the ones that failed")
    args = ap.parse_args()

    mod = load_render()
    if not os.path.isdir(mod.KOTOR):
        raise SystemExit(f"KOTOR install not found: {mod.KOTOR} (set KOTOR_DIR)")
    waves = mod.index_streamwaves(mod.KOTOR)
    gaps = mod.load_gaps(os.path.join(HERE, "hk47-gaps.tsv"))

    rows = []
    with open(args.wiring) as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if row and not row[0].startswith("#") and row[2] != "AUDITION":
                rows.append(row)

    # Decode each source once. The envelope is what everything below reads.
    envs = {}

    def env(ref):
        key = ref.lower()
        if key not in envs:
            if key not in waves:
                envs[key] = (None, 0.0)
            else:
                tmp = mod.to_mp3(waves[key])
                try:
                    envs[key] = envelope(tmp)
                finally:
                    os.unlink(tmp)
        return envs[key]

    work = []
    for row in rows:
        for ref, a, b in audio_spans(mod, row, gaps):
            env(ref)
            work.append((row, ref, a, b))

    pool = np.concatenate([d for d, _ in envs.values() if d is not None and len(d)])
    floor = float(np.percentile(pool, FLOOR_PCT))
    print(f"sources decoded: {len(envs)}   frames: {len(pool)}   "
          f"floor (p{FLOOR_PCT:g}): {floor:.1f} dBFS")
    print(f"frame {FRAME_MS}ms   quiet = {QUIET_DB:g}dB under own speech for "
          f"{RUN_MS}ms   slack {SLACK_MS}ms, tight to {TIGHT_MS}ms\n")

    findings, checked, missing = [], 0, []
    peaks, runs = {}, {}
    for row, ref, a, b in work:
        db, dur = env(ref)
        if db is None:
            missing.append(f"{row[0]}/{ref}")
            continue
        key = ref.lower()
        if key not in peaks:
            peaks[key] = float(np.percentile(db, PEAK_PCT))
            runs[key] = quiet_runs(db, peaks[key])
        for which, idx, inside, outside in boundaries(db, dur, a, b):
            checked += 1
            verdict, dist = judge(idx, runs[key], inside,
                                  peaks[key] - QUIET_DB)
            if verdict != "clean" or args.all:
                findings.append((dist, verdict, row, ref, which,
                                 a if which == "start" else b,
                                 inside if inside is not None else float("nan"),
                                 outside if outside is not None else float("nan"),
                                 peaks[key]))

    findings.sort(key=lambda x: -x[0])
    hdr = (f"{'verdict':8} {'into':>6}  {'category':22} {'resref':18} {'edge':5} "
           f"{'t':>6}  {'in':>7} {'out':>7} {'peak':>7}  label")
    print(hdr)
    print("-" * len(hdr))
    for dist, verdict, row, ref, which, t, inside, outside, peak in findings:
        label = row[5] if len(row) > 5 else ""
        shown = "none" if dist == float("inf") else f"{dist:.0f}ms"
        print(f"{verdict:8} {shown:>6}  {row[0]:22} {ref.rstrip('_'):18} "
              f"{which:5} {t:6.2f}  {inside:7.1f} {outside:7.1f} {peak:7.1f}  "
              f"{label[:52]}")

    bad = sum(1 for f in findings if f[1] == "CLIPPED")
    tight = sum(1 for f in findings if f[1] == "TIGHT")
    print(f"\n{checked} boundaries checked across {len(rows)} wiring rows: "
          f"{bad} CLIPPED, {tight} TIGHT, {checked - bad - tight} clean")
    if missing:
        print("no audio for: " + ", ".join(sorted(set(missing))))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
