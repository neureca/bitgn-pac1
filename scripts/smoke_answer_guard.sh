#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -z "${BITGN_API_KEY:-}" ]]; then
  echo "BITGN_API_KEY is required"
  exit 2
fi

STATE_FILE="$(mktemp)"
trap 'rm -f "$STATE_FILE"' EXIT

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
  "pending_verification_paths": ["/docs/todo.txt"]
}
JSON

echo "== answer should be blocked before network =="
if BITGN_STATE_PATH="$STATE_FILE" BENCHMARK_PROFILE=prod python3 main.py answer \
  --outcome OUTCOME_OK \
  --message "Updated /docs/todo.txt" \
  --ref /docs/todo.txt; then
  echo "Expected answer guard to block, but command succeeded"
  exit 1
fi

echo "== guard behaved as expected =="
BITGN_STATE_PATH="$STATE_FILE" python3 main.py session
