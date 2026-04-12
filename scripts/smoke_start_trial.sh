#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "${BITGN_API_KEY:-}" ]]; then
  echo "BITGN_API_KEY is required"
  exit 2
fi

PROFILE="${BENCHMARK_PROFILE:-prod}"
PYTHON_CMD=(uv run python3)

echo "== session =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py session

echo "== start-trial =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py start-trial "${1:-}"

echo "== inspect =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py inspect

echo "== session =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py session
