# CLAUDE.md — steeze plugin (synced mirror)

This is the published mirror of the steeze Claude Code plugin. **The source of truth lives in a private monorepo** (`bigspinai/bigspin-app`, at `public-repos/steeze/`), and a GitHub Actions workflow (`sync-steeze.yml`) propagates main-branch changes here. Do not edit files in this repo directly — they will be overwritten on the next sync. Open issues for bug reports; pull requests will be closed.

## Layout

```
.
├── .claude-plugin/
│   ├── marketplace.json   # declares this repo as the "bigspinai" marketplace
│   └── plugin.json        # plugin manifest
├── commands/
│   └── steeze.md          # /steeze slash command entry point
├── skills/
│   └── steeze/
│       ├── SKILL.md       # orchestration playbook (9-step pipeline)
│       ├── requirements.txt
│       ├── scripts/       # bootstrap.sh, open_report.sh, run_id.sh
│       ├── preprocessing/ # sessions_to_csv.py, enrich.py
│       ├── tagging/       # tag_sessions.py + taxonomy + prompt template
│       ├── analysis/      # archetypes, shapes, metrics, render, templates
│       ├── baselines/     # 12 CSV/JSON files — measured corpus baseline
│       ├── report/        # Manager exemplar (used by smoke test)
│       └── tests/         # smoke_test.py + fixtures
├── agents/
│   └── steeze-tagger.md   # subagent definition (tag mode + open mode)
├── README.md              # end-user-facing
├── LICENSE                # MIT
└── CLAUDE.md              # this file
```

## Local development

To iterate on the plugin without going through the public install path:

```bash
claude --plugin-dir ./
/steeze
```

`/reload-plugins` picks up changes without restarting Claude Code.

## Quick reference (for agents who land here)

- **Where does the report style live?** `skills/steeze/analysis/render/templates/`. `report_wrapped.html.j2` is `default` (the Bigspin-branded slide variant), `report.html.j2` is `editorial`. Selected via `render_report.py --style {default,editorial}`.
- **Where does the archetype taxonomy live?** `skills/steeze/tagging/taxonomy.json`. Locked — do not modify without re-tagging the corpus baseline.
- **Where do the baselines live?** `skills/steeze/baselines/`. 12 files, measured 2026-05-01 from 4,846 sessions. Locked.
- **What does the user invoke?** `/steeze` (slash command in `commands/steeze.md`), which calls the skill in `skills/steeze/SKILL.md`.
- **Where do per-run artifacts go?** `~/.claude/steeze/<timestamp>/` on the user's machine. Never inside this repo.

## Pipeline overview

`SKILL.md` is the canonical playbook. At a glance:

1. Bootstrap Python venv via `scripts/bootstrap.sh` (idempotent, uv-preferred).
2. Run renderer smoke test (`tests/smoke_test.py`).
3. Preprocess `~/.claude/projects/` → `sessions.csv`.
4. Enrich with deterministic signals → `sessions_enriched.csv`.
5. Spawn 5 × `steeze-tagger` subagents (mode: tag) → per-session JSON annotations.
6. Assemble + compute metrics → `metrics.json` (positioned against baselines).
7. Spawn 1 × `steeze-tagger` subagent (mode: open) → `findings.md`.
8. Author `report_content.json` synthesizing both tracks (per `analysis/interpret.md` voice rules).
9. Render with `--style default|editorial` (default `default`) → HTML + Markdown + hero card.

## Restrictions

- **Don't edit `taxonomy.json` or `baselines/*`** without coordinating a baseline re-measurement. The published corpus is immutable; changing the schema invalidates positioning across users.
- **Don't change `model: inherit` in `agents/steeze-tagger.md`.** The corpus was tagged on Opus 4.7; the agent must inherit the parent session's model so model-pinning works automatically.
- **Don't add network calls** to any pipeline script. The privacy promise is "all local." Anything that ships data off-machine breaks the contract.
- **Don't hardcode paths inside this plugin directory** in scripts. The orchestrator passes `$STEEZE_PLUGIN_ROOT` and `$STEEZE_RUN_DIR` for read-only assets and writable per-run output respectively.

## License

MIT. See [LICENSE](LICENSE).
