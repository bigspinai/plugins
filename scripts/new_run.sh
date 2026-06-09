#!/usr/bin/env bash
#
# The bigspin report run contract — the SINGLE source of truth for where a
# report run lives and how it is bootstrapped. Every report skill calls this
# once at the start; nothing else decides the output location.
#
# Usage:
#   eval "$(bash <plugin-root>/scripts/new_run.sh <slug>)"
#
# <slug> is the report type, also the run-dir / filename prefix:
#   persona | token-roi | sample-persona
#
# On success it prints eval-able `export` lines on stdout (progress/errors go
# to stderr) and the caller ends up with these set:
#   BIGSPIN_PLUGIN_ROOT  absolute plugin root (canonicalized here)
#   PY                   venv python interpreter (from bootstrap.sh)
#   RUN_ID               <slug>-YYYYMMDD-HHMMSS
#   OUT_DIR              ~/.claude/bigspin/<RUN_ID>   (created, absolute)
#   PYTHONPATH           includes <root>/lib so renderers can `import report_io`
#
# Because OUT_DIR is always absolute under $HOME, the run is identical whether
# the skill was launched as an installed plugin or from a local clone — the
# current working directory never affects where output lands.
#
# Failure modes are inherited from bootstrap.sh:
#   exit 2 — no uv/python3 on PATH
#   exit 3 — venv/pip failure
#   exit 64 — usage error (missing slug)

set -euo pipefail

SLUG="${1:-}"
if [[ -z "${SLUG}" ]]; then
  echo "usage: new_run.sh <slug>   (e.g. persona | token-roi | sample-persona)" >&2
  exit 64
fi

# Resolve the plugin root from this script's own location (scripts/new_run.sh
# → plugin root is one level up), honoring an explicit override. This is what
# frees every skill from having to walk up from its own SKILL.md.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${BIGSPIN_PLUGIN_ROOT:-$(cd "${SCRIPT_DIR}/.." && pwd)}"

# Shared, idempotent venv bootstrap (prints the interpreter path on stdout).
PY="$(bash "${SCRIPT_DIR}/bootstrap.sh" "${ROOT}/requirements.txt")"

RUN_ID="${SLUG}-$(bash "${SCRIPT_DIR}/run_id.sh")"
OUT_DIR="${HOME}/.claude/bigspin/${RUN_ID}"
mkdir -p "${OUT_DIR}"

# Emit eval-able exports. printf %q quotes each value so paths with spaces
# survive the round-trip through `eval`.
printf 'export BIGSPIN_PLUGIN_ROOT=%q\n' "${ROOT}"
printf 'export PY=%q\n' "${PY}"
printf 'export RUN_ID=%q\n' "${RUN_ID}"
printf 'export OUT_DIR=%q\n' "${OUT_DIR}"
printf 'export PYTHONPATH=%q\n' "${ROOT}/lib${PYTHONPATH:+:${PYTHONPATH}}"
