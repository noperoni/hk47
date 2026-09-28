"""The mouth, PERS-2: IndexTTS 2.5 on ref13 held warm, behind the pause gate.

    run by hk47-mouth.service on the warehouse host; by hand:
    docker exec omnivoice bash -lc \
      'cd /root/.omnivoice/engines/indextts2/index-tts-2.5 && \
       .venv/bin/python /root/.omnivoice/mouth/hk47-mouth-server.py'

    POST /say  {"text": "Statement: ...", "rolls": 4, "gate": true}  -> audio/wav
    GET  /health                                                      -> {"ok": true}

The bake-off scripts load the model per call and batch their rolls because
Whisper was loaded per call too. Here both stay warm, so a roll is judged the
moment it exists and the first one under the ceilings is returned: the common
case costs one render and one alignment, not N of each.

LOOPBACK ONLY. The container runs --network host, so 0.0.0.0 would hand the LAN
a GPU. The desktop reaches it through `ssh -L 3901:127.0.0.1:3901 warehouse`,
the same pattern the VoiceStudio UI on 3900 already uses. HTTPServer is single
threaded on purpose: the GPU does one utterance at a time, and a second caller
waits rather than splitting the card.

The verdict rides back in headers, because the caller wants the audio as the
body and still needs to know whether it was spoken under protest:

    X-HK47-Gate     pass | abstain | protest | off
    X-HK47-Rolls    how many renders it took
    X-HK47-Seconds  wall clock for the whole request
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ENGINE = "/root/.omnivoice/engines/indextts2/index-tts-2.5"
REF = "/root/.omnivoice/hk47-bakeoff/refs/ref13-diag-then-protocol.wav"
GATE = "/root/.omnivoice/bakeoff/hk47-gate.py"
GATE_PYTHON = "/opt/conda/bin/python3"   # Whisper lives there, not in the engine venv
TMP = "/root/.omnivoice/mouth/tmp"

HOST, PORT = "127.0.0.1", 3901
MAX_TEXT = 400    # a notice or a summary, never a document
MAX_ROLLS = 8     # the bake-off's own ceiling price
MAX_BODY = 4096
# Tighter than the bake-off's 0.12s, Master's ruling of 2026-09-27 after the
# phrase-pool auditions: commas still read slow in short spoken notices. The gate
# keeps 0.12s as its own default so the bake-off's history stays comparable.
COMMA_CEILING = 0.08

TTS = None   # loaded in __main__, so spoken() can be tested without a GPU

# The vocative "master" is never spoken, Master's ruling of 2026-09-27. The comma
# in front of it was the boundary IndexTTS stretched most (15 of 27 gate failures
# in draft 2 of the phrase pool), and ad-lib dialogue from a persona model will
# carry it in nearly every sentence. Written chat keeps it; only speech drops it.
# Order matters: the doubly fenced form first, so ", master," leaves one comma.
VOCATIVE = [
    (re.compile(r",\s*master\s*,", re.I), ","),                   # busy, master, others
    (re.compile(r",\s*master\s*(?=[.?!;:]|$)", re.I), ""),        # bother you, master?
    (re.compile(r"(^|[.?!:]\s+)master\s*,\s*(\w)", re.I),         # Master, a word
     lambda m: m.group(1) + m.group(2).upper()),
]


def spoken(text):
    for pattern, repl in VOCATIVE:
        text = pattern.sub(repl, text)
    return text


class Gate:
    """hk47-gate.py --serve, restarted if it ever dies."""

    def __init__(self):
        self.proc = None

    def _start(self):
        self.proc = subprocess.Popen(
            [GATE_PYTHON, GATE, "--serve"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
        )
        ready = self.proc.stdout.readline()
        if not ready or not json.loads(ready).get("ready"):
            raise RuntimeError("gate did not come up")

    def judge(self, text, wav):
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        self.proc.stdin.write(json.dumps({"text": text, "wavs": [wav],
                                          "ceiling": COMMA_CEILING}) + "\n")
        line = self.proc.stdout.readline()
        if not line:
            self.proc = None
            raise RuntimeError("gate died mid-judgement")
        out = json.loads(line)
        if isinstance(out, dict):
            raise RuntimeError(out.get("error", "gate refused"))
        return out[0]


GATE_PROC = Gate()


def say(text, rolls, gated):
    """Roll until a render clears the gate. Returns (path, verdict, rolls used)."""
    renders = []
    for n in range(1, rolls + 1):
        fd, path = tempfile.mkstemp(suffix=".wav", dir=TMP)
        os.close(fd)
        TTS.infer(spk_audio_prompt=REF, text=text, output_path=path, lang="en", verbose=False)
        if not gated:
            return path, "off", n, renders
        verdict = GATE_PROC.judge(text, path)
        if verdict["pass"]:
            return path, "pass", n, renders
        if verdict["excess"] is None:
            if not renders:
                # No judged boundary on the first take (a line without commas,
                # or an aligner that lost them), so re-rolling has nothing to aim at.
                return path, "abstain", n, renders
            # After measured failures, a take whose boundaries were lost is
            # unknown rather than better: keep rolling, and never return it.
            os.unlink(path)
            continue
        renders.append((path, verdict))
    # Nothing cleared: keep the least bad and say so, as the bake-off does.
    path, _ = min(renders, key=lambda pv: pv[1]["excess"])
    return path, "protest", rolls, [r for r in renders if r[0] != path]


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/say":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._json(413, {"error": f"body must be 1..{MAX_BODY} bytes"})
        try:
            req = json.loads(self.rfile.read(length))
            text = spoken(str(req["text"]).strip())
            rolls = int(req.get("rolls", 4))
            gated = bool(req.get("gate", True))
        except (ValueError, KeyError, TypeError) as exc:
            return self._json(400, {"error": f"bad request: {exc}"})
        if not text or len(text) > MAX_TEXT:
            return self._json(400, {"error": f"text must be 1..{MAX_TEXT} characters"})
        rolls = max(1, min(rolls, MAX_ROLLS))

        start = time.perf_counter()
        path, verdict, used, spare = None, None, 0, []
        try:
            path, verdict, used, spare = say(text, rolls, gated)
            audio = open(path, "rb").read()
        except Exception as exc:
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            for p in [path] + [s[0] for s in spare]:
                if p and os.path.exists(p):
                    os.unlink(p)
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("X-HK47-Gate", verdict)
        self.send_header("X-HK47-Rolls", str(used))
        self.send_header("X-HK47-Seconds", f"{time.perf_counter() - start:.2f}")
        self.end_headers()
        self.wfile.write(audio)

    def log_message(self, fmt, *args):
        print(f"mouth: {fmt % args}", flush=True)


if __name__ == "__main__":
    sys.path.insert(0, ENGINE)
    os.chdir(ENGINE)
    from indextts.infer_v2_5 import IndexTTS2

    TTS = IndexTTS2(
        cfg_path=os.path.join(ENGINE, "checkpoints/config.yaml"),
        model_dir=os.path.join(ENGINE, "checkpoints"),
        use_bf16=True,
        use_qwen_emo=False,
    )
    os.makedirs(TMP, exist_ok=True)
    # A render that raised mid-roll never reached the handler's cleanup.
    for stale in os.listdir(TMP):
        os.unlink(os.path.join(TMP, stale))
    GATE_PROC._start()
    print(f"mouth: listening on {HOST}:{PORT}", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()
