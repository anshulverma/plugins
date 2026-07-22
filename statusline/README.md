# statusline

A custom Claude Code statusline. Two lines:

```
🤖 Claude Opus 4.8 | 💪 high | 🧠 12% | 💰 $0.04 | ⏱️ 5h ████░░░░░░ 42% resets 2:00PM | 🧩 i-have-adhd
📁 my-project | 🌳 my-feature | 🌿 main +42 -7
```

| Field | Meaning |
|---|---|
| 🤖 | Active model |
| 💪 | Effort level (shown only when set) |
| 🧠 | Context window usage |
| 💰 | Session cost (USD) |
| ⏱️ | 5-hour rate-limit bar, %, and reset time |
| 🧩 | `i-have-adhd` mode — shown only when always-on is enabled (see below) |
| 📁 | Repo/folder name |
| 🌳 | Worktree |
| 🌿 | Branch with staged (+) / modified (~) counts |

## i-have-adhd indicator

`🧩 i-have-adhd` appears when the [i-have-adhd](https://github.com/ayghri/i-have-adhd)
always-on flag is set — i.e. when `$CLAUDE_CONFIG_DIR/.i-have-adhd-always` (default
`~/.claude/.i-have-adhd-always`) exists. That flag is the only persistent, always-on
signal; a per-session `/i-have-adhd` (with no flag) leaves nothing on disk for the
statusline to read, so it is not shown in that case.

Toggle it:

```sh
touch ~/.claude/.i-have-adhd-always   # on
rm    ~/.claude/.i-have-adhd-always   # off
```

## Install

Run the repo-root `install.sh`, or manually:

```sh
ln -sfn "$PWD/statusline/statusline-command.sh" ~/.claude/statusline-command.sh
```

then add to `~/.claude/settings.json`:

```json
{ "statusLine": { "type": "command", "command": "sh ~/.claude/statusline-command.sh", "padding": 0 } }
```

Requires `jq` and `git`.

## Credit

Forked from [danielmackay/claude-code-statusline](https://github.com/danielmackay/claude-code-statusline).
The `🧩 i-have-adhd` indicator is a local addition.
