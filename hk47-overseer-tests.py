#!/usr/bin/env python3
"""Tests for hk47-overseer.py. No mouth, no ear, no desktop, no warehouse.

The policy functions are pure. The exchange runs for real against fake hands
and a fake clock, so every rule in the docstring is driven end to end: the
window, the ask, the answer, the summary, the meantime loop, the stopper.
"""

import importlib
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ov = importlib.import_module("hk47-overseer")

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok))
    print(f"    {'ok  ' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n            got  {got!r}\n            want {want!r}"))


POOL = ov.load_pool()
first = lambda seq: seq[0]   # deterministic pick


# --- pure -------------------------------------------------------------------


def policy_cases():
    print("  policy")
    check("an open window with no arrival is not closed", ov.window_closed(None, None, 99), False)
    check("5s after the only arrival: still open", ov.window_closed(0, 0, 5), False)
    check("10s of quiet closes it", ov.window_closed(0, 0, 10), True)
    check("an arrival at 8s restarts the quiet", ov.window_closed(0, 8, 12), False)
    check("the cap closes it at 60s regardless", ov.window_closed(0, 58, 60), True)

    for text, want in (("Yes.", "yes"), ("Go ahead.", "yes"), ("Now.", "yes"), ("Not now.", "no"),
                       ("After this one.", "no"), ("No.", "no"), ("What was that?", None), ("", None)):
        check(f"{text!r} is {want}", ov.answer(text), want)

    silence = {"text": "Thank you.", "segments": [{"no_speech_prob": 0.835, "avg_logprob": -0.795}]}
    check("a 'Thank you.' over silence is Whisper's", ov.hallucinated(silence), True)
    check("a quiet but real 'later' counts",
          ov.hallucinated({"text": "later", "segments": [{"no_speech_prob": 0.102, "avg_logprob": -0.86}]}), False)
    check("nothing kept is nothing said", ov.hallucinated({"text": "", "segments": []}), True)
    check("a record with no scores is taken at its word", ov.hallucinated({"text": "Thank you."}), False)

    for text, want in (("What's waiting?", "waiting"), ("Tell me more.", "more"), ("Next.", "next"),
                       ("What's next?", "next"), ("Done.", "done"), ("Later.", "later"), ("No more.", "later"),
                       ("Not yet.", "later"), ("Thank you.", None)):
        check(f"{text!r} is {want}", ov.command(text), want)

    check("a summary splits at each qualifier", ov.sentences("Statement: Done. Warning: It is v2.1 now. "),
          ["Statement: Done.", "Warning: It is v2.1 now."])

    check("a ramp ends exactly at the prior volume", ov.ramp([100, 200], 4),
          [[25, 50], [50, 100], [75, 150], [100, 200]])


def pool_cases():
    print("  phrase pool")
    for klass in ov.STOCK + ("q_design", "q_approval", "q_gate_matrix", "t_done", "t_failed", "summary"):
        check(f"{klass} has lines", bool(POOL.get(klass)), True)
    check("stock lines carry no placeholder",
          [t for k in ov.STOCK for t in POOL[k] if "{" in t], [])
    check("no line says master", [t for ts in POOL.values() for t in ts if "master" in t.lower()], [])
    one = [{"subject": "HK47", "category": "t_done"}]
    check("one project gets its class line", ov.compose(one, POOL, first),
          "Statement: HK47 has finished and awaits your inspection.")
    same = one + [{"subject": "HK47", "category": "q_design"}]
    check("two sessions of one project are one project", ov.compose(same, POOL, first),
          "Statement: HK47 has finished and awaits your inspection.")
    two = one + [{"subject": "Archon", "category": "q_design"}]
    check("two projects get one summary", ov.compose(two, POOL, first), "Query: Where do we start with HK47 and Archon?")
    check("counts are spoken as words", ov.compose(two, POOL, lambda s: s[1]),
          "Statement: two projects are waiting on you.")


# --- the exchange -----------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Events:
    def __init__(self):
        self.queue = []

    def push(self, session, flag="waiting"):
        self.queue.append({"session": session, "flag": flag})

    def read(self):
        out, self.queue = self.queue, []
        return out


