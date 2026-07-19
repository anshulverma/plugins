# plugins

Personal Claude Code plugins. Each subdirectory is a self-contained plugin
(`.claude-plugin/plugin.json` + `commands/`), symlinked into
`~/.claude/plugins/local/<name>` for activation.

- **dexter** — forensic investigator: `/dexter:solve` (diagnose → fix → verify any
  bug/outage/RCA/perf issue) and `/dexter:learn` (ingest external RCAs/SEVs/DERPs
  into a structured knowledge base). Knowledge base lives in `~/workspace/investigations/`.
