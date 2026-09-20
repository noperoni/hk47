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

THE CEILING IS 0.12s, SET 2026-09-19 FROM EIGHT LABELLED RENDERS. Master marked
each of the best-of-N winners good or "pause before master too long", and the
measurement brackets his line tightly: accepted at 0.05s and 0.08s, faulted at
0.17s, 0.24s and 0.29s. 0.12s is the midpoint of that bracket and it reproduces
all six of his rulings, the sixth being ref31-long, which no model can align and
which the gate therefore refuses rather than passes.

His earlier blind ranking, which put 0.19s first and faulted 0.44s, set this at
0.30s. The second set is the sharper instrument: it is per-file labels rather
than an ordering, and it was taken after the blip-merging fix below changed what
the numbers mean.

THE COLON IS JUDGED TOO, AT 0.42s, added 2026-09-19 and the sharpest result of
the lot. Master labelled eight renders and the pause after the qualifier splits
them perfectly: 0.04s, 0.06s, 0.18s, 0.28s and 0.40s accepted, 0.45s, 0.48s and
0.48s faulted. Two of the three he rejected have unimpeachable commas, and one
of them has no comma in it at all, so the hole after "Explanation:" and
"Affirmation:" is what he was hearing. The qualifier snaps into its sentence; it
does not breathe. 0.42s is the midpoint of that 0.40/0.45 bracket, which is a
thin margin and the first number to revisit if a render sounds wrong and passes.

The full stop is measured and not judged. It runs 0.22-0.32s in renders he
accepted and 0.55s in one he ranked first, and nothing has ever been faulted
on it.

THE CALLSIGN MISS IS FIXED, 2026-09-20, and the diagnosis I had carried for two
sessions was wrong. "difflib will not match hk against however Whisper spells the
callsign" named the symptom and not the cause. Probed across two models and two
renders, Whisper writes "HK-47" three ways:

    ' HK', '-47'        which tokenises to hk, 47 and matches the text exactly
    ' HK47'             one solid token, which matches neither hk nor 47
    ' hk', ' forty', ' seven'

