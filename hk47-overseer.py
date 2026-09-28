#!/usr/bin/env python3
"""The overseer's voice, PERS-5 Phase A: the queue, spoken, with ears.

hk47-queue.py knows who waits on Master. This daemon watches the same event log,
lets a burst of arrivals settle, asks aloud whether it may speak, listens for the
answer through the ear (PERS-11), and then says what is waiting, with the
desktop hushed around it. No model: every line comes from hk47-mouth-lines.tsv.

    hk47-overseer.py run                     the daemon (hk47-overseer.service)
    hk47-overseer.py voice on|off|toggle|status
    hk47-overseer.py cache                   render the stock lines ahead of time

THE EXCHANGE, AS MASTER RULED IT (2026-09-27, 2026-09-28)
---------------------------------------------------------
  1. An item arrives: a session raised a question or a permission prompt. A
     finished turn never arrives (Master, 2026-09-28): it is spoken only when
     he asks what is waiting. Each arrival restarts a QUIET_SECONDS timer, capped at
     CAP_SECONDS from the first, so a burst is one exchange and not five.
  2. The window closes. Voice off, meeting or focus: nothing is said, ever.
     Written mode owns those items and they stay in `hk47-queue.py list`.
  3. Otherwise the droid asks (a game gets its own asks) and waits
     ANSWER_SECONDS for a spoken yes or no, longer while the ear is recording.
  4. Yes: hush, then the item's class line, or one summary when two or more
     projects wait; no opener, the question already opened it. Anything that arrived meanwhile is spoken after a
     "meantime" line, in a loop, until nothing new is left. Then resume, fading
     back to the prior volume over FADE_SECONDS.
  5. Any speech but a no is a yes (Master, 2026-09-28): he pressed the key to
     answer, so "all right, what's up" consents as well as "yes" does. A no or
     silence: the batch is carried, unspoken, and asked about again together
     with the next arrival. The sprite's badge still counts
     it. Wiring a refusal into Archon or the sprite is open (Master, 2026-09-28).
  6. After the droid has spoken, it listens ANSWER_SECONDS at a time for the
     queue grammar, and only then (Master, 2026-09-28: only in an exchange):
       what's waiting   the whole queue, not only this batch
       tell me more     the current item: its session's last message, condensed
                        by Opus into a spoken sentence or two (Master's ruling)
       next             the next item's line
       done             dismiss the current item, as `hk47-queue.py done`
       later            stop; what was not dismissed is carried and asked again
     Anything else is answered with the choices, and it listens again.
     Silence ends it with nothing carried: he heard it and chose not to act.

Every line is rendered once and cached on the desk. An item's line starts
rendering the moment it arrives, so it is ready when the window closes rather
than 19s after Master's yes, which was the first trial's lag. peon-ping is
paused from the question to the end of the exchange, so its clips never talk
over Master, and only the installs this paused are unpaused. Pressing the ear's key while the droid speaks stops it (the ear kills
whatever speaking.pid names), and the exchange ends there. `voice off` is the
stopper: it silences the droid mid-sentence and keeps it silent. Its switch is
the hk47.voice bar button (companion/contrib/omarchy-voice), which shows the
state as well as changing it; Master chose it over a key on 2026-09-28.

The mouth is reached through the same tunnel as the ear (hk47-voice-tunnel).
"""

import argparse
import hashlib
import importlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
desktop = importlib.import_module("hk47-desktop")
ear = importlib.import_module("hk47-ear")
hkq = importlib.import_module("hk47-queue")

LINES = HERE / "hk47-mouth-lines.tsv"
CACHE = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "hk47" / "mouth"
VOICE_OFF = os.path.join(desktop.STATE_DIR, "voice-off")
SPEAKING_WAV = os.path.join(desktop.STATE_DIR, "speaking.wav")
MOUTH_URL = os.environ.get("HK47_MOUTH_URL", "http://127.0.0.1:3901")
# peon-ping's own pause marker, the interface its hook checks before playing.
# Its clips talked over Master's answer in the first trial (2026-09-28).
PEON_INSTALLS = [Path.home() / f".{account}" / "hooks" / "peon-ping" for account in hkq.ACCOUNTS]
PEON_RECORD = os.path.join(desktop.STATE_DIR, "peon-paused.json")

