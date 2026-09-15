#!/usr/bin/env python3
"""Export the 291-clip HK-47 corpus as a fine-tuning dataset, from an owned install.

Why this exists. Zero-shot cloning needed one 20-second reference and got it by
hand. A fine-tune needs the whole corpus with transcripts, in two different
layouts, and needs them to be reproducible rather than assembled once by hand in
a shell. This writes both layouts from hk47-lines.tsv and throws nothing away.

WHOLE CLIPS ONLY. hk47-wiring.tsv's cut/splice/graft ops exist to make a pack
clip say one thing; a trainer wants what the actor actually performed, so this
reads hk47-lines.tsv (every clip, untouched) and not the wiring.

NO PROCESSING, by Master's ruling of 2026-09-15 on the cloning reference: he
rejected afftdn nr=24 for the reference even though it measured near-reference,
so the corpus is not quietly cleaned either. Audio is the raw MP3 decode: mono,
44100, s16, which is hk47-render.py's own output format. Both trainers resample
for themselves (CosyVoice's dataset pipeline to 24k and its extractors to 16k,
GPT-SoVITS's step 2 to 32k), so exporting at the source's decode rate resamples
nothing here and loses nothing.

Two layouts, one set of wav files:

  <out>/wav/                  the clips
  <out>/train/, <out>/dev/    Kaldi-style, as examples/libritts/cosyvoice3
                              wants: wav.scp, text, utt2spk, spk2utt, instruct
  <out>/gpt-sovits.list       vocal_path|speaker|language|text, all 291 rows
  <out>/MANIFEST.tsv          utt, measured seconds, declared seconds, text

THE PATHS IN THE MANIFESTS ARE NOT THE PATHS WRITTEN TO. Both trainers run
inside a container that sees this directory at a different mount point, so
--audio-prefix sets what goes into wav.scp and the .list while --out sets where
the files land. Default prefix is the omnivoice container's view.

The instruct string is the recipe's own default, verbatim. CosyVoice 3 prepends
one to every training utterance and the zero-shot calls already in use send the
same string, so keeping it identical means a tuned checkpoint drops into the
existing VoiceStudio call path without changing how it is asked for. A custom
instruct would have to be reproduced at every inference call to match.

DEV SPLIT is held out from train because stage 6's average_model --val_best
ranks checkpoints by cv loss and has nothing to rank without one. It is taken at
a fixed stride over sorted utt ids, which spreads it across source modules
because resrefs sort by module, and is the same split every run.

Audio is extracted from an owned install for personal use only. It never reaches
a third party or a hosted service. It may cross the local network to another
machine under the same ownership, which is how the corpus reaches the TTS host,
and that is the only egress permitted.
"""

import argparse
import csv
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

SPEAKER = "hk47"
LANGUAGE = "en"
INSTRUCT = "You are a helpful assistant.<|endofprompt|>"
DEFAULT_OUT = "/nfs/ops-center/omnivoice/state/hk47-corpus"
DEFAULT_PREFIX = "/root/.omnivoice/hk47-corpus/wav"
DEFAULT_DEV = 12
DURATION_TOLERANCE = 0.06  # a measured length this far from the declared one is a match


