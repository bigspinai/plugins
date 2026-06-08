# Skill: Interpret metrics & write report content

Read this before authoring `report/report_content.json`. Your job is to
synthesize **two** sources into structured content the renderer turns
into three artifacts (HTML, CLI hero card, markdown):

- `report/metrics.json` — the structured track. Aggregated rates,
  archetype scoring, within-cohort positioning, baseline comparisons.
  This is the source for **numbers** and the archetype label.
- `report/findings.md` — the open behavioral track. A subagent's
  schema-free read of ~12 of the exported transcripts: distinctive
  patterns with session anchors, sensitivity framing, two experiments.
  This is the source for **prose** that needs genuine reading to land.

The renderer enforces a JSON-Schema contract
(`analysis/report_content.schema.json`) and looks up numbers from
`metrics.json` via `*_ref` fields. **You author prose. Numbers come from
data refs — you do not round-trip them.**

## Source split — which file feeds which field

The two passes do different jobs. The structured pass measures and
positions; the open pass observes and frames. The report's prose-heavy
fields (recognition lines, sensitivity, move bodies) need genuine
reading to land — author them from `findings.md`, not from aggregates.
This is the single most important authoring discipline in this skill.

| Field | Source | Notes |
|---|---|---|
| `title.archetype_name` | `metrics.json` | `user_archetype.primary` minus "The" |
| `title.modifier` ("Who Audits") | both | Verb chosen from `metrics.json` top within-archetype signal; sharpened by the language used in `findings.md`'s distinctive patterns |
| `tagline` | `findings.md` | Lift the "Character" line, edit for length |
| `fingerprint_badge`, `shadow.*` | `metrics.json` | Refs only |
| `traits[].name_em` | `metrics.json` | Anchor to a structured signal so the bar pair has data |
| `traits[].characterization` | both | Name from archetype convention; sensory-anchored prose paraphrasable from `findings.md` patterns |
| `pullquote.text` | **synthesis** | One sentence that lands archetype + distinctive pattern in one breath |
| `recognition_lines` | **`findings.md` only** | Concrete behavioral lines from the distinctive patterns. **Do not author from aggregates.** |
| `section_outcome.headline` | both | Names the tension between what the user does and what their data shows |
| `section_outcome.body` | both | `metrics.json` for cohort positioning context; `findings.md` for the *mechanism* of the sensitivity |
| `section_outcome.primary_value` / `comparison_values` | `metrics.json` | Refs + `value_static` for cross-archetype facts |
| `section_moves[].from_archetype`, `verb_phrase` | `metrics.json` | Use `user_archetype.baseline.borrow_from` for attribution |
| `section_moves[].body_lead`, `body_action` | **`findings.md`** | Lift from "Two experiments" — those are already specific to the user. The structured `borrow_from.rationale` is a generic fallback only |
| `compass`, `reflection_prompts_ref`, `colophon` | `metrics.json` | Refs only |

**Rule of thumb:** if a field describes *what's happening for this user
specifically*, draw from `findings.md`. If a field positions the user
against a corpus, draw from `metrics.json`. The pullquote, the
section-outcome headline, and the trait characterizations are the three
synthesis points — every other field draws cleanly from one source.

## When `findings.md` is missing or thin

If the open pass was skipped or produced ≤2 distinctive patterns:

- Note this in `comment_for_reviewers` so reviewers know.
- Author recognition lines and move bodies from the structured signals
  with the highest within-archetype positioning. The result will be
  thinner — claim-shaped rather than observation-shaped — but valid.
- The renderer doesn't know or care; the schema is the same.

This is the failure mode the architecture is designed to avoid.
Recognition lines authored from aggregates feel like horoscopes; lines
authored from observed sessions feel like recognition. Always prefer
`findings.md`.

## What this report is for

The user opened a Claude Code session and asked to see what their
practice looks like. They want to **recognize themselves**, learn
something non-obvious, and walk away with one or two concrete
experiments to try. They do not want a research-paper voice or a
string of percentages.

The artifact has *editorial* register, not dashboard register —
Kinfolk-magazine warmth, second-person, sensory anchors. Identity, not
data. The user reaction we want is *"yes, that's literally what I do —
and I didn't have language for it."*

## The contract

1. Read `report/metrics.json` end to end. Skim the `user_archetype`
   block and the `within_archetype_positioning` substructure carefully —
   those carry the signal that makes the report specific.
