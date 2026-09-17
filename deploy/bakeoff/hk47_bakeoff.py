"""Shared constants for the zero-shot engine bake-off.

Four engines, one reference, one line, no fine-tuning anywhere. The point is to
find out which engine clones this voice before any of them earns a training run,
because two sessions went into fine-tuning an engine whose zero-shot output was
never auditioned first.

Every runner imports this by path rather than by package, because each engine
lives in its own uv venv and none of them can see the others.

The reference and the line are deliberately the ones CosyVoice was judged on, so
the new renders can be put beside the old ones without re-rendering anything.
"""

import os

REF_WAV = "/root/.omnivoice/voices/7d29527a.wav"

# The reference transcript, for the engines that want one. CosyVoice needed the
# <|endofprompt|> instruct prefix and nothing else does, so it is not here: this
# is the plain text of what the reference wav actually says.
REF_TEXT = (
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

# Four lines rather than one, because a single sample hides the failure modes
# that matter here. None of them appears in REF_TEXT: an engine parroting the
# reference back is not evidence of anything.
#
#   long     the CosyVoice audition line, kept so new renders can sit beside old
#   short    a clipped line, where an engine that pads or trails shows it
#   dense    numbers and a long clause, which is where prosody falls apart
#   query    a question, where a flat delivery either finds the rise or does not
LINES = {
    "long": TARGET,
    "short": "Affirmation: HK-47 exists only to serve.",
    "dense": (
        "Explanation: The card is idle, master, and the forty gigabytes of "
        "checkpoints it filled produced nothing worth hearing."
    ),
    "query": (
        "Query: Shall I reclaim that space, master, or do you intend to keep "
        "the wreckage as a monument?"
    ),
}

OUT = "/root/.omnivoice/hk47-bakeoff/out"


def out_path(engine, line="long"):
    os.makedirs(OUT, exist_ok=True)
    return os.path.join(OUT, f"{engine}-{line}.wav")


def report(engine, path, seconds=None, sample_rate=None):
    bits = [f"BAKEOFF {engine}: {path}"]
    if seconds is not None:
        bits.append(f"{seconds:.2f}s")
    if sample_rate is not None:
        bits.append(f"{sample_rate} Hz")
    print("  ".join(bits), flush=True)
