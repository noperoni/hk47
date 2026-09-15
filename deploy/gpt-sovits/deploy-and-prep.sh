#!/usr/bin/env bash
# Deploy GPT-SoVITS on the TTS host and run its dataset prep over the HK-47 corpus.
#
# Why this exists as a script rather than a paragraph. Driving the four prep
# stages directly, instead of clicking them in the WebUI, needs three things that
# are nowhere in upstream's docs and each of which fails in its own way:
#
#   1. PYTHONPATH must carry the repo root AND the GPT_SoVITS package directory.
#      The image exports only the root, so `from text.cleaner import ...` in
#      stage 1 raises ModuleNotFoundError. The two entries below are the pair
#      api_v2.py adds to sys.path by hand, so this is upstream's own resolution.
#   2. is_half must be capitalised. The image inherits is_half=true from
#      upstream's compose, while every prep script does
#      eval(os.environ.get("is_half", "True")): eval("true") is a NameError.
#      The WebUI passes str(is_half), which is why it never hits this.
#   3. The WebUI merges each stage's per-part files afterwards and nothing else
#      does. Training reads 2-name2text.txt and 6-name2semantic.tsv, and the
#      latter needs a header row. We keep the -0 parts rather than deleting them
#      as upstream does, because they cost nothing and are the evidence.
#
# The Lite image is the right one on merit and not only on size: what it drops is
# ASR and UVR5, and the corpus needs neither, since the transcripts are exact out
# of dialog.tlk and the source is clean dialogue.
#
# CAUTION: the image's CMD rm -rf's four model directories INSIDE the bind-mounted
# project directory and replaces them with symlinks into the image. Never place
# anything of value under the project dir, which is why the corpus is a separate
# read-only mount.
#
# v2Pro is upstream's own default (webui.py line 1 sets it), not a choice made
# here. It is the reason 2-get-sv.py runs at all: only the Pro variants use the
# speaker-verification features.
#
# Run this ON the TTS host. It is safe to re-run: the container is only created
# if absent, and the prep stages overwrite their own outputs.

set -euo pipefail

IMAGE=xxxxrt666/gpt-sovits:latest-cu128-lite
NAME=gpt-sovits
PROJECT=/mnt/ops-center/gpt-sovits
CORPUS=/mnt/ops-center/omnivoice/state/hk47-corpus
EXP=hk47
VERSION=v2Pro

# 8g rather than upstream's 16g: shm sizes a DataLoader's worker queue, this host
# has 31GB of RAM with another engine resident, and the dataset is 291 clips.
# ponytail: raise it if the trainer ever stalls on worker IPC.
SHM=8g

if [ ! -d "$PROJECT/.git" ]; then
  git clone --depth 1 https://github.com/RVC-Boss/GPT-SoVITS.git "$PROJECT"
fi

if ! docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  docker run -dit --name "$NAME" \
    --gpus all \
    --shm-size "$SHM" \
    -e is_half=true \
    -p 9871-9874:9871-9874 -p 9880:9880 \
    -v "$PROJECT":/workspace/GPT-SoVITS \
    -v "$CORPUS":/workspace/corpus:ro \
    --restart unless-stopped \
    "$IMAGE"
fi

ENVS=(
  -e PYTHONPATH=/workspace/GPT-SoVITS:/workspace/GPT-SoVITS/GPT_SoVITS
  -e inp_text=/workspace/corpus/gpt-sovits.list
  -e inp_wav_dir=/workspace/corpus/wav
  -e exp_name="$EXP"
  -e opt_dir="logs/$EXP"
  -e bert_pretrained_dir=GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large
  -e cnhubert_base_dir=GPT_SoVITS/pretrained_models/chinese-hubert-base
  -e sv_path=GPT_SoVITS/pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt
  -e pretrained_s2G="GPT_SoVITS/pretrained_models/v2Pro/s2G${VERSION}.pth"
  -e s2config_path="GPT_SoVITS/configs/s2${VERSION}.json"
  -e i_part=0 -e all_parts=1 -e is_half=True -e version="$VERSION"
  -e _CUDA_VISIBLE_DEVICES=0
)

for stage in 1-get-text.py 2-get-hubert-wav32k.py 2-get-sv.py 3-get-semantic.py; do
  echo "== $stage"
  docker exec "${ENVS[@]}" -w /workspace/GPT-SoVITS "$NAME" \
    python3 -s "GPT_SoVITS/prepare_datasets/$stage"
