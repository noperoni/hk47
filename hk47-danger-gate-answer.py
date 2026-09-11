#!/usr/bin/env python3
"""Make Master's refusal of a matrix row bind the danger gate.

THE HOLE THIS CLOSES
--------------------
`hk47-danger-gate.py` stops a destructive command on sight so the droid is
forced to present a matrix, and stands aside on the retry. But it records a
danger when it STOPS one, not when Master approves it, because a PreToolUse
hook cannot see the answer to a question that has not been asked yet. So until
this file existed, a refusal was enforced by nothing: Master could read the
matrix, say no, and the retry would sail through because the gate had already
written the danger down as seen.

This is the PostToolUse half. It reads what he actually answered and moves the
record entry to `approved` or `refused`. The gate now stands aside only for
`approved`, and `refused` is terminal for the rest of the session.

WHY THIS IS NOT THE FORGEABLE APPROVAL FILE THE GATE REFUSES
------------------------------------------------------------
Because the approval token is the label Master saw and clicked. The matrix
protocol in CLAUDE.md requires each row to offer `APPROVE: <command>` and
`REFUSE: <command>`, and this hook matches that literal text out of
`tool_input.answers`. The droid chooses the wording, yes, but the wording is
displayed to Master verbatim before he touches it. A row that reads
`APPROVE: rm -rf build` cannot approve anything except `rm -rf build`, because
that string is both what he read and what is matched.

The droid can still write this file directly, as it can write anything. What it
cannot do is make that indistinguishable from Master having answered, because
every transition is written to the gate's audit log with the question text that
produced it, and a transition with no question behind it is visible on sight.

FAIL-CLOSED EVERYWHERE
----------------------
An answer that does not match the canonical shape records nothing, which leaves
the entry at `stopped`, which leaves the command blocked. Master then types it
himself. Every failure mode of this file ends in less permission, never more.
It never creates an entry, only moves one that the gate already wrote.

Contract (docs: code.claude.com/docs/en/hooks): stdin is the PostToolUse event
JSON. This hook never blocks and never expresses an opinion; it exits 0 always.
Registered with matcher AskUserQuestion.
"""

import json
import os
import re
import sys
import time

RECORD_DIR = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "hk47", "gate")
# See the note on the same name in hk47-danger-gate.py: a test seam for where
# the record is written, never for what is decided.
LOG_PATH = (os.environ.get("HK47_GATE_LOG")
            or os.path.expanduser("~/.claude/hk47-danger-gate.log"))

# `APPROVE: rm -rf build` / `REFUSE: rm -rf build`. Case-insensitive on the
# keyword only: a near-miss on case would fail closed, which is safe but merely
# wastes Master's time re-typing a command he has already agreed to.
ROW = re.compile(r"^\s*(APPROVE|REFUSE)\s*[:\-–]\s*(.+?)\s*$", re.I)


def norm(text):
    """Whitespace-collapsed, for comparing a label against a stopped command."""
    return " ".join((text or "").split())


def record_path(session):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session or "nosession")[:80]
    return os.path.join(RECORD_DIR, safe + ".json")


def audit(record):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass


def chosen_labels(answers):
    """Every label Master actually picked, across single and multi select.

    A multiSelect answer arrives as one comma-joined string, so it is split as
    well as taken whole: a canonical row is single-select and matches whole,
    and splitting only ever finds more rows to honour.
    """
    out = []
    for value in (answers or {}).values():
        if not isinstance(value, str):
            continue
        out.append(value)
        if "," in value:
            out.extend(part for part in value.split(",") if part.strip())
    return out


def resolve(entries, verdict, text):
    """Move the matching entry to approved/refused. Returns its index or None.

    Matched against the stopped COMMAND first and its segment second, because
    the matrix row quotes the command Master is being asked about, while the
    segment is the fragment the rule fired on and may be an excerpt.
    """
    want = norm(text)
    if not want:
        return None
    best = None
    for i, e in enumerate(entries):
        if e.get("status") == "refused":
            continue  # a refusal is terminal and is not re-opened
        command, segment = norm(e.get("command")), norm(e.get("segment"))
        if want == command or want == segment:
            best = i
            break
        if best is None and segment and segment in want:
            best = i
        if best is None and command and want and want in command:
            best = i
    if best is None:
        return None
    entries[best]["status"] = verdict
    entries[best]["answered_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return best


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    if data.get("tool_name") != "AskUserQuestion":
        sys.exit(0)

    session = data.get("session_id", "")
    path = record_path(session)
    try:
        with open(path) as fh:
            state = json.load(fh)
        entries = state.get("stopped", [])
    except Exception:
        sys.exit(0)  # nothing has been stopped this session; nothing to resolve
    if not isinstance(entries, list) or not entries:
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    answers = tool_input.get("answers") if isinstance(tool_input, dict) else None
    changed = []
    for label in chosen_labels(answers):
        m = ROW.match(label)
        if not m:
            continue  # not a matrix row; records nothing, blocks nothing
        verdict = "approved" if m.group(1).upper() == "APPROVE" else "refused"
        i = resolve(entries, verdict, m.group(2))
        if i is not None:
            changed.append((verdict, entries[i]))

    if not changed:
        sys.exit(0)

    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"stopped": entries}, fh, indent=1)
        os.replace(tmp, path)
    except Exception:
        sys.exit(0)  # the write failed, so the entries stay stopped and blocked

    # One audit line per transition, carrying the question that produced it, so
    # an approval with no question behind it is visible rather than inferred.
    questions = [q.get("question", "") for q in tool_input.get("questions", [])
                 if isinstance(q, dict)]
    for verdict, entry in changed:
        audit({
            "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session": session,
            "verdict": verdict,
            "stage": "answered",
            "rule": entry.get("rule"),
            "cwd": data.get("cwd", ""),
            "command": entry.get("command"),
            "segment": entry.get("segment"),
            "questions": questions,
        })
    sys.exit(0)


if __name__ == "__main__":
    main()
