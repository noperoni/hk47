"""IndexTTS 2.5: the two references Master ruled closest, merged, diag first.

    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/bakeoff/run-indextts-merged.py'

Master's ruling on 2026-09-17, by ear, over the four-reference sweep:

    nm35aahhkd07024  7.81s   "Answer: My assassination functions are currently
                              non-functional, having been de-activated by the
                              meatbag Yuka Laka on Tatooine."
    nm17ac08hk01016  12.25s  "Greeting: Hello to you, prospective purchaser. I am
                              referred to as HK-47, a fully functional Systech
                              Corporation droid skilled in both combat and
                              protocol functions."

were the two closest, and the stitched incumbent 7d29527a was not. The incumbent
is five sentences of the courteous register and that courtesy is what read as
British, which is the artefact he objected to.

Both orders WERE rendered because the engine is autoregressive and the tail of a
prompt carries more weight than its head, so which clip ends the reference is
not a cosmetic choice. It mattered, and ONLY the ear could say so: on 2026-09-20
Master heard eight best-of-N winners and ruled ref13 perfect and ref31 out. The
gate had already leaned the same way across two labelled rounds, but it was
never the deciding instrument.

ref31 is struck from MERGED rather than from merge(), so the order is one line
away if a later reference pair ever wants the same comparison. Its wav is left
on the warehouse: it rebuilds from this file in seconds and costs 1.7MB to keep.

The 200ms of silence between them is a seam, not a pause: butted directly
together the two takes sound like one impossible breath, and an engine reading
prosody off the reference would learn that from it.
"""

import importlib.util
import os
import sys
import time

import numpy as np
import soundfile as sf

ENGINE = "/root/.omnivoice/engines/indextts2/index-tts-2.5"
CORPUS = "/root/.omnivoice/hk47-corpus/wav"
REFDIR = "/root/.omnivoice/hk47-bakeoff/refs"

DIAGNOSTIC = f"{CORPUS}/nm35aahhkd07024.wav"
PROTOCOL = f"{CORPUS}/nm17ac08hk01016.wav"
SEAM_MS = 200

spec = importlib.util.spec_from_file_location(
    "hk47_bakeoff", "/root/.omnivoice/bakeoff/hk47_bakeoff.py"
)
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)


def merge(first, second, out):
    a, sr_a = sf.read(first, dtype="float32")
    b, sr_b = sf.read(second, dtype="float32")
    if sr_a != sr_b:
        raise SystemExit(f"sample rate mismatch: {sr_a} vs {sr_b}")
    seam = np.zeros(int(sr_a * SEAM_MS / 1000), dtype="float32")
    sf.write(out, np.concatenate([a, seam, b]), sr_a, subtype="PCM_16")
    return out, sf.info(out).duration


os.makedirs(REFDIR, exist_ok=True)
MERGED = {
    "ref13-diag-then-protocol": merge(
        DIAGNOSTIC, PROTOCOL, f"{REFDIR}/ref13-diag-then-protocol.wav"
    ),
}

sys.path.insert(0, ENGINE)
os.chdir(ENGINE)

from indextts.infer_v2_5 import IndexTTS2  # noqa: E402

tts = IndexTTS2(
    cfg_path=os.path.join(ENGINE, "checkpoints/config.yaml"),
    model_dir=os.path.join(ENGINE, "checkpoints"),
    use_bf16=True,
    use_qwen_emo=False,
)

for name, (ref, ref_seconds) in MERGED.items():
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
    print(f"BAKEOFF {name}: ref {ref_seconds:.2f}s -> out {info.duration:.2f}s "
          f"in {elapsed:.1f}s  {path}", flush=True)
