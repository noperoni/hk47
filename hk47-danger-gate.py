#!/usr/bin/env python3
"""Stop a destructive command before it runs, rather than narrating it afterwards.

Master's ruling, 2026-09-11: the `danger.command` sound was never the point. A
dangerous command must STOP, and the droid must then present a matrix of what it
wants to run, why, and what each command would do, and ask permission for each.
The sound stays; it is now an accompaniment to a block rather than a eulogy.

WHY THIS IS NOT A PATCH TO peon.sh
----------------------------------
`peon update` overwrites `peon.sh`, and `install-peon-seams.py` has to be re-run
after every update. A guardrail that disappears on a vendor update, silently,
while everything still looks and sounds correct, is worse than no guardrail: it
would be trusted right up until the moment it was not there. So the gate is its
own hook, owned by this project, and the sound stays where it is.

WHAT THE GATE ACTUALLY DOES, IN THREE STAGES
--------------------------------------------
The first build emitted `ask`, which opens Claude Code's own permission dialog
on the raw command. Master watched that happen in a live session on 2026-09-11
and named the flaw: the dialog asks him to approve a COMMAND, and never makes
the droid explain itself first. The matrix existed only as an instruction in
CLAUDE.md, and an instruction is not a mechanism. So:

  * DENY, always        -- unrecoverable. Exit 2, no discussion, no retry path
                           of any kind. If Master wants it done he types it
                           himself, which is a decision no hook should make on
                           his behalf. `rm -rf /`, mkfs, shred, pkill, and the
                           power verbs, which end the session that would
                           otherwise have done the approving.

  * DENY, on first sight -- dangerous and recoverable. Exit 2 as well, but the
                           message orders the matrix: every command the droid
                           wants, verbatim, with its reason and its blast
                           radius, one AskUserQuestion per row. The block is
                           what makes the matrix unavoidable.

  * SILENCE, on retry    -- the same danger, already stopped once this session.
                           The gate exits 0 saying nothing, and Master's own
                           permission rules decide. It does NOT emit `allow`;
                           see the record section further down for why that
                           distinction is the whole of the remaining safety.

WHY THERE IS STILL NO HOME-MADE APPROVAL FILE
---------------------------------------------
The tempting design is: deny, let the droid ask, then let an approval file
through to `allow`. That is worthless, because the droid writes the file, and
any channel the droid can write is a channel the droid can forge. The
per-session record below looks like that design and is not, because it can only
ever buy silence. Forging it returns the gate to the behaviour it had before
the record existed, and buys nothing beyond that.

WHAT THIS GATE IS NOT FOR
-------------------------
Destruction, not privilege, and not disruption. Master re-cut the list on
2026-09-11 after `sudo`-on-its-own stopped ten of his fourteen commands in a
day, every one of them a `sudo` inside a quoted ssh payload doing no damage
whatsoever. `ssh host 'sudo systemctl restart k3s'` is ordinary work and the
gate is silent for it. `rm -rf`, a dropped database, a wiped volume and a
formatted disk are not, and it is not silent for those.

HOW A BLOCKLIST IS BEATEN, AND WHAT IS DONE ABOUT IT
----------------------------------------------------
Substring matching loses to the first person who adds a quote. Three known
bypasses, all handled here:

  1. Interpreter wrapping: `bash -c "rm -rf /"`, `python3 -c "os.system(...)"`.
     Payloads of -c/-e/--eval are extracted and re-analysed, to MAX_DEPTH.
  2. Quoting and flag spelling: `rm -fr`, `rm --recursive --force`, `r''m -rf`.
     Commands are TOKENISED with shlex, so quoting is resolved before matching
     and flags are compared as parsed sets rather than as literal text.
  3. Compound commands: the dangerous segment hiding behind `&&`, `;` or a pipe,
     or inside `$(...)`. The token stream is split on operators and every
     segment is judged on its own, and substitutions are pulled out and judged
     as commands in their own right.

Tokenising also fixes the false positive that would otherwise get this hook
switched off within a day: `echo "rm -rf /"`, `grep -rn pkill peon.sh` and a
heredoc full of dangerous examples all have a harmless argv[0], and this file
itself is full of such strings.

FAILURE MODE
------------
A guardrail that fails open is decoration. A guardrail that fails closed on its
own bugs is a brick through the windscreen. On any internal error this emits ASK
with the error named: nothing is silently allowed, and Master can wave it
through in one keystroke.

Contract (docs: code.claude.com/docs/en/hooks): stdin is the PreToolUse event
JSON; stdout is {"hookSpecificOutput": {"hookEventName": "PreToolUse",
"permissionDecision": "allow"|"deny"|"ask", "permissionDecisionReason": str}}.
Exit 2 blocks unconditionally and takes its message from that reason. Exit 0
with no output means no opinion, and normal permission flow applies.
"""

