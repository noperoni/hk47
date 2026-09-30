#!/bin/bash
#
# Install the hk47.voice bar widget as a real folder in ~/.config.
#
# Not a symlink into the checkout: the checkout lives on NFS, and a boot where
# the network comes up after the shell leaves the link dangling at the one
# moment the shell scans for plugins, so the widget silently never loads. The
# Omarchy validator refuses symlinked plugin folders for the same reason.
#
# Re-run after editing the widget. Adding it to the bar is a separate step:
#   omarchy plugin enable hk47.voice

set -euo pipefail

SRC="$(dirname "$(readlink -f "$0")")"
DST="$HOME/.config/omarchy/plugins/hk47.voice"
OVERSEER="$(readlink -f "$SRC/../../../hk47-overseer.py")"

[[ -f $OVERSEER ]] || { echo "ERROR: overseer not found at $OVERSEER" >&2; exit 1; }

# Staged outside the plugins folder, which the shell watches and scans.
mkdir -p "$HOME/.cache"
STAGE="$(mktemp -d "$HOME/.cache/hk47.voice.XXXXXX")"
chmod 755 "$STAGE"
cp -r "$SRC/BarWidget.qml" "$SRC/manifest.json" "$SRC/bin" "$STAGE/"
printf '%s\n' "$OVERSEER" > "$STAGE/overseer.path"
omarchy plugin validate "$STAGE"

# A symlink or an older copy is replaced, never merged into.
[[ -L $DST ]] && rm "$DST"
[[ -d $DST ]] && mv "$DST" "$STAGE.old"
mv "$STAGE" "$DST"
rm -rf "$STAGE.old"

omarchy-shell shell rescanPlugins >/dev/null
echo "installed $DST"
