"""Fold a LoRA adapter into the pretrained llm.pt so a rung can be auditioned.

    docker exec omnivoice python3 /root/.omnivoice/hk47-merge-lora.py \
        --adapter .../exp/hk47-lora/llm/torch_ddp/epoch_9_whole.pt \
        --out     .../exp/hk47-lora/merged/epoch_9-llm.pt

The training run saves only what moved, so a rung on its own is not a model.
This puts it back on the pretrained tensors and writes something the audition
script can hand to CosyVoiceModel.load.

Deliberately no peft import. The arithmetic of a standard LoRA merge is
W <- W + (alpha / r) * B @ A, and spelling it out here means a merge done months
from now does not depend on peft still laying its module tree out the same way.
It is only correct for plain LoRA: rslora scales by alpha/sqrt(r) and DoRA adds a
magnitude vector, so if either is ever switched on this script must change too.
It refuses rather than guesses if it sees a key it does not recognise.

'epoch' and 'step' are dropped. They sit beside the tensors in every checkpoint
this recipe writes, and CosyVoiceModel.load is strict=True, so a merged file
that kept them would fail to load for a reason that looks nothing like the cause.
"""

import argparse
import re

import torch

LORA_A = re.compile(r"^(?P<base>.+)\.lora_A\.(?P<adapter>[^.]+)\.weight$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", required=True, help="epoch_N_whole.pt from the LoRA run")
    ap.add_argument("--base", default="/root/.omnivoice/engines/cosyvoice/CosyVoice/"
                                      "pretrained_models/Fun-CosyVoice3-0.5B/llm.pt")
    ap.add_argument("--out", required=True)
    ap.add_argument("--alpha", type=float, default=32.0,
                    help="lora_alpha the run used; not recorded in the checkpoint")
    args = ap.parse_args()

    base = torch.load(args.base, map_location="cpu")
    adapter = torch.load(args.adapter, map_location="cpu")
    epoch, step = adapter.pop("epoch", None), adapter.pop("step", None)

    merged, copied, seen = 0, 0, set()
    for key, a in list(adapter.items()):
        match = LORA_A.match(key)
        if match is None:
            continue
        prefix, name = match.group("base"), match.group("adapter")
        b_key = f"{prefix}.lora_B.{name}.weight"
        if b_key not in adapter:
            raise SystemExit(f"{key} has no matching {b_key}")
        b = adapter[b_key]

        # inject_adapter_in_model moves the original Linear down to .base_layer,
        # but the pretrained file predates the injection and still has it flat.
        target = f"{prefix}.weight"
        if target not in base:
            raise SystemExit(f"no pretrained tensor at {target}")

        r = a.shape[0]
        delta = (args.alpha / r) * (b.float() @ a.float())
        if delta.shape != base[target].shape:
            raise SystemExit(f"{target}: delta {tuple(delta.shape)} "
                             f"does not fit {tuple(base[target].shape)}")
        base[target] = (base[target].float() + delta).to(base[target].dtype)
        seen.update({key, b_key})
        merged += 1

    for key, value in adapter.items():
        if key in seen:
            continue
        if ".lora_" in key:
            raise SystemExit(f"unrecognised adapter tensor {key}, refusing to guess")
        # HK47_LORA_TRAIN_HEAD was up: llm_decoder and speech_embedding moved and
        # are full tensors, so they replace rather than add.
        if key not in base:
            raise SystemExit(f"trained tensor {key} has no pretrained counterpart")
        base[key] = value
        copied += 1

    torch.save(base, args.out)
    print(f"merged {merged} projections, copied {copied} whole tensors, "
          f"from epoch {epoch} step {step}, alpha {args.alpha} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
