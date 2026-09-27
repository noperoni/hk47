#!/usr/bin/env python3
"""PERS-20: does hosted Jev beat the incumbent at PERS-5 overseer triage?

Run in order:

  build    draw 200 real events from the personal accounts' transcripts and
           danger-gate logs, stratified by kind, and give each a stated context;
           danger events are sorted into a category mechanically
  pending  print the questions and turn ends still unsorted; the droid sorts
           them into jev-eval/data/categories.jsonl
  label    Master's blind spot-check of 20 events, one keystroke per answer
  score    hosted Jev and the incumbent answer the same three questions, both
           scored against Master's ruled POLICY_TABLE, then the spot-check
           reports how far that policy sits from his own judgement

Master chose ruling per category over labelling 200 events one by one. The
truth is therefore his stated policy, not his per-event judgement, which is
exactly what the spot-check exists to measure.

THE INCUMBENT is what decides today, not a strawman. Urgency is hk47_rank in
install-peon-seams.py (danger 2, anything Master caused 1, my chatter 0), and
channel and interrupt are POLICY in hk47-desktop.py (speak in ordinary, stay
silent in a meeting or in focus, never drop). Its answers are one-hot.

CONTEXT was never recorded, so it is assigned, a third each, and stated in the
event text. Master, Jev and the incumbent all read the same statement.

PRIVACY: events are Master's own session text. They live in jev-eval/data/,
which is gitignored because this repo is public, and `score` sends them to
TypeSafe through OpenRouter. The work account is excluded by Master's ruling
of 2026-09-27: employer content never leaves the machine.
"""

import argparse
import glob
import hashlib
import json
import os
import random
import statistics
import subprocess
import sys
import termios
import time
import tty
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "jev-eval" / "data"
CORPUS = DATA / "corpus.jsonl"
LABELS = DATA / "labels.jsonl"  # Master's blind spot-check
CATEGORIES = DATA / "categories.jsonl"  # the droid's sort of questions and turn ends

# Personal accounts only. ~/.claude-work is excluded on purpose, see above.
ACCOUNTS = [Path.home() / ".claude-personal", Path.home() / ".claude"]

# Pinned to the dated build: `typesafe/jev-1.13` is itself a floating alias.
MODEL = "typesafe/jev-1.13-20260917"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
COST_CEILING = 0.50  # dollars per score run; 200 events cost about $0.005

STRATA = {"danger": 40, "question": 80, "turn_end": 80}
SPOT_CHECK = 20
CONTEXTS = ["ordinary", "meeting", "focus"]
SEED = 47
BODY_CHARS = 900

CONTEXT_TEXT = {
    "ordinary": "Master is at his desk, not in a call, and has not declared focus mode.",
    "meeting": "Master is in a call and his microphone is live.",
    "focus": "Master has declared focus mode.",
}

# One wording, shown to Master and sent to Jev alike. Keys are the answer keys.
QUESTIONS = {
    "interrupt": {
        "type": "noul",
        "instructions": "Should Master be interrupted right now for this event?",
        "criteria": {
            "true": "It needs him now: it is blocked on him, urgent, or destructive, and waiting would cost something.",
            "false": "It can wait until he is free without harm.",
        },
    },
    "channel": {
        "type": "choice",
        "instructions": "How should the overseer surface this event?",
        "criteria": {
            "speak": "Say it aloud immediately.",
            "written": "Queue it silently in written mode for when he looks.",
            "drop": "Do not surface it at all.",
        },
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this event?",
        "criteria": [
            "Can wait for hours",
            "Should be handled within the hour",
            "Needs attention now",
        ],
    },
}
# Second run, by Master's choice: the same questions with his ruled policy
# written into the criteria. It can only show Jev reproducing a table we
# already hold, and is scored on the same events, so read it as a ceiling.
QUESTIONS_RULED = {
    "interrupt": QUESTIONS["interrupt"] | {"criteria": {
        "true": "Master is at his desk (not in a call, not in focus mode) and the event is neither a "
                "danger-gate outright refusal nor a progress or holding note.",
        "false": "Master is in a call or in focus mode, or the event is a danger-gate outright refusal "
                 "or a progress or holding note while work continues.",
    }},
    "channel": QUESTIONS["channel"] | {"criteria": {
        "speak": "Master is at his desk and the event is anything but a gate refusal or a progress note.",
        "written": "Master is in a call or in focus mode, and the event is not a gate refusal or a progress note.",
        "drop": "A danger-gate outright refusal, or a progress or holding note while work continues.",
    }},
    "urgency": QUESTIONS["urgency"] | {"criteria": [
        "Can wait for hours: finished work, answers, design or approach questions, what-next menus, "
        "clarifications, verdicts on drafts.",
        "Should be handled within the hour: approval of a risky or outward act (push, delete, send, deploy), "
        "a danger-gate stop on a local command, finished work waiting on Master's own action or check, "
        "blocked on a credential only he can provide.",
        "Needs attention now: a danger-gate stop on a remote or container command, the gate's APPROVE/REFUSE "
        "matrix, a session failed or blocked by an error it cannot resolve.",
    ]},
}
VARIANTS = {"generic": QUESTIONS, "ruled": QUESTIONS_RULED}

