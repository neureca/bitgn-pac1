from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from importlib import import_module
from pathlib import Path, PurePosixPath
from typing import Any

from config import Settings, load_settings

CLI_RED = "\x1B[31m"
CLI_GREEN = "\x1B[32m"
CLI_CLR = "\x1B[0m"
CLI_BLUE = "\x1B[34m"
CLI_YELLOW = "\x1B[33m"

OUTCOME_NAMES = {
    "OUTCOME_OK",
    "OUTCOME_DENIED_SECURITY",
    "OUTCOME_NONE_CLARIFICATION",
    "OUTCOME_NONE_UNSUPPORTED",
    "OUTCOME_ERR_INTERNAL",
}

NON_OK_OUTCOME_NAMES = sorted(name for name in OUTCOME_NAMES if name != "OUTCOME_OK")


@dataclass
class OperatorState:
    benchmark_profile: str = ""
    benchmark_id: str = ""
    run_id: str = ""
    trial_id: str = ""
    task_id: str = ""
    harness_url: str = ""
    answer_sent: bool = False
    answer_outcome: str = ""
    pending_verification_paths: list[str] = field(default_factory=list)


def _normalize_pcm_path(path: str) -> str:
    raw = (path or "").strip()
    if not raw or raw == "/":
        return "/"
    normalized = PurePosixPath("/" + raw.lstrip("/")).as_posix()
    return normalized or "/"


def _command_paths(command: str, args: argparse.Namespace) -> list[str]:
    if command == "tree":
        return [_normalize_pcm_path(args.root)]
    if command == "find":
        return [_normalize_pcm_path(args.root)]
    if command == "search":
        return [_normalize_pcm_path(args.root)]
    if command == "list":
        return [_normalize_pcm_path(args.path)]
    if command == "read":
        return [_normalize_pcm_path(args.path)]
    if command == "write":
        return [_normalize_pcm_path(args.path)]
    if command == "delete":
        return [_normalize_pcm_path(args.path)]
    if command == "mkdir":
        return [_normalize_pcm_path(args.path)]
    if command == "move":
        return [_normalize_pcm_path(args.from_name), _normalize_pcm_path(args.to_name)]
    return []


def _is_mutating_command(command: str) -> bool:
    return command in {"write", "delete", "mkdir", "move"}


def _is_verification_command(command: str) -> bool:
    return command in {"tree", "list", "read", "search", "find"}


def _looks_like_protected_path(path: str) -> bool:
    normalized = _normalize_pcm_path(path)
    name = PurePosixPath(normalized).name.lower()
    if not name:
        return False
    if name in {"agents.md", "cli.md", "readme", "readme.md"}:
        return True
    if name.startswith("_"):
        return True
    if "template" in name:
        return True
    return False


def _allow_protected_mutation(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "allow_protected_mutation", False))


def _overlap(left: str, right: str) -> bool:
    left_norm = _normalize_pcm_path(left)
    right_norm = _normalize_pcm_path(right)
    if left_norm == right_norm:
        return True
    if right_norm.startswith(f"{left_norm.rstrip('/')}/"):
        return True
    if left_norm.startswith(f"{right_norm.rstrip('/')}/"):
        return True
    return False


def _load_state(state_path: Path) -> OperatorState:
    if not state_path.exists():
        return OperatorState()
    try:
        payload = json.loads(state_path.read_text())
    except json.JSONDecodeError:
        return OperatorState()
    if isinstance(payload, dict) and "run_id" in payload and "trial_id" not in payload:
        return OperatorState(run_id=str(payload.get("run_id") or ""))
    if not isinstance(payload, dict):
        return OperatorState()
    pending = payload.get("pending_verification_paths", [])
    if not isinstance(pending, list):
        pending = []
    return OperatorState(
        benchmark_profile=str(payload.get("benchmark_profile") or ""),
        benchmark_id=str(payload.get("benchmark_id") or ""),
        run_id=str(payload.get("run_id") or ""),
        trial_id=str(payload.get("trial_id") or ""),
        task_id=str(payload.get("task_id") or ""),
        harness_url=str(payload.get("harness_url") or ""),
        answer_sent=bool(payload.get("answer_sent", False)),
        answer_outcome=str(payload.get("answer_outcome") or ""),
        pending_verification_paths=[_normalize_pcm_path(str(item)) for item in pending if str(item).strip()],
    )


def _save_state(state_path: Path, state: OperatorState) -> None:
    state_path.write_text(json.dumps(asdict(state), indent=2) + "\n")


def _clear_run_context(state: OperatorState) -> None:
    state.run_id = ""
    _clear_trial_context(state)


def _clear_trial_context(state: OperatorState) -> None:
    state.trial_id = ""
    state.task_id = ""
    state.harness_url = ""
    state.answer_sent = False
    state.answer_outcome = ""
    state.pending_verification_paths = []


