#!/usr/bin/env python3
"""The overseer's queue, PERS-5 phase 1: no model, no voice, no conversation.

The queue is the product and the voice is an interface. This is the queue:
who is waiting on Master, for what, in what order. The mouth (PERS-2) and the
ears (PERS-11) attach to it later; until then written mode is the only mode,
and this CLI plus the companion's badge are the only surface.

    hk47-queue.py list [--json]      the waiting sessions, in policy order
    hk47-queue.py show <id|n>        the full text of one item
    hk47-queue.py pick <id|n>        take an item; logged as a label
    hk47-queue.py done <id|n>        dismiss an item until the session speaks again
    hk47-queue.py reply <id|n> TEXT  answer a detached background session

SOURCES
-------
Events come from the badge hook (companion/contrib/hk47-badge-hook.py), which
appends one line per fire to ~/.local/state/hk47/queue/events.jsonl. The
roster comes from `claude agents --json` on each account: it names every live
session, and gives background sessions a state, but reports interactive ones
as state null, so whether a terminal session waits on Master is known only
from its hooks. A session missing from the roster has ended and is dropped.

ORDER, AS MASTER RULED IT
-------------------------
At his desk: urgency, then arrival. In a call or in focus urgency does not
exist and everything runs in arrival order. In every mode an item untouched for
seven days sorts below every live one, so June's blocked sessions stay in the
queue, uncapped, without ever heading it.

Urgency is POLICY_TABLE from hk47-jev-eval.py, applied by rule. A rule sees
the event's kind and not its text, so a question is scored as a design question
and a turn end as finished work: effectively always-0 outside the gate. That
gap is measured, not guessed: `pick` logs which item Master took and which the
queue ranked above it, and the Jev harness can score against those labels later
without anything leaving the machine in the meantime (Master's ruling, 2026-09-27).

DRIVING
-------
`reply` works on background sessions only. A live background session, even an
idle one, holds a process, and `claude --bg --resume` then starts a copy under a
new id rather than continuing it (measured 2026-09-27), so reply stops the
session first and resumes it under its own id. It refuses a session that is
still working, and it refuses the danger gate's APPROVE/REFUSE matrix outright:
the matrix belongs to Master and never to whatever is driving (PERS-5, PERS-7).
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "hk47" / "queue"
EVENTS = STATE / "events.jsonl"
LABELS = STATE / "labels.jsonl"
ACCOUNTS = ["claude-personal", "claude-work", "claude"]
STALE_AFTER = timedelta(days=7)
DESKTOP = HERE / "hk47-desktop.py"

# The ruled policy's urgency per category, from hk47-jev-eval.py POLICY_TABLE.
URGENCY = {"q_gate_matrix": 2, "q_approval": 1, "q_design": 0, "t_done": 0, "t_failed": 2}
CATEGORY = {"PreToolUse": "q_design", "PermissionRequest": "q_approval", "Stop": "t_done"}


def read_jsonl(path):
    if not path.exists():
        return []
    out = []
    with open(path) as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # a torn line from a hook killed mid-write
    return out


def append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def config_dir(account):
    return Path.home() / f".{account}"


def roster():
    """Every live session on every account, keyed by full session id where the
    roster gives one and by short id otherwise."""
    seen = {}
    for account in ACCOUNTS:
        if not config_dir(account).is_dir():
            continue
        env = os.environ | {"CLAUDE_CONFIG_DIR": str(config_dir(account))}
        try:
            proc = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30, env=env)
            entries = json.loads(proc.stdout or "[]")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
        for e in entries:
            seen.setdefault(e.get("sessionId") or e.get("id"), e)
    return seen


def in_roster(session, live):
    return session in live or session[:8] in live or any(k and session.startswith(k) for k in live)


def roster_entry(session, live):
    return live.get(session) or live.get(session[:8]) or next(
        (v for k, v in live.items() if k and session.startswith(k)), {})


def transcript(session):
    for account in ACCOUNTS:
        hits = list((config_dir(account) / "projects").glob(f"*/{session}.jsonl"))
        if hits:
            return account, hits[0]
    return None, None


def subject(cwd, fallback=""):
    """PROJECT.json name, falling back to the directory, as ruled 2026-09-13."""
    try:
        return json.loads((Path(cwd) / ".claude" / "PROJECT.json").read_text())["project"]["name"]
    except (OSError, ValueError, KeyError, TypeError):
        return Path(cwd).name if cwd else fallback


def context():
    try:
        proc = subprocess.run([sys.executable, str(DESKTOP), "context"], capture_output=True, text=True, timeout=10)
        return json.loads(proc.stdout).get("class", "ordinary")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "ordinary"  # no context means no reason to suppress urgency


def items():
    latest = {}
    for ev in read_jsonl(EVENTS):
        if ev.get("session"):
            latest[ev["session"]] = ev
    live = roster()
    now = datetime.now()
    out, covered = [], set()

    for session, ev in latest.items():
        if ev.get("flag") == "clear" or not in_roster(session, live):
            continue
        entry = roster_entry(session, live)
        category = "q_gate_matrix" if ev.get("matrix") else CATEGORY.get(ev.get("event"), "t_done")
        t = datetime.fromisoformat(ev["t"])
        out.append({"session": session, "id": session[:8], "subject": subject(ev.get("cwd"), entry.get("name", "")),
                    "name": entry.get("name", ""), "kind": entry.get("kind", "interactive"),
                    "state": entry.get("state"), "account": ev.get("account", ""), "cwd": ev.get("cwd", ""),
                    "category": category, "urgency": URGENCY[category], "arrived": ev["t"],
                    "stale": now - t > STALE_AFTER, "text": ev.get("text", "")})
        covered.add(session)

    # Background sessions that were blocked or failed before the hook logged
    # anything: the roster knows they wait, the event log does not.
    for key, entry in live.items():
        session = entry.get("sessionId") or key
        if entry.get("kind") != "background" or entry.get("state") not in ("blocked", "failed") or session in covered:
            continue
        if latest.get(session, {}).get("flag") == "clear":
            continue  # dismissed with `done`
        account, path = transcript(session)
        touched = datetime.fromtimestamp(path.stat().st_mtime) if path else datetime.fromtimestamp(
            entry.get("startedAt", 0) / 1000)
        category = "t_failed" if entry["state"] == "failed" else "q_design"
        out.append({"session": session, "id": session[:8], "subject": subject(entry.get("cwd"), entry.get("name", "")),
                    "name": entry.get("name", ""), "kind": "background", "state": entry["state"],
                    "account": account or "", "cwd": entry.get("cwd", ""), "category": category,
                    "urgency": URGENCY[category], "arrived": touched.isoformat(timespec="seconds"),
                    "stale": now - touched > STALE_AFTER, "text": f"{entry['state']}: {entry.get('name', '')}"})
    return out


def ordered(queue, ctx):
    if ctx == "ordinary":
        return sorted(queue, key=lambda i: (i["stale"], -i["urgency"], i["arrived"]))
    return sorted(queue, key=lambda i: (i["stale"], i["arrived"]))


def resolve(handle, queue):
    if handle.isdigit() and 1 <= int(handle) <= len(queue):
        return queue[int(handle) - 1]
    hits = [i for i in queue if i["session"].startswith(handle)]
    if len(hits) != 1:
        sys.exit(f"no single queue item matches {handle!r}")
    return hits[0]


def age(iso):
    delta = datetime.now() - datetime.fromisoformat(iso)
    if delta.days:
        return f"{delta.days}d"
    return f"{delta.seconds // 3600}h" if delta.seconds >= 3600 else f"{delta.seconds // 60}m"


def one_line(text, width):
    flat = " ".join(text.split())
    return flat if len(flat) <= width else flat[: width - 1] + "…"


def cmd_list(args, queue, ctx):
    if args.json:
        print(json.dumps({"context": ctx, "items": queue}, indent=2))
        return
    print(f"context: {ctx}" + ("" if ctx == "ordinary" else " (arrival order, urgency suspended)"))
    divided = False
    for n, i in enumerate(queue, 1):
        if i["stale"] and not divided:
            print("--- untouched 7 days or more ---")
            divided = True
        where = "bg" if i["kind"] == "background" else "term"
        print(f"{n:>3} {i['id']} {age(i['arrived']):>4} u{i['urgency']} {i['category']:<13} {where:<4} "
              f"{one_line(i['subject'], 24):<24} {one_line(i['text'], 70)}")
    if not queue:
        print("nothing waits on you")


def cmd_show(args, queue, ctx):
    i = resolve(args.item, queue)
    print(f"{i['subject']}  ({i['name'] or i['id']}, {i['kind']}, {i['account']})\n{i['cwd']}\n"
          f"{i['category']}, urgency {i['urgency']}, arrived {i['arrived']}\n\n{i['text']}")


def cmd_pick(args, queue, ctx):
    i = resolve(args.item, queue)
    rank = queue.index(i)
    append(LABELS, {"t": datetime.now().isoformat(timespec="seconds"), "context": ctx, "picked": i["session"],
                    "picked_arrived": i["arrived"], "rank": rank + 1,
                    "above": [{"session": a["session"], "arrived": a["arrived"], "category": a["category"]}
                              for a in queue[:rank]]})
    cmd_show(args, queue, ctx)
    if i["kind"] == "background":
        print(f"\nclaude attach {i['id']}    or    hk47-queue.py reply {i['id']} \"...\"")
    else:
        print(f"\nterminal session {i['name'] or i['id']}")


def cmd_done(args, queue, ctx):
    i = resolve(args.item, queue)
    append(EVENTS, {"t": datetime.now().isoformat(timespec="seconds"), "session": i["session"], "event": "Dismiss",
                    "flag": "clear", "account": i["account"], "cwd": i["cwd"], "text": "", "matrix": False})
    print(f"dismissed {i['id']} {i['subject']}")


def cmd_reply(args, queue, ctx):
    i = resolve(args.item, queue)
    if i["category"] == "q_gate_matrix":
        sys.exit(f"{i['id']} is waiting on the danger gate's matrix, which is yours alone; answer it in the session")
    if i["kind"] != "background":
        sys.exit(f"{i['id']} is a terminal session; reply there, the queue will not type into it")
    if i["state"] == "working":
        sys.exit(f"{i['id']} is still working; replying now would stop it mid-task")
    account = i["account"] or transcript(i["session"])[0]
    if not account:
        sys.exit(f"cannot tell which account {i['id']} belongs to")
    env = os.environ | {"CLAUDE_CONFIG_DIR": str(config_dir(account))}
    cwd = i["cwd"] if Path(i["cwd"]).is_dir() else None
    subprocess.run(["claude", "stop", i["id"]], env=env, capture_output=True, text=True, timeout=30)
    proc = subprocess.run(["claude", "--bg", "--resume", i["session"], args.text], env=env, cwd=cwd,
                          capture_output=True, text=True, timeout=60)
    print((proc.stdout + proc.stderr).strip())
    append(LABELS, {"t": datetime.now().isoformat(timespec="seconds"), "context": ctx, "replied": i["session"],
                    "category": i["category"]})
    if proc.returncode:
        sys.exit(proc.returncode)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    p = sub.add_parser("list")
    p.add_argument("--json", action="store_true")
    for verb in ("show", "pick", "done"):
        sub.add_parser(verb).add_argument("item")
    p = sub.add_parser("reply")
    p.add_argument("item")
    p.add_argument("text")
    args = parser.parse_args()

    ctx = context()
    queue = ordered(items(), ctx)
    {"list": cmd_list, "show": cmd_show, "pick": cmd_pick, "done": cmd_done, "reply": cmd_reply}[args.verb](
        args, queue, ctx)


if __name__ == "__main__":
    main()
