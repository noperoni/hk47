"""Judge a render against the pause CEILING, so best-of-N has something to reject.

    docker exec omnivoice python3 /root/.omnivoice/bakeoff/hk47-gate.py \
        --line long out/a.wav out/b.wav
    docker exec omnivoice python3 /root/.omnivoice/bakeoff/hk47-gate.py \
        --json --text "Statement: ..." out/a.wav

hk47-word-gaps.py measures every gap and names none of them. This one knows what
the line was supposed to say, so it can ask the question Master's ruling actually
turns on: how long the silence runs at each place where the text has punctuation.

THE TARGET IS A CEILING, AND THE FLOOR THEORY IS DEAD, ruled 2026-09-18 on a
blind listen of six renders. Best to worst he ordered them at measured silences
of 0.19s, 0.07s, 0.07s, 0.11s, 0.44s and 0.44s before "master", calling the last
two the worst "only relative to how good the others were". Short pauses are what
he likes, the two long ones are the defect, and no render in the set was ever
rejected for pausing too little.

That reinstates his original ruling of 2026-09-18, that the pause runs about
twice as long as it should, which the previous session withdrew in commit 37c1c97
on the strength of a 0.00s reading. That reading was the aligner collapsing, not
audio: the file it came from is the one he has now ranked first.

The ceiling is 0.30s by default, a midpoint rather than a measurement, because
his set jumps from 0.19s accepted to 0.44s faulted with nothing auditioned in
between. Move it with --ceiling once something in the gap is heard.

IT IS JUDGED AT COMMAS ONLY, and that is measurement rather than taste. The
full stop in "master. The" holds 0.55s in the render he ranked first, and the
colon in "Statement: Both" sits at 0.32-0.45s in all six including his
favourites. A sentence break and a discourse prefix are supposed to breathe; the
comma before the address is the one boundary his ranking discriminates on. Widen
it with --judge-punct once another class has a ruling behind it.
# ponytail: one ceiling shared by whichever classes are judged; separate numbers
# per class when a second class earns one.
#
# No floor is imposed. 0.07s of silence sits in two of his top three, so words
# arriving close together is not a defect he minds, and a floor invented here
# would reject the renders he likes.

Alignment is by difflib against the source text rather than by trusting the
transcript, because Whisper writes "fine-tunes" as one token where the text has
two words. A boundary the aligner loses mid-line fails the render rather than
passing it quietly, since an unmeasured gap is not a measured one.

THE HEAD OF THE CLIP IS THE EXCEPTION, measured on 2026-09-18. Whisper dropped
"Statement:" from roll1 and stretched the first token back to 0.00s to cover it,
which would have failed the exact render Master passed on a blind listen. A
20ms RMS probe showed roll1's onset pattern is identical to roll3's, where the
word was transcribed, so the audio has it and the transcript does not. A boundary
whose following word lands first in the transcript is therefore reported and not
counted against the render.

CPU deliberately, for the reason in hk47-word-gaps.py: the CUDA word-timestamp
path dies in a triton median-filter kernel this container will not let it patch.
"""

import argparse
import difflib
import importlib.util
import json
import os
import re
import sys

import numpy as np
import soundfile as sf
import whisper

WORD = re.compile(r"[A-Za-z0-9']+")
PUNCT = re.compile(r"[,.:;!?]")
BAKEOFF = "/root/.omnivoice/bakeoff/hk47_bakeoff.py"

FRAME = 0.01
FLOOR = 0.01   # RMS, matching hk47-cap-pauses.py
WINDOW = 0.6   # how far either side of the transcript's word start to look


def expected_pauses(text):
    """The words of the line, and the punctuation standing in front of each."""
    words, marks, prev_end = [], [], 0
    for match in WORD.finditer(text):
        words.append(match.group().lower())
        found = PUNCT.search(text[prev_end:match.start()])
        marks.append(found.group() if found else "")
        prev_end = match.end()
    return words, marks


def heard_words(model, path):
    result = model.transcribe(path, word_timestamps=True, language="en", fp16=False,
                              temperature=0.0)
    return [w for seg in result["segments"] for w in seg["words"]]


def frame_energy(path):
    """RMS per 10ms frame, the same probe hk47-cap-pauses.py walks."""
    x, sr = sf.read(path, dtype="float32")
    mono = x.mean(1) if x.ndim > 1 else x
    frame = int(sr * FRAME)
    usable = len(mono) // frame * frame
    return np.sqrt((mono[:usable].reshape(-1, frame) ** 2).mean(1))


def silence_before(energy, onset, previous):
    """The longest silent run between the previous word and this one.

    Whisper's own arithmetic, one word's end subtracted from the next word's
    start, is not silence: it includes the decay of the preceding word, which is
    what defeated hk47-cap-pauses.py. This reads the waveform instead and uses
    the transcript only as a pointer to where in the file to look.

    LONGEST RUN RATHER THAN THE RUN TOUCHING THE WORD, measured 2026-09-18. One
    render holds 0.45s of silence before "Both" and then a 50ms burst of breath
    immediately before the onset. Walking back from the word stopped at that
    burst and called the pause 0.03s. A breath is part of pausing, not the end
    of one, so the burst has to be stepped over rather than believed.
    """
    lo = max(0, int(max(previous, onset - WINDOW) / FRAME))
    hi = min(len(energy), int(onset / FRAME) + 1)
    longest, run = 0, 0
    for value in energy[lo:hi]:
        run = run + 1 if value < FLOOR else 0
        longest = max(longest, run)
    return round(longest * FRAME, 2)