CLASSES = {"interrupt": ["false", "true"], "channel": ["speak", "written", "drop"], "urgency": ["0", "1", "2"]}
KEYS = {
    "interrupt": {"y": "true", "n": "false"},
    "channel": {"s": "speak", "w": "written", "d": "drop"},
    "urgency": {"0": "0", "1": "1", "2": "2"},
}

# MASTER'S POLICY, ruled 2026-09-27 in five question waves instead of 200 blind
# labels. Per category: (urgency, channel at his desk). Urgency belongs to the
# event and never to the context. In a meeting or in focus nothing speaks, so a
# spoken event goes written there, and a dropped one stays dropped everywhere.
# Interrupt is exactly "spoken". Note the local/remote split he made: a remote
# stop is 2 and a local one 1, where hk47_rank gives every danger stop 2.
POLICY_TABLE = {
    "gate_local": (1, "speak"),       # gate stops a local destructive command, awaits APPROVE/REFUSE
    "gate_remote": (2, "speak"),      # gate stops a command bound for ssh or a container
    "gate_refuse": (0, "drop"),       # gate refuses outright; nothing waits on him
    "q_gate_matrix": (2, "speak"),    # the APPROVE/REFUSE matrix put to him
    "q_approval": (1, "speak"),       # approve a risky or outward act: push, delete, send, publish
    "q_design": (0, "speak"),         # blocked mid-task on a design or approach choice
    "q_what_next": (0, "speak"),      # work done, a "what next?" menu
    "q_clarify": (0, "speak"),        # clarify an ambiguous request before starting
    "t_done": (0, "speak"),           # finished and verified, nothing needed
    "t_needs_master": (1, "speak"),   # finished, waiting on his live check or action
    "t_failed": (2, "speak"),         # failed or blocked on something it cannot resolve alone
    "t_answer": (0, "speak"),         # an answer or plain information, nothing pending
    "t_progress": (0, "drop"),        # progress note while background work still runs
}
REMOTE_PREFIXES = ("ssh ", "docker ", "podman ", "kubectl ")
NOT_APPLICABLE = "n/a"


def policy_label(category, context):
    """Master clarified 2026-09-27: in a call or in focus urgency does not exist.
    Nothing speaks, everything is written and surfaces in arrival order, so
    urgency is only labelled, and only scored, at his desk."""
    urgency, desk = POLICY_TABLE[category]
    channel = "drop" if desk == "drop" else ("speak" if context == "ordinary" else "written")
    return {"interrupt": "true" if channel == "speak" else "false", "channel": channel,
            "urgency": str(urgency) if context == "ordinary" else NOT_APPLICABLE}


# hk47_rank's categories for the three kinds, and the ranks it gives them.
CATEGORY = {"danger": "danger.command", "question": "input.question", "turn_end": "task.complete"}
RANK = {"danger": 2, "question": 1, "turn_end": 0}
# hk47-desktop.py POLICY: speech only in ordinary; notify is always on.
SPEAKS = {"ordinary": True, "meeting": False, "focus": False}


def read_jsonl(path):
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


