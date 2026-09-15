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
