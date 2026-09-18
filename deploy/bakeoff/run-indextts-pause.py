"""IndexTTS 2.5: shorten the pause before "master" on the ref31 mood.

    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/bakeoff/run-indextts-pause.py'

Master ruled on 2026-09-18 that ref31 holds the pause before "master" twice as
long as it should, and the word aligner put it at 0.64s against ref13's 0.30s on
the identical line. He wants both merge orders kept as two moods, so ref31 has to
be repaired rather than dropped.

Three runs against the one reference, changing one thing each:

    control      the line as written, normalisation on, the 0.64s to beat
    nocomma      the comma before "master" removed, normalisation on
    nonorm       the line as written, normalisation off

The comma is the obvious suspect and has form: the colon-to-comma ruling of
2026-09-16 turned the same class of defect on GPT-SoVITS, where a colon held
0.097-0.232s and a comma held 0.094-0.140s. The normalisation run is here because
IndexTTS rewrites punctuation before tokenising and a pause it inserts itself
would not be fixed by editing the text at all.

Target is ref13's 0.30s, not zero: HK-47 does pause before the address, and a
render with no gap at all would be the opposite defect.
"""

import importlib.util
import os
import sys
import time

import soundfile as sf

ENGINE = "/root/.omnivoice/engines/indextts2/index-tts-2.5"
REF = "/root/.omnivoice/hk47-bakeoff/refs/ref31-protocol-then-diag.wav"

spec = importlib.util.spec_from_file_location(
    "hk47_bakeoff", "/root/.omnivoice/bakeoff/hk47_bakeoff.py"
)
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)

LINE = bake.LINES["long"]
RUNS = {
    "pause-control": (LINE, True),
    "pause-nocomma": (LINE.replace(", master.", " master."), True),
    "pause-nonorm": (LINE, False),
}

# Second pass, 2026-09-18: the control re-rendered at 0.30s where the first ref31
# render held 0.64s, on the same reference, text and settings. That makes the
# defect a roll of the dice rather than a property of the punctuation, so the
# next question is the spread, not the lever. Four more identical runs measure it.
if os.environ.get("HK47_PAUSE_SPREAD") == "1":
    RUNS = {f"pause-roll{n}": (LINE, True) for n in range(1, 5)}

sys.path.insert(0, ENGINE)
os.chdir(ENGINE)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

tts = IndexTTS2(
    cfg_path=os.path.join(ENGINE, "checkpoints/config.yaml"),
    model_dir=os.path.join(ENGINE, "checkpoints"),
    use_bf16=True,
    use_qwen_emo=False,
)

for name, (text, normalize) in RUNS.items():
    path = bake.out_path("indextts25", name)
    start = time.perf_counter()
    tts.infer(
        spk_audio_prompt=REF,
        text=text,
        output_path=path,
        lang="en",
        verbose=False,
        text_normalization=normalize,
    )
    elapsed = time.perf_counter() - start
    print(f"BAKEOFF {name}: normalize={normalize} out "
          f"{sf.info(path).duration:.2f}s in {elapsed:.1f}s  {path}", flush=True)
