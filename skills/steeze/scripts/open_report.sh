#!/usr/bin/env bash
#
# Open a file (typically the steeze report.html) in the user's default
# browser. Works on macOS, Linux, and Windows (WSL/Git Bash).
#
# Usage:
#   open_report.sh <path-to-file>
#
# Silent on success; prints an error and exits non-zero if no opener
# is available.

set -euo pipefail

FILE="${1:-}"
if [[ -z "${FILE}" ]]; then
  echo "usage: open_report.sh <path-to-file>" >&2
  exit 64
fi
if [[ ! -e "${FILE}" ]]; then
  echo "ERROR: file not found: ${FILE}" >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin)
    open "${FILE}"
    ;;
  Linux)
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "${FILE}" >/dev/null 2>&1
    elif command -v wslview >/dev/null 2>&1; then
      # WSL with wslu installed.
      wslview "${FILE}"
    else
      echo "ERROR: no xdg-open or wslview found; open this file manually:" >&2
      echo "  ${FILE}" >&2
      exit 1
    fi
    ;;
  MINGW*|MSYS*|CYGWIN*)
    start "" "${FILE}"
    ;;
  *)
    echo "ERROR: unknown platform $(uname -s); open this file manually:" >&2
    echo "  ${FILE}" >&2
    exit 1
    ;;
esac
