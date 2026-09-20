#!/usr/bin/env bash
# Deploy Higgs Audio v2 inside the omnivoice container and render the four
# bake-off lines from ref13, zero-shot, at upstream's own defaults.
#
# HIGGS WAS AXED BY MASTER ON 2026-09-20, AND THIS SCRIPT IS THE RECORD OF HOW.
# Two runs, both on ref13 and the same four lines. At upstream's default scene
# he ruled "index still better, by a mile. Higgs doesn't seem to capture the
# killer robot feeling, talks like a calm soothing butler instead". The second
# run overturned the scene prompt and described the reference in the system
# message as well, which moved the delivery and did not save it: "yikes, nope".
# The engine is struck on timbre and character, never on plumbing, so nothing
# below is a defect waiting to be fixed. It is kept runnable because the next
# engine will want the same pinning and the same reference staging.
#
# This one is cheap, and it is worth saying why before anyone budgets for it.
# The clone is ALREADY inside the container at engines/higgs, 21MB of code with
# no venv and no weights, so nothing here pulls a Docker image. That matters:
# the warehouse root disk sits at 18GB free and 91% full after the GPT-SoVITS
# pull, and an image is the one thing that would land on it.
#
# Four traps, each of which costs a run:
#
#   1. uv caches wheels under ~/.cache/uv, which is ON THE ROOT DISK. torch and
#      its CUDA libraries are several gigabytes of wheel, so UV_CACHE_DIR is
#      moved onto the mounted volume below. HF_HOME already points at
#      /app/omnivoice_data/huggingface, which is the same volume, so the weights
#      were never the hazard.
#   2. Its own venv, not indextts2's. requirements.txt pins
#      transformers>=4.45.1,<4.47.0, which is older than what the sibling engine
#      runs, and sharing would break whichever one lost.
#   3. --ref_audio takes a NAME, never a path. generation.py resolves it under
#      examples/voice_prompts and asserts on BOTH <name>.wav and <name>.txt, so
#      the reference has to be copied in under a name with its transcript beside
#      it. ref13's transcript is the two corpus clips in merge order.
#   4. --transcript takes literal text when the string is not an existing path,
#      which is os.path.exists at line 689 and not a documented feature. The
#      four lines are read out of hk47_bakeoff.LINES rather than retyped here,
#      because that dict is the fixed set every engine has been judged on and a
#      second copy would drift.
#
# THE WEIGHTS ARE PINNED, and this is the whole reason the first run died.
# Both Hugging Face repos were migrated to a transformers-native format on
# 2026-04-04 ("trfms-support") and then had their old weight files deleted on
# 2026-05-31: model.pth from the tokenizer, all three shards from the 3B model.
# The tokenizer card now asks for transformers>=5.3.0 while this clone pins
# <4.47.0, so the code we hold cannot read what upstream now publishes, and the
# clone carries no .git to update from. The failure surfaces as
#
#   TypeError: HiggsAudioTokenizer.__init__() got an unexpected keyword
#   argument 'acoustic_model_config'
#
# which reads like a bug and is really a two-year gap: fifteen of the eighteen
# keys in the new config are unknown to this __init__, and model.pth is simply
# gone. The revisions below are the last before that migration, matched to this
# clone. Chasing the new format instead would mean a new venv, transformers 5,
# and upstream code we do not have, for an audition.
#
# CAUTION: the warehouse CPU is a 2018 i7-9700K with AVX2 and no AVX-512. A
# wheel built for x86-64-v4 dies on an invalid opcode, which surfaces as exit
# 132 and a kernel trap rather than as a Python exception, so no except clause
# anywhere catches it. If this script dies at 132, read the host dmesg and find
# out which shared object trapped before theorising about anything else.
#
# Run it ON the TTS host, where the repo is not visible inside the container:
#   docker exec -i omnivoice bash -s < deploy/higgs/deploy-and-run.sh
#
# Safe to re-run: the venv is created only if absent and the installs are
# idempotent.
set -euo pipefail

ENGINE=/root/.omnivoice/engines/higgs/higgs-audio
REFS=/root/.omnivoice/hk47-bakeoff/refs
OUT=/root/.omnivoice/hk47-bakeoff/out
BAKEOFF=/root/.omnivoice/bakeoff/hk47_bakeoff.py
export UV_CACHE_DIR=/root/.omnivoice/.uv-cache

