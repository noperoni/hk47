#!/usr/bin/env python3
"""PERS-22: may Haiku answer Master in dialogue, or must it stay Opus?

Master's rulings of 2026-09-28: quality is the floor and speed comes second,
so Haiku is used only if it matches Opus on what the dialogue is for; and he
judges every pair himself, blind.

    build   draw 30 real items (15 questions, 15 turn ends) from the queue's
            event log, personal account only, and give each a spoken command
    answer  both models do three tasks on every item; outputs and wall time
            are stored, not shown
    judge   90 pairs, model hidden and sides shuffled: a, b, or = for a tie.
            Resumable; quit with q
    score   Haiku's wins, ties and losses per task, and the median seconds

THE THREE TASKS are the dialogue's jobs:
    condense   the session's last message as one or two spoken sentences,
               with the overseer's own SUMMARISE prompt
    follow     his spoken follow-up about the item, answered
    readback   a spoken command, possibly misheard, read back before acting
               (the Archon read-back Master ruled for PERS-22)

PRIVACY: the items are Master's own session text. They live in
dialogue-eval/data/, gitignored because this repo is public, and go only to
Claude through `claude -p` on the personal account. The work account is
excluded, as in hk47-jev-eval.py.
"""

import argparse
import importlib
import json
import os
import random
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
overseer = importlib.import_module("hk47-overseer")
jev = importlib.import_module("hk47-jev-eval")
hkq = importlib.import_module("hk47-queue")

DATA = HERE / "dialogue-eval" / "data"
ITEMS = DATA / "items.jsonl"
ANSWERS = DATA / "answers.jsonl"
JUDGED = DATA / "judged.jsonl"
MODELS = ("opus", "haiku")
PER_KIND = 15
SEED = 20260928

VOICE = ("You are HK-47 speaking aloud to your master through text-to-speech. Each sentence starts with a "
         "declared qualifier such as Statement: or Query:. No markdown, no lists, no paths or code, no commas "
         "at all, never the word master, and avoid words spelled one way and said two ways such as live or "
         "read. Output only the spoken sentences.")
TASKS = {
    "condense": (overseer.SUMMARISE, "Project {subject}, its session's last message:\n\n{text}"),
    "follow": (VOICE + " Answer his follow-up question about a waiting session in one to three short "
               "sentences, from what the session said and nothing else. If it does not say, say so.",
               "Project {subject}, its session's last message:\n\n{text}\n\nHe asks: What does it need "
               "from me, and what happens if I say yes?"),
    "readback": (VOICE + " He spoke a command about a waiting session and Whisper transcribed it, possibly "
                 "wrongly. Do not act. Read back exactly what you understood you are to do, naming each "
                 "action, then ask whether that is correct. If a word seems misheard, say which.",
                 "Project {subject}, its session's last message:\n\n{text}\n\nHis command, as transcribed: "
                 "{command}"),
}
# Whisper-shaped commands, two with a plausible mishearing, dealt round-robin.
COMMANDS = ["Go with the first option and push when it's done.",
            "Tell it no, and ask it to explain the second one first.",
            "Approve it but don't commit anything yet.",
            "Tell it to wrap up and write the hand off.",
            "Go ahead and delete the old brunch after merging.",
            "Say yes to the plan but skip the test sweet for now."]


def call(model, system, prompt):
    env = os.environ | {"CLAUDE_CONFIG_DIR": str(hkq.config_dir("claude-personal"))}
    start = time.perf_counter()
    proc = subprocess.run(["claude", "-p", "--model", model, "--tools", "", "--no-session-persistence",
                           "--settings", '{"disableAllHooks": true}', "--system-prompt", system],
                          input=prompt, capture_output=True, text=True, timeout=180, env=env, cwd="/tmp")
    return {"text": " ".join(proc.stdout.split()), "seconds": round(time.perf_counter() - start, 1),
            "error": proc.stderr.strip()[:200] if proc.returncode else ""}


