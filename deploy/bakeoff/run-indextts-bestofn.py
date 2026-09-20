"""IndexTTS 2.5 best-of-N: roll until the pause gate is satisfied, then keep it.

    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/bakeoff/run-indextts-bestofn.py'
    ... --mood ref13-diag-then-protocol --line all -n 7

This is the join between the renderer and hk47-gate.py, and it exists because
IndexTTS samples its pause lengths. The silence before "master" varies from 0.05s
to 0.46s across identical calls, and Master hears the difference: on 2026-09-19
he labelled eight winners one by one, passing 0.05s and 0.08s and faulting
everything from 0.17s up as "1.5x too much". So the long pause is the defect, it
is a roll of the dice rather than anything in the text, and re-rolling is cheaper
than repairing it. hk47-cap-pauses.py tried the repair and is the negative result.

The first passing roll wins, and nothing here ranks the passers against each
other, because the gate can say what is too long and nothing more. Rendering is
the cheap half at an rtf under one; the aligner is the expensive half and loads
Whisper once, which is why every roll is rendered before any is judged rather
than gating one at a time.

-n 8 rather than 5 is the ceiling's price: at 0.12s a healthy share of rolls are
rejected, and a line that clears nothing keeps nothing at all.
# ponytail: batch of N, all judged in one pass; make it incremental if N ever
# grows past what a GPU minute is worth.

ONE MOOD, from 2026-09-20. Both merge orders were rolled at n=8 across all four
lines under these ceilings and Master labelled the eight winners: ref13 perfect,
ref31 out. --mood still takes any name under REFDIR, and ref31's wav is still
there, so the rejected order is one flag away if it is ever wanted as a control.

--line all matters more than it looks: the model loads once per process, so four
lines in one call cost one load rather than four. The full sweep is seven minutes
of wall clock, not the thirty-five that eight separate invocations would spend.
"""

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time

import soundfile as sf

ENGINE = "/root/.omnivoice/engines/indextts2/index-tts-2.5"
REFDIR = "/root/.omnivoice/hk47-bakeoff/refs"
GATE = "/root/.omnivoice/bakeoff/hk47-gate.py"
# The container interpreter, named absolutely: Whisper is installed there and not
# in any engine's uv venv, and this script is itself running inside one of those.
GATE_PYTHON = "/opt/conda/bin/python3"
BAKEOFF = "/root/.omnivoice/bakeoff/hk47_bakeoff.py"

spec = importlib.util.spec_from_file_location("hk47_bakeoff", BAKEOFF)
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)

ap = argparse.ArgumentParser()
ap.add_argument("--mood", default="ref13-diag-then-protocol")
ap.add_argument("--line", default="long", help="a key of hk47_bakeoff.LINES, or all")
# Arbitrary text, so the approved path can speak a real sentence rather than only
# the four bake-off lines. LINES stays untouched on purpose: it is the fixed set
# every engine was judged on, and growing it would make old renders incomparable.
ap.add_argument("--text", default="", help="speak this instead of a LINES entry")
ap.add_argument("--name", default="adhoc", help="output stem when --text is used")
ap.add_argument("-n", "--rolls", type=int, default=5)
ap.add_argument("--ceiling", type=float, default=0.12)
ap.add_argument("--colon-ceiling", type=float, default=0.42)
args = ap.parse_args()

REF = f"{REFDIR}/{args.mood}.wav"
if not os.path.exists(REF):
    raise SystemExit(f"no such reference: {REF}, run run-indextts-merged.py first")
if args.text:
    LINES, TEXTS = [args.name], {args.name: args.text}
else:
    LINES = list(bake.LINES) if args.line == "all" else [args.line]
    TEXTS = bake.LINES

sys.path.insert(0, ENGINE)
os.chdir(ENGINE)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

tts = IndexTTS2(
    cfg_path=os.path.join(ENGINE, "checkpoints/config.yaml"),
    model_dir=os.path.join(ENGINE, "checkpoints"),
    use_bf16=True,
    use_qwen_emo=False,
)

for line in LINES:
    text = TEXTS[line]
    rolls = []
    for n in range(1, args.rolls + 1):
        path = bake.out_path(f"indextts25-{args.mood}", f"{line}-roll{n}")
        start = time.perf_counter()
        tts.infer(
            spk_audio_prompt=REF,
            text=text,
            output_path=path,
            lang="en",
            verbose=False,
        )
        elapsed = time.perf_counter() - start
        rolls.append(path)
        print(f"BAKEOFF {args.mood} {line} roll{n}: out "
              f"{sf.info(path).duration:.2f}s in {elapsed:.1f}s", flush=True)

    judged = subprocess.run(
        [GATE_PYTHON, GATE, "--json", "--ceiling", str(args.ceiling),
         "--colon-ceiling", str(args.colon_ceiling), "--text", text, *rolls],
        capture_output=True, text=True,
    )
    try:
        verdicts = json.loads(judged.stdout)
    except ValueError:
        raise SystemExit(f"gate returned no verdict (exit {judged.returncode}):\n"
                         f"{judged.stderr}")

    for verdict in verdicts:
        detail = ", ".join(f"{g['after']}|{g['before']} {g['gap']:.2f}s"
                           for g in verdict["gaps"])
        print(f"GATE {os.path.basename(verdict['path'])}: "
              f"{'PASS' if verdict['pass'] else 'FAIL'}  {detail}"
              + ("  LOST " + "; ".join(verdict["lost"]) if verdict["lost"] else ""),
              flush=True)

    winner = next((v for v in verdicts if v["pass"]), None)
    if winner is None and all(v["excess"] is None for v in verdicts):
        # The ceiling is judged at commas, and "short" has none. A line the gate
        # cannot rule on takes its first roll rather than losing every one.
        winner = verdicts[0]
        print(f"BAKEOFF {args.mood} {line}: no judged boundary in this line, "
              f"the gate abstained and roll1 stands", flush=True)
    if winner is None:
        # Keeping nothing leaves the previous winner in place under a name that
        # says it passed, which is worse than keeping the least bad roll and
        # saying so. The query line earns this: its clause comma in "master, or"
        # did not come under 0.17s in eight rolls while the address comma cleared
        # three times, so the ceiling may not belong to both positions.
        measured = [v for v in verdicts if v["excess"] is not None]
        if not measured:
            print(f"BAKEOFF {args.mood} {line}: NOTHING MEASURABLE in "
                  f"{args.rolls} attempts, nothing kept", flush=True)
            continue
        winner = min(measured, key=lambda v: v["excess"])
        print(f"BAKEOFF {args.mood} {line}: NO ROLL CLEARED ITS CEILINGS in "
              f"{args.rolls} attempts, keeping the least bad at "
              f"{winner['excess']:+.2f}s over UNDER PROTEST", flush=True)

    kept = bake.out_path(f"indextts25-{args.mood}", line)
    shutil.copyfile(winner["path"], kept)
    worst = winner["max_gap"]
    measure = f"at worst {worst:.2f}s" if worst is not None else "unjudged"
    print(f"BAKEOFF {args.mood} {line}: kept "
          f"{os.path.basename(winner['path'])} {measure} -> {kept}", flush=True)
