#!/usr/bin/env python3
"""The overlord's hands: pause, mute, move focus, and put everything back.

Master's brief, 2026-09-13: "The overlord agent needs to be able to pause videos
that are running to talk, ask for permission to speak, speak or continue the
video, mute an audiostream, move focus to one window or workspace and back."

This is a standalone CLI with no model attached and no daemon behind it. It is
built before the overlord (PERS-5) exists, so that the hands can be proven while
the thing that will drive them is still a design. Every verb prints JSON on
stdout and nothing else, because its only caller will be a program.

    hk47-desktop.py context           what is playing, what is capturing, which
                                      class we are in, and what that permits
    hk47-desktop.py hush              silence what may be silenced, and record it
    hk47-desktop.py resume            undo exactly what `hush` did, and nothing else
    hk47-desktop.py ask [--line ...]  request permission to speak
    hk47-desktop.py focus <target>    move focus to a window
    hk47-desktop.py unfocus           go back to the window focus came from
    hk47-desktop.py mode on|off|status   the declared focus mode

THREE CLASSES, AND THE POLICY THEY SELECT
-----------------------------------------
Master ruled three, not four, on 2026-09-14: meeting, focus, ordinary. A show
does not get its own rule, and the immersive class was struck outright because
he watches and plays windowed, so fullscreen detects nothing about him.

  meeting   something that is not ours holds a live capture stream
  focus     Master has declared focus mode, by voice, by a click on the
            diorama, or by `mode on` from anything else
  ordinary  neither

Meeting and focus carry the same policy today and are kept apart anyway, because
they are different facts about the world and the table below is where they will
diverge. In both: the hands work and the mouth does not. Silent acts are
permitted, speech is not, and focus is never stolen.

WHY PROCESS NAME AND NOT PID
----------------------------
Two links are needed: player to stream, so an app with MPRIS is paused rather
than muted, and meeting-holder to everything else, so the call is never touched.
The obvious key is the pid, and it was measured on this machine and does not
work:

  * Brave's MPRIS bus name sits on pid 9011 while Brave's sink-input reports pid
    10211, because Chromium runs its audio in a separate process from the one
    that owns the bus name.
  * Plex reports `application.process.id` as `5`, a sandbox pid namespace
    leaking through the property.

So the key is the process name, compared at fifteen characters: `busctl` reports
the kernel's `comm`, which truncates there, so `wine64-preloader` arrives as
`wine64-preloade` beside pactl's full basename.

WHAT `corked` MEANS HERE
------------------------
PipeWire's node state, `running` against `idle`, is the discriminator recorded
on PERS-10, and it came from `pw-dump`. `pactl -f json list source-outputs`
carries no `state` field at all: `corked` is the nearest thing it has, measured
against a live `parecord`. It is an approximation and is marked as one. If a
call on hold ever proves to cork its capture stream, this is the line to move to
`pw-dump`.

A SUCCESSFUL CALL IS NOT A PAUSE
--------------------------------
Measured live on 2026-09-14: `busctl call ... Pause` returned 0 on Brave and the
player was still reporting `Playing` a second later. So every MPRIS act is read
back before it is believed. A silence that was only requested is reported as a
failure and kept out of the record, because a record of a pause that never
happened is a Play pressed later on something that never stopped.

TWO HYPRLAND DIALECTS
---------------------
Hyprland 0.56 with a Lua config evaluates `hyprctl dispatch` arguments as Lua,
so the classic `dispatch focuswindow address:0x...` is a parse error there and
moves nothing, while `hl.dsp.focus({ window = "address:0x..." })` works and
brings a hidden workspace with it. A conf-configured Hyprland is the other way
round. `dispatch()` tries the classic form, recognises the compositor's own
complaint, and retries in Lua, so neither kind of machine needs a flag set.

WHAT THIS DELIBERATELY CANNOT DO
--------------------------------
It does not speak. `ask` returns a verdict about whether speech is permitted and
the line that would be spoken; the mouth is PERS-2 and belongs to whoever calls
this. It also never rearranges the tiling composition, which is enforced rather
than promised: every Hyprland dispatch goes through `dispatch()`, and that
function refuses any verb outside ALLOWED_DISPATCH.

Its argument surface is closed on purpose. PERS-5 will eventually hand it a
target chosen by a model, so `focus` accepts an address or a class matching one
regex and nothing else, and no argument is ever passed through a shell.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

# --- where state lives ------------------------------------------------------
#
# $XDG_RUNTIME_DIR, the same place the badge hook keeps its flags. It is wiped
# on reboot, which is right for every file here: a mute does not survive a
# reboot either, so a record of one must not outlive it and invite a resume of
# something nothing has muted.

STATE_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "hk47")
HUSH_RECORD = os.path.join(STATE_DIR, "desktop-hushed.json")
FOCUS_RECORD = os.path.join(STATE_DIR, "desktop-focus.json")
MODE_FLAG = os.path.join(STATE_DIR, "focus-mode")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 3

# Capture streams that belong to us and therefore never mean "a meeting".
# `alsa_capture.claude` is Claude Code's /voice dictation, measured on
# 2026-09-11. `hk47-listen` is reserved for PERS-11's listener, which does not
# exist yet: naming it now costs nothing and stops the droid from mistaking its
# own ear for a call on the day it grows one.
DEFAULT_OWN_CAPTURE_NODES = ("alsa_capture.claude", "hk47-listen")

# The only Hyprland dispatcher this tool may ever call. Master's ruling is that
# the tiling composition is never rearranged to let the droid talk, and a list
# that `dispatch()` checks is a mechanism where a comment would only be a wish.
# `movewindow`, `swapwindow`, `togglefloating` and their kind are absent by
# intent, not by oversight. `workspace` is absent too, and for a different
# reason: focusing a window on a hidden workspace brings that workspace with it,
# measured on 2026-09-14, so a second dispatcher would only be a way to move a
# workspace without wanting anything on it.
ALLOWED_DISPATCH = ("focuswindow",)

# Hyprland 0.56 with a Lua config evaluates `hyprctl dispatch` arguments as Lua:
# the classic `dispatch focuswindow address:0x...` comes back as a parse error
# and moves nothing. A conf-configured Hyprland wants exactly that classic form.
# Rather than sniff the config, `dispatch()` tries the classic form, recognises
# the compositor's own complaint about `hl.dispatch`, and retries in Lua.
LUA_DISPATCH = {"focuswindow": 'hl.dsp.focus({{ window = "{arg}" }})'}

# The Lua form is an eval inside the compositor, so the only arguments allowed
# to reach it are the ones that cannot be anything but an address. This is
# stricter than TARGET_RE on purpose: TARGET_RE guards a lookup, this guards an
# interpolation into a language.
LUA_ARG_RE = re.compile(r"^address:0x[0-9a-fA-F]{2,16}$")

_LUA_MODE = False

# A target is an address or a class, and nothing else is a target. Hyprland
# classes on this machine include `brave-web.telegram.org__a_-Default`, so dots,
# underscores and hyphens have to be admitted; spaces, quotes and semicolons do
# not.
TARGET_RE = re.compile(r"^(?:address:0x[0-9a-fA-F]{2,16}|class:[A-Za-z0-9._-]{1,128})$")

POLICY = {
    "ordinary": {"speak": True, "notify": True, "move_focus": True, "silent_acts": True},
    "meeting": {"speak": False, "notify": True, "move_focus": False, "silent_acts": True},
    "focus": {"speak": False, "notify": True, "move_focus": False, "silent_acts": True},
}


# --- running other programs -------------------------------------------------


def run(argv, timeout=5):
    """Run a program. Never through a shell, and never with a non-string in argv.

    The assertion is not defensive clutter: the eventual caller is a model
    choosing a window to focus, and a number or a None sliding into argv is the
    shape of bug that turns into an injection the day someone "fixes" it by
    joining the list into a string.
    """
    assert all(isinstance(a, str) for a in argv), f"non-string in argv: {argv!r}"
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(argv, 127, "", str(exc))


def jrun(argv):
    proc = run(argv)
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None


def hyprctl_ok(proc):
    """hyprctl says `ok` and nothing else when a dispatch landed.

    The returncode alone is not enough: a Lua-configured compositor answers the
    classic form with returncode 7 on one build and an `error:` line on another,
    and both of them moved nothing.
    """
    return proc.returncode == 0 and proc.stdout.strip().lower().startswith("ok")


def dispatch(verb, arg):
    if verb not in ALLOWED_DISPATCH:
        raise ValueError(f"dispatcher {verb!r} is not in ALLOWED_DISPATCH")

    global _LUA_MODE
    if not _LUA_MODE:
        proc = run(["hyprctl", "dispatch", verb, arg])
        if hyprctl_ok(proc):
            return True
        if "hl.dispatch" not in proc.stdout + proc.stderr:
            return False  # a real failure, not a dialect mismatch
        _LUA_MODE = True

    if not LUA_ARG_RE.match(arg):
        raise ValueError(f"{arg!r} may not be interpolated into Lua")
    return hyprctl_ok(run(["hyprctl", "dispatch", LUA_DISPATCH[verb].format(arg=arg)]))


# --- identity ---------------------------------------------------------------


def appkey(name):
    """Normalise a process name so pactl's binary and busctl's comm compare equal."""
    if not name:
        return ""
    return os.path.basename(str(name)).strip().lower()[:15]


def same_app(a, b):
    """True when two process names name the same program.

    Prefix matching in both directions, because only one of the two sources is
    truncated and which one that is depends on the program.
    """
    ka, kb = appkey(a), appkey(b)
    if not ka or not kb:
        return False
    return ka.startswith(kb) or kb.startswith(ka)


# --- probes -----------------------------------------------------------------


def own_capture_nodes():
    override = os.environ.get("HK47_OWN_CAPTURE_NODES")
    if override:
        return {s.strip() for s in override.split(",") if s.strip()}
    return set(DEFAULT_OWN_CAPTURE_NODES)


def pactl_list(what):
    return jrun(["pactl", "-f", "json", "list", what]) or []


def captures():
    own = own_capture_nodes()
    out = []
    for item in pactl_list("source-outputs"):
        props = item.get("properties") or {}
        node = props.get("node.name") or ""
        out.append({
            "index": item.get("index"),
            "node": node,
            "app": props.get("application.name") or "",
            "binary": props.get("application.process.binary") or "",
            "media": props.get("media.name") or "",
            # ponytail: `corked` stands in for pw-dump's node state. Move to
            # pw-dump if a held call ever proves to cork its capture stream.
            "live": not bool(item.get("corked")),
            "own": node in own,
        })
    return out


def sinks():
    out = []
    for item in pactl_list("sink-inputs"):
        props = item.get("properties") or {}
        out.append({
            "index": item.get("index"),
            "app": props.get("application.name") or "",
            "binary": props.get("application.process.binary") or "",
            "node": props.get("node.name") or "",
            "media": props.get("media.name") or "",
            "muted": bool(item.get("mute")),
            "corked": bool(item.get("corked")),
        })
    return out


def mpris_get(bus, iface, prop):
    data = jrun(["busctl", "--user", "get-property", bus, "/org/mpris/MediaPlayer2",
                 iface, prop, "--json=short"])
    return data.get("data") if isinstance(data, dict) else None


def mpris_call(bus, method):
    return run(["busctl", "--user", "call", bus, "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2.Player", method]).returncode == 0


def mpris_call_verified(bus, method, wanted):
    """Ask, then check, because a successful `busctl call` is not a pause.

    Measured on 2026-09-14: Brave accepted `Pause` with returncode 0 and was
    still reporting `Playing` a second later, on a live stream. A silence that
    was only requested must not be reported as a silence achieved, and it must
    not go into the record either, or `resume` will later press Play on
    something that never stopped.

    One round trip in the common case, at 2.6ms; the retry exists because the
    property is read over the same bus that has just been written to.
    """
    if not mpris_call(bus, method):
        return False
    for delay in (0.0, 0.15):
        if delay:
            time.sleep(delay)
        if mpris_get(bus, "org.mpris.MediaPlayer2.Player", "PlaybackStatus") == wanted:
            return True
    return False


def players():
    """Every live MPRIS player, with the two properties any decision needs.

    A bus name that answers no PlaybackStatus is dropped rather than reported:
    it is a name that has been taken and not yet served, or a player that died
    between the list and the query.
    """
    rows = jrun(["busctl", "--user", "list", "--no-pager", "--json=short"]) or []
    out = []
    for row in rows:
        bus = row.get("name") or ""
        if not bus.startswith("org.mpris.MediaPlayer2."):
            continue
        status = mpris_get(bus, "org.mpris.MediaPlayer2.Player", "PlaybackStatus")
        if status is None:
            continue
        out.append({
            "bus": bus,
            "process": row.get("process") or "",
            "pid": row.get("pid"),
            "status": status,
            "can_pause": bool(mpris_get(bus, "org.mpris.MediaPlayer2.Player", "CanPause")),
            "identity": mpris_get(bus, "org.mpris.MediaPlayer2", "Identity") or "",
        })
    return out


def clients():
    return jrun(["hyprctl", "clients", "-j"]) or []


def monitors():
    return jrun(["hyprctl", "monitors", "-j"]) or []


def activewindow():
    return jrun(["hyprctl", "activewindow", "-j"]) or {}


# --- state ------------------------------------------------------------------


def mode_on():
    return os.path.exists(MODE_FLAG)


def read_json(path, default):
    try:
        with open(path) as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    with open(tmp, "w") as handle:
        json.dump(payload, handle, indent=2)
    os.replace(tmp, path)


def unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


# --- classification ---------------------------------------------------------


def classify(caps, focus_mode):
    if any(c["live"] and not c["own"] for c in caps):
        return "meeting"
    if focus_mode:
        return "focus"
    return "ordinary"


def meeting_holders(caps):
    return [c for c in caps if c["live"] and not c["own"]]


# --- planning, which is the whole of the policy and touches nothing ----------
#
# Every rule lives in these two functions and they perform no action, take no
# probe and hold no state, which is what lets the test file drive the entire
# policy from fixtures with no desktop attached.


def plan_hush(caps, plist, slist):
    """Decide what to silence. Returns (actions, skipped), performs nothing.

    Two rules do the work, and both are Master's:

      * an app that exposes an MPRIS player is PAUSED and never muted, and an
        app that exposes none is MUTED and never pause-attempted. "Just mute the
        game, no point in stopping it": per-game pause would have to be built
        for every game ever made.
      * whatever holds the meeting is not touched at all, in either layer.
        Muting the browser would mute the people talking.
    """
    holders = meeting_holders(caps)
    actions, skipped = [], []

    def holds_meeting(name):
        return any(same_app(name, h["binary"]) or same_app(name, h["app"])
                   for h in holders)

    for player in plist:
        base = {"kind": "mpris", "bus": player["bus"], "process": player["process"],
                "identity": player["identity"]}
        if holds_meeting(player["process"]):
            skipped.append({**base, "why": "holds the meeting"})
        elif player["status"] != "Playing":
            skipped.append({**base, "why": f"not playing ({player['status']})"})
        elif not player["can_pause"]:
            skipped.append({**base, "why": "refuses to be paused"})
        else:
            actions.append(base)

    # Every app that has a player, not merely the ones being paused. A browser
    # whose player is already stopped may still be carrying a call in the same
    # sink-input, and muting that is the exact failure the meeting rule exists
    # to prevent.
    voiced = [p["process"] for p in plist]

    for sink in slist:
        base = {"kind": "sink", "index": sink["index"], "binary": sink["binary"],
                "node": sink["node"], "app": sink["app"]}
        if holds_meeting(sink["binary"]) or holds_meeting(sink["app"]):
            skipped.append({**base, "why": "holds the meeting"})
        elif any(same_app(sink["binary"], name) for name in voiced):
            skipped.append({**base, "why": "has an MPRIS player, so it is paused not muted"})
        elif sink["muted"]:
            skipped.append({**base, "why": "already muted"})
        else:
            # Corked streams are muted too, deliberately. Muting something
            # already silent costs nothing and is reversed correctly, while
            # skipping it leaves a stream free to uncork in the middle of a
            # sentence.
            actions.append(base)

    return actions, skipped


def plan_resume(entries, plist, slist):
    """Decide what to put back. Returns (actions, stale), performs nothing.

    The identity check on a sink is not paranoia. Sink-input indices are
    recycled by PulseAudio, so an index recorded before a five-minute silence
    can name a different program's stream by the time it is read, and unmuting
    that is a stranger's audio turned on by us.
    """
    by_bus = {p["bus"]: p for p in plist}
    by_index = {s["index"]: s for s in slist}
    actions, stale = [], []

    for entry in entries:
        if entry.get("kind") == "mpris":
            if entry.get("bus") not in by_bus:
                stale.append({**entry, "why": "the player is gone"})
            else:
                actions.append(entry)
            continue

        sink = by_index.get(entry.get("index"))
        if sink is None:
            stale.append({**entry, "why": "the stream is gone"})
        elif not same_app(sink["binary"], entry.get("binary")) or sink["node"] != entry.get("node"):
            stale.append({**entry, "why": "the index now belongs to another stream"})
        else:
            actions.append(entry)

    return actions, stale


def merge_entries(existing, fresh):
    """Add what is new without dropping what an earlier hush is still holding.

    Speaking twice in a row used to lose the first record wholesale: the second
    hush found everything already silenced, recorded nothing, and wrote its
    empty record over the one that knew how to put things back. Everything
    stayed muted and nothing knew why.
    """
    merged = list(existing)
    seen = {(e.get("kind"), e.get("bus"), e.get("index")) for e in existing}
    for entry in fresh:
        key = (entry.get("kind"), entry.get("bus"), entry.get("index"))
        if key not in seen:
            merged.append(entry)
            seen.add(key)
    return merged


# --- verbs ------------------------------------------------------------------


def emit(payload, code=EXIT_OK):
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return code


def gather():
    caps = captures()
    klass = classify(caps, mode_on())
    return caps, klass


def verb_context(_args):
    caps, klass = gather()
    return emit({
        "class": klass,
        "focus_mode": mode_on(),
        "policy": POLICY[klass],
        "captures": caps,
        "players": players(),
        "sinks": sinks(),
        "active_window": {k: activewindow().get(k) for k in ("address", "class", "title")},
        "hushed": read_json(HUSH_RECORD, {}).get("entries", []),
    })


def verb_hush(_args):
    caps, klass = gather()
    if not POLICY[klass]["silent_acts"]:
        return emit({"class": klass, "refused": "silent acts are not permitted in this class"},
                    EXIT_REFUSED)

    plist, slist = players(), sinks()
    actions, skipped = plan_hush(caps, plist, slist)

    done, failed = [], []
    for act in actions:
        if act["kind"] == "mpris":
            ok = mpris_call_verified(act["bus"], "Pause", "Paused")
        else:
            ok = run(["pactl", "set-sink-input-mute", str(act["index"]), "1"]).returncode == 0
        (done if ok else failed).append(act)

    record = read_json(HUSH_RECORD, {})
    stamped = [{**a, "at": time.time()} for a in done]
    write_json(HUSH_RECORD, {"entries": merge_entries(record.get("entries", []), stamped)})

    return emit({"class": klass, "silenced": done, "skipped": skipped, "failed": failed},
                EXIT_ERROR if failed else EXIT_OK)


def verb_resume(_args):
    entries = read_json(HUSH_RECORD, {}).get("entries", [])
    actions, stale = plan_resume(entries, players(), sinks())

    done, failed = [], []
    for act in actions:
        if act["kind"] == "mpris":
            ok = mpris_call_verified(act["bus"], "Play", "Playing")
        else:
            ok = run(["pactl", "set-sink-input-mute", str(act["index"]), "0"]).returncode == 0
        (done if ok else failed).append(act)

    # Everything that could be put back has been, and everything that could not
    # never will be, so the record is discarded either way. Keeping the failures
    # would only guarantee a later resume acts on a still-staler index.
    unlink(HUSH_RECORD)
    return emit({"resumed": done, "stale": stale, "failed": failed},
                EXIT_ERROR if failed else EXIT_OK)


def sanitise(line, limit=200):
    """A line bound for a notification argv. Printable, one line, and short."""
    cleaned = "".join(ch for ch in (line or "") if ch.isprintable())
    return cleaned.strip()[:limit]


def find_icon():
    override = os.environ.get("HK47_ICON")
    if override and os.path.isfile(override):
        return override
    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, ".claude/hooks/peon-ping/packs/hk47/icon.png"),
        os.path.join(home, ".claude-personal/hooks/peon-ping/packs/hk47/icon.png"),
        os.path.join(home, ".claude-work/hooks/peon-ping/packs/hk47/icon.png"),
    ):
        if os.path.isfile(candidate):
            return candidate
    return None


def verb_ask(args):
    _caps, klass = gather()
    policy = POLICY[klass]
    line = sanitise(args.line) or "Master?"

    notified = False
    if policy["notify"]:
        argv = ["notify-send", "-a", "HK-47", "-u", "normal"]
        icon = find_icon()
        if icon:
            argv += ["-i", icon]
        notified = run(argv + ["HK-47", line]).returncode == 0

    # Both channels at once is Master's ruling, and only one of them is built
    # here. `may_speak` is the verdict; the voice that acts on it is PERS-2.
    return emit({"class": klass, "line": line, "notified": notified,
                 "may_speak": policy["speak"]})


def resolve_target(target, window_list):
    if target.startswith("address:"):
        wanted = target.split(":", 1)[1].lower()
        return next((w for w in window_list
                     if str(w.get("address", "")).lower() == wanted), None)
    wanted = target.split(":", 1)[1]
    return next((w for w in window_list if w.get("class") == wanted), None)


def verb_focus(args):
    if not TARGET_RE.match(args.target):
        return emit({"error": "target must be address:0x... or class:NAME",
                     "target": args.target}, EXIT_ERROR)

    _caps, klass = gather()
    if not POLICY[klass]["move_focus"]:
        return emit({"class": klass,
                     "refused": "focus is never stolen in this class"}, EXIT_REFUSED)

    window = resolve_target(args.target, clients())
    if window is None:
        return emit({"class": klass, "error": "no window matches", "target": args.target},
                    EXIT_ERROR)

    visible = {m.get("activeWorkspace", {}).get("id") for m in monitors()}
    already_visible = window.get("workspace", {}).get("id") in visible

    previous = activewindow()
    write_json(FOCUS_RECORD, {
        "address": previous.get("address"),
        "class": previous.get("class"),
        "title": previous.get("title"),
        "at": time.time(),
    })

    ok = dispatch("focuswindow", f"address:{window['address']}")
    return emit({
        "class": klass,
        "focused": {k: window.get(k) for k in ("address", "class", "title")},
        # Reported rather than decided: focuswindow switches workspace only when
        # it has to, which is precisely Master's "if it is already in view, do
        # not switch". Nothing here needs to arrange that, only to say so.
        "switched_workspace": not already_visible,
        "came_from": previous.get("address"),
        "ok": ok,
    }, EXIT_OK if ok else EXIT_ERROR)


def verb_unfocus(_args):
    record = read_json(FOCUS_RECORD, {})
    address = record.get("address")
    if not address:
        return emit({"error": "nothing recorded to go back to"}, EXIT_ERROR)

    _caps, klass = gather()
    if not POLICY[klass]["move_focus"]:
        return emit({"class": klass,
                     "refused": "focus is never stolen in this class"}, EXIT_REFUSED)

    if not any(w.get("address") == address for w in clients()):
        unlink(FOCUS_RECORD)
        return emit({"class": klass, "error": "the window focus came from has closed",
                     "address": address}, EXIT_ERROR)

    ok = dispatch("focuswindow", f"address:{address}")
    unlink(FOCUS_RECORD)
    return emit({"class": klass, "restored": address, "ok": ok},
                EXIT_OK if ok else EXIT_ERROR)


def verb_mode(args):
    if args.state == "on":
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(MODE_FLAG, "w") as handle:
            handle.write(f"{time.time()}\n")
    elif args.state == "off":
        unlink(MODE_FLAG)

    caps = captures()
    return emit({"focus_mode": mode_on(), "class": classify(caps, mode_on())})


# --- entry ------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="hk47-desktop.py",
        description="The overlord's hands. Every verb prints JSON on stdout.")
    subs = parser.add_subparsers(dest="verb", required=True)

    subs.add_parser("context", help="class, policy, players, streams, focus")
    subs.add_parser("hush", help="silence what may be silenced, and record it")
    subs.add_parser("resume", help="undo exactly what hush did")

    ask = subs.add_parser("ask", help="request permission to speak")
    ask.add_argument("--line", default="Master?", help="the line that would be spoken")

    focus = subs.add_parser("focus", help="move focus to a window")
    focus.add_argument("target", help="address:0x... or class:NAME")

    subs.add_parser("unfocus", help="go back to where focus came from")

    mode = subs.add_parser("mode", help="the declared focus mode")
    mode.add_argument("state", choices=("on", "off", "status"))

    return parser


VERBS = {
    "context": verb_context,
    "hush": verb_hush,
    "resume": verb_resume,
    "ask": verb_ask,
    "focus": verb_focus,
    "unfocus": verb_unfocus,
    "mode": verb_mode,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    return VERBS[args.verb](args)


if __name__ == "__main__":
    sys.exit(main())
