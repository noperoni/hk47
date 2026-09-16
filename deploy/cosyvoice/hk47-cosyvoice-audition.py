"""Render one line through a ladder of CosyVoice 3 fine-tune checkpoints.

    docker exec omnivoice python3 /root/.omnivoice/hk47-cosyvoice-audition.py 0 1 5 10 19

CosyVoiceModel.load calls load_state_dict(..., strict=True), while the trainer
saves epoch_N_whole.pt with 'epoch' and 'step' alongside the tensors, so each
checkpoint is stripped before it can stand in as llm.pt.

The model directory is symlinks to the pretrained bundle for everything except
llm.pt, which is rewritten in place per epoch: each one is 2GB and there is no
reason to hold five of them at once.

The reference is 7d29527a, "HK-47 ref-01", which Master approved by ear on
2026-09-15, with its transcript out of the voice_profiles table. No denoise, by
his ruling of the same day.
"""

import os
import shutil
import sys

import torch
import torchaudio

R = "/root/.omnivoice/engines/cosyvoice/CosyVoice"
P = f"{R}/pretrained_models/Fun-CosyVoice3-0.5B"
EXP = f"{R}/examples/libritts/cosyvoice3/exp/hk47/llm/torch_ddp"
WORK = "/root/.omnivoice/hk47-cosy-audition"
MODEL = f"{WORK}/model"
OUT = f"{WORK}/out"

# CosyVoice3 asserts <|endofprompt|> is present in the text or the prompt text.
# This is the instruct prefix, and it is byte-for-byte what every row of the
# training manifest carries, so inference and training agree on it.
INSTRUCT = "You are a helpful assistant.<|endofprompt|>"

REF_WAV = "/root/.omnivoice/voices/7d29527a.wav"
REF_TEXT = INSTRUCT + (
    "Statement: HK-47 is ready to serve, master. "
    "Affirmation: HK-47 exists only to serve, master. "
    "Observation: Organics have no sense of persistence. "
    "Statement: I will endeavour to do so, master. "
    "Observation: Now that is the master I remember."
)
TARGET = (
    "Statement: Both fine-tunes are complete, master. "
    "The corpus was thirty-eight minutes and it wanted none of it cleaned."
)

# Everything the inference side reads out of a model dir except the llm itself.
LINKED = [
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "cosyvoice3.yaml",
    "config.json",
    "configuration.json",
    "CosyVoice-BlankEN",
]


def build_model_dir():
    os.makedirs(MODEL, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    for name in LINKED:
        dst = f"{MODEL}/{name}"
        if not os.path.lexists(dst):
            os.symlink(f"{P}/{name}", dst)


def install_checkpoint(epoch):
    # "base" installs the untouched pretrained llm, which is the control: if the
    # harness cannot render with weights that are known to work, the fault is here
    # and not in anything the trainer produced.
    if epoch == "base":
        shutil.copyfile(f"{P}/llm.pt", f"{MODEL}/llm.pt")
        print("base: installed the pretrained llm unchanged", flush=True)
        return True

    src = f"{EXP}/epoch_{epoch}_whole.pt"
    if not os.path.exists(src):
        print(f"epoch {epoch}: no checkpoint at {src}", file=sys.stderr)
        return False
    state = torch.load(src, map_location="cpu", weights_only=True)
    dropped = [k for k in ("epoch", "step") if k in state]
    for k in dropped:
        state.pop(k)
    torch.save(state, f"{MODEL}/llm.pt")
    print(f"epoch {epoch}: installed, dropped {dropped}, {len(state)} tensors")
    return True


def main():
    epochs = [a if a == "base" else int(a) for a in sys.argv[1:]] or [1]
    build_model_dir()

    # Imported here so the path is set up before cosyvoice pulls its own config.
    sys.path[:0] = [R, f"{R}/third_party/Matcha-TTS"]
    from cosyvoice.cli.cosyvoice import CosyVoice3

    for epoch in epochs:
        if not install_checkpoint(epoch):
            continue
        model = CosyVoice3(MODEL, load_trt=False, load_vllm=False, fp16=False)
        for i, result in enumerate(
            model.inference_zero_shot(TARGET, REF_TEXT, REF_WAV, stream=False)
        ):
            tag = epoch if isinstance(epoch, str) else f"epoch{epoch:02d}"
            path = f"{OUT}/{tag}.wav"
            torchaudio.save(path, result["tts_speech"], model.sample_rate)
            print(f"{epoch}: wrote {path} at {model.sample_rate} Hz", flush=True)
            break
        del model
        torch.cuda.empty_cache()

    shutil.rmtree(f"{MODEL}/llm.pt", ignore_errors=True) if os.path.isdir(f"{MODEL}/llm.pt") else None


if __name__ == "__main__":
    main()