def _print_run_summary(run: Any, run_state_name: Any) -> None:
    print(f"Run: {run.run_id} [{run_state_name(run.state).removeprefix('RUN_STATE_')}]")
    if run.HasField("stats"):
        print(
            "Trials:"
            f" new={run.stats.new_count}"
            f" running={run.stats.running_count}"
            f" done={run.stats.done_count}"
            f" error={run.stats.error_count}"
        )


def _print_benchmark_target(settings: Settings) -> None:
    print(f"Benchmark: {settings.benchmark_id} [profile={settings.benchmark_profile}]")


def _sync_state_benchmark(state: OperatorState, settings: Settings) -> None:
    state.benchmark_profile = settings.benchmark_profile
    state.benchmark_id = settings.benchmark_id


def _reconcile_state_with_settings(settings: Settings, state: OperatorState, state_path: Path) -> None:
    current_profile = state.benchmark_profile.strip()
    current_benchmark = state.benchmark_id.strip()
    has_saved_context = bool(state.run_id or state.trial_id or state.harness_url)

    if not has_saved_context:
        if current_profile != settings.benchmark_profile or current_benchmark != settings.benchmark_id:
            _sync_state_benchmark(state, settings)
            _save_state(state_path, state)
        return

    if not current_profile or not current_benchmark:
        print("Saved operator session has no benchmark identity. Clearing stale run/trial context.")
        _clear_run_context(state)
        _sync_state_benchmark(state, settings)
        _save_state(state_path, state)
        return

    if current_profile != settings.benchmark_profile or current_benchmark != settings.benchmark_id:
        print(
            "Saved operator session belongs to a different benchmark "
            f"({current_benchmark} [{current_profile}]). Clearing it before continuing."
        )
        _clear_run_context(state)
        _sync_state_benchmark(state, settings)
        _save_state(state_path, state)


def _print_trial_checkpoint(started: Any) -> None:
    print(f"{'=' * 30} Trial: {started.task_id} ({started.trial_id}) {'=' * 30}")
    print(f"Task restatement: complete task `{started.task_id}` using verified PCM actions only.")
    print(f"Harness URL: {started.harness_url}")
    print(f"{CLI_BLUE}{started.instruction}{CLI_CLR}")
    print("-" * 80)
    print("Required flow: StartTrial -> inspect -> narrow action -> verify -> answer -> end_trial")
    print("Use the harness URL above as authoritative for PCM operations.")


def _render_command(command: str, body: str) -> str:
    return f"{command}\n{body}"


def _format_tree_entry(entry: Any, prefix: str = "", is_last: bool = True) -> list[str]:
    branch = "└── " if is_last else "├── "
    lines = [f"{prefix}{branch}{entry.name}"]
    child_prefix = f"{prefix}{'    ' if is_last else '│   '}"
    children = list(entry.children)
    for index, child in enumerate(children):
        lines.extend(_format_tree_entry(child, child_prefix, index == len(children) - 1))
    return lines


def _format_pcm_result(command: str, args: argparse.Namespace, result: Any) -> str:
    if result is None:
        return "{}"

    if command == "tree":
        root = result.root
        if not root.name:
            body = "."
        else:
            lines = [root.name]
            children = list(root.children)
            for index, child in enumerate(children):
                lines.extend(_format_tree_entry(child, is_last=index == len(children) - 1))
            body = "\n".join(lines)
        level_arg = f" -L {args.level}" if args.level > 0 else ""
        return _render_command(f"tree{level_arg} {_normalize_pcm_path(args.root)}", body)

    if command == "list":
        if not result.entries:
            body = "."
        else:
            body = "\n".join(f"{entry.name}/" if entry.is_dir else entry.name for entry in result.entries)
        return _render_command(f"ls {_normalize_pcm_path(args.path)}", body)

    if command == "read":
        normalized = _normalize_pcm_path(args.path)
        if args.start_line > 0 or args.end_line > 0:
            start = args.start_line if args.start_line > 0 else 1
            end = args.end_line if args.end_line > 0 else "$"
            shell_cmd = f"sed -n '{start},{end}p' {normalized}"
        elif args.number:
            shell_cmd = f"cat -n {normalized}"
        else:
            shell_cmd = f"cat {normalized}"
        return _render_command(shell_cmd, result.content)

    if command == "search":
        body = "\n".join(f"{match.path}:{match.line}:{match.line_text}" for match in result.matches)
        return _render_command(
            f"rg -n --no-heading -e {json.dumps(args.pattern)} {_normalize_pcm_path(args.root)}",
            body,
        )

    if command in {"context", "find", "write", "mkdir", "move", "delete", "answer"}:
        message_to_dict = getattr(import_module("google.protobuf.json_format"), "MessageToDict")
        return json.dumps(message_to_dict(result), indent=2)

    return json.dumps(result, indent=2, default=str)


def _require_api_key(settings: Settings) -> int | None:
    if settings.bitgn_api_key:
        return None
    print("Missing BITGN_API_KEY")
    print("Set BITGN_API_KEY before running the operator commands.")
    return 2


