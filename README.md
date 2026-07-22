# plugins

Personal Claude Code plugins. Each subdirectory is a self-contained plugin
(`.claude-plugin/plugin.json` + `commands/`), symlinked into
`~/.claude/plugins/local/<name>` for activation.

- **dexter** — forensic investigator: `/dexter:solve` (diagnose → fix → verify any
  bug/outage/RCA/perf issue) and `/dexter:learn` (ingest external RCAs/SEVs/DERPs
  into a structured knowledge base). Knowledge base lives in `~/workspace/investigations/`.

Plus a non-plugin extra:

- **statusline** — a custom Claude Code statusline (model, effort, context, cost,
  rate limit, git). Shows a `🧩 i-have-adhd` badge when that mode is on. See
  [`statusline/README.md`](statusline/README.md).

## Install

```sh
./install.sh
```

Symlinks every plugin into `~/.claude/plugins/local/<name>` and installs the
statusline (symlinks the script and wires the `statusLine` block into
`~/.claude/settings.json`). Idempotent; requires `jq`.
