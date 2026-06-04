---
description: Chart your Claude Code token-vs-outcome ROI over time from your local session history.
argument-hint: "[--days N] [--no-git]"
---

The user invoked `/token-roi`. Follow `skills/token-roi/SKILL.md` to completion.

Pass through any arguments the user supplied:
- `--days N` — lookback window in days (default 90).
- `--no-git` — skip the local-git committed-line enrichment (faster; drops the committed-line and agent-attributed-line series).

The skill handles environment setup (`BIGSPIN_PLUGIN_ROOT`), Python dependency bootstrapping (shared venv with `/persona`), the pipeline (compute metrics from `~/.claude/projects/` → render an HTML report with inline SVG charts), and delivery (print the hero summary inline, open `report.html` in the user's browser).

Do not invent steps not in `SKILL.md`. Do not skip the smoke test or the bootstrap. If something fails, surface the error and stop — do not paper over it.
