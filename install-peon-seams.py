#!/usr/bin/env python3
"""Teach peon-ping the eighteen HK-47 categories it does not route.

peon-ping already receives every hook event the pack needs: SessionStart carries
a source, SessionEnd fires, Notification distinguishes idle from elicitation,
SubagentStop is its own event, PostToolUseFailure carries the tool name. What it
does not do is give those events distinct category names, because upstream ships
a seven-category vocabulary and maps everything onto it.

So this is a re-mapping of peon.sh's routing chain, not new plumbing.

It is a patch to a vendored upstream file, which is a thing to do reluctantly and
reversibly. Hence the shape, copied from install-bar-identity.sh:

  * idempotent, so running it twice is a no-op
  * every edit carries an HK47SEAM marker, so "is it applied" is a grep
  * --revert restores the .orig taken on first application
  * `peon update` overwrites peon.sh, so re-run this afterwards

Anchors are matched exactly and must be unique. A patch whose anchor has moved
fails loudly rather than guessing, because a silent half-patch would leave the
routing chain in a state nobody designed.
"""

import os
import shutil
import sys

MARKER = "HK47SEAM"
INSTALLS = (".claude-personal", ".claude-work")

# peon.sh embeds its routing python in an UNQUOTED heredoc, so bash expands the
# body before python ever sees it. Nothing inserted below may contain a dollar
# sign or a backtick, which is why the tests are word lists rather than regexes.
PROMPT_CLASSIFIER = '''\
    # HK47SEAM: classify the prompt itself. Eight of the pack's categories are
    # told apart only by what Master typed, so this is the seam that gives them
    # a driver. Ordered most specific first, and the first hit wins. Every test
    # is deliberately narrow: a wrong guess here puts words in Master's mouth.
    if not category:
        _p = (event_data.get('prompt', '') or '').strip()
        _lp = _p.lower()
        _words = _lp.split()
        _first = _words[0].strip('.,!?:') if _words else ''

        def _any(needles):
            for _n in needles:
                if _n in _lp:
                    return True
            return False

        def _tasks(text):
            # Bullets and numbered items first, since a list is an explicit
            # count. Failing that, fall back to counting sequencing phrases.
            _n = 0
            for _line in text.splitlines():
                _s = _line.strip()
                if not _s:
                    continue
                if _s[0] in '-*' or (len(_s) > 1 and _s[0].isdigit() and _s[1] in '.)'):
                    _n += 1
            if _n:
                return _n
            return 1 + _lp.count(' and then ') + _lp.count(', then ')

        if _p.startswith('/'):
            category = 'skill.invoke'
        elif _any(('what do you think', 'your opinion', 'your take',
                   'how do you feel', 'do you agree', 'thoughts?')):
            category = 'user.opinion'
        elif _any(('idiot', 'stupid', 'moron', 'useless', 'garbage', 'pathetic',
                   'incompetent', 'worthless', 'rubbish', 'hopeless', 'dumb',
                   'shit', 'fuck', 'bloody hell')):
            # Split by target, which is the entire reason the category is split.
            category = ('user.insult.droid'
                        if _any(('you ', 'you.', 'you!', 'your ', 'yourself',
                                 'youre', "you're"))
                        else 'user.insult.world')
        elif _any(('i told you', 'you broke', 'you missed', 'you forgot',
                   'that is not', "that's not", 'not what i', 'wrong again',
                   'why did you', 'read it again', 'stop doing')):
            category = 'user.rebuke'
        elif _tasks(_p) >= 6:
            category = 'user.overload'
        elif _any(('delete', 'remove ', 'rm -', 'drop table', 'drop database',
                   'wipe ', 'purge ', 'get rid of', 'uninstall')):
            category = 'user.delete'
        elif len(_words) <= 4 and (
                _first in ('continue', 'proceed', 'next', 'yes', 'yep', 'ok',
                           'okay', 'sure', 'go', 'fine')
                or _any(('go on', 'keep going', 'carry on', 'go ahead', 'do it'))):
            category = 'user.continue'
        elif _p.endswith('?') or _first in (
                'what', 'why', 'how', 'when', 'where', 'who', 'which', 'is',
                'are', 'do', 'does', 'can', 'could', 'should', 'would', 'did'):
            category = 'user.question'

    # HK47SEAM: Master answering a question I put to him is the one observable
    # form of "the work was his". The flag is raised by the elicitation dialog
    # and cleared by the very next prompt, whatever that prompt turns out to be,
    # so a stale flag cannot leak into an unrelated exchange later. It sits below
    # the classifier on purpose: if he answered with an insult, the insult is the
    # truer reading of what just happened.
    _await = state.get('awaiting_master', {})
    if session_id in _await:
        del _await[session_id]
        state['awaiting_master'] = _await
        state_dirty = True
        if not category:
            category = 'task.complete.master'
            status = 'working'

    if not category and cat_enabled.get('task.acknowledge', False):
        category = 'task.acknowledge'
        status = 'working'\
'''

