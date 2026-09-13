#!/usr/bin/env python3
"""Render HK-47 pack clips from hk47-wiring.tsv against a local KOTOR 1 install.

Every output clip is qualifier-first: the spoken qualifier leads, the source's
own pause follows, then the sentence. That pause is never invented. It is
measured from the source (hk47-gaps.tsv) and reinserted at the join, so a
spliced clip breathes the way the actor did.

Ops, from the wiring file:
  whole                       the clip unchanged
  cut     A-B                 keep one span
  splice  A-B+C-D             join two spans of this clip, natural pause between
  graft   OTHER:A-B+C-D       donor's span, pause, then this clip's span
  AUDITION                    skip, rendered separately by hand-placed offsets

Inside a span list, '~D' is D seconds of explicit silence rather than audio,
for the case where a word has to be excised and neither the source's own pause
nor DEFAULT_PAUSE is the right length to put in its place.

KOTOR's streamwaves/*.wav carry a stub RIFF header with a zero-length data
chunk followed by raw MP3 frames, so the sync word has to be found by hand
before ffmpeg will touch them.

Audio never leaves this machine: extracted from an owned install into the local
config dir, for personal use only.
"""

import csv
import os
import subprocess
import sys
import tempfile

KOTOR = os.environ.get("KOTOR_DIR") or os.path.expanduser(
    "~/.steam/steam/steamapps/common/swkotor")
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PAUSE = 0.45
JOIN_TOLERANCE = 0.15  # a span end this close to a silence start counts as that pause


def index_streamwaves(root):
    out = {}
    for dirpath, _dirs, names in os.walk(os.path.join(root, "streamwaves")):
        for n in names:
            if n.lower().endswith(".wav"):
                out[n[:-4].lower()] = os.path.join(dirpath, n)
    return out


def load_gaps(path):
    """resref -> [(silence_start, silence_end), ...]"""
    out = {}
    with open(path) as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or row[0].startswith("#"):
                continue
            spans = []
            for g in row[2].split(";"):
                if g:
                    a, b = g.split("-")
                    spans.append((float(a), float(b)))
            out[row[0]] = spans
    return out


def to_mp3(src_path):
    """Strip KOTOR's decoy RIFF header, return a temp path to the raw MP3."""
    blob = open(src_path, "rb").read()
    off = next(
        (i for i in range(len(blob) - 1)
         if blob[i] == 0xFF and (blob[i + 1] & 0xE0) == 0xE0),
        -1,
    )
    if off < 0:
        raise ValueError(f"no MP3 sync word in {src_path}")
    tmp = tempfile.mktemp(suffix=".mp3")
    with open(tmp, "wb") as fh:
        fh.write(blob[off:])
    return tmp


def pause_after(gaps, ref, t):
    """Length of the source's own silence starting at t, for reuse at a join."""
    for start, end in gaps.get(ref, []):
        if abs(start - t) <= JOIN_TOLERANCE:
            return round(end - start, 3)
    return DEFAULT_PAUSE


def parse_chunk(buf):
    """'A-B' -> (A, B).  '~D' -> ('pause', D), an explicit silence of D seconds."""
    if buf.startswith("~"):
        return ("pause", float(buf[1:]))
    return tuple(float(x) for x in buf.split("-"))


def parse_spans(text):
    """'A-B+C-D|E-F' -> [(A,B), '+', (C,D), '|', (E,F)].

    '+' joins with the source's own pause, for a join between sentences.
    '|' butt-joins, for a join inside one sentence where no pause belongs.

    A '~D' chunk is not audio but D seconds of silence, for the case where the
    source's own pause is the wrong length and the fallback is wrong too. Fence
    it with '|' on both sides so no implicit pause is added around it:
    'A-B|~0.30|C-D'.
    """
    out, buf = [], ""
    for ch in text:
        if ch in "+|":
            out.append(parse_chunk(buf))
            out.append(ch)
            buf = ""
        else:
            buf += ch
    out.append(parse_chunk(buf))
    return out


