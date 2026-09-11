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

        if _lp.startswith('/start-session'):
            # HK47SEAM: these two are skills, therefore prompts, and never fire
            # the SessionStart/SessionEnd hooks at all, so before this arm the
            # only line either could produce was a generic skill.invoke. Match
            # the word to the sound; a real launch and a real exit keep their
            # own routes, which is why both categories already have clips.
            category = 'session.resume'
        elif _lp.startswith('/end-session'):
            category = 'session.end'
        elif _p.startswith('/'):
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
    # HK47SEAM: the tool events carry three of the pack's categories.
    #
    # Measured 2026-09-10, against a live log, and it overturned a guess: asking
    # Master a question does NOT raise an elicitation Notification, and his
    # answer does NOT arrive as a UserPromptSubmit. The whole exchange is one
    # AskUserQuestion tool call. So the question is this PreToolUse.
    #
    # Re-measured 2026-09-11, which overturned the other half of that reading:
    # the answer is not "whatever tool call I make next" either. It is the
    # PostToolUse of the AskUserQuestion itself, which is the exact instant
    # Master confirms. The old reading needed a flag to survive from one hook
    # invocation to the next, and it mostly did not: Claude Code raises
    # PermissionRequest for the same question 11ms later, that invocation reads
    # the state file before this arm has written it and writes last, so the flag
    # was erased 13 times in 15 over two days of log.
    #
    # A destructive command earns a word before it runs, not after. PreToolUse
    # fires on every single tool call, so that arm stays silent unless a token
    # from the list actually appears in the command.
    _tn = event_data.get('tool_name', '')
    _await = state.get('awaiting_master', {})
    if event == 'PostToolUse':
        # Scoped to AskUserQuestion here as well as by the matcher in
        # settings.json, because every other tool call would be a voice line.
        #
        # MASTER'S RULING 2026-09-11: submitting answers in the question tool is
        # him putting something in, exactly as typing a prompt is, so it takes
        # the same line a prompt takes and not a category of its own. That is
        # task.acknowledge, which is what the prompt classifier falls back to
        # when nothing in the text claims it.
        if _tn == 'AskUserQuestion':
            category = 'task.acknowledge'
            if session_id in _await:
                del _await[session_id]
                state['awaiting_master'] = _await
                state_dirty = True
    else:
        _ti = event_data.get('tool_input', {}) or {}
        _cmd = str(_ti.get('command', '') or '').lower() if isinstance(_ti, dict) else ''
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
            # The flag no longer decides whether the answer is audible, so
            # nothing load-bearing depends on it surviving that write race. It
            # is cleared here, and still read by the UserPromptSubmit arm, for
            # the case where Master abandons the dialog and types instead.
            # ponytail: the lost update is upstream's whole-dict last-writer-wins
            # state file, not this key; fix it there if another flag ever needs
            # to live across invocations.
            if session_id in _await:
                del _await[session_id]
                state['awaiting_master'] = _await
                state_dirty = True\