def load_render():
    """Borrow the renderer's own extraction so the two can never disagree."""
    path = os.path.join(HERE, "hk47-render.py")
    spec = importlib.util.spec_from_file_location("hk47_render", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_lines(path):
    """[(utt, resref, declared_seconds, text), ...] in file order."""
    out = []
    with open(path) as fh:
        for row in csv.reader(fh, delimiter="\t"):
            if not row or row[0].startswith("#"):
                continue
            resref = row[0]
            out.append((resref.rstrip("_"), resref, float(row[1]), row[2].strip()))
    return out


def measure(wav_path):
    """Seconds of a pcm_s16le mono 44100 wav, from its size rather than a probe."""
    return (os.path.getsize(wav_path) - 44) / (44100 * 2)


def dev_split(utts, count):
    """A fixed stride over sorted ids, offset half a stride off the ends."""
    if count <= 0:
        return set()
    step = len(utts) / count
    return {utts[int(i * step + step / 2)] for i in range(count)}


def write_kaldi(dest, rows, prefix):
    """wav.scp / text / utt2spk / spk2utt / instruct for one split."""
    os.makedirs(dest, exist_ok=True)
    with open(os.path.join(dest, "wav.scp"), "w") as fh:
        for utt, _ref, _dur, _text in rows:
            fh.write(f"{utt} {prefix}/{utt}.wav\n")
    with open(os.path.join(dest, "text"), "w") as fh:
        for utt, _ref, _dur, text in rows:
            fh.write(f"{utt} {text}\n")
    with open(os.path.join(dest, "utt2spk"), "w") as fh:
        for utt, _ref, _dur, _text in rows:
            fh.write(f"{utt} {SPEAKER}\n")
    with open(os.path.join(dest, "spk2utt"), "w") as fh:
        fh.write(f"{SPEAKER} {' '.join(utt for utt, _r, _d, _t in rows)}\n")
    with open(os.path.join(dest, "instruct"), "w") as fh:
        for utt, _ref, _dur, _text in rows:
            fh.write(f"{utt} {INSTRUCT}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="where the files are written")
    ap.add_argument("--audio-prefix", default=DEFAULT_PREFIX,
                    help="the wav directory AS THE TRAINER SEES IT")
    ap.add_argument("--dev", type=int, default=DEFAULT_DEV,
                    help="clips held out of train for cv loss (0 for none)")
    ap.add_argument("--lines", default=os.path.join(HERE, "hk47-lines.tsv"))
    ap.add_argument("--keep", action="store_true",
                    help="reuse any wav already present at the right length")
    args = ap.parse_args()

    r = load_render()
    if not os.path.isdir(r.KOTOR):
        raise SystemExit(f"KOTOR install not found: {r.KOTOR} (set KOTOR_DIR)")
    waves = r.index_streamwaves(r.KOTOR)
    rows = load_lines(args.lines)

    seen = {}
    for utt, resref, _dur, _text in rows:
        if utt in seen:
            raise SystemExit(f"utt collision: {resref} and {seen[utt]} both -> {utt}")
        seen[utt] = resref
    missing = [ref for _u, ref, _d, _t in rows if ref.lower() not in waves]
    if missing:
        raise SystemExit(f"{len(missing)} clips have no audio, first: {missing[0]}")
    bad = [u for u, _r, _d, t in rows if not t or "\t" in t]
    if bad:
        raise SystemExit(f"{len(bad)} transcripts are empty or contain a tab: {bad[:3]}")

    wav_dir = os.path.join(args.out, "wav")
    os.makedirs(wav_dir, exist_ok=True)

    mismatched, made, reused = [], 0, 0
    manifest = []
    for utt, resref, declared, text in rows:
        out_path = os.path.join(wav_dir, f"{utt}.wav")
        if args.keep and os.path.exists(out_path) \
                and abs(measure(out_path) - declared) <= DURATION_TOLERANCE:
            reused += 1
        else:
            mp3 = r.to_mp3(waves[resref.lower()])
            try:
                r.render([(mp3, 0, 99)], out_path)
            finally:
                os.remove(mp3)
            made += 1
        actual = measure(out_path)
        if abs(actual - declared) > DURATION_TOLERANCE:
            mismatched.append((utt, declared, actual))
        manifest.append((utt, actual, declared, text))

    utts = sorted(u for u, _r, _d, _t in rows)
    dev = dev_split(utts, args.dev)
    train_rows = [row for row in rows if row[0] not in dev]
    dev_rows = [row for row in rows if row[0] in dev]

    write_kaldi(os.path.join(args.out, "train"), train_rows, args.audio_prefix)
    if dev_rows:
        write_kaldi(os.path.join(args.out, "dev"), dev_rows, args.audio_prefix)

    # GPT-SoVITS trains on the whole list: its prep has no cv split to feed.
    with open(os.path.join(args.out, "gpt-sovits.list"), "w") as fh:
        for utt, _ref, _dur, text in rows:
            fh.write(f"{args.audio_prefix}/{utt}.wav|{SPEAKER}|{LANGUAGE}|{text}\n")

    with open(os.path.join(args.out, "MANIFEST.tsv"), "w") as fh:
        fh.write("# utt\tmeasured_s\tdeclared_s\ttext\n")
        for utt, actual, declared, text in manifest:
            fh.write(f"{utt}\t{actual:.2f}\t{declared:.2f}\t{text}\n")

    total = sum(a for _u, a, _d, _t in manifest)
    print(f"{len(rows)} clips, {total / 60:.2f} minutes, "
          f"{made} rendered, {reused} reused")
    print(f"train {len(train_rows)}  dev {len(dev_rows)}  -> {args.out}")
    print(f"wav.scp points at {args.audio_prefix}")
    if mismatched:
        print(f"\n{len(mismatched)} DURATION MISMATCHES against hk47-lines.tsv:")
        for utt, declared, actual in mismatched:
            print(f"  {utt:24} declared {declared:6.2f}  measured {actual:6.2f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