def render(parts, out_path):
    """parts: [(mp3_path, a, b), ...] joined with pauses given as (None, seconds)."""
    inputs, filters, seq = [], [], []
    srcs = {}
    for item in parts:
        if item[0] is not None and item[0] not in srcs:
            srcs[item[0]] = len(srcs)
    for path in srcs:
        inputs += ["-i", path]
    for i, item in enumerate(parts):
        if item[0] is None:
            filters.append(
                f"anullsrc=r=44100:cl=mono,atrim=0:{item[1]},asetpts=N/SR/TB[s{i}]"
            )
        else:
            idx = srcs[item[0]]
            filters.append(
                f"[{idx}:a]atrim={item[1]}:{item[2]},asetpts=N/SR/TB[s{i}]"
            )
        seq.append(f"[s{i}]")
    filters.append("".join(seq) + f"concat=n={len(seq)}:v=0:a=1[o]")
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs,
         "-filter_complex", ";".join(filters), "-map", "[o]",
         "-ac", "1", "-ar", "44100", "-c:a", "pcm_s16le", out_path],
        check=True,
    )


def main():
    out_root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "audition/decided")
    spec = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "hk47-wiring.tsv")
    if not os.path.isdir(KOTOR):
        raise SystemExit(f"KOTOR install not found: {KOTOR} (set KOTOR_DIR)")
    waves = index_streamwaves(KOTOR)
    gaps = load_gaps(os.path.join(HERE, "hk47-gaps.tsv"))

    mp3s = {}

    def mp3(ref):
        if ref not in mp3s:
            mp3s[ref] = to_mp3(waves[ref.lower()])
        return mp3s[ref]

    made, skipped = 0, []
    with open(spec) as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or row[0].startswith("#"):
                continue
            cat, ref, op, spans = row[0], row[1], row[2], row[3]
            if op == "AUDITION":
                skipped.append(f"{cat}/{ref} ({spans})")
                continue
            if ref.lower() not in waves:
                skipped.append(f"{cat}/{ref} (no audio)")
                continue

            if op == "whole":
                parts = [(mp3(ref), 0, 99)]
            elif op == "cut":
                a, b = parse_spans(spans)[0]
                parts = [(mp3(ref), a, b)]
            elif op in ("splice", "graft"):
                # graft's first span comes from a donor clip: its qualifier is
                # borrowed because this clip has none of its own.
                if op == "graft":
                    donor, spans = spans.split(":", 1)
                else:
                    donor = ref
                toks = parse_spans(spans)
                first, rest = toks[0], toks[1:]
                parts = [(mp3(donor), first[0], first[1])]
                prev_end, src = first[1], donor
                for i in range(0, len(rest), 2):
                    join, span = rest[i], rest[i + 1]
                    if span[0] == "pause":
                        # explicit silence: it is not audio, so it leaves
                        # prev_end alone and any '+' around it would double up.
                        parts.append((None, span[1]))
                        continue
                    if join == "+":
                        parts.append((None, pause_after(gaps, src, prev_end)))
                    parts.append((mp3(ref), span[0], span[1]))
                    prev_end, src = span[1], ref
            else:
                skipped.append(f"{cat}/{ref} (unknown op {op})")
                continue

            cat_dir = os.path.join(out_root, cat)
            os.makedirs(cat_dir, exist_ok=True)
            n = len([f for f in os.listdir(cat_dir) if f.endswith(".wav")]) + 1
            out = os.path.join(cat_dir, f"{n:02d}-{ref.rstrip('_')}.wav")
            render(parts, out)
            made += 1
            print(f"  {cat:24} {os.path.basename(out):26} "
                  f"{(os.path.getsize(out) - 44) / 88200:5.2f}s  {row[5]}")

    for path in mp3s.values():
        os.unlink(path)
    print(f"\nrendered {made} clips -> {out_root}")
    if skipped:
        print("skipped: " + ", ".join(skipped))


if __name__ == "__main__":
    main()
