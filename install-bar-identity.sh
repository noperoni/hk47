#!/bin/bash
#
# Put HK-47 on the Omarchy top bar.
#
# Two changes, both inside the package-owned agents plugin:
#
#   * assets/claude.svg      the hero mark drawn at the top of the usage panel
#   * Panel.qml bar glyph    U+F16A3 md-robot_excited -> U+F169D md-robot_angry
#
# Omarchy ships an excited robot for the agents widget. That is the wrong
# diagnosis for an assassination droid.
#
# Both targets live under /usr/share and are replaced wholesale by a package
# upgrade, which is why this script is idempotent and is re-run from
# ~/.config/omarchy/hooks/post-update.d/. It takes exactly one backup per file,
# the first time it runs, so a re-run after an upgrade never overwrites the
# pristine copy with an already-patched one.
#
# Reverting: run with --revert, or copy the .orig files back by hand.

set -euo pipefail

PLUGIN="/usr/share/omarchy/shell/plugins/agents"
MARK_SRC="$(dirname "$(readlink -f "$0")")/assets/hk47.svg"
MARK_DST="$PLUGIN/assets/claude.svg"
PANEL="$PLUGIN/Panel.qml"

GLYPH_STOCK=$'\U000F16A3'   # md-robot_excited
GLYPH_HK47=$'\U000F169D'    # md-robot_angry

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d $PLUGIN ]] || die "agents plugin not found at $PLUGIN"
[[ -f $MARK_SRC ]] || die "mark not found at $MARK_SRC"

if [[ ${1:-} == "--revert" ]]; then
  for f in "$MARK_DST" "$PANEL"; do
    if [[ -f $f.orig ]]; then
      sudo cp -p "$f.orig" "$f"
      say "reverted $f"
    else
      say "no backup for $f, left alone"
    fi
  done
  say "restart the shell to see it: omarchy-restart-shell"
  exit 0
fi

# --- Hero mark -------------------------------------------------------------
[[ -f $MARK_DST.orig ]] || sudo cp -p "$MARK_DST" "$MARK_DST.orig"
if sudo cmp -s "$MARK_SRC" "$MARK_DST"; then
  say "mark already current"
else
  sudo install -m 644 "$MARK_SRC" "$MARK_DST"
  say "installed mark -> $MARK_DST"
fi

# --- Bar glyph -------------------------------------------------------------
[[ -f $PANEL.orig ]] || sudo cp -p "$PANEL" "$PANEL.orig"
if grep -qF "$GLYPH_HK47" "$PANEL"; then
  say "bar glyph already angry"
elif grep -qF "$GLYPH_STOCK" "$PANEL"; then
  sudo sed -i "s/$GLYPH_STOCK/$GLYPH_HK47/g" "$PANEL"
  say "bar glyph -> md-robot_angry"
else
  # Loud rather than silent: upstream changed the glyph, so the patch no longer
  # describes reality and somebody needs to look at it.
  die "neither the stock nor the HK-47 glyph found in $PANEL; upstream changed, re-check the patch"
fi

say "done. Apply with: omarchy-restart-shell"