def _harness_parts(settings: Settings) -> tuple[Any, Any, Any]:
    connect_mod = import_module("bitgn.harness_connect")
    pb2_mod = import_module("bitgn.harness_pb2")
    errors_mod = import_module("connectrpc.errors")
    client = connect_mod.HarnessServiceClientSync(settings.bitgn_host)
    return client, pb2_mod, errors_mod.ConnectError


def _pcm_parts(harness_url: str) -> tuple[Any, Any, Any]:
    connect_mod = import_module("bitgn.vm.pcm_connect")
    pb2_mod = import_module("bitgn.vm.pcm_pb2")
    errors_mod = import_module("connectrpc.errors")
    client = connect_mod.PcmRuntimeClientSync(harness_url)
    return client, pb2_mod, errors_mod.ConnectError


def _pick_next_trial(run: Any, trial_state_enum: Any) -> Any | None:
    for trial in run.trials:
        if trial.state != trial_state_enum.TRIAL_STATE_DONE:
            return trial
    return None


def _save_state_after_trial_start(state: OperatorState, started: Any) -> None:
    state.trial_id = started.trial_id
    state.task_id = started.task_id
    state.harness_url = started.harness_url
    state.answer_sent = False
    state.answer_outcome = ""
    state.pending_verification_paths = []


def _ensure_active_trial(state: OperatorState) -> int | None:
    if state.trial_id and state.harness_url:
        return None
    print("No active trial in saved state.")
    print("Start a trial first with `python3 main.py start-trial`.")
    return 2


def _record_post_command_state(state: OperatorState, command: str, args: argparse.Namespace) -> None:
    paths = _command_paths(command, args)
    if _is_mutating_command(command):
        current = {_normalize_pcm_path(path) for path in state.pending_verification_paths}
        current.update(paths)
        state.pending_verification_paths = sorted(current)


def _clear_pending_verification(state: OperatorState, path: str) -> None:
    normalized = _normalize_pcm_path(path)
    state.pending_verification_paths = [
        item for item in state.pending_verification_paths if not _overlap(item, normalized)
    ]


def _guard_protected_mutation(command: str, args: argparse.Namespace) -> str | None:
    if not _is_mutating_command(command):
        return None
    if _allow_protected_mutation(args):
        return None
    protected = [path for path in _command_paths(command, args) if _looks_like_protected_path(path)]
    if not protected:
        return None
    joined = ", ".join(sorted({_normalize_pcm_path(path) for path in protected}))
    return (
        f"Refusing to {command} protected or scaffold-like path(s): {joined}. "
        "Use --allow-protected-mutation only when the task explicitly requires it."
    )


def _is_retryable_transport_error(exc: Any) -> bool:
    code = str(getattr(exc, "code", "") or "").upper()
    message = str(getattr(exc, "message", "") or "")
    normalized = message.lower()
    if "UNAVAILABLE" in code:
        return True
    return any(
        marker in normalized
        for marker in (
            "unavailable",
            "tunnel",
            "connection refused",
            "connection reset",
            "eof",
            "broken pipe",
            "deadline exceeded",
            "timeout",
            "temporarily unavailable",
        )
    )


def _answer_preflight_error(args: argparse.Namespace) -> str | None:
    message = (args.message or "").strip()
    if not message:
        return "Refusing to send an empty answer message."
    return None


def _validate_answer_request(settings: Settings, state: OperatorState, args: argparse.Namespace) -> str | None:
    basic_error = _answer_preflight_error(args)
    if basic_error is not None:
        return basic_error

    if args.outcome == "OUTCOME_OK":
        return "Use `answer-ok` for OUTCOME_OK. The generic `answer` command is reserved for non-OK outcomes."

    refs = [item.strip() for item in getattr(args, "ref", []) if item.strip()]
    if state.pending_verification_paths:
        pending = ", ".join(state.pending_verification_paths)
        return f"Verification required before answer. Confirm final state for: {pending}"

    return None


def _validate_answer_ok_request(settings: Settings, state: OperatorState, args: argparse.Namespace) -> str | None:
    message = (args.message or "").strip()
    if not message:
        return "Refusing to send an empty OUTCOME_OK answer message."

    lowered = " ".join(message.lower().split())
    generic_messages = {
        "done",
        "completed",
        "task completed",
        "completed task",
        "success",
        "ok",
    }
    if lowered in generic_messages:
        return "Refusing generic OUTCOME_OK answer text. Summarize the concrete verified result."

    refs = [item.strip() for item in getattr(args, "ref", []) if item.strip()]
    if len(refs) < settings.min_ok_refs:
        return (
            f"Refusing OUTCOME_OK with only {len(refs)} ref(s). "
            f"Current minimum is {settings.min_ok_refs}. Add more grounding refs or lower BITGN_MIN_OK_REFS."
        )

    if state.pending_verification_paths:
        pending = ", ".join(state.pending_verification_paths)
        return f"Verification required before answer. Confirm final state for: {pending}"

    return None


