---
description: Render a personal Claude Code skill-usage report from your local session history. Use when the user asks "which skills do I use most / least?", "which skill files are worth updating?", "are any of my skills never used?", or runs /skill-usage. Reads ~/.claude/projects/, counts Skill-tool invocations and SKILL.md reads, cross-references the skills on disk, and renders an HTML report with inline SVG charts. All local — no API key, no network.
---

# Skill: Skill-usage mirror

Goal: produce an HTML report that answers *"which skills do I actually read
and invoke, which never get used, and which skill files are worth a
refresh?"* — a ranking of skills by usage, a weekly trend, a read-vs-invoke
split, and deterministic "update candidate" flags.

You're the orchestrator. Everything stays **local** — the pipeline reads
`~/.claude/projects/` (where Claude Code already stores session history),
scans the repo on disk for the skill inventory, optionally shells out to
local `git` for file dates, and writes artifacts to
`~/.claude/bigspin/<run_id>/`. No API key, no network, no data leaves the
machine.

This is a **sibling** of the `/token-roi` and `/persona` skills, not a child.
`/token-roi` measures token efficiency; `/persona` characterizes your style;
this skill measures which *skills* you use. Do not invoke those pipelines
from here.

## What this skill does NOT do

- **No subagents, no tagging, no LLM calls.** Every number is computed
  deterministically from transcript structure + the skill files on disk.
- **No network.** Charts are inline SVG rendered server-side; the HTML is
  self-contained (fonts and logos base64-embedded).
- **No editing the bundled scripts, fixtures, or sibling skills.**

## Two usage signals

A skill is counted as "used" when a transcript shows either:
- **Signal A — Skill-tool invocation:** an assistant `tool_use` with
  `name == "Skill"` and `input.skill` = the skill name. Plugin-namespaced
  names (`bigspin:persona`) fold onto their base (`persona`).
- **Signal B — SKILL.md read:** a `Read` tool call whose `file_path`
  contains `SKILL.md`; the skill is the parent directory. These often happen
  inside subagents (sidechain entries) — that's real usage and is counted,
  with the subagent share surfaced separately.

Skills the harness auto-loads without an explicit read/invocation are **not**
counted — treat the numbers as a lower bound.

## Inputs

Resolve the plugin root. Prefer `$BIGSPIN_PLUGIN_ROOT`; if unset, walk up
from this file (two levels: `skills/skill-usage/SKILL.md` → plugin root).

Path shorthands used below:
- `$SU` = `$BIGSPIN_PLUGIN_ROOT/skills/skill-usage`
- `$SCRIPTS` = `$BIGSPIN_PLUGIN_ROOT/scripts` (the shared run contract and
  helpers — `new_run.sh`, `bootstrap.sh`, `run_id.sh`, `open_report.sh`).

Arguments (all optional, pass through from `/skill-usage`):
- `--days N` — lookback window in days (default: all history).
- `--repo-root PATH` — repo to scan for the skill inventory (default: the
  current working directory). Point this at the repo whose skills you care
  about flagging as used/unused.
- `--extra-root PATH` — add another inventory root (repeatable).
- `--stale-days N` — age past which a SKILL.md counts as stale for the
  update flags (default 60).
- `--no-git` — use file mtime instead of `git log` for last-modified dates.

If the user passes anything else, tell them the supported flags and stop.

## Steps

### 1. Start the run (shared contract: bootstrap + output dir)

```bash
eval "$(bash "$SCRIPTS/new_run.sh" skill-usage)"
```

`new_run.sh` bootstraps the shared venv (reused with `/persona` and
`/token-roi`), creates the output directory, and exports `PY` (venv
interpreter), `RUN_ID` (`skill-usage-<timestamp>`), `OUT_DIR`
(`~/.claude/bigspin/$RUN_ID`, already created), plus `BIGSPIN_PLUGIN_ROOT`
and `PYTHONPATH`. Output always lands under `$OUT_DIR` regardless of where
the skill was launched. If it exits non-zero, surface stderr and stop
(exit 2 = no uv/python3, exit 3 = venv/pip failure).

### 2. Renderer smoke test

```bash
"${PY}" "$SU/tests/smoke_test.py"
```

Validates the renderer against the checked-in fixture before touching real
data. If it fails, the renderer or schema has drifted — surface stderr and
stop.

### 3. Compute usage

```bash
"${PY}" "$SU/preprocessing/compute_usage.py" \
    --out "${OUT_DIR}" \
    --repo-root "$(pwd)" \
    [--days <N>] \
    [--extra-root <PATH>] \
    [--stale-days <N>] \
    [--no-git]
```

Reads `~/.claude/projects/`, extracts the two usage signals, builds the
on-disk skill inventory (de-duped by name across worktrees/mirrors), and
writes `usage_data.json` (+ `usage.csv`) to `${OUT_DIR}`. Print the script's
summary lines (window, skills used vs inventory, most used, never-used count,
reads vs invocations, top 5) inline.

If there are no events in the window, the script says so — relay that and
stop; there's nothing to render.

### 4. Render the report

```bash
"${PY}" "$SU/analysis/render_report.py" \
    --data "${OUT_DIR}/usage_data.json" \
    --out "${OUT_DIR}" \
    --slug skill-usage
```

Validates `usage_data.json` against `analysis/usage_data.schema.json`, builds
the three inline-SVG charts (ranking, weekly trend, reads-vs-invocations
split) plus the update-candidates section, and writes
`skill-usage-report.html` + `skill-usage-hero.md` to `${OUT_DIR}`. On any
schema/validation failure it exits non-zero with a diff — print stderr
verbatim and stop.

### 5. Deliver

```bash
bash "$SCRIPTS/open_report.sh" "${OUT_DIR}/skill-usage-report.html"
```

Then paste the contents of `${OUT_DIR}/skill-usage-hero.md` inline in the
chat so the user gets the headline numbers without leaving the terminal.

Close with one line pointing at the report:

```
Skill-usage report opened: ~/.claude/bigspin/<RUN_ID>/skill-usage-report.html
```

## Failure modes

- **`bootstrap.sh` can't find `uv` or `python3`.** Surface the instructions
  the script prints. Don't invent a workaround.
- **Smoke test fails.** Renderer/schema drift — stop and surface; don't run
  against real data on a broken renderer.
- **Renderer schema validation fails on real data.** `compute_usage.py`
  emitted a shape the schema rejects — surface the diff; this is a bug to
  fix, not to paper over.
- **Inventory is empty / everything reads "(not in repo)".** `--repo-root`
  is probably pointing somewhere without skills. Re-run with the correct
  repo root.
- **`open_report.sh` can't find an opener.** It prints the file path for the
  user to open manually. Pass that through.

## Related

- `/token-roi` (`skills/token-roi/SKILL.md`) — token-vs-outcome ROI mirror.
- `/persona` (`skills/persona/SKILL.md`) — the style/archetype mirror.
  All three share the same venv and the same `scripts/` helpers.
