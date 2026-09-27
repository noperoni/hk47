#!/usr/bin/env python3
"""Tests for hk47-queue.py. Drives ordering, categories and the reply refusals
from fixtures, with the roster and every subprocess stubbed out, so no session
is listed, stopped or resumed. The roster shapes are the ones `claude agents
--json` printed on 2026-09-27: interactive sessions with state null and no
sessionId, background ones with both.
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib

queue = importlib.import_module("hk47-queue")

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok))
    print(f"    {'ok  ' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n            got  {got!r}\n            want {want!r}"))


NOW = datetime.now()


def at(**delta):
    return (NOW - timedelta(**delta)).isoformat(timespec="seconds")


def event(session, name, flag, t, matrix=False, text=""):
    return {"t": t, "session": session, "event": name, "flag": flag, "account": "claude-personal",
            "cwd": "/nonexistent/" + session[:4], "text": text, "matrix": matrix}


ROSTER = {
    "term0001": {"id": "term0001", "kind": "interactive", "state": None, "name": "hk47-14"},
    "term0002": {"id": "term0002", "kind": "interactive", "state": None, "name": "echo-a2"},
    "term0003": {"id": "term0003", "kind": "interactive", "state": None, "name": "gate"},
    "term0004": {"id": "term0004", "kind": "interactive", "state": None, "name": "done"},
    "bg000001-full": {"id": "bg000001", "sessionId": "bg000001-full", "kind": "background", "state": "blocked",
                      "cwd": "/nonexistent/bg", "name": "old block", "startedAt": (NOW - timedelta(days=90)).timestamp() * 1000},
    "bg000002-full": {"id": "bg000002", "sessionId": "bg000002-full", "kind": "background", "state": "failed",
                      "cwd": "/nonexistent/bg", "name": "fresh failure", "startedAt": (NOW - timedelta(minutes=5)).timestamp() * 1000},
    "bg000003-full": {"id": "bg000003", "sessionId": "bg000003-full", "kind": "background", "state": "working",
                      "cwd": "/nonexistent/bg", "name": "busy", "startedAt": NOW.timestamp() * 1000},
}

EVENTS = [
    event("term0001-aaaa", "Stop", "waiting", at(minutes=30), text="finished"),
    event("term0002-bbbb", "PermissionRequest", "permission", at(minutes=10), text="Bash: git push"),
    event("term0003-cccc", "PreToolUse", "question", at(minutes=1), matrix=True),
    event("term0004-dddd", "Stop", "waiting", at(minutes=50)),
    event("term0004-dddd", "UserPromptSubmit", "clear", at(minutes=40)),
    event("gone0005-eeee", "Stop", "waiting", at(minutes=2)),  # not in the roster: ended
    event("bg000003-full", "Stop", "waiting", at(minutes=3)),
]


def setup(tmp):
    queue.EVENTS = Path(tmp) / "events.jsonl"
    queue.LABELS = Path(tmp) / "labels.jsonl"
    queue.EVENTS.write_text("".join(json.dumps(e) + "\n" for e in EVENTS))
    queue.roster = lambda: ROSTER
    queue.transcript = lambda session: (None, None)


def by_id(items):
    return {i["id"]: i for i in items}


def category_cases(items):
    print("  categories")
    got = by_id(items)
    check("gate matrix is urgency 2", (got["term0003"]["category"], got["term0003"]["urgency"]), ("q_gate_matrix", 2))
    check("permission is an approval, urgency 1", (got["term0002"]["category"], got["term0002"]["urgency"]), ("q_approval", 1))
    check("turn end is finished work, urgency 0", (got["term0001"]["category"], got["term0001"]["urgency"]), ("t_done", 0))
    check("failed background session is urgency 2", got["bg000002"]["category"], "t_failed")
    check("old blocked background session is stale", got["bg000001"]["stale"], True)
    check("cleared session leaves the queue", "term0004" in got, False)
    check("session missing from the roster leaves the queue", "gone0005" in got, False)


def order_cases(items):
    print("  order")
    desk = [i["id"] for i in queue.ordered(items, "ordinary")]
    check("desk: urgency first, arrival within it, stale last", desk,
          ["bg000002", "term0003", "term0002", "term0001", "bg000003", "bg000001"])
    call = [i["id"] for i in queue.ordered(items, "meeting")]
    check("call: arrival only, stale still last", call,
          ["term0001", "term0002", "bg000002", "bg000003", "term0003", "bg000001"])


class Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def refused(fn, args, q):
    try:
        fn(args, q, "ordinary")
    except SystemExit as e:
        return str(e.code)
    return None


def reply_cases(items):
    print("  reply refusals")
    calls = []
    queue.subprocess.run = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("ran"))
    q = queue.ordered(items, "ordinary")
    check("refuses a terminal session", "terminal session" in (refused(queue.cmd_reply, Args(item="term0001", text="x"), q) or ""), True)
    check("refuses the gate matrix", "yours alone" in (refused(queue.cmd_reply, Args(item="term0003", text="x"), q) or ""), True)
    check("refuses a working session", "still working" in (refused(queue.cmd_reply, Args(item="bg000003", text="x"), q) or ""), True)
    check("no refused reply ran a command", calls, [])


def label_cases(items):
    print("  labels")
    q = queue.ordered(items, "ordinary")
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        queue.cmd_pick(Args(item="3"), q, "ordinary")
        queue.cmd_done(Args(item="term0001"), q, "ordinary")
    label = json.loads(queue.LABELS.read_text().splitlines()[-1])
    check("pick logs its rank", label["rank"], 3)
    check("pick logs what the queue ranked above it", [a["session"] for a in label["above"]],
          ["bg000002-full", "term0003-cccc"])
    check("done hides the item", "term0001" in by_id(queue.items()), False)


def main():
    print("hk47-queue.py")
    with tempfile.TemporaryDirectory() as tmp:
        setup(tmp)
        items = queue.items()
        category_cases(items)
        order_cases(items)
        label_cases(items)
        reply_cases(items)

    bad = sum(1 for _name, ok in RESULTS if not ok)
    print(f"\n  {len(RESULTS)} checks, {'ALL PASS' if not bad else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
