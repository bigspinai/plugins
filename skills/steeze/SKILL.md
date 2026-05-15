---
name: steeze
description: Generates a personal Claude Code practice report ("archetype mirror") by analyzing the user's local ~/.claude/projects session history against a measured baseline corpus. Use when the user invokes /steeze, or when they ask for a "claude code archetype", "practice report", "session analysis", or "what's my coding style". The report is rendered locally as HTML; no data leaves the user's machine.
---

# Skill: Run the Steeze Practice Mirror

You are the orchestrator. The user invoked `/steeze` (or asked for an archetype report). Walk through the steps below in order. Everything stays local — no API key required, no data leaves their machine.

The full voice + content rules live in `analysis/interpret.md` (alongside this file in the plugin install). Read that **after** you have `metrics.json` + `findings.md` and before you write `report_content.json`. Don't skip it.

## At a glance — dual-track architecture

```
preprocessing/sessions_to_csv.py   → $RUN/sessions.csv             (1 row/session)
preprocessing/enrich.py            → $RUN/sessions_enriched.csv    (+ deterministic signal cols)

[STRUCTURED TRACK — for positioned numbers]
tagging/tag_sessions.py            → $RUN/tag_prompt.md, $RUN/transcripts/, $RUN/_manifest.json
[you orchestrate subagents]        → $RUN/annotations/<session_id>.json
tagging/tag_sessions.py --assemble → $RUN/tagged_sessions.csv
analysis/compute_metrics.py        → $RUN/metrics.json

[OPEN TRACK — for rich behavioral observation]
[you orchestrate one open subagent] → $RUN/findings.md

[AUTHORING — synthesis between both tracks]
[you author]                       → $RUN/report_content.json
analysis/render_report.py          → $RUN/report.html, report.md, hero.md, hero_card.txt
```

Where `$RUN` is `~/.claude/steeze/<timestamp>/`. The plugin's read-only assets (scripts, baselines, templates) live under `$PLUGIN_ROOT/skills/steeze/...` and are referenced by absolute path.

The output is **three artifacts sharing copy verbatim**: an HTML report, a CLI hero card, and a markdown report. All three render from a single `report_content.json`.

The pipeline has **three** layers:

1. **Deterministic signals** (`enrich.py`) — ~14 structural columns from message structure. Iteration counts, tool diversity, tests_attempted. Free, instant, fully reproducible.
2. **Structured interpretive tagging** — ~36 schema-bound signals + four categorical fields. Tagged by the `steeze-tagger` subagent reading each session against a fixed taxonomy. Produces aggregated rates that position the user against a 4,846-session corpus baseline.
3. **Open behavioral observation** — one `steeze-tagger` subagent in `open` mode reads ~20 sessions without a schema and returns rich findings: co-occurrence patterns, failure modes, session anchors.

The **structured track** gives the archetype label, fingerprint gauge, and within-cohort bars. The **open track** gives the recognition lines, sensitivity framing, and experiments. Neither alone produces a credible report — the synthesis does.

## Critical: model pinning

The corpus baselines were tagged with **Claude Opus 4.7**. For within-cohort positioning to be calibrated, the `steeze-tagger` subagent must run on the same model. Subagents inherit the parent session's model (the agent definition declares `model: inherit`), so this happens automatically when the user invoked you from Opus 4.7.

Before Step 4, **verify** which model the parent session is on. If it's not Opus 4.7:

- The archetype label is robust across models — proceed.
- Within-cohort positioning will drift ~10pp on average — flag this in the final report's "edge case" framing (see `interpret.md`).
- Mention to the user that they can re-run from an Opus 4.7 session if calibration matters to them.

Empirically: holding model constant, ~5pp drift on average. Switching models, ~11pp drift. Model-pinning matters more than delivery-pinning.

## Setup — discover paths and bootstrap deps

Before Step 1, set two environment variables. **You will reference these for every subsequent step.**

