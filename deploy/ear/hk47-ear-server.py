"""The ear, PERS-11: Whisper small.en held warm on the 3090, push-to-talk only.

    run by hk47-ear.service on the warehouse host; by hand:
    docker exec -d omnivoice /opt/conda/bin/python3 /root/.omnivoice/ear/hk47-ear-server.py

    POST /hear  <audio/wav body>  -> {"text": "...", "seconds": 0.21}
    GET  /health                  -> {"ok": true}

Master ruled push-to-talk on 2026-09-28 over the wake-word stack: a key held
while he speaks marks both ends of his turn, so there is no spotter, no voice
activity detection and no echo cancellation to run. This server only ever
hears a clip he chose to send.

Its own process, not a route on the mouth. The mouth is single threaded and a
render can hold it for 38s, and a "yes" queued behind a render is a droid that
does not listen. The gate's Whisper is not reused either: it runs on the CPU at
3.4s a clip, measured 2026-09-20, where this one on the GPU took 0.21s for a 3s
clip and 1.5 GB of VRAM (2026-09-28). /opt/conda already holds whisper with a
CUDA torch and the small.en weights, so nothing is installed.

LOOPBACK ONLY, for the mouth's reason: the container runs --network host. The
desktop reaches it through hk47-voice-tunnel.service, an `ssh -L 3902:...`. Clips are
written to a temp file, transcribed and deleted; nothing Master says is kept.
"""

import json
import os
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import whisper

HOST, PORT = "127.0.0.1", 3902
MODEL = "small.en"
TMP = "/root/.omnivoice/ear/tmp"
MAX_BODY = 4 * 1024 * 1024   # 60s of 16 kHz mono s16 is 1.9 MB; a held key, not a dictation

EAR = None   # loaded in __main__


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
            return self._json(200, {"ok": True, "model": MODEL})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/hear":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._json(413, {"error": f"body must be 1..{MAX_BODY} bytes"})
        audio = self.rfile.read(length)
        fd, path = tempfile.mkstemp(suffix=".wav", dir=TMP)
        start = time.perf_counter()
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(audio)
            # No previous-text conditioning: every clip is a fresh utterance, and
            # carrying context across them is how Whisper starts repeating itself.
            result = EAR.transcribe(path, language="en", fp16=True, beam_size=1, best_of=1,
                                    condition_on_previous_text=False)
        except Exception as exc:
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
        finally:
            os.unlink(path)
        return self._json(200, {"text": result["text"].strip(),
                                "seconds": round(time.perf_counter() - start, 2)})

    def log_message(self, fmt, *args):
        print(f"ear: {fmt % args}", flush=True)


if __name__ == "__main__":
    os.makedirs(TMP, exist_ok=True)
    for stale in os.listdir(TMP):
        os.unlink(os.path.join(TMP, stale))
    EAR = whisper.load_model(MODEL, device="cuda")
    print(f"ear: listening on {HOST}:{PORT}", flush=True)
    HTTPServer((HOST, PORT), Handler).serve_forever()