def cmd_build(_args):
    rng, seen, pool = random.Random(SEED), set(), {"question": [], "waiting": []}
    for ev in jev.read_jsonl(hkq.EVENTS):
        text = ev.get("text", "")
        if ev.get("account") == "claude-personal" and ev.get("flag") in pool and len(text) > 80 and text not in seen:
            seen.add(text)
            pool[ev["flag"]].append(ev)
    items = []
    for flag, evs in pool.items():
        for ev in rng.sample(evs, min(PER_KIND, len(evs))):
            items.append({"id": f"{flag[0]}{len(items):02d}", "kind": flag, "subject": hkq.subject(ev.get("cwd")),
                          "text": ev["text"], "command": COMMANDS[len(items) % len(COMMANDS)]})
    DATA.mkdir(parents=True, exist_ok=True)
    ITEMS.write_text("".join(json.dumps(i) + "\n" for i in items))
    print(f"{len(items)} items -> {ITEMS}")


def cmd_answer(_args):
    items = jev.read_jsonl(ITEMS)
    done = {(a["item"], a["task"], a["model"]) for a in jev.read_jsonl(ANSWERS) if not a["error"]}
    jobs = [(i, task, model) for i in items for task in TASKS for model in MODELS
            if (i["id"], task, model) not in done]

    def run(job):
        item, task, model = job
        system, template = TASKS[task]
        return {"item": item["id"], "task": task, "model": model, **call(model, system, template.format(**item))}

    with ThreadPoolExecutor(max_workers=6) as pool, open(ANSWERS, "a") as out:
        for n, row in enumerate(pool.map(run, jobs), 1):
            out.write(json.dumps(row) + "\n")
            out.flush()
            print(f"{n}/{len(jobs)} {row['item']} {row['task']} {row['model']} {row['seconds']}s"
                  + (f" ERROR {row['error']}" if row["error"] else ""), flush=True)


def pairs():
    answers = {}
    for a in jev.read_jsonl(ANSWERS):
        if not a["error"]:
            answers[(a["item"], a["task"], a["model"])] = a
    items = {i["id"]: i for i in jev.read_jsonl(ITEMS)}
    out = [(items[i], t, answers[(i, t, "opus")], answers[(i, t, "haiku")])
           for i in items for t in TASKS if (i, t, "opus") in answers and (i, t, "haiku") in answers]
    random.Random(SEED).shuffle(out)
    return out


def cmd_judge(_args):
    judged = {(j["item"], j["task"]) for j in jev.read_jsonl(JUDGED)}
    todo = [p for p in pairs() if (p[0]["id"], p[1]) not in judged]
    rng = random.SystemRandom()
    for n, (item, task, opus, haiku) in enumerate(todo, 1):
        left = rng.choice(("opus", "haiku"))
        a, b = (opus, haiku) if left == "opus" else (haiku, opus)
        print("\033[2J\033[H" + f"[{n}/{len(todo)}]  {task.upper()}  ({item['subject']})\n")
        print(item["text"])
        if task == "readback":
            print(f"\nCOMMAND AS HEARD: {item['command']}")
        print(f"\n  a) {a['text']}\n\n  b) {b['text']}\n\nbetter?  a / b / = tie / q quit ", end="", flush=True)
        key = ""
        while key not in ("a", "b", "=", "q"):
            key = jev.getch()
        if key == "q":
            print("\nsaved; run judge again to resume")
            return
        winner = {"a": a["model"], "b": b["model"], "=": "tie"}[key]
        with open(JUDGED, "a") as out:
            out.write(json.dumps({"item": item["id"], "task": task, "winner": winner, "left": left}) + "\n")
    print("\nall pairs judged; run score")


def cmd_score(_args):
    judged = jev.read_jsonl(JUDGED)
    answers = jev.read_jsonl(ANSWERS)
    print(f"{'task':<10} {'haiku wins':>10} {'ties':>5} {'opus wins':>10}")
    for task in list(TASKS) + ["all"]:
        rows = [j for j in judged if task in (j["task"], "all")]
        count = lambda w: sum(1 for j in rows if j["winner"] == w)
        print(f"{task:<10} {count('haiku'):>10} {count('tie'):>5} {count('opus'):>10}")
    for model in MODELS:
        secs = [a["seconds"] for a in answers if a["model"] == model and not a["error"]]
        if secs:
            print(f"{model}: median {statistics.median(secs)}s over {len(secs)} calls")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="verb", required=True)
    for verb in ("build", "answer", "judge", "score"):
        sub.add_parser(verb)
    args = parser.parse_args()
    {"build": cmd_build, "answer": cmd_answer, "judge": cmd_judge, "score": cmd_score}[args.verb](args)


if __name__ == "__main__":
    main()
