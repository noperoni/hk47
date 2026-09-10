# HK-47

A pixel-art desktop companion for Hyprland. HK-47 stands in a lit corridor
diorama in a corner of your screen, idling, shifting stance and sweeping the room
with a scanner beam. A badge on his alcove counts the Claude Code sessions that
want your attention.

He is a sprite in a diorama, not a chat client and not a button. Clicking him
does nothing.

## Provenance

This is a fork of Alfred (`/path/to/upstream`, Plane project BUDY),
taken at the point Alfred gained the diorama backdrop and project launcher. It is
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
system, the attention badge and the IPC.

Fixes worth carrying back upstream, should Alfred ever want them: the `Error`
animation state, the attention badge, the theme-owned geometry block, the
per-frame contact shadow and the scan beam are all theme-agnostic.

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

## The attention badge

A coloured disc in the top-right corner counts what is waiting for you across
*every* Claude Code session on the machine:

- **amber** — one or more sessions are stopped at a permission prompt, or have
  notified that they want input
- **red** — at least one session has a tool failure you have not answered

HK-47 cannot see other sessions from inside his own process, so the count is
kept for him by a hook script that drops one flag file per session under
`$XDG_RUNTIME_DIR/hk47/badge`. He polls that directory once a second. The runtime
dir is tmpfs, so the state clears itself on reboot and no reaping is needed.

Flags clear on different events, because the two mean different things. A
`blocked` flag clears on the tool call that follows an approval, on the end of
the turn, or on your next prompt — whichever lands first. An `error` flag clears
only when you next type into that session, on the reasoning that answering the
prompt is what proves you saw it. An error you never returned to keeps counting.

### Installing the hook

Symlink the script somewhere stable and register it on both accounts:

```sh
ln -sfn "$PWD/contrib/hk47-badge-hook.py" ~/.claude/hooks/hk47_badge.py
```

Then add it to `hooks` in each `settings.json` you use, on `PermissionRequest`,
`Notification`, `PostToolUseFailure` (matcher `*`), `PostToolUse`, `Stop`,
`UserPromptSubmit` and `SessionEnd`, as a `command` hook with `async: true`.

Register `PostToolUseFailure` with matcher `*`, not `Bash`. peon-ping's own entry
matches `Bash` alone and therefore never sees a failed Edit, Write or MCP call.

## Animation states

`theme.toml` may define `idle`, `idle_alt`, `scan_l`, `scan_r`, `attentive`,
`thinking` and `error`. Any state without art falls back to the idle sheet with a
warning on stderr, so a theme is never required to supply all of them.

The idle rotation cuts between four beats — `idle`, `scan_l`, `idle_alt`,
`scan_r` — holding each for a randomised 10 to 15 seconds and freezing on its
last frame rather than looping, which is what stops him fidgeting once a second.
The two scan beats turn his head 45° and sweep a beam across the room, flashing
the emitter at ignition and shutdown and holding the turn for half a second after
it goes dark.

`error` is entered when the badge's error count goes from zero to non-zero, and
left again when it returns to zero. `attentive` and `thinking` have art but no
driver at present: they are waiting on the separate question and permission
counters.

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

Three numbers interlock. `config.sprite.size` divided by `frame_size` gives the
diorama's scale, which sets the window size; `geometry.scale` multiplies the
figure back up from there. For the `hk47` pack, 96 over 128 gives 0.75 and a
scale of 4/3 returns the figure to exactly 1.0 — native pixel density for him,
while only the room is reduced. Move one and the others must move with it.

## Configuration

Config lives at `~/.config/hk47/config.toml`. It's generated with defaults on
first run; any missing section falls back to its defaults, and unknown keys are
ignored, so a config written for an older build keeps working.

```toml
[sprite]
fps = 24
size = 128                # px; the badge disc is sized at 22% of this
theme = "default"         # loads ~/.config/hk47/sprites/<theme>/, "default" = embedded
```

There is no placement section. A Wayland client cannot set its own coordinates,
so where he opens is a Hyprland window rule against `com.hk47.desktop` — see
`contrib/hyprland.conf`.

The sprite pack itself is symlinked in from this project rather than copied:

```sh
ln -sfn /path/to/hk47/sprites/hk47 ~/.config/hk47/sprites/hk47
```
