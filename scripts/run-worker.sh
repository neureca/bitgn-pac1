#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ $# -lt 2 ]]; then
  echo "Usage:"
  echo "  $0 <worker-name> <trial-id> [command ...]"
  echo
  echo "Examples:"
  echo "  BITGN_API_KEY=... $0 worker-01 vm-123"
  echo "  BITGN_API_KEY=... $0 worker-01 vm-123 uv run python3 main.py context"
  exit 2
fi

if [[ -z "${BITGN_API_KEY:-}" ]]; then
  echo "BITGN_API_KEY is required"
  exit 2
fi

WORKER_NAME="$1"
TRIAL_ID="$2"
shift 2

mkdir -p .bitgn-state

export BENCHMARK_PROFILE="${BENCHMARK_PROFILE:-prod}"
export BITGN_STATE_PATH="${BITGN_STATE_PATH:-.bitgn-state/${WORKER_NAME}.run.json}"
export BITGN_JOURNAL_PATH="${BITGN_JOURNAL_PATH:-.bitgn-state/${WORKER_NAME}.journal.jsonl}"

PYTHON_CMD=()
if [[ -x .venv/bin/python3 ]]; then
  PYTHON_CMD=(.venv/bin/python3)
elif command -v uv >/dev/null 2>&1; then
  PYTHON_CMD=(uv run python3)
else
  echo "Need either .venv/bin/python3 or uv on PATH"
  exit 2
fi

if [[ $# -eq 0 ]]; then
  exec "${PYTHON_CMD[@]}" main.py start-trial "$TRIAL_ID"
fi

exec "$@"