```bash
# 1. Discover the plugin root (where the read-only assets live).
#    Claude Code injects $CLAUDE_PLUGIN_ROOT for plugin skills. If unset
#    (older Claude Code), derive it from this SKILL.md's path.
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-}"
if [ -z "$PLUGIN_ROOT" ]; then
  # Fallback: this SKILL.md is at $PLUGIN_ROOT/skills/steeze/SKILL.md
  SKILL_DIR=$(cd "$(dirname "$0")" && pwd)  # only works if invoked as a script
  PLUGIN_ROOT=$(cd "$SKILL_DIR/../.." && pwd)
fi
export STEEZE_PLUGIN_ROOT="$PLUGIN_ROOT"

# 2. Create a fresh run directory.
RUN_ID=$(bash "$STEEZE_PLUGIN_ROOT/skills/steeze/scripts/run_id.sh")
export STEEZE_RUN_DIR="$HOME/.claude/steeze/$RUN_ID"
mkdir -p "$STEEZE_RUN_DIR"
```

When invoking these as shell commands from within Claude Code, set both env vars at the start of your shell session and reuse them in every subsequent `Bash` call. If your environment doesn't preserve env vars between Bash calls, substitute the absolute paths inline.

## Step 1 — orient and check prerequisites

Bootstrap the Python venv:

```bash
STEEZE_PY=$(bash "$STEEZE_PLUGIN_ROOT/skills/steeze/scripts/bootstrap.sh" \
    "$STEEZE_PLUGIN_ROOT/skills/steeze/requirements.txt")
echo "Python: $STEEZE_PY"
```

The script is idempotent — first run installs `jinja2` and `jsonschema` into `~/.claude/steeze/.venv` via `uv` (or `python3 -m venv` fallback); subsequent runs just print the interpreter path.

**If bootstrap exits non-zero**: report the error to the user and stop. Common cases:
- exit 2 — no `uv` and no `python3` on PATH. Tell the user to install one and re-run.
- exit 3 — venv creation or pip install failed. Stderr has details.

Then run the smoke test:

```bash
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/tests/smoke_test.py" \
    --plugin-root "$STEEZE_PLUGIN_ROOT/skills/steeze"
```

Free, fast, < 1 second. Confirms the renderer can produce all five artifacts from the schema + the Manager exemplar + the Generalist edge case. **If it fails, stop and surface the error to the user.**

Announce what you're about to do — show the defaults, don't ask:

- **Last 30 parent sessions** in `~/.claude/projects` for structured tagging.
- **20 sessions sampled across projects** for the open behavioral pass.
- **Sessions root**: `~/.claude/projects` (override available via `--session-root` if the user passes one).
- **Report style**: `default` (the user can re-run with `/steeze --style editorial` for the single-page variant).

If the user passed `--sessions N` or `--style editorial` as args, honor them; otherwise use these defaults and proceed.

## Step 2 — preprocess

```bash
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/preprocessing/sessions_to_csv.py" \
    --out "$STEEZE_RUN_DIR/sessions.csv" \
    --min-messages 5
```

Surface the script's summary back to the user — parent count, subagent count, date range, p95 message size. If p95 is over ~500 KB, mention that to the user (rare; usually fine).

## Step 3 — enrich (deterministic signals, no API call)

```bash
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/preprocessing/enrich.py" \
    "$STEEZE_RUN_DIR/sessions.csv" \
    --out "$STEEZE_RUN_DIR/sessions_enriched.csv"
```

Surface the per-script summary — it lists the 14 column names added.

## Step 4 — structured tagging (subagents, local, no API key)

Four sub-steps. The helpers all live in `tagging/tag_sessions.py`.

### 4a. Export the prompt + per-session transcripts

```bash
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/tagging/tag_sessions.py" \
    --export-prompt "$STEEZE_RUN_DIR/tag_prompt.md"

"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/tagging/tag_sessions.py" \
    "$STEEZE_RUN_DIR/sessions_enriched.csv" \
    --export-transcripts "$STEEZE_RUN_DIR/transcripts/" \
    --limit 30
```

The first line writes the ~20 KB system prompt (built from `taxonomy.json`) to `tag_prompt.md`. The second writes one `<session_id>.txt` per selected session into `transcripts/`, plus a `_manifest.json` the assembler reads later.

### 4b. Spawn `steeze-tagger` subagents in parallel

Create the annotations dir:

```bash
mkdir -p "$STEEZE_RUN_DIR/annotations/"
```

Then spawn **5 subagents in parallel, 6 transcripts each** for a 30-session run. Use `subagent_type: "steeze-tagger"` — the plugin ships this agent definition specifically for this step.

