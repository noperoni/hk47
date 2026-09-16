"""Two patches that give the CosyVoice 3 recipe a LoRA path, both idempotent.

Why LoRA at all: the full-parameter run destroyed the model. CV loss climbed
monotonically 3.436 to 8.972 over 20 epochs, accuracy fell 0.213 to 0.150, and
epoch 0 was already wrong after 31 optimiser steps, saying an 11-second line in
2.40s where epoch 10 took 20.00s. 620 Adam steps at lr 1e-5 over every parameter
of a 0.5B model, on 279 clips, does not adapt the timbre: it overwrites the
text-to-speech-token mapping the pretrained checkpoint spent its whole training
budget learning. Rank-16 adapters on the Qwen backbone cannot reach that mapping.

1. cosyvoice/bin/train.py gains a LoRA wrap between the checkpoint load and
   wrap_cuda_model. It must sit there and nowhere else: after the load, because
   injecting adapters first would leave llm.pt's tensors landing on renamed keys,
   and before the DDP wrap and the optimizer, because both enumerate parameters
   and neither re-reads the module tree afterwards.

   peft.inject_adapter_in_model is used rather than get_peft_model. get_peft_model
   returns a PeftModelForCausalLM whose forward is not Qwen2ForCausalLM's, and
   Qwen2Encoder calls the model with inputs_embeds, output_hidden_states and
   past_key_values directly. Injection edits the module tree in place and leaves
   the class and its signature alone.

2. cosyvoice/utils/train_utils.py save_model writes adapters only under the gate.
   The failed run wrote a ~2GB epoch_N_whole.pt per epoch and spent 40G on a
   ladder with no usable rung. An adapter is about 35MB, and hk47-merge-lora.py
   reconstructs any epoch from the pretrained llm.pt on demand.

Both are gated on HK47_LORA=1, so with the gate down the code path is what
upstream shipped. Knobs, all optional:

    HK47_LORA_R           16     rank
    HK47_LORA_ALPHA       32     scaling is alpha/r, so 2.0 at the defaults
    HK47_LORA_DROPOUT     0.05
    HK47_LORA_TARGETS     the seven Qwen2 projections, comma separated
    HK47_LORA_TRAIN_HEAD  unset  set to 1 to also train llm_decoder and
                                 speech_embedding, which is the 6561-token
                                 vocabulary the failed run wrecked. Leave it
                                 frozen unless the frozen run proves too timid.

train_utils.py is already patched by hk47-patch-train-utils.py, which keeps the
true original at .orig. This script writes .pre-lora instead so that backup is
not overwritten with an already-patched file.
"""

import shutil
import sys

R = "/root/.omnivoice/engines/cosyvoice/CosyVoice"
TRAIN = f"{R}/cosyvoice/bin/train.py"
UTILS = f"{R}/cosyvoice/utils/train_utils.py"

TRAIN_ANCHOR = (
    "    # Dispatch model from cpu to gpu\n"
    "    model = wrap_cuda_model(args, model)\n"
)

TRAIN_GUARD = (
    "    # HK47: rank-decomposed adapters on the Qwen backbone, off unless the\n"
    "    # gate is up. Everything is frozen first and only the injected lora_\n"
    "    # tensors are handed back, so llm_decoder and speech_embedding, the\n"
    "    # 6561-token speech vocabulary, cannot move at all.\n"
    "    if os.environ.get('HK47_LORA') == '1':\n"
    "        from peft import LoraConfig, inject_adapter_in_model\n"
    "        for p in model.parameters():\n"
    "            p.requires_grad = False\n"
    "        lora_config = LoraConfig(\n"
    "            r=int(os.environ.get('HK47_LORA_R', 16)),\n"
    "            lora_alpha=int(os.environ.get('HK47_LORA_ALPHA', 32)),\n"
    "            lora_dropout=float(os.environ.get('HK47_LORA_DROPOUT', 0.05)),\n"
    "            bias='none',\n"
    "            target_modules=os.environ.get(\n"
    "                'HK47_LORA_TARGETS',\n"
    "                'q_proj,k_proj,v_proj,o_proj,gate_proj,up_proj,down_proj',\n"
    "            ).split(','),\n"
    "        )\n"
    "        model.llm.model = inject_adapter_in_model(lora_config, model.llm.model)\n"
    "        for name, p in model.named_parameters():\n"
    "            if 'lora_' in name:\n"
    "                p.requires_grad = True\n"
    "        if os.environ.get('HK47_LORA_TRAIN_HEAD') == '1':\n"
    "            for p in model.llm_decoder.parameters():\n"
    "                p.requires_grad = True\n"
    "            for p in model.speech_embedding.parameters():\n"
    "                p.requires_grad = True\n"
    "        trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)\n"
    "        total = sum(p.numel() for p in model.parameters())\n"
    "        assert trainable > 0, 'HK47_LORA is up but nothing is trainable'\n"
    "        logging.info('HK47 LoRA: {} trainable of {} parameters, {:.4f}%'.format(\n"
    "            trainable, total, 100.0 * trainable / total))\n"
    "\n"
) + TRAIN_ANCHOR

UTILS_ANCHOR = (
    '    if info_dict["train_engine"] == "torch_ddp":\n'
    "        if rank == 0:\n"
    "            torch.save({**model.module.state_dict(), 'epoch': info_dict['epoch'], "
    "'step': info_dict['step']}, save_model_path)\n"
)

UTILS_GUARD = (
    '    if info_dict["train_engine"] == "torch_ddp":\n'
    "        if rank == 0:\n"
    "            state_dict = model.module.state_dict()\n"
    "            # HK47: under LoRA the frozen tensors are the pretrained file we\n"
    "            # already have on disk. Keeping only what moved turns a 2GB\n"
    "            # checkpoint into a ~35MB adapter, and 20 epochs into 700MB.\n"
    "            if os.environ.get('HK47_LORA') == '1':\n"
    "                trainable = {n for n, p in model.module.named_parameters() "
    "if p.requires_grad}\n"
    "                state_dict = {n: v for n, v in state_dict.items() if n in trainable}\n"
    "                assert state_dict, 'HK47_LORA is up but no trainable tensor to save'\n"
    "            torch.save({**state_dict, 'epoch': info_dict['epoch'], "
    "'step': info_dict['step']}, save_model_path)\n"
)


def apply(path, name, marker, anchor, guard, backup):
    src = open(path).read()
    if marker in src:
        print(f"{name}: already patched")
        return
    if anchor not in src:
        print(f"{name}: anchor not found in {path}, refusing to guess", file=sys.stderr)
        raise SystemExit(1)
    shutil.copyfile(path, backup)
    open(path, "w").write(src.replace(anchor, guard))
    print(f"{name}: patched, {backup} kept")


def main():
    apply(
        TRAIN,
        "train.py lora wrap",
        "HK47_LORA",
        TRAIN_ANCHOR,
        TRAIN_GUARD,
        TRAIN + ".pre-lora",
    )
    apply(
        UTILS,
        "train_utils.py adapter-only save",
        "HK47_LORA",
        UTILS_ANCHOR,
        UTILS_GUARD,
        UTILS + ".pre-lora",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
