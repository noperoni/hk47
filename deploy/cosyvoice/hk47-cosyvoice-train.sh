#!/bin/bash
# CosyVoice 3 LLM fine-tune on the HK-47 corpus.
#
#   docker exec -d omnivoice bash /root/.omnivoice/hk47-cosyvoice-train.sh
#
# Only the llm is trained: the recipe's own comment says "We only support llm
# traning for now", and the flow/hifigan arms of its loop would look for
# hifigan.pt, which the pretrained dir does not have. It ships hift.pt.
#
# Epochs are capped at 20 rather than upstream's 200 because every epoch writes a
# ~2GB epoch_N_whole.pt and the volume has 139G free. Twenty gives a checkpoint
# ladder to audition, which is how the GPT-SoVITS epoch question got settled.
#
# Runtime additions this needed, none of which survive the container being
# recreated and all of which belong in deploy/omnivoice/Dockerfile:
#   openai-whisper==20231117  declared in requirements.txt and never installed,
#                             needs --no-build-isolation because its sdist build
#                             wants pkg_resources and pip's isolated env takes a
#                             setuptools too new to have it
#   numba, llvmlite           only because whisper/__init__ imports whisper.timing
#   pyarrow                   make_parquet_list.py writes its .list files and
#                             exits 0 without it, leaving lists pointing at a
#                             .tar that was never created
#   deepspeed, tensorboard    train.py imports both unconditionally, even on the
#                             torch_ddp path that never uses deepspeed
#   hydra-core, gdown, and    pulled in through matcha.utils, plus conformer,
#   the rest of the 19        diffusers, x-transformers, librosa, onnx, pyworld,
#                             inflect, wetext, wget
#
# Deliberately NOT installed: grpcio-tools==1.57.0, which would drag protobuf
# from 7.36.1 down to 4.25.9 and break tensorboard, and it is only needed by the
# gRPC serving runtime. onnxruntime-gpu and the tensorrt trio are skipped too:
# onnxruntime 1.30 is already present and / has 11G free.
#
# speech_tokenizer_v3.batch.onnx was missing from the pretrained bundle and was
# fetched from the upstream repo. train.py sets os.environ['onnx_path'] with no
# guard, so the online-feature path cannot be declined and that file is required
# even though the parquet already carries offline tokens.
#
# num_workers is 0 because the container's /dev/shm is Docker's default 64MB and
# two workers at prefetch 100 die with a Bus error on the first batch. Raising it
# means recreating the container with --shm-size, which would discard every pip
# install listed above.
set -euo pipefail

R=/root/.omnivoice/engines/cosyvoice/CosyVoice
E="$R/examples/libritts/cosyvoice3"
P="$R/pretrained_models/Fun-CosyVoice3-0.5B"
EXP="$E/exp/hk47/llm/torch_ddp"

export PYTHONIOENCODING=UTF-8
export PYTHONPATH="$R:$R/third_party/Matcha-TTS:${PYTHONPATH:-}"
export CUDA_VISIBLE_DEVICES=0
cd "$E"

cp data/hk47-train/parquet/data.list data/train.data.list
cp data/hk47-dev/parquet/data.list data/dev.data.list
mkdir -p "$EXP" "$E/tensorboard/hk47/llm"

torchrun --nnodes=1 --nproc_per_node=1 \
    --rdzv_id=1986 --rdzv_backend="c10d" --rdzv_endpoint="localhost:1234" \
  "$R/cosyvoice/bin/train.py" \
  --train_engine torch_ddp \
  --config conf/hk47-cosyvoice3.yaml \
  --train_data data/train.data.list \
  --cv_data data/dev.data.list \
  --qwen_pretrain_path "$P/CosyVoice-BlankEN" \
  --onnx_path "$P" \
  --model llm \
  --checkpoint "$P/llm.pt" \
  --model_dir "$EXP" \
  --tensorboard_dir "$E/tensorboard/hk47/llm" \
  --ddp.dist_backend nccl \
  --num_workers 0 \
  --prefetch 10 \
  --pin_memory \
  --use_amp \
  --deepspeed_config ./conf/ds_stage2.json \
  --deepspeed.save_states model_only
