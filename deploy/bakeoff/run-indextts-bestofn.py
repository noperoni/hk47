"""IndexTTS 2.5 best-of-N: roll until the pause gate is satisfied, then keep it.

    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/bakeoff/run-indextts-bestofn.py'
    ... --mood ref13-diag-then-protocol --line all -n 7

This is the join between the renderer and hk47-gate.py, and it exists because
IndexTTS samples its pause lengths. Measured silences before "master" across six
renders of one reference, text and settings ran 0.07s to 0.51s, and on a blind
listen of 2026-09-18 Master ranked them shortest-first, faulting only the two at
0.44s. So the long pause is the defect, it is a roll of the dice rather than
anything in the text, and re-rolling is cheaper than repairing it.
hk47-cap-pauses.py tried the repair and is kept as the negative result.

The first passing roll wins. Nothing here ranks the passers against each other:
his top four sat at 0.19s, 0.07s, 0.07s and 0.11s in that order, which is not
monotone in the number, so the gate can say what is too long and nothing more.
Rendering is the cheap half at an rtf under one; the aligner is the expensive
half and loads Whisper once, which is why every roll is rendered before any is
judged rather than gating one at a time.
# ponytail: batch of N, all judged in one pass; make it incremental if N ever
# grows past what a GPU minute is worth.

Both merged moods are here because Master kept both orders on 2026-09-17. Only
the "long" line has ever been rendered through them, so --line all is the way the
other three catch up.
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
ap.add_argument("--mood", default="ref31-protocol-then-diag")
ap.add_argument("--line", default="long", help="a key of hk47_bakeoff.LINES, or all")
ap.add_argument("-n", "--rolls", type=int, default=5)
ap.add_argument("--ceiling", type=float, default=0.30)
args = ap.parse_args()

REF = f"{REFDIR}/{args.mood}.wav"
if not os.path.exists(REF):
    raise SystemExit(f"no such reference: {REF}, run run-indextts-merged.py first")
LINES = list(bake.LINES) if args.line == "all" else [args.line]

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
    text = bake.LINES[line]
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
         "--text", text, *rolls],
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
    if winner is None and all(v["max_gap"] is None for v in verdicts):
        # The ceiling is judged at commas, and "short" has none. A line the gate
        # cannot rule on takes its first roll rather than losing every one.
        winner = verdicts[0]
        print(f"BAKEOFF {args.mood} {line}: no judged boundary in this line, "
              f"the gate abstained and roll1 stands", flush=True)
    if winner is None:
        print(f"BAKEOFF {args.mood} {line}: NO ROLL CAME IN UNDER {args.ceiling:.2f}s "
              f"in {args.rolls} attempts, nothing kept", flush=True)
        continue

    kept = bake.out_path(f"indextts25-{args.mood}", line)
    shutil.copyfile(winner["path"], kept)
    worst = winner["max_gap"]
    measure = f"at worst {worst:.2f}s" if worst is not None else "unjudged"
    print(f"BAKEOFF {args.mood} {line}: kept "
          f"{os.path.basename(winner['path'])} {measure} -> {kept}", flush=True)
