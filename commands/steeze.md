---
description: Generate a personal Claude Code practice report from your local session history.
argument-hint: "[--style default|editorial] [--sessions N]"
---

The user invoked `/steeze`. Follow `skills/steeze/SKILL.md` to completion.

Pass through any arguments the user supplied:
- `--style default` (default) or `--style editorial` — selects the HTML report template.
- `--sessions N` — number of recent parent sessions to analyze (default 30).

The skill handles environment setup (`STEEZE_PLUGIN_ROOT`, `STEEZE_RUN_DIR`), Python dependency bootstrapping, the full nine-step pipeline (preprocessing → enrichment → subagent tagging → metrics → open behavioral pass → render), and delivery (print hero markdown inline, open `report.html` in the user's browser).

Do not invent steps not in `SKILL.md`. Do not skip the smoke test or the bootstrap. If something fails, surface the error and stop — do not paper over it.
