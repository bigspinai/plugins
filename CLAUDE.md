# CLAUDE.md — bigspin plugin (synced mirror)

This is the published mirror of the bigspin Claude Code plugin. **The source of truth lives in a private monorepo** (`bigspinai/bigspin-app`, at `public-repos/plugins/`), and a GitHub Actions workflow (`sync-plugins.yml`) propagates main-branch changes here. Do not edit files in this repo directly — they will be overwritten on the next sync. Open issues for bug reports; pull requests will be closed.

## Layout

```
.
├── .claude-plugin/
│   ├── marketplace.json   # declares this repo as the "bigspinai" marketplace
│   └── plugin.json        # plugin manifest
├── AGENTS.md              # cross-tool agent entrypoint (Codex, Cursor, Copilot, …)
├── CLAUDE.md              # this file (Claude Code dev doc)
├── README.md              # end-user-facing
├── LICENSE                # MIT
├── agents/
│   └── persona-tagger.md  # subagent definition (tag mode + open mode)
├── commands/
│   └── persona.md         # /persona slash command entry point
└── skills/
    └── persona/
        ├── SKILL.md       # orchestration playbook (9-step pipeline)
        ├── requirements.txt
        ├── scripts/       # bootstrap.sh, open_report.sh, run_id.sh
        ├── preprocessing/ # sessions_to_csv.py, enrich.py
        ├── tagging/       # tag_sessions.py + taxonomy + prompt template
        ├── analysis/      # archetypes, shapes, metrics, render, templates
        ├── baselines/     # 12 CSV/JSON files — measured corpus baseline
        ├── report/        # Manager exemplar (used by smoke test)
        └── tests/         # smoke_test.py + fixtures
```

The plugin sits at the repo root (no nested `plugins/<name>/` subdirectory) since this repo ships a single plugin.

## Local development

To iterate on the plugin without going through the public install path:

```bash
claude --plugin-dir ./
/persona
```

`/reload-plugins` picks up changes without restarting Claude Code.

## Quick reference (for agents who land here)

- **Where does the report template live?** `skills/persona/analysis/render/templates/`. `report_wrapped.html.j2` is the single report design (Bigspin-branded slides); `render_report.py` renders it. `reports_preview.py` / `cards_preview.py` render the same template for local design preview.
- **Where does the archetype taxonomy live?** `skills/persona/tagging/taxonomy.json`. Locked — do not modify without re-tagging the corpus baseline.
- **Where do the baselines live?** `skills/persona/baselines/`. 12 files, measured 2026-05-01 from 4,846 sessions. Locked.
- **What does the user invoke?** `/persona` (slash command in `commands/persona.md`), which calls the skill in `skills/persona/SKILL.md`. Or, in clone-and-run mode, the paste-in prompt in the README that points the agent at `skills/persona/SKILL.md` directly.
- **Where do per-run artifacts go?** `~/.claude/bigspin/<timestamp>/` on the user's machine. Never inside this repo.

## Pipeline overview

`SKILL.md` is the canonical playbook. At a glance:

1. Bootstrap Python venv via `scripts/bootstrap.sh` (idempotent, uv-preferred).
2. Run renderer smoke test (`tests/smoke_test.py`).
3. Preprocess `~/.claude/projects/` → `sessions.csv`.
4. Enrich with deterministic signals → `sessions_enriched.csv`.
5. Spawn 4 × `persona-tagger` subagents (mode: tag) → per-session JSON annotations.
6. Assemble + compute metrics → `metrics.json` (positioned against baselines).
7. Spawn 1 × `persona-tagger` subagent (mode: open) → `findings.md`.
8. Author `report_content.json` synthesizing both tracks (per `analysis/interpret.md` voice rules).
9. Render with `render_report.py` → HTML (wrapped slides) + Markdown + hero card.

## Restrictions

- **Don't edit `taxonomy.json` or `baselines/*`** without coordinating a baseline re-measurement. The published corpus is immutable; changing the schema invalidates positioning across users.
- **Don't change `model: inherit` in `agents/persona-tagger.md`.** The corpus was tagged on Opus 4.7; the agent must inherit the parent session's model so model-pinning works automatically.
- **Don't add network calls** to any pipeline script. The privacy promise is "all local." Anything that ships data off-machine breaks the contract.
- **Don't hardcode paths inside this plugin directory** in scripts. The orchestrator passes `$BIGSPIN_PLUGIN_ROOT` and `$BIGSPIN_RUN_DIR` for read-only assets and writable per-run output respectively.

## License

MIT. See [LICENSE](LICENSE).