class FakeHands:
    def __init__(self, table, ctx="ordinary"):
        self.table, self.ctx = table, ctx
        self.voice, self.mouth_up = True, True
        self.heard, self.played, self.log, self.notified, self.prepared = [], [], [], [], []
        self.on_play = None
        self.stop_at = None   # index into played at which a line is cut short

    def klass(self):
        return self.ctx

    def voice_on(self):
        return self.voice

    def items(self, ctx):
        return [{"session": s, "subject": subj, "category": cat, "stale": False, "text": f"{subj} said things"}
                for s, (subj, cat) in self.table.items()]

    def summarise(self, item):
        if item["subject"] == "broken":
            raise OSError("summariser exit 1")
        return f"Statement: {item['text']}. Warning: Nothing else."

    def dismiss(self, item):
        self.log.append(f"dismiss {item['session']}")
        self.table.pop(item["session"], None)

    def prepare(self, text):
        self.prepared.append(text)

    def render(self, text):
        if not self.mouth_up:
            raise OSError("connection refused")
        return text

    def quiet_peon(self):
        self.log.append("peon off")

    def restore_peon(self):
        self.log.append("peon on")

    def play(self, audio):
        self.played.append(audio)
        if self.on_play:
            self.on_play(audio)
        return len(self.played) - 1 != self.stop_at

    def mark_heard(self):
        pass

    def heard_since_mark(self):
        out, self.heard = self.heard, []
        return out

    def ear_recording(self):
        return False

    def hush(self):
        self.log.append("hush")

    def resume(self):
        self.log.append("resume")

    def notify(self, line):
        self.notified.append(line)


TABLE = {"s-hk47": ("HK47", "t_done"), "s-archon": ("Archon", "q_design"), "s-lib": ("Library", "t_failed")}


def rig(ctx="ordinary", table=None):
    clock, events = Clock(), Events()
    hands = FakeHands(dict(table or {"s-hk47": TABLE["s-hk47"]}), ctx)
    return ov.Overseer(hands, events, POOL, clock=clock, sleep=clock.sleep, pick=first,
                       spawn=lambda fn: fn()), hands, events, clock


def run_until(overseer, clock, limit=120):
    while clock.now < limit:
        outcome = overseer.tick()
        if outcome:
            return outcome
        clock.sleep(0.5)
    return None


def exchange_cases():
    print("  exchange")
    o, hands, events, clock = rig()
    events.push("s-hk47")
    hands.heard = [{"text": "Yes.", "rms_dbfs": -25.0}]
    check("a yes is spoken", run_until(o, clock), "spoken")
    check("closed after 10s of quiet, then 15s awaiting a command", round(clock.now), 25)
    check("the ask, then the class line, and no opener", hands.played,
          ["Query: Can I bother you?", "Statement: HK47 has finished and awaits your inspection."])
    check("the line began rendering on arrival", hands.prepared[0],
          "Statement: HK47 has finished and awaits your inspection.")
    check("peon-ping paused for the whole exchange, hushed after the ask",
          hands.log, ["peon off", "hush", "resume", "peon on"])

    o, hands, events, clock = rig()
    events.push("s-hk47")
    o.tick()
    clock.sleep(8)
    events.push("s-hk47")
    o.tick()
    clock.sleep(5)
    check("an arrival restarts the quiet", o.tick(), None)

    o, hands, events, clock = rig()
    events.push("s-hk47")
    o.tick()
    clock.sleep(4)
    events.push("s-hk47", "clear")
    check("an item answered in its session is never spoken", run_until(o, clock, 30), None)
    check("and nothing was said", hands.played, [])

    for ctx in ("meeting", "focus"):
        o, hands, events, clock = rig(ctx)
        events.push("s-hk47")
        check(f"{ctx}: deferred", run_until(o, clock), "deferred")
        check(f"{ctx}: not a word", hands.played, [])
        check(f"{ctx}: not carried either", o.carried, set())

    o, hands, events, clock = rig()
    hands.voice = False
    events.push("s-hk47")
    check("voice off: deferred", run_until(o, clock), "deferred")

    o, hands, events, clock = rig("game")
    events.push("s-hk47")
    hands.heard = [{"text": "Now.", "rms_dbfs": -30.0}]
    run_until(o, clock)
    check("a game gets the game ask", hands.played[0], "Query: A word, when you have finished killing things?")

    o, hands, events, clock = rig()
    hands.heard = [{"text": "Yes, no.", "segments": [{"no_speech_prob": 0.89}]}, {"text": "Yes.", "rms_dbfs": -25.0}]
    events.push("s-hk47")
    check("a no over silence is skipped, the yes counts", run_until(o, clock), "spoken")


