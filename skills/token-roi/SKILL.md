---
description: Render a personal Claude Code token-ROI report from your local session history. Use when the user asks "am I getting more output per token over time?", "what's my tokens-per-PR / tokens-per-committed-line trend?", "chart my Claude Code token usage vs outcomes", or runs /token-roi. Reads ~/.claude/projects/, computes weekly token/activity/engineering-outcome metrics, and renders an HTML report with inline SVG charts. All local — no API key, no network.
---

# Skill: Token-ROI mirror

Goal: produce an HTML report that answers *"am I getting more engineering
output per token over time?"* — weekly trends of token usage against
activity and engineering outcomes (committed lines, PRs, agent draft
lines), plus tokens-per-unit-of-work cost ratios.

You're the orchestrator. Everything stays **local** — the pipeline reads
`~/.claude/projects/` (where Claude Code already stores session history),
optionally shells out to local `git`, and writes artifacts to
`~/.claude/bigspin/<run_id>/`. No API key, no network, no data leaves the
machine.

This is a **sibling** of the `/persona` skill (`skills/persona/SKILL.md`),
not a child. `/persona` characterizes your *style/archetype*; this skill
measures your *token efficiency*. Do not invoke the persona pipeline from
here.

## What this skill does NOT do

- **No subagents, no tagging, no LLM calls.** Every number is computed
  deterministically from transcript structure + local git. There is no
  model-pinning concern.
- **No network.** Charts are inline SVG rendered server-side; the HTML is
  self-contained.
- **No editing the bundled scripts, fixtures, or the persona skill.**

## Inputs

Resolve the plugin root. Prefer `$BIGSPIN_PLUGIN_ROOT`; if unset, walk up
from this file (two levels: `skills/token-roi/SKILL.md` → plugin root).

Path shorthands used below:
- `$TR` = `$BIGSPIN_PLUGIN_ROOT/skills/token-roi`
- `$SCRIPTS` = `$BIGSPIN_PLUGIN_ROOT/skills/persona/scripts` (the shared
  `bootstrap.sh`, `run_id.sh`, `open_report.sh` live under the persona
  skill — reuse them, don't copy).

Arguments (both optional, pass through from `/token-roi`):
- `--days N` — lookback window in days (default 90).
- `--no-git` — skip local-git committed-line enrichment.

If the user passes anything else, tell them the supported flags and stop.

## Steps

### 1. Bootstrap Python deps (idempotent)

```bash
PY="$(bash "$SCRIPTS/bootstrap.sh" "$TR/requirements.txt")"
```

Captures the venv interpreter path on stdout (e.g.
`~/.claude/bigspin/.venv/bin/python`). `$TR/requirements.txt` is
byte-identical to persona's, so this shares the same venv — it will **not**
rebuild when the user has already run `/persona`. If `bootstrap.sh` exits
non-zero, surface stderr and stop (same failure modes as `/persona`: exit 2
= no uv/python3, exit 3 = venv/pip failure).

### 2. Renderer smoke test

```bash
"${PY}" "$TR/tests/smoke_test.py"
```

Validates the renderer against the checked-in fixture before touching real
data. If it fails, the renderer or schema has drifted — surface stderr and
stop.

### 3. Pick an output directory

```bash
RUN_ID="roi-$(bash "$SCRIPTS/run_id.sh")"
OUT_DIR="${HOME}/.claude/bigspin/${RUN_ID}"
mkdir -p "${OUT_DIR}"
```

The `roi-` prefix keeps these runs visually distinct from `/persona` runs
in `~/.claude/bigspin/`.

### 4. Compute metrics

```bash
"${PY}" "$TR/preprocessing/compute_roi.py" \
    --out "${OUT_DIR}" \
    --days <N> \
    [--no-git]
```

Reads `~/.claude/projects/`, aggregates per-session token / activity /
outcome metrics into weekly buckets, and writes `roi_data.json` (+
`roi.csv`) to `${OUT_DIR}`. With local git available (default), it also
attributes committed lines per session; `--no-git` skips that and those
series degrade gracefully (rendered as "no git data"). Print the script's
summary lines (session count, date range, medians) inline.

If there are no sessions in the window, the script says so — relay that and
stop; there's nothing to render.

### 5. Render the report

```bash
"${PY}" "$TR/analysis/render_report.py" \
    --data "${OUT_DIR}/roi_data.json" \
    --out "${OUT_DIR}"
```

Validates `roi_data.json` against `analysis/roi_data.schema.json`, builds
the three inline-SVG charts (weekly trend, outcome-bucket distributions,
tokens-per-unit-of-work cost ratios), and writes `report.html` + `hero.md`
to `${OUT_DIR}`. On any schema/validation failure it exits non-zero with a
diff — print stderr verbatim and stop.

### 6. Deliver

```bash
bash "$SCRIPTS/open_report.sh" "${OUT_DIR}/report.html"
```

Then paste the contents of `${OUT_DIR}/hero.md` inline in the chat so the
user gets the headline numbers without leaving the terminal.

Close with one line pointing at the report:

```
Token-ROI report opened: ~/.claude/bigspin/<RUN_ID>/report.html
```

## Failure modes

- **`bootstrap.sh` can't find `uv` or `python3`.** Surface the instructions
  the script prints. Don't invent a workaround.
- **Smoke test fails.** Renderer/schema drift — stop and surface; don't run
  against real data on a broken renderer.
- **Renderer schema validation fails on real data.** `compute_roi.py`
  emitted a shape the schema rejects — surface the diff; this is a bug to
  fix, not to paper over.
- **`open_report.sh` can't find an opener.** It prints the file path for
  the user to open manually. Pass that through.

## Related

- `/persona` (`skills/persona/SKILL.md`) — the style/archetype mirror.
  Shares the same venv and the same `scripts/` helpers.
