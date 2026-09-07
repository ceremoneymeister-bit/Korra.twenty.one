#!/usr/bin/env bash
# Run in a dedicated calculator document worktree; never reuse the kernel venv.
set -euo pipefail
DOC_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TASK_ROOT="$(cd "$DOC_ROOT/../.." && pwd)"
TASK_VENV="$TASK_ROOT/.venv"
DEPENDENCIES="$DOC_ROOT/requirements.lock"
if [[ "${1:-}" == "--test" && $# == 1 ]]; then
  DEPENDENCIES="$DOC_ROOT/requirements-test.lock"
elif [[ $# != 0 ]]; then
  echo "Usage: bash calculator/documents/setup.sh [--test]" >&2
  exit 2
fi
if [[ -e "$TASK_VENV" && ! -f "$TASK_VENV/.calculator-documents-venv" ]]; then
  echo "Existing .venv is not owned by this adapter; use a fresh dedicated worktree." >&2
  exit 2
fi
if [[ ! -e "$TASK_VENV" ]]; then
  python3 -m venv "$TASK_VENV"
  touch "$TASK_VENV/.calculator-documents-venv"
fi
"$TASK_VENV/bin/python" -m pip install -r "$DEPENDENCIES"
"$TASK_VENV/bin/python" -m pip check