2. Read `analysis/report_content.schema.json`. The schema is the
   contract; the renderer rejects anything that doesn't validate.
3. Author `report/report_content.json` matching the schema. **The
   existing file at that path is the Showrunner exemplar — read it as the
   reference shape.**
4. Run `python analysis/render_report.py`. It validates the schema, pulls
   numbers from `metrics.json` via your refs, and emits three artifacts.
   Schema-failure or unresolved-ref → non-zero exit with a diff. Fix
   and re-run.

**You write strings. The renderer fetches numbers.** The only numeric
content in the JSON is `value_static` for cross-archetype facts (e.g.,
"Runtime Mechanic ships at 22.7%") — those are stable corpus statistics
from `archetype_baselines.csv`, not user data.

## Voice rules

These are not stylistic preferences. They are the difference between a
report that lands as recognition and one that reads as a performance
review.

1. **Lead with a thesis, not a description.** The hook is a *claim* —
   *"You're a Showrunner — but not a typical one"*. Not *"Your sessions show
   patterns of decomposition."* The first 80 words decide whether they
   keep reading.

2. **Strip numbers from prose. Numbers go in visualizations.** The
   prose says *"Decomposes before moving."* The renderer draws the bars
   that show 67% vs 40%. If a sentence has a percentage in it, the
   percentage almost certainly belongs in a bar.

3. **Vocative, not analytical.** Translate the dialect: not
   *"above_p75 within the Showrunner cohort"* but *"more than three-quarters
   of Showrunners fall below where you are on this."* Avoid `rate`,
   `delta`, `percentile`, `signal_name`, `_snake_case_` in user-facing
   strings. Sensory anchors where possible.

4. **Confident, not hedged.** *"You're X"* beats *"you may tend toward X."*
   The renderer-fetched numbers are the safety net. No "interesting,"
   no "worth noting," no "may suggest."

5. **Tension, not flattery.** Every archetype has a cost. Name the
   strength alongside the trade-off — that's what makes it feel real
   rather than horoscope-cheap.

6. **Identity-defining traits are positives, never deficits.** The
   "contrast" trait — the move you *don't* do that defines you against
   your archetype's default — is framed as character, not gap.
   *"The Showrunner move you've replaced — not a gap in your practice."*
   The deficit conversation belongs in Section III (Moves), not in
   Section I (Identity).

7. **Shadow is a door, not a verdict.** Frame as *a mode you don't
   naturally inhabit*, with a name. Curiosity, not prescription.

8. **Borrowing is the prescription, in vocabulary.** Each move is a
   *named move from another archetype*, with the from-archetype credit
   making the experiment portable. The user should be saying
   *"I tried a Runtime Mechanic move"* in their head next week.

9. **Specificity over balance.** A sharp report has *one thesis* and cuts
   what doesn't sharpen it. Don't try to surface every finding the data
   supports.

## The structural spine

The schema enforces three sections. Each does one job. The renderer
flow places the **pullquote** and the **"you'll know this is you if…"
recognition lines** *between the hero and Section I* — the recognition
lines act as concrete color on the pullquote's thesis, and Section I
then formalizes the recognition with the three named traits + bars.

| Block | Role | Contents |
|---|---|---|
| **Hero** | Recognition moment. | Title, tagline, shadow archetype, compass map |
| **Pullquote** | Thesis in one breath. | One sentence with weight |
| **"You'll know this is you if…"** | Concrete color on the pullquote. | Three behavioral recognition lines |
| **— I — Identity** | The fingerprint. | Three traits (2 high + 1 contrast), each with a bar pair |
| **— II — Outcome** | Where the data shows tension. Strengths alongside trade-offs. | Headline + body prose; primary value (your PR-rate) + cohort comparison gauge |
| **— III — Moves** | Two named borrows. | Two moves only, each from another archetype |

Reflection prompts and the colophon close the report.

## How to author each piece

### Title — *"The X Who Y"*

`title.archetype_name` is the bare archetype noun (`"Showrunner"`, not
`"The Showrunner"` — the renderer prepends "The"). It comes from
`metrics.user_archetype.primary` minus the leading "The".

