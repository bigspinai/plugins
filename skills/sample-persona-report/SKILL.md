---
description: Render the persona practice mirror from checked-in sample data so the user can see what `/persona` produces without running the full analysis pipeline. Use when the user asks "what does the persona report look like?", "show me a sample/demo persona report", "preview the persona report", "can I see an example?", "is there a way to test the persona report?", or wants a quick visual of the output. No subagent tagging, no metrics computation, no Anthropic API calls — just the renderer (~500ms) against pre-committed fixtures.
---

# Skill: Render a sample persona report

Goal: produce a working HTML report from the checked-in fixtures so the
user can see what `/persona` would deliver, **without** spending 5–10
minutes running the real pipeline.

This is the "demo" / "preview" path. The orchestrator for the real
analysis (the nine-step pipeline) lives in `skills/persona/SKILL.md`.
**Do not** invoke that skill from here. The two are siblings, not
parent/child.

## What this skill does NOT do

- **No reading `~/.claude/projects/`.** The user's session history is
  never touched.
- **No preprocessing, no tagging, no metrics computation, no open pass,
  no content authoring.** All inputs are pre-committed under
  `skills/persona/`.
- **No Anthropic API calls.** No subagents. No model selection.
- **No `interpret.md` voice work.** The content JSON is fixed.
- **No editing the fixtures.** They are the demo data. If a template
  change requires a fixture update, that belongs in a separate edit to
  the persona skill itself.

If anything in the above list seems necessary to complete the task,
something has gone wrong — stop and surface the issue to the user.

## Inputs

Resolve the plugin root. Prefer `$BIGSPIN_PLUGIN_ROOT`; if unset, walk
up from this file (two levels: `skills/sample-persona-report/SKILL.md`
→ plugin root). All paths below are relative to `$PERSONA` =
`$BIGSPIN_PLUGIN_ROOT/skills/persona`.

Pick the fixture pair based on the optional argument:

| Argument | Content JSON | Metrics JSON |
|---|---|---|
| *(none)* or `showrunner` | `$PERSONA/report/report_content.json` | `$PERSONA/tests/fixtures/sample_metrics.manager.json` |
| `generalist` | `$PERSONA/tests/fixtures/sample_content.generalist.json` | `$PERSONA/tests/fixtures/sample_metrics.generalist.json` |

If the user passes anything else, tell them the supported values and
stop — do not guess.

## Steps

### 1. Bootstrap Python deps (idempotent)

```bash
PY="$(bash "$PERSONA/scripts/bootstrap.sh" "$PERSONA/requirements.txt")"
```

Captures the venv interpreter path on stdout (something like
`~/.claude/bigspin/.venv/bin/python`). If `bootstrap.sh` exits non-zero,
surface stderr and stop — same failure modes as `/persona` (see the
bootstrap script's header comment).

### 2. Pick an output directory

```bash
RUN_ID="sample-$(bash "$PERSONA/scripts/run_id.sh")"
OUT_DIR="${HOME}/.claude/bigspin/${RUN_ID}"
mkdir -p "${OUT_DIR}"
```

The `sample-` prefix makes these directories grep-able and visually
distinct from real `/persona` runs in `~/.claude/bigspin/`.

### 3. Render

```bash
"${PY}" "$PERSONA/analysis/render_report.py" \
    --content "<chosen content JSON>" \
    --metrics "<chosen metrics JSON>" \
    --out "${OUT_DIR}"
```

The renderer validates the content JSON against
`$PERSONA/analysis/report_content.schema.json` and resolves all
`*_ref` paths into the metrics JSON. It runs from any cwd — the
`--content`/`--metrics`/`--out` flags are absolute. On any schema or
ref failure it exits non-zero with a diff — print stderr verbatim and
stop.

Expected artifacts under `${OUT_DIR}` (same set
`$PERSONA/tests/smoke_test.py` validates):

- `report.html`           — full HTML report
- `report.md`             — markdown version
- `hero.md`               — chat-paste summary
- `hero_card.txt`         — CLI hero card with ANSI
- `hero_card.plain.txt`   — same, no ANSI

### 4. Open

```bash
bash "$PERSONA/scripts/open_report.sh" "${OUT_DIR}/report.html"
```

Cross-platform browser-open (macOS `open`, Linux `xdg-open`/`wslview`,
Windows `start`).

### 5. Close

Print exactly one line:

```
Sample persona report (<archetype>) opened: ~/.claude/bigspin/<RUN_ID>/report.html
```

No file inventory, no hero-card paste, no "want me to dive into…"
follow-up. The user asked for a sample; they got a sample.

## Failure modes

- **`bootstrap.sh` can't find `uv` or `python3`.** Surface the
  instructions the script itself prints — same as `/persona`. Don't
  invent a workaround.
- **Renderer schema validation fails.** Almost always means the
  fixture has drifted from the schema. Stop and surface — the fixture
  needs an update in `skills/persona/`, which is not this skill's
  responsibility.
- **`open_report.sh` can't find an opener.** It prints the file path
  for the user to open manually. Pass that through.

## Related

- The real nine-step pipeline lives at `skills/persona/SKILL.md`,
  invoked via `/persona`.
- For active design iteration on the report template with hot-reload,
  use `python skills/persona/analysis/reports_preview.py` (serves all
  seven archetypes on `localhost:8780`). That tool is not exposed as a
  slash command — run it directly from a shell when you're editing
  templates.
