#!/usr/bin/env python3
"""PostToolUse backstop: blank any keyring secret out of Bash output (PERS-33).

The danger gate stops a command that would print a credential. This catches the
one it could not foresee, a verbose curl or a stack trace quoting a header,
before the model reads it and the transcript keeps it for good.

Values are matched exactly, taken from the keyring under `service hk47`, so the
redaction knows what a secret IS rather than guessing at its shape. A locked
keyring yields nothing, and output then passes through untouched.

Measured 2026-10-03: `updatedToolOutput` must be the structured Bash response
(stdout, stderr, ...). A bare string is silently ignored.
"""
import json
import sys

MIN_LEN = 8  # shorter values (logins, "sa") would blank ordinary words
FIELDS = ("stdout", "stderr")


def secrets():
    # secretstorage, because `secret-tool search` splits a value and its name
    # across stdout and stderr, which cannot be paired reliably.
    # One GetSecrets over a plain session: fetching item by item cost 620ms per
    # Bash call for 56 secrets, batched it is 80ms. Plain is safe on the
    # per-user session bus, which never leaves this machine.
    try:
        import secretstorage
        from secretstorage.defines import SS_PATH, SS_PREFIX
        from secretstorage.util import DBusAddressWrapper
        conn = secretstorage.dbus_init()
        coll = secretstorage.get_default_collection(conn)
        if coll.is_locked():
            return {}
        service = DBusAddressWrapper(SS_PATH, SS_PREFIX + "Service", conn)
        _, session = service.call("OpenSession", "sv", "plain", ("s", ""))
        items = list(coll.search_items({"service": "hk47"}))
        (values,) = service.call("GetSecrets", "aoo", [i.item_path for i in items], session)
        found = {}
        for item in items:
            name = item.get_attributes().get("key", "")
            value = bytes(values.get(item.item_path, (0, 0, b""))[2]).decode("utf-8", "replace")
            # ponytail: logins are not secret and are often short or common words
            if not (name.startswith("USER_") or name.endswith("_USER")) and len(value) >= MIN_LEN:
                found[name] = value
        return found
    except Exception:
        return {}


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    resp = data.get("tool_response")
    if data.get("tool_name") != "Bash" or not isinstance(resp, dict):
        return
    text = {f: resp.get(f) for f in FIELDS if isinstance(resp.get(f), str) and resp.get(f)}
    if not text:
        return
    changed = False
    for name, value in secrets().items():
        for f in text:
            if value in text[f]:
                text[f] = text[f].replace(value, f"<redacted {name}>")
                changed = True
    if changed:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "updatedToolOutput": dict(resp, **text),
        }}))


if __name__ == "__main__":
    main()
