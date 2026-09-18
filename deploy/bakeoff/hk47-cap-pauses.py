"""Cap over-long internal silences in a render, in place of re-rolling for one.

    docker exec omnivoice python3 /root/.omnivoice/bakeoff/hk47-cap-pauses.py \
        --ceiling 0.35 out/a.wav out/b.wav

Master ruled on 2026-09-18 that the pause before "master" runs about twice as
long as it should. The obvious suspects were all innocent: five identical rolls
of the same reference, text and settings put that gap at 0.86s, 0.00s, 0.64s,
0.30s and 0.30s. IndexTTS samples pause length across most of a second, so the
good renders were luck and no punctuation edit, normalisation switch or clip
reordering can reach it.

That leaves two honest fixes. Best-of-N re-rolls until the aligner likes the
result, which spends a GPU render per attempt and still has no guaranteed stop.
Or this: walk the rendered audio and shorten any internal silence past the
ceiling. A pause is silence, so trimming it removes nothing but duration, and it
costs no GPU and is deterministic on every engine in the bake-off rather than
only on this one.

The ceiling is 0.35s rather than zero because HK-47 does pause before the
address, and a render with no gap at all is the opposite defect: roll2 above hit
0.00s and ran "complete master" together as one word.

MEASURED 2026-09-18, AND IT IS NOT ENOUGH. Capping at 0.35s drove ref31's gap
before "master" to 0.00s, the run-together defect, while leaving roll1's at 0.68s
from 0.86s. The two disagree because what the aligner calls a gap is mostly not
floor-level silence: it is the decay tail of the preceding word and a breath
above the floor, so a silence cap reaches a different quantity than the one being
judged. Tuning the ceiling trades one file's over-correction for another's.

THE VERIFICATION ABOVE WAS BLIND, amended 2026-09-18. "Drove ref31's gap to
0.00s" was read off hk47-word-gaps.py, whose alignment collapses to a uniform
0.00s without warning, so that number never described the audio. The negative
result still stands on its own reasoning, that a silence cap reaches a different
quantity than the one being judged, but its headline measurement does not.

What the evidence actually supports is best-of-N. Good rolls are common, two of
five landed at 0.30s, so rendering a handful and letting hk47-word-gaps.py pick
the one nearest target is bounded, cheap on an engine at rtf under 1, and judges
exactly the quantity Master ruled on. This file is kept for the negative result,
which took five renders and two alignment passes to establish.

Leading and trailing silence are left alone. They are not pauses in the delivery
and whatever plays the file decides what to do with them.
"""

import argparse
import os
import shutil

import numpy as np
import soundfile as sf

FRAME_MS = 10
FLOOR = 0.01  # RMS below this is silence, matching the other probes in this dir


def cap(path, ceiling, out_path):
    x, sr = sf.read(path, dtype="float32")
    mono = x.mean(1) if x.ndim > 1 else x
    frame = int(sr * FRAME_MS / 1000)
    usable = len(mono) // frame * frame
    energy = np.sqrt((mono[:usable].reshape(-1, frame) ** 2).mean(1))
    voiced = energy >= FLOOR
    if not voiced.any():
        return None

    first, last = int(np.argmax(voiced)), len(voiced) - 1 - int(np.argmax(voiced[::-1]))
    ceiling_frames = int(ceiling * 1000 / FRAME_MS)

    keep, trimmed, run_start = [], [], None
    for i in range(first, last + 2):
        silent = i <= last and not voiced[i]
        if silent and run_start is None:
            run_start = i
        elif not silent and run_start is not None:
            run = i - run_start
            if run > ceiling_frames:
                trimmed.append((run_start * FRAME_MS / 1000, run * FRAME_MS / 1000))
                keep.append((run_start, run_start + ceiling_frames))
            else:
                keep.append((run_start, i))
            run_start = None
        elif not silent:
            keep.append((i, i + 1))

    if not trimmed:
        return []

    pieces = [x[:first * frame]]
    for start, end in keep:
        pieces.append(x[start * frame:end * frame])
    pieces.append(x[(last + 1) * frame:])
    sf.write(out_path, np.concatenate(pieces), sr, subtype="PCM_16")
    return trimmed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ceiling", type=float, default=0.35)
    ap.add_argument("--suffix", default="-capped")
    ap.add_argument("wavs", nargs="+")
    args = ap.parse_args()

    for path in args.wavs:
        stem, ext = os.path.splitext(path)
        out_path = f"{stem}{args.suffix}{ext}"
        trimmed = cap(path, args.ceiling, out_path)
        name = os.path.basename(path)
        if trimmed is None:
            print(f"{name}: no voiced frame, skipped")
        elif not trimmed:
            shutil.copyfile(path, out_path)
            print(f"{name}: nothing over {args.ceiling:.2f}s, copied unchanged")
        else:
            detail = ", ".join(f"@{t:.2f}s was {d:.2f}s" for t, d in trimmed)
            print(f"{name}: capped {len(trimmed)} pause(s) to "
                  f"{args.ceiling:.2f}s ({detail}) -> {os.path.basename(out_path)}")


if __name__ == "__main__":
    raise SystemExit(main())
