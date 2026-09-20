#!/usr/bin/env bash
# Deploy F5-TTS inside the omnivoice container and render the four bake-off
# lines from ref13, zero-shot, at upstream's own defaults. Fourth and last of
# the authorised engines.
#
# Fourth engine, fourth venv, same reason as the other three: every one of them
# pins its own torch and transformers, and sharing breaks whichever loses. That
# is roughly 3GB of torch per venv, all on the mounted volume and none on the
# 18GB root disk, which is what UV_CACHE_DIR is for.
#
# Upstream's documented interface is the CLI, not the Python API, so this drives
# f5-tts_infer-cli and pays a model load per line. F5's checkpoint is small
# enough that this costs seconds rather than the half-minute the 3B Higgs model
# charged, so it is not worth writing around an undocumented API to avoid.
#
# --help IS PRINTED BEFORE THE FIRST RENDER, deliberately. The flag names below
# come from upstream's README and have not been run here. If they are wrong, the
# help text is in the same log as the failure and the fix takes one round trip
# instead of two.
#
# ref_text is required and is not optional as it is in some engines: F5 wants
# the transcript of the reference in order to align it, which is why ref13's two
# clips are spelled out below in merge order.
#
# CAUTION: the warehouse CPU is a 2018 i7-9700K with AVX2 and no AVX-512. A
# wheel built for x86-64-v4 dies on an invalid opcode, surfacing as exit 132 and
# a kernel trap rather than a Python exception. If this dies at 132, read the
# host dmesg before theorising.
#
# Run it ON the TTS host:
#   docker exec -i omnivoice bash -s < deploy/f5-tts/deploy-and-run.sh
#
# Safe to re-run: the venv is created only if absent and the install is
# idempotent.
set -euo pipefail

ENGINE=/root/.omnivoice/engines/f5-tts
REF=/root/.omnivoice/hk47-bakeoff/refs/ref13-diag-then-protocol.wav
OUT=/root/.omnivoice/hk47-bakeoff/out
BAKEOFF=/root/.omnivoice/bakeoff/hk47_bakeoff.py
MODEL=${HK47_F5_MODEL:-F5TTS_v1_Base}
export UV_CACHE_DIR=/root/.omnivoice/.uv-cache

REF_TEXT="Answer: My assassination functions are currently non-functional, having been de-activated by the meatbag Yuka Laka on Tatooine. Greeting: Hello to you, prospective purchaser. I am referred to as HK-47, a fully functional Systech Corporation droid skilled in both combat and protocol functions."

# HK47_F5_SHORT_REF=1 uses the single 7.81s diagnostic clip instead of ref13.
#
# WHY, MEASURED 2026-09-20 rather than guessed. The first run came out sounding
# "like someone sped up through them at 10x speed", and utils_infer.py line 325
# says why: "Audio is over 12s, clipping short." ref13 is about twenty seconds,
# so F5 clipped the AUDIO and kept the whole 330-character transcript, which
# tells the duration predictor that all of it fits in the clipped span. Its
# first clipping strategy looks for a long silence, and the 200ms seam between
# ref13's two stitched clips is exactly that, so it very likely kept the first
# clip alone while still believing the text described both.
#
# The fix is a reference UNDER the cap whose transcript matches what is actually
# in it. This is a constraint no other engine here imposed, and it is why a bad
# F5 render on ref13 says nothing about F5.
if [ "${HK47_F5_SHORT_REF:-0}" = "1" ]; then
    REF=/root/.omnivoice/hk47-corpus/wav/nm35aahhkd07024.wav
    REF_TEXT="Answer: My assassination functions are currently non-functional, having been de-activated by the meatbag Yuka Laka on Tatooine."
    TAG=f5-shortref
else
    TAG=f5
fi

mkdir -p "$ENGINE" "$OUT"
cd "$ENGINE"

if [ ! -x .venv/bin/python ]; then
    uv venv .venv --python 3.11
fi
uv pip install --python .venv/bin/python f5-tts
# setuptools<81 pre-emptively: pkg_resources was removed at 81 and this engine
# pulls a dependency tree deep enough that something in it still imports it.
# CosyVoice and Chatterbox both cost a run to this exact removal.
uv pip install --python .venv/bin/python "setuptools<81"

.venv/bin/python -c "import torch, transformers; \
print('torch', torch.__version__, 'transformers', transformers.__version__, \
'cuda', torch.cuda.is_available())"

echo "===== f5-tts_infer-cli --help ====="
.venv/bin/f5-tts_infer-cli --help || true
echo "==================================="

.venv/bin/python - > /tmp/hk47-lines.tsv <<'PY'
import importlib.util

spec = importlib.util.spec_from_file_location("hk47_bakeoff", "/root/.omnivoice/bakeoff/hk47_bakeoff.py")
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)
for name, text in bake.LINES.items():
    print(name + "\t" + text)
PY

while IFS=$'\t' read -r name text; do
    echo ">> f5 $MODEL ref13 $name"
    .venv/bin/f5-tts_infer-cli \
        --model "$MODEL" \
        --ref_audio "$REF" \
        --ref_text "$REF_TEXT" \
        --gen_text "$text" \
        --output_dir "$OUT" \
        --output_file "$TAG-ref13-$name.wav"
done < /tmp/hk47-lines.tsv

echo ">> done, four wavs under $OUT named $TAG-ref13-*.wav"