QUIET_SECONDS = 10
CAP_SECONDS = 60
ANSWER_SECONDS = 15
FADE_SECONDS = 3
FADE_STEPS = 15
POLL_SECONDS = 0.5
# One render, no re-rolls (Master, 2026-09-28): a roll is 8-12s and the gate took
# up to four, 54.7s for two sentences. A bad render is heard and fixed at the
# source (Jev, voice training), not paid for on every line. The gate still judges
# the one roll, and its verdict goes to the journal as the record of how often.
ROLLS = 1

STOCK = ("ask", "ask_game", "meantime", "dismissed", "last", "unheard")
# The badge hook's raise flags that open an exchange. Not "waiting", a finished
# turn: Master ruled 2026-09-28 that those are spoken only when he asks, so they
# wait in writing and on the badge, and "what's waiting" still names them.
ARRIVING = ("question", "permission")

# Measured 2026-09-28 on Master's mic: three silent presses of 1.8-3.1s, each
# heard as "Thank you.", scored no_speech_prob 0.835-0.892; eight spoken presses
# scored 0.075-0.237. Loudness failed first: speech fell to -49.5 dBFS, below the
# old -47 gate. Whisper's own filter misses these because it also demands an
# avg_logprob under -1.0, and the inventions scored -0.79 to -0.93.
SILENT_ABOVE = 0.5

NO = {"no", "nope", "nah", "not", "later", "after", "wait", "busy", "don't", "dont", "stop", "quiet",
      "hush", "negative", "negatory"}
# The queue grammar, checked in this order, so "no more" is later and "what's
# next" is next. Words and not phrases: Whisper punctuates and pads freely.
GRAMMAR = (("later", {"later", "enough", "stop", "quiet", "hush", "no", "not", "nope", "bye"}),
           ("done", {"done", "dismiss", "dismissed", "finished", "handled", "clear"}),
           ("next", {"next", "skip", "another"}),
           ("more", {"more", "detail", "details", "explain", "elaborate"}),
           ("waiting", {"waiting", "queue", "what's", "whats", "list"}))
SUMMARISE_SECONDS = 60
SUMMARISE = ("You condense a coding session's last message into one or two short spoken sentences for "
             "text-to-speech, in HK-47's voice, each starting with a declared qualifier such as Statement: "
             "or Warning:. Say where the work stands and what it needs from him. No markdown, no lists, no "
             "paths or code, no commas at all since the voice stretches every one, never the word master. "
             "Avoid words spelled one way and said two ways (live, read, lead, close, wind, tear, present): the "
             "voice guesses the wrong one, so say running or active rather than live. "
             "Output only the sentences.")
NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


# --- policy, pure -----------------------------------------------------------


def window_closed(first, last, now):
    return first is not None and (now - last >= QUIET_SECONDS or now - first >= CAP_SECONDS)


def answer(text):
    """"no" for any refusal word, "yes" for any other speech, None for none.
    A no wins a tie, so "not now" is a no and "now" alone is a yes."""
    words = set(re.findall(r"[a-z']+", (text or "").lower()))
    if words & NO:
        return "no"
    return "yes" if words else None


def command(text):
    words = set(re.findall(r"[a-z']+", (text or "").lower()))
    return next((verb for verb, vocab in GRAMMAR if words & vocab), None)


def sentences(text):
    """Split at a full stop followed by a capital, which is where every qualifier starts."""
    return [part for part in re.split(r"(?<=[.?!])\s+(?=[A-Z])", text.strip()) if part]


def hallucinated(record):
    """Whisper speaking over silence: every segment it kept is probably no speech.
    A record without scores, from an ear older than the scores, is taken at its word."""
    segments = record.get("segments")
    if segments is None:
        return False
    return all(seg.get("no_speech_prob", 0) > SILENT_ABOVE for seg in segments)


def load_pool(path=LINES):
    pool = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        klass, text = line.split("\t", 1)
        pool.setdefault(klass, []).append(text)
    return pool