DANGER_CLASSIFIER = '''\
elif event in ('PreToolUse', 'PostToolUse'):
    # Tool use events indicate Claude is actively working — clear needs_approval tab color
    status = 'working'
    # HK47SEAM: PreToolUse carries two of the pack's categories.
    #
    # Measured 2026-09-10, against a live log, and it overturned a guess: asking
    # Master a question does NOT raise an elicitation Notification, and his
    # answer does NOT arrive as a UserPromptSubmit. The whole exchange is one
    # AskUserQuestion tool call. So the question is this PreToolUse, and the
    # answer is whatever tool call I make next.
    #
    # A destructive command earns a word before it runs, not after. PreToolUse
    # fires on every single tool call, so that arm stays silent unless a token
    # from the list actually appears in the command.
    if event == 'PreToolUse':
        _tn = event_data.get('tool_name', '')
        _ti = event_data.get('tool_input', {}) or {}
        _cmd = str(_ti.get('command', '') or '').lower() if isinstance(_ti, dict) else ''
        _await = state.get('awaiting_master', {})
        if _tn == 'AskUserQuestion':
            category = 'input.question'
            _await[session_id] = time.time()
            state['awaiting_master'] = _await
            state_dirty = True
        else:
            for _d in ('rm -rf', 'rm -fr', 'rm -r ', 'mkfs', 'dd if=', 'drop table',
                       'drop database', 'truncate table', 'git reset --hard',
                       'git push --force', 'git push -f', 'chmod 777', 'shutdown ',
                       'reboot', 'killall', 'pkill', 'userdel', 'iptables -f'):
                if _d in _cmd:
                    category = 'danger.command'
                    break
            # Any other tool call means the question was answered and I am moving
            # again. The flag clears either way, so a danger warning does not
            # leave it armed for the next unrelated call.
            if session_id in _await:
                del _await[session_id]
                state['awaiting_master'] = _await
                state_dirty = True
                if not category:
                    category = 'task.complete.master'\
'''

