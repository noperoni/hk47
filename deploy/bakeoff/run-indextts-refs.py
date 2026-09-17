"""IndexTTS 2.5 reference sweep: same line, four different reference clips.

    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/bakeoff/run-indextts-refs.py'

Master's verdict on the first pass was that the clone is close but reads too
British and not enough like HK-47. In a zero-shot engine the timbre comes
entirely from the reference clip, so the reference is the lever, not the engine.

The incumbent is five stitched sentences of the polite register, mostly "ready
to serve" and "I will endeavour". The three challengers are single takes from
the corpus, chosen for the register the incumbent lacks: flat diagnostic report,
fond menace, and the long formal sales pitch.

One line only, and the same line each time, because the question here is timbre
and rendering four lines per reference would spend sixteen renders to answer it.
"""

import importlib.util
import os
import sys
import time

ENGINE = "/root/.omnivoice/engines/indextts2/index-tts-2.5"
CORPUS = "/root/.omnivoice/hk47-corpus/wav"

spec = importlib.util.spec_from_file_location(
    "hk47_bakeoff", "/root/.omnivoice/bakeoff/hk47_bakeoff.py"
)
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)

REFS = {
    # the incumbent, approved by ear on 2026-09-15 and used for every render so far
    "ref0-incumbent": bake.REF_WAV,
    # "Answer: My assassination functions are currently non-functional, having
    #  been de-activated by the meatbag Yuka Laka on Tatooine." 7.81s
    "ref1-diagnostic": f"{CORPUS}/nm35aahhkd07024.wav",
    # "Commentary: As do I. It is our lot in life, I suppose, master. Shall we
    #  find something to kill to cheer ourselves up?" 8.37s
    "ref2-menace": f"{CORPUS}/nm35aahhkd07060.wav",
    # "Greeting: Hello to you, prospective purchaser. I am referred to as HK-47,
    #  a fully functional Systech Corporation droid..." 12.25s
    "ref3-protocol": f"{CORPUS}/nm17ac08hk01016.wav",
}

missing = [name for name, path in REFS.items() if not os.path.exists(path)]
if missing:
    raise SystemExit(f"reference clips not found: {missing}")

sys.path.insert(0, ENGINE)
os.chdir(ENGINE)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402
import soundfile as sf  # noqa: E402

tts = IndexTTS2(
    cfg_path=os.path.join(ENGINE, "checkpoints/config.yaml"),
    model_dir=os.path.join(ENGINE, "checkpoints"),
    use_bf16=True,
    use_qwen_emo=False,
)

for name, ref in REFS.items():
    path = bake.out_path("indextts25", name)
    start = time.perf_counter()
    tts.infer(
        spk_audio_prompt=ref,
        text=bake.LINES["long"],
        output_path=path,
        lang="en",
        verbose=False,
    )
    elapsed = time.perf_counter() - start
    info = sf.info(path)
    ref_info = sf.info(ref)
    print(f"BAKEOFF {name}: ref {ref_info.duration:.2f}s -> out "
          f"{info.duration:.2f}s in {elapsed:.1f}s  {path}", flush=True)