'''

# --- A question is not an approval -------------------------------------------
# Claude Code raises PermissionRequest as well as PreToolUse for one
# AskUserQuestion call, 11ms apart, so the question was spoken twice: once as
# input.question and once as input.required.
PERMREQ_ANCHOR = (
    "    category = 'input.required'\n"
    "    status = 'needs approval'\n"
    "    marker = '\\u25cf '\n"
    "    notify = '1'\n"
    "    notify_color = 'red'\n"
    "    _tool = event_data.get('tool_name', '')\n"
    "    msg = notification_message(status, _tool)"
)

PERMREQ_PATCH = (
    "    _tool = event_data.get('tool_name', '')\n"
    "    # HK47SEAM: MEASURED 2026-09-11 across two days of live log. 17 of 18\n"
    "    # PermissionRequest events were AskUserQuestion and exactly one was a\n"
    "    # real Bash approval, so this arm has been the wrong voice for a\n"
    "    # question almost every time it has ever fired. The lock built for\n"
    "    # ruling 2 did not remove the second line, it only stopped the two\n"
    "    # overlapping and queued one behind the other.\n"
    "    #\n"
    "    # The PreToolUse arm owns the question, because it fires for every\n"
    "    # AskUserQuestion whereas this event is not guaranteed to. This arm\n"
    "    # keeps the desktop notification and drops the voice.\n"
    "    if _tool == 'AskUserQuestion':\n"
    "        category = ''\n"
    "        status = 'question'\n"
    "        marker = '\\u25cf '\n"
    "        notify = '1'\n"
    "        notify_color = 'blue'\n"
    "        msg = notification_message(status, 'Question pending')\n"
    "        msg_subtitle = 'Question pending'\n"
    "    else:\n"
    "        category = 'input.required'\n"
    "        status = 'needs approval'\n"
    "        marker = '\\u25cf '\n"
    "        notify = '1'\n"
    "        notify_color = 'red'\n"
    "        msg = notification_message(status, _tool)"
)

# --- Playback: defer instead of interrupt ------------------------------------
#
# These two patches are the only ones that land in SHELL rather than in the
# routing Python, so the unquoted-heredoc rule does not apply to them and a
# dollar sign here is just a dollar sign.
#
# Upstream's play_sound() opens by killing whatever is still playing, so the
# newest line always wins and the previous one is cut off mid-sentence. With
# barks that is invisible; with HK-47, whose median clip is four seconds of
# conversational dialogue, it is the defect Master reported.

KILL_ANCHOR = "# --- Kill any previously playing peon-ping sound ---"

DEFER_HELPERS = '''# --- HK47SEAM: hold the tongue while a microphone is live ------------------
# Master dictates with /voice, Claude Code's push-to-talk. Four seconds of
# HK-47 fired into an open microphone is four seconds typed into his prompt.
#
# MEASURED on 2026-09-10, not assumed. /voice opens exactly one PipeWire
# capture stream, holds it for the whole time dictation is active, and drops it
# when he stops:
#
#   Source Output #107249
#     application.name = "PipeWire ALSA [claude]"
#     node.name        = alsa_capture.claude
#     media.name       = "ALSA Capture"
#
# Keyed on node.name, which is the narrowest of the three. Any Claude session
# dictating anywhere silences this one, which is correct: it means a live
# microphone in the same room. For the broader "shut up during meetings" case
# peon-ping already ships meeting_detect, which is a config flag, not this.
#
# 6ms per call, measured over 20 runs, so it is not worth a cheaper pre-gate.
#
# Overridable by environment so the detector can be exercised against a stream
# that is not Claude Code. arecord registers as alsa_capture.aplay, arecord
# being a symlink to aplay, which is what the suppression test drives it with:
# arecord refuses to run under any other name, so a real alsa_capture.claude
# cannot be faked. The literal string is not a guess either way -- it came off
# Master's own probe run.
HK47_VOICE_NODE="${HK47_VOICE_NODE:-alsa_capture.claude}"

hk47_voice_active() {
  command -v pactl >/dev/null 2>&1 || return 1
  pactl list source-outputs 2>/dev/null | grep -q "$HK47_VOICE_NODE"
}

# --- HK47SEAM: let the clip in flight finish -------------------------------
# Master's complaint: a line cut off mid-sentence is worse than a line that
# arrives a moment late. These helpers hold a new clip until the current one
# ends, rather than shooting the current one in the head.
#
# The pending slot is exactly ONE deep, on purpose. A real FIFO queue would
# turn a busy turn into a monologue running several seconds behind reality,
# which is the same defect wearing a different coat. So the slot holds one
# line, and which one it holds is decided by rank, not by arrival order: see
# hk47_rank for why newest-wins was wrong.
HK47_DEFER_WAIT=120    # 12.0s: give up on a speaker that will not free up
HK47_DEFER_STALE=80    # 8.0s: waited so long the line no longer describes now

