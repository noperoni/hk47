"""Bake-off entry 1 of 4: IndexTTS 2.5, zero-shot.

    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/bakeoff/run-indextts.py'

The engine was already installed by the omnivoice studio image, weights and all,
19G under engines/indextts2 with its own uv venv. Nothing here installs anything.

Run from the repo root: config.yaml carries relative paths that only resolve from
there, and the checkpoints dir is its sibling.

No emotion steering. use_qwen_emo stays off and no emo_audio, emo_text or
emo_vector is passed, so this measures the timbre clone alone and stays
comparable with the other three, none of which have IndexTTS's emotion controls.
The disentangled emotion prompts are the reason to come back here if it wins.
"""

import importlib.util
import os
import sys
import time

ENGINE = "/root/.omnivoice/engines/indextts2/index-tts-2.5"

spec = importlib.util.spec_from_file_location(
    "hk47_bakeoff", "/root/.omnivoice/bakeoff/hk47_bakeoff.py"
)
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)

sys.path.insert(0, ENGINE)
os.chdir(ENGINE)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

tts = IndexTTS2(
    cfg_path=os.path.join(ENGINE, "checkpoints/config.yaml"),
    model_dir=os.path.join(ENGINE, "checkpoints"),
    use_bf16=True,
    use_qwen_emo=False,
)

import soundfile as sf  # noqa: E402

for key, text in bake.LINES.items():
    path = bake.out_path("indextts25", key)
    start = time.perf_counter()
    tts.infer(
        spk_audio_prompt=bake.REF_WAV,
        text=text,
        output_path=path,
        lang="en",
        verbose=False,
    )
    elapsed = time.perf_counter() - start
    info = sf.info(path)
    bake.report("indextts25", path, info.duration, info.samplerate)
    print(f"BAKEOFF indextts25 {key}: {elapsed:.1f}s wall, "
          f"rtf {elapsed / info.duration:.2f}", flush=True)