def compose(items, pool, pick=random.choice):
    """What is said about a set of queue items: the class line for one project,
    one summary for several (Master, 2026-09-28: always one summary)."""
    subjects = list(dict.fromkeys(i["subject"] for i in items))
    if len(subjects) == 1:
        return pick(pool[items[0]["category"]]).format(project=subjects[0])
    return pick(pool["summary"]).format(list=" and ".join(subjects),
                                        n=NUMBERS.get(len(subjects), str(len(subjects))))


def ramp(prior, steps=FADE_STEPS):
    """Channel volumes from silence back to prior, one list per step."""
    return [[round(v * k / steps) for v in prior] for k in range(1, steps + 1)]


# --- the event log ----------------------------------------------------------


class Tail:
    """New lines of a jsonl file since the last read, from the end at start: the
    daemon speaks about what happens while it runs, not about June."""

    def __init__(self, path, from_end=True):
        self.path = Path(path)
        self.offset = self.path.stat().st_size if from_end and self.path.exists() else 0

    def read(self):
        try:
            size = self.path.stat().st_size
        except OSError:
            return []
        if size < self.offset:
            self.offset = 0   # truncated or recreated (heard.jsonl is wiped at reboot)
        with open(self.path) as handle:
            handle.seek(self.offset)
            chunk = handle.read()
        complete = chunk.rsplit("\n", 1)[0] if "\n" in chunk else ""
        self.offset += len(complete.encode()) + (1 if complete else 0)
        out = []
        for line in complete.splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


# --- the hands the overseer drives -------------------------------------------


