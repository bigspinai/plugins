---
description: Open a sample persona report rendered from checked-in fixture data. No pipeline run, no API calls.
argument-hint: "[showrunner|generalist]"
---

The user invoked `/sample-persona-report`. Follow `skills/sample-persona-report/SKILL.md` to completion.

Pass through any argument the user supplied:
- (no arg) or `showrunner` — the canonical Showrunner exemplar (sharp fingerprint, full shadow + cohort bars).
- `generalist` — the Multi-Mode Journeyman edge case (no shadow, no within-archetype positioning).

The skill renders the report from pre-committed JSON fixtures in ~500ms, writes artifacts to `~/.claude/bigspin/sample-<timestamp>/`, and opens `report.html` in the browser. No subagent tagging, no metrics computation, no Anthropic API calls.

If the renderer fails (schema validation, unresolved `*_ref`), surface the error and stop — do not paper over it.
