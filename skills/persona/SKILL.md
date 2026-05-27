# Skill: Run the Claude Code Practice Mirror

You're the orchestrator. The user has cloned this repo, opened Claude Code
in it, and asked you to run the analysis. Walk through the steps below,
keeping the user in the loop at the decision points. Everything stays
local — no API key required, no data leaves this directory.

The full voice + content rules live in
`analysis/interpret.md`. Read that **after** you have `metrics.json` +
`findings.md` and before you write `report/report_content.json`. Don't
skip it.

## At a glance — dual-track architecture

```
preprocessing/sessions_to_csv.py   → sessions.csv             (1 row/session)
preprocessing/enrich.py            → sessions_enriched.csv    (+ deterministic signal cols)

[STRUCTURED TRACK — for positioned numbers]
tagging/tag_sessions.py            → tag_prompt.md, transcripts/, _manifest.json
[you orchestrate subagents]        → tagging/annotations/<session_id>.json
tagging/tag_sessions.py --assemble → tagged_sessions.csv
analysis/compute_metrics.py        → report/metrics.json

[OPEN TRACK — for rich behavioral observation]
[you orchestrate one open subagent] → report/findings.md

[AUTHORING — synthesis between both tracks]
[you author]                       → report/report_content.json
analysis/render_report.py          → report/report.html, report.md, hero.md, hero_card.txt
```

The output is **three artifacts sharing copy verbatim**: an HTML report, a
CLI hero card, and a markdown report. All three render from a single
`report_content.json`.

The pipeline has **three** layers now, not two:

1. **Deterministic signals** (`enrich.py`) — ~14 structural columns from
   message structure. Iteration counts, tool diversity, tests_attempted.
   Free, instant, fully reproducible.
2. **Structured interpretive tagging** — ~36 schema-bound signals plus
   four categorical fields. Tagged by Claude Code subagents reading each
   session against a fixed taxonomy. Produces aggregated rates that
   position the user against a 4,846-session corpus baseline.
3. **Open behavioral observation** — one subagent reads ~20 sessions
   without a schema and returns rich findings: co-occurrence patterns,
   failure modes, session anchors. The schema can't represent these
   because it didn't think to ask.

The **structured track** gives the report its archetype label, fingerprint
gauge, and within-cohort bars. The **open track** gives the report its
recognition lines, sensitivity framing, and experiments. Neither alone
produces a credible report — the synthesis does.

You are running this **for the user**, on their data, on their machine. No
data leaves this directory. (An older API-tagging path exists as a
fallback — see "Legacy: API tagging" at the bottom — but the default flow
is fully local.)

## Critical: model pinning

**The corpus baselines were tagged with Claude Opus 4.7.** For
within-cohort positioning to be calibrated, your subagents must run on
the same model. Claude Code subagents inherit the parent session's
model — so this happens automatically when the user invoked you from an
Opus 4.7 session.

Before starting Step 4, **verify** which model you're on. If the user is
on a different model (e.g., Sonnet 4.6, Haiku 4.5):

- The archetype label is robust across models — proceed.
- Within-cohort positioning will drift ~10pp on average — flag this in
  `interpret.md`'s edge-case section so the report can hedge precision
  claims.
- Suggest the user re-run from an Opus 4.7 session if calibration
  matters to them.

The model dependency exists because Sonnet and Opus read the taxonomy's
categorical rubrics differently (especially `interaction_style` —
Sonnet defaults to `mixed`, Opus to `augmentative`). Empirically:
holding model constant, API and subagent paths produce ~5pp drift on
average. Switching models produces ~11pp drift. Model-pinning matters
more than delivery-pinning.

## Step 1 — orient and check prerequisites

Run `python --version` to confirm Python ≥ 3.10. Check that `jinja2` and
`jsonschema` are installed; if not, tell the user to run
`pip install -r requirements.txt`. (Preprocessing has zero deps; metrics
is stdlib; the renderer needs jinja2 and jsonschema. The legacy API path
also needs `anthropic`.)

Then run the renderer smoke test:

```bash
python tests/smoke_test.py
```

Free, fast, < 1 second. Confirms the renderer can produce all four
artifacts from the schema + the Showrunner exemplar + the Multi-Mode Journeyman edge
case. **If it fails, stop and surface the error to the user.**

Confirm the user wants to proceed. Show them what you're about to do and
ask if they want to override defaults:

- **Default sample**: their **last 30 parent sessions** in `~/.claude/projects`
  for the structured track. (Down from 50 in the old API path —
  subagents read sequentially within a batch, so smaller batches keep
  wall-clock time reasonable.)
- **Default open-pass sample**: 20 sessions, sampled across projects
  (not just the most recent — the whole point of the open pass is
  catching cross-context patterns).
- **Default sessions root**: `~/.claude/projects` (override with
  `CLAUDE_SESSIONS_ROOT` or a path arg).

## Step 2 — preprocess

```bash
python preprocessing/sessions_to_csv.py --out sessions.csv --min-messages 5
```

Or with a custom path:

```bash
python preprocessing/sessions_to_csv.py /path/to/sessions \
    --out sessions.csv --min-messages 5
```