So the boundary was lost only in the solid case, and the fault was the tokeniser
rather than the matcher: [A-Za-z0-9']+ keeps letters and digits together, so it
cannot see the hk inside hk47. Splitting letter runs from digit runs on BOTH
sides makes all three spellings align on hk, which is the only token this line's
one judged boundary needs. The spelled-out case still loses 47, and that costs
nothing, because no punctuation stands in front of it.

A boundary landing on a token that is NOT the first of its transcript word is
reported LOST rather than measured, because it has no onset of its own: the only
timestamp available is the whole word's start, which would put the pause before
"HK47" in front of a boundary that actually sits in the middle of it.
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

WORD = re.compile(r"[A-Za-z']+|[0-9]+")   # letters and digits are separate runs
PUNCT = re.compile(r"[,.:;!?]")
BAKEOFF = "/root/.omnivoice/bakeoff/hk47_bakeoff.py"

FRAME = 0.01
FLOOR = 0.01   # RMS, matching hk47-cap-pauses.py
WINDOW = 0.6   # how far either side of the transcript's word start to look
BLIP = 4       # frames of sound too short to be a word, so too short to end a pause


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

    BLIPS ARE MERGED INTO THE RUN THEY INTERRUPT, added 2026-09-19. ref31-long
    breaks as 0.09s silent, a 20ms tick, then 0.44s silent, all at one boundary.
    Master hears one long pause there and faulted it; the unmerged scan reported
    0.09s. Nothing under BLIP frames can be a word, so it does not end a pause.
    """
    lo = max(0, int(max(previous, onset - WINDOW) / FRAME))
    hi = min(len(energy), int(onset / FRAME) + 1)
    longest, run, voiced = 0, 0, 0
    for value in energy[lo:hi]:
        if value < FLOOR:
            run, voiced = run + voiced + 1, 0
        elif run and voiced < BLIP:
            voiced += 1          # held, in case silence resumes past this tick
        else:
            run, voiced = 0, 0
        longest = max(longest, run)
    return round(longest * FRAME, 2)


def judge(model, path, text, ceilings):
    heard = heard_words(model, path)
    energy = frame_energy(path)

    # One transcript word can carry several tokens, which is the whole callsign
    # fix: "HK47" has to offer an "hk" for the text's "hk" to find. origin says
    # which transcript word a token came from, and leads says whether it began
    # that word and therefore owns its onset.
    spoken, origin, leads = [], [], []
    for index, word in enumerate(heard):
        for position, token in enumerate(WORD.findall(word["word"].lower())):
            spoken.append(token)
            origin.append(index)
            leads.append(position == 0)

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
        if j is None or (j < len(leads) and not leads[j]):
            # Not aligned at all, or aligned inside a transcript word and so
            # without an onset of its own. Both are uncertifiable, not measured.
            lost.append(label)
        elif origin[j] == 0:
            at_head.append(label)
        else:
            # float() throughout: Whisper hands back numpy scalars, and a numpy
            # bool falling out of the comparison is not JSON serialisable.
            h = origin[j]
            start = float(heard[h]["start"])
            gaps.append({
                "after": words[i - 1],
                "before": words[i],
                "punct": mark,
                "gap": silence_before(energy, start, float(heard[h - 1]["start"])),
                "heard_gap": round(start - float(heard[h - 1]["end"]), 2),
            })

    # Whisper's word alignment collapses without warning, tiling every word end
    # to end so that its own gaps are uniformly zero. It did that to a good
    # render on small.en and to a different good render on medium.en, so the
    # verdict reads the waveform and this only marks the transcript as not worth
    # trusting for position either.
    #
    # THE TELL IS THE CONTRADICTION, not the zeros. A line delivered without
    # pauses honestly reports no gaps, and "short" does exactly that at 0.04s
    # measured. A transcript claiming no gap anywhere while the waveform holds a
    # third of a second of internal silence is the collapse.
    heard_gaps = [float(b["start"]) - float(a["end"]) for a, b in zip(heard, heard[1:])]
    voiced = np.flatnonzero(energy >= FLOOR)
    internal = 0.0
    if len(voiced) > 1:
        internal = silence_before(energy, float(voiced[-1]) * FRAME,
                                  float(voiced[0]) * FRAME)
    suspect = bool(heard_gaps) and not any(g > 0.01 for g in heard_gaps) \
        and internal >= 0.15

    judged = [g for g in gaps if g["punct"] in ceilings]
    excess = max((round(g["gap"] - ceilings[g["punct"]], 2) for g in judged),
                 default=None)
    worst = max((g["gap"] for g in judged), default=None)
    return {
        "path": path,
        "pass": bool(judged and not lost and not suspect and excess <= 0),
        "max_gap": worst,
        "excess": excess,
        "ceilings": ceilings,
        "gaps": gaps,
        "lost": lost,
        "at_head": at_head,
        "suspect_alignment": suspect,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", default="long", help="key in hk47_bakeoff.LINES")
    ap.add_argument("--text", default="", help="the spoken text, overriding --line")
    ap.add_argument("--ceiling", type=float, default=0.12,
                    help="ceiling at a comma, in seconds")
    ap.add_argument("--colon-ceiling", type=float, default=0.42,
                    help="ceiling after a qualifier's colon, in seconds")
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

    # Two entries rather than a parser for a syntax nobody asked for. A full
    # stop is absent deliberately: it runs 0.22-0.32s in renders Master passed
    # and nothing has ever been faulted on it.
    ceilings = {",": args.ceiling, ":": args.colon_ceiling}

    model = whisper.load_model(args.model, device="cpu")
    verdicts = [judge(model, path, text, ceilings) for path in args.wavs]

    # A collapsed alignment is per model and per file, so the second opinion is
    # only paid for on the renders that need it.
    retry = [v for v in verdicts if v["suspect_alignment"]] if args.fallback_model else []
    if retry:
        second = whisper.load_model(args.fallback_model, device="cpu")
        for verdict in retry:
            again = judge(second, verdict["path"], text, ceilings)
            again["model"] = args.fallback_model
            verdicts[verdicts.index(verdict)] = again

    if args.json:
        print(json.dumps(verdicts))
        return 0

    for verdict in verdicts:
        worst, over = verdict["max_gap"], verdict["excess"]
        summary = (f"worst {worst:.2f}s, {over:+.2f}s on its ceiling"
                   if worst is not None else "no judged boundary measured")
        note = "  alignment collapsed" if verdict["suspect_alignment"] else ""
        print(f"\n{os.path.basename(verdict['path'])}  "
              f"{'PASS' if verdict['pass'] else 'FAIL'}  {summary}{note}")
        for gap in verdict["gaps"]:
            limit = verdict["ceilings"].get(gap["punct"])
            if limit is None:
                note = "  (not judged)"
            elif gap["gap"] > limit:
                note = f"  <<< over {limit:.2f}s"
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