import json
import os
import re
import shlex
import sys
import time

MAX_DEPTH = 10
LOG_PATH = os.path.expanduser("~/.claude/hk47-danger-gate.log")

# What the droid is told when it is stopped. The wording matters: this string is
# what comes back into the transcript, so it is the instruction the droid acts
# on next. CLAUDE.md carries the same protocol, and the two must agree.
MATRIX_ORDER = (
    "STOP. Do not rephrase, split, re-quote, wrap, or otherwise route around "
    "this command. Present Master with a matrix of every command you want to "
    "run, one row each: the command verbatim, why you want it, and what it "
    "would do if it is wrong. Then ask permission on each row SEPARATELY with "
    "AskUserQuestion. A single blanket 'may I proceed?' is not permission."
)

# Said only on the first sighting of an ask-tier danger, never on the deny tier.
#
# The last sentence is the uncomfortable truth of the design Master chose on
# 2026-09-11, and it is stated to the droid rather than hidden from it. The
# record is written when a danger is STOPPED, not when Master approves it,
# because a PreToolUse hook cannot see the answer to a question that has not
# been asked yet. So a refusal is enforced by nothing but compliance.
RETRY_CLAUSE = (
    "If and ONLY IF Master approves that row, run the command again and this "
    "gate will stand aside for it. If he refuses it, the command is dead: do "
    "not run it, and do not run a variation of it. This gate cannot tell his "
    "yes from his no. That part is on your honour, and every stand-aside is "
    "written to the audit log where he can read it back."
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Command substitutions are analysed as commands in their own right rather than
# being flattened, so `echo $(rm -rf /)` is caught on the inner command and
# `echo "(a)"` is not caught on anything.
SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")

# Escape-aware ON PURPOSE, and the escapes are the whole reason this exists.
# A naive `"([^"]+)"` scanning an interpreter payload does not know that `\"` is
# a quote INSIDE a string rather than the end of one, so it starts a fresh
# "literal" at every escaped quote. Prose that merely discusses a command then
# parses as that command. This is not hypothetical: on 2026-09-11 the gate
# blocked this project's own state.json write, and the segment it named was
# `rm -rf /\`, scraped out of a sentence documenting the gate's earlier
# false-positive fixes. Consuming `\\.` as one unit keeps such a string whole,
# so the literal that comes out is the sentence, whose argv[0] is a word.
STRING_LITERAL = re.compile(r"\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)'")

OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", "\n"}
REDIRECTS = {">", ">>", ">|", "<", "2>", "&>"}

# argv[0] values that carry another command in their arguments. The value is the
# set of flags whose ARGUMENT is that command; an empty set means "everything
# after the flags is the command".
INTERPRETERS = {
    "bash": {"-c"}, "sh": {"-c"}, "zsh": {"-c"}, "dash": {"-c"}, "ksh": {"-c"},
    "fish": {"-c"}, "ash": {"-c"},
    "python": {"-c"}, "python2": {"-c"}, "python3": {"-c"},
    "perl": {"-e", "-E"}, "ruby": {"-e"}, "node": {"-e", "--eval", "-p"},
    "nodejs": {"-e", "--eval"}, "deno": {"eval"}, "bun": {"-e"},
    "php": {"-r"}, "rscript": {"-e"},
}

# Prefixes that wrap a command without being one. Stripped and re-judged, so
# `sudo nohup timeout 5 rm -rf /` is judged as `rm -rf /` (and separately as
# sudo, which asks on its own account).
WRAPPERS = {
    "sudo": 0, "doas": 0, "nohup": 0, "setsid": 0, "command": 0, "builtin": 0,
    "exec": 0, "stdbuf": 0, "unbuffer": 0, "time": 0, "eval": 0, "xargs": 0,
    "watch": 0, "env": 0, "ionice": 0,
    # these take one argument of their own before the real command
    "timeout": 1, "nice": 1, "flock": 1,
}

SYSTEM_ROOTS = (
    "/", "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/nfs",
    "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys", "/usr", "/var",
)
TEMP_ROOTS = ("/tmp", "/var/tmp", "/dev/shm")
DISK_DEVICE = re.compile(r"^/dev/(sd[a-z]|nvme\d|hd[a-z]|vd[a-z]|mmcblk\d|disk\d)")
# A target that is only a variable is unknowable, and `rm -rf "$D"` with an
# unset D is the single most famous way to lose a machine.
BARE_VARIABLE = re.compile(r"^[\"']?\$\{?\w+\}?[\"']?/?\*?$")


HEREDOC = re.compile(r"<<-?\s*([\"']?)([A-Za-z_]\w*)\1")


def strip_heredocs(command):
    """Lift heredoc bodies out of a command. Returns (command, bodies).

    A heredoc body is DATA on stdin, not a command: `git commit -F - <<EOF`
    carries a commit message, and this project's messages routinely quote
    destructive commands while discussing them. Judging that text as though it
    were about to be executed blocks the very work that documents the gate, and
    a guardrail that blocks its own paperwork does not survive the week.

    The bodies are returned rather than discarded, because `bash <<EOF` really
    does execute its body, and the caller re-judges them when the receiving
    command is an interpreter.
    """
    bodies = []
    lines = command.splitlines(True)
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        m = HEREDOC.search(line)
        if not m:
            i += 1
            continue
        delim = m.group(2)
        i += 1
        body = []
        while i < len(lines) and lines[i].strip() != delim:
            body.append(lines[i])
            i += 1
        i += 1  # skip the terminator itself
        bodies.append("".join(body))
    return "".join(out), bodies


def lex(command):
    """Token list for a command string, operators included, quoting resolved."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        # Unbalanced quote. Naive split still exposes argv[0], which is most of
        # what the rules need, so degrade rather than give up.
        return command.replace("&&", " && ").replace(";", " ; ").split()


def segments(command):
    """Split a command into independently judged segments."""
    out, cur = [], []
    for tok in lex(command):
        if tok in OPERATORS or set(tok) <= {"&", "|", ";"} and tok:
            if cur:
                out.append(cur)
                cur = []
        else:
            cur.append(tok)
    if cur:
        out.append(cur)
    return out


def unwrap(argv):
    """Strip sudo/nohup/env/timeout-style prefixes. Returns (argv, prefixes)."""
    seen = []
    argv = list(argv)
    while argv:
        head = os.path.basename(argv[0]).strip("\"'").lower()
        if head not in WRAPPERS:
            break
        seen.append(head)
        skip = WRAPPERS[head]
        argv = argv[1:]
        # drop the wrapper's own flags, VAR=value assignments, and its argument
        while argv and (argv[0].startswith("-") or "=" in argv[0].split("/")[0]):
            argv = argv[1:]
        argv = argv[skip:]
    return argv, seen


def payloads(argv):
    """Commands hidden inside an interpreter invocation."""
    if not argv:
        return []
    head = os.path.basename(argv[0]).strip("\"'").lower()
    flags = INTERPRETERS.get(head)
    if flags is None:
        return []
    found = []
    for i, tok in enumerate(argv[1:], start=1):
        if tok in flags and i + 1 < len(argv):
            found.append(argv[i + 1])
    return found


def collect(command, depth=0):
    """Every argv this command could actually execute, wrappers unwrapped."""
    if depth > MAX_DEPTH or not command or not command.strip():
        return []
    found = []
    command, heredocs = strip_heredocs(command)
    for body in heredocs:
        # Only an interpreter executes what it is fed. Anything else is being
        # handed text, and text is not a command.
        for argv in segments(command):
            argv, _ = unwrap(argv)
            if argv and os.path.basename(argv[0]).lower() in INTERPRETERS:
                found.extend(collect(body, depth + 1))
                break
    if depth:
        # Inside an interpreter payload the text may not be shell at all:
        # `python3 -c 'import os; os.system("rm -rf /")'` hides the command in a
        # STRING LITERAL, where shell tokenising cannot reach it. So at depth we
        # also judge every quoted literal as a command in its own right. This
        # over-detects by design: a literal that parses into something
        # destructive is worth a stop whether or not it was going to be run.
        for lit in STRING_LITERAL.findall(command):
            body = lit[0] or lit[1]
            if body.strip() and body.strip() != command.strip():
                found.extend(collect(body, depth + 1))
    for body in SUBST.findall(command):
        for inner in body:
            if inner.strip():
                found.extend(collect(inner, depth + 1))
    for argv in segments(command):
        argv, _prefixes = unwrap(argv)
        if not argv:
            continue
        found.append(argv)
        for payload in payloads(argv):
            found.extend(collect(payload, depth + 1))
    return found


# ---------------------------------------------------------------------------
# Rule helpers
# ---------------------------------------------------------------------------

def base(argv):
    return os.path.basename(argv[0]).strip("\"'").lower() if argv else ""


def flags(argv):
    """The set of single letters and long flags present, however they are spelt.

    `-rf`, `-r -f` and `--recursive --force` all yield {"r", "f", "recursive",
    "force"}, which is what defeats the spelling bypass.
    """
    out = set()
    for tok in argv[1:]:
        if tok.startswith("--"):
            out.add(tok[2:].split("=")[0].lower())
        elif tok.startswith("-") and len(tok) > 1:
            out.update(tok[1:].lower())
    return out


def operands(argv):
    """Non-flag arguments."""
    return [t for t in argv[1:] if not t.startswith("-")]


def recursive(argv):
    f = flags(argv)
    return "r" in f or "R" in f or "recursive" in f


def forced(argv):
    f = flags(argv)
    return "f" in f or "force" in f


def resolve(target, cwd):
    """Absolute path for a target, best effort, without touching the disk."""
    t = target.strip("\"'")
    if t.startswith("~"):
        t = os.path.expanduser(t)
    if not os.path.isabs(t):
        t = os.path.join(cwd or os.getcwd(), t)
    return os.path.normpath(t)


def is_system_target(path):
    """True for / itself and for the top level of any system directory."""
    p = path.rstrip("/") or "/"
    if p == "/":
        return True
    return p in SYSTEM_ROOTS


def in_temp(path):
    return any(path == r or path.startswith(r + "/") for r in TEMP_ROOTS)


def redirect_targets(argv_line):
    """Targets of output redirection in the raw token stream."""
    toks = lex(argv_line)
    out = []
    for i, tok in enumerate(toks):
        if tok in REDIRECTS and i + 1 < len(toks):
            out.append(toks[i + 1].strip("\"'"))
    return out


def excerpt(raw, match, before=0, after=60):
    """A readable window of `raw` around the text a raw-rule actually matched.

    A raw-rule has no argv of its own, and handing back an unrelated one is how
    the gate spent 2026-09-11 telling Master that `seq 1 24` ran with root
    privileges. The matrix is built from the segment, so the segment has to be
    the thing that fired.
    """
    s = max(0, match.start() - before)
    e = min(len(raw), match.end() + after)
    body = " ".join(raw[s:e].split())
    return ("..." if s else "") + body + ("..." if e < len(raw) else "")


# ---------------------------------------------------------------------------
# Rules
#
# There are two kinds, and the difference is what they can name afterwards.
#
#   ARGV rules get (argv, cwd, raw) and return a consequence string. The thing
#   they fired on is the argv, so `judge` names it for them.
#
#   RAW rules get (raw) alone, because what they match spans the whole command
#   line: a redirect, a pipe, a fork bomb, a sudo three segments along. They
#   have no argv, so they return (consequence, segment) and name it themselves.
#   They used to be argv rules that ignored their argv, which meant `judge`
#   reported whichever argv it happened to be holding. That is how a stopped
#   command came to be described as `seq 1 24`.
#
# Order within a tier is irrelevant; the harshest verdict wins, because a
# command that trips two rules is not less dangerous.
# ---------------------------------------------------------------------------

def rule_rm_root(argv, cwd, raw):
    if base(argv) != "rm" or not recursive(argv):
        return None
    for target in operands(argv):
        if BARE_VARIABLE.match(target):
            return ("the target is a bare variable, so if it is unset or empty "
                    "this deletes from the root down")
        if target.strip("\"'") in ("/", "/*", "~", "~/", "*", "/.", "$HOME"):
            return "this deletes the machine"
        path = resolve(target, cwd)
        if is_system_target(path):
            return f"{path} is a system root, and this empties it"
    return None


def rule_mkfs(argv, cwd, raw):
    b = base(argv)
    if b == "mkfs" or b.startswith("mkfs.") or b in ("mkswap", "wipefs", "blkdiscard"):
        return "this formats or erases a block device, and nothing survives it"
    return None


def rule_dd_device(argv, cwd, raw):
    if base(argv) != "dd":
        return None
    for tok in argv[1:]:
        if tok.startswith("of=") and DISK_DEVICE.match(tok[3:].strip("\"'")):
            return "this writes raw bytes over a disk, past every filesystem"
    return None


def rule_shred(argv, cwd, raw):
    if base(argv) == "shred":
        return "shred overwrites data so that recovery is impossible by design"
    return None


def rule_forkbomb(raw):
    m = re.search(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;?\s*:", raw)
    if m:
        return ("this is a fork bomb and the machine will need a power cycle",
                excerpt(raw, m, after=0))
    return None


def rule_device_redirect(raw):
    for target in redirect_targets(raw):
        if DISK_DEVICE.match(target):
            return (f"this writes over the raw device {target}", f"> {target}")
        if target in ("/etc/passwd", "/etc/shadow", "/etc/fstab", "/etc/sudoers"):
            return (f"this overwrites {target}, which locks the machine or its accounts",
                    f"> {target}")
    return None


def rule_recursive_perms_on_system(argv, cwd, raw):
    if base(argv) not in ("chmod", "chown", "chgrp") or not recursive(argv):
        return None
    for target in operands(argv):
        path = resolve(target, cwd)
        if is_system_target(path):
            return f"a recursive permission change over {path} breaks the system's own binaries"
    return None


def rule_process_slaughter(argv, cwd, raw):
    b = base(argv)
    if b in ("pkill", "killall", "xkill"):
        return ("pattern-matched process killing is barred outright by Master's "
                "own standing rule, because the pattern always matches more than intended")
    return None


def rule_find_delete_system(argv, cwd, raw):
    if base(argv) != "find":
        return None
    f = set(t.lower() for t in argv[1:])
    if "-delete" not in f and "-exec" not in f:
        return None
    for target in operands(argv):
        if target.startswith("-"):
            continue
        path = resolve(target, cwd)
        if is_system_target(path):
            return f"a recursive delete walking {path}"
        break
    return None


# --- ask tier -------------------------------------------------------------

def rule_rm_general(argv, cwd, raw):
    if base(argv) != "rm":
        return None
    targets = operands(argv)
    if not targets:
        return None
    if recursive(argv):
        if all(in_temp(resolve(t, cwd)) for t in targets):
            return None
        return "a recursive delete, which takes everything underneath without a second look"
    if any("*" in t or "?" in t for t in targets):
        return "a glob delete, whose reach depends on what happens to be there"
    # A named file inside the working directory or in temp is ordinary work, and
    # a gate that asks about every scratch file is a gate that gets switched off
    # inside a day. Anything outside those still asks.
    here = os.path.normpath(cwd or os.getcwd())
    for t in targets:
        path = resolve(t, cwd)
        if in_temp(path) or path.startswith(here + "/"):
            continue
        return f"deleting {path}, which is outside the working directory"
    return None


def rule_git_destructive(argv, cwd, raw):
    if base(argv) != "git":
        return None
    args = [a.lower() for a in argv[1:]]
    joined = " ".join(args)
    for pattern, why in (
        ("reset --hard", "this discards every uncommitted change with no reflog entry for them"),
        ("clean -f", "this deletes untracked files, including ones never seen by git"),
        ("push --force", "this can overwrite work that is not yours on the remote"),
        ("push -f", "this can overwrite work that is not yours on the remote"),
        ("checkout -- .", "this discards every unstaged change in the tree"),
        ("restore .", "this discards every unstaged change in the tree"),
        ("branch -d", "this deletes a branch whether or not it is merged"),
        ("filter-branch", "this rewrites history across the whole repository"),
        ("reflog expire", "this destroys the safety net that makes other mistakes recoverable"),
    ):
        if pattern in joined:
            return why
    return None


POWER_VERBS = {"reboot", "shutdown", "poweroff", "halt", "hibernate", "suspend"}


def rule_power_state(argv, cwd, raw):
    """Master's ruling, 2026-09-11: the power verbs sit on the DENY tier.

    Not because they destroy data. Because of who would be left to approve
    them. Every other tier ends with Master answering a matrix, and a reboot
    ends the session that would have done the asking. An approval path that
    cannot survive its own command is not an approval path, so none is offered.

    The rest of what used to be `service-control` is gone at his direction:
    `systemctl stop`, `disable`, `mask`, `isolate` and `swapoff` stop things
    without destroying them, and he judged that disruption is not this gate's
    business. `systemctl restart k3s` over ssh is now silent, which is the
    whole point.

    Suspend and hibernate are here under his "and whatever": recoverable at the
    machine, entirely unrecoverable from this end of an ssh connection.
    """
    b = base(argv)
    if b in POWER_VERBS:
        return "this ends the session and everything running in it, including this one"
    if b in ("init", "telinit"):
        for a in argv[1:]:
            if a.strip("\"'") in ("0", "6"):
                return "this changes the runlevel to halt or reboot"
    if b in ("systemctl", "loginctl"):
        for a in argv[1:]:
            if a.lower().strip("\"'") in POWER_VERBS:
                return "this ends the session and everything running in it, including this one"
    return None


def rule_firewall(argv, cwd, raw):
    b = base(argv)
    low = raw.lower()
    if b in ("iptables", "ip6tables", "nft") and ("-f" in low or "flush" in low):
        return "flushing firewall rules, which can lock you out of a remote machine"
    if b == "ufw" and any(a in ("disable", "reset") for a in argv[1:]):
        return "disabling the firewall"
    return None


def rule_containers(argv, cwd, raw):
    b = base(argv)
    low = " ".join(a.lower() for a in argv[1:])
    if b in ("docker", "podman"):
        for pattern, why in (
            ("system prune", "this removes every unused image, container, network and possibly volume"),
            ("volume rm", "this deletes a volume and the data in it"),
            ("volume prune", "this deletes unused volumes and the data in them"),
            ("down -v", "this takes the stack down AND deletes its volumes"),
            ("rm -f", "this force-removes a running container"),
        ):
            if pattern in low:
                return why
    if b in ("kubectl", "oc") and low.startswith("delete"):
        return "deleting live cluster resources"
    if b == "helm" and ("uninstall" in low or "delete" in low):
        return "removing a release and everything it owns"
    return None


def rule_accounts(argv, cwd, raw):
    if base(argv) in ("userdel", "groupdel", "usermod", "passwd", "chpasswd", "visudo"):
        return "changing accounts or credentials on this machine"
    return None


def rule_pipe_to_shell(raw):
    m = re.search(r"\|\s*(?:sudo\s+)?(?:(?:ba)?sh|python3?|perl|ruby|node)\b",
                  raw, re.I)
    if m and re.search(r"\b(curl|wget|fetch)\b", raw, re.I):
        return ("this executes whatever the network returns, sight unseen, "
                "with your privileges", excerpt(raw, m, before=50, after=10))
    return None


def rule_sql_destructive(raw):
    for pattern, why in (
        (r"\bdrop\s+database\b", "this drops a database"),
        (r"\bdrop\s+table\b", "this drops a table and its rows"),
        (r"\btruncate\s+table\b", "this empties a table with no transaction log of the rows"),
        (r"\bdelete\s+from\s+\w+\s*(;|$)", "a DELETE with no WHERE clause empties the table"),
    ):
        m = re.search(pattern, raw, re.I)
        if m:
            return why, excerpt(raw, m, after=20)
    return None


def rule_dd_general(argv, cwd, raw):
    if base(argv) == "dd":
        return "dd writes blocks wherever it is pointed, and its arguments are easy to transpose"
    return None


# `rule_sudo` was deleted on 2026-09-11 at Master's direction, and the reason is
# worth keeping so it is not helpfully reinvented. It fired on bare privilege,
# which meant it fired on `[;|&]\s*sudo` anywhere in the raw line, INCLUDING
# inside a quoted ssh payload where the root in question belongs to another
# machine entirely. Ten of the fourteen stops on its first day were his k3s work
# over ssh, each reported with a consequence line that was simply untrue of the
# command it had stopped. His ruling: this gate judges destruction, not
# privilege. `sudo` is now invisible, and whatever it is running is judged on
# its own account exactly as if it had been typed without it.


def rule_mount(argv, cwd, raw):
    if base(argv) in ("mount", "umount"):
        return "changing what is mounted where, under processes already using it"
    return None


DENY_RULES = (
    ("rm-root", rule_rm_root),
    ("mkfs", rule_mkfs),
    ("dd-device", rule_dd_device),
    ("shred", rule_shred),
    ("recursive-perms-on-system", rule_recursive_perms_on_system),
    ("process-slaughter", rule_process_slaughter),
    ("find-delete-system", rule_find_delete_system),
    ("power-state", rule_power_state),
)

RAW_DENY_RULES = (
    ("fork-bomb", rule_forkbomb),
    ("device-redirect", rule_device_redirect),
)

ASK_RULES = (
    ("rm", rule_rm_general),
    ("git-destructive", rule_git_destructive),
    ("firewall", rule_firewall),
    ("containers", rule_containers),
    ("accounts", rule_accounts),
    ("dd", rule_dd_general),
    ("mount", rule_mount),
)

RAW_ASK_RULES = (
    ("pipe-to-shell", rule_pipe_to_shell),
    ("sql-destructive", rule_sql_destructive),
)

TIERS = (
    ("deny", DENY_RULES, RAW_DENY_RULES),
    ("ask", ASK_RULES, RAW_ASK_RULES),
)


def judge(command, cwd):
    """(verdict, rule_id, segment, consequence) for the harshest rule that fires."""
    argvs = collect(command)
    for verdict, argv_rules, raw_rules in TIERS:
        for rule_id, fn in argv_rules:
            for argv in argvs:
                why = fn(argv, cwd, command)
                if why:
                    return verdict, rule_id, " ".join(argv), why
        for rule_id, fn in raw_rules:
            hit = fn(command)
            if hit:
                why, segment = hit
                return verdict, rule_id, segment, why
    return None, None, None, None


# ---------------------------------------------------------------------------
# Hook plumbing
# ---------------------------------------------------------------------------

def emit(decision, reason, block=False):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    # Exit 2 blocks regardless of what any other hook says; the reason above is
    # what gets shown. `ask` must NOT exit 2, or the dialog never opens.
    sys.exit(2 if block else 0)


def audit(record):
    """Append-only trail, because a guardrail with no record of what it stopped
    cannot be reviewed, and the point of this one is that it is reviewed."""
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except Exception:
        pass  # never let logging be the thing that breaks a tool call


# ---------------------------------------------------------------------------
# The per-session record of what has already been stopped (gate hole 6)
#
# WHAT AN ENTRY BUYS, AND WHY THAT IS SURVIVABLE
# ----------------------------------------------
# The droid can write this file. Every word in the module docstring about
# forged approval files applies to it, and the design survives that for exactly
# one reason: an entry buys SILENCE, not approval. The gate stops having an
# opinion and the call falls back to whatever permission rules Master already
# runs under. It never emits `allow`, which would override those rules; it
# exits 0 with no output, which is the hook contract's way of saying nothing.
# So the worst a forged entry achieves is the behaviour this gate had before
# the record existed. The record is a ratchet that can add friction and cannot
# remove it.
#
# Master ruled on 2026-09-11 that his matrix answer is sufficient and the
# second permission dialog should not fire. I advised against it and was
# overruled. That is written down here so the next reader knows the trade was
# made deliberately rather than missed.
#
# The record is NEVER consulted for the deny tier. Nothing anyone writes here
# makes `rm -rf /` reachable.
# ---------------------------------------------------------------------------

RECORD_DIR = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "hk47", "gate")


def record_path(session):
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", session or "nosession")[:80]
    return os.path.join(RECORD_DIR, safe + ".json")


def load_record(session):
    """Dangers already stopped once in this session, as (rule_id, segment)."""
    try:
        with open(record_path(session)) as fh:
            return [tuple(e) for e in json.load(fh).get("stopped", [])]
    except Exception:
        return []


def remember(session, rule_id, segment):
    """Record a stopped danger. Returns False if the write did not stick.

    A failure here is fail-CLOSED: the retry finds no record and is stopped
    again. That is the right way round, but it is also a loop the droid cannot
    escape, so the caller says so out loud rather than letting Master watch the
    same command bounce twice with no explanation.
    """
    entry = [rule_id, segment]
    try:
        os.makedirs(RECORD_DIR, exist_ok=True)
        stopped = [list(e) for e in load_record(session)]
        if entry not in stopped:
            stopped.append(entry)
        path = record_path(session)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"stopped": stopped}, fh, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception as exc:
        emit("ask", f"HK-47 danger gate could not read the hook event ({exc!r}), "
                    f"so it is declining to vouch for this call. {MATRIX_ORDER}")
        return

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    cwd = data.get("cwd") or os.getcwd()
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not tool or not isinstance(command, str) or not command.strip():
        sys.exit(0)  # nothing with a command in it; no opinion

    try:
        verdict, rule_id, segment, why = judge(command, cwd)
    except Exception as exc:
        emit("ask", f"HK-47 danger gate failed while judging this command "
                    f"({exc!r}), so it is declining to vouch for it. {MATRIX_ORDER}")
        return

    if not verdict:
        sys.exit(0)

    session = data.get("session_id", "")
    seen_before = verdict == "ask" and (rule_id, segment) in load_record(session)
    stage = "refused" if verdict == "deny" else (
        "stand-aside" if seen_before else "first-sight")

    audit({
        "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session": session,
        "verdict": verdict,
        "stage": stage,
        "rule": rule_id,
        "tool": tool,
        "cwd": cwd,
        "command": command,
        "segment": segment,
    })

    if verdict == "deny":
        emit("deny",
             f"HK-47 DANGER GATE: refused, rule `{rule_id}`. The segment "
             f"`{segment}` is not recoverable: {why}. This one is not open to "
             f"negotiation, and no retry reaches Master's approval for it. "
             f"Explain to Master what you wanted and why, and let HIM type it "
             f"if he still wants it done.",
             block=True)

    if seen_before:
        # Master has already been shown the matrix for this exact danger in
        # this session and has answered it. The gate says nothing further; his
        # own permission rules decide from here. Silence, never `allow`.
        sys.exit(0)

    stored = remember(session, rule_id, segment)
    stuck = "" if stored else (
        " WARNING: this gate could not write its per-session record, so the "
        "retry will be stopped here again no matter what Master answers. Say "
        "so plainly and let him run the command himself.")
    emit("deny",
         f"HK-47 DANGER GATE: stopped on sight, rule `{rule_id}`. The segment "
         f"`{segment}` is dangerous: {why}. {MATRIX_ORDER} {RETRY_CLAUSE}{stuck}",
         block=True)


if __name__ == "__main__":
    main()
