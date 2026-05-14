# steeze

A Claude Code plugin that analyzes your local session history and renders a personal "practice mirror" — your archetype, your signature moves, and how you compare against a measured baseline of 4,846 real sessions from 172 Claude Code users. This runs entirely on your machine, with no API key required. No data ever leaves your laptop.

## How to set this up
Open Claude Code and start a new session.
Paste in each slash command, one at a time.
Once it finishes running, you will receive an html report that will open automatically.

```
/plugin marketplace add bigspinai/steeze
/plugin install steeze@bigspinai
/steeze
```

## What you get

`/steeze` reads `~/.claude/projects/` (the directory Claude Code already keeps your session history in), runs a nine-step analysis pipeline, and writes a fresh batch of artifacts to `~/.claude/steeze/<timestamp>/`:

- **`report.html`** — the full editorial report. Opens automatically in your default browser. Mobile-vertical, screenshot-friendly.
- **`report.md`** — the same report as markdown. Portable, no images, can be pasted back into Claude Code.
- **`hero.md`** — a tight summary (~10 lines) printed inline in the chat right after the run finishes. The thing you actually read first.
- **`hero_card.txt`** + **`hero_card.plain.txt`** — CLI hero card with and without ANSI color.

## Report styles

Two HTML styles ship with the plugin. Same content, same `report_content.json` — only the template differs.

| Style | When | Vibe |
|---|---|---|
| **`editorial`** (default) | `/steeze` | Kinfolk-magazine, single-page, editorial-serif voice with rust accents. Best for reading end-to-end, screenshotting a section, or sharing as a PDF. |
| **`wrapped`** | `/steeze --style wrapped` | Spotify-Wrapped-inspired slide reveal, build-up cards, more visual punch. Best for sharing on social or one-screen-at-a-time discovery. |

## Privacy

Everything runs locally on your machine. No upload, no telemetry, no third-party request.

- Session data: read from `~/.claude/projects/` (where Claude Code already stores it). Never copied off-disk.
- Analysis: a Python pipeline + Claude Code subagents you spawn yourself. Subagents inherit your Claude Code session — no separate API key, no separate vendor.
- Output: written to `~/.claude/steeze/<timestamp>/` on your machine.

You can audit the whole pipeline — it's ~4 K lines of Python plus markdown skill instructions, all bundled in the plugin.

## Requirements

- **Claude Code** (any current version). The plugin needs subagent spawning to do the structured tagging step.
- **Python 3.10+** OR **`uv`** (`curl -LsSf https://astral.sh/uv/install.sh | sh`). On first run, the plugin auto-bootstraps a venv at `~/.claude/steeze/.venv` and installs `jinja2` + `jsonschema` into it. Idempotent on subsequent runs.
- **Some Claude Code history.** 30+ sessions makes positioning stable; 10–30 still works with reduced confidence; under 10 produces a graceful "small history" version of the report.
- **Time.** ~5–10 minutes wall-clock for a 30-session run, almost all of it the structured tagging step where subagents read transcripts in parallel.

## How it works

Three layers of analysis combine into one report:

1. **Deterministic signals** (iteration count, tool diversity, course corrections, tests attempted, …) — computed from message structure in <1 second.
2. **Structured interpretive tagging** — 5 `steeze-tagger` subagents in parallel tag ~36 signals against a fixed taxonomy. Produces aggregated rates positioned against the corpus baseline.
3. **Open behavioral observation** — one `steeze-tagger` subagent in `open` mode reads ~20 sessions schema-free and writes the rich behavioral findings (distinctive patterns, sensitivity, suggested experiments).

The two tracks synthesize into the final report: the structured side gives the archetype label and the comparison bars; the open side gives the recognition lines, the suggested moves, and the framing voice.

Full methodology lives in [`skills/steeze/analysis/interpret.md`](skills/steeze/analysis/interpret.md) inside the plugin. The corpus baseline (measured 2026-05-01) is documented in [`skills/steeze/baselines/README.md`](skills/steeze/baselines/README.md).

## Contributing

This repo is a synced mirror — the source of truth lives in a private monorepo. **Do not file PRs here.** Open issues for bug reports, but pull requests against `bigspinai/steeze` will be closed in favor of upstream changes.

## License

MIT. See [LICENSE](LICENSE).
