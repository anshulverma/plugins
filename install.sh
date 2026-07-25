#!/bin/sh
# install.sh — activate every plugin in this repo, plus the statusline.
#
# What it does (idempotent, safe to re-run):
#   1. Symlinks each plugin subdir (any dir with .claude-plugin/plugin.json)
#      into $CLAUDE_CONFIG_DIR/plugins/local/<name>.
#   2. Generates a "local" marketplace manifest listing every plugin under
#      plugins/local/, registers it in settings.json (extraKnownMarketplaces),
#      and enables this repo's plugins in settings.json (enabledPlugins). Claude
#      Code discovers plugins via a marketplace + an enabled flag, NOT from the
#      plugins/local symlink alone — without this step nothing loads.
#   3. Symlinks statusline/statusline-command.sh into $CLAUDE_CONFIG_DIR/ and
#      wires the statusLine block into settings.json (merged, existing keys kept).
#
# Requires: jq (used to merge settings.json without clobbering other settings).

set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
LOCAL_DIR="$CLAUDE_DIR/plugins/local"

if ! command -v jq >/dev/null 2>&1; then
  echo "error: jq is required (brew install jq / apt-get install jq)" >&2
  exit 1
fi

mkdir -p "$LOCAL_DIR"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

# 1. Plugins: symlink every subdir that is a self-contained plugin.
echo "Plugins:"
found_plugin=0
repo_plugins=""
for manifest in "$REPO_ROOT"/*/.claude-plugin/plugin.json; do
  [ -e "$manifest" ] || continue
  found_plugin=1
  plugin_dir=$(dirname -- "$(dirname -- "$manifest")")
  name=$(basename -- "$plugin_dir")
  ln -sfn "$plugin_dir" "$LOCAL_DIR/$name"
  repo_plugins="$repo_plugins $name"
  echo "  ✓ symlinked $name -> $LOCAL_DIR/$name"
done
[ "$found_plugin" -eq 1 ] || echo "  (none found)"

# 2. Local marketplace: build the manifest from EVERY plugin under plugins/local
#    (not just this repo's) so pre-existing local plugins keep working, then
#    register the marketplace and enable this repo's plugins.
echo "Marketplace:"
mkdir -p "$LOCAL_DIR/.claude-plugin"
entries=$(mktemp)
: > "$entries"
for m in "$LOCAL_DIR"/*/.claude-plugin/plugin.json; do
  [ -e "$m" ] || continue
  pdir=$(dirname -- "$(dirname -- "$m")")
  pname=$(basename -- "$pdir")
  jq --arg src "./$pname" \
     '{name: .name, version: (.version // "0.0.0"), description: (.description // ""), source: $src, strict: true}' \
     "$m" >> "$entries"
done
jq -s '{name: "local", owner: {name: "Anshul Verma", url: ""}, metadata: {description: "Personal local plugins", version: "1.0.0"}, plugins: .}' "$entries" > "$LOCAL_DIR/.claude-plugin/marketplace.json"
rm -f "$entries"
echo "  ✓ wrote $LOCAL_DIR/.claude-plugin/marketplace.json"

tmp=$(mktemp)
jq --arg path "$LOCAL_DIR" \
   '.extraKnownMarketplaces.local = {source: {source: "directory", path: $path}}' \
   "$SETTINGS" > "$tmp" && mv -f "$tmp" "$SETTINGS"
echo "  ✓ registered local marketplace in $SETTINGS"

for name in $repo_plugins; do
  tmp=$(mktemp)
  jq --arg key "$name@local" '.enabledPlugins[$key] = true' \
     "$SETTINGS" > "$tmp" && mv -f "$tmp" "$SETTINGS"
  echo "  ✓ enabled $name@local"
done

# 3. Statusline: symlink the script and merge the statusLine config.
STATUSLINE_SRC="$REPO_ROOT/statusline/statusline-command.sh"
if [ -f "$STATUSLINE_SRC" ]; then
  echo "Statusline:"
  STATUSLINE_DEST="$CLAUDE_DIR/statusline-command.sh"
  ln -sfn "$STATUSLINE_SRC" "$STATUSLINE_DEST"
  chmod +x "$STATUSLINE_SRC"
  echo "  ✓ script -> $STATUSLINE_DEST"

  tmp=$(mktemp)
  jq --arg cmd "sh $STATUSLINE_DEST" \
     '.statusLine = {type: "command", command: $cmd, padding: 0}' \
     "$SETTINGS" > "$tmp" && mv -f "$tmp" "$SETTINGS"
  echo "  ✓ statusLine wired into $SETTINGS"
fi

echo "Done. Restart Claude Code (or open a new session) to load plugins + statusline."
