---
description: Generate a personal Claude Code practice report from your local session history.
argument-hint: "[--sessions N]"
---

The user invoked `/persona`. Follow `skills/persona/SKILL.md` to completion.

Pass through any arguments the user supplied:
- `--sessions N` — number of recent parent sessions to analyze (default 20).

The skill handles environment setup via the shared `scripts/new_run.sh` run contract (resolves `BIGSPIN_PLUGIN_ROOT`, bootstraps the Python venv, and creates the per-run `OUT_DIR` under `~/.claude/bigspin/persona-<timestamp>/`), the full nine-step pipeline (preprocessing → enrichment → subagent tagging → metrics → open behavioral pass → render), and delivery (print hero markdown inline, open `persona-report.html` in the user's browser).

Before any token-consuming work, the skill shows the user an estimated token usage for the run and requires their explicit consent to proceed (SKILL.md Step 4b). Do not skip that gate.

Do not invent steps not in `SKILL.md`. Do not skip the smoke test or the bootstrap. If something fails, surface the error and stop — do not paper over it.
