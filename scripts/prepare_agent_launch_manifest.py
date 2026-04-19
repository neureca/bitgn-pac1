#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import os

from prepare_parallel_workers import (
    ROOT_DIR,
    _build_agent_prompt,
    _harness_parts,
    _load_manifest,
    _prepare_worker_files,
    _require_api_key,
    _save_json,
    _save_text,
    _select_trials,
    _launch_start_trial,
    _write_runtime_env,
)
from config import load_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare an agent-launch manifest for isolated BitGN trial workers")
    parser.add_argument("--workers", type=int, default=4, help="Number of NEW trials to prepare")
    parser.add_argument("--worker-prefix", default="agent-worker", help="Prefix for worker state filenames")
    parser.add_argument("--run-id", help="Explicit existing run id to use")
    parser.add_argument("--force-new-run", action="store_true", help="Start a fresh run instead of reusing manifest run")
    parser.add_argument("--launch-start-trial", action="store_true", help="Immediately initialize each worker by calling start-trial")
    parser.add_argument("--model", default="gpt-5.4", help="Recommended model for spawned trial agents")
    parser.add_argument("--reasoning-effort", default="high", help="Recommended reasoning effort for spawned trial agents")
    args = parser.parse_args()

    if args.workers < 1:
        print("--workers must be >= 1")
        return 2

    root_dir = ROOT_DIR
    base_dir = root_dir / ".bitgn-state"
    parallel_manifest_path = base_dir / "manifest.json"
    agent_manifest_path = base_dir / "agent-launch-manifest.json"

    settings = load_settings()
    _require_api_key(settings.bitgn_api_key)
    runtime_env_path = _write_runtime_env(base_dir, settings)
    client, pb2_mod, connect_error = _harness_parts(settings.bitgn_host)

    parallel_manifest = _load_manifest(parallel_manifest_path)
    run_id = (args.run_id or "").strip()
    if not run_id and not args.force_new_run:
        manifest_run_id = str(parallel_manifest.get("run_id") or "").strip()
        if manifest_run_id:
            run_id = manifest_run_id

    try:
        if run_id:
            run = client.get_run(pb2_mod.GetRunRequest(run_id=run_id))
        else:
            run = client.start_run(
                pb2_mod.StartRunRequest(
                    name=settings.run_name,
                    benchmark_id=settings.benchmark_id,
                    api_key=settings.bitgn_api_key,
                )
            )
            run_id = run.run_id
            run = client.get_run(pb2_mod.GetRunRequest(run_id=run_id))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1

    selected = _select_trials(run, pb2_mod, args.workers)
    if not selected:
        print(f"No NEW trials available in run {run_id}")
        return 0

    assignments: list[dict[str, object]] = []
    launch_env = os.environ.copy()
    launch_env.setdefault("BENCHMARK_PROFILE", settings.benchmark_profile)

    for index, trial in enumerate(selected, start=1):
        worker_name = f"{args.worker_prefix}-{index:02d}-{trial.task_id}"
        state_path, journal_path = _prepare_worker_files(base_dir, worker_name, settings, run_id)
        prompt_path = base_dir / f"{worker_name}.prompt.md"
        prompt_markdown = _build_agent_prompt(
            root_dir=root_dir,
            settings=settings,
            run_id=run_id,
            worker_name=worker_name,
            task_id=trial.task_id,
            trial_id=trial.trial_id,
            state_path=state_path,
            journal_path=journal_path,
            runtime_env_path=runtime_env_path,
        )
        _save_text(prompt_path, prompt_markdown)

        if args.launch_start_trial:
            exit_code = _launch_start_trial(
                root_dir=root_dir,
                worker_name=worker_name,
                trial_id=trial.trial_id,
                state_path=state_path,
                journal_path=journal_path,
                env=launch_env,
            )
            if exit_code != 0:
                return exit_code

        launch_message = (
            f"You own only trial {trial.trial_id} ({trial.task_id}). "
            f"Source {runtime_env_path}, then read {prompt_path} and follow it exactly. "
            f"Your first reply must confirm reads of AGENTS.md and STATE_MACHINE.md and must list the semantic frame "
            f"(task restatement, explicit constraints, required obligations, candidate blockers) before any runtime action. "
            f"Use only its isolated state and journal files."
        )

        assignments.append(
            {
                "worker_name": worker_name,
                "task_id": trial.task_id,
                "trial_id": trial.trial_id,
                "state_path": str(state_path),
                "journal_path": str(journal_path),
                "runtime_env_path": str(runtime_env_path),
                "prompt_path": str(prompt_path),
                "spawn_recommendation": {
                    "agent_type": "default",
                    "model": args.model,
                    "reasoning_effort": args.reasoning_effort,
                    "fork_context": False,
                    "message": launch_message,
                },
            }
        )

    payload = {
        "benchmark_profile": settings.benchmark_profile,
        "benchmark_id": settings.benchmark_id,
        "run_id": run_id,
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "workers": assignments,
    }
    _save_json(agent_manifest_path, payload)

    print(f"Run: {run_id}")
    print(f"Agent launch manifest: {agent_manifest_path}")
    for assignment in assignments:
        print(
            f"{assignment['worker_name']}: {assignment['task_id']} ({assignment['trial_id']})\n"
            f"  state={assignment['state_path']}\n"
            f"  journal={assignment['journal_path']}\n"
            f"  prompt={assignment['prompt_path']}\n"
            f"  model={args.model}\n"
            f"  reasoning={args.reasoning_effort}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