# --- HK47SEAM: one decision at a time --------------------------------------
# Two hook events arriving within ~2ms both read an idle pid file and both
# play, which Master heard once when a PermissionRequest and an
# AskUserQuestion fired as a pair. The decide-and-claim below runs under this
# lock so only one of them can find the speaker free.
#
# -w 0.3 because a hook is in Claude Code's critical path: a lock that can
# hang an event is worse than the race it closes. On timeout we proceed
# unlocked and log it, so the worst case is the old behaviour, not a stall.
#
# The lock covers the DECISION, not the playback. Holding it across the spawn
# would leak fd 9 into the player, which then holds the lock for the whole
# clip and taxes every hook 0.3s for four seconds. What makes the shorter
# critical section safe is the provisional claim in play_sound: a pid that is
# alive for exactly as long as the gap between deciding and spawning.
HK47_LOCK_FD=9

hk47_lock() {
  command -v flock >/dev/null 2>&1 || return 1
  exec 9>"$PEON_DIR/.hk47-play.lock" 2>/dev/null || return 1
  flock -w 0.3 9 2>/dev/null
}

hk47_unlock() {
  exec 9>&- 2>/dev/null || return 0
}

# Busy means either a clip is playing or a hook has claimed the speaker and is
# a few milliseconds from spawning one. The claim lives in its OWN file rather
# than in .sound.pid, because that file has upstream readers: kill_previous_sound
# would shoot a hook process, and the trainer's wait loop would be handed a pid
# that is not a player.
#
# THE CLAIM IS READ FIRST, AND THE ORDER IS LOAD-BEARING. save_sound_pid hands
# over in the other direction: it writes .sound.pid and THEN retires the claim.
# Reading in the same direction as that write lets a reader slip between the two
# and see neither, which is exactly how two events still played together after
# the lock went in. Read the claim first and the handoff cannot be caught
# mid-air: while the claim is alive we are busy, and by the time it is gone the
# real pid is already on disk.
hk47_sound_busy() {
  local f p
  for f in "$PEON_DIR/.hk47-claim.pid" "$PEON_DIR/.sound.pid"; do
    [ -f "$f" ] || continue
    p=$(cat "$f" 2>/dev/null)
    if [ -n "$p" ] && kill -0 "$p" 2>/dev/null; then
      return 0
    fi
  done
  return 1
}

# danger.command is the one category still allowed to interrupt. It fires at
# PreToolUse, BEFORE the dangerous thing runs, so a warning made to wait four
# seconds behind an acknowledgement is a warning delivered after the fact.
hk47_may_interrupt() {
  case "${CATEGORY:-}" in
    danger.command) return 0 ;;
    *) return 1 ;;
  esac
}

# --- HK47SEAM: rank the pending slot by who caused the line ----------------
# Depth-one newest-wins was wrong, and Master caught it within the hour. It
# discards HIS lines systematically, because a tool call of mine always lands
# within a second of his prompt:
#
#   1. my turn ends, Stop plays a 4s completion line
#   2. he submits a prompt, which is deferred behind it
#   3. a tool call of mine fires and REPLACES his pending line
#   4. he hears the tool call and never his own acknowledgement
#
# So: 2 preempts outright, 1 is Master's own doing, 0 is my background
# chatter, and a 0 may never displace a 1. Equal ranks keep newest-wins,
# which is right within a tier: two of my own lines, the later one describes
# now. session.start/resume/end are rank 1 by extension rather than by
# Master's list, on the same principle: he launched the session, and after
# the classifier arm below, /start-session and /end-session are prompts.
hk47_rank() {
  case "$1" in
    danger.command) echo 2 ;;
    user.*|input.*|task.acknowledge|skill.invoke|session.start|session.resume|session.end)
      echo 1 ;;
    *) echo 0 ;;
  esac
}

# The pending slot's category, empty if nothing is waiting. The pid is checked
# because a deferrer that has already gone to the speaker removes its own file
# and a killed one may not have: a dead pid is not a pending line.
hk47_pending_category() {
  local dpidfile="$PEON_DIR/.hk47-defer.pid" dp dc
  [ -f "$dpidfile" ] || return 0
  read -r dp dc < "$dpidfile" 2>/dev/null || return 0
  [ -n "$dp" ] && kill -0 "$dp" 2>/dev/null && printf '%s' "$dc"
  return 0
}