`title.modifier` is what makes *this* user specific within their
archetype. Two to four words; sentence-case. Build it from the user's
most-elevated *within-archetype* signal — the renderer's data pipeline
already exposes the candidates at
`metrics.user_archetype.within_archetype_positioning.signals[0]`. Map
the signal to a verb the user could say aloud:

| Signal (top within-cohort positioning) | Verb candidates |
|---|---|
| `pointed_to_specific_issue`, `change_request_specific`, `in_review_edge_case_surface` | "Audits", "Catches" |
| `intent_reframe`, `meta_behavioral_steering`, `constraint_added_later` | "Reframes", "Steers" |
| `decomposition`, `context_loading_directive`, `safety_constraint_set` | "Plans", "Maps", "Scopes" |
| `subagent_explicitly_delegated`, `workflow_step_delegation` | "Orchestrates", "Delegates" |
| `ran_and_reported`, high `tests_attempted` | "Tests", "Runs" |
| `cited_team_convention` | "Cites" |

Pick the verb that produces the most *narrative tension* with the
archetype default. The Showrunner default is "delegates and accepts."
*"The Showrunner Who Audits"* lands because it's the move a typical Showrunner
*doesn't* make.

### Tagline

One short sentence under the title — five to twelve words. A vivid
*character* line, not a thesis statement. Reserve the longer thesis
for the pullquote in Section I.

> *"Reads the diff. Won't run it."*
> *"Writes the brief, then accepts."*
> *"Catches the edge case the spec missed."*

Each one captures the archetype-with-modifier in a single breath.

### Fingerprint badge

`fingerprint_badge.ref` points at `user_archetype.fingerprint_sharpness`.
`label_map` provides the display string for each value (sharp / typical
/ borderline). The renderer composes: *"75th percentile manager · score
4.0."* This is the only stat in the hero — keep the rest for the
sections below.

### Traits (exactly 3)

The trait section is the recognition spine. Each trait has:

- `kind`: `"high"` or `"contrast"`. Typical pattern: 2 high + 1 contrast.
  All-three-high is allowed when the user is genuinely a textbook
  archetype member.
- `name_em`: 1-3 words, sentence-case. Becomes italic in the title slot
  ("*Decomposition*", "*Context loading*", "*Delegative style*").
- `characterization`: one to two short sentences. **No numbers.**
  Describe what the move *looks like* — the kind of thing a friend would
  say about you. Use sensory anchors when natural.
- `data`: tells the renderer where to read user/cohort numbers. Either
  `{kind: "signal", signal: "..."}` or
  `{kind: "categorical", field: "...", bucket: "..."}`.

**The contrast trait must be framed as identity, not deficit.** Bad:
*"You don't delegate — only 20% of your sessions, vs. 57% in the cohort."*
Good: *"\"Hand off and trust the brief.\" The classic Showrunner move
you've replaced — not a gap in your practice."*

### Pullquote

The screenshot moment. One sentence — or two short ones — with weight.
Renderer treats this as oversized serif italic with rules above and
below. Earn the visual real estate.

> *"Set up like a Showrunner. Audit like a Runtime Mechanic. The combination
> is unusual."*

Two markers of a good pullquote: it can be read aloud cold, and it
captures the *thesis* of the report in one breath. If the rest of the
report could be summarized by something else, the pullquote isn't right
yet.

### Recognition lines (exactly 3)

The "you'll know this is you if…" lines. Second-person, behavior-
specific, written for *uncanny recognition*. The user should think:
*"how did they know?"*

- Concrete behaviors, not feelings. *"You've typed 'before you do
  anything' more than once this week"* > *"You're a careful planner."*
- Anchored to specific moments. The line should evoke a session the
  user has actually had.
- Use the user's own observable behaviors when you can — patterns from
  `signature_sessions[].evidence` in `metrics.json` are good source
  material, but **paraphrase**. Never paste a transcript verbatim
  (feels like surveillance).

When `archetype_profiles.json` ships `recognition_lines` per archetype
(research deliverable, in flight), the LLM can reference those instead
of authoring inline. Until then, author them.

### Shadow

`shadow.name_ref` and `shadow.axis_ref` come from `metrics.json` directly.
`shadow.tagline` is LLM-authored — describe the shadow as a *character*
in one short sentence. The shadow archetype's profile in
`baselines/archetype_profiles.json` has its own `tagline` field; you can
lift it.

### Section II — Outcome

Where the data shows tension. The user *ships* — and the data shows
where the asset is sensitive.

