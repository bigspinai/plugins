# bigspin toolkit

A toolkit of skills and a Claude Code plugin (**bigspin**) that turns your local session history into personal reports. Everything runs on your machine — no API key required, and no data ever leaves your laptop.

Three reports ship today:

| Command | What it tells you |
| --- | --- |
| **`/persona`** | Your **practice archetype** — your signature moves and how you compare against a measured baseline of 4,846 real sessions from 172 Claude Code users. |
| **`/token-roi`** | Your **token-vs-outcome ROI** — weekly trends of token usage against engineering outcomes (committed lines, PRs, agent draft lines) and your tokens-per-unit-of-work cost ratios over time. |
| **`/skill-usage`** | Your **skill usage** — which skills you read and invoke most (and least), how that trends week over week, and which skill files in your repo are candidates for a refresh or retirement. |

All three read the session transcripts Claude Code already stores in `~/.claude/projects/` and write a self-contained HTML report (plus a short inline summary) under `~/.claude/bigspin/`.

> Curious what `/persona` looks like before running the full pipeline? `/sample-persona-report` renders a demo from checked-in sample data in ~500ms — no analysis, no session access.

## Two ways to run

Both paths produce the same artifacts in the same place — pick whichever fits your setup.

### Option A — as a Claude Code plugin

Works best from the CLI; we don't recommend the Claude desktop app for this. Open Claude Code and run, one command at a time:

```
/plugin marketplace add bigspinai/toolkit
/plugin install bigspin@bigspinai
/persona
/token-roi
/skill-usage
```

Each command finishes by opening its HTML report in your browser automatically.

### Option B — from a local clone

Clone the repo and ask any agentic coding tool (Claude Code, Codex, Cursor, Copilot, …) to follow the skill you want. If you're not sure how to clone, just ask your tool to do it for you, then — from the repo root — paste one of the prompts below.

**For the practice mirror (`/persona`):**

```
I've cloned https://github.com/bigspinai/toolkit and am running from the repo root.

Please follow `skills/persona/SKILL.md` end to end. It will analyze my local
session history in ~/.claude/projects against the project's measured baseline
corpus and produce the report. SKILL.md handles opening persona-report.html in
my browser when it's ready.
```

**For the token-ROI report (`/token-roi`):**

```
I've cloned https://github.com/bigspinai/toolkit and am running from the repo root.

Please follow `skills/token-roi/SKILL.md` end to end. It will analyze my local
session history in ~/.claude/projects and produce the token-ROI report. SKILL.md
handles opening token-roi-report.html in my browser when it's ready.
```

**For the skill-usage report (`/skill-usage`):**

```
I've cloned https://github.com/bigspinai/toolkit and am running from the repo root.

Please follow `skills/skill-usage/SKILL.md` end to end. It will count which skills
I read and invoke in ~/.claude/projects, cross-reference the skills in my repo, and
produce the skill-usage report. SKILL.md handles opening skill-usage-report.html in
my browser when it's ready. Point --repo-root at the repo whose skills I care about.
```

Tip: this is a smoother experience with Auto mode on, so you don't have to approve each step — but that's your call.

## What each command produces

All artifacts land under `~/.claude/bigspin/<run-id>/` on your machine.

**`/persona`** → `~/.claude/bigspin/persona-<timestamp>/`

- `persona-report.html` — the full slide-style report (Bigspin-branded, mobile-vertical, screenshot-friendly). Opens automatically.
- `persona-report.md` — the same report as portable markdown.
- `persona-hero.md` — a tight ~10-line summary printed inline in the chat. The thing you read first.
- `persona-hero-card.txt` + `persona-hero-card.plain.txt` — CLI hero card, with and without ANSI color.

**`/token-roi`** → `~/.claude/bigspin/token-roi-<timestamp>/`

- `token-roi-report.html` — three inline-SVG charts: weekly trend (indexed, log scale), per-session outcome distributions, and tokens-per-unit-of-work cost ratios. Opens automatically.
- `roi.csv` — anonymized per-week aggregates.
- `token-roi-hero.md` — a tight inline summary.

`/token-roi` accepts two flags: `--days N` (lookback window, default 90) and `--no-git` (skip the local-git committed-line attribution).

**`/skill-usage`** → `~/.claude/bigspin/skill-usage-<timestamp>/`

