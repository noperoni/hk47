#!/usr/bin/env python3
"""The ear's desk half, PERS-11: push-to-talk into the 3090's Whisper.

Master ruled push-to-talk on 2026-09-28, over a wake-word stack that needed a
spotter, a confirmation stage, echo cancellation and end-of-speech detection
to do what one held key does: mark where his turn begins and ends. A wake word
can be added in front of this later; it is an upgrade, not a foundation.

    hk47-ear.py press      start listening (bound to the key going down)
    hk47-ear.py release    stop, transcribe, report (bound to the key coming up)

Hyprland runs each as its own process, so everything between the two lives in
$XDG_RUNTIME_DIR/hk47: the recorder's pid and the clip. Every verb prints JSON.

WHAT IT GUARDS
--------------
  * The recording is named `hk47-listen`, which hk47-desktop.py already counts
    as the droid's own capture, so its ear is never mistaken for a meeting.
  * In the meeting class the key is disarmed. Anything said while holding it
    would reach the call through Master's own open mic, and the droid must not
    answer aloud into it. The refusal arrives as a notification, which the
    meeting policy permits.
  * Pressing the key is the barge-in: whatever writes speaking.pid (the mouth
    client, PERS-5) is stopped before recording starts.
  * A tap shorter than MIN_SECONDS is an accident, not an utterance, and is
    discarded rather than sent to Whisper to hallucinate from.

Transcripts go to heard.jsonl in the runtime dir, wiped at reboot, for the
PERS-5 client to consume. Nothing Master says is kept on disk past that.

The ear is reached through `ssh -N -L 3902:127.0.0.1:3902 warehouse`.
"""

import argparse
import importlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
desktop = importlib.import_module("hk47-desktop")

STATE_DIR = desktop.STATE_DIR
RECORDER_PID = os.path.join(STATE_DIR, "ear-recorder.pid")
CLIP = os.path.join(STATE_DIR, "ear-clip.wav")
SPEAKING_PID = os.path.join(STATE_DIR, "speaking.pid")
HEARD = os.path.join(STATE_DIR, "heard.jsonl")

EAR_URL = os.environ.get("HK47_EAR_URL", "http://127.0.0.1:3902")
NODE_NAME = "hk47-listen"   # must match DEFAULT_OWN_CAPTURE_NODES in hk47-desktop.py
MIN_SECONDS = 0.4
# A missed release left the mic open for a minute on 2026-09-28. Whatever a bind
# misses, the recorder stops itself here; the ear server's body cap is 4 MB.
MAX_SECONDS = 60
RECORDER = "timeout"   # the pid on record is the timeout wrapping pw-record
TUNNEL = "ssh -N -L 3902:127.0.0.1:3902 warehouse"


# --- policy, pure -----------------------------------------------------------


def press_verdict(klass, listening):
    """What a key press may do. Returns (verdict, reason)."""
    if listening:
        return "ignore", "already listening"
    if klass == "meeting":
        return "refuse", "disarmed in a meeting"
    return "listen", ""


def usable(seconds):
    return seconds >= MIN_SECONDS


# --- processes ---------------------------------------------------------------


def alive(pid, comm):
    """A pid is ours only while it still names the program we started: pids are
    recycled, and signalling a stranger's process is not a barge-in."""
    try:
        with open(f"/proc/{pid}/comm") as handle:
            return handle.read().strip() == comm
    except OSError:
        return False


def read_pid(path):
    try:
        with open(path) as handle:
            return int(handle.read().strip())
    except (OSError, ValueError):
        return None


def stop(path, comm, wait=2.0):
    """SIGTERM the process named in path, if it is still ours, and wait for it."""
    pid = read_pid(path)
    desktop.unlink(path)
    if not pid or not alive(pid, comm):
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait
    while alive(pid, comm) and time.monotonic() < deadline:
        time.sleep(0.02)
    return True


def clip_seconds(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / w.getframerate()
    except (OSError, EOFError, wave.Error):
        return 0.0


def notify(line):
    argv = ["notify-send", "-a", "HK-47", "-u", "low"]
    icon = desktop.find_icon()
    if icon:
        argv += ["-i", icon]
    desktop.run(argv + ["HK-47", desktop.sanitise(line)])


# --- verbs ------------------------------------------------------------------


def verb_press(_args):
    _caps, klass = desktop.gather()
    pid = read_pid(RECORDER_PID)
    verdict, why = press_verdict(klass, bool(pid) and alive(pid, RECORDER))
    if verdict == "refuse":
        notify(f"Statement: My ear is {why}.")
        return desktop.emit({"class": klass, "refused": why}, desktop.EXIT_REFUSED)
    if verdict == "ignore":
        return desktop.emit({"class": klass, "ignored": why})

    interrupted = stop(SPEAKING_PID, os.environ.get("HK47_PLAYER_COMM", "pw-play"))
    os.makedirs(STATE_DIR, exist_ok=True)
    desktop.unlink(CLIP)
    # `timeout` forwards release's SIGTERM to pw-record, which then finishes the
    # WAV header, and on its own ends a recording whose release never came.
    proc = subprocess.Popen(
        ["timeout", str(MAX_SECONDS), "pw-record", "--rate", "16000", "--channels", "1",
         "-P", f'{{ node.name = "{NODE_NAME}" }}', CLIP],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)
    with open(RECORDER_PID, "w") as handle:
        handle.write(f"{proc.pid}\n")
    return desktop.emit({"class": klass, "listening": True, "interrupted": interrupted})


def verb_release(_args):
    if not stop(RECORDER_PID, RECORDER):
        return desktop.emit({"ignored": "not listening"})

    seconds = clip_seconds(CLIP)
    if not usable(seconds):
        desktop.unlink(CLIP)
        return desktop.emit({"discarded": f"{seconds:.2f}s is a tap, not an utterance"})

    with open(CLIP, "rb") as handle:
        audio = handle.read()
    desktop.unlink(CLIP)
    request = urllib.request.Request(f"{EAR_URL}/hear", data=audio,
                                     headers={"Content-Type": "audio/wav"})
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            heard = json.load(reply)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        notify(f"Statement: I cannot reach my ear on the warehouse. Open the tunnel: {TUNNEL}")
        return desktop.emit({"error": f"ear unreachable: {exc}", "tunnel": TUNNEL}, desktop.EXIT_ERROR)

    record = {"t": datetime.now().isoformat(timespec="seconds"), "text": heard.get("text", ""),
              "clip_seconds": round(seconds, 2), "whisper_seconds": heard.get("seconds")}
    with open(HEARD, "a") as handle:
        handle.write(json.dumps(record) + "\n")
    # ponytail: the notification is the only consumer until the PERS-5 client
    # reads heard.jsonl; drop it then, or it doubles every exchange.
    notify(f"Observation: I heard \"{record['text']}\"")
    return desktop.emit(record)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hk47-ear.py", description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="verb", required=True)
    subs.add_parser("press", help="start listening")
    subs.add_parser("release", help="stop, transcribe, report")
    args = parser.parse_args(argv)
    return {"press": verb_press, "release": verb_release}[args.verb](args)


if __name__ == "__main__":
    sys.exit(main())