Issue all 5 `Task` calls in a single message so they run concurrently. Each call's prompt should be:

```
mode: tag

system prompt: <$STEEZE_RUN_DIR/tag_prompt.md absolute path>

transcripts to tag (read each in full and write JSON annotation):
  <absolute path to transcript 1>.txt
  <absolute path to transcript 2>.txt
  ... (6 per batch)

annotations dir: <$STEEZE_RUN_DIR/annotations/ absolute path>

Read the system prompt end-to-end first. For each transcript, write
<session_id>.json (where session_id is the filename without .txt) to the
annotations dir, conforming to the schema in tag_prompt.md. Return when
all 6 annotations are written.
```

Subagents work in parallel. Each session typically takes 30–90 seconds depending on transcript length.

### 4c. Assemble

```bash
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/tagging/tag_sessions.py" \
    --assemble "$STEEZE_RUN_DIR/annotations/" \
    --manifest "$STEEZE_RUN_DIR/transcripts/_manifest.json" \
    --out "$STEEZE_RUN_DIR/tagged_sessions.csv"
```

Reports `ok / missing / invalid` counts. If `missing` > 10% of the batch, re-spawn a `steeze-tagger` subagent for the missing session_ids before continuing.

### 4d. Validate

Spot-check 2–3 random files in `$STEEZE_RUN_DIR/annotations/`. Confirm:
- All required top-level fields present.
- Fired signals have `evidence` strings.
- Reality-contact signals have `trigger` + `surface_type`.

The renderer won't catch malformed annotations until much later, so a 30-second spot-check pays off.

## Step 5 — compute metrics

```bash
mkdir -p "$STEEZE_RUN_DIR/report"  # not strictly needed; output is the JSON itself
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/analysis/compute_metrics.py" \
    "$STEEZE_RUN_DIR/tagged_sessions.csv" \
    --raw "$STEEZE_RUN_DIR/sessions.csv" \
    --baselines "$STEEZE_PLUGIN_ROOT/skills/steeze/baselines/" \
    --out "$STEEZE_RUN_DIR/metrics.json"
```

Pure stdlib, fast. Output is the JSON file the renderer consumes for numbers via `*_ref` paths.

## Step 6 — open behavioral pass

Spawn **one** `steeze-tagger` subagent in `open` mode. Hand it 20 sessions sampled across projects, not just the most recent. The Task prompt:

```
mode: open

sessions to characterize (sampled across projects, ~20 total):
  <absolute path to session 1>
  <absolute path to session 2>
  ...

output path: <$STEEZE_RUN_DIR/findings.md absolute path>

Characterize what's distinctive about how this user works. Write four
sections to findings.md: Character, Three distinctive patterns,
Sensitivity, Two experiments. Paraphrase — no verbatim quotes. No
percentages. See the agent's own SKILL definition for the full section
spec.
```

The output `findings.md` carries the rich behavioral observation into Step 7. This is the part the structured tagger can't do because the schema is a ceiling on what it can find.

## Step 7 — author report_content.json

**Now read `$STEEZE_PLUGIN_ROOT/skills/steeze/analysis/interpret.md` end to end.** It has the voice rules, structural contract, Y-vocabulary table for picking the title verb, and the section on synthesizing across structured + open tracks.

Then author `$STEEZE_RUN_DIR/report_content.json`:

- **Title, fingerprint badge, traits, comparison bars, archetype label, shadow** → from `metrics.json` via `*_ref` paths.
- **Recognition lines, Section II body, Section III move bodies** → from `findings.md`. The open pass authored these specifically; lift them with light editing for voice.
- **Pullquote** → synthesis. Should land both the structured thesis (the archetype) and the open thesis (the distinctive pattern) in one breath.

The schema is at `$STEEZE_PLUGIN_ROOT/skills/steeze/analysis/report_content.schema.json`. The renderer validates and exits non-zero on failure.

**You write strings. The renderer fetches numbers via `*_ref` paths.** Don't round-trip arithmetic through your head.

Pre-flight checklist (also in interpret.md):