done

# 3-bert stays empty for English and that is correct, not a failure: the loader
# zero-fills bert_padded and only writes into it when a feature file exists.
docker exec -w "/workspace/GPT-SoVITS/logs/$EXP" "$NAME" python3 -c '
opt = open("2-name2text-0.txt").read().strip("\n").split("\n")
open("2-name2text.txt", "w").write("\n".join(opt) + "\n")
sem = ["item_name\tsemantic_audio"] + open("6-name2semantic-0.tsv").read().strip("\n").split("\n")
open("6-name2semantic.tsv", "w").write("\n".join(sem) + "\n")
print("text rows", len(opt), "semantic rows", len(sem) - 1)
'

echo "prepared: $PROJECT/logs/$EXP"

# ---------------------------------------------------------------------------
# TRAINING AND AUDITION, appended after the prep proved out. Run these after
# hk47-write-configs.py has written TEMP/tmp_s2.json and TEMP/tmp_s1.yaml.
#
# Copy write-configs.py into the project first, because the experiment directory
# is root-owned by the container that made it and the repo is not mounted inside:
#
#   cp deploy/gpt-sovits/write-configs.py "$PROJECT/hk47-write-configs.py"
#   docker exec -w /workspace/GPT-SoVITS gpt-sovits \
#     python3 hk47-write-configs.py /workspace/GPT-SoVITS
#
# Both trainers are launched with `docker exec -d` rather than through ssh in the
# foreground, so they survive the ssh session dying rather than taking a SIGHUP
# with it. MEASURED on this corpus: s2 is ~3 minutes over 8 epochs of 27 steps,
# s1 under a minute over 15 epochs of 25. The GPU peaks near 11.6GB of 24, which
# is why running this beside a CosyVoice tune on one card was refused.
#
# is_half=True is passed EXPLICITLY to every call. The container inherits
# lowercase `true` from upstream's compose and eval() rejects it: this bit the
# prep, then bit the audition separately, because inference_cli.py reads it too.
#
#   docker exec -d gpt-sovits sh -c "cd /workspace/GPT-SoVITS && \
#     PYTHONPATH=/workspace/GPT-SoVITS:/workspace/GPT-SoVITS/GPT_SoVITS \
#     python3 -s GPT_SoVITS/s2_train.py --config TEMP/tmp_s2.json \
#     > /workspace/GPT-SoVITS/hk47-s2.log 2>&1"
#
#   docker exec -d gpt-sovits sh -c "cd /workspace/GPT-SoVITS && \
#     PYTHONPATH=/workspace/GPT-SoVITS:/workspace/GPT-SoVITS/GPT_SoVITS \
#     hz=25hz _CUDA_VISIBLE_DEVICES=0 \
#     python3 -s GPT_SoVITS/s1_train.py --config_file TEMP/tmp_s1.yaml \
#     > /workspace/GPT-SoVITS/hk47-s1.log 2>&1"
#
# Audition. inference_cli.py writes output_path/output.wav and does NOT create
# output_path, so mkdir it first. The language arguments are Chinese labels by
# upstream's own argparse choices: 英文 is English. The reference must be 3 to 10
# seconds, which rules out the 20-second CosyVoice reference.
#
#   mkdir -p "$PROJECT/hk47-audition/out1"
#   docker exec -e is_half=True \
#     -e PYTHONPATH=/workspace/GPT-SoVITS:/workspace/GPT-SoVITS/GPT_SoVITS \
#     -w /workspace/GPT-SoVITS gpt-sovits \
#     python3 -s GPT_SoVITS/inference_cli.py \
#     --gpt_model GPT_weights_v2Pro/hk47-e15.ckpt \
#     --sovits_model SoVITS_weights_v2Pro/hk47_e8_s216.pth \
#     --ref_audio /workspace/corpus/wav/nm17ac08hk01004.wav \
#     --ref_text hk47-audition/ref.txt --ref_language 英文 \
#     --target_text hk47-audition/t1.txt --target_language 英文 \
#     --output_path hk47-audition/out1
#
# The s1 learning rate is a hardcoded 0.002 no matter what the config says:
# WarmupCosineLRSchedule.step ends with `self.lr = lr = self.end_lr = 0.002` and
# set_lr writes end_lr into every param group, so the warmup and cosine above it
# are dead code. Do not tune the optimizer block expecting an effect; it only has
# to survive construction arithmetic, which is where the JSON-as-YAML bug hit.