- `headline`: one sentence. Names the tension.
- `body`: one paragraph. Anchored to specific signals (no numbers in
  the prose; numbers go in the gauge).
- `primary_value`: the user's PR-rate (`structural.pr_rate_pct`).
  `kind: "primary"`.
- `comparison_values`: two cohort rows for context. For cross-archetype
  facts (Runtime Mechanic ships at X%, Typical Showrunner at Y%), use
  `value_static` with the number from `archetype_baselines.csv`.
  `kind: "cohort"`.

### Section III — Moves

Exactly two moves. Each is a *borrow from another archetype* — framed
as a small, named **experiment to try this week**, not a correction.
The attribution matters: *"From the Runtime Mechanic — …"* gives the
experiment a name and a community.

**The verb_phrase has to be specific.** This is the line the user
remembers. *"Run it yourself."* is too generic — it reads like a
directive ("you should be doing this"), which slips into judgment.
A specific verb_phrase names a tool, a moment, or a count, and reads
like an experiment ("here's one concrete thing to try"):

| Generic, judgment-shaped (avoid) | Specific, experiment-shaped (do this) |
|---|---|
| *"Run it yourself."* | *"Open `git diff` before you accept."* |
| *"Run it yourself."* | *"Run the test suite once before you commit."* |
| *"Reframe the goal mid-flight."* | *"Restate the goal when work surfaces a surprise."* |
| *"Cite the convention."* | *"Name the doc or RFC in your opening prompt."* |
| *"Decompose before delegating."* | *"Number the first three steps in your next brief."* |

