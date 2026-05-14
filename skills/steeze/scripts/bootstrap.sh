#!/usr/bin/env bash
#
# Bootstrap a Python venv for steeze and install runtime deps.
#
# Usage:
#   bootstrap.sh <requirements.txt>
#
# Prints the path to the venv's Python interpreter on stdout. Everything
# else (progress, errors) goes to stderr.
#
# Idempotent: if the venv already exists and the requirements file
# matches the one we last installed from (byte-for-byte), this is a no-op
# beyond printing the interpreter path.
#
# Failure modes:
#   exit 2 — no uv and no python3 on PATH. Install one and retry.
#   exit 3 — venv creation or pip install failed; stderr has details.
#
# Recovery: rm -rf ~/.claude/steeze/.venv and re-run.

set -euo pipefail

VENV_DIR="${HOME}/.claude/steeze/.venv"
REQ_FILE="${1:-}"
STAMP="${VENV_DIR}/.installed_from"

if [[ -z "${REQ_FILE}" ]]; then
  echo "usage: bootstrap.sh <requirements.txt>" >&2
  exit 64
fi
if [[ ! -f "${REQ_FILE}" ]]; then
  echo "ERROR: requirements file not found: ${REQ_FILE}" >&2
  exit 64
fi

# Fast path: existing venv with matching stamp.
if [[ -d "${VENV_DIR}" && -f "${STAMP}" && -x "${VENV_DIR}/bin/python" ]]; then
  if cmp -s "${REQ_FILE}" "${STAMP}"; then
    echo "${VENV_DIR}/bin/python"
    exit 0
  fi
  echo "steeze: requirements changed since last bootstrap; rebuilding venv" >&2
fi

mkdir -p "$(dirname "${VENV_DIR}")"

# Prefer uv if present — faster and self-contained.
if command -v uv >/dev/null 2>&1; then
  echo "steeze: bootstrapping venv via uv" >&2
  if ! uv venv --python 3.10 "${VENV_DIR}" >&2; then
    echo "ERROR: uv venv failed" >&2
    exit 3
  fi
  if ! uv pip install --python "${VENV_DIR}/bin/python" -r "${REQ_FILE}" >&2; then
    echo "ERROR: uv pip install failed" >&2
    exit 3
  fi
else
  # Fallback: system python3 + venv module + pip.
  PY="$(command -v python3 || true)"
  if [[ -z "${PY}" ]]; then
    PY="$(command -v python || true)"
  fi
  if [[ -z "${PY}" ]]; then
    cat >&2 <<'EOF'
ERROR: neither uv nor python3 is installed.
Install one:
  - uv (recommended):   curl -LsSf https://astral.sh/uv/install.sh | sh
  - Python 3.10+:       https://www.python.org/downloads/
Then re-run /steeze.
EOF
    exit 2
  fi
  echo "steeze: bootstrapping venv via ${PY} -m venv (uv not found)" >&2
  if ! "${PY}" -m venv "${VENV_DIR}" >&2; then
    echo "ERROR: '${PY} -m venv' failed; ensure the python3-venv package is installed" >&2
    exit 3
  fi
  if ! "${VENV_DIR}/bin/python" -m pip install --quiet --upgrade pip >&2; then
    echo "ERROR: pip self-upgrade failed" >&2
    exit 3
  fi
  if ! "${VENV_DIR}/bin/python" -m pip install --quiet -r "${REQ_FILE}" >&2; then
    echo "ERROR: pip install -r ${REQ_FILE} failed" >&2
    exit 3
  fi
fi

cp "${REQ_FILE}" "${STAMP}"
echo "${VENV_DIR}/bin/python"
