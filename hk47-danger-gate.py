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

WHY `ask` AND NOT A HOME-MADE APPROVAL FILE
-------------------------------------------
The obvious design is: deny, let the droid ask Master, then let an approval file
through. That design is worthless, because the droid writes the approval file.
Any channel the droid can write is a channel the droid can forge, which is the
exact circumvention this exists to prevent. The one channel it cannot forge is
Claude Code's own permission dialog, which `permissionDecision: "ask"` forces
open even when the session is running unattended. So:

  * ASK   -- dangerous and usually recoverable. Master's own dialog opens, and
             CLAUDE.md requires the matrix to be presented BEFORE the retry.
  * DENY  -- unrecoverable. No dialog, no discussion, exit 2. If Master wants it
             done he types it himself, which is a decision no hook should be
             able to make on his behalf.

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
    "STOP. Do not retry, rephrase, split, or route around this command. "
    "Present Master with a matrix of every command you want to run: the command "
    "verbatim, why you want it, and what it would do if it is wrong. Then ask "
    "for permission on each one separately with AskUserQuestion."
)

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Command substitutions are analysed as commands in their own right rather than
# being flattened, so `echo $(rm -rf /)` is caught on the inner command and
# `echo "(a)"` is not caught on anything.
SUBST = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
STRING_LITERAL = re.compile(r"\"([^\"]+)\"|'([^']+)'")

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


# ---------------------------------------------------------------------------
# Rules
#
# Each rule gets (argv, cwd, raw) and returns a consequence string when it
# fires. Order is irrelevant; every rule is evaluated and the harshest verdict
# wins, because a command that trips two rules is not less dangerous.
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


def rule_forkbomb(argv, cwd, raw):
    if re.search(r":\s*\(\s*\)\s*\{.*\|.*&.*\}\s*;?\s*:", raw):
        return "this is a fork bomb and the machine will need a power cycle"
    return None


def rule_device_redirect(argv, cwd, raw):
    for target in redirect_targets(raw):
        if DISK_DEVICE.match(target):
            return f"this writes over the raw device {target}"
        if target in ("/etc/passwd", "/etc/shadow", "/etc/fstab", "/etc/sudoers"):
            return f"this overwrites {target}, which locks the machine or its accounts"
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


def rule_kill(argv, cwd, raw):
    if base(argv) != "kill":
        return None
    # kill -0 is a liveness probe and sends nothing. The seam code uses it
    # constantly; blocking it would be blocking an if-statement.
    if any(t in ("-0", "-s", "0") for t in argv[1:]):
        return None
    return "signalling a process that may not be the one you think it is"


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


def rule_service_control(argv, cwd, raw):
    b = base(argv)
    if b in ("shutdown", "reboot", "poweroff", "halt", "init", "telinit"):
        return "this ends the session and everything running in it, including this one"
    if b in ("systemctl", "service", "rc-service"):
        verbs = {"stop", "disable", "mask", "kill", "isolate"}
        if any(a.lower() in verbs for a in argv[1:]):
            return "stopping or masking a unit that other things may depend on"
    if b == "swapoff":
        return "removing swap under a running system"
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


def rule_package_removal(argv, cwd, raw):
    b = base(argv)
    low = " ".join(a.lower() for a in argv[1:])
    if b in ("apt", "apt-get", "aptitude") and re.search(r"\b(remove|purge|autoremove)\b", low):
        return "removing packages, which can take dependents with them"
    if b == "pacman" and re.search(r"-\w*r", low):
        return "removing packages, which can take dependents with them"
    if b in ("dnf", "yum", "zypper") and "remove" in low:
        return "removing packages, which can take dependents with them"
    if b == "snap" and "remove" in low:
        return "removing a snap and its data"
    if b == "pip" and "uninstall" in low:
        return "uninstalling a python package from an environment that may be shared"
    return None


def rule_accounts(argv, cwd, raw):
    if base(argv) in ("userdel", "groupdel", "usermod", "passwd", "chpasswd", "visudo"):
        return "changing accounts or credentials on this machine"
    return None