def _refresh_trial_harness(settings: Settings, state: OperatorState) -> bool:
    if not state.trial_id:
        return False
    client, pb2_mod, connect_error = _harness_parts(settings)
    try:
        started = client.start_trial(pb2_mod.StartTrialRequest(trial_id=state.trial_id))
    except connect_error as exc:
        print(f"{CLI_RED}RETRY FAILED {exc.code}: {exc.message}{CLI_CLR}")
        return False
    state.harness_url = started.harness_url
    print(f"{CLI_YELLOW}RETRY{CLI_CLR}: refreshed harness_url via StartTrial for {state.trial_id}")
    return True


def _pcm_call_with_retry(
    settings: Settings,
    state: OperatorState,
    invoke: Any,
) -> Any:
    attempts = 2
    last_exc: Exception | None = None
    for attempt in range(attempts):
        client, pb2_mod, connect_error = _pcm_parts(state.harness_url)
        try:
            return invoke(client, pb2_mod)
        except connect_error as exc:
            last_exc = exc
            if attempt + 1 >= attempts or not _is_retryable_transport_error(exc):
                raise
            print(f"{CLI_YELLOW}RETRY{CLI_CLR}: {exc.code}: {exc.message}")
            if not _refresh_trial_harness(settings, state):
                raise
            _save_state(Path(settings.state_path), state)
            time.sleep(0.2)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("PCM retry loop exited without result")


def _path_kind_guess(path: str) -> str:
    name = PurePosixPath(_normalize_pcm_path(path)).name
    if not name:
        return "dir"
    if "." in name:
        return "file"
    return "unknown"


def _append_journal_entry(settings: Settings, state: OperatorState, result: Any) -> None:
    payload = {
        "ts": int(time.time()),
        "benchmark_id": settings.benchmark_id,
        "benchmark_profile": settings.benchmark_profile,
        "run_id": state.run_id,
        "task_id": state.task_id,
        "trial_id": state.trial_id,
        "answer_outcome": state.answer_outcome,
        "score": result.score,
        "trial_state": int(result.state),
        "score_detail": list(result.score_detail),
    }
    journal_path = Path(settings.journal_path)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    with journal_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _handle_preanswer(settings: Settings, state: OperatorState) -> int:
    print("Pre-answer checklist")
    print(f"- benchmark: {settings.benchmark_id} [profile={settings.benchmark_profile}]")
    print(f"- active trial: {state.task_id or '<none>'} ({state.trial_id or '<none>'})")
    print(f"- answer_sent: {str(state.answer_sent).lower()}")
    print(f"- minimum OUTCOME_OK refs: {settings.min_ok_refs}")
    if state.pending_verification_paths:
        print("- pending verification paths:")
        for path in state.pending_verification_paths:
            print(f"  {path}")
    else:
        print("- pending verification paths: none")
    print("- for OUTCOME_OK: refs should cover identity plus final value path")
    print("- for ambiguous or unsafe tasks: use the appropriate non-OK outcome")
    return 0


def _handle_status(args: argparse.Namespace, settings: Settings, state: OperatorState) -> int:
    if not state.run_id:
        print("No saved run context.")
        _print_benchmark_target(settings)
        return 0
    client, pb2_mod, connect_error = _harness_parts(settings)
    try:
        run = client.get_run(pb2_mod.GetRunRequest(run_id=state.run_id))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    _print_benchmark_target(settings)
    _print_run_summary(run, pb2_mod.RunState.Name)
    next_trial = _pick_next_trial(run, pb2_mod.TrialState)
    if next_trial is None:
        print(f"{CLI_GREEN}All trials are complete.{CLI_CLR}")
        return 0
    print(
        "Next unfinished trial:"
        f" {next_trial.task_id} ({next_trial.trial_id})"
        f" state={pb2_mod.TrialState.Name(next_trial.state).removeprefix('TRIAL_STATE_')}"
    )
    if state.trial_id:
        print(f"Active saved trial: {state.task_id or '<unknown>'} ({state.trial_id})")
    return 0