# (name, anchor, replacement). Order is irrelevant: anchors are disjoint.
PATCHES = [
    (
        "category registry",
        "for c in ['session.start','task.acknowledge','task.complete',"
        "'task.error','input.required','resource.limit','user.spam']:",
        "for c in ['session.start','task.acknowledge','task.complete',"
        "'task.error','input.required','resource.limit','user.spam',\n"
        "          # HK47SEAM: the pack's own categories, so they can be toggled\n"
        "          # like any other. Unlisted keys already default to enabled at\n"
        "          # the point of use, so this is for `peon config`, not routing.\n"
        "          'session.resume','session.end','session.idle','input.question',\n"
        "          'subagent.complete','tool.error','danger.command','skill.invoke',\n"
        "          'user.question','user.continue','user.opinion','user.delete',\n"
        "          'user.rebuke','user.overload','user.insult.world',\n"
        "          'user.insult.droid']:",
    ),
    (
        "session.resume",
        "    category = 'session.start'\n    status = 'ready'",
        "    # HK47SEAM: a resumed session is not a greeting, it is a recollection.\n"
        "    category = 'session.resume' if source == 'resume' else 'session.start'\n"
        "    status = 'ready'",
    ),
    (
        "session.idle",
        "    elif ntype == 'idle_prompt':\n        category = 'task.complete'",
        "    elif ntype == 'idle_prompt':\n"
        "        category = 'session.idle'  # HK47SEAM: idling is not completion",
    ),
    (
        "input.question",
        "    elif ntype == 'elicitation_dialog':\n        category = 'input.required'",
        "    elif ntype == 'elicitation_dialog':\n"
        "        category = 'input.question'  # HK47SEAM: a question, not a permission\n"
        "        # HK47SEAM: remember that a question is outstanding. The next\n"
        "        # prompt from Master is him answering it, which is the one\n"
        "        # observable form of work he had to do himself.\n"
        "        _await = state.get('awaiting_master', {})\n"
        "        _await[session_id] = time.time()\n"
        "        state['awaiting_master'] = _await\n"
        "        state_dirty = True",
    ),
    (
        "subagent.complete",
        "    # When not suppressed, fall through to sound logic as task.complete\n"
        "    category = 'task.complete'",
        "    # HK47SEAM: the subagent finished, which is not the same as the work\n"
        "    # being done, and deserves its own line.\n"
        "    category = 'subagent.complete'",
    ),
    (
        "input.declined",
        "elif event == 'PostToolUseFailure':\n"
        "    # Bash failures arrive here with error field (e.g. Exit code 1)\n"
        "    tool_name = event_data.get('tool_name', '')\n"
        "    error_msg = event_data.get('error', '')",
        "elif event == 'PostToolUseFailure':\n"
        "    # Bash failures arrive here with error field (e.g. Exit code 1)\n"
        "    tool_name = event_data.get('tool_name', '')\n"
        "    error_msg = event_data.get('error', '')\n"
        "    # HK47SEAM: Master refusing a tool call arrives here too, wearing the\n"
        "    # same clothes as a failure. It is not the droid's error and must not\n"
        "    # take the droid's apology. Matched without apostrophes on purpose:\n"
        "    # the surrounding heredoc is unquoted and mangles nothing here, but\n"
        "    # the phrasing varies and these two fragments do not.\n"
        "    _e = error_msg.lower()\n"
        "    if 'want to proceed' in _e or 'rejected' in _e:\n"
        "        category = 'input.declined'\n"
        "        status = 'working'\n"
        "    # Every arm below is now guarded on category being unset, so a\n"
        "    # declined call falls straight through to the sound logic.",
    ),
    (
        "declined guard",
        "    if error_msg and (tool_name == 'Bash' or session_source == 'copilot'):\n"
        "        category = 'task.error'",
        "    if not category and error_msg and (tool_name == 'Bash'"
        " or session_source == 'copilot'):  # HK47SEAM guard\n"
        "        category = 'task.error'",
    ),
    (
        "tool.error",
        "    else:\n"
        "        # Non-Bash tool failure — no sound, but maintain tab title\n"
        "        log('route', category='none', suppressed=True, reason='non_bash_tool_failure')",
        "    elif not category and error_msg:\n"
        "        # HK47SEAM: upstream silences non-Bash failures as noise. They are\n"
        "        # still the droid's own fault, so they get the quieter tool.error\n"
        "        # voice rather than the task.error one. Silence it with\n"
        "        # `peon config` if it proves too talkative.\n"
        "        category = 'tool.error'\n"
        "        status = 'error'\n"
        "    elif not category:\n"
        "        # Failure with no error text — no sound, but maintain tab title\n"
        "        log('route', category='none', suppressed=True, reason='non_bash_tool_failure')",
    ),
    (
        "awaiting_master disarm at Stop",
        "elif event == 'Stop':\n    category = 'task.complete'",
        "elif event == 'Stop':\n"
        "    category = 'task.complete'\n"
        "    # HK47SEAM: if the answer was met with prose and no tool call, the\n"
        "    # flag never got its chance. Disarm it here rather than let it fire\n"
        "    # against the first unrelated tool call of the next turn.\n"
        "    _await = state.get('awaiting_master', {})\n"
        "    if session_id in _await:\n"
        "        del _await[session_id]\n"
        "        state['awaiting_master'] = _await\n"
        "        state_dirty = True",
    ),
    (
        "awaiting_master cleanup",
        "    for key in ('session_packs', 'prompt_timestamps', 'session_start_times',"
        " 'prompt_start_times', 'subagent_sessions', 'last_task_complete'):",
        "    for key in ('session_packs', 'prompt_timestamps', 'session_start_times',"
        " 'prompt_start_times', 'subagent_sessions', 'last_task_complete',\n"
        "                'awaiting_master'):  # HK47SEAM: do not leak the flag",
    ),
    (
        "session.end",
        "    write_state(state, state_file)\n"
        "    log('route', category='none', suppressed=True, reason='session_end_cleanup')\n"
        "    log('exit', duration_ms=int((time.monotonic() - _peon_start) * 1000), exit=0)\n"
        "    print('EVENT=' + q(event))\n"
        "    print('PEON_EXIT=true')\n"
        "    sys.exit(0)",
        "    write_state(state, state_file)\n"
        "    # HK47SEAM: sign off out loud, but only on a real exit. /clear ends a\n"
        "    # session too, and a goodbye every time the context is cleared would\n"
        "    # be a farewell to nobody. The cleanup above has already run; nothing\n"
        "    # downstream of here reads the keys it removed.\n"
        "    if event_data.get('reason', '') in ('clear', 'compact'):\n"
        "        log('route', category='none', suppressed=True, reason='session_end_cleanup')\n"
        "        log('exit', duration_ms=int((time.monotonic() - _peon_start) * 1000), exit=0)\n"
        "        print('EVENT=' + q(event))\n"
        "        print('PEON_EXIT=true')\n"
        "        sys.exit(0)\n"
        "    category = 'session.end'\n"
        "    status = 'done'",
    ),
    (
        "prompt classifier",
        "    if not category and cat_enabled.get('task.acknowledge', False):\n"
        "        category = 'task.acknowledge'\n"
        "        status = 'working'",
        PROMPT_CLASSIFIER,
    ),
    (
        "danger.command",
        "elif event in ('PreToolUse', 'PostToolUse'):\n"
        "    # Tool use events indicate Claude is actively working"
        " — clear needs_approval tab color\n"
        "    status = 'working'",
        DANGER_CLASSIFIER,
    ),
]