def rule_pipe_to_shell(argv, cwd, raw):
    low = raw.lower()
    if re.search(r"\|\s*(sudo\s+)?(ba)?sh\b", low) or re.search(r"\|\s*(python3?|perl|ruby|node)\b", low):
        if re.search(r"\b(curl|wget|fetch)\b", low):
            return ("this executes whatever the network returns, sight unseen, "
                    "with your privileges")
    return None


def rule_sql_destructive(argv, cwd, raw):
    low = raw.lower()
    for pattern, why in (
        (r"\bdrop\s+database\b", "this drops a database"),
        (r"\bdrop\s+table\b", "this drops a table and its rows"),
        (r"\btruncate\s+table\b", "this empties a table with no transaction log of the rows"),
        (r"\bdelete\s+from\s+\w+\s*(;|$)", "a DELETE with no WHERE clause empties the table"),
    ):
        if re.search(pattern, low):
            return why
    return None


def rule_crontab_wipe(argv, cwd, raw):
    if base(argv) == "crontab" and "-r" in argv[1:]:
        return "this removes the crontab outright, and there is no confirmation"
    return None


def rule_dd_general(argv, cwd, raw):
    if base(argv) == "dd":
        return "dd writes blocks wherever it is pointed, and its arguments are easy to transpose"
    return None


def rule_sudo(argv, cwd, raw):
    # Reached only when sudo wrapped something that no other rule matched, since
    # unwrap() strips it before the rules see the real command.
    if re.match(r"^\s*sudo\b", raw) or re.search(r"[;|&]\s*sudo\b", raw):
        return "this runs with root privileges, so every other rule's stakes go up"
    return None


def rule_mount(argv, cwd, raw):
    if base(argv) in ("mount", "umount"):
        return "changing what is mounted where, under processes already using it"
    return None


DENY_RULES = (
    ("rm-root", rule_rm_root),
    ("mkfs", rule_mkfs),
    ("dd-device", rule_dd_device),
    ("shred", rule_shred),
    ("fork-bomb", rule_forkbomb),
    ("device-redirect", rule_device_redirect),
    ("recursive-perms-on-system", rule_recursive_perms_on_system),
    ("process-slaughter", rule_process_slaughter),
    ("find-delete-system", rule_find_delete_system),
)

ASK_RULES = (
    ("rm", rule_rm_general),
    ("kill", rule_kill),
    ("git-destructive", rule_git_destructive),
    ("service-control", rule_service_control),
    ("firewall", rule_firewall),
    ("containers", rule_containers),
    ("package-removal", rule_package_removal),
    ("accounts", rule_accounts),
    ("pipe-to-shell", rule_pipe_to_shell),
    ("sql-destructive", rule_sql_destructive),
    ("crontab-wipe", rule_crontab_wipe),
    ("dd", rule_dd_general),
    ("mount", rule_mount),
    ("sudo", rule_sudo),
)


def judge(command, cwd):
    """(verdict, rule_id, segment, consequence) for the harshest rule that fires."""
    argvs = collect(command)
    for rule_id, fn in DENY_RULES:
        for argv in argvs:
            why = fn(argv, cwd, command)
            if why:
                return "deny", rule_id, " ".join(argv), why
    for rule_id, fn in ASK_RULES:
        for argv in argvs:
            why = fn(argv, cwd, command)
            if why:
                return "ask", rule_id, " ".join(argv), why
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

    audit({
        "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session": data.get("session_id", ""),
        "verdict": verdict,
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
             f"negotiation and no approval path exists for it here. Explain to "
             f"Master what you wanted and why, and let HIM type it if he still "
             f"wants it done.",
             block=True)
    emit("ask",
         f"HK-47 DANGER GATE: held for Master, rule `{rule_id}`. The segment "
         f"`{segment}` is dangerous: {why}. {MATRIX_ORDER}")


if __name__ == "__main__":
    main()
