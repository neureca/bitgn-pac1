from __future__ import annotations

import os
from dataclasses import dataclass


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


@dataclass(frozen=True)
class Settings:
    benchmark_profile: str
    bitgn_host: str
    benchmark_id: str
    run_name: str
    bitgn_api_key: str
    state_path: str
    journal_path: str
    state_path_explicit: bool
    journal_path_explicit: bool
    min_ok_refs: int


def _default_benchmark_id(profile: str) -> str:
    normalized = profile.strip().lower()
    if normalized == "prod":
        return "bitgn/pac1-prod"
    return "bitgn/pac1-dev"


def load_settings() -> Settings:
    benchmark_profile = _env("BENCHMARK_PROFILE", "PAC_PROFILE", default="prod")
    state_path_env = _env("BITGN_STATE_PATH")
    journal_path_env = _env("BITGN_JOURNAL_PATH")
    if bool(state_path_env) != bool(journal_path_env):
        raise SystemExit("BITGN_STATE_PATH and BITGN_JOURNAL_PATH must be set together or omitted together.")
    return Settings(
        benchmark_profile=benchmark_profile,
        bitgn_host=_env("BENCHMARK_HOST", "BITGN_HOST", default="https://api.bitgn.com"),
        benchmark_id=_env("BENCHMARK_ID", "BENCH_ID", default=_default_benchmark_id(benchmark_profile)),
        run_name=_env("BITGN_RUN_NAME", "RUN_NAME", default="https://t.me/ulanov_agents"),
        bitgn_api_key=_env("BITGN_API_KEY"),
        state_path=state_path_env or ".bitgn-state/operator.state.json",
        journal_path=journal_path_env or ".bitgn-state/operator.journal.jsonl",
        state_path_explicit=bool(state_path_env),
        journal_path_explicit=bool(journal_path_env),
        min_ok_refs=int(_env("BITGN_MIN_OK_REFS", default="1")),
    )
