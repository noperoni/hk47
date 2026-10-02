#!/usr/bin/env bash
# Copy the hooks into ~/.claude/hooks as real files, never symlinks onto the share.
# A symlink onto NFS means an outage takes the danger gate down with it, and
# anyone who can write the share can rewrite the gate (PERS-32).
# Rerun after every change to a hook: the installed copy does not follow the repo.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
dest="${CLAUDE_HOOKS_DIR:-$HOME/.claude/hooks}"
mkdir -p "$dest"
while read -r src name; do
    tmp="$dest/.$name.tmp"
    install -m 755 "$src" "$tmp"
    mv -f "$tmp" "$dest/$name"   # rename over the old file or symlink, never a half-written hook
    echo "installed $name"
done <<'EOF'
hk47-danger-gate.py hk47_danger_gate.py
hk47-danger-gate-answer.py hk47_danger_gate_answer.py
hk47-persona-reminder.py persona_reminder.py
companion/contrib/hk47-badge-hook.py hk47_badge.py
EOF