cd "$ENGINE"

if [ ! -x .venv/bin/python ]; then
    uv venv .venv --python 3.11
fi
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python -e .

.venv/bin/python -c "import torch, transformers; \
print('torch', torch.__version__, 'transformers', transformers.__version__, \
'cuda', torch.cuda.is_available())"

# The pinned snapshots, fetched by path so nothing upstream needs editing:
# load_higgs_audio_tokenizer takes a local directory when one exists, and
# --model_path is handed straight to from_pretrained. allow_patterns keeps the
# demo videos and figures out of a 7GB download.
.venv/bin/python - > /tmp/hk47-higgs-paths.txt <<'PY'
from huggingface_hub import snapshot_download

KEEP = ["*.json", "*.safetensors", "*.pth", "*.txt", "*.model"]
print(snapshot_download("bosonai/higgs-audio-v2-tokenizer",
                        revision="9d4988fbd4ad07b4cac3a5fa462741a41810dbec",
                        allow_patterns=KEEP))
print(snapshot_download("bosonai/higgs-audio-v2-generation-3B-base",
                        revision="10840182ca4ad5d9d9113b60b9bb3c1ef1ba3f84",
                        allow_patterns=KEEP))
PY
TOKENIZER=$(sed -n 1p /tmp/hk47-higgs-paths.txt)
MODEL=$(sed -n 2p /tmp/hk47-higgs-paths.txt)
echo ">> tokenizer $TOKENIZER"
echo ">> model     $MODEL"
test -f "$TOKENIZER/model.pth"

# The reference under the name generation.py will resolve, with the transcript
# it asserts on. Both corpus clips, diagnostic first, which is what ref13 is.
cp "$REFS/ref13-diag-then-protocol.wav" examples/voice_prompts/ref13.wav
cat > examples/voice_prompts/ref13.txt <<'TXT'
Answer: My assassination functions are currently non-functional, having been de-activated by the meatbag Yuka Laka on Tatooine. Greeting: Hello to you, prospective purchaser. I am referred to as HK-47, a fully functional Systech Corporation droid skilled in both combat and protocol functions.
TXT

.venv/bin/python - > /tmp/hk47-lines.tsv <<'PY'
import importlib.util

spec = importlib.util.spec_from_file_location("hk47_bakeoff", "/root/.omnivoice/bakeoff/hk47_bakeoff.py")
bake = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bake)
for name, text in bake.LINES.items():
    print(name + "\t" + text)
PY

# HK47_HIGGS_SCENE=hk47 overturns upstream's default scene, which reads "Audio
# is recorded from a quiet room" and is an explicit instruction toward calm.
# Master's verdict on the default run, 2026-09-20: "doesn't seem to capture the
# killer robot feeling, talks like a calm soothing butler instead". His own
# descriptors are sarcastic, menacing, metallic, robotic, murderous glee; the
# text below deliberately asks for those DEADPAN, because a prompt that says
# glee invites performance and this character never performs.
cat > examples/scene_prompts/hk47.txt <<'TXT'
The speaker is an assassination droid. The voice is synthetic and metallic, unhurried, with a flat affect that never rises and never warms. Contempt and amusement are both present and both delivered deadpan, as a calm technical reading rather than as performance. The tone is dry, clipped and faintly menacing, offering no reassurance of any kind.
TXT

SCENE_ARGS=()
TAG=higgs2
if [ "${HK47_HIGGS_SCENE:-quiet}" = "hk47" ]; then
    # The reference is DESCRIBED in the system message as well as heard, which
    # is a structural change to the prompt and not only a wording one.
    SCENE_ARGS=(--scene_prompt "$ENGINE/examples/scene_prompts/hk47.txt"
                --ref_audio_in_system_message)
    TAG=higgs2-hk47
fi

mkdir -p "$OUT"
while IFS=$'\t' read -r name text; do
    echo ">> higgs $TAG ref13 $name"
    .venv/bin/python examples/generation.py \
        --model_path "$MODEL" \
        --audio_tokenizer "$TOKENIZER" \
        --ref_audio ref13 \
        --transcript "$text" \
        --device cuda \
        "${SCENE_ARGS[@]}" \
        --out_path "$OUT/$TAG-ref13-$name.wav"
done < /tmp/hk47-lines.tsv

echo ">> done, four wavs under $OUT named $TAG-ref13-*.wav"