The good versions name the **tool** (`git diff`, the test suite, the
RFC), the **moment** (before you accept, when the brief drifts), or
the **count** (once a week, three of next week's sessions). They feel
like a thing the user can put on their calendar.

Field-by-field:

- `from_archetype`: full archetype name with "The" prefix.
- `verb_phrase`: specific imperative with terminal punctuation. Names
  a tool, moment, or count (see table above). 4–60 chars.
- `body_lead`: one sentence. *Why this move, given the user's data.*
  Reference what they already do well — the move builds on a
  strength, doesn't paper over a deficit.
- `body_action`: one to two sentences. What to actually do this week.
  Concrete, behavioral, time-bounded. Inline backticks for tool/file
  names are fine (`` `git diff` ``).
- `effect_size_signal` and `effect_size_note` are optional but
  encouraged. The `signal_effect_sizes` table in `metrics.json` ranks
  moves by population-level PR-yield correlate; cite the strongest
  correlate that fits the move (e.g. *"verification arc correlates
  with +13–17pp on shipping"*). The number is a credibility anchor —
  it's why this move and not another.

The two moves should serve **two different gaps**. Don't pick two
moves that close the same gap from different angles.

Source the candidates from the user's primary-archetype `borrow_from`
field in `archetype_profiles.json` (mirrored at
`metrics.user_archetype.baseline.borrow_from`). The canned rationale
is a starting point — sharpen the verb_phrase against this user's
*actual* data before you ship. A Showrunner who already audits doesn't
need *"Run it yourself"*; they need a more specific borrow that meets
them where they are.

### Compass

Spatial map placing the user among all 7 archetypes. Renderer computes
positions from `all_scores` + `fingerprint_sharpness` — content JSON
only specifies `include: true` and the refs.

Set `include: false` only when:
- `n_sessions_tagged < 5` (positioning would be noisy)
- `user_archetype.primary == "The Multi-Mode Journeyman"` (no fingerprint, dot at
  center; renderer handles this)

### Reflection prompts

`reflection_prompts_ref` points at
`user_archetype.baseline.reflection_prompts` (from research-authored
`archetype_profiles.json`). Three prompts, second-person, anchored to
the archetype's blind spot. The renderer reads them directly for the
Markdown export.

### Colophon

`n_sessions_ref`, `n_sessions_seen_ref`, `date_earliest_ref`,
`date_latest_ref` are straightforward refs into `metrics.json`. The
footnote is the only authored copy.

`n_sessions_ref` is the sample we read in depth (the most-recent ~20 —
our cap), while `n_sessions_seen_ref` (`n_sessions_seen`) is how many
sessions are actually in the user's local history. The report leads with
the *seen* count and discloses the sample as a deliberate choice, so the
build-up slide never presents our 20-session cap as if it were an
observation. Keep `n_sessions_seen_ref` pointed at `n_sessions_seen`; if
omitted, the renderer falls back to the analyzed count and shows no
sampling framing.

## Italic emphasis in headings

Section headings use a `*word*` convention to mark which word the
renderer should italicize. Pick the word that carries the noun-tension
of the section (the word the reader stops on).

- `"Your *fingerprint*, in three dimensions"`
- `"The *outcome* signal"`
- `"Two *moves* to try this month"`

The renderer converts `*word*` to `<em>word</em>` in HTML and to
`*word*` in markdown. Don't use asterisks anywhere else (they're
parsed and replaced).

## Reference syntax

`*_ref` fields are simple paths into `metrics.json`. Two forms:

- **Dot path:** `structural.pr_rate_pct` → `metrics["structural"]["pr_rate_pct"]`.
- **Array filter:** `user_archetype.within_archetype_positioning.categoricals.interaction_style.buckets[bucket=delegative].cohort_pct` → search the `buckets` list for the entry where `bucket == "delegative"`, return its `cohort_pct`.

The renderer parser handles both. If a ref doesn't resolve, the renderer
exits with the path and the parent keys it found, so you can fix.

## Edge cases

| Case | What to do |
|---|---|
| **Multi-Mode Journeyman primary** (no shadow, no fingerprint) | Set `compass.include: true` (renderer will dot-at-center). Tagline reframes positively: *"Your practice flexes."* Author the `shadow` block with refs into `user_archetype.shadow.{name,axis}` (those resolve to empty strings for Multi-Mode Journeyman) and a real prose tagline (≥ 20 chars; the schema requires it even though the renderer suppresses the block when the resolved name is empty). See `tests/fixtures/sample_content.generalist.json` for a worked example. |
| **Borderline / low confidence** (`user_archetype.confidence == "low"`) | Set the fingerprint badge label to "borderline" via `label_map`. Don't soften the rest of the prose — one visible hedge is enough. |
| **Comparison baselines unavailable** (`comparison_to_baseline.available == false`) | Pick traits whose `data.kind == "signal"` so the renderer falls back to within-archetype positioning. |
| **Thin data** (`n_sessions_tagged < 10`) | Add a one-line hedge to the tagline ("a tentative read"). Reduce trait count to 2 if data is genuinely sparse — set the schema's `traits` to exactly 3 still; pick the two strongest plus one weaker but anchorable. |

## Worked examples

The current `report/report_content.json` is the Showrunner exemplar.
Read it side-by-side with this spec while authoring a new one.

A second exemplar — Multi-Mode Journeyman — lives at
`tests/fixtures/sample_content.generalist.json`. It shows the
shadow-stub pattern, the all-three-high trait pattern, and how to
author moves when within-archetype positioning is unavailable.

## Pre-flight checklist

Before running the renderer, check:

- [ ] **Title is "The X Who Y"** — modifier is 1-4 words, sentence-case, captures the user's *within-archetype* distinction.
- [ ] **Tagline is one short sentence** (5–12 words). Vivid character-line, not a thesis.
- [ ] **Three traits.** Two high + one contrast (typical) — contrast trait framed positively.
- [ ] **Trait characterizations have no numbers.** A friend could say each one out loud.
- [ ] **Pullquote can be read aloud cold** and captures the thesis in one breath.
- [ ] **Three recognition lines** — concrete, behavioral, second-person.
- [ ] **Shadow tagline** describes the shadow as a character.
- [ ] **Two moves, two different gaps.** Each is a named borrow with attribution.
- [ ] **All `*_ref` paths exist in `metrics.json`.** Cross-check; the renderer will catch you, but the cycle is faster if you check first.
- [ ] **No verbatim transcript quotes.** Paraphrase patterns, never paste.
- [ ] **Schema validates.** Run `python -c "import json, jsonschema; jsonschema.validate(json.load(open('report/report_content.json')), json.load(open('analysis/report_content.schema.json')))"`.

## What's no longer in this spec

The previous version of this skill specified writing `report/report.md`
directly with embedded matplotlib PNGs. That contract is retired. The
markdown report still exists — but the renderer produces it from the
content JSON, alongside the HTML and CLI artifacts. You author one
file (`report_content.json`); the renderer makes three.

If you find yourself writing prose that includes a percentage, stop —
that number belongs in a bar.