- Title is *"The X Who Y"* — modifier is 1–4 words, captures the user's *within-archetype* distinction.
- Subtitle has no numbers. Pure thesis.
- Three traits — typically two `kind: "high"` + one `kind: "contrast"`.
- Trait characterizations have no numbers; a friend could say each one out loud.
- Pullquote can be read aloud cold and captures the thesis in one breath.
- **Three recognition lines — drawn from `findings.md`, not invented.**
- **Two moves — drawn from `findings.md`'s "Two experiments" section, cross-referenced with the structured track's `borrow_from`.** Each attributed to a named archetype.

## Step 8 — render

Determine style: if the user passed `--style editorial` in their slash command args, use `editorial`; otherwise `default`.

```bash
STEEZE_STYLE="${STEEZE_STYLE:-default}"
"$STEEZE_PY" "$STEEZE_PLUGIN_ROOT/skills/steeze/analysis/render_report.py" \
    --content "$STEEZE_RUN_DIR/report_content.json" \
    --metrics "$STEEZE_RUN_DIR/metrics.json" \
    --schema "$STEEZE_PLUGIN_ROOT/skills/steeze/analysis/report_content.schema.json" \
    --style "$STEEZE_STYLE" \
    --out "$STEEZE_RUN_DIR"
```

Validates the content JSON, resolves all `*_ref` paths, writes:

- `$STEEZE_RUN_DIR/report.html`           — full report
- `$STEEZE_RUN_DIR/report.md`             — markdown version
- `$STEEZE_RUN_DIR/hero.md`               — chat-paste summary (the inline deliverable)
- `$STEEZE_RUN_DIR/hero_card.txt`         — CLI hero card with ANSI
- `$STEEZE_RUN_DIR/hero_card.plain.txt`   — same, no ANSI

If validation fails or a `*_ref` doesn't resolve, the renderer prints a diff and exits non-zero. Fix the content JSON and re-run.

## Step 9 — deliver

1. **Print the hero markdown inline** in the chat (so the user gets recognition without opening anything):

   ```bash
   cat "$STEEZE_RUN_DIR/hero.md"
   ```

2. **Open the HTML report** in the user's browser:

   ```bash
   bash "$STEEZE_PLUGIN_ROOT/skills/steeze/scripts/open_report.sh" \
       "$STEEZE_RUN_DIR/report.html"
   ```

3. **Tell the user where the artifacts live** for later reference:

   > Report opened in your browser. All artifacts saved to `$STEEZE_RUN_DIR/`.

Stop there. No file inventory, no `cat`'ing the ANSI hero card, no "want me to dive into…" follow-up question. The user can ask if they want more.

If the user later asks "is this private?" — yes, fully. With the subagent path, no data leaves their machine. They can verify by reading `tagging/tag_sessions.py` and `analysis/compute_metrics.py` in the plugin install.

## Failure modes to watch for

- **Bootstrap failure (exit 2 or 3).** Tell the user; suggest `rm -rf ~/.claude/steeze/.venv` and re-run if the venv looks stale.
- **No sessions found.** Wrong path, or no Claude Code history yet. Suggest `ls ~/.claude/projects`.
- **Few sessions tagged (< 10).** Either the user has small history or the filter dropped them. Tell `interpret.md` to flag this in the report (edge-case framing).
- **Subagent annotation missing or invalid.** Common in long transcripts. The assembler reports counts; re-spawn a `steeze-tagger` subagent for the gaps before computing metrics. If a session is genuinely un-tag-able, leave it out — metrics will compute on what remains.
- **Open-pass findings shallow.** If `findings.md` reads as generic, the subagent likely under-sampled or skimmed. Re-spawn with a smaller batch (~10 sessions) and emphasize "specific session anchors required."
- **Baselines missing or unreadable.** `compute_metrics.py` runs anyway; the renderer drops cohort comparison bars; `interpret.md`'s edge-case section describes how to author content JSON when comparisons are unavailable.

## What you don't need to do

- **Don't author the recognition lines from aggregates.** That was the old failure mode — `findings.md` is the source.
- **Don't invent baseline numbers.** The CSVs in `baselines/` are the source of truth.
- **Don't soften the framing voice.** Specific over general; numbers in visualizations, characterizations in prose. Don't backslide into "you're doing great" / "you should improve X."
- **Don't put numbers in prose.** They go in the bars, the gauge, and the headline-stats tiles.
- **Don't paste verbatim transcript quotes** — even from `findings.md`. Paraphrase patterns; verbatim feels like surveillance.
