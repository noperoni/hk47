"""Word-level pause probe: how long is the silence before each word.

    docker exec omnivoice python3 /root/.omnivoice/bakeoff/hk47-word-gaps.py a.wav b.wav

An energy threshold finds gaps but cannot name them. This aligns the words, so a
ruling like "the pause before master is twice as long as it needs to be" becomes
a number that can be tuned against, which an RMS probe could not do: it read the
comma gaps as 0.48s and 0.52s and found nothing wrong.

Measured on 2026-09-18 over the two merged references, on the same line:

    ref31 (protocol then diagnostic)   gap before "Master"  0.64s
    ref13 (diagnostic then protocol)   gap before "Master"  0.30s

Master called it twice as long as it needed to be. It is 2.1x.

DO NOT RULE ON THESE NUMBERS ALONE, established 2026-09-18. Two failures live
here. The alignment collapses without warning, tiling every word end to end so
that every gap reads 0.00s, and it did that to a good render on small.en and to a
different good render on medium.en, which is where the withdrawn 0.00s of commit
37c1c97 came from. Even when it holds, the gap it prints is not silence: it
includes the decay of the preceding word, and it runs 0.15-0.45s longer than the
measured silence at the same boundary. hk47-gate.py reads the waveform and uses
this only as a pointer to where in the file to look.

CPU deliberately. Whisper's word-timestamp path calls a triton median-filter
kernel on CUDA, and this container's triton refuses the in-place kernel.src
rewrite it performs, so the GPU path raises AttributeError before any audio is
read. These clips are under ten seconds and the CPU path costs nothing.
"""

import os
import sys

import whisper

model = whisper.load_model("small.en", device="cpu")
for path in sys.argv[1:]:
    result = model.transcribe(path, word_timestamps=True, language="en", fp16=False)
    words = [w for seg in result["segments"] for w in seg["words"]]
    print(f"\n{os.path.basename(path)}")
    prev_end = 0.0
    for word in words:
        gap = word["start"] - prev_end
        flag = "  <<<" if gap >= 0.25 else ""
        print(f"  {word['start']:6.2f}-{word['end']:6.2f}  gap {gap:5.2f}s  "
              f"{word['word'].strip()}{flag}")
        prev_end = word["end"]
