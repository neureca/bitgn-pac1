#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "${BITGN_API_KEY:-}" ]]; then
  echo "BITGN_API_KEY is required"
  exit 2
fi

STATE_FILE="$(mktemp)"
JOURNAL_FILE="$(mktemp)"
trap 'rm -f "$STATE_FILE" "$JOURNAL_FILE"' EXIT
PYTHON_CMD=(uv run python3)

cat >"$STATE_FILE" <<'JSON'
{
  "benchmark_profile": "prod",
  "benchmark_id": "bitgn/pac1-prod",
  "run_id": "run-smoke",
  "trial_id": "vm-smoke",
  "task_id": "smoke",
  "harness_url": "https://example.invalid",
  "answer_sent": false,
  "answer_outcome": "",
  "pending_verification_checks": [{"path":"/docs/todo.txt","expectation":"present"}]
}
JSON

echo "== answer should be blocked before network =="
if BITGN_STATE_PATH="$STATE_FILE" BITGN_JOURNAL_PATH="$JOURNAL_FILE" BENCHMARK_PROFILE=prod BITGN_MIN_OK_REFS=1 "${PYTHON_CMD[@]}" main.py answer-ok \
  --message "Updated /docs/todo.txt" \
  --ref /docs/todo.txt; then
  echo "Expected answer guard to block, but command succeeded"
  exit 1
fi

echo "== guard behaved as expected =="
BITGN_STATE_PATH="$STATE_FILE" BITGN_JOURNAL_PATH="$JOURNAL_FILE" "${PYTHON_CMD[@]}" main.py session