# Cancel whatever is waiting its turn. Killing a deferrer only ends a sleep,
# so nothing audible is interrupted: that is the whole difference between this
# and kill_previous_sound. Called from both, because ANY decision to take the
# speaker must also drop the line that was queued for it. Without this, a clip
# that plays by the direct path leaves an orphaned deferrer sleeping on a pid
# that is already dead, and it wakes to play straight over the top.
hk47_cancel_pending() {
  local dpidfile="$PEON_DIR/.hk47-defer.pid" dp _dc
  [ -f "$dpidfile" ] || return 0
  # Two fields since the rank patch: kill the pid, never the category with it.
  read -r dp _dc < "$dpidfile" 2>/dev/null || true
  [ -n "${dp:-}" ] && kill "$dp" 2>/dev/null
  rm -f "$dpidfile"
  return 0
}

# Returns 1 without queueing anything when the pending line outranks this one,
# which is a decision to stay silent rather than a failure.
hk47_defer() {
  local file="$1" vol="$2" player="$3"
  local dpidfile="$PEON_DIR/.hk47-defer.pid"
  local newcat="${CATEGORY:-}" pendcat
  HK47_OUTRANKED_BY=""
  pendcat=$(hk47_pending_category)
  if [ -n "$pendcat" ] && [ "$(hk47_rank "$newcat")" -lt "$(hk47_rank "$pendcat")" ]; then
    HK47_OUTRANKED_BY="$pendcat"
    return 1
  fi
  hk47_cancel_pending
  (
    # Never hold the decision lock through a twelve-second sleep. The parent
    # spawned us while holding it, so fd 9 came with us.
    exec 9>&- 2>/dev/null || true
    # The hook process that spawned us is about to exit. Upstream wraps its
    # player in nohup for the same reason: outlive the hangup, or the deferred
    # line is lost silently and the defect looks intermittent rather than fixed.
    trap "" HUP
    n=0
    # Waits on the speaker rather than on one pid, so a clip that takes over
    # mid-wait is also waited out instead of being played straight over.
    while hk47_sound_busy && [ "$n" -lt "$HK47_DEFER_WAIT" ]; do
      sleep 0.1
      n=$((n + 1))
    done
    rm -f "$dpidfile"
    # Checked again here, not only at decision time: he may have started
    # dictating during the very seconds we spent waiting our turn.
    if [ "$n" -lt "$HK47_DEFER_STALE" ] && ! hk47_voice_active; then
      # Claim the speaker before spawning, for the same reason play_sound
      # does. BASHPID, not $$, which inside a subshell is still the hook's.
      echo $BASHPID > "$PEON_DIR/.hk47-claim.pid"
      play_linux_sound "$file" "$vol" "$player"
      # Register it, or the next event reads an idle pid file and interrupts
      # the very clip this function waited to play.
      save_sound_pid $!
    fi
  ) >/dev/null 2>&1 &
  echo "$! $newcat" > "$dpidfile"
  return 0
}

# --- Kill any previously playing peon-ping sound ---'''

CANCEL_ANCHOR = '''kill_previous_sound() {
  local pidfile="$PEON_DIR/.sound.pid"
'''

CANCEL_PATCH = '''kill_previous_sound() {
  # HK47SEAM: taking the speaker also drops whatever was queued for it. See
  # hk47_cancel_pending above for why an orphaned deferrer is audible.
  hk47_cancel_pending
  local pidfile="$PEON_DIR/.sound.pid"
'''

PLAY_ANCHOR = '''play_sound() {
  local file="$1" vol="$2"
  _peon_log play "backend=$PEON_PLATFORM file=$(basename "$file") volume=$vol async=true"
  kill_previous_sound
'''