class Hands:
    """Everything that touches the desktop, the mouth or the ear. Tests replace it."""

    def __init__(self):
        self.heard = Tail(ear.HEARD)
        # One render at a time, as the mouth serves them, and a line asked for
        # twice (prepared on arrival, then wanted at the ask) is rendered once.
        self.mouth = ThreadPoolExecutor(max_workers=1)
        self.inflight = {}
        self.lock = threading.Lock()

    def klass(self):
        return desktop.gather()[1]

    def voice_on(self):
        return not os.path.exists(VOICE_OFF)

    def items(self, ctx):
        return hkq.ordered(hkq.items(), ctx)

    def prepare(self, text):
        """Start rendering a line now, so it is ready when it is wanted."""
        with self.lock:
            if text not in self.inflight:
                self.inflight[text] = self.mouth.submit(self._render, text)
            return self.inflight[text]

    def render(self, text):
        future = self.prepare(text)
        try:
            return future.result()
        finally:
            with self.lock:
                if self.inflight.get(text) is future and future.done():
                    del self.inflight[text]   # a failure may be retried; a success is on disk

    def _render(self, text):
        # ponytail: every line is cached, stock and project alike, with no
        # eviction. Lines are short and projects few; prune by mtime if it grows.
        key = CACHE / f"{hashlib.sha1(text.encode()).hexdigest()[:16]}.wav"
        if key.exists():
            return key.read_bytes()
        request = urllib.request.Request(f"{MOUTH_URL}/say", data=json.dumps({"text": text, "rolls": ROLLS}).encode(),
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as reply:
            audio = reply.read()
            print(f"mouth: gate {reply.headers.get('X-HK47-Gate')} in {reply.headers.get('X-HK47-Seconds')}s: {text}",
                  flush=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        key.write_bytes(audio)
        return audio

    def quiet_peon(self):
        """Pause peon-ping for the conversation, recording which installs this
        paused, so Master's own mute is never lifted by us."""
        paused = read_peon_record()
        for install in PEON_INSTALLS:
            marker = install / ".paused"
            if install.is_dir() and not marker.exists():
                marker.touch()
                paused.append(str(marker))
        desktop.write_json(PEON_RECORD, {"paused": paused})

    def restore_peon(self):
        for marker in read_peon_record():
            desktop.unlink(marker)
        desktop.unlink(PEON_RECORD)

    def play(self, audio):
        """True when the line played to its end, False when it was stopped."""
        os.makedirs(desktop.STATE_DIR, exist_ok=True)
        with open(SPEAKING_WAV, "wb") as handle:
            handle.write(audio)
        proc = subprocess.Popen(["pw-play", SPEAKING_WAV], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(ear.SPEAKING_PID, "w") as handle:
            handle.write(f"{proc.pid}\n")
        code = proc.wait()
        desktop.unlink(ear.SPEAKING_PID)
        desktop.unlink(SPEAKING_WAV)
        return code == 0

    def mark_heard(self):
        self.heard.read()   # anything said before the question is not its answer

    def heard_since_mark(self):
        return self.heard.read()

    def ear_recording(self):
        pid = ear.read_pid(ear.RECORDER_PID)
        return bool(pid) and ear.alive(pid, ear.RECORDER)

    def hush(self):
        desktop.run([sys.executable, str(desktop.__file__), "hush"], timeout=20)

    def resume(self):
        """desktop resume, with every stream it puts back faded up from silence."""
        entries = desktop.read_json(desktop.HUSH_RECORD, {}).get("entries", [])
        streams = {s["index"]: s for s in desktop.pactl_list("sink-inputs")}
        prior = {}
        for entry in entries:
            for index, stream in streams.items():
                binary = (stream.get("properties") or {}).get("application.process.binary", "")
                if (entry.get("kind") == "sink" and index == entry.get("index")) or \
                        (entry.get("kind") == "mpris" and desktop.same_app(binary, entry.get("process"))):
                    prior[index] = [ch.get("value", 0) for ch in (stream.get("volume") or {}).values()]
        for index, values in prior.items():
            set_volume(index, [0] * len(values))
        desktop.run([sys.executable, str(desktop.__file__), "resume"], timeout=20)
        # ponytail: a daemon killed mid-fade leaves a stream quiet; the next
        # resume never sees it because its record is gone. Rare, and audible.
        steps = {index: ramp(values) for index, values in prior.items()}
        for k in range(FADE_STEPS if steps else 0):
            time.sleep(FADE_SECONDS / FADE_STEPS)
            for index, levels in steps.items():
                set_volume(index, levels[k])

    def notify(self, line):
        ear.notify(line)

    def summarise(self, item):
        """The item's recorded text, condensed by Opus. No hooks, so the call is
        never itself a queue arrival; no tools; nothing persisted."""
        account = item.get("account") or hkq.ACCOUNTS[0]
        env = os.environ | {"CLAUDE_CONFIG_DIR": str(hkq.config_dir(account))}
        proc = subprocess.run(["claude", "-p", "--model", "opus", "--tools", "", "--no-session-persistence",
                               "--settings", '{"disableAllHooks": true}', "--system-prompt", SUMMARISE],
                              input=f"Project {item['subject']}, its session's last message:\n\n{item['text']}",
                              capture_output=True, text=True, timeout=SUMMARISE_SECONDS, env=env,
                              cwd=desktop.STATE_DIR if os.path.isdir(desktop.STATE_DIR) else None)
        said = " ".join(proc.stdout.split())
        if proc.returncode or not said:
            raise OSError(f"summariser exit {proc.returncode}: {proc.stderr.strip()[:200]}")
        return said

    def dismiss(self, item):
        hkq.append(hkq.EVENTS, {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "session": item["session"],
                                "event": "Dismiss", "flag": "clear", "account": item.get("account", ""),
                                "cwd": item.get("cwd", ""), "text": "", "matrix": False})


def read_peon_record():
    return list(desktop.read_json(PEON_RECORD, {}).get("paused", []))


def set_volume(index, values):
    desktop.run(["pactl", "set-sink-input-volume", str(index)] + [str(int(v)) for v in values])


# --- the overseer -----------------------------------------------------------


class Overseer:
    def __init__(self, hands, events, pool, clock=time.monotonic, sleep=time.sleep, pick=random.choice,
                 spawn=lambda fn: threading.Thread(target=fn, daemon=True).start()):
        self.hands, self.events, self.pool = hands, events, pool
        self.clock, self.sleep, self.pick, self.spawn = clock, sleep, pick, spawn
        self.pending = {}      # session -> arrival time, the open window
        self.carried = set()   # sessions Master declined to hear, asked again with the next arrival
        self.chosen = {}       # (session, category) -> its line, chosen and rendering since arrival
        self.first = self.last = None

    def take_events(self):
        """Apply new events. Returns the sessions that newly arrived."""
        arrived = []
        for ev in self.events.read():
            session = ev.get("session")
            if not session:
                continue
            if ev.get("flag") in ARRIVING:
                now = self.clock()
                self.pending[session] = now
                self.first = self.first if self.first is not None else now
                self.last = now
                arrived.append(session)
            else:
                self.pending.pop(session, None)
                self.carried.discard(session)
        if not self.pending:
            self.first = self.last = None
        return arrived

    def tick(self):
        for session in self.take_events():
            self.spawn(lambda s=session: self.prepare(s))
        if not window_closed(self.first, self.last, self.clock()):
            return None
        batch = set(self.pending) | self.carried
        self.pending.clear()
        self.first = self.last = None
        outcome, unheard = self.exchange(batch)
        self.carried = unheard if outcome == "declined" else set()
        return outcome

    def prepare(self, session):
        """On arrival, choose the item's line and start rendering it, so a single
        item is ready by the time the window closes (first trial: the render
        began at the close and was still running after Master's yes)."""
        for item in self.waiting({session}, "ordinary"):
            key = (session, item["category"])
            if key not in self.chosen:
                self.chosen[key] = compose([item], self.pool, self.pick)
            self.hands.prepare(self.chosen[key])

    def line_for(self, items):
        subjects = {i["subject"] for i in items}
        if len(subjects) == 1:
            chosen = self.chosen.get((items[0]["session"], items[0]["category"]))
            if chosen:
                return chosen
        return compose(items, self.pool, self.pick)

    def waiting(self, sessions, ctx):
        return [i for i in self.hands.items(ctx) if i["session"] in sessions and not i["stale"]]

    def exchange(self, sessions):
        """One conversation about a batch. Returns (outcome, sessions not heard)."""
        ctx = self.hands.klass()
        if not self.hands.voice_on() or not desktop.POLICY[ctx]["speak"]:
            return "deferred", set()
        items = self.waiting(sessions, ctx)
        if not items:
            return "empty", set()

        body = self.line_for(items)
        self.hands.prepare(body)
        try:
            ask_audio = self.hands.render(self.pick(self.pool["ask_game" if ctx == "game" else "ask"]))
        except (urllib.error.URLError, OSError):
            self.hands.notify("Statement: My mouth is unreachable on the warehouse, so the queue waits in writing.")
            return "mute", set()

        self.hands.quiet_peon()
        try:
            return self.converse(items, body, ask_audio, ctx)
        finally:
            self.hands.restore_peon()
            for item in items:
                self.chosen.pop((item["session"], item["category"]), None)

    def converse(self, items, body, ask_audio, ctx):
        heard = {i["session"] for i in items}
        self.hands.mark_heard()
        # A press that cuts the question short is Master answering it, so a
        # stopped ask still listens. Only the stopper ends it here.
        self.hands.play(ask_audio)
        if not self.hands.voice_on():
            return "deferred", set()
        if self.listen() != "yes":
            return "declined", heard

        try:
            body_audio = self.hands.render(body)
        except (urllib.error.URLError, OSError):
            self.hands.notify("Statement: My mouth failed mid-render, so the queue waits in writing.")
            return "mute", set()
        self.hands.hush()
        try:
            # No opener: the question opened the exchange, and an opener beside
            # the class line said one fact twice in the first trial.
            if not self.say(body_audio):
                return "interrupted", set()
            spoken = list(items)
            while self.hands.voice_on():
                new = set(self.take_events()) - heard
                self.pending = {s: t for s, t in self.pending.items() if s not in new}
                if not self.pending:
                    self.first = self.last = None
                fresh = self.waiting(new, ctx)
                if not fresh:
                    break
                heard |= {i["session"] for i in fresh}
                spoken += fresh
                try:
                    lines = [self.hands.render(self.pick(self.pool["meantime"])),
                             self.hands.render(compose(fresh, self.pool, self.pick))]
                except (urllib.error.URLError, OSError):
                    break
                if not all(self.say(line) for line in lines):
                    return "interrupted", set()
            return self.follow(spoken, ctx)
        finally:
            self.hands.resume()

    def follow(self, items, ctx):
        """The queue grammar, after the droid has spoken. `items` is the order
        they were spoken in; the current item is the first not yet passed."""
        cursor = 0
        while self.hands.voice_on():
            verb = self.listen(lambda text: command(text) or "unheard")
            if verb is None:
                return "spoken", set()
            if verb == "later":
                return "declined", {i["session"] for i in items[cursor:]}
            if verb == "waiting":
                items, cursor = self.waiting({i["session"] for i in self.hands.items(ctx)}, ctx), 0
                lines = [compose(items, self.pool, self.pick)] if items else [self.pick(self.pool["last"])]
            elif verb == "unheard":
                lines = [self.pick(self.pool["unheard"])]
            elif cursor >= len(items):
                lines = [self.pick(self.pool["last"])]
            elif verb == "more":
                try:
                    lines = sentences(self.hands.summarise(items[cursor]))
                except (OSError, subprocess.SubprocessError) as exc:
                    self.hands.notify(f"Statement: I could not condense {items[cursor]['subject']}: {exc}")
                    continue
            else:
                lines = []
                if verb == "done":
                    self.hands.dismiss(items.pop(cursor))
                    lines.append(self.pick(self.pool["dismissed"]))
                else:
                    cursor += 1
                lines.append(compose([items[cursor]], self.pool, self.pick) if cursor < len(items)
                             else self.pick(self.pool["last"]))
            # Streamed a sentence at a time: all queued on the mouth now, each
            # played as it lands, so the first is heard while the rest render.
            for line in lines:
                self.hands.prepare(line)
            for line in lines:
                try:
                    audio = self.hands.render(line)
                except (urllib.error.URLError, OSError):
                    self.hands.notify("Statement: My mouth failed mid-render, so the queue waits in writing.")
                    return "mute", set()
                if not self.say(audio):
                    return "interrupted", set()
        return "spoken", set()

    def say(self, audio):
        return self.hands.voice_on() and self.hands.play(audio)

    def listen(self, parse=answer):
        deadline = self.clock() + ANSWER_SECONDS
        while self.clock() < deadline or self.hands.ear_recording():
            for record in self.hands.heard_since_mark():
                if hallucinated(record):
                    continue
                verdict = parse(record.get("text"))
                if verdict:
                    return verdict
            self.sleep(0.2)
        return None


# --- verbs ------------------------------------------------------------------


def verb_run(_args):
    hands = Hands()
    hands.restore_peon()   # a daemon killed mid-exchange left peon-ping paused
    overseer = Overseer(hands, Tail(hkq.EVENTS), load_pool())
    print("overseer: listening to the queue", flush=True)
    while True:
        outcome = overseer.tick()
        if outcome:
            print(f"overseer: {outcome}", flush=True)
        time.sleep(POLL_SECONDS)


def verb_voice(args):
    if args.state == "on":
        desktop.unlink(VOICE_OFF)
    elif args.state == "off" or (args.state == "toggle" and not os.path.exists(VOICE_OFF)):
        os.makedirs(desktop.STATE_DIR, exist_ok=True)
        Path(VOICE_OFF).touch()
        ear.stop(ear.SPEAKING_PID, "pw-play")   # the stopper: silence now, not after the sentence
    elif args.state == "toggle":
        desktop.unlink(VOICE_OFF)
    if args.state != "status":
        desktop.run(["omarchy-shell", "hk47.voice", "refresh"])   # the bar button, if installed
    return desktop.emit({"voice": not os.path.exists(VOICE_OFF)})


def verb_cache(_args):
    hands, pool, done, failed = Hands(), load_pool(), [], []
    for klass in STOCK:
        for text in pool.get(klass, []):
            try:
                hands.render(text)
                done.append(text)
            except (urllib.error.URLError, OSError) as exc:
                failed.append({"text": text, "error": str(exc)})
    return desktop.emit({"cached": len(done), "failed": failed, "dir": str(CACHE)},
                        desktop.EXIT_ERROR if failed else desktop.EXIT_OK)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hk47-overseer.py", description=__doc__.splitlines()[0])
    subs = parser.add_subparsers(dest="verb", required=True)
    subs.add_parser("run", help="the daemon")
    subs.add_parser("voice", help="the loop stopper").add_argument("state", choices=("on", "off", "toggle", "status"))
    subs.add_parser("cache", help="render the stock lines ahead of time")
    args = parser.parse_args(argv)
    return {"run": verb_run, "voice": verb_voice, "cache": verb_cache}[args.verb](args)


if __name__ == "__main__":
    sys.exit(main())
