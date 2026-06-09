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
├── requirements.txt       # shared Python deps (jinja2, jsonschema) — all skills
├── scripts/               # shared run contract + helpers (used by EVERY skill)
│   ├── new_run.sh         #   run contract: bootstrap venv + create OUT_DIR, emits PY/OUT_DIR/RUN_ID
│   ├── bootstrap.sh       #   idempotent venv at ~/.claude/bigspin/.venv
│   ├── run_id.sh          #   YYYYMMDD-HHMMSS run id
│   └── open_report.sh     #   cross-platform browser open
├── lib/
│   └── report_io.py       # shared deliverable filename scheme (<slug>-report.html, …)
├── agents/
│   └── persona-tagger.md  # subagent definition (tag mode + open mode)
├── commands/              # slash-command entry points
│   ├── persona.md         # /persona
│   ├── token-roi.md       # /token-roi
│   └── sample-persona-report.md
└── skills/
    ├── persona/           # the 9-step practice-mirror pipeline
    │   ├── SKILL.md       # orchestration playbook
    │   ├── preprocessing/ # sessions_to_csv.py, enrich.py
    │   ├── tagging/       # tag_sessions.py + taxonomy + prompt template
    │   ├── analysis/      # archetypes, shapes, metrics, render, templates
    │   ├── baselines/     # 12 CSV/JSON files — measured corpus baseline
    │   ├── report/        # Manager exemplar (used by smoke test)
    │   └── tests/         # smoke_test.py + fixtures
    ├── token-roi/         # token-vs-outcome ROI report (own analysis/, tests/)
    └── sample-persona-report/  # demo render from checked-in fixtures (reuses persona/)
```

Shared code (`scripts/`, `lib/`, `requirements.txt`) lives at the repo root, not under any one skill — every skill calls `scripts/new_run.sh <slug>` to start a run, so the output location and naming are defined in exactly one place.

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
- **Where do per-run artifacts go?** `~/.claude/bigspin/<slug>-<timestamp>/` on the user's machine (`persona-…`, `token-roi-…`, `sample-persona-…`). Created by `scripts/new_run.sh`. Never inside this repo or the current working directory — this holds for both the installed-plugin and clone-and-run paths.

## Run contract (read before touching output paths)

Every skill begins with `eval "$(bash scripts/new_run.sh <slug>)"`. That script is the single source of truth for *where output goes and how it's named*: it resolves the plugin root, bootstraps the shared venv, creates `~/.claude/bigspin/<slug>-<timestamp>/`, and exports `PY`, `OUT_DIR`, `RUN_ID`, `BIGSPIN_PLUGIN_ROOT`, `PYTHONPATH`. Skills then reference *code* via `$BIGSPIN_PLUGIN_ROOT/...` and write *everything* under `$OUT_DIR`. Deliverable filenames come from `lib/report_io.py`. Do not reintroduce cwd-relative output paths in any SKILL — that's the bug this contract exists to prevent.

## Pipeline overview

`SKILL.md` is the canonical playbook. At a glance:

1. Start the run via `scripts/new_run.sh persona` (resolves root, bootstraps venv, creates `OUT_DIR`).
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
- **Don't hardcode paths inside this plugin directory** in scripts, and don't write output relative to cwd. Start every skill with `scripts/new_run.sh <slug>`; reference read-only assets via `$BIGSPIN_PLUGIN_ROOT` and write per-run output under `$OUT_DIR`.

## License

MIT. See [LICENSE](LICENSE).
