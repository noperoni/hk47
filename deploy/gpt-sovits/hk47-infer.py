"""Render one line through a fine-tuned GPT-SoVITS pair with every sampling knob exposed.

Exists because upstream's GPT_SoVITS/inference_cli.py hardcodes top_p=1 and
temperature=1 into its get_tts_wav call, overriding the signature defaults of
0.6/0.6 that the WebUI and every published demo actually use. That is the
difference between decisive delivery and a breathy wander.
"""

import argparse
import os
import re
import sys

import soundfile as sf

from types import SimpleNamespace

from tools.i18n.i18n import I18nAuto
from GPT_SoVITS.inference_webui import change_gpt_weights, change_sovits_weights, get_tts_wav

i18n = I18nAuto()


# GPT_SoVITS/text/cleaner.py deletes the colon outright: "Statement: Both" becomes
# "Statement Both" with no boundary phoneme, so the model cannot pause where the
# qualifier ends no matter how long it trains. A comma survives the cleaner and
# Master ruled it the best of the survivors by ear on 2026-09-16; it also holds a
# steadier length than a period, 0.094-0.140s against 0.097-0.232s over three takes.
QUALIFIER = re.compile(r"^([A-Z][a-z]+):[ ]")


def comma_qualifier(text):
    """Rewrite a leading qualifier's colon as a comma so it survives text cleaning."""
    return QUALIFIER.sub(r"\1, ", text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gpt_model", required=True)
    ap.add_argument("--sovits_model", required=True)
    ap.add_argument("--ref_audio", required=True)
    ap.add_argument("--ref_text", required=True, help="literal text, not a path")
    ap.add_argument(
        "--fuse_refs",
        default="",
        help="comma-separated extra reference wavs whose timbre is averaged with the main one. "
        "v2Pro also averages a speaker-verification embedding per clip. Delivery still comes "
        "from --ref_audio alone: this fuses timbre, not performance.",
    )
    ap.add_argument("--target_text", required=True, help="literal text, not a path")
    ap.add_argument("--ref_language", default="英文")
    ap.add_argument("--target_language", default="英文")
    ap.add_argument("--out", required=True, help="output wav path")
    ap.add_argument("--top_k", type=int, default=20)
    ap.add_argument("--top_p", type=float, default=0.6)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--sample_steps", type=int, default=8)
    ap.add_argument("--pause_second", type=float, default=0.3)
    ap.add_argument("--if_sr", action="store_true")
    ap.add_argument(
        "--keep-colon",
        action="store_true",
        help="leave a leading qualifier's colon alone; it will be deleted by the text cleaner",
    )
    args = ap.parse_args()

    target_text = args.target_text if args.keep_colon else comma_qualifier(args.target_text)
    ref_text = args.ref_text if args.keep_colon else comma_qualifier(args.ref_text)

    # get_tts_wav reads path.name off each entry, because the WebUI hands it
    # gradio upload objects rather than strings.
    inp_refs = [SimpleNamespace(name=p) for p in args.fuse_refs.split(",") if p.strip()] or None

    change_gpt_weights(gpt_path=args.gpt_model)
    change_sovits_weights(sovits_path=args.sovits_model)

    result = list(
        get_tts_wav(
            ref_wav_path=args.ref_audio,
            prompt_text=ref_text,
            prompt_language=i18n(args.ref_language),
            text=target_text,
            text_language=i18n(args.target_language),
            top_k=args.top_k,
            top_p=args.top_p,
            temperature=args.temperature,
            speed=args.speed,
            sample_steps=args.sample_steps,
            pause_second=args.pause_second,
            if_sr=args.if_sr,
            inp_refs=inp_refs,
        )
    )
    if not result:
        sys.exit("get_tts_wav yielded nothing")

    rate, audio = result[-1]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    sf.write(args.out, audio, rate)
    fused = len(inp_refs) if inp_refs else 0
    print(
        f"{args.out}  {rate} Hz  top_k={args.top_k} top_p={args.top_p} "
        f"temp={args.temperature} fused_refs={fused}"
    )


if __name__ == "__main__":
    main()