def refusal_cases():
    print("  refusal and carry")
    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    check("silence declines", run_until(o, clock), "declined")
    check("after waiting the full 15s", round(clock.now), 25)
    check("only the ask was said", hands.played, ["Query: Can I bother you?"])
    check("nothing was hushed, and peon-ping is back", hands.log, ["peon off", "peon on"])
    check("the batch is carried", o.carried, {"s-hk47"})

    hands.played.clear()
    clock.sleep(30)
    check("a carried batch alone is not asked again", o.tick(), None)
    events.push("s-archon")
    hands.heard = [{"text": "Go ahead.", "rms_dbfs": -25.0}]
    check("the next arrival asks again", run_until(o, clock, 200), "spoken")
    check("covering the old item and the new", hands.played[-1], "Query: Where do we start with HK47 and Archon?")
    check("and the carry is spent", o.carried, set())

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    hands.heard = [{"text": "Not now.", "rms_dbfs": -25.0}]
    check("a spoken no declines", run_until(o, clock), "declined")
    events.push("s-hk47", "clear")
    o.tick()
    check("an item answered while carried is dropped", o.carried, set())


def meantime_cases():
    print("  meantime and stoppers")
    o, hands, events, clock = rig(table=TABLE)
    hands.heard = [{"text": "Yes.", "rms_dbfs": -25.0}]
    events.push("s-hk47")

    def arrive(audio):
        if audio.startswith("Statement: HK47"):
            events.push("s-lib")
    hands.on_play = arrive
    check("spoken", run_until(o, clock), "spoken")
    check("what arrived meanwhile follows a meantime line", hands.played[-2:],
          ["Observation: While we were busy, others joined the queue.", "Observation: Library has failed and I did not do it."])
    check("one hush, one resume around all of it", hands.log, ["peon off", "hush", "resume", "peon on"])
    check("and it does not open a second window", o.pending, {})

    o, hands, events, clock = rig()
    hands.heard = [{"text": "Yes.", "rms_dbfs": -25.0}]
    hands.stop_at = 1   # the ear's key pressed during the class line
    events.push("s-hk47", "question")
    events.push("s-archon")
    check("a barge-in ends the exchange", run_until(o, clock), "interrupted")
    check("nothing is said after it", len(hands.played), 2)
    check("and still resumes", hands.log, ["peon off", "hush", "resume", "peon on"])

    o, hands, events, clock = rig()
    hands.heard = [{"text": "Yes.", "rms_dbfs": -25.0}]
    hands.stop_at = 0   # the key pressed over the question, to answer it
    events.push("s-hk47")
    check("a press over the question still hears the answer", run_until(o, clock), "spoken")

    o, hands, events, clock = rig()
    hands.mouth_up = False
    events.push("s-hk47")
    check("no mouth: mute", run_until(o, clock), "mute")
    check("said in writing instead", len(hands.notified), 1)