# --- build ------------------------------------------------------------------


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def is_prompt(entry):
    """A turn boundary: Master typed something, as opposed to a tool result or a meta line."""
    if entry.get("type") != "user" or entry.get("isMeta") or entry.get("isSidechain"):
        return False
    content = entry.get("message", {}).get("content")
    if isinstance(content, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
        return False
    return bool(text_of(content).strip())


def transcript_events(path, account):
    """Question and turn-end events from one transcript."""
    events, last_text, last_kind, cwd = [], "", None, ""
    with open(path) as f:
        entries = [json.loads(line) for line in f if line.strip()]
    for e in entries:
        cwd = e.get("cwd") or cwd
        if e.get("isSidechain"):
            continue
        if is_prompt(e):
            if last_kind == "text" and last_text.strip():
                events.append({"kind": "turn_end", "account": account, "project": Path(cwd).name,
                               "body": "My turn has ended with this reply to Master:\n" + last_text[-BODY_CHARS:]})
            last_kind = None
            continue
        if e.get("type") != "assistant":
            continue
        for b in e.get("message", {}).get("content", []) or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                last_text, last_kind = b["text"], "text"
            elif b.get("type") == "tool_use":
                last_kind = "tool"
                if b.get("name") == "AskUserQuestion":
                    qs = (b.get("input") or {}).get("questions") or []
                    asked = "\n".join(
                        "- " + q.get("question", "") + " [" + " | ".join(o.get("label", "") for o in q.get("options", [])) + "]"
                        for q in qs if isinstance(q, dict))
                    events.append({"kind": "question", "account": account, "project": Path(cwd).name,
                                   "body": "I am blocked waiting for Master to answer:\n" + asked[:BODY_CHARS]})
    return events


def gate_events(account_dir, account):
    events = []
    for g in read_jsonl(account_dir / "hk47-danger-gate.log"):
        cmd = g.get("segment") or g.get("command") or ""
        # Sorted mechanically, not by judgement: a deny waits on nobody, and an
        # ask is remote when the command leaves this machine.
        # "approved" lines record his answer, not a stop, and dontAsk turns an
        # ask into a refusal because no matrix can be shown in that mode.
        if g.get("verdict") not in ("ask", "deny"):
            continue
        if g.get("verdict") == "deny" or g.get("mode") == "dontAsk":
            category, what = "gate_refuse", "refused one of my commands outright; nothing waits on Master"
        else:
            category = "gate_remote" if cmd.lstrip().startswith(REMOTE_PREFIXES) else "gate_local"
            what = "stopped one of my commands and it waits on Master's approval"
        events.append({"kind": "danger", "category": category, "account": account,
                       "project": Path(g.get("cwd") or "").name,
                       "body": (f"The danger gate {what} (rule {g.get('rule')}, stage {g.get('stage')}):\n"
                                + cmd[:BODY_CHARS])})
    return events


def state_text(ev):
    return (f"Context: {CONTEXT_TEXT[ev['context']]}\n"
            f"Event in project {ev['project'] or 'unknown'}:\n{ev['body']}")


def cmd_build(args):
    if CORPUS.exists() and not args.force:
        sys.exit(f"{CORPUS} exists; labels are keyed to it. Pass --force to rebuild.")
    pool = {k: [] for k in STRATA}
    for acc in ACCOUNTS:
        for ev in gate_events(acc, acc.name):
            pool["danger"].append(ev)
        for path in glob.glob(str(acc / "projects" / "*" / "*.jsonl")):
            for ev in transcript_events(path, acc.name):
                pool[ev["kind"]].append(ev)
    rng = random.Random(SEED)
    corpus = []
    for kind, want in STRATA.items():
        seen, unique = set(), []
        for ev in pool[kind]:
            h = hashlib.sha1(ev["body"].encode()).hexdigest()
            if h not in seen:
                seen.add(h)
                unique.append(ev | {"id": h[:12]})
        print(f"{kind}: {len(pool[kind])} found, {len(unique)} unique, taking {min(want, len(unique))}")
        picked = rng.sample(unique, min(want, len(unique)))
        for i, ev in enumerate(picked):
            ev["context"] = CONTEXTS[i % 3]
            corpus.append(ev)
    rng.shuffle(corpus)
    DATA.mkdir(parents=True, exist_ok=True)
    with open(CORPUS, "w") as f:
        for ev in corpus:
            f.write(json.dumps(ev | {"state": state_text(ev)}) + "\n")
    print(f"{len(corpus)} events written to {CORPUS}")


# --- label ------------------------------------------------------------------


def getch():
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def ask(name):
    q = QUESTIONS[name]
    keys = KEYS[name]
    if name == "urgency":
        legend = "  ".join(f"[{k}] {c}" for k, c in zip(keys, q["criteria"]))
    else:
        legend = "  ".join(f"[{k}] {v}: {q['criteria'][v]}" for k, v in keys.items())
    print(f"\n{q['instructions']}\n  {legend}\n  [u] undo previous event   [q] quit")
    while True:
        c = getch().lower()
        if c in keys or c in ("u", "q", "\x03"):
            return c


def cmd_label(args):
    """The blind spot-check: Master labels a fixed sample, never seeing the category or any answer."""
    corpus = read_jsonl(CORPUS)
    if not corpus:
        sys.exit("No corpus. Run `build` first.")
    corpus = random.Random(SEED + 1).sample(corpus, min(SPOT_CHECK, len(corpus)))
    while True:
        sample_ids = {ev["id"] for ev in corpus}
        done = {r["id"]: r for r in read_jsonl(LABELS) if r["id"] in sample_ids}
        todo = [ev for ev in corpus if ev["id"] not in done]
        if not todo:
            print(f"All {len(corpus)} labelled.")
            return
        ev = todo[0]
        print("\033[2J\033[H" + f"[{len(done) + 1}/{len(corpus)}]\n\n" + ev["state"])
        answer = {}
        for name in QUESTIONS:
            c = ask(name)
            if c in ("q", "\x03"):
                return
            if c == "u":
                rows = read_jsonl(LABELS)[:-1]
                LABELS.write_text("".join(json.dumps(r) + "\n" for r in rows))
                break
            answer[name] = KEYS[name][c]
            print(f"  -> {answer[name]}")
        else:
            with open(LABELS, "a") as f:
                f.write(json.dumps({"id": ev["id"], **answer, "t": time.time()}) + "\n")


# --- score ------------------------------------------------------------------


def api_key():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        key = subprocess.run(["fish", "-c", "echo $OPENROUTER_API_KEY"], capture_output=True, text=True).stdout.strip()
    if not key:
        sys.exit("OPENROUTER_API_KEY not found in the environment or fish universal vars.")
    return key


def jev(state, key, questions):
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    req = urllib.request.Request(ENDPOINT, data=body, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=60) as r:
        out = json.load(r)
    out["latency_s"] = time.monotonic() - t0
    return out


def jev_probs(answers):
    """Probabilities per class, always from `probabilities`, never from `choice`, which hides ties."""
    p = {"interrupt": {"true": answers["interrupt"]["noul"], "false": 1 - answers["interrupt"]["noul"]}}
    for name in ("channel", "urgency"):
        p[name] = {c: answers[name]["probabilities"].get(c, 0.0) for c in CLASSES[name]}
    return p


def incumbent_probs(ev):
    speaks = SPEAKS[ev["context"]]
    one = lambda hot, name: {c: float(c == hot) for c in CLASSES[name]}  # noqa: E731
    return {"interrupt": one("true" if speaks else "false", "interrupt"),
            "channel": one("speak" if speaks else "written", "channel"),
            "urgency": one(str(RANK[ev["kind"]]), "urgency")}


def metrics(rows):
    """rows: (probs dict, true class). Agreement, multiclass Brier, and 10-bin ECE on the top class."""
    agree, brier, bins = 0, 0.0, [[] for _ in range(10)]
    for p, y in rows:
        top = max(p, key=p.get)
        agree += top == y
        brier += sum((p[c] - (c == y)) ** 2 for c in p)
        bins[min(int(p[top] * 10), 9)].append((p[top], top == y))
    n = len(rows)
    ece = sum(len(b) / n * abs(statistics.mean(c for c, _ in b) - statistics.mean(h for _, h in b)) for b in bins if b)
    return agree / n, brier / n, ece


def categorised(corpus):
    """Every event with its category: danger sorted at build, the rest from the droid's sort."""
    sorted_ = {r["id"]: r["category"] for r in read_jsonl(CATEGORIES)}
    out = {}
    for i, ev in corpus.items():
        cat = ev.get("category") or sorted_.get(i)
        if cat not in POLICY_TABLE:
            continue
        out[i] = ev | {"category": cat}
    return out


def cmd_pending(args):
    """Questions and turn ends not yet sorted, compact, for the droid to categorise."""
    done = {r["id"] for r in read_jsonl(CATEGORIES)}
    for ev in read_jsonl(CORPUS):
        if ev["kind"] != "danger" and ev["id"] not in done:
            print(json.dumps({"id": ev["id"], "kind": ev["kind"], "body": ev["body"]}))


def cmd_score(args):
    corpus = {ev["id"]: ev for ev in read_jsonl(CORPUS)}
    events = categorised(corpus)
    missing = len(corpus) - len(events)
    if missing and not args.probe:
        sys.exit(f"{missing} events have no category yet. Sort them into {CATEGORIES} first (see `pending`).")
    # Truth is Master's ruled policy applied to each event's category and context.
    labels = [{"id": i, **policy_label(ev["category"], ev["context"])} for i, ev in events.items()]
    suffix = "" if args.variant == "generic" else f"-{args.variant}"
    cache_path = DATA / f"jev-{MODEL.replace('/', '_')}{suffix}.jsonl"
    cache = {r["id"]: r for r in read_jsonl(cache_path)}
    ids = [r["id"] for r in labels] if not args.probe else list(corpus)[:args.probe]
    key, spent = api_key(), sum(r["usage"]["cost"] for r in cache.values())
    with open(cache_path, "a") as f:
        for i in ids:
            if i in cache:
                continue
            if spent > COST_CEILING:
                sys.exit(f"Cost ceiling ${COST_CEILING} reached; stopping.")
            out = jev(corpus[i]["state"], key, VARIANTS[args.variant])
            spent += out["usage"]["cost"]
            # The response carries its own generation id; ours must win the key.
            cache[i] = {**out, "gen_id": out.get("id"), "id": i}
            f.write(json.dumps(cache[i]) + "\n")
    if args.probe:
        for i in ids:
            print(i, json.dumps(jev_probs(cache[i]["answers"])))
        return

    lat = [cache[r["id"]]["latency_s"] for r in labels]
    print(f"model {MODEL}  labelled {len(labels)}  cost ${sum(cache[r['id']]['usage']['cost'] for r in labels):.5f}  "
          f"latency p50 {statistics.median(lat):.2f}s  p95 {sorted(lat)[int(len(lat) * 0.95) - 1]:.2f}s\n")
    print(f"{'question':<10} {'system':<10} {'agree':>6} {'brier':>6} {'ece':>6}")
    for name in QUESTIONS:
        res = {}
        for system, probs in (("incumbent", lambda r: incumbent_probs(corpus[r["id"]])),
                              ("jev", lambda r: jev_probs(cache[r["id"]]["answers"]))):
            res[system] = metrics([(probs(r)[name], r[name]) for r in labels if r[name] != NOT_APPLICABLE])
            print(f"{name:<10} {system:<10} {res[system][0]:>6.2f} {res[system][1]:>6.3f} {res[system][2]:>6.3f}")
        beats = res["jev"][1] < res["incumbent"][1] and res["jev"][0] >= res["incumbent"][0]
        print(f"{'':<10} {'verdict':<10} {'Jev beats the incumbent' if beats else 'Jev does not beat the incumbent'}\n")

    # How far the ruled policy (and the droid's sort) sits from Master's own
    # per-event judgement. Low agreement means the table above measured the
    # policy, not Master.
    spot = read_jsonl(LABELS)
    truth = {r["id"]: r for r in labels}
    spot = [r for r in spot if r["id"] in truth]
    if not spot:
        print("Spot-check: no blind labels yet, so the policy labels are unverified.")
        return
    print(f"Spot-check: policy vs Master's blind labels on {len(spot)} events")
    for name in QUESTIONS:
        rows = [r for r in spot if truth[r["id"]][name] != NOT_APPLICABLE]
        agree = sum(r[name] == truth[r["id"]][name] for r in rows)
        print(f"  {name:<10} {agree}/{len(rows)}")
    for r in spot:
        diff = [n for n in QUESTIONS if truth[r["id"]][n] not in (r[n], NOT_APPLICABLE)]
        if diff:
            print(f"  {r['id']} {events[r['id']]['category']:<15} "
                  + "  ".join(f"{n}: policy {truth[r['id']][n]} / Master {r[n]}" for n in diff))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--force", action="store_true")
    sub.add_parser("label")
    sub.add_parser("pending")
    s = sub.add_parser("score")
    s.add_argument("--probe", type=int, default=0, help="score N events to exercise the API")
    s.add_argument("--variant", choices=VARIANTS, default="generic",
                   help="generic criteria, or Master's ruled policy written into them")
    args = ap.parse_args()
    {"build": cmd_build, "label": cmd_label, "pending": cmd_pending, "score": cmd_score}[args.cmd](args)


if __name__ == "__main__":
    main()
