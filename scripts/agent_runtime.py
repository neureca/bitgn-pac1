#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT_DIR / ".bitgn-state"
REGISTRY_PATH = STATE_DIR / "agents-runtime.json"
MANIFEST_PATH = STATE_DIR / "agent-launch-manifest.json"
LOCK_PATH = STATE_DIR / "agents-runtime.lock"


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(temp_path, path)


def _load_registry() -> dict[str, object]:
    payload = _load_json(REGISTRY_PATH)
    payload.setdefault("runs", [])
    return payload


def _save_registry(payload: dict[str, object]) -> None:
    _save_json(REGISTRY_PATH, payload)


def _with_registry_lock(func, *, timeout_sec: float = 5.0):
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as handle:
        deadline = time.monotonic() + timeout_sec
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(f"Timed out waiting for registry lock: {LOCK_PATH}", file=sys.stderr)
                    raise SystemExit(2)
                time.sleep(0.05)
        try:
            return func()
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_manifest() -> dict[str, object]:
    payload = _load_json(MANIFEST_PATH)
    if not payload:
        print(f"Missing manifest: {MANIFEST_PATH}", file=sys.stderr)
        raise SystemExit(2)
    workers = payload.get("workers")
    if not isinstance(workers, list):
        print(f"Malformed manifest: {MANIFEST_PATH}", file=sys.stderr)
        raise SystemExit(2)
    return payload


def _find_run(registry: dict[str, object], run_id: str) -> dict[str, object] | None:
    for item in registry["runs"]:
        if isinstance(item, dict) and item.get("run_id") == run_id:
            return item
    return None


def _find_worker(run_entry: dict[str, object], worker_name: str) -> dict[str, object] | None:
    workers = run_entry.get("workers", [])
    if not isinstance(workers, list):
        return None
    for item in workers:
        if isinstance(item, dict) and item.get("worker_name") == worker_name:
            return item
    return None


def _sync_registry_from_manifest(registry: dict[str, object], manifest: dict[str, object]) -> dict[str, object]:
    run_id = str(manifest.get("run_id") or "")
    if not run_id:
        print("Manifest has no run_id", file=sys.stderr)
        raise SystemExit(2)

    run_entry = _find_run(registry, run_id)
    if run_entry is None:
        run_entry = {
            "run_id": run_id,
            "benchmark_profile": manifest.get("benchmark_profile", ""),
            "benchmark_id": manifest.get("benchmark_id", ""),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "workers": [],
        }
        registry["runs"].append(run_entry)

    existing_by_name = {
        item.get("worker_name"): item
        for item in run_entry.get("workers", [])
        if isinstance(item, dict) and item.get("worker_name")
    }
    synced_workers: list[dict[str, object]] = []
    seen_worker_names: set[str] = set()
    for item in manifest["workers"]:
        if not isinstance(item, dict):
            continue
        worker_name = str(item.get("worker_name") or "")
        if not worker_name:
            continue
        seen_worker_names.add(worker_name)
        existing = existing_by_name.get(worker_name, {})
        worker_entry = {
            "worker_name": worker_name,
            "task_id": item.get("task_id", ""),
            "trial_id": item.get("trial_id", ""),
            "state_path": item.get("state_path", ""),
            "journal_path": item.get("journal_path", ""),
            "prompt_path": item.get("prompt_path", ""),
            "agent_id": existing.get("agent_id", ""),
            "status": existing.get("status", "prepared"),
            "prepared_at": existing.get("prepared_at", _utc_now()),
            "started_at": existing.get("started_at", ""),
            "finished_at": existing.get("finished_at", ""),
            "last_transition_at": existing.get("last_transition_at", _utc_now()),
            "retry_count": existing.get("retry_count", 0),
            "last_error": existing.get("last_error", ""),
            "notes": existing.get("notes", ""),
        }
        synced_workers.append(worker_entry)

    for worker_name, existing in existing_by_name.items():
        if worker_name in seen_worker_names:
            continue
        synced_workers.append(existing)

    run_entry["workers"] = synced_workers
    run_entry["updated_at"] = _utc_now()
    return run_entry