def grammar_cases():
    print("  queue grammar")

    def say(o, hands, clock, *said):
        hands.heard = [{"text": "Yes.", "rms_dbfs": -25.0}]
        replies = list(said)

        def feed(_audio):
            if replies:
                reply = replies.pop(0)
                hands.heard.append(reply if isinstance(reply, dict) else {"text": reply, "rms_dbfs": -25.0})
        hands.on_play = feed
        return run_until(o, clock, 400)

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    events.push("s-archon")
    check("tell me more condenses the current item", say(o, hands, clock, None, "Tell me more."), "spoken")
    check("from its recorded text, a sentence at a time", hands.played[-2:],
          ["Statement: HK47 said things.", "Warning: Nothing else."])
    check("every sentence queued on the mouth before the first plays", hands.prepared[-2:],
          ["Statement: HK47 said things.", "Warning: Nothing else."])

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    events.push("s-archon")
    say(o, hands, clock, None, "Next.", "Next.")
    check("next walks the batch, then says it is the last", hands.played[2:],
          ["Query: Will you take a question from Archon?", "Statement: Nothing else waits on you."])

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    events.push("s-archon")
    say(o, hands, clock, None, "Done.")
    check("done dismisses the current item", hands.log[2], "dismiss s-hk47")
    check("acknowledges it, then names the next", hands.played[2:],
          ["Statement: Dismissed.", "Query: Will you take a question from Archon?"])

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    say(o, hands, clock, None, "What's waiting?")
    check("what's waiting covers the whole queue, not the batch", hands.played[-1],
          "Query: Where do we start with HK47 and Archon and Library?")

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    events.push("s-archon")
    check("later declines", say(o, hands, clock, None, "Next.", "Later."), "declined")
    check("carrying only what was not yet passed", o.carried, {"s-archon"})

    o, hands, events, clock = rig(table={"s-x": ("broken", "t_done")})
    events.push("s-x")
    check("a failed summary is said in writing", say(o, hands, clock, None, "Tell me more."), "spoken")
    check("and the exchange carries on", len(hands.notified), 1)

    o, hands, events, clock = rig(table=TABLE)
    events.push("s-hk47")
    say(o, hands, clock, None, {"text": "Next.", "segments": [{"no_speech_prob": 0.89}]})
    check("a command heard over silence is not a command", hands.played[-1],
          "Statement: HK47 has finished and awaits your inspection.")


def voice_cases():
    print("  voice flag")
    with tempfile.TemporaryDirectory() as tmp:
        ov.VOICE_OFF = os.path.join(tmp, "voice-off")
        ov.desktop.STATE_DIR = tmp
        ov.ear.SPEAKING_PID = os.path.join(tmp, "speaking.pid")
        ov.desktop.run = lambda argv, timeout=5: None   # no bar refresh from a test
        state = lambda v: ov.verb_voice(type("A", (), {"state": v})())
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            state("off")
            check("off writes the flag", os.path.exists(ov.VOICE_OFF), True)
            state("toggle")
            check("toggle turns it back on", os.path.exists(ov.VOICE_OFF), False)
            state("toggle")
            check("and off again", os.path.exists(ov.VOICE_OFF), True)
            state("on")
            check("on clears it", os.path.exists(ov.VOICE_OFF), False)


def peon_cases():
    print("  peon-ping pause")
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        mine, masters = Path(tmp, "personal"), Path(tmp, "work")
        mine.mkdir()
        masters.mkdir()
        (masters / ".paused").touch()   # Master muted this one himself
        ov.PEON_INSTALLS = [mine, masters, Path(tmp, "absent")]
        ov.PEON_RECORD = os.path.join(tmp, "peon-paused.json")
        ov.desktop.STATE_DIR = tmp
        hands = ov.Hands.__new__(ov.Hands)
        hands.quiet_peon()
        check("an audible install is paused", (mine / ".paused").exists(), True)
        check("a missing install is not created", Path(tmp, "absent").exists(), False)
        hands.restore_peon()
        check("and unpaused after", (mine / ".paused").exists(), False)
        check("Master's own mute survives the exchange", (masters / ".paused").exists(), True)
        check("the record is gone", os.path.exists(ov.PEON_RECORD), False)


def main():
    policy_cases()
    pool_cases()
    exchange_cases()
    refusal_cases()
    meantime_cases()
    grammar_cases()
    voice_cases()
    peon_cases()
    bad = sum(1 for _name, ok in RESULTS if not ok)
    print(f"\n  {len(RESULTS)} checks, {'ALL PASS' if not bad else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
