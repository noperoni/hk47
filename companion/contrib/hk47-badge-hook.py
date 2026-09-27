#!/usr/bin/env python3
"""Maintain the HK-47 companion's attention badge from Claude Code hook events.

The companion is a separate process and cannot see other Claude Code sessions,
so this script keeps the count for it: one flag file per session under
$XDG_RUNTIME_DIR/hk47/badge, which the companion counts once a second.

Three kinds of flag, one per way a session can be waiting on Master:

  <session>.question    the session has put a question through the
                        AskUserQuestion tool and the tool call is blocking on
                        the answer.
  <session>.permission  the session is stopped at a permission prompt.
  <session>.waiting     the session finished its turn and wants a prompt.

The three are mutually exclusive, so raising one clears the other two. A session
appears in exactly one column or in none of them, which is what makes the three
counts sum to "sessions currently waiting on Master" rather than to something
larger than the number of sessions open.

Errors are deliberately not counted. A failed tool call is the session's problem
to report in its own transcript; the badge answers "who is waiting for me", and
a failure that Claude is still working around is not waiting for anybody.

Notification is deliberately unregistered. It fires both for a permission prompt
and for an idle session, so it duplicates PermissionRequest and Stop with worse
timing and no way to tell the two apart except by parsing its message text.

Every fire is also appended to the overseer's event log (PERS-5), with the
session's account, cwd and the text Master would need to act on it: the last
reply on Stop, the questions on AskUserQuestion, the command on a permission
prompt. hk47-queue.py reads it; this script only writes it.

The runtime dir is tmpfs, so a reboot clears the state and no reaping is needed.
A dead session that ended without firing SessionEnd leaves one stale flag until
the next reboot, which is the price of having no reaper; it is not worth a
liveness check on every hook event.

Registered on both accounts' settings.json. PreToolUse and PostToolUse are
registered with matcher "AskUserQuestion" rather than "*", because only that one
tool blocks on Master.

Contract: hooks run on a timeout and their stdout on UserPromptSubmit is injected
into the prompt, so this script prints nothing and always exits 0. A badge that
is briefly wrong is a cosmetic fault; a hook that breaks a session is not.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

QUESTION = "question"
PERMISSION = "permission"
WAITING = "waiting"

ALL_FLAGS = (QUESTION, PERMISSION, WAITING)

# event name -> flag to raise, or None to clear the session entirely.
# Every raise clears the other two, so the states stay mutually exclusive.
EVENTS = {
    "PreToolUse": QUESTION,  # matcher AskUserQuestion: the tool is blocking now
    "PermissionRequest": PERMISSION,
    "Stop": WAITING,  # turn over, so anything it was blocked on is moot
    "PostToolUse": None,  # matcher AskUserQuestion: the answer came back
    "UserPromptSubmit": None,
    "SessionEnd": None,
}


BODY_CHARS = 900  # the same trim the Jev harness scores on


def queue_log():
    """PERS-5's event log. Persistent, not tmpfs, because the queue's labels
    have to outlive a reboot to be worth scoring."""
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "hk47" / "queue" / "events.jsonl"


def account_of(payload):
    """Which config dir the session runs under, read off its transcript path,
    because CLAUDE_CONFIG_DIR is not reliably exported to hooks."""
    for part in Path(payload.get("transcript_path") or "").parts:
        if part.startswith(".claude"):
            return part.lstrip(".")
    return "claude"


def question_text(tool_input):
    lines = []
    for q in (tool_input or {}).get("questions") or []:
        if isinstance(q, dict):
            labels = " | ".join(o.get("label", "") for o in q.get("options") or [] if isinstance(o, dict))
            lines.append(f"- {q.get('question', '')} [{labels}]")
    return "\n".join(lines)


def is_gate_matrix(tool_input):
    """The danger gate's approval question is recognised by its fixed option
    wording, which the gate itself matches on."""
    return any(o.get("label", "").startswith(("APPROVE: ", "REFUSE: "))
               for q in (tool_input or {}).get("questions") or [] if isinstance(q, dict)
               for o in q.get("options") or [] if isinstance(o, dict))


def record(payload, event, raise_flag):
    """One line per fire, for the queue. Text is trimmed and never parsed here."""
    text, matrix = "", False
    if event == "Stop":
        text = (payload.get("last_assistant_message") or "")[-BODY_CHARS:]
    elif event == "PreToolUse":
        text = question_text(payload.get("tool_input"))[:BODY_CHARS]
        matrix = is_gate_matrix(payload.get("tool_input"))
    elif event == "PermissionRequest":
        tool_input = payload.get("tool_input") or {}
        detail = tool_input.get("command") or tool_input.get("file_path") or ""
        text = f"{payload.get('tool_name', '')}: {detail}"[:BODY_CHARS]
    line = {"t": datetime.now().isoformat(timespec="seconds"), "session": payload.get("session_id", ""),
            "event": event, "flag": raise_flag or "clear", "account": account_of(payload),
            "cwd": payload.get("cwd", ""), "text": text, "matrix": matrix}
    path = queue_log()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(line) + "\n")


def badge_dir():
    """Kept in sync by hand with badge_dir() in src/overlay/badge.rs."""
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / "hk47" / "badge"


def session_key(raw):
    """A session id becomes a filename, so keep it to characters that cannot
    escape the directory or collide with the extension."""
    key = "".join(c for c in str(raw) if c.isalnum() or c in "-_")
    return key[:64]


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return

    event = payload.get("hook_event_name", "")
    if event not in EVENTS:
        return
    raise_flag = EVENTS[event]

    # Guard the matcher in code as well as in settings.json. The registration is
    # scoped to AskUserQuestion, but a wildcard entry copied in by hand would
    # otherwise raise the question flag on every tool call in the session.
    if event in ("PreToolUse", "PostToolUse"):
        if payload.get("tool_name") != "AskUserQuestion":
            return

    key = session_key(payload.get("session_id", ""))
    if not key:
        return

    directory = badge_dir()
    for flag in ALL_FLAGS:
        if flag == raise_flag:
            continue
        try:
            (directory / f"{key}.{flag}").unlink()
        except OSError:
            pass  # already gone, or the dir was never created

    if raise_flag:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / f"{key}.{raise_flag}").touch()
        except OSError:
            pass

    try:
        record(payload, event, raise_flag)
    except OSError:
        pass  # the queue losing a line must not cost the badge its flag


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never let the badge take a session down with it
    sys.exit(0)
