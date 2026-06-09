---
description: Chart your Claude Code token-vs-outcome ROI over time from your local session history.
argument-hint: "[--days N] [--no-git]"
---

The user invoked `/token-roi`. Follow `skills/token-roi/SKILL.md` to completion.

Pass through any arguments the user supplied:
- `--days N` — lookback window in days (default 90).
- `--no-git` — skip the local-git committed-line enrichment (faster; drops the committed-line and agent-attributed-line series).

The skill handles environment setup via the shared `scripts/new_run.sh` run contract (resolves `BIGSPIN_PLUGIN_ROOT`, bootstraps the Python venv shared with `/persona`, and creates the per-run `OUT_DIR` under `~/.claude/bigspin/token-roi-<timestamp>/`), the pipeline (compute metrics from `~/.claude/projects/` → render an HTML report with inline SVG charts), and delivery (print the hero summary inline, open `token-roi-report.html` in the user's browser).

Do not invent steps not in `SKILL.md`. Do not skip the smoke test or the bootstrap. If something fails, surface the error and stop — do not paper over it.
