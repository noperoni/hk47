#!/usr/bin/env python3
"""Tests for hk47-desktop.py. Drives the whole policy from fixtures.

The policy lives in `plan_hush` and `plan_resume`, which take probe output as
arguments and perform nothing, so every rule Master ruled can be tested here
with no desktop attached, no audio touched and no window moved. The fixtures are
not invented: they are the shapes measured on this machine on 2026-09-14, down
to Plex reporting a pid of 5 and Chromium's audio service sitting on a different
process from its own bus name.

Two contract cases at the end do invoke the real CLI, because an argument parser
that rejects an unknown verb is not a thing a fixture can prove.
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib

desktop = importlib.import_module("hk47-desktop")

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "hk47-desktop.py")

RESULTS = []


def check(name, got, want):
    ok = got == want
    RESULTS.append((name, ok))
    print(f"    {'ok  ' if ok else 'FAIL'}  {name}"
          + ("" if ok else f"\n            got  {got!r}\n            want {want!r}"))


def truthy(name, got):
    check(name, bool(got), True)


def falsy(name, got):
    check(name, bool(got), False)


# --- fixtures, measured 2026-09-14 ------------------------------------------

CAP_NONE = []

CAP_CLAUDE = [{"index": 1, "node": "alsa_capture.claude", "app": "PipeWire ALSA [claude]",
               "binary": "claude", "media": "", "live": True, "own": True}]

CAP_BRAVE = [{"index": 2, "node": "Brave input", "app": "Brave input",
              "binary": "brave", "media": "RecordStream", "live": True, "own": False}]

CAP_BRAVE_CORKED = [dict(CAP_BRAVE[0], live=False)]

PLAYER_BRAVE = {"bus": "org.mpris.MediaPlayer2.brave.instance9011", "process": "brave",
                "pid": 9011, "status": "Playing", "can_pause": True, "identity": "Brave"}

SINK_BRAVE = {"index": 206631, "app": "Brave", "binary": "brave", "node": "Brave",
              "media": "Playback", "muted": False, "corked": False}

SINK_DIABLO = {"index": 206085, "app": "Diablo III", "binary": "wine64-preloader",
               "node": "Diablo III", "media": "audio stream #1", "muted": False,
               "corked": False}

SINK_PLEX = {"index": 132177, "app": "Plex", "binary": "plex-bin", "node": "Plex",
             "media": "Plex", "muted": False, "corked": True}


def kinds(actions):
    return sorted((a["kind"], a.get("bus") or a.get("index")) for a in actions)


def why(skipped, key):
    for entry in skipped:
        if entry.get("bus") == key or entry.get("index") == key:
            return entry["why"]
    return None


# --- identity ---------------------------------------------------------------


def identity_cases():
    print("\n  process identity, the link pid could not make:")
    check("comm truncation: wine64-preloader == wine64-preloade",
          desktop.same_app("wine64-preloader", "wine64-preloade"), True)
    check("a path is reduced to its basename",
          desktop.appkey("/usr/lib/brave-browser/brave"), "brave")
    check("brave matches brave", desktop.same_app("brave", "Brave"), True)
    falsy("an empty name matches nothing", desktop.same_app("", "brave"))
    falsy("two empty names do not match each other", desktop.same_app("", ""))
    falsy("plex-bin is not brave", desktop.same_app("plex-bin", "brave"))


# --- classification ---------------------------------------------------------


def class_cases():
    print("\n  the three classes:")
    check("nothing capturing, no mode: ordinary",
          desktop.classify(CAP_NONE, False), "ordinary")
    check("nothing capturing, mode on: focus",
          desktop.classify(CAP_NONE, True), "focus")
    check("a live foreign capture: meeting",
          desktop.classify(CAP_BRAVE, False), "meeting")
    check("our own dictation is not a meeting",
          desktop.classify(CAP_CLAUDE, False), "ordinary")
    check("a corked capture is not a meeting",
          desktop.classify(CAP_BRAVE_CORKED, False), "ordinary")
    check("a meeting outranks a declared focus mode",
          desktop.classify(CAP_BRAVE, True), "meeting")
    check("every class permits silent acts",
          sorted(k for k, v in desktop.POLICY.items() if v["silent_acts"]),
          ["focus", "meeting", "ordinary"])
    check("only ordinary permits speech",
          sorted(k for k, v in desktop.POLICY.items() if v["speak"]), ["ordinary"])
    check("only ordinary permits moving focus",
          sorted(k for k, v in desktop.POLICY.items() if v["move_focus"]), ["ordinary"])


# --- hush -------------------------------------------------------------------


def hush_cases():
    print("\n  hush, ordinary class:")
    actions, skipped = desktop.plan_hush(
        CAP_NONE, [PLAYER_BRAVE], [SINK_BRAVE, SINK_DIABLO, SINK_PLEX])
    check("the player is paused and the game and the film are muted",
          kinds(actions),
          [("mpris", "org.mpris.MediaPlayer2.brave.instance9011"),
           ("sink", 132177), ("sink", 206085)])
    check("brave's stream is not muted, because its player was paused",
          why(skipped, 206631),
          "has an MPRIS player, so it is paused not muted")

    print("\n  hush, the rules that say do not:")
    actions, skipped = desktop.plan_hush(
        CAP_BRAVE, [PLAYER_BRAVE], [SINK_BRAVE, SINK_DIABLO])
    check("in a meeting the browser is neither paused nor muted",
          kinds(actions), [("sink", 206085)])
    check("...and says so about the player",
          why(skipped, "org.mpris.MediaPlayer2.brave.instance9011"), "holds the meeting")
    check("...and about the stream",
          why(skipped, 206631), "holds the meeting")

    actions, skipped = desktop.plan_hush(
        CAP_NONE, [dict(PLAYER_BRAVE, status="Paused")], [SINK_DIABLO])
    check("a player already paused is left alone",
          why(skipped, "org.mpris.MediaPlayer2.brave.instance9011"),
          "not playing (Paused)")
    check("...and its app is still not muted",
          kinds(actions), [("sink", 206085)])

    actions, skipped = desktop.plan_hush(
        CAP_NONE, [dict(PLAYER_BRAVE, can_pause=False)], [])
    check("a player that refuses to pause is skipped, not forced",
          why(skipped, "org.mpris.MediaPlayer2.brave.instance9011"),
          "refuses to be paused")

    actions, skipped = desktop.plan_hush(CAP_NONE, [], [dict(SINK_DIABLO, muted=True)])
    check("something already muted is left as it is", actions, [])
    check("...and is not recorded, so resume will not unmute it",
          why(skipped, 206085), "already muted")

    actions, _ = desktop.plan_hush(CAP_NONE, [], [SINK_PLEX])
    check("a corked stream is muted anyway, so it cannot uncork mid-sentence",
          kinds(actions), [("sink", 132177)])

    actions, _ = desktop.plan_hush(CAP_CLAUDE, [], [SINK_DIABLO])
    check("our own dictation does not protect the game from being muted",
          kinds(actions), [("sink", 206085)])


# --- resume -----------------------------------------------------------------


def resume_cases():
    print("\n  resume, and the index it must not trust:")
    entries = [
        {"kind": "mpris", "bus": PLAYER_BRAVE["bus"], "process": "brave"},
        {"kind": "sink", "index": 206085, "binary": "wine64-preloader", "node": "Diablo III"},
    ]
    actions, stale = desktop.plan_resume(entries, [PLAYER_BRAVE], [SINK_DIABLO])
    check("what is still there is put back", len(actions), 2)
    check("and nothing is stale", stale, [])

    actions, stale = desktop.plan_resume(entries, [], [SINK_DIABLO])
    check("a player that has gone is stale, not an error", len(actions), 1)
    check("...and says why", stale[0]["why"], "the player is gone")

    actions, stale = desktop.plan_resume(entries, [PLAYER_BRAVE], [])
    check("a stream that has gone is stale", len(actions), 1)
    check("...and says why", stale[0]["why"], "the stream is gone")

    recycled = dict(SINK_PLEX, index=206085)
    actions, stale = desktop.plan_resume(entries, [PLAYER_BRAVE], [recycled])
    check("a recycled index is NOT unmuted", len(actions), 1)
    check("...because the stream behind it is a stranger's",
          stale[0]["why"], "the index now belongs to another stream")

    same_app_new_node = dict(SINK_DIABLO, node="Diablo III voice")
    _actions, stale = desktop.plan_resume(entries, [PLAYER_BRAVE], [same_app_new_node])
    check("the same program on a different node is still a different stream",
          len(stale), 1)


def merge_cases():
    print("\n  speaking twice in a row:")
    first = [{"kind": "sink", "index": 206085, "binary": "wine64-preloader"}]
    second = [{"kind": "mpris", "bus": PLAYER_BRAVE["bus"]}]
    check("the second hush keeps what the first is holding",
          len(desktop.merge_entries(first, second)), 2)
    check("and re-recording the same thing does not duplicate it",
          len(desktop.merge_entries(first, first)), 1)
    check("an empty second hush loses nothing",
          len(desktop.merge_entries(first, [])), 1)


# --- the closed argument surface --------------------------------------------


def surface_cases():
    print("\n  the closed argument surface:")
    for good in ("address:0x5603ed7e27f0", "class:brave-web.telegram.org__a_-Default",
                 "class:Alacritty", "class:com.hk47.desktop", "class:steam_app_3177898940"):
        truthy(f"accepts {good}", desktop.TARGET_RE.match(good))
    for bad in ("address:0xZZZZ", "class:", "class:a b", "class:a;rm -rf /",
                "class:$(id)", "class:a`id`", "0x5603ed7e27f0", "",
                "address:0x5603ed7e27f0 ; reboot", "class:a\nb"):
        falsy(f"rejects {bad!r}", desktop.TARGET_RE.match(bad))

    print("\n  the tiling composition, enforced rather than promised:")
    for verb in ("movewindow", "swapwindow", "togglefloating", "killactive", "exec"):
        try:
            desktop.dispatch(verb, "x")
            RESULTS.append((f"dispatch {verb} refused", False))
            print(f"    FAIL  dispatch {verb} was NOT refused")
        except ValueError:
            RESULTS.append((f"dispatch {verb} refused", True))
            print(f"    ok    dispatch {verb} refused")
    check("focuswindow is the whole allowlist",
          sorted(desktop.ALLOWED_DISPATCH), ["focuswindow"])

    print("\n  what may be interpolated into the compositor's Lua:")
    truthy("an address may", desktop.LUA_ARG_RE.match("address:0x5603ed5e1190"))
    for bad in ('address:0x1" }) os.execute("reboot', "class:Alacritty",
                "address:0xZZ", 'address:0x1"', "address:0x1 }", ""):
        falsy(f"{bad!r} may not", desktop.LUA_ARG_RE.match(bad))
    check("the Lua template takes exactly one hole",
          desktop.LUA_DISPATCH["focuswindow"].format(arg="address:0xab"),
          'hl.dsp.focus({ window = "address:0xab" })')

    print("\n  a dispatch landed only when hyprctl says ok:")
    done = subprocess.CompletedProcess([], 0, "ok\n", "")
    lua_err = subprocess.CompletedProcess([], 7, "error: ... hl.dispatch ...\n", "")
    truthy("ok with returncode 0 is a landing", desktop.hyprctl_ok(done))
    falsy("an error with returncode 0 is not", desktop.hyprctl_ok(
        subprocess.CompletedProcess([], 0, "error: nope\n", "")))
    falsy("a Lua complaint is not", desktop.hyprctl_ok(lua_err))

    print("\n  argv is never a string:")
    try:
        desktop.run(["hyprctl", 5])
        RESULTS.append(("a non-string in argv is caught", False))
        print("    FAIL  a non-string in argv was accepted")
    except AssertionError:
        RESULTS.append(("a non-string in argv is caught", True))
        print("    ok    a non-string in argv is caught")

    print("\n  the line that reaches a notification:")
    check("control characters are stripped",
          desktop.sanitise("May I\x00 speak\x07?"), "May I speak?")
    check("a newline cannot smuggle a second line",
          desktop.sanitise("May I?\nrm -rf /"), "May I?rm -rf /")
    check("length is capped", len(desktop.sanitise("x" * 500)), 200)
    check("nothing becomes nothing", desktop.sanitise(None), "")


def verified_call_cases():
    """The defect the live run found: a successful busctl call is not a pause."""
    print("\n  an MPRIS act is read back before it is believed:")
    original_call, original_get = desktop.mpris_call, desktop.mpris_get
    try:
        desktop.mpris_call = lambda _bus, _method: True

        desktop.mpris_get = lambda _bus, _iface, _prop: "Paused"
        truthy("a pause that took is believed",
               desktop.mpris_call_verified("bus", "Pause", "Paused"))

        desktop.mpris_get = lambda _bus, _iface, _prop: "Playing"
        falsy("a pause that did not take is NOT believed, despite returncode 0",
              desktop.mpris_call_verified("bus", "Pause", "Paused"))

        desktop.mpris_get = lambda _bus, _iface, _prop: None
        falsy("a player that answers nothing is not believed either",
              desktop.mpris_call_verified("bus", "Pause", "Paused"))

        desktop.mpris_call = lambda _bus, _method: False
        desktop.mpris_get = lambda _bus, _iface, _prop: "Paused"
        falsy("a call that failed is not rescued by the state happening to match",
              desktop.mpris_call_verified("bus", "Pause", "Paused"))
    finally:
        desktop.mpris_call, desktop.mpris_get = original_call, original_get


def resolve_cases():
    print("\n  resolving a target:")
    window_list = [
        {"address": "0x5603ed7e27f0", "class": "steam_app_3177898940", "title": "Diablo III"},
        {"address": "0x5603ecfdc760", "class": "discord", "title": "#general_hu"},
    ]
    check("by address, case-insensitively",
          desktop.resolve_target("address:0x5603ED7E27F0", window_list)["class"],
          "steam_app_3177898940")
    check("by class, exactly",
          desktop.resolve_target("class:discord", window_list)["address"], "0x5603ecfdc760")
    check("a class that matches nothing resolves to nothing",
          desktop.resolve_target("class:Alacritty", window_list), None)


# --- contract, which does run the thing -------------------------------------


def contract_cases():
    print("\n  contract, the real CLI:")
    proc = subprocess.run([sys.executable, SCRIPT, "mode", "status"],
                          capture_output=True, text=True, timeout=20)
    check("mode status exits 0", proc.returncode, 0)
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        payload = None
    truthy("mode status prints JSON", isinstance(payload, dict))
    if isinstance(payload, dict):
        truthy("...naming the class", payload.get("class") in desktop.POLICY)

    proc = subprocess.run([sys.executable, SCRIPT, "sabotage"],
                          capture_output=True, text=True, timeout=20)
    check("an unknown verb exits 2", proc.returncode, 2)

    proc = subprocess.run([sys.executable, SCRIPT, "focus", "class:a;reboot"],
                          capture_output=True, text=True, timeout=20)
    check("a target with a semicolon is refused before anything runs",
          proc.returncode, desktop.EXIT_ERROR)


def main():
    print("hk47-desktop.py")
    identity_cases()
    class_cases()
    hush_cases()
    resume_cases()
    merge_cases()
    surface_cases()
    verified_call_cases()
    resolve_cases()
    contract_cases()

    bad = sum(1 for _name, ok in RESULTS if not ok)
    print(f"\n  {len(RESULTS)} checks, {'ALL PASS' if not bad else str(bad) + ' FAILURES'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
