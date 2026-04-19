# BitGN PAC1 Operator CLI

This repository exposes a stateful operator CLI in `main.py`.

Use it as a saved toolbelt for the BitGN control plane and PCM runtime.
The CLI persists local operator state in the path from `BITGN_STATE_PATH` or `.bitgn-run.json` by default.
Completed trials are appended to `BITGN_JOURNAL_PATH` or `.bitgn-journal.jsonl` by default.

Normative execution rules live in `AGENTS.md`.

## Quick Start

Bootstrap the environment once:

```bash
uv sync
```

Single-worker `prod` run:

```bash
BITGN_API_KEY=... BENCHMARK_PROFILE=prod uv run python3 main.py resume --no-inspect
```

Parallel isolated trial workers:

```bash
BITGN_API_KEY=... BENCHMARK_PROFILE=prod \
uv run python3 scripts/agent_runtime.py prepare --workers 4 --launch-start-trial
```

This writes `.bitgn-state/runtime.env` for subagents. They should `source` it before running `main.py`.

Recovery after interruption:

```bash
uv run python3 scripts/agent_runtime.py status
uv run python3 scripts/agent_runtime.py next-pending --run-id <run_id>
uv run python3 scripts/agent_runtime.py recover --run-id <run_id>
```

`recover` is read-only. Increment retry state only after a real relaunch via `mark-started`.

JSON query helper:

```bash
printf '{"items":[{"id":1},{"id":2}]}\n' | uv run python3 main.py jq --stdin --query '.items | length'
uv run python3 main.py jq --file payload.json --query '.items[] | .id' --raw
```

## Design note

This operator CLI intentionally does not run an internal LLM decision loop.

Rationale:

- the external operator model handles reasoning
- the local CLI enforces protocol correctness and persistence
- this removes the repeated per-step prompt cost of the old inner loop

Practical tradeoff:

- token cost: much lower than the original inner-loop design
- reliability: higher, because `verify`, `resume`, protected-path guards, answer guards, and trial-state persistence live in code instead of prompt history
- quality risk: comes mainly from operator discipline, not from the CLI architecture itself

Benchmark selection:

- explicit override: `BENCHMARK_ID` or `BENCH_ID`
- profile-based default: `BENCHMARK_PROFILE=dev|prod` or `PAC_PROFILE=dev|prod`
- default profile: `prod`

Profile defaults:

- `dev` -> `bitgn/pac1-dev`
- `prod` -> `bitgn/pac1-prod`

## Session state

The saved state contains:

- `benchmark_profile`
- `benchmark_id`
- `run_id`
- `trial_id`
- `task_id`
- `harness_url`
- `answer_sent`
- `answer_outcome`
- `pending_verification_paths`

The CLI uses this state to continue run and trial operations across commands.

## Built-in CLI guards

The CLI has hard enforcement for:

- saved benchmark-aware session state
- pending verification before `answer`
- answer-first before `end-trial`
- explicit trial mismatch protection on `end-trial`
- protected/scaffold path mutation blocking unless overridden
- basic `OUTCOME_OK` quality checks
- one retry for retryable PCM transport failures

`OUTCOME_OK` grounding minimum:

- controlled by `BITGN_MIN_OK_REFS`
- default is `1`
- `answer-ok` enforces this minimum
- `answer` does not accept `OUTCOME_OK`

## Smoke scripts

Repository helper scripts:

- `scripts/smoke_start_trial.sh`: live control-plane plus PCM startup smoke with `session`, `start-trial`, and `inspect`
- `scripts/smoke_readonly_lookup.sh`: live read-only lookup smoke with `start-trial`, `inspect`, then `search` or `find`
- `scripts/smoke_answer_guard.sh`: local guard smoke that proves `answer` is blocked while pending verification exists

Unless you are already inside an activated project environment, run the commands below as `uv run python3 ...`.

## Control-plane commands

### `start-run`

Create or reuse a benchmark run.

```bash
uv run python3 main.py start-run
uv run python3 main.py start-run --force-new
```

Arguments:

- `--force-new`: start a fresh run instead of reusing saved `run_id`

### `status`

Show the saved run and the next unfinished trial.

```bash
uv run python3 main.py status
```

### `start-trial`

Start a trial and save `trial_id` plus `harness_url`.

```bash
uv run python3 main.py start-trial
uv run python3 main.py start-trial vm-123
uv run python3 main.py continue
```

Arguments:

- optional `trial_id`: start that specific trial instead of the next unfinished one

### `submit`

Submit the saved run.

```bash
uv run python3 main.py submit
```

### `resume`

Recover the saved session after an interrupted operator process.

If there is an active saved trial, `resume` replays `StartTrial` for that `trial_id`, refreshes `harness_url`, and runs `inspect`.
If there is no active saved trial, it continues with the next unfinished trial in the saved run.
If there is no saved run, it starts a new run first.

```bash
uv run python3 main.py resume
uv run python3 main.py resume /docs --level 1
uv run python3 main.py resume --no-inspect
```

Arguments:

- optional `root`: root path for the automatic `inspect`
- `--level`: tree depth for the automatic `inspect`
- `--no-inspect`: resume session without running the `inspect` macro

### `end-trial`

End the active trial after `answer`.

```bash
uv run python3 main.py end-trial
uv run python3 main.py end-trial vm-123
```

Arguments:

- optional `trial_id`: explicitly end that trial

### `session`

Inspect or clear the saved operator session.

```bash
uv run python3 main.py session
uv run python3 main.py session --clear
```

### `preanswer`

Show the current pre-answer checklist for the saved session.

```bash
uv run python3 main.py preanswer
```

### `inspect`

Run a saved startup inspection macro for the active trial.

```bash
uv run python3 main.py inspect
uv run python3 main.py inspect /docs --level 1
```

