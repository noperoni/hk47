#!/usr/bin/env bash
# Deploy Chatterbox TTS inside the omnivoice container and render the four
# bake-off lines from ref13, zero-shot, at upstream's own defaults.
#
# Third engine, third venv, and the reason is the same each time: chatterbox-tts
# 0.1.7 pins torch==2.6.0 and transformers==5.2.0, where the Higgs venv holds
# torch 2.14 with transformers 4.46 and indextts2 holds its own pair. Sharing
# would break whichever engine lost the pin. That is about 3GB of torch per
# venv, all of it on the mounted volume and none of it on the 18GB root disk,
# which is what UV_CACHE_DIR below is for.
#
# The omnivoice registry DOES list Chatterbox and it is no use here: the entry
# is mlx-community/Chatterbox-TTS-4bit behind the mlx-audio backend, declared
# platforms darwin-arm64. Apple Silicon only. The upstream PyPI package is the
# only route on this card.
#
# CAUTION, AND IT IS NOT A DEFECT TO BE FIXED: every Chatterbox output carries
# an imperceptible neural watermark from resemble-perth, which upstream says
# survives MP3 compression and editing at near-perfect detection. Upstream
# documents no way to disable it. If this engine is ever chosen, that property
# ships with it and belongs in the decision rather than in a surprise later.
#
# The classic ChatterboxTTS is used rather than the Turbo model, because the two
# documented knobs live there: exaggeration (0.5 default, expressiveness, higher
# also speeds the tempo) and cfg_weight (0.5 default, adherence to the
# reference). Both are left at their defaults for this first audition, so the
# comparison against IndexTTS and Higgs is like for like: one zero-shot take per
# line at whatever upstream thinks is right.
#
# HK47_CHATTERBOX_EXAGGERATION and HK47_CHATTERBOX_CFG override them for a
# second pass, which is the cheap lever if the delivery is close but flat or
# close but overacted.
#
# The model loads ONCE and renders all four lines, unlike the Higgs script,
# which pays a 3B checkpoint load per invocation because upstream's entry point
# is a click command. That is worth about thirty seconds a line.
#
# CAUTION: the reference is about twenty seconds, where Chatterbox's own example
# names a ten-second clip. If the clone is poor, a shorter reference is the
# first thing to try, not the last.
#
# CAUTION: the warehouse CPU is a 2018 i7-9700K with AVX2 and no AVX-512. A
# wheel built for x86-64-v4 dies on an invalid opcode, which surfaces as exit
# 132 and a kernel trap rather than a Python exception, so no except clause
# catches it. If this dies at 132, read the host dmesg before theorising.
#
# Run it ON the TTS host, where the repo is not visible inside the container:
#   docker exec -i omnivoice bash -s < deploy/chatterbox/deploy-and-run.sh
#
# Safe to re-run: the venv is created only if absent and the install is
# idempotent.
set -euo pipefail

ENGINE=/root/.omnivoice/engines/chatterbox
REF=/root/.omnivoice/hk47-bakeoff/refs/ref13-diag-then-protocol.wav
OUT=/root/.omnivoice/hk47-bakeoff/out
BAKEOFF=/root/.omnivoice/bakeoff/hk47_bakeoff.py
export UV_CACHE_DIR=/root/.omnivoice/.uv-cache

mkdir -p "$ENGINE" "$OUT"
cd "$ENGINE"

if [ ! -x .venv/bin/python ]; then
    uv venv .venv --python 3.11
fi
uv pip install --python .venv/bin/python chatterbox-tts
# setuptools<81 for pkg_resources, which 81 removed and perth still imports.
# This project met the same trap on CosyVoice. It is worth knowing HOW it
# presents, because the message names neither setuptools nor perth:
#
#   TypeError: 'NoneType' object is not callable
#   ... self.watermarker = perth.PerthImplicitWatermarker()
#
# perth/__init__.py guards its real implementation with a bare except ImportError
# and sets the name to None on failure, so a missing dependency four levels down
# arrives as an attribute that is quietly None. The actual error only appears by
# importing perth.perth_net.perth_net_implicit.perth_watermarker by hand.
uv pip install --python .venv/bin/python "setuptools<81"

.venv/bin/python -c "import torch, transformers; \
print('torch', torch.__version__, 'transformers', transformers.__version__, \
'cuda', torch.cuda.is_available())"

HK47_REF="$REF" \
HK47_OUT="$OUT" \
HK47_BAKEOFF="$BAKEOFF" \
.venv/bin/python - <<'PY'
import importlib.util
import os
import time

import torch
import torchaudio as ta
from chatterbox.tts import ChatterboxTTS

REF = os.environ["HK47_REF"]
OUT = os.environ["HK47_OUT"]

spec = importlib.util.spec_from_file_location("hk47_bakeoff", os.environ["HK47_BAKEOFF"])
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)

exaggeration = float(os.environ.get("HK47_CHATTERBOX_EXAGGERATION", 0.5))
cfg_weight = float(os.environ.get("HK47_CHATTERBOX_CFG", 0.5))
tag = "chatterbox" if (exaggeration, cfg_weight) == (0.5, 0.5) \
    else f"chatterbox-e{exaggeration:g}-c{cfg_weight:g}"
print(f">> exaggeration {exaggeration}, cfg_weight {cfg_weight}, tag {tag}", flush=True)

model = ChatterboxTTS.from_pretrained(device="cuda")

for name, text in bake.LINES.items():
    path = os.path.join(OUT, f"{tag}-ref13-{name}.wav")
    start = time.perf_counter()
    wav = model.generate(
        text,
        audio_prompt_path=REF,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
    )
    elapsed = time.perf_counter() - start
    ta.save(path, wav, model.sr)
    seconds = wav.shape[-1] / model.sr
    print(f"BAKEOFF {tag} {name}: out {seconds:.2f}s in {elapsed:.1f}s  {path}",
          flush=True)
PY