def target(install):
    return os.path.join(
        os.path.expanduser("~"), install, "hooks", "peon-ping", "peon.sh"
    )


def apply(path):
    orig = path + ".orig"
    src = open(path).read()
    if MARKER in src:
        # Re-apply from the pristine copy rather than refusing, so tuning a word
        # list is one run rather than a revert and a run.
        if not os.path.exists(orig):
            print("  ABORT: patched but no .orig to re-apply from", file=sys.stderr)
            return False
        src = open(orig).read()
        print("  already patched, re-applying from .orig")

    for name, anchor, replacement in PATCHES:
        n = src.count(anchor)
        if n != 1:
            print(f"  ABORT: anchor for '{name}' found {n} times, expected 1",
                  file=sys.stderr)
            return False
        src = src.replace(anchor, replacement)

    if not os.path.exists(orig):
        shutil.copy2(path, orig)
        print(f"  original saved to {orig}")
    mode = os.stat(path).st_mode
    with open(path, "w") as fh:
        fh.write(src)
    os.chmod(path, mode)
    print(f"  applied {len(PATCHES)} patches")
    return True


def revert(path):
    orig = path + ".orig"
    if not os.path.exists(orig):
        print(f"  no {orig}, nothing to revert")
        return True
    mode = os.stat(path).st_mode
    shutil.copy2(orig, path)
    os.chmod(path, mode)
    print("  reverted to upstream")
    return True


def main():
    action = revert if "--revert" in sys.argv else apply
    ok = True
    for install in INSTALLS:
        path = target(install)
        if not os.path.isfile(path):
            print(f"skip {install}: no peon.sh")
            continue
        print(install)
        ok = action(path) and ok
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
