# VoiceStudio for the warehouse, which has no AVX-512.
#
# The stock image ships pedalboard 0.9.25, whose native extension contains an
# instruction the host CPU (i7-9700K: AVX, AVX2, no AVX-512) does not implement.
# Importing it raises SIGILL, which kills the interpreter outright.
#
# That matters more than a missing effects library normally would, because
# apply_mastering() runs a highpass and a gentle compressor on EVERY clone
# generation, and it reaches pedalboard through apply_effects_chain(). The
# upstream code guards that import with `except ImportError` and wraps the call
# in `except Exception`, so it degrades gracefully when pedalboard is ABSENT and
# not at all when pedalboard is PRESENT AND ILLEGAL. A signal is not an
# exception; nothing catches it and the container restarts mid-request.
#
# Removing the package makes the code take the path its own author designed for
# pedalboard's absence: mastering is skipped, generation succeeds. The cost is a
# highpass and some compression that ffmpeg can reapply downstream if wanted.
#
# Diagnosed 2026-09-15 from `traps: python3 trap invalid opcode ... in
# pedalboard_native.cpython-311-x86_64-linux-gnu.so` in the host kernel log,
# after three container deaths at exit code 132 across three unrelated engines.
#
# Everything else this deployment needed repairing lives under /root/.omnivoice,
# which is a bind mount onto the ops-center SSD, so it survives without baking:
#   - indextts2  checkpoints/config.yaml shipped with the authors' own cluster
#                paths for gpt_checkpoint and s2mel_checkpoint
#   - cosyvoice  installer skipped 16 declared packages and ignored the pinned
#                transformers version
#
# Build on the warehouse:
#   docker build -t omnivoice-studio:warehouse /mnt/ops-center/Personal/HK47/deploy/omnivoice
#
# Run (--network host is deliberate: install and models-dir routes are gated to
# a loopback client, so administering them means reaching the host's own stack):
#   docker run -d --name omnivoice --restart unless-stopped --gpus all \
#     --network host --env-file ~/.config/omnivoice/env \
#     -v /mnt/ops-center/omnivoice/data:/app/omnivoice_data \
#     -v /mnt/ops-center/omnivoice/state:/root/.omnivoice \
#     omnivoice-studio:warehouse

FROM ghcr.io/debpalash/omnivoice-studio:latest

RUN /opt/conda/bin/python3 -m pip uninstall -y pedalboard \
 && /opt/conda/bin/python3 -c "\
import importlib.util, sys; \
sys.exit(0 if importlib.util.find_spec('pedalboard') is None else 1)"
