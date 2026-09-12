#!/usr/bin/env python3
"""Draw a clip's loudness as characters, to see where a word actually ends.

Why this exists. hk47-edge-sweep.py judges a cut by whether it LANDS AT A DIP,
which is 07122's lesson and is correct as far as it goes. On 2026-09-12 it
passed three cuts that sliced a word in half and Master caught all three by ear:
"un|fair", "i|dea" and "meatb--". Every one of those cuts did land at a dip.

That is the limit of the test rather than a bug in it. A breath inside a word is
a dip too: the stop in "unfair" is 140ms of near silence, longer than the 40ms
the sweep needs to call it one. The dip LIST cannot tell the two apart, because
both entries look identical in it.

The envelope SHAPE can, and that is the whole method:

    a word END falls and STAYS DOWN until the next word begins
    a breath INSIDE a word dips and CLIMBS STRAIGHT BACK into the same word

So this prints one character per 10ms and lets the eye do it. Reading the strip
for 07326 around the bad cut:

      2.00  +--..............-+######################+#+#+++--

the run of dots from 2.03 looks exactly like a sentence end in the dip list, and
the strip shows loud audio resuming 140ms later and running on for another
220ms, which is the rest of "unfair". The true end of the sentence is the run at
3.25, where nothing comes back.

USE THIS BEFORE COMMITTING ANY NEW CUT. The sweep is the tripwire that says look
here; this is what you look with. It borrows the sweep's own envelope() and peak
percentile, so the two can never disagree about what they are measuring.

    python3 hk47-envelope.py <resref> [start] [end]

Read-only. Writes nothing, decides nothing, and touches no wiring.
"""

import importlib.util
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# One character per envelope frame, and one row per half second of audio.
ROW_FRAMES = 50

# Bands, all relative to the source's OWN speech level, never an absolute dBFS
# line, for the reason the sweep's QUIET_DB carries: these recordings differ by
# more than 10dB file to file.
LOUD_DB = 12.0   # within this of the speech level: clearly a vowel
MID_DB = 22.0    # consonants, onsets, decay
SOFT_DB = 30.0   # the sweep's own quiet line; below it is a dip


def load_sweep():
    """hk47-edge-sweep.py is not an importable module name, so load it by path."""
    spec = importlib.util.spec_from_file_location(
        "hk47_edge_sweep", os.path.join(HERE, "hk47-edge-sweep.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def band(value, peak):
    if value >= peak - LOUD_DB:
        return "#"
    if value >= peak - MID_DB:
        return "+"
    if value >= peak - SOFT_DB:
        return "-"
    return "."


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip().splitlines()[-3].strip())
    ref = sys.argv[1]
    lo = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
    hi = float(sys.argv[3]) if len(sys.argv) > 3 else float("inf")

    sweep = load_sweep()
    render = sweep.load_render()
    if not os.path.isdir(render.KOTOR):
        raise SystemExit(f"KOTOR install not found: {render.KOTOR} (set KOTOR_DIR)")
    waves = render.index_streamwaves(render.KOTOR)
    if ref.lower() not in waves:
        raise SystemExit(f"no audio for {ref}")

    db, dur = sweep.envelope(render.to_mp3(waves[ref.lower()]))
    peak = float(np.percentile(db, sweep.PEAK_PCT))
    step = sweep.FRAME_MS / 1000.0

    print(f"{ref}  dur={dur:.2f}s  peak={peak:.1f}dB  "
          f"frame={sweep.FRAME_MS}ms  quiet-line={peak - SOFT_DB:.1f}dB")
    print(f"  '#' within {LOUD_DB:.0f}dB of speech, '+' {MID_DB:.0f}dB, "
          f"'-' {SOFT_DB:.0f}dB, '.' below")
    print("  a word end stays down; a breath climbs straight back\n")

    i = max(0, int(round(lo / step)))
    end = min(int(round(hi / step)), len(db))
    while i < end:
        row = "".join(band(float(db[j]), peak)
                      for j in range(i, min(i + ROW_FRAMES, end)))
        print(f"{i * step:7.2f}  {row}")
        i += ROW_FRAMES


if __name__ == "__main__":
    main()
