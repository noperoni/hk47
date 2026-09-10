#!/usr/bin/env python3
"""Maintain the HK-47 companion's attention badge from Claude Code hook events.

The companion is a separate process and cannot see other Claude Code sessions,
so this script keeps the count for it: one flag file per session under
$XDG_RUNTIME_DIR/hk47/badge, which the companion counts once a second.

Two kinds of flag, because they clear differently:

  <session>.blocked  the session is stopped at a permission prompt, or has
                     notified that it wants input. Cleared by the tool call that
                     follows an approval, by the end of the turn, or by your next
                     prompt -- whichever arrives first.
  <session>.error    a tool call in that session failed. Cleared only when you
                     next type into that session, because answering the prompt is
                     what counts as having seen it. An error you never returned to
                     stays on the badge.

The runtime dir is tmpfs, so a reboot clears the state and no reaping is needed.
A dead session that ended without firing SessionEnd leaves one stale flag until
the next reboot, which is the price of having no reaper; it is not worth a
liveness check on every hook event.

Registered on both accounts' settings.json. Deliberately registered with matcher
"*" on PostToolUseFailure, unlike peon-ping's own entry, which matches Bash only
and would therefore miss every non-shell failure.

Contract: hooks run on a timeout and their stdout on UserPromptSubmit is injected
into the prompt, so this script prints nothing and always exits 0. A badge that
is briefly wrong is a cosmetic fault; a hook that breaks a session is not.
"""

import json
import os
import sys
from pathlib import Path

BLOCKED = "blocked"
ERROR = "error"

# event name -> (flags to raise, flags to clear)
EVENTS = {
    "PermissionRequest": ((BLOCKED,), ()),
    "Notification": ((BLOCKED,), ()),
    "PostToolUseFailure": ((ERROR,), ()),
    "PostToolUse": ((), (BLOCKED,)),
    "Stop": ((), (BLOCKED,)),
    "UserPromptSubmit": ((), (BLOCKED, ERROR)),
    "SessionEnd": ((), (BLOCKED, ERROR)),
}


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
    raise_flags, clear_flags = EVENTS.get(event, ((), ()))
    if not raise_flags and not clear_flags:
        return

    key = session_key(payload.get("session_id", ""))
    if not key:
        return

    directory = badge_dir()
    for flag in clear_flags:
        try:
            (directory / f"{key}.{flag}").unlink()
        except OSError:
            pass  # already gone, or the dir was never created

    if raise_flags:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        for flag in raise_flags:
            try:
                (directory / f"{key}.{flag}").touch()
            except OSError:
                pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never let the badge take a session down with it
    sys.exit(0)
