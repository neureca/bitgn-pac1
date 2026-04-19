#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "${BITGN_API_KEY:-}" ]]; then
  echo "BITGN_API_KEY is required"
  exit 2
fi

if [[ $# -lt 1 ]]; then
  echo "Usage:"
  echo "  $0 search <pattern> [root]"
  echo "  $0 find <name> [root]"
  exit 2
fi

PROFILE="${BENCHMARK_PROFILE:-prod}"
MODE="$1"
QUERY="${2:-}"
ROOT="${3:-/}"
PYTHON_CMD=(uv run python3)

if [[ -z "$QUERY" ]]; then
  echo "Missing query"
  exit 2
fi

echo "== start-trial =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py start-trial

echo "== inspect =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py inspect

case "$MODE" in
  search)
    echo "== search =="
    BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py search "$QUERY" --root "$ROOT"
    ;;
  find)
    echo "== find =="
    BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py find "$QUERY" --root "$ROOT"
    ;;
  *)
    echo "Unsupported mode: $MODE"
    exit 2
    ;;
esac

echo "== session =="
BENCHMARK_PROFILE="$PROFILE" "${PYTHON_CMD[@]}" main.py session