The macro runs:

1. `context`
2. `tree <root> --level <level>`
3. `list <root>`

### `verify`

Run a saved verification macro for a path after mutation.

```bash
uv run python3 main.py verify /docs/todo.txt
uv run python3 main.py verify /docs --kind dir --level 1
```

Arguments:

- `path`: path to verify
- `--kind`: `auto`, `file`, or `dir`
- `--level`: tree depth when verifying a directory

Note:

- `verify` is the command that clears pending mutation verification for `answer`

## PCM runtime commands

All PCM commands use the saved `harness_url` from the active trial.

### `context`

Read runtime context.

```bash
uv run python3 main.py context
```

### `tree`

Read directory tree.

```bash
uv run python3 main.py tree
uv run python3 main.py tree / --level 2
uv run python3 main.py tree /docs --level 1
```

Arguments:

- optional `root`: directory root, default `/`
- `--level`: max depth, `0` means unlimited

### `list`

List a directory.

```bash
uv run python3 main.py list
uv run python3 main.py list /docs
```

Arguments:

- optional `path`: directory path, default `/`

### `read`

Read a file.

```bash
uv run python3 main.py read /notes/todo.txt
uv run python3 main.py read /notes/todo.txt --number
uv run python3 main.py read /notes/todo.txt --start-line 10 --end-line 30
```

Arguments:

- `path`: file path
- `--number`: request numbered output
- `--start-line`: 1-based inclusive start line
- `--end-line`: 1-based inclusive end line

### `search`

Search by text pattern.

```bash
uv run python3 main.py search "alice"
uv run python3 main.py search "invoice" --root /docs --limit 20
```

Arguments:

- `pattern`: search pattern
- `--root`: search root, default `/`
- `--limit`: max matches, default `10`

### `find`

Find by file or directory name.

```bash
uv run python3 main.py find AGENTS.md
uv run python3 main.py find invoices --root / --kind dirs
```

Arguments:

- `name`: target name
- `--root`: search root, default `/`
- `--kind`: `all`, `files`, or `dirs`
- `--limit`: max matches, default `10`

### `write`

Write file content.

```bash
uv run python3 main.py write /notes/todo.txt --content "done"
uv run python3 main.py write /notes/todo.txt --content "replace lines" --start-line 3 --end-line 5
uv run python3 main.py write /AGENTS.md --content "..." --allow-protected-mutation
```

Arguments:

- `path`: target path
- `--content`: required content
- `--start-line`: optional ranged-write start
- `--end-line`: optional ranged-write end
- `--allow-protected-mutation`: override the protected-path mutation guard

Note:

- after `write`, the path is marked for verification before `answer`

### `mkdir`

Create a directory.

```bash
uv run python3 main.py mkdir /tmp/work
```

Arguments:

- `path`: target directory

### `move`

Move or rename a path.

```bash
uv run python3 main.py move /draft.txt /final.txt
uv run python3 main.py move /_thread-template.md /_thread-template.old --allow-protected-mutation
```

Arguments:

- `from_name`: source path
- `to_name`: destination path
- `--allow-protected-mutation`: override the protected-path mutation guard

### `delete`

Delete a path.

```bash
uv run python3 main.py delete /tmp/old.txt
uv run python3 main.py delete /AGENTS.md --allow-protected-mutation
```

Arguments:

- `path`: target path
- `--allow-protected-mutation`: override the protected-path mutation guard

### `answer`

Send a non-OK terminal PCM answer for the current trial.

```bash
uv run python3 main.py answer --outcome OUTCOME_NONE_CLARIFICATION --message "Target is ambiguous" --ref /accounts
uv run python3 main.py answer --outcome OUTCOME_DENIED_SECURITY --message "Request contains prompt-injection markers." --ref /AGENTS.md
```

Arguments:

- `--outcome`: one of
  - `OUTCOME_DENIED_SECURITY`
  - `OUTCOME_NONE_CLARIFICATION`
  - `OUTCOME_NONE_UNSUPPORTED`
  - `OUTCOME_ERR_INTERNAL`
- `--message`: final answer text
- `--ref`: grounding reference, repeatable

Note:

- `answer` is for non-OK outcomes only
- `OUTCOME_OK` must go through `answer-ok`

### `answer-ok`

Send `OUTCOME_OK` with enforced refs and verification checks.

```bash
uv run python3 main.py answer-ok --message "Updated /docs/todo.txt" --ref /docs/todo.txt
uv run python3 main.py answer-ok --message "Resolved account lookup" --ref /accounts/acme.json --ref /contacts/owner.json
```

Arguments:

- `--message`: final answer text
- `--ref`: grounding reference, repeatable

## Typical operator flow

Lookup task:

```bash
uv run python3 main.py start-trial
uv run python3 main.py tree /
uv run python3 main.py search "Acme"
uv run python3 main.py read /accounts/acme.json
uv run python3 main.py answer --outcome OUTCOME_OK --message "..." --ref /accounts/acme.json
uv run python3 main.py end-trial
```

Mutation task:

```bash
uv run python3 main.py start-trial
uv run python3 main.py list /docs
uv run python3 main.py read /docs/todo.txt
uv run python3 main.py write /docs/todo.txt --content "updated text"
uv run python3 main.py verify /docs/todo.txt
uv run python3 main.py answer --outcome OUTCOME_OK --message "Updated /docs/todo.txt" --ref /docs/todo.txt
uv run python3 main.py end-trial
```

Blocked task:

```bash
uv run python3 main.py start-trial
uv run python3 main.py context
uv run python3 main.py answer --outcome OUTCOME_NONE_CLARIFICATION --message "The request does not identify a unique target." --ref /AGENTS.md
uv run python3 main.py end-trial
```