- `skill-usage-report.html` — inline-SVG charts: a most-to-least ranking (reads vs invocations, with never-used skills called out), a weekly usage trend, and a reads-vs-invocations split — plus a section flagging skill files worth updating. Opens automatically.
- `usage.csv` — per-skill counts (invocations, reads, total, last-modified).
- `skill-usage-hero.md` — a tight inline summary.

`/skill-usage` accepts: `--days N` (lookback window, default: all history), `--repo-root PATH` (which repo to scan for the skill inventory, default: current directory), `--extra-root PATH` (add another inventory root, repeatable), `--stale-days N` (age past which a `SKILL.md` counts as stale for the update flags, default 60), and `--no-git` (use file mtime instead of `git log` for last-modified dates).

## Privacy

Everything runs locally on your machine. No upload, no telemetry, no third-party request.

- **Session data** is read from `~/.claude/projects/` (where Claude Code already stores it) and never copied off-disk.
- **Analysis** is a Python pipeline. `/persona` additionally uses Claude Code subagents you spawn yourself — they inherit your session, so there's no separate API key and no separate vendor. `/token-roi` and `/skill-usage` use no subagents and no model calls at all.
- **Output** is written only to `~/.claude/bigspin/<run-id>/`.

You can audit the whole thing — it's Python plus markdown skill instructions, all bundled in the plugin.

## Requirements

- **Claude Code** — or, for the clone-and-run flow, any agentic tool that can spawn subagents (needed for `/persona`'s tagging step).
- **Python 3.10+** or **`uv`** (`curl -LsSf https://astral.sh/uv/install.sh | sh`). On first run the pipeline auto-bootstraps a venv at `~/.claude/bigspin/.venv` and installs `jinja2` + `jsonschema`. All three commands share this one venv.
- **Some Claude Code history.** For `/persona`, 30+ sessions makes positioning stable; 10–30 still works with reduced confidence; under 10 produces a graceful "small history" version. `/token-roi` and `/skill-usage` work with whatever sessions fall in their lookback window.
- **Time.** `/persona` takes ~4–8 minutes for a 20-session run (mostly the parallel subagent tagging). `/token-roi` runs in seconds to a couple of minutes — the optional git pass dominates, and `--no-git` is near-instant. `/skill-usage` runs in seconds (it scans transcripts and skill files only; no model calls).

## How it works

**`/persona`** combines three layers into one report:

1. **Deterministic signals** (iteration count, tool diversity, course corrections, tests attempted, …) — computed from message structure in under a second.
2. **Structured interpretive tagging** — 4 `persona-tagger` subagents tag ~36 signals against a fixed taxonomy, producing rates positioned against the corpus baseline.
3. **Open behavioral observation** — one `persona-tagger` subagent reads ~12 exported transcripts schema-free and writes the distinctive patterns, sensitivity, and suggested experiments.

Full methodology lives in [`skills/persona/analysis/interpret.md`](skills/persona/analysis/interpret.md); the corpus baseline (measured 2026-05-01) is documented in [`skills/persona/baselines/README.md`](skills/persona/baselines/README.md).

**`/token-roi`** is purely deterministic — no subagents, no model calls. It parses each session's tokens, turns, tool calls, agent draft lines (Edit/Write/MultiEdit), files touched, and PRs opened, and — when local `git` is available — attributes committed lines, including a conservative exact-match "agent-attributed" lower bound. It rolls these into weekly buckets and draws the charts with the Python standard library (no matplotlib/pandas/numpy).

**`/skill-usage`** is also purely deterministic. It counts two signals from your transcripts: **Skill-tool invocations** (`tool_use` calls to the `Skill` tool) and **`SKILL.md` reads** (including the reads that happen inside subagents, where most occur). Plugin-namespaced names like `bigspin:persona` fold onto their base name. It then walks the skill files in your repo (`--repo-root`) to build an inventory — de-duplicated by name across git worktrees and published mirrors — so it can flag skills that exist but are never used. From there it ranks every skill, buckets usage by week, and emits deterministic "update candidate" flags (`retire-or-refresh`, `refine`, `invocation-only`, `read-only`, `never-triggered`). Charts are drawn with the standard library; usage is a lower bound, since skills the harness auto-loads without an explicit read or invocation aren't counted.

## Contributing

This repo is a synced mirror — the source of truth lives in a private monorepo. **Do not file PRs here.** Open issues for bug reports, but pull requests against `bigspinai/toolkit` will be closed in favor of upstream changes.

## License

MIT. See [LICENSE](LICENSE).
