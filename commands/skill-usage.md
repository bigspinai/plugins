---
description: Rank which Claude Code skills you read/invoke most and least, and flag skill files worth updating — from your local session history.
argument-hint: "[--days N] [--repo-root PATH] [--stale-days N] [--no-git]"
---

The user invoked `/skill-usage`. Follow `skills/skill-usage/SKILL.md` to completion.

Pass through any arguments the user supplied:
- `--days N` — lookback window in days (default: all history).
- `--repo-root PATH` — repo to scan for the on-disk skill inventory (default: current working directory).
- `--extra-root PATH` — add another inventory root (repeatable).
- `--stale-days N` — age past which a SKILL.md counts as stale for update flags (default 60).
- `--no-git` — use file mtime instead of `git log` for last-modified dates.

The skill handles environment setup via the shared `scripts/new_run.sh` run contract (resolves `BIGSPIN_PLUGIN_ROOT`, bootstraps the Python venv shared with `/persona` and `/token-roi`, and creates the per-run `OUT_DIR` under `~/.claude/bigspin/skill-usage-<timestamp>/`), the pipeline (count Skill-tool invocations + SKILL.md reads from `~/.claude/projects/`, cross-reference the skills on disk → render an HTML report with inline SVG charts), and delivery (print the hero summary inline, open `skill-usage-report.html` in the user's browser).

Do not invent steps not in `SKILL.md`. Do not skip the smoke test or the bootstrap. If something fails, surface the error and stop — do not paper over it.
