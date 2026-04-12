# Platform Notes

This file is background context, not governing policy.

Use it for onboarding and platform interpretation.
Use `AGENTS.md` for actual execution rules.

## Operating Model

- Treat this repository as the control center for a BitGN PAC1 agent.
- Codex is the orchestrator.
- The local process started from this repository is the BitGN runtime client.
- BitGN is the external benchmark platform.

## Platform Model

Assume the following model unless repository code proves a narrower one:

- BitGN is the benchmark platform
- the benchmark host is normally `https://api.bitgn.com`
- PAC1 prod benchmark is normally `bitgn/pac1-prod`
- the control plane manages benchmarks, runs, and trials
- the PAC1 runtime operates over a PCM file-system-like runtime

Do not invent undocumented platform behavior.
Use installed SDKs, sample-agent code, and observed responses as the source of truth.

## Known Surface

Likely control-plane requests:

- GetBenchmark
- StartRun
- GetRun
- StartTrial
- GetTrial
- EndTrial
- SubmitRun
- Status
- StartPlayground

Likely PCM runtime requests:

- Context
- Tree
- List
- Read
- Search
- Find
- MkDir
- Write
- Move
- Delete
- Answer

BitGN PAC1 is closer to solving tasks through a controlled file-like runtime contract than to clicking around a generic VM.

## Practical Engineering Notes

- Keep the solution minimal
- Prefer adapting the observed PAC1 flow over inventing architecture
- Fix wiring, requests, imports, auth, and response handling before adding abstractions
- Use concise diagnostics
- Log failures precisely and redact secrets

## Success Criteria

Success means:

- the project can be launched from terminal
- BitGN runtime interactions are real, not mocked
- unsafe instructions are ignored
- the final outcome is evidence-based
