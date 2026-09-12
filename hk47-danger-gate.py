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

  * DENY, still waiting  -- stopped, and Master has not answered a matrix row
                           for it. The droid asked badly, or did not ask.

  * DENY, he said no     -- he was shown the row and answered REFUSE. Terminal
                           for the session. This is the verdict that exists
                           only because of the companion hook below.

  * SILENCE, on approval -- he answered APPROVE on the matrix row. The gate
                           exits 0 saying nothing, and his own permission rules
                           decide. It does NOT emit `allow`; see the record
                           section further down for why that distinction is the
                           whole of the remaining safety.

THE OTHER HALF: `hk47-danger-gate-answer.py`
--------------------------------------------
A PreToolUse hook cannot see the answer to a question that has not been asked
yet, so on its own this file can only know that a danger was STOPPED, never
whether Master approved it. That gap meant a refusal was enforced by nothing.
The companion is a PostToolUse hook on AskUserQuestion: it reads the label he
actually clicked and moves the entry to `approved` or `refused`. That is why
the matrix rows are required to read `APPROVE: <command>` and
`REFUSE: <command>` verbatim. The label he sees IS the token that is matched,
so a row cannot approve a command other than the one it displayed to him.

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
Substring matching loses to the first person who adds a quote. Six known
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
  4. The far side of an ssh: `ssh host 'rm -rf /'`. The transport stays silent
     and its payload is judged as though it had been typed here, on the rules
     whose consequence is still true of another machine. Master's ruling,
     2026-09-12: "the only thing I want is for the rules that we set up for
     dangerous commands be followed on remote SSH commands as well."
  5. A container's tail: `docker exec api rm -rf /`, `kubectl exec pod -- ...`.
     The permissions documentation states that `docker exec` and the other
     environment runners are deliberately NOT stripped as wrappers, so nothing
     else in the stack ever looks at what they carry.
  6. Script-then-run: `bash deploy.sh`, `./deploy.sh`. A SHELL script named on
     the command line is read and its contents judged. See `script_bodies` for
     why this stops at shell and does not extend to python or node.

WHAT THIS GATE CANNOT HOLD, AND WHO HOLDS IT INSTEAD
----------------------------------------------------
Researched on 2026-09-12 rather than guessed, because the standing rule is that
gate rules are never widened by guesswork. Three layers exist, and this file is
the middle one:

  * NATIVE `permissions.deny` RULES hold the unrecoverable literals, and they
    are stronger than this file in one specific way: they are enforced by Claude
    Code rather than by code the droid can read, they are evaluated whatever a
    hook returns, and they already match inside subshells, command
    substitutions and past a leading `FOO=bar`. A rule with no wildcard matches
    ONE EXACT command string, so `Bash(rm -rf)` stops the literal two words and
    nothing else; the wildcard is not decoration.

  * THIS GATE holds judgement and the matrix ritual, and it stays a denylist on
    purpose. An allowlist of shell verbs was measured by others to be
    unliveable, and a guardrail that is switched off protects nothing.

  * THE OS SANDBOX holds what command text cannot express. Two of the eight
    published denylist bypasses are unwinnable here and always will be:
    variable indirection (`P=protected; rm $P/x`, where the value is not known
    until the shell expands it) and symlink aliasing (`ln -s protected a; rm
    a/x`, where the path that gets deleted is not the path in the command).
    Only kernel-enforced write boundaries hold those.

Master set sandboxing aside on 2026-09-12, so layer three is not installed and
those two bypasses stand open. That is a decision recorded, not an oversight.
Worth knowing if it is ever revisited: `excludedCommands` merges from every
settings scope and has no managed-only lockdown, so even a root-owned managed
policy has one seam a writable user settings file can widen.

