#!/bin/bash
# CosyVoice 3 LoRA fine-tune on the HK-47 corpus, the second attempt.
#
#   docker exec -d omnivoice bash /root/.omnivoice/hk47-cosyvoice-train-lora.sh
#
# The first attempt updated every parameter and destroyed the model: CV loss
# climbed 3.436 to 8.972 across 20 epochs, accuracy fell 0.213 to 0.150, and
# epoch 0 was already wrong after 31 optimiser steps. See hk47-patch-lora.py for
# why adapters cannot reach the mapping that broke, and what the gate switches.
#
# Everything in hk47-cosyvoice-train.sh's header still applies: llm only, one
# 3090 serialised, num_workers 0 because /dev/shm is 64MB, the <|endofprompt|>
# prefix the manifest carries, and the runtime pip installs that die with the
# container. peft is one more of those and is installed below rather than
# assumed; it resolved to 0.21.0 with no downgrades against transformers 5.17.0.
#
# A separate exp dir. The failed run's 40G ladder is left where it is, because
# it is the control this run is measured against and deleting it is Master's
# call, not this script's.
set -euo pipefail

R=/root/.omnivoice/engines/cosyvoice/CosyVoice
E="$R/examples/libritts/cosyvoice3"
P="$R/pretrained_models/Fun-CosyVoice3-0.5B"
EXP="$E/exp/hk47-lora/llm/torch_ddp"

export PYTHONIOENCODING=UTF-8
export PYTHONPATH="$R:$R/third_party/Matcha-TTS:${PYTHONPATH:-}"
export CUDA_VISIBLE_DEVICES=0

export HK47_LORA=1
export HK47_LORA_R="${HK47_LORA_R:-16}"
export HK47_LORA_ALPHA="${HK47_LORA_ALPHA:-32}"
export HK47_LORA_DROPOUT="${HK47_LORA_DROPOUT:-0.05}"

python3 -c 'import peft' 2>/dev/null || pip install --quiet peft==0.21.0
python3 -c 'import peft; print("peft", peft.__version__)'

python3 /root/.omnivoice/hk47-patch-train-utils.py
python3 /root/.omnivoice/hk47-patch-lora.py

cd "$E"
cp /root/.omnivoice/hk47-cosyvoice3-lora.yaml conf/hk47-cosyvoice3-lora.yaml
cp data/hk47-train/parquet/data.list data/train.data.list
cp data/hk47-dev/parquet/data.list data/dev.data.list
mkdir -p "$EXP" "$E/tensorboard/hk47-lora/llm"

torchrun --nnodes=1 --nproc_per_node=1 \
    --rdzv_id=1986 --rdzv_backend="c10d" --rdzv_endpoint="localhost:1234" \
  "$R/cosyvoice/bin/train.py" \
  --train_engine torch_ddp \
  --config conf/hk47-cosyvoice3-lora.yaml \
  --train_data data/train.data.list \
  --cv_data data/dev.data.list \
  --qwen_pretrain_path "$P/CosyVoice-BlankEN" \
  --onnx_path "$P" \
  --model llm \
  --checkpoint "$P/llm.pt" \
  --model_dir "$EXP" \
  --tensorboard_dir "$E/tensorboard/hk47-lora/llm" \
  --ddp.dist_backend nccl \
  --num_workers 0 \
  --prefetch 10 \
  --pin_memory \
  --use_amp \
  --deepspeed_config ./conf/ds_stage2.json \
  --deepspeed.save_states model_only