def judge(model, path, text, ceiling, judge_punct):
    heard = heard_words(model, path)
    energy = frame_energy(path)
    spoken = []
    for word in heard:
        found = WORD.search(word["word"])
        spoken.append(found.group().lower() if found else "")

    words, marks = expected_pauses(text)
    aligned = {}
    matcher = difflib.SequenceMatcher(None, words, spoken, autojunk=False)
    for start_a, start_b, size in matcher.get_matching_blocks():
        for k in range(size):
            aligned[start_a + k] = start_b + k

    gaps, lost, at_head = [], [], []
    for i, mark in enumerate(marks):
        if not mark or i == 0:
            continue
        label = f"{words[i - 1]} | {words[i]}"
        j = aligned.get(i)
        if j is None:
            lost.append(label)
        elif j == 0:
            at_head.append(label)
        else:
            # float() throughout: Whisper hands back numpy scalars, and a numpy
            # bool falling out of the comparison is not JSON serialisable.
            start = float(heard[j]["start"])
            gaps.append({
                "after": words[i - 1],
                "before": words[i],
                "punct": mark,
                "gap": silence_before(energy, start, float(heard[j - 1]["start"])),
                "heard_gap": round(start - float(heard[j - 1]["end"]), 2),
            })

    # Whisper's word alignment collapses without warning, tiling every word end
    # to end so that its own gaps are uniformly zero. It did that to a good
    # render on small.en and to a different good render on medium.en, which is
    # why the verdict reads the waveform and this only marks the transcript as
    # not worth trusting for position either.
    heard_gaps = [float(b["start"]) - float(a["end"]) for a, b in zip(heard, heard[1:])]
    suspect = bool(heard_gaps) and not any(g > 0.01 for g in heard_gaps)

    judged = [g for g in gaps if g["punct"] in judge_punct]
    worst = max((g["gap"] for g in judged), default=None)
    return {
        "path": path,
        "pass": bool(judged and not lost and not suspect and worst <= ceiling),
        "max_gap": worst,
        "ceiling": ceiling,
        "judged_punct": judge_punct,
        "gaps": gaps,
        "lost": lost,
        "at_head": at_head,
        "suspect_alignment": suspect,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", default="long", help="key in hk47_bakeoff.LINES")
    ap.add_argument("--text", default="", help="the spoken text, overriding --line")
    ap.add_argument("--ceiling", type=float, default=0.30)
    ap.add_argument("--judge-punct", default=",",
                    help="which punctuation the ceiling applies to; the rest are "
                         "measured and reported only")
    ap.add_argument("--model", default="small.en")
    ap.add_argument("--fallback-model", default="medium.en",
                    help="re-read a render whose alignment collapsed; '' to disable")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("wavs", nargs="+")
    args = ap.parse_args()

    text = args.text
    if not text:
        spec = importlib.util.spec_from_file_location("hk47_bakeoff", BAKEOFF)
        bake = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bake)
        text = bake.LINES[args.line]

    model = whisper.load_model(args.model, device="cpu")
    verdicts = [judge(model, path, text, args.ceiling, args.judge_punct) for path in args.wavs]

    # A collapsed alignment is per model and per file, so the second opinion is
    # only paid for on the renders that need it.
    retry = [v for v in verdicts if v["suspect_alignment"]] if args.fallback_model else []
    if retry:
        second = whisper.load_model(args.fallback_model, device="cpu")
        for verdict in retry:
            again = judge(second, verdict["path"], text, args.ceiling, args.judge_punct)
            again["model"] = args.fallback_model
            verdicts[verdicts.index(verdict)] = again

    if args.json:
        print(json.dumps(verdicts))
        return 0

    for verdict in verdicts:
        worst = verdict["max_gap"]
        summary = (f"worst {worst:.2f}s at {verdict['judged_punct']!r}"
                   if worst is not None else "no judged boundary measured")
        note = "  alignment collapsed" if verdict["suspect_alignment"] else ""
        print(f"\n{os.path.basename(verdict['path'])}  "
              f"{'PASS' if verdict['pass'] else 'FAIL'}  {summary}{note}")
        for gap in verdict["gaps"]:
            if gap["punct"] not in verdict["judged_punct"]:
                note = "  (not judged)"
            elif gap["gap"] > verdict["ceiling"]:
                note = "  <<< over ceiling"
            else:
                note = ""
            print(f"  {gap['gap']:5.2f}s silent (whisper said {gap['heard_gap']:5.2f}s)  "
                  f"{gap['after']}{gap['punct']} | {gap['before']}{note}")
        for boundary in verdict["at_head"]:
            print(f"    ----  {boundary}  <<< first in the transcript, unmeasurable")
        for boundary in verdict["lost"]:
            print(f"    ----  {boundary}  <<< not aligned, cannot be certified")

    return 0 if any(v["pass"] for v in verdicts) else 1


if __name__ == "__main__":
    sys.exit(main())