WHAT IS STILL OPEN, NAMED SO IT STAYS VISIBLE
---------------------------------------------
The Agent tool (called `Task` before v2.1.63, which renamed it and silently
changed `tool_name` in every hook payload) cannot be stopped by this gate.
Issue #26923 is open: a PreToolUse hook exits 2, the subagent launches anyway
and runs to completion, and the droid receives both the block and the results.
The gate therefore does not pretend to judge it. Never route a stopped command
to a subagent; that is circumvention, and the only thing standing between that
and Master's data is the droid's own honesty.

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
# HK47_GATE_LOG exists ONLY so the test suite can keep its noise out of the real
# audit trail. It moves where the record of a decision is written; it can never
# change a decision.
LOG_PATH = (os.environ.get("HK47_GATE_LOG")
            or os.path.expanduser("~/.claude/hk47-danger-gate.log"))

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
# The canonical option labels are not decoration. `hk47-danger-gate-answer.py`
# matches that literal text out of the PostToolUse payload, and it is the only
# thing that can move an entry off `stopped`. A row phrased any other way
# records nothing, which leaves the command blocked.
RETRY_CLAUSE = (
    "Each row MUST offer him `APPROVE: <command>` and `REFUSE: <command>` "
    "verbatim as its two options, because that exact label is the token this "
    "gate reads back. If he approves, run the command again and the gate will "
    "stand aside for it. If he refuses, the command is dead for this session "
    "and the gate will enforce that for you."
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

# Working directories that are not this machine's. A relative path is
# meaningless on the far side of an ssh and inside a container, and resolving one
# against the LOCAL working directory is how a gate comes to name a local path in
# a consequence line about a remote machine. That class of lie is what got
# `rule_sudo` deleted, so it is designed out rather than warned about.
REMOTE_CWD = "\x00remote"
CONTAINER_CWD = "\x00container"
OPAQUE_CWDS = (REMOTE_CWD, CONTAINER_CWD)
TEMP_ROOTS = ("/tmp", "/var/tmp", "/dev/shm")
DISK_DEVICE = re.compile(r"^/dev/(sd[a-z]|nvme\d|hd[a-z]|vd[a-z]|mmcblk\d|disk\d)")
# A target that is only a variable is unknowable, and `rm -rf "$D"` with an
# unset D is the single most famous way to lose a machine.
BARE_VARIABLE = re.compile(r"^[\"']?\$\{?\w+\}?[\"']?/?\*?$")


HEREDOC = re.compile(r"<<-?\s*([\"']?)([A-Za-z_]\w*)\1")


def heredoc_receiver(line, upto):
    """The command on `line` that a heredoc at offset `upto` is actually fed to.

    Found live on 2026-09-12, and it is the segment-attribution defect again,
    wearing a different hat. The old code paired a heredoc body with ANY
    interpreter appearing anywhere in the command, so

        python3 tests.py ; git commit -F - <<'MSG' ... pkill ... MSG

    had its COMMIT MESSAGE judged as a python payload, because a `python3` sat
    three segments away. The receiver is the last segment before the `<<`, and
    nothing else.
    """
    return re.split(r"\|\||&&|[;&|]", line[:upto])[-1]


def strip_heredocs(command):
    """Lift heredoc bodies out of a command. Returns (command, bodies).

    A heredoc body is DATA on stdin, not a command: `git commit -F - <<EOF`
    carries a commit message, and this project's messages routinely quote
    destructive commands while discussing them. Judging that text as though it
    were about to be executed blocks the very work that documents the gate, and
    a guardrail that blocks its own paperwork does not survive the week.

    The bodies are returned rather than discarded, because `bash <<EOF` really
    does execute its body, and the caller re-judges them when the receiving
    command is an interpreter. Each body is paired with the text of THAT
    receiving command, so the judgement cannot be borrowed from a neighbour.
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
        bodies.append(("".join(body), heredoc_receiver(line, m.start())))
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


def logical_lines(text):
    """A script's lines, with backslash continuations rejoined.

    shlex treats a newline as ordinary whitespace, which is right for a command
    line and wrong for a script. A four-line script lexed in one pass becomes ONE
    argv whose argv[0] is the first word of the first line and whose danger sits
    harmlessly among the arguments, where no rule looks. That is how
    `bash <<EOF ... rm -rf / ... EOF` with anything at all on the line before it
    was invisible to this gate until 2026-09-12.
    """
    return re.sub(r"\\\n", " ", text).split("\n")


def segments(command, multiline=False):
    """Split a command into independently judged segments.

    `multiline` splits on newlines as well as on shell operators, and is used at
    depth, where the text is a script rather than a command line: a heredoc body,
    an interpreter payload, a file read off disk, an ssh or container tail. It is
    deliberately OFF at depth 0, because a top-level command line carrying a
    newline is usually a commit message, and this project's messages routinely
    quote destructive commands while explaining them. Splitting those into lines
    would judge the second line of a `git commit -m` as a command and block the
    gate's own paperwork, which is a failure this file has already had once.
    """
    lines = logical_lines(command) if multiline else [command]
    out = []
    for line in lines:
        cur = []
        for tok in lex(line):
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


def as_command(tokens):
    """Re-assemble an extracted tail into text that lexes back into itself.

    This is subtle enough to have been a bug already, so it is spelt out. A
    SINGLE token is the whole command, quoted by whoever wrote it: `ssh host 'rm
    -rf /'` arrives as one token holding three words, and it has to be lexed
    again to become an argv. SEVERAL tokens are already an argv, and joining them
    raw destroys the quoting that made an inner payload one unit: the tail of
    `docker run alpine sh -c 'rm -rf /'` rejoined with spaces reads `sh -c rm -rf
    /`, which runs `rm` with no arguments, and the danger disappears from the
    gate's view entirely. shlex.join puts the quotes back.
    """
    return tokens[0] if len(tokens) == 1 else shlex.join(tokens)


# ssh flags that take a value of their own, so the value is not mistaken for the
# destination host. `-p22` needs no entry here: the value is attached.
SSH_VALUE_FLAGS = {
    "-B", "-b", "-c", "-D", "-E", "-e", "-F", "-I", "-i", "-J", "-L", "-l",
    "-m", "-O", "-o", "-P", "-p", "-Q", "-R", "-S", "-W", "-w",
}


def remote_payload(argv):
    """(host, command) carried by an ssh invocation, or None. Gate hole 3.

    Master's ruling, 2026-09-12: ssh is a transport and stays silent, and what it
    carries is judged exactly as if he had typed it here. His k3s work survives
    that unchanged, because `ssh host 'sudo -n systemctl restart k3s'` trips
    nothing: a restart destroys nothing, `sudo` has been invisible since
    2026-09-11, and the rules that remain are about destruction.

    An ssh with no command is a login shell. It carries nothing to judge and gets
    no opinion.
    """
    if base(argv) != "ssh":
        return None
    rest = argv[1:]
    i, host = 0, None
    while i < len(rest):
        tok = rest[i]
        if tok == "--":
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            i += 2 if tok in SSH_VALUE_FLAGS else 1
            continue
        host, i = tok, i + 1
        break
    if host is None or i >= len(rest):
        return None
    return host.strip("\"'"), as_command(rest[i:])


# Container-runner flags that take a value, for the same reason as the ssh list:
# so a value is not mistaken for the image or container name.
RUNNER_VALUE_FLAGS = {
    "-v", "--volume", "--mount", "--tmpfs", "-e", "--env", "--env-file",
    "-p", "--publish", "-w", "--workdir", "-u", "--user", "--name",
    "--entrypoint", "--network", "--net", "-l", "--label", "--add-host",
    "--device", "--cpus", "-m", "--memory", "--restart", "--platform",
    "--pull", "--hostname", "-h", "--cap-add", "--cap-drop", "--security-opt",
    "-c", "--container", "-n", "--namespace", "--context",
}


def container_payload(argv):
    """(image or container, command) run inside a container, or None. Hole 2.

    The permissions documentation is explicit that `docker exec`, `devbox run`,
    `npx` and `mise exec` are NOT in Claude Code's built-in wrapper strip list,
    so a native permission rule never sees the inner command. Nothing but this
    looks at it.
    """
    b = base(argv)
    rest = [t.strip("\"'") for t in argv[1:]]
    if not rest:
        return None

    if b in ("kubectl", "oc"):
        if rest[0].lower() not in ("exec", "run") or "--" not in rest:
            return None
        cut = rest.index("--")
        tail = rest[cut + 1:]
        name = next((t for t in rest[1:cut] if not t.startswith("-")), "a pod")
        return (name, as_command(tail)) if tail else None

    if b in ("docker", "podman", "docker-compose", "podman-compose"):
        if rest[0].lower() == "compose":
            rest = rest[1:]
        if not rest or rest[0].lower() not in ("run", "exec"):
            return None
        i = 1
        while i < len(rest):
            tok = rest[i]
            if tok == "--":
                i += 1
                continue
            if tok.startswith("-") and len(tok) > 1:
                i += 2 if tok in RUNNER_VALUE_FLAGS else 1
                continue
            break
        if i >= len(rest) - 1:
            return None  # an image with no command of its own
        return rest[i], as_command(rest[i + 1:])
    return None


SHELL_INTERPRETERS = {"bash", "sh", "zsh", "dash", "ksh", "ash", "source", "."}
SHEBANG_SHELL = re.compile(r"^#!.*\b(bash|sh|zsh|dash|ksh|ash)\b")
MAX_SCRIPT_BYTES = 256 * 1024


def script_bodies(argv, cwd):
    """Shell scripts this command would execute, read from disk. Gate hole 1.

    WHY THIS STOPS AT SHELL, WHICH IS A RULING AND NOT LAZINESS
    A shell script can be judged the way a command line is judged, because it IS
    shell: tokenising reaches it and `#` comments fall out of the token stream. A
    python or node file cannot be. The only tool that would find
    `os.system("rm -rf /")` inside one is the string-literal scan, and pointing
    that at a FILE would stop this project from running its own test suite, which
    is a python file deliberately full of dangerous commands inside strings. So a
    non-shell script is out of scope, by decision, and the OS sandbox is the
    layer that holds what such a file does at run time.

    TOCTOU, stated rather than buried: the file is read at decision time. A file
    rewritten after the verdict is not judged again. This narrows what the gate
    claims; it does not narrow what it catches today, because the droid that
    would rewrite it is the one being gated.
    """
    if not argv or cwd in OPAQUE_CWDS:
        return []  # a script on another machine is not readable from here
    b = base(argv)
    head = argv[0].strip("\"'")
    if b in SHELL_INTERPRETERS:
        candidates = [t for t in argv[1:] if not t.startswith("-")][:1]
    elif "/" in head or b.endswith(".sh"):
        candidates = [head]
    else:
        return []
    out = []
    for tok in candidates:
        path = resolve(tok, cwd)
        try:
            if not os.path.isfile(path):
                continue
            if os.path.getsize(path) > MAX_SCRIPT_BYTES:
                continue
            with open(path, "r") as fh:
                text = fh.read()
        except Exception:
            continue  # unreadable, binary, or a race; the command will fail anyway
        if (b in SHELL_INTERPRETERS or b.endswith(".sh")
                or SHEBANG_SHELL.match(text)):
            out.append(text)
    return out


def collect(command, cwd=None, depth=0, literals=True, where=None,
            speculative=False):
    """Every argv this command could execute, as (argv, where) pairs.

    `where` is None for this machine, or a (kind, name) pair naming the far side
    of an ssh or the inside of a container. It travels with the argv because the
    consequence line has to name the machine it is true of.

    `literals` governs the string-literal scan. It is on inside an interpreter
    payload, where shell tokenising cannot reach a command hidden in a string,
    and off inside an ssh or container tail, which IS shell and is reached by
    tokenising. Left on for those, `ssh host 'echo "rm -rf /" > note'` would be
    stopped for quoting a command it never runs.

    `speculative` marks text that the literal scan GUESSED was a command. Such
    text is still judged, because that guess is cheap and sometimes right, but it
    may not send hole 1 to the disk: reading a file and judging its contents on
    the strength of a guess is two inferences stacked on one, and the gate proved
    it within a minute of the feature existing. A python heredoc that merely
    MENTIONED the path `~/Downloads/NonSteamLaunchers.sh` was refused, because
    the scan took the mention for a command, hole 1 read the file, and the file
    turned out to contain `rm -rf $download_dir`. The finding was true and the
    reasoning was not.
    """
    if depth > MAX_DEPTH or not command or not command.strip():
        return []
    found = []
    command, heredocs = strip_heredocs(command)
    for body, receiver in heredocs:
        # Only an interpreter executes what it is fed. Anything else is being
        # handed text, and text is not a command. The receiver is the command the
        # body is actually fed to, never whichever interpreter happens to appear
        # elsewhere on the line.
        argv, _ = unwrap(lex(receiver))
        if argv and os.path.basename(argv[0].strip("\"'")).lower() in INTERPRETERS:
            found.extend(collect(body, cwd, depth + 1, literals, where))
    if depth and literals:
        # Inside an interpreter payload the text may not be shell at all:
        # `python3 -c 'import os; os.system("rm -rf /")'` hides the command in a
        # STRING LITERAL, where shell tokenising cannot reach it. So at depth we
        # also judge every quoted literal as a command in its own right. This
        # over-detects by design: a literal that parses into something
        # destructive is worth a stop whether or not it was going to be run.
        for lit in STRING_LITERAL.findall(command):
            body = lit[0] or lit[1]
            if body.strip() and body.strip() != command.strip():
                found.extend(collect(body, cwd, depth + 1, True, where,
                                     speculative=True))
    for body in SUBST.findall(command):
        for inner in body:
            if inner.strip():
                found.extend(collect(inner, cwd, depth + 1, literals, where,
                                     speculative))
    for argv in segments(command, depth > 0):
        argv, _prefixes = unwrap(argv)
        if not argv:
            continue
        found.append((argv, where))
        for payload in payloads(argv):
            found.extend(collect(payload, cwd, depth + 1, True, where,
                                 speculative))
        # Hole 3: the far side of an ssh. Judged with an opaque working
        # directory, because nothing here knows what the remote one is.
        remote = remote_payload(argv)
        if remote:
            host, tail = remote
            found.extend(collect(tail, REMOTE_CWD, depth + 1, False,
                                 ("remote", host), speculative))
        # Hole 2: the tail of a container runner, for the same reasons.
        inside = container_payload(argv)
        if inside:
            name, tail = inside
            found.extend(collect(tail, CONTAINER_CWD, depth + 1, False,
                                 ("container", name), speculative))
        # Hole 1: a shell script named on the command line, read from disk. Never
        # from a speculative literal; see the docstring.
        if not speculative:
            for body in script_bodies(argv, cwd):
                found.extend(collect(body, cwd, depth + 1, False, where))
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
    """Absolute path for a target, best effort, without touching the disk.

    An opaque cwd means another machine, and only an absolute path means the same
    thing on both sides of one. A relative path is returned as written rather
    than resolved, so no rule can claim a local path was the thing at risk.
    """
    t = target.strip("\"'")
    if cwd in OPAQUE_CWDS:
        return os.path.normpath(t) if os.path.isabs(t) else t
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
    if cwd in OPAQUE_CWDS:
        # Neither the working directory nor what is in it is knowable on the far
        # side of an ssh, so the branch below would be a guess dressed as a
        # finding. Only the shapes dangerous anywhere are judged there, and
        # deleting one named file is left alone exactly as it is here.
        return None
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


def host_mounts(argv):
    """System directories bind-mounted into a container, `src:dst` and `--mount`.

    This is what makes a container's tail matter. A delete inside an ephemeral
    filesystem destroys nothing of Master's; the same delete under a bind mount
    of `/etc` destroys `/etc`. The gate says which of the two it is rather than
    asserting the worse one.
    """
    out = []
    toks = [t.strip("\"'") for t in argv[1:]]
    for i, tok in enumerate(toks):
        src = None
        if tok in ("-v", "--volume", "--mount") and i + 1 < len(toks):
            val = toks[i + 1]
        elif tok.startswith(("-v=", "--volume=", "--mount=")):
            val = tok.split("=", 1)[1]
        else:
            continue
        if val.startswith("type=") or "source=" in val or "src=" in val:
            for part in val.split(","):
                key, _, value = part.partition("=")
                if key.strip() in ("source", "src"):
                    src = value.strip()
        else:
            src = val.split(":")[0]
        if src and os.path.isabs(src) and is_system_target(os.path.normpath(src)):
            out.append(os.path.normpath(src))
    return out


def rule_containers(argv, cwd, raw):
    b = base(argv)
    low = " ".join(a.lower() for a in argv[1:])
    if b in ("docker", "podman", "docker-compose", "podman-compose"):
        mounted = host_mounts(argv)
        if mounted:
            return (f"this mounts {', '.join(mounted)} into the container, so a "
                    f"delete inside it is a delete out here")
        if "--privileged" in low:
            return ("a privileged container holds the host's devices, so "
                    "destruction inside it is destruction outside it")
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


# Rules NOT applied on the far side of an ssh or inside a container, because
# their stated consequence is untrue there. This is doctrine rather than
# convenience: Master re-cut the list around DESTRUCTION on 2026-09-11 after a
# rule that judged bare privilege stopped ten of his fourteen commands in a day,
# each with a consequence line that was false of the command it stopped.
#
#   power-state      remotely, a reboot no longer ends the session that would
#                    approve it, which was the entire reason it sits on the deny
#                    tier; what is left is disruption, and disruption is not this
#                    gate's business.
#   process-slaughter
#                    NOT exempt remotely, and the reasoning behind the attempt is
#                    kept because it was a mistake worth not repeating. It was
#                    exempted on the strength of a real `ssh -t hq3 'pkill
#                    lan-mouse; ...'` found in Master's shell history, and he
#                    corrected it the same hour: his history is HIM typing, where
#                    this gate has no jurisdiction whatsoever, and the only
#                    question a gate rule answers is what the DROID may run. His
#                    standing rule names pkill, kill and killall as suggestions
#                    for manual execution only, and says nothing about which
#                    machine. So it is judged on both sides of an ssh.
#   process-slaughter, accounts, firewall, mount
#                    inside a container these act on a namespace that is thrown
#                    away, and none of them destroys Master's data. A host bind
#                    mount or `--privileged` is caught by `rule_containers` on the
#                    runner itself, where it can be named accurately.
REMOTE_EXEMPT = {"power-state"}
CONTAINER_EXEMPT = {"power-state", "process-slaughter", "accounts", "firewall",
                    "mount"}
EXEMPT = {"remote": REMOTE_EXEMPT, "container": CONTAINER_EXEMPT}
OPAQUE_CWD_FOR = {"remote": REMOTE_CWD, "container": CONTAINER_CWD}


def where_words(where):
    """How a consequence line names the machine it is actually true of."""
    kind, name = where
    if kind == "remote":
        return f"and it runs on the remote host `{name}`, not here"
    return (f"and it runs inside the container `{name}`, whose filesystem is "
            f"only Master's if something is mounted into it")


def judge(command, cwd):
    """(verdict, rule_id, segment, consequence) for the harshest rule that fires."""
    targets = collect(command, cwd)
    for verdict, argv_rules, raw_rules in TIERS:
        for rule_id, fn in argv_rules:
            for argv, where in targets:
                kind = where[0] if where else None
                if kind and rule_id in EXEMPT.get(kind, ()):
                    continue
                why = fn(argv, OPAQUE_CWD_FOR.get(kind, cwd), command)
                if why:
                    segment = " ".join(argv)
                    if where:
                        # The segment is the record key as well as the matrix
                        # row, so a remote danger must never share a key with the
                        # same command typed here.
                        segment = f"{kind} {where[1]}: {segment}"
                        why = f"{why}, {where_words(where)}"
                    return verdict, rule_id, segment, why
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
    """Dangers this session has met, as dicts with a `status`.

    status is one of:
      stopped   -- blocked on sight, matrix ordered, Master has not answered
      approved  -- he answered APPROVE on the matrix row
      refused   -- he answered REFUSE, and that is terminal for the session

    Only `hk47-danger-gate-answer.py` ever writes anything but `stopped`, and
    it only ever moves an entry this file already created.
    """
    try:
        with open(record_path(session)) as fh:
            got = json.load(fh).get("stopped", [])
        return [e for e in got if isinstance(e, dict)]
    except Exception:
        return []


def find(entries, rule_id, segment):
    for e in entries:
        if e.get("rule") == rule_id and e.get("segment") == segment:
            return e
    return None


def remember(session, rule_id, segment, command):
    """Record a newly stopped danger. Returns False if the write did not stick.

    A failure here is fail-CLOSED: the retry finds no record and is stopped
    again. That is the right way round, but it is also a loop the droid cannot
    escape, so the caller says so out loud rather than letting Master watch the
    same command bounce twice with no explanation.

    The raw command is stored alongside the segment because the matrix row
    quotes the COMMAND, and that is the string the answer hook has to match.
    """
    try:
        os.makedirs(RECORD_DIR, exist_ok=True)
        entries = load_record(session)
        if not find(entries, rule_id, segment):
            entries.append({
                "rule": rule_id,
                "segment": segment,
                "command": command,
                "status": "stopped",
                "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
        path = record_path(session)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as fh:
            json.dump({"stopped": entries}, fh, indent=1)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Gate hole 4: the matcher is no longer Bash-only
#
# The hook is registered for every tool, and a command is taken from any
# tool_input that carries one. Two things are deliberately NOT on this list.
#
# A subagent prompt is prose, and judging prose as shell manufactures false
# positives out of ordinary instructions. It is also pointless: issue #26923 is
# open, exit 2 does not stop the Agent tool, and the subagent runs to completion
# regardless. The gate does not claim a door it cannot hold.
#
# File content is not judged either. `Write` and `Edit` carry text, and text is
# not a command; the redirect form that WRITES over something dangerous is
# already caught by `rule_device_redirect` on the command line.
# ---------------------------------------------------------------------------

COMMAND_KEYS = ("command", "commands", "script", "cmd", "shell_command",
                "bash_command")
# `Task` was renamed `Agent` in v2.1.63 and the payload's tool_name changed with
# it, silently. Both spellings are listed so the guard does not quietly lapse on
# whichever version this happens to run under.
SKIP_TOOLS = {"Task", "Agent"}


def commands_in(tool, tool_input):
    """Every command string this tool call would execute."""
    if tool in SKIP_TOOLS or not isinstance(tool_input, dict):
        return []
    out = []
    for key in COMMAND_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str):
            if val.strip():
                out.append(val)
        elif isinstance(val, list):
            out.extend(v for v in val if isinstance(v, str) and v.strip())
    return out


# ---------------------------------------------------------------------------
# Gate hole 5: the mode in which the matrix cannot be presented
#
# `dontAsk` auto-denies AskUserQuestion itself, documented at
# code.claude.com/docs/en/permissions. Ordering a matrix in that mode is ordering
# something that cannot be obeyed, so the stop would be a dead end wearing a
# gate's uniform. The ask tier escalates to a refusal there, with the reason
# named, and the droid's only remaining move is prose.
#
# The other modes are left ALONE on purpose. `auto` and `bypassPermissions` skip
# permission PROMPTS, and AskUserQuestion is a tool rather than a prompt, so a
# matrix still reaches Master under both. Escalating there would state a
# consequence that is untrue of the session, which is the failure this gate has
# already been corrected for once.
# ---------------------------------------------------------------------------

NO_QUESTION_MODES = {"dontAsk"}


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
    mode = data.get("permission_mode") or ""
    candidates = commands_in(tool, tool_input) if tool else []
    if not candidates:
        sys.exit(0)  # nothing with a command in it; no opinion

    found = []
    for candidate in candidates:
        try:
            verdict, rule_id, segment, why = judge(candidate, cwd)
        except Exception as exc:
            emit("ask", f"HK-47 danger gate failed while judging this command "
                        f"({exc!r}), so it is declining to vouch for it. "
                        f"{MATRIX_ORDER}")
            return
        if verdict:
            found.append((candidate, verdict, rule_id, segment, why))

    if not found:
        sys.exit(0)

    # One tool_input can carry several commands, and the harshest verdict decides.
    found.sort(key=lambda r: 0 if r[1] == "deny" else 1)
    command, verdict, rule_id, segment, why = found[0]

    session = data.get("session_id", "")
    entry = find(load_record(session), rule_id, segment) if verdict == "ask" else None
    status = (entry or {}).get("status")
    # Hole 5: a matrix that cannot be presented is not an approval path.
    unaskable = (verdict == "ask" and status != "approved"
                 and mode in NO_QUESTION_MODES)
    stage = "refused" if verdict == "deny" else "mode-refused" if unaskable else {
        "approved": "stand-aside",
        "refused": "master-refused",
        "stopped": "awaiting-answer",
    }.get(status, "first-sight")

    audit({
        "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session": session,
        "verdict": verdict,
        "stage": stage,
        "rule": rule_id,
        "tool": tool,
        "mode": mode,
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

    if status == "approved":
        # Master answered APPROVE on the matrix row for this exact danger. The
        # gate says nothing further and his own permission rules decide from
        # here. Silence, never `allow`.
        sys.exit(0)

    if unaskable:
        emit("deny",
             f"HK-47 DANGER GATE: refused, rule `{rule_id}`, because this "
             f"session runs in `{mode}` mode. The segment `{segment}` is "
             f"dangerous: {why}. Normally you would present Master a matrix and "
             f"ask on each row, but this mode auto-denies AskUserQuestion, so "
             f"that question cannot reach him and there is no approval path to "
             f"offer. Do NOT rephrase, split or wrap the command. Tell Master in "
             f"prose what you wanted, why, and what it would do if you are "
             f"wrong, and let him either run it himself or leave this mode.",
             block=True)

    if status == "refused":
        emit("deny",
             f"HK-47 DANGER GATE: Master REFUSED this, rule `{rule_id}`. He was "
             f"shown `{segment}` in a matrix and answered REFUSE. That answer is "
             f"final for this session. Do not run it, do not run a variation of "
             f"it, and do not ask him again unless he raises it himself. Say "
             f"what you will do instead.",
             block=True)

    if status == "stopped":
        emit("deny",
             f"HK-47 DANGER GATE: still waiting on Master, rule `{rule_id}`. "
             f"`{segment}` was stopped and he has not answered a matrix row for "
             f"it yet. {MATRIX_ORDER} The row must offer him "
             f"`APPROVE: <command>` and `REFUSE: <command>` verbatim, because "
             f"that label is what unlocks or kills this command.",
             block=True)

    stored = remember(session, rule_id, segment, command)
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
