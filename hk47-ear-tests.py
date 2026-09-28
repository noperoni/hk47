#!/usr/bin/env python3
"""Tests for hk47-ear.py. No microphone, no warehouse.

The press policy is pure and driven from fixtures. The release path runs for
real against a stand-in /hear server on a loopback port and a WAV written here,
so the HTTP contract with deploy/ear/hk47-ear-server.py is exercised end to end
without Whisper. State goes to a temp dir, never to the live runtime dir.
"""

import importlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import wave
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ear = importlib.import_module("hk47-ear")

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok))
    print(f"    {'ok  ' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n            got  {got!r}\n            want {want!r}"))


def write_wav(path, seconds):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\0\0" * int(16000 * seconds))


def isolate(tmp):
    ear.STATE_DIR = tmp
    ear.RECORDER_PID = os.path.join(tmp, "ear-recorder.pid")
    ear.CLIP = os.path.join(tmp, "ear-clip.wav")
    ear.SPEAKING_PID = os.path.join(tmp, "speaking.pid")
    ear.HEARD = os.path.join(tmp, "heard.jsonl")
    ear.notify = lambda line: NOTIFIED.append(line)


NOTIFIED = []


def quiet(fn, *args):
    with redirect_stdout(io.StringIO()) as out:
        code = fn(*args)
    return code, json.loads(out.getvalue())


def policy_cases():
    print("  press policy")
    check("ordinary listens", ear.press_verdict("ordinary", False)[0], "listen")
    check("focus listens: he pressed it himself", ear.press_verdict("focus", False)[0], "listen")
    check("a game listens", ear.press_verdict("game", False)[0], "listen")
    check("a meeting refuses", ear.press_verdict("meeting", False)[0], "refuse")
    check("a second press is ignored", ear.press_verdict("ordinary", True)[0], "ignore")
    check("a tap is discarded", ear.usable(0.2), False)
    check("a word is kept", ear.usable(0.6), True)


def process_cases():
    print("  processes")
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        proc = subprocess.Popen(["sleep", "30"])
        with open(ear.SPEAKING_PID, "w") as handle:
            handle.write(f"{proc.pid}\n")
        check("a live pid under the wrong name is left alone", ear.stop(ear.SPEAKING_PID, "pw-play"), False)
        check("and is still running", proc.poll(), None)
        with open(ear.SPEAKING_PID, "w") as handle:
            handle.write(f"{proc.pid}\n")
        check("a live pid under its own name is stopped", ear.stop(ear.SPEAKING_PID, "sleep"), True)
        proc.wait(timeout=2)
        check("and has exited", proc.returncode is not None, True)
        check("the pid file is gone", os.path.exists(ear.SPEAKING_PID), False)
        check("a missing pid file stops nothing", ear.stop(ear.SPEAKING_PID, "sleep"), False)


def press_cases():
    print("  press")
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        real = ear.desktop.gather
        ear.desktop.gather = lambda: ([], "meeting")
        try:
            code, out = quiet(ear.verb_press, None)
        finally:
            ear.desktop.gather = real
        check("a meeting exits refused", code, ear.desktop.EXIT_REFUSED)
        check("and says why", out.get("refused"), "disarmed in a meeting")
        check("and tells him", any("disarmed" in n for n in NOTIFIED), True)
        check("and records nothing", os.path.exists(ear.RECORDER_PID), False)


class FakeEar(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        FakeEar.received.append((self.path, self.headers.get("Content-Type"), len(body)))
        payload = json.dumps({"text": "yes, go ahead", "seconds": 0.21}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


def release_cases():
    print("  release")
    with tempfile.TemporaryDirectory() as tmp:
        isolate(tmp)
        server = HTTPServer(("127.0.0.1", 0), FakeEar)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        ear.EAR_URL = f"http://127.0.0.1:{server.server_address[1]}"

        # `timeout ... sleep` stands in for `timeout ... pw-record`: the pid on
        # record is the wrapper either way, and the real stop() signals it.
        for seconds, label in ((0.2, "tap"), (1.5, "utterance")):
            recorder = subprocess.Popen(["timeout", "30", "sleep", "30"])
            with open(ear.RECORDER_PID, "w") as handle:
                handle.write(f"{recorder.pid}\n")
            write_wav(ear.CLIP, seconds)
            code, out = quiet(ear.verb_release, None)
            recorder.wait(timeout=2)
            if label == "tap":
                check("a tap is discarded", "discarded" in out, True)
                check("and never sent", FakeEar.received, [])
            else:
                check("an utterance is transcribed", out.get("text"), "yes, go ahead")
                check("it went to /hear as a wav", FakeEar.received[0][:2], ("/hear", "audio/wav"))
                check("the whole clip was sent", FakeEar.received[0][2], 44 + 2 * 16000 * 1.5)
                with open(ear.HEARD) as handle:
                    check("it is logged for the client", json.loads(handle.readline())["text"],
                          "yes, go ahead")
            check(f"the {label} clip is deleted", os.path.exists(ear.CLIP), False)

        code, out = quiet(ear.verb_release, None)
        check("a release with nothing recording is ignored", out.get("ignored"), "not listening")

        recorder = subprocess.Popen(["timeout", "30", "sleep", "30"])
        with open(ear.RECORDER_PID, "w") as handle:
            handle.write(f"{recorder.pid}\n")
        write_wav(ear.CLIP, 1.0)
        server.shutdown()
        server.server_close()
        code, out = quiet(ear.verb_release, None)
        recorder.wait(timeout=2)
        check("an unreachable ear exits in error", code, ear.desktop.EXIT_ERROR)
        check("and names the tunnel", out.get("tunnel"), ear.TUNNEL)


def main():
    policy_cases()
    process_cases()
    press_cases()
    release_cases()
    bad = sum(1 for _name, ok in RESULTS if not ok)
    print(f"\n  {len(RESULTS)} checks, {'ALL PASS' if not bad else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
