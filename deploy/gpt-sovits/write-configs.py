#!/usr/bin/env python3
"""Write GPT-SoVITS's two training configs, as its WebUI would, for the HK-47 run.

The WebUI does not hand its trainers a config file from the repo. open1Ba and
open1Bb each load a base config, overwrite about fifteen keys from the form, and
drop the result in TEMP/. Driving s2_train.py and s1_train.py directly therefore
means doing that transformation, and this is a faithful transcription of it: every
value below is upstream's own default for a 24GB card and v2Pro, not a preference
expressed here.

  batch_size 12      minmem // 2, which is webui.py's own formula for 24GB
  s2 8 epochs, saving every 4
  s1 15 epochs, saving every 5
  text_low_lr_rate 0.4, if_dpo off, grad_ckpt off, latest-only and
  every-weights both on

tmp_s1 is written with yaml.dump, exactly as the WebUI writes it, and NOT as JSON
on the "YAML is a superset" argument. MEASURED: that argument is wrong for small
floats. json.dump renders lr_init 0.00001 as `1e-05`, PyYAML's 1.1 float resolver
requires a dot in the mantissa, so it loads as the STRING "1e-05" and s1 dies in
WarmupCosineLRSchedule with `unsupported operand type(s) for -: 'float' and
'str'`. yaml.dump emits `1.0e-05`, which its own loader resolves as a float.

Paths are all relative to the project root, because both trainers are launched
with that as their working directory inside the container, and an absolute host
path would be wrong there.
"""

import json
import os
import sys

import yaml

EXP = "hk47"
VERSION = "v2Pro"
GPU = "0"
BATCH = 12
S2_EPOCHS = 8
S2_SAVE_EVERY = 4
S1_EPOCHS = 15
S1_SAVE_EVERY = 5
TEXT_LOW_LR_RATE = 0.4

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/mnt/ops-center/gpt-sovits"
EXP_DIR = f"logs/{EXP}"
PRE = "GPT_SoVITS/pretrained_models"


def rel(*parts):
    return os.path.join(ROOT, *parts)


def main():
    os.makedirs(rel("TEMP"), exist_ok=True)
    os.makedirs(rel(EXP_DIR, f"logs_s2_{VERSION}"), exist_ok=True)
    os.makedirs(rel(EXP_DIR, "logs_s1"), exist_ok=True)
    # The two weight roots are created by the WebUI at startup, not by either
    # trainer, and neither config names them. MEASURED the hard way: an s2 run
    # completed all 8 epochs and then died in process_ckpt.savee with
    # FileNotFoundError on SoVITS_weights_v2Pro/, losing only the small weight
    # because the full checkpoints go to logs_s2_* instead.
    os.makedirs(rel(f"SoVITS_weights_{VERSION}"), exist_ok=True)
    os.makedirs(rel(f"GPT_weights_{VERSION}"), exist_ok=True)

    with open(rel("GPT_SoVITS", "configs", f"s2{VERSION}.json")) as fh:
        s2 = json.load(fh)
    s2["train"].update({
        "batch_size": BATCH,
        "epochs": S2_EPOCHS,
        "text_low_lr_rate": TEXT_LOW_LR_RATE,
        "pretrained_s2G": f"{PRE}/{VERSION}/s2G{VERSION}.pth",
        "pretrained_s2D": f"{PRE}/{VERSION}/s2D{VERSION}.pth",
        "if_save_latest": True,
        "if_save_every_weights": True,
        "save_every_epoch": S2_SAVE_EVERY,
        "gpu_numbers": GPU,
        "grad_ckpt": False,
        "lora_rank": 32,
    })
    s2["model"]["version"] = VERSION
    s2["data"]["exp_dir"] = s2["s2_ckpt_dir"] = EXP_DIR
    s2["save_weight_dir"] = f"SoVITS_weights_{VERSION}"
    s2["name"] = EXP
    s2["version"] = VERSION
    with open(rel("TEMP", "tmp_s2.json"), "w") as fh:
        json.dump(s2, fh)

    with open(rel("GPT_SoVITS", "configs", "s1longer-v2.yaml")) as fh:
        s1 = yaml.safe_load(fh)
    s1["train"].update({
        "batch_size": BATCH,
        "epochs": S1_EPOCHS,
        "save_every_n_epoch": S1_SAVE_EVERY,
        "if_save_every_weights": True,
        "if_save_latest": True,
        "if_dpo": False,
        "half_weights_save_dir": f"GPT_weights_{VERSION}",
        "exp_name": EXP,
    })
    s1["pretrained_s1"] = f"{PRE}/s1v3.ckpt"
    s1["train_semantic_path"] = f"{EXP_DIR}/6-name2semantic.tsv"
    s1["train_phoneme_path"] = f"{EXP_DIR}/2-name2text.txt"
    s1["output_dir"] = f"{EXP_DIR}/logs_s1_{VERSION}"
    with open(rel("TEMP", "tmp_s1.yaml"), "w") as fh:
        yaml.dump(s1, fh, default_flow_style=False)

    print(f"wrote TEMP/tmp_s2.json and TEMP/tmp_s1.yaml under {ROOT}")
    print(f"  batch {BATCH}, s2 {S2_EPOCHS} epochs, s1 {S1_EPOCHS} epochs, {VERSION}")


if __name__ == "__main__":
    main()