# No backslash line-continuations in here: this is a plain triple-quoted Python
# string, so a trailing backslash would be eaten as a Python continuation and
# the shell would never see it. Bash continues happily after a trailing &&.
DEFER_ENTRY = '''play_sound() {
  local file="$1" vol="$2"
  _peon_log play "backend=$PEON_PLATFORM file=$(basename "$file") volume=$vol async=true"
  # HK47SEAM: defer rather than interrupt. Linux only, because that is the only
  # platform whose player this fork drives, and never under PEON_TEST, whose
  # whole contract is that playback is synchronous.
  #
  # Everything from the busy check to the claim runs under hk47_lock, because
  # two events 2ms apart used to read the same idle pid file and both play.
  if [ "$PEON_PLATFORM" = "linux" ] && [ "${PEON_TEST:-0}" != "1" ]; then
    hk47_lock || _peon_log play "lock=timeout category=${CATEGORY:-}"
    local _hk_busy=no
    hk47_sound_busy && _hk_busy=yes
    if [ "$_hk_busy" = yes ] && ! hk47_may_interrupt; then
      local _hk_player
      _hk_player=$(detect_linux_player "${LINUX_AUDIO_PLAYER:-}") || _hk_player=""
      if [ -n "$_hk_player" ]; then
        if hk47_defer "$file" "$vol" "$_hk_player"; then
          _peon_log play "deferred=true category=${CATEGORY:-}"
        else
          _peon_log play "dropped=outranked category=${CATEGORY:-} pending=${HK47_OUTRANKED_BY:-}"
        fi
        hk47_unlock
        return 0
      fi
    elif [ "$_hk_busy" = no ]; then
      # Provisional claim: a live pid covering the gap between deciding and
      # spawning, so a concurrent hook reads the speaker as taken and defers
      # instead of playing over the top. Retired by save_sound_pid below.
      #
      # BASHPID, never $$. MEASURED, and it cost an afternoon: upstream runs
      # this whole path as `_run_sound_and_notify & disown`, so $$ names the
      # hook shell that has ALREADY EXITED by the time a second event checks,
      # which made every claim dead on arrival and left the race wide open.
      # $BASHPID names the subshell actually doing the work.
      echo $BASHPID > "$PEON_DIR/.hk47-claim.pid"
    fi
    hk47_unlock
  fi
  kill_previous_sound
'''

SAVEPID_ANCHOR = '''save_sound_pid() {
  [ -n "${1:-}" ] || return 0
  echo "$1" > "$PEON_DIR/.sound.pid"
'''

SAVEPID_PATCH = '''save_sound_pid() {
  [ -n "${1:-}" ] || return 0
  echo "$1" > "$PEON_DIR/.sound.pid"
  # HK47SEAM: a real player pid supersedes the provisional claim, and the claim
  # must not outlive the clip. Without this, the claiming subshell goes on to do
  # notifications and tab titles, so a finished clip could still read as busy
  # and the next line would wait behind nothing.
  rm -f "$PEON_DIR/.hk47-claim.pid"
'''

SKIP_ANCHOR = '''  # --- Play sound and/or TTS based on mode ---
  if [ "$_skip_sound" = "false" ]; then
'''

# Gated here rather than inside play_sound because this one branch governs the
# clip AND the TTS line below it, and dictation must silence both.
SKIP_VOICE = '''  # HK47SEAM: say nothing into an open microphone. See hk47_voice_active.
  if [ "$_skip_sound" = "false" ] && hk47_voice_active; then
    _skip_sound=true
    _peon_log play "suppressed=voice reason=dictation_active"
  fi

  # --- Play sound and/or TTS based on mode ---
  if [ "$_skip_sound" = "false" ]; then
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
    (
        "category export",
        "print('SOUND_FILE=' + q(sound_file))",
        "print('SOUND_FILE=' + q(sound_file))\n"
        "# HK47SEAM: the shell below needs to know WHICH line it is about to play,\n"
        "# because one category is allowed to interrupt another. Parsing it back out\n"
        "# of the filename would work only for packs that happen to be named the way\n"
        "# this one is.\n"
        "print('CATEGORY=' + q(category or ''))",
    ),
    ("a question is not an approval", PERMREQ_ANCHOR, PERMREQ_PATCH),
    ("defer helpers", KILL_ANCHOR, DEFER_HELPERS),
    ("retire the claim", SAVEPID_ANCHOR, SAVEPID_PATCH),
    ("defer instead of interrupt", PLAY_ANCHOR, DEFER_ENTRY),
    ("silence while dictating", SKIP_ANCHOR, SKIP_VOICE),
    ("cancel pending on takeover", CANCEL_ANCHOR, CANCEL_PATCH),
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
