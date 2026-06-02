# AGENTS.md

This repository is the **bigspin** Claude Code plugin. It analyzes a user's local Claude Code session history (`~/.claude/projects/`) and renders a personal "practice mirror" report — archetype, signature moves, and how the user compares against a measured baseline of 4,846 real sessions.

This file is the cross-tool entrypoint for agentic coding tools (Codex, Cursor, Copilot, Claude Code, …) so the skill is discoverable without relying on a single tool's plugin format.

## When the user asks for their Claude Code archetype, persona, or practice report

Follow [`skills/persona/SKILL.md`](skills/persona/SKILL.md) end to end. It's a 9-step orchestration playbook. Briefly:

1. **Plugin root** = this repo's root directory (the directory containing this file). If you're invoked outside the Claude Code plugin install path, set `BIGSPIN_PLUGIN_ROOT="$PWD"` before following the SKILL.md setup section.
2. **Bootstrap** a Python venv via `skills/persona/scripts/bootstrap.sh`. Idempotent; first run installs `jinja2` + `jsonschema` into `~/.claude/bigspin/.venv`.
3. **Smoke test** the renderer (`skills/persona/tests/smoke_test.py`) before any tagging work.
4. **Pipeline**: preprocess → enrich → spawn 4× `persona-tagger` subagents in parallel for structured tagging → assemble + compute metrics → spawn 1× `persona-tagger` in open mode (reading ~12 of the exported transcripts) for behavioral findings → author `report_content.json` → render HTML + Markdown + hero card.
5. **Outputs** land in `~/.claude/bigspin/<timestamp>/` on the user's machine. Nothing leaves the machine.
6. **Deliver**: print `hero.md` inline, then open `report.html` in the user's browser via `skills/persona/scripts/open_report.sh`.

## Restrictions

- **Do not edit `skills/persona/tagging/taxonomy.json` or anything under `skills/persona/baselines/`** — they're locked corpus state. Changing the schema invalidates positioning across users.
- **Do not change `model: inherit`** in `agents/persona-tagger.md`. The corpus was tagged on Opus 4.7; the agent must inherit the parent session's model so model-pinning works.
- **Do not add network calls** to any pipeline script. The privacy promise is "all local." Anything that ships data off-machine breaks the contract.
- **Do not invent steps not in SKILL.md.** Do not skip the smoke test or the bootstrap. If something fails, surface the error and stop — do not paper over it.
