#!/bin/bash
# CosyVoice 3 fine-tune prep for the HK-47 corpus, stages 1 to 3 of the
# libritts/cosyvoice3 recipe.
#
# Run inside the omnivoice container:
#   docker exec omnivoice bash /root/.omnivoice/hk47-cosyvoice-prep.sh <stage> <data-dir>
#
# The recipe ships without its path.sh, local/ and tools/ symlinks: whatever
# packaged this image dropped them, so run.sh cannot be used as written and each
# stage is invoked against the repo-root tools/ directly. The PYTHONPATH below is
# what the surviving path.sh in the cosyvoice v1 recipe sets, with absolute paths
# instead of the relative ones that assume you are standing in the recipe dir.
set -euo pipefail

STAGE="${1:?stage number 1, 2 or 3}"
DATA="${2:?data dir under the recipe, e.g. data/hk47-dev}"

R=/root/.omnivoice/engines/cosyvoice/CosyVoice
E="$R/examples/libritts/cosyvoice3"
P="$R/pretrained_models/Fun-CosyVoice3-0.5B"

export PYTHONIOENCODING=UTF-8
export PYTHONPATH="$R:$R/third_party/Matcha-TTS:${PYTHONPATH:-}"
cd "$E"

case "$STAGE" in
  1)
    echo "STAGE 1 campplus speaker embedding -> $DATA/utt2embedding.pt, spk2embedding.pt"
    python3 "$R/tools/extract_embedding.py" --dir "$DATA" --onnx_path "$P/campplus.onnx"
    ;;
  2)
    echo "STAGE 2 discrete speech token -> $DATA/utt2speech_token.pt"
    python3 "$R/tools/extract_speech_token.py" --dir "$DATA" --onnx_path "$P/speech_tokenizer_v3.onnx"
    ;;
  3)
    echo "STAGE 3 parquet -> $DATA/parquet"
    mkdir -p "$DATA/parquet"
    python3 "$R/tools/make_parquet_list.py" \
      --num_utts_per_parquet 300 \
      --num_processes 4 \
      --src_dir "$DATA" \
      --des_dir "$DATA/parquet"
    ;;
  *)
    echo "unknown stage $STAGE" >&2
    exit 2
    ;;
esac

ls -l "$DATA" | awk '{print $5, $9}'