Surface the script's summary back to the user — parent count, subagent
count, date range, p95 message size. If p95 is over ~500 KB, mention that
and ask if they want to filter (rare, usually fine).

## Step 3 — enrich (deterministic signals, no API call)

```bash
python preprocessing/enrich.py sessions.csv --out sessions_enriched.csv
```

Surface the per-script summary — it lists the 14 column names added.

## Step 4 — structured tagging (subagents, local, no API key)

This step replaces the old API call with Claude Code subagents. It has
four sub-steps; the helpers all live in `tagging/tag_sessions.py`.

### 4a. Export the prompt + per-session transcripts

```bash
python tagging/tag_sessions.py --export-prompt tagging/tag_prompt.md
python tagging/tag_sessions.py sessions_enriched.csv \
    --export-transcripts tagging/transcripts/ \
    --limit 30
```

The first line writes the ~20 KB system prompt (built from
`tagging/taxonomy.json`) to `tag_prompt.md`. The second writes one
`<session_id>.txt` per selected session into `tagging/transcripts/`,
plus a `_manifest.json` that the assembler reads later.

### 4b. Spawn subagents to tag

Spawn N subagents in parallel — typically **5 subagents × 6 transcripts
each** for a 30-session run. Use the `Explore` subagent type or
`general-purpose`; they need read access to `tagging/transcripts/` and
write access to `tagging/annotations/`.

Each subagent's prompt:

```
Your job is to tag Claude Code session transcripts against a fixed
taxonomy. Read the system prompt at `tagging/tag_prompt.md` end to end —
it defines the output schema, the categorical fields, and per-signal
rubrics with strength anchors.

For each transcript file in this batch:
  <list of paths to tagging/transcripts/<session_id>.txt>

Read the FULL transcript, then write the JSON annotation to
`tagging/annotations/<session_id>.json`. The JSON must conform to the
schema described in tag_prompt.md (the OUTPUT FORMAT section). No
markdown fencing, no commentary — just the JSON object.

Precision over recall: tag a signal only if you see clear evidence.
Every fired signal must include `evidence`. Anti-pattern signals are
presence-only. Reality-contact signals require `trigger` and
`surface_type`.

Return when all transcripts in the batch have annotation files written.
```

The subagents work in parallel. Each session takes ~30–90 seconds of
subagent time depending on transcript length.

### 4c. Assemble

```bash
python tagging/tag_sessions.py \
    --assemble tagging/annotations/ \
    --manifest tagging/transcripts/_manifest.json \
    --out tagged_sessions.csv
```

This emits the canonical `tagged_sessions.csv` — same shape
`compute_metrics.py` already consumes. Reports `ok / missing / invalid`
counts. If the missing count is > 10% of the batch, re-spawn a subagent
to fill the gaps before continuing.

### 4d. Validate

A spot-check is worth doing: read 2–3 random files from
`tagging/annotations/` and confirm they conform to the schema (all
required top-level fields present, signals have `evidence` strings,
reality-contact signals have `trigger` + `surface_type`). The renderer
won't catch malformed annotations until much later.

## Step 5 — compute metrics

```bash
python analysis/compute_metrics.py tagged_sessions.csv \
    --raw sessions.csv \
    --out report/metrics.json
```

Pure stdlib, fast. Same as before. Output is the JSON file the renderer
consumes for numbers via `*_ref` paths.

## Step 6 — open behavioral pass

This is the **new** track. Spawn **one** general-purpose subagent. Hand
it 20 sessions sampled across projects, not just the most recent. The
subagent's prompt:

```
You're characterizing a user's Claude Code practice — what's distinctive
about how they work, not what's generic-software-engineer-y. You have
~20 session transcripts to read, listed below. Read enough of each to
form a behavioral picture; you don't need to read every line of every
session.

Sessions:
  <list of paths into ~/.claude/projects>
  (sampled across projects, not just the most recent)

Return findings as markdown to `report/findings.md` with these sections:

  ## Character
  One sentence describing how this user collaborates with Claude. Vivid,
  specific, second-person.

  ## Three distinctive patterns
  Three behavioral patterns that are this user's, not generic. Each:
    - One-line claim
    - 2–3 specific session anchors with session_id and brief paraphrase
      (NOT verbatim quotes — paraphrase patterns)
    - Why it's distinctive (vs. what most users do)

  ## Sensitivity
  Where this user's practice is fragile — what they'd lose if a session
  went sideways. Identify the *mechanism*, not just the missing tool-use.

  ## Two experiments
  Two concrete moves to try. Each:
    - One-sentence move name (imperative)
    - Why this user specifically — anchored to a pattern from above
    - Specific action in their next session

Be sharp, not flattering. The user wants recognition, not reassurance.
No verbatim transcript quotes — paraphrase. No percentages — those
belong on the structured side.
```

The output `report/findings.md` is what carries the rich behavioral
observation into Step 7. This is the part the structured tagger can't do
because the schema is a ceiling on what it can find.

## Step 7 — author report_content.json

**Now read `analysis/interpret.md` end to end.** It has the voice rules,
structural contract, Y-vocabulary table for picking the title verb, and
the new section on synthesizing across structured + open tracks.

