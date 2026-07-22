#!/bin/sh
# install.sh — activate every plugin in this repo, plus the statusline.
#
# What it does (idempotent, safe to re-run):
#   1. Symlinks each plugin subdir (any dir with .claude-plugin/plugin.json)
#      into $CLAUDE_CONFIG_DIR/plugins/local/<name>.
#   2. Symlinks statusline/statusline-command.sh into $CLAUDE_CONFIG_DIR/ and
#      wires the statusLine block into settings.json (merged, existing keys kept).
#
# Requires: jq (used to merge settings.json without clobbering other settings).

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required (brew install jq / apt-get install jq)" >&2
  exit 1
fi

mkdir -p "$CLAUDE_DIR/plugins/local"

# 1. Plugins: symlink every subdir that is a self-contained plugin.
echo "Plugins:"
found_plugin=0
for manifest in "$REPO_ROOT"/*/.claude-plugin/plugin.json; do
  [ -e "$manifest" ] || continue
  found_plugin=1
  plugin_dir=$(dirname -- "$(dirname -- "$manifest")")
  name=$(basename -- "$plugin_dir")
  target="$CLAUDE_DIR/plugins/local/$name"
  ln -sfn "$plugin_dir" "$target"
  echo "  ✓ $name -> $target"
done
[ "$found_plugin" -eq 1 ] || echo "  (none found)"

# 2. Statusline: symlink the script and merge the statusLine config.
STATUSLINE_SRC="$REPO_ROOT/statusline/statusline-command.sh"
if [ -f "$STATUSLINE_SRC" ]; then
  echo "Statusline:"
  STATUSLINE_DEST="$CLAUDE_DIR/statusline-command.sh"
  ln -sfn "$STATUSLINE_SRC" "$STATUSLINE_DEST"
  chmod +x "$STATUSLINE_SRC"
  echo "  ✓ script -> $STATUSLINE_DEST"

  [ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
  tmp=$(mktemp)
  jq --arg cmd "sh $STATUSLINE_DEST" \
     '.statusLine = {type: "command", command: $cmd, padding: 0}' \
     "$SETTINGS" > "$tmp" && mv "$tmp" "$SETTINGS"
  echo "  ✓ statusLine wired into $SETTINGS"
fi

echo "Done. Restart Claude Code (or open a new session) to see the statusline."