def _cmd_prepare(args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "prepare_agent_launch_manifest.py"),
        "--workers",
        str(args.workers),
        "--worker-prefix",
        args.worker_prefix,
        "--model",
        args.model,
        "--reasoning-effort",
        args.reasoning_effort,
    ]
    if args.run_id:
        cmd.extend(["--run-id", args.run_id])
    if args.force_new_run:
        cmd.append("--force-new-run")
    if args.launch_start_trial:
        cmd.append("--launch-start-trial")

    result = subprocess.run(cmd, cwd=ROOT_DIR, check=False)
    if result.returncode != 0:
        return result.returncode

    manifest = _load_manifest()

    def _update() -> dict[str, object]:
        registry = _load_registry()
        run_entry = _sync_registry_from_manifest(registry, manifest)
        _save_registry(registry)
        return run_entry

    run_entry = _with_registry_lock(_update)
    print(f"Registry: {REGISTRY_PATH}")
    print(f"Prepared workers: {len(run_entry['workers'])}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    registry = _load_registry()
    runs = registry.get("runs", [])
    if not runs:
        print("No agent runtime registry entries.")
        return 0

    for run_entry in runs:
        if not isinstance(run_entry, dict):
            continue
        if args.run_id and run_entry.get("run_id") != args.run_id:
            continue
        print(f"Run: {run_entry.get('run_id')}")
        print(f"  updated_at={run_entry.get('updated_at', '')}")
        for worker in run_entry.get("workers", []):
            if not isinstance(worker, dict):
                continue
            print(
                f"  {worker.get('worker_name')}: {worker.get('task_id')} ({worker.get('trial_id')}) "
                f"status={worker.get('status')} retries={worker.get('retry_count', 0)} "
                f"agent_id={worker.get('agent_id') or '-'}"
            )
    return 0


def _cmd_mark_started(args: argparse.Namespace) -> int:
    def _update() -> None:
        registry = _load_registry()
        run_entry = _find_run(registry, args.run_id)
        if run_entry is None:
            print(f"Unknown run_id: {args.run_id}", file=sys.stderr)
            raise SystemExit(2)
        worker = _find_worker(run_entry, args.worker_name)
        if worker is None:
            print(f"Unknown worker_name for run {args.run_id}: {args.worker_name}", file=sys.stderr)
            raise SystemExit(2)
        if worker.get("status") == "interrupted":
            worker["retry_count"] = int(worker.get("retry_count", 0) or 0) + 1
        worker["agent_id"] = args.agent_id
        worker["status"] = "running"
        worker["started_at"] = _utc_now()
        worker["finished_at"] = ""
        worker["last_transition_at"] = _utc_now()
        worker["last_error"] = ""
        run_entry["updated_at"] = _utc_now()
        _save_registry(registry)

    _with_registry_lock(_update)
    print(f"Marked started: {args.worker_name} -> {args.agent_id}")
    return 0


def _cmd_mark_finished(args: argparse.Namespace) -> int:
    def _update() -> None:
        registry = _load_registry()
        run_entry = _find_run(registry, args.run_id)
        if run_entry is None:
            print(f"Unknown run_id: {args.run_id}", file=sys.stderr)
            raise SystemExit(2)
        worker = _find_worker(run_entry, args.worker_name)
        if worker is None:
            print(f"Unknown worker_name for run {args.run_id}: {args.worker_name}", file=sys.stderr)
            raise SystemExit(2)
        worker["status"] = args.status
        worker["finished_at"] = _utc_now()
        worker["last_transition_at"] = _utc_now()
        if args.note:
            worker["notes"] = args.note
        if args.error:
            worker["last_error"] = args.error
        run_entry["updated_at"] = _utc_now()
        _save_registry(registry)

    _with_registry_lock(_update)
    print(f"Marked {args.status}: {args.worker_name}")
    return 0


def _cmd_next_pending(args: argparse.Namespace) -> int:
    registry = _load_registry()
    run_entry = _find_run(registry, args.run_id)
    if run_entry is None:
        print(f"Unknown run_id: {args.run_id}", file=sys.stderr)
        return 2
    pending = []
    for worker in run_entry.get("workers", []):
        if not isinstance(worker, dict):
            continue
        if worker.get("status") in {"prepared", "running", "interrupted", "failed"}:
            pending.append(worker)
    if not pending:
        print("No pending workers.")
        return 0
    for worker in pending:
        print(json.dumps(worker, ensure_ascii=False))
    return 0


def _cmd_mark_interrupted(args: argparse.Namespace) -> int:
    def _update() -> None:
        registry = _load_registry()
        run_entry = _find_run(registry, args.run_id)
        if run_entry is None:
            print(f"Unknown run_id: {args.run_id}", file=sys.stderr)
            raise SystemExit(2)
        worker = _find_worker(run_entry, args.worker_name)
        if worker is None:
            print(f"Unknown worker_name for run {args.run_id}: {args.worker_name}", file=sys.stderr)
            raise SystemExit(2)
        worker["status"] = "interrupted"
        worker["last_transition_at"] = _utc_now()
        if args.error:
            worker["last_error"] = args.error
        if args.note:
            worker["notes"] = args.note
        run_entry["updated_at"] = _utc_now()
        _save_registry(registry)

    _with_registry_lock(_update)
    print(f"Marked interrupted: {args.worker_name}")
    return 0


def _cmd_recover(args: argparse.Namespace) -> int:
    def _collect() -> list[dict[str, object]]:
        registry = _load_registry()
        run_entry = _find_run(registry, args.run_id)
        if run_entry is None:
            print(f"Unknown run_id: {args.run_id}", file=sys.stderr)
            raise SystemExit(2)

        recovery: list[dict[str, object]] = []
        for worker in run_entry.get("workers", []):
            if not isinstance(worker, dict):
                continue
            status = str(worker.get("status") or "")
            retry_count = int(worker.get("retry_count", 0) or 0)
            if status == "prepared":
                action = "launch"
            elif status == "interrupted" and retry_count < args.max_retries:
                action = "retry"
            else:
                continue
            recovery.append(
                {
                    "worker_name": worker.get("worker_name", ""),
                    "task_id": worker.get("task_id", ""),
                    "trial_id": worker.get("trial_id", ""),
                    "status": worker.get("status", ""),
                    "retry_count": retry_count,
                    "action": action,
                    "state_path": worker.get("state_path", ""),
                    "journal_path": worker.get("journal_path", ""),
                    "prompt_path": worker.get("prompt_path", ""),
                }
            )

        run_entry["updated_at"] = _utc_now()
        _save_registry(registry)
        return recovery

    recovery = _with_registry_lock(_collect)

    if not recovery:
        print("No workers eligible for recovery.")
        return 0

    for item in recovery:
        print(json.dumps(item, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Registry-backed orchestration helper for isolated BitGN trial agents")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="Create agent-launch manifest and sync registry")
    prepare.add_argument("--workers", type=int, default=4)
    prepare.add_argument("--worker-prefix", default="agent-worker")
    prepare.add_argument("--run-id")
    prepare.add_argument("--force-new-run", action="store_true")
    prepare.add_argument("--launch-start-trial", action="store_true")
    prepare.add_argument("--model", default="gpt-5.4")
    prepare.add_argument("--reasoning-effort", default="high")
    prepare.set_defaults(func=_cmd_prepare)

    status = subparsers.add_parser("status", help="Show registry state")
    status.add_argument("--run-id")
    status.set_defaults(func=_cmd_status)

    mark_started = subparsers.add_parser("mark-started", help="Mark a worker as running with an agent id")
    mark_started.add_argument("--run-id", required=True)
    mark_started.add_argument("--worker-name", required=True)
    mark_started.add_argument("--agent-id", required=True)
    mark_started.set_defaults(func=_cmd_mark_started)

    mark_finished = subparsers.add_parser("mark-finished", help="Mark a worker as completed/failed/interrupted")
    mark_finished.add_argument("--run-id", required=True)
    mark_finished.add_argument("--worker-name", required=True)
    mark_finished.add_argument("--status", required=True, choices=["completed", "failed", "interrupted"])
    mark_finished.add_argument("--note", default="")
    mark_finished.add_argument("--error", default="")
    mark_finished.set_defaults(func=_cmd_mark_finished)

    mark_interrupted = subparsers.add_parser("mark-interrupted", help="Mark a worker as interrupted and eligible for recovery")
    mark_interrupted.add_argument("--run-id", required=True)
    mark_interrupted.add_argument("--worker-name", required=True)
    mark_interrupted.add_argument("--note", default="")
    mark_interrupted.add_argument("--error", default="")
    mark_interrupted.set_defaults(func=_cmd_mark_interrupted)

    next_pending = subparsers.add_parser("next-pending", help="Emit registry entries that still need attention")
    next_pending.add_argument("--run-id", required=True)
    next_pending.set_defaults(func=_cmd_next_pending)

    recover = subparsers.add_parser("recover", help="Emit workers that should be launched or retried")
    recover.add_argument("--run-id", required=True)
    recover.add_argument("--max-retries", type=int, default=1)
    recover.set_defaults(func=_cmd_recover)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