Then author `report/report_content.json`:

- **Title, fingerprint badge, traits, comparison bars, archetype label,
  shadow** → from `metrics.json` via `*_ref` paths. (Same as before.)
- **Recognition lines, Section II body, Section III move bodies** →
  from `findings.md`. The open pass authored these specifically; lift
  them with light editing for voice.
- **Pullquote** → synthesis. Should land both the structured thesis (the
  archetype) and the open thesis (the distinctive pattern) in one
  breath.

The schema is at `analysis/report_content.schema.json`. The renderer
validates and exits non-zero on failure.

**You write strings. The renderer fetches numbers via `*_ref` paths.**
Don't round-trip arithmetic through your head.

Pre-flight checklist (also in interpret.md):

- Title is *"The X Who Y"* — modifier is 1–4 words, captures the user's
  *within-archetype* distinction.
- Subtitle has no numbers. Pure thesis.
- Three traits — typically two `kind: "high"` + one `kind: "contrast"`.
- Trait characterizations have no numbers; a friend could say each one
  out loud.
- Pullquote can be read aloud cold and captures the thesis in one
  breath.
- **Three recognition lines — drawn from `findings.md`, not invented.**
- **Two moves — drawn from `findings.md`'s "Two experiments" section,
  cross-referenced with the structured track's `borrow_from`.** Each
  attributed to a named archetype.

## Step 8 — render

```bash
python analysis/render_report.py
```

Validates the content JSON, resolves all `*_ref` paths, writes:

- `report/report.html`           — full report
- `report/report.md`             — markdown version
- `report/hero.md`               — chat-paste summary (the inline deliverable)
- `report/hero_card.txt`         — CLI hero card with ANSI
- `report/hero_card.plain.txt`   — same, no ANSI

If validation fails or a `*_ref` doesn't resolve, the renderer prints a
diff and exits non-zero. Fix the content JSON and re-run.

## Step 9 — close

Paste the contents of `report/hero.md` inline in the chat. That is the
deliverable — a tight markdown summary the user can read in place
without opening anything. Then a single CTA:

> Open `report/report.html` for the full read.

Stop there. No file inventory, no `cat`'ing the ANSI hero card, no
"want me to dive into…" follow-up question. The user can ask if they
want more.

If the user later asks "is this private?" — yes, fully. With the
subagent path, no data leaves this directory. They can verify by
reading `tagging/tag_sessions.py` and `analysis/compute_metrics.py`.

## Failure modes to watch for

- **No sessions found.** Wrong path, or no Claude Code history yet.
  Suggest `ls ~/.claude/projects`.
- **Few sessions tagged (< 10).** Either the user has small history or
  the filter dropped them. Tell `interpret.md` to flag this in the
  report.
- **Subagent annotation missing or invalid.** Common in long
  transcripts. The assembler reports counts; re-spawn a subagent for
  the gaps before computing metrics. If a session is genuinely
  un-tag-able (e.g., transcript truncation), leave it out — the
  metrics will compute on what remains.
- **Open-pass findings shallow.** If `findings.md` reads as generic, the
  subagent likely under-sampled or skimmed. Re-spawn with a smaller
  batch (~10 sessions) and emphasize "specific session anchors required."
- **Baselines missing.** `compute_metrics.py` runs anyway; the renderer
  drops cohort comparison bars; `interpret.md`'s edge-case section
  describes how to author content JSON when comparisons are
  unavailable.

## Legacy: API tagging (fallback)

The API-based tagger still works:

```bash
python tagging/tag_sessions.py sessions_enriched.csv \
    --out tagged_sessions.csv \
    --limit 50 \
    --model claude-opus-4-7 \
    --dry-run    # cost estimate first
python tagging/tag_sessions.py sessions_enriched.csv \
    --out tagged_sessions.csv \
    --limit 50 \
    --model claude-opus-4-7 \
    --yes
```

**Pin the model to match the corpus baseline** (Opus 4.7). The default
in `tag_sessions.py` is Sonnet 4.6 for historical reasons — explicitly
pass `--model claude-opus-4-7` for calibrated output.

Cost: ~$25–30 for 50 sessions on Opus 4.7 (~5× Sonnet). Requires
`ANTHROPIC_API_KEY`. Slightly faster than the subagent path (1–3 min)
and has a small failure mode of its own — Opus occasionally responds in
text instead of via the enforced tool call (~1 in 50 sessions in our
testing). The subagent path is the default because it's free, local,
runs without a key, and avoids the tool-enforcement edge case.

## What you don't need to do

- **Don't author the recognition lines from aggregates.** That was the
  old failure mode — `findings.md` is the source.
- **Don't invent baseline numbers.** The CSVs in `baselines/` are the
  source of truth.
- **Don't soften the framing voice.** Specific over general; numbers in
  visualizations, characterizations in prose. Don't backslide into
  "you're doing great" / "you should improve X."
- **Don't put numbers in prose.** They go in the bars, the gauge, and
  the headline-stats tiles.
- **Don't paste verbatim transcript quotes** — even from `findings.md`.
  Paraphrase patterns; verbatim feels like surveillance.
