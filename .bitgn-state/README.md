# Parallel Worker State

Use this directory for per-worker CLI state and journals when running multiple trial workers in parallel.

Recommended naming:

- state: `.bitgn-state/<worker-name>.run.json`
- journal: `.bitgn-state/<worker-name>.journal.jsonl`
- prompt: `.bitgn-state/<worker-name>.prompt.md`

Example:

```bash
BITGN_STATE_PATH=.bitgn-state/worker-t012.run.json \
BITGN_JOURNAL_PATH=.bitgn-state/worker-t012.journal.jsonl \
BENCHMARK_PROFILE=prod \
uv run python3 main.py start-trial vm-...
```

Each worker must use its own `BITGN_STATE_PATH` and `BITGN_JOURNAL_PATH`.

Coordinator helper:

```bash
uv sync
BITGN_API_KEY=... BENCHMARK_PROFILE=prod \
uv run python3 scripts/prepare_parallel_workers.py --workers 4 --launch-start-trial --emit-agent-prompts
```

This will:

- create or reuse a run for the parallel worker manifest
- select the next `NEW` trials
- create isolated state and journal files under `.bitgn-state/`
- write one ready-to-use agent prompt file per worker
- optionally initialize each worker with an explicit `start-trial <trial_id>`

Agent-launch manifest helper:

```bash
BITGN_API_KEY=... BENCHMARK_PROFILE=prod \
uv run python3 scripts/prepare_agent_launch_manifest.py --workers 4 --launch-start-trial
```

This writes `.bitgn-state/agent-launch-manifest.json` with one entry per trial worker, including:

- isolated state and journal paths
- a ready-to-use `.prompt.md`
- a compact `spawn_recommendation` block that points the agent at the prompt file instead of embedding the full prompt again

Runtime registry helper:

```bash
BITGN_API_KEY=... BENCHMARK_PROFILE=prod \
uv run python3 scripts/agent_runtime.py prepare --workers 4 --launch-start-trial
```

This also maintains `.bitgn-state/agents-runtime.json` so agent launches can be resumed after interruption.

Useful commands:

```bash
uv run python3 scripts/agent_runtime.py status
uv run python3 scripts/agent_runtime.py mark-started --run-id <run> --worker-name <worker> --agent-id <agent>
uv run python3 scripts/agent_runtime.py mark-finished --run-id <run> --worker-name <worker> --status completed
uv run python3 scripts/agent_runtime.py mark-interrupted --run-id <run> --worker-name <worker> --error "..."
uv run python3 scripts/agent_runtime.py recover --run-id <run>
```

Recovery playbook:

1. `status` to inspect the current registry state
2. If an agent died mid-trial, mark it `interrupted`
3. Use `recover --run-id <run>` to list workers eligible for relaunch
4. Relaunch only `prepared` and `interrupted` workers
5. Call `mark-started` only after the relaunch really happened; retries are counted there, not in `recover`
6. Do not auto-relaunch `running` workers unless you know the agent is dead
7. Do not auto-relaunch `completed` workers
