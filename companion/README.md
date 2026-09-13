# HK-47

A pixel-art desktop companion for Hyprland. HK-47 stands in a lit corridor
diorama in a corner of your screen, idling, shifting stance and sweeping the room
with a scanner beam. Three of the corridor's own wall consoles light up to count
the Claude Code sessions waiting on you.

He is a sprite in a diorama, not a chat client and not a button. Clicking him
does nothing.

## Provenance

This is a fork of Alfred, a private predecessor project, taken at the point Alfred gained the diorama backdrop and project launcher. It is
built to run **alongside** Alfred rather than replace him, so every name that
would otherwise collide has been changed:

| | Alfred | this fork |
|---|---|---|
| binary | `alfred` | `hk47` |
| IPC socket | `/run/user/$UID/alfred.sock` | `/run/user/$UID/hk47.sock` |
| config dir | `~/.config/alfred/` | `~/.config/hk47/` |
| GTK app id | `com.alfred.desktop` | `com.hk47.desktop` |
| systemd unit | `alfred.service` | `hk47.service` |
| keybind | `SUPER B` chat, `SUPER CTRL B` start | `SUPER H` presence |

Two processes sharing any one of those cannot both run: whichever binds the
socket first owns `toggle`, and the other is left without a listener.

Alfred's chat bubble, intent router, project picker and `claude` subprocess have
all been removed from this fork. What remains is the diorama, the animation
system, the attention readouts and the IPC.

Fixes worth carrying back upstream, should Alfred ever want them: the `Error`
animation state, the theme-owned geometry block, the per-frame contact shadow and
the scan beam are all theme-agnostic. The attention readouts are not: they are
drawn into rects a theme declares against its own backdrop art, so a pack with no
`[readouts]` block simply shows no counts.

## Requirements

- Hyprland (or another wlroots compositor)

The window is a plain xdg-toplevel, not a layer-shell surface, so the compositor
can drag it freely across monitors. The tradeoff is that it cannot render above a
fullscreen window.

## Build

```sh
cargo build --release
# binary at target/release/hk47
```

## Commands

Both talk to the running instance over the Unix socket, so neither spawns a
second window.

| Command | Effect | Exit code when nothing is running |
|---|---|---|
| `hk47 toggle` | hides or shows the window, leaving the process running | `1` |
| `hk47 quit` | removes the socket and stops the process | `1` |

That non-zero exit is load-bearing: a failed `quit` is how a presence script
learns he is not running, without racing a `pgrep` between check and action.

## Hyprland integration

Add to your Hyprland config (or source `contrib/hyprland.conf`):

```
# One key for presence: put him on screen, or take him off.
bindd = SUPER, H, Show/hide HK-47, exec, ~/.config/hypr/scripts/hk47-presence.sh
```

where the script is the single line `hk47 quit 2>/dev/null || uwsm-app -- hk47`.

No autostart, matching upstream Alfred, who has no `exec-once`: he appears when
summoned. Avoid `SUPER CTRL H` on Omarchy, which is its hardware menu.

He is moved with SUPER + left-drag, which is Omarchy's global window-move bind
and needs no per-app rule.

## The attention readouts

Three of the corridor's wall consoles double as counters, tallying what is
waiting for you across *every* Claude Code session on the machine:

| console | counts | colour |
|---|---|---|
| the tan notice board at his shoulder | sessions blocked on an `AskUserQuestion` call | red |
| the glyph screen on the left wall | sessions stopped at a permission prompt | amber |
| the small panel beneath it | sessions that finished a turn and want a prompt | cyan |

A live counter repaints the glass inside its bezel with dark glass, a lit rim, a
scanline and a chunky hand-built numeral. A dead one draws nothing at all, so at
rest the backdrop is exactly as painted and there is no evidence anything was
ever added. Counts above nine clamp to `9+`.

Position is the identity: a given console always means the same counter. That is
what makes icons unnecessary, which matters, because no padlock survives being
drawn at seven pixels square.

The rects are declared per theme, in backdrop pixels, so the readouts are not
theme-agnostic and a pack that omits them shows no counts. See `[readouts]` under
Theme geometry.

Errors are deliberately not counted. A failed tool call is the session's own
business to report in its transcript; these three answer "who is waiting for me",
and a failure Claude is still working around is not waiting for anybody.