def _handle_start_run(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    client, pb2_mod, connect_error = _harness_parts(settings)
    try:
        if state.run_id and not args.force_new:
            run = client.get_run(pb2_mod.GetRunRequest(run_id=state.run_id))
            _print_benchmark_target(settings)
            print(f"Reusing run: {run.run_id}")
            _print_run_summary(run, pb2_mod.RunState.Name)
            return 0
        run = client.start_run(
            pb2_mod.StartRunRequest(
                name=settings.run_name,
                benchmark_id=settings.benchmark_id,
                api_key=settings.bitgn_api_key,
            )
        )
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    _clear_run_context(state)
    _sync_state_benchmark(state, settings)
    state.run_id = run.run_id
    _save_state(state_path, state)
    _print_benchmark_target(settings)
    print(f"{CLI_GREEN}Started run{CLI_CLR}: {run.run_id}")
    return 0


def _handle_start_trial(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    client, pb2_mod, connect_error = _harness_parts(settings)
    try:
        trial_id = args.trial_id
        if not trial_id:
            if not state.run_id:
                run = client.start_run(
                    pb2_mod.StartRunRequest(
                        name=settings.run_name,
                        benchmark_id=settings.benchmark_id,
                        api_key=settings.bitgn_api_key,
                    )
                )
                state.run_id = run.run_id
                _print_benchmark_target(settings)
                print(f"{CLI_GREEN}Started run{CLI_CLR}: {run.run_id}")
            run = client.get_run(pb2_mod.GetRunRequest(run_id=state.run_id))
            _print_benchmark_target(settings)
            _print_run_summary(run, pb2_mod.RunState.Name)
            next_trial = _pick_next_trial(run, pb2_mod.TrialState)
            if next_trial is None:
                print(f"{CLI_GREEN}Run has no unfinished trials.{CLI_CLR}")
                _save_state(state_path, state)
                return 0
            trial_id = next_trial.trial_id

        started = client.start_trial(pb2_mod.StartTrialRequest(trial_id=trial_id))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1

    _sync_state_benchmark(state, settings)
    _save_state_after_trial_start(state, started)
    _save_state(state_path, state)
    _print_trial_checkpoint(started)
    return 0


def _resume_inspect(settings: Settings, state: OperatorState, state_path: Path, root: str, level: int) -> int:
    inspect_args = argparse.Namespace(root=root, level=level)
    return _handle_inspect(inspect_args, settings, state, state_path)


def _handle_resume(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    client, pb2_mod, connect_error = _harness_parts(settings)

    if state.trial_id:
        if state.answer_sent:
            print(
                "Saved session already has an answered trial. "
                "Finish it with `python3 main.py end-trial` or clear the session."
            )
            return 2
        try:
            started = client.start_trial(pb2_mod.StartTrialRequest(trial_id=state.trial_id))
        except connect_error as exc:
            print(f"{exc.code}: {exc.message}")
            return 1
        _sync_state_benchmark(state, settings)
        _save_state_after_trial_start(state, started)
        _save_state(state_path, state)
        print("Resumed saved active trial.")
        _print_trial_checkpoint(started)
        if args.no_inspect:
            return 0
        return _resume_inspect(settings, state, state_path, args.root, args.level)

    if not state.run_id:
        try:
            run = client.start_run(
                pb2_mod.StartRunRequest(
                    name=settings.run_name,
                    benchmark_id=settings.benchmark_id,
                    api_key=settings.bitgn_api_key,
                )
            )
        except connect_error as exc:
            print(f"{exc.code}: {exc.message}")
            return 1
        _clear_run_context(state)
        _sync_state_benchmark(state, settings)
        state.run_id = run.run_id
        _save_state(state_path, state)
        _print_benchmark_target(settings)
        print(f"{CLI_GREEN}Started run{CLI_CLR}: {run.run_id}")

    try:
        run = client.get_run(pb2_mod.GetRunRequest(run_id=state.run_id))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1

    _print_benchmark_target(settings)
    _print_run_summary(run, pb2_mod.RunState.Name)
    next_trial = _pick_next_trial(run, pb2_mod.TrialState)
    if next_trial is None:
        print(f"{CLI_GREEN}Run has no unfinished trials.{CLI_CLR}")
        return 0

    try:
        started = client.start_trial(pb2_mod.StartTrialRequest(trial_id=next_trial.trial_id))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1

    _sync_state_benchmark(state, settings)
    _save_state_after_trial_start(state, started)
    _save_state(state_path, state)
    print("Resumed with the next unfinished trial.")
    _print_trial_checkpoint(started)
    if args.no_inspect:
        return 0
    return _resume_inspect(settings, state, state_path, args.root, args.level)


def _handle_submit(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    if not state.run_id:
        print("No saved run context.")
        return 2
    client, pb2_mod, connect_error = _harness_parts(settings)
    try:
        result = client.submit_run(pb2_mod.SubmitRunRequest(run_id=state.run_id, force=True))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1
    print(f"Submitted run {result.run_id} [{pb2_mod.RunState.Name(result.state).removeprefix('RUN_STATE_')}]")
    _clear_run_context(state)
    _sync_state_benchmark(state, settings)
    _save_state(state_path, state)
    return 0


def _handle_session(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    if args.clear:
        _clear_run_context(state)
        _sync_state_benchmark(state, settings)
        _save_state(state_path, state)
        print("Cleared saved operator session.")
        return 0
    print(json.dumps(asdict(state), indent=2))
    return 0


def _handle_answer_ok(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    validation_error = _validate_answer_ok_request(settings, state, args)
    if validation_error is not None:
        print(validation_error)
        return 2
    answer_args = argparse.Namespace(
        message=args.message,
        outcome="OUTCOME_OK",
        ref=args.ref,
        allow_outcome_ok=True,
    )
    return _run_pcm_command("answer", answer_args, settings, state, state_path)


def _run_pcm_command(command: str, args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    missing_trial = _ensure_active_trial(state)
    if missing_trial is not None:
        return missing_trial

    mutation_guard = _guard_protected_mutation(command, args)
    if mutation_guard is not None:
        print(mutation_guard)
        return 2

    if command == "answer" and not getattr(args, "allow_outcome_ok", False):
        answer_preflight = _validate_answer_request(settings, state, args)
        if answer_preflight is not None:
            print(answer_preflight)
            return 2

    try:
        def invoke(client: Any, pb2_mod: Any) -> Any:
            if command == "context":
                return client.context(pb2_mod.ContextRequest())
            if command == "tree":
                return client.tree(
                    pb2_mod.TreeRequest(root=_normalize_pcm_path(args.root), level=args.level)
                )
            if command == "list":
                return client.list(pb2_mod.ListRequest(name=_normalize_pcm_path(args.path)))
            if command == "read":
                return client.read(
                    pb2_mod.ReadRequest(
                        path=_normalize_pcm_path(args.path),
                        number=args.number,
                        start_line=args.start_line,
                        end_line=args.end_line,
                    )
                )
            if command == "search":
                return client.search(
                    pb2_mod.SearchRequest(
                        root=_normalize_pcm_path(args.root),
                        pattern=args.pattern,
                        limit=args.limit,
                    )
                )
            if command == "find":
                return client.find(
                    pb2_mod.FindRequest(
                        root=_normalize_pcm_path(args.root),
                        name=args.name,
                        type={"all": 0, "files": 1, "dirs": 2}[args.kind],
                        limit=args.limit,
                    )
                )
            if command == "write":
                return client.write(
                    pb2_mod.WriteRequest(
                        path=_normalize_pcm_path(args.path),
                        content=args.content,
                        start_line=args.start_line,
                        end_line=args.end_line,
                    )
                )
            if command == "mkdir":
                return client.mk_dir(pb2_mod.MkDirRequest(path=_normalize_pcm_path(args.path)))
            if command == "move":
                return client.move(
                    pb2_mod.MoveRequest(
                        from_name=_normalize_pcm_path(args.from_name),
                        to_name=_normalize_pcm_path(args.to_name),
                    )
                )
            if command == "delete":
                return client.delete(pb2_mod.DeleteRequest(path=_normalize_pcm_path(args.path)))
            if command == "answer":
                outcome_value = getattr(pb2_mod.Outcome, args.outcome)
                return client.answer(
                    pb2_mod.AnswerRequest(
                        message=args.message,
                        outcome=outcome_value,
                        refs=args.ref,
                    )
                )
            raise ValueError(f"Unsupported PCM command: {command}")

        result = _pcm_call_with_retry(settings, state, invoke)
        if command == "answer":
            state.answer_sent = True
            state.answer_outcome = args.outcome
    except Exception as exc:
        if hasattr(exc, "code") and hasattr(exc, "message"):
            print(f"{exc.code}: {exc.message}")
            return 1
        print(str(exc))
        return 1

    print(_format_pcm_result(command, args, result))
    _record_post_command_state(state, command, args)
    _save_state(state_path, state)
    return 0


def _execute_pcm(command: str, args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    return _run_pcm_command(command, args, settings, state, state_path)


def _handle_inspect(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    commands = [
        ("context", argparse.Namespace()),
        ("tree", argparse.Namespace(root=args.root, level=args.level)),
        ("list", argparse.Namespace(path=args.root)),
    ]
    for command, command_args in commands:
        exit_code = _run_pcm_command(command, command_args, settings, state, state_path)
        if exit_code != 0:
            return exit_code
    return 0


def _handle_verify(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    normalized = _normalize_pcm_path(args.path)
    kind = args.kind
    if kind == "auto":
        kind = _path_kind_guess(normalized)
    commands: list[tuple[str, argparse.Namespace]]
    if kind == "file":
        commands = [("read", argparse.Namespace(path=normalized, number=False, start_line=0, end_line=0))]
    elif kind == "dir":
        commands = [
            ("list", argparse.Namespace(path=normalized)),
            ("tree", argparse.Namespace(root=normalized, level=args.level)),
        ]
    else:
        commands = [
            ("list", argparse.Namespace(path=normalized)),
            ("read", argparse.Namespace(path=normalized, number=False, start_line=0, end_line=0)),
        ]
    for command, command_args in commands:
        exit_code = _run_pcm_command(command, command_args, settings, state, state_path)
        if exit_code != 0:
            return exit_code
    _clear_pending_verification(state, normalized)
    _save_state(state_path, state)
    return 0


def _handle_end_trial(args: argparse.Namespace, settings: Settings, state: OperatorState, state_path: Path) -> int:
    trial_id = args.trial_id or state.trial_id
    if not trial_id:
        print("No active or explicit trial id.")
        return 2
    if args.trial_id and args.trial_id != state.trial_id:
        print(
            "Refusing to end a trial different from the active saved session. "
            "Start that trial in this session first or clear the current session."
        )
        return 2
    if not state.answer_sent and not args.allow_unanswered:
        print("Refusing to end trial before answer.")
        print("Send `python3 main.py answer ...` first or use `python3 main.py end-trial --allow-unanswered`.")
        return 2

    client, pb2_mod, connect_error = _harness_parts(settings)
    try:
        result = client.end_trial(pb2_mod.EndTrialRequest(trial_id=trial_id))
    except connect_error as exc:
        print(f"{exc.code}: {exc.message}")
        return 1

    print(f"{CLI_GREEN}Trial ended [{pb2_mod.TrialState.Name(result.state).removeprefix('TRIAL_STATE_')}]{CLI_CLR}")

    _append_journal_entry(settings, state, result)

    if state.trial_id == trial_id:
        _clear_trial_context(state)
        _save_state(state_path, state)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stateful BitGN PAC1 operator CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status", help="Show saved run status")
    status_parser.set_defaults(handler="status")

    start_run_parser = subparsers.add_parser("start-run", help="Create or reuse a run")
    start_run_parser.add_argument("--force-new", action="store_true", help="Start a fresh run instead of reusing saved run_id")
    start_run_parser.set_defaults(handler="start-run")

    start_trial_parser = subparsers.add_parser("start-trial", help="Start a trial and save harness state")
    start_trial_parser.add_argument("trial_id", nargs="?", help="Explicit trial id; defaults to next unfinished trial")
    start_trial_parser.set_defaults(handler="start-trial")

    continue_parser = subparsers.add_parser("continue", help="Alias for start-trial")
    continue_parser.add_argument("trial_id", nargs="?", help="Explicit trial id; defaults to next unfinished trial")
    continue_parser.set_defaults(handler="start-trial")

    resume_parser = subparsers.add_parser("resume", help="Recover saved session or continue with the next unfinished trial")
    resume_parser.add_argument("root", nargs="?", default="/", help="Root path for automatic inspect after resume")
    resume_parser.add_argument("--level", type=int, default=2, help="Tree depth for automatic inspect after resume")
    resume_parser.add_argument("--no-inspect", action="store_true", help="Resume session without running the inspect macro")
    resume_parser.set_defaults(handler="resume")

    submit_parser = subparsers.add_parser("submit", help="Submit the saved run")
    submit_parser.set_defaults(handler="submit")

    session_parser = subparsers.add_parser("session", help="Show or clear saved operator state")
    session_parser.add_argument("--clear", action="store_true", help="Clear saved run and trial state")
    session_parser.set_defaults(handler="session")

    preanswer_parser = subparsers.add_parser("preanswer", help="Show the pre-answer checklist for the current session")
    preanswer_parser.set_defaults(handler="preanswer")

    inspect_parser = subparsers.add_parser("inspect", help="Macro: context + tree + list for the active trial")
    inspect_parser.add_argument("root", nargs="?", default="/", help="Root path for tree/list inspection")
    inspect_parser.add_argument("--level", type=int, default=2, help="Tree depth for the inspect macro")
    inspect_parser.set_defaults(handler="inspect")

    verify_parser = subparsers.add_parser("verify", help="Macro: verify a path after mutation")
    verify_parser.add_argument("path", help="Path to verify")
    verify_parser.add_argument("--kind", choices=["auto", "file", "dir"], default="auto", help="Verification mode")
    verify_parser.add_argument("--level", type=int, default=2, help="Tree depth when verifying a directory")
    verify_parser.set_defaults(handler="verify")

    end_trial_parser = subparsers.add_parser("end-trial", help="End the active trial after answer")
    end_trial_parser.add_argument("trial_id", nargs="?", help="Explicit trial id; defaults to saved active trial")
    end_trial_parser.add_argument(
        "--allow-unanswered",
        action="store_true",
        help="Allow end_trial without a prior answer when you intentionally stop on a diagnosed blocker",
    )
    end_trial_parser.set_defaults(handler="end-trial")

    context_parser = subparsers.add_parser("context", help="PCM context")
    context_parser.set_defaults(handler="pcm")

    tree_parser = subparsers.add_parser("tree", help="PCM tree")
    tree_parser.add_argument("root", nargs="?", default="/")
    tree_parser.add_argument("--level", type=int, default=2)
    tree_parser.set_defaults(handler="pcm")

    list_parser = subparsers.add_parser("list", help="PCM list")
    list_parser.add_argument("path", nargs="?", default="/")
    list_parser.set_defaults(handler="pcm")

    read_parser = subparsers.add_parser("read", help="PCM read")
    read_parser.add_argument("path")
    read_parser.add_argument("--number", action="store_true")
    read_parser.add_argument("--start-line", type=int, default=0)
    read_parser.add_argument("--end-line", type=int, default=0)
    read_parser.set_defaults(handler="pcm")

    search_parser = subparsers.add_parser("search", help="PCM search")
    search_parser.add_argument("pattern")
    search_parser.add_argument("--root", default="/")
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.set_defaults(handler="pcm")

    find_parser = subparsers.add_parser("find", help="PCM find")
    find_parser.add_argument("name")
    find_parser.add_argument("--root", default="/")
    find_parser.add_argument("--kind", choices=["all", "files", "dirs"], default="all")
    find_parser.add_argument("--limit", type=int, default=10)
    find_parser.set_defaults(handler="pcm")

    write_parser = subparsers.add_parser("write", help="PCM write")
    write_parser.add_argument("path")
    write_parser.add_argument("--content", required=True)
    write_parser.add_argument("--start-line", type=int, default=0)
    write_parser.add_argument("--end-line", type=int, default=0)
    write_parser.add_argument(
        "--allow-protected-mutation",
        action="store_true",
        help="Allow writes to protected policy, scaffold, or template-like paths",
    )
    write_parser.set_defaults(handler="pcm")

    mkdir_parser = subparsers.add_parser("mkdir", help="PCM mkdir")
    mkdir_parser.add_argument("path")
    mkdir_parser.set_defaults(handler="pcm")

    move_parser = subparsers.add_parser("move", help="PCM move")
    move_parser.add_argument("from_name")
    move_parser.add_argument("to_name")
    move_parser.add_argument(
        "--allow-protected-mutation",
        action="store_true",
        help="Allow moves involving protected policy, scaffold, or template-like paths",
    )
    move_parser.set_defaults(handler="pcm")

    delete_parser = subparsers.add_parser("delete", help="PCM delete")
    delete_parser.add_argument("path")
    delete_parser.add_argument(
        "--allow-protected-mutation",
        action="store_true",
        help="Allow deletes of protected policy, scaffold, or template-like paths",
    )
    delete_parser.set_defaults(handler="pcm")

    answer_parser = subparsers.add_parser("answer", help="PCM answer")
    answer_parser.add_argument("--message", required=True)
    answer_parser.add_argument("--outcome", required=True, choices=NON_OK_OUTCOME_NAMES)
    answer_parser.add_argument("--ref", action="append", default=[], help="Grounding ref; may be repeated")
    answer_parser.set_defaults(handler="pcm")

    answer_ok_parser = subparsers.add_parser("answer-ok", help="PCM OUTCOME_OK helper with enforced refs")
    answer_ok_parser.add_argument("--message", required=True)
    answer_ok_parser.add_argument("--ref", action="append", default=[], help="Grounding ref; may be repeated")
    answer_ok_parser.set_defaults(handler="answer-ok")

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    settings = load_settings()
    state_path = Path(settings.state_path)
    state = _load_state(state_path)
    _reconcile_state_with_settings(settings, state, state_path)

    if args.command in {
        "status",
        "start-run",
        "start-trial",
        "continue",
        "resume",
        "submit",
        "preanswer",
        "inspect",
        "verify",
        "end-trial",
        "context",
        "tree",
        "list",
        "read",
        "search",
        "find",
        "write",
        "mkdir",
        "move",
        "delete",
        "answer",
        "answer-ok",
    }:
        missing_key = _require_api_key(settings)
        if missing_key is not None:
            return missing_key

    try:
        if args.handler == "status":
            return _handle_status(args, settings, state)
        if args.handler == "start-run":
            return _handle_start_run(args, settings, state, state_path)
        if args.handler == "start-trial":
            return _handle_start_trial(args, settings, state, state_path)
        if args.handler == "resume":
            return _handle_resume(args, settings, state, state_path)
        if args.handler == "submit":
            return _handle_submit(args, settings, state, state_path)
        if args.handler == "session":
            return _handle_session(args, settings, state, state_path)
        if args.handler == "preanswer":
            return _handle_preanswer(settings, state)
        if args.handler == "inspect":
            return _handle_inspect(args, settings, state, state_path)
        if args.handler == "verify":
            return _handle_verify(args, settings, state, state_path)
        if args.handler == "end-trial":
            return _handle_end_trial(args, settings, state, state_path)
        if args.handler == "answer-ok":
            return _handle_answer_ok(args, settings, state, state_path)
        if args.handler == "pcm":
            return _execute_pcm(args.command, args, settings, state, state_path)
        parser.print_help()
        return 2
    except ModuleNotFoundError as exc:
        print(f"Missing dependency: {exc.name}")
        print("Install project dependencies first, for example with `uv sync`.")
        return 2
    except KeyboardInterrupt:
        print(f"{CLI_RED}Interrupted{CLI_CLR}")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