HK-47 cannot see other sessions from inside his own process, so the counts are
kept for him by a hook script that drops one flag file per session under
`$XDG_RUNTIME_DIR/hk47/badge`. He polls that directory once a second. The runtime
dir is tmpfs, so the state clears itself on reboot and no reaping is needed.

The three states are mutually exclusive per session: raising one clears the other
two, so the counts sum to the number of sessions waiting rather than to something
larger than the number of sessions open. Every flag clears on `UserPromptSubmit`
and on `SessionEnd`.

### Installing the hook

Symlink the script somewhere stable and register it on both accounts:

```sh
ln -sfn "$PWD/contrib/hk47-badge-hook.py" ~/.claude/hooks/hk47_badge.py
```

Then add it to `hooks` in each `settings.json` you use, as a `command` hook with
`async: true`, on `PermissionRequest`, `Stop`, `UserPromptSubmit`, `SessionEnd`,
and on `PreToolUse` and `PostToolUse` with matcher `AskUserQuestion`.

Do not register it on `Notification`. That event fires both for a permission
prompt and for an idle session, so it duplicates `PermissionRequest` and `Stop`
with worse timing and no way to tell the two apart except by parsing its message
text.

The `AskUserQuestion` matcher is enforced in the script as well as in
`settings.json`, so a wildcard entry copied in by hand cannot raise the question
count on every tool call in the session.

## Animation states

`theme.toml` may define `idle`, `idle_alt`, `scan_l`, `scan_r`, `attentive`,
`thinking` and `error`. Any state without art falls back to the idle sheet with a
warning on stderr, so a theme is never required to supply all of them.

The idle rotation cuts between four beats (`idle`, `scan_l`, `idle_alt`,
`scan_r`), holding each for a randomised 10 to 15 seconds and freezing on its
last frame rather than looping, which is what stops him fidgeting once a second.
The two scan beats turn his head 45° and sweep a beam across the room, flashing
the emitter at ignition and shutdown and holding the turn for half a second after
it goes dark.

`error`, `attentive` and `thinking` all have art but no driver. The idle rotation
is the only behaviour, and nothing outside the process may interrupt it until a
state has an agreed behaviour of its own. `set_state` is retained and unwired.

## Theme geometry

`theme.toml` carries a `[geometry]` block that the draw loop reads instead of
hardcoding, so a repack can re-frame him without a recompile:

| key | meaning |
|---|---|
| `frame_size` | native width of one animation frame |
| `feet_y` | his soles' y within that frame |
| `floor_y` | the diorama's floor line, in backdrop coordinates |
| `scale` | figure size relative to `config.sprite.size` |
| `border` | width of the backdrop's frame line |

Alongside it, an optional `[readouts]` table names the wall consoles that carry
the attention counters. Each key is a counter name, and `rect` is the glass in
backdrop pixels with `x1`/`y1` exclusive:

```toml
[readouts.question]
rect = [178, 95, 191, 116]
colour = [214, 56, 46]
```

Valid keys are `question`, `permission` and `waiting`. A face narrower than five
pixels or shorter than seven is skipped, on the grounds that no numeral survives
it. The `hk47` pack's rects are derived in `assemble-hk47-pack.py` from
coordinates in the *original* room art, so a change to the crop or the border
moves them rather than stranding them.

Three numbers interlock. `config.sprite.size` divided by `frame_size` gives the
diorama's scale, which sets the window size; `geometry.scale` multiplies the
figure back up from there. For the `hk47` pack, 96 over 128 gives 0.75 and a
scale of 4/3 returns the figure to exactly 1.0: native pixel density for him,
while only the room is reduced. Move one and the others must move with it.

## Configuration

Config lives at `~/.config/hk47/config.toml`. It's generated with defaults on
first run; any missing section falls back to its defaults, and unknown keys are
ignored, so a config written for an older build keeps working.

```toml
[sprite]
fps = 24
size = 128                # px; his native frame width, and the window's opening size
theme = "default"         # loads ~/.config/hk47/sprites/<theme>/, "default" = embedded
```

There is no placement section. A Wayland client cannot set its own coordinates,
so where he opens is a Hyprland window rule against `com.hk47.desktop`: see
`contrib/hyprland.conf`.

The sprite pack itself is symlinked in from this project rather than copied:

```sh
ln -sfn "$(git rev-parse --show-toplevel)/sprites/hk47" ~/.config/hk47/sprites/hk47
```
