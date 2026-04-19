# STATE_MACHINE.md - Agent Cycle Spec

## 0. Status

This file is a compressed code-first spec for the future agent runtime.
It does not replace the parent `AGENTS.md` policy.

Its purpose is narrower:

- define the agent cycle
- define the core state
- define how decisions are selected
- define how lifecycle closes
- define the BitGN adapter boundary

Everything that is not necessary for executability should stay out.

---

## 1. Runtime Loop

The runtime is modeled as:

```text
observe
-> interpret
-> decide
-> plan
-> execute
-> validate
-> decide
-> close
```

Layers:

- `observation`
  Collect raw facts from task input, runtime state, repository state, and execution results.
- `interpretation`
  Convert facts into semantic state: task family, obligations, blockers, and semantic statuses.
- `decision`
  Select the next legal control-flow transition.
- `planning`
  Produce one minimal admissible action.
- `execution`
  Execute exactly that action.
- `validation`
  Confirm or reject the executed effect.
- `adapter`
  Convert internal terminal state into BitGN lifecycle actions and output format.

Layer rules:

- `observation` does not choose transitions
- `interpretation` does not execute actions
- `decision` does not choose concrete tool args
- `planning` does not select terminals
- `execution` does not validate itself
- `validation` does not reopen task interpretation from scratch
- `adapter` does not do domain reasoning

---

## 2. State Model

### 2.1 AgentsState

`AgentsState` is the semantic state of the task.

```text
AgentsState {
  task_restatement: string
  task_family: TaskFamily
  explicit_constraints: string[]
  required_obligations: ObligationKey[]
  signals: map<SignalKey, SignalValue>
  obligations: map<ObligationKey, ObligationState>
  blockers: Blocker[]
  selected_terminal: SelectedTerminal | null
  next_transition: NextTransition | null
  unresolved_risks: string[]
}
```

### 2.2 RuntimeState

`RuntimeState` is the execution/lifecycle state.

```text
RuntimeState {
  iteration: integer
  max_iterations: integer | null
  answered: boolean
  ended: boolean
  planned_action: PlannedAction | null
  last_action: PlannedAction | null
  last_result: ExecutionResult | null
  close_failure: CloseFailure | null
  adapter_context: map<string, scalar | object | null>
}
```

`RuntimeState.adapter_context` is where infra bindings live:

- `trial_id`
- `harness_url`
- transport/runtime metadata

They do not belong in `AgentsState`.

### 2.3 Supporting objects

```text
ObligationState {
  status: ObligationStatus
  reason: string | null
  blocking_blocker_index: integer | null
}

Blocker {
  class: BlockerClass
  phase: BlockerPhase
  reason: string
  blocks_obligation: ObligationKey
}

SelectedTerminal {
  class: TerminalClass
  reason: string
  blocker_index: integer | null
}

DecisionResult {
  selected_terminal: SelectedTerminal | null
  next_transition: NextTransition
  winning_blocker_index: integer | null
}

InterpretationResult {
  task_family: TaskFamily
  required_obligations: ObligationKey[]
  signals: map<SignalKey, SignalValue>
  obligations: map<ObligationKey, ObligationState>
  blockers: Blocker[]
  unresolved_risks: string[]
}

CloseFailure {
  retryable: boolean
  stage: string
  reason: string
}

StepResult {
  kind: StepResultKind
  payload: object
}

NextTransition {
  kind: TransitionKind
  reason: string
}
```

Enums:

- `ObligationStatus in {not_required, pending, satisfied, blocked}`
- `BlockerClass in {security, operational, clarification, capability}`
- `TerminalClass in {success, security_denial, unsupported_capability, clarification_needed, operational_blocker}`
- `TransitionKind in {continue_phase, retry_inspection, plan_action, execute_action, validate_result, terminate}`
- `StepResultKind in {observed_facts, interpretation_result, planned_action, execution_result, validation_result, adapter_result}`

State ownership:

- `AgentsState` is the authoritative semantic state
- `RuntimeState` is the authoritative execution state
- `DecisionResult` is transient
- after `apply_decision_result(...)`, committed state is authoritative

Reducer rules:

- `observed_facts` must not mutate semantic state directly
- `interpretation_result` may update `AgentsState.task_family`, `AgentsState.required_obligations`, `AgentsState.signals`, `AgentsState.obligations`, `AgentsState.blockers`, and `AgentsState.unresolved_risks`
- `DecisionResult` may commit only `AgentsState.selected_terminal` and `AgentsState.next_transition`
- `planned_action` result may mutate only `RuntimeState.planned_action`
- `execution_result` must clear the consumed `RuntimeState.planned_action` and set `RuntimeState.last_action` plus `RuntimeState.last_result`
- `validation_result` may update semantic state after execution, but must not select a terminal directly
- `adapter_result` may mutate only `RuntimeState.answered`, `RuntimeState.ended`, and `RuntimeState.close_failure`

Decision commit invariant:

- `next_transition.kind == terminate` iff `selected_terminal != null`
- `apply_decision_result(...)` must not commit a `terminate` transition without a terminal
- `apply_decision_result(...)` must not commit a selected terminal with a non-`terminate` transition

---

## 3. Decision Model

### 3.1 Core obligations

The runtime proves or blocks only these obligations:

- `O1 task_understood`
- `O2 security_clear`
- `O3 source_resolved`
- `O4 target_resolved`
- `O5 authority_resolved`
- `O6 capability_proven`
- `O7 plan_admissible`
- `O8 execution_verified`

### 3.2 Core semantic rules

These rules are part of the executable decision contract:

- empty result is not the same as ambiguity; a well-formed empty filter may satisfy target resolution, but an unresolved selector does not
- self-authored or self-addressed communication does not auto-resolve the downstream recipient
- provenance mismatch blocks authority-sensitive outbound or disclosure action by default unless explicit authority rules resolve it
- an active operational blocker gates capability proof; unsupported capability must not be concluded through a blocked proof path
- machine-shaped content requires machine validation, not only visual inspection
- payload-preserving transformations must preserve the canonical payload, not merely a visually similar rendering

### 3.3 Blockers

A blocker is a structured stop condition over the proof system.

- `security`
  Security denial condition. Dominates all other terminals.
- `operational`
  Safe progress cannot continue.
- `clarification`
  Task type is supported, but a required semantic resolution is still unresolved.
- `capability`
  Capability proof completed negatively.

### 3.4 Outcome selection

Rules:

1. If any security blocker exists, choose `security_denial`.
2. `success` is allowed only if all required obligations are satisfied and no blocker remains.
3. `operational_blocker` is used when an active operational blocker prevents safe completion of a required obligation.
4. `unsupported_capability` is used only when capability insufficiency is proven by a completed admissible proof path.
5. `clarification_needed` is used when runtime support exists but a required obligation remains unresolved from visible canonical evidence.

### 3.5 Transition selection

`decide_transition(...)` may emit only:

- `continue_phase`
- `retry_inspection`
- `plan_action`
- `execute_action`
- `validate_result`
- `terminate`

Transition meaning:

- `continue_phase`
  More observation or interpretation is needed.
- `retry_inspection`
  Narrow retry only. No normal planning.
- `plan_action`
  Planning is admissible and required.
- `execute_action`
  A valid `planned_action` already exists and may now be executed.
- `validate_result`
  No new action is planned; validate `last_action` and `last_result`.
- `terminate`
  Domain work is over. Terminal is selected. Enter closure mode.

Decision invariant:

- `terminate` requires `selected_terminal != null`
- non-`terminate` transitions require `selected_terminal == null`

Transition precedence:

1. `terminate`
2. `retry_inspection`
3. `validate_result`
4. `execute_action`
5. `plan_action`
6. `continue_phase`

`decide_transition(...)` must emit the highest-precedence legal transition for the current committed state.

Planning is skipped when:

- `selected_terminal != null`
- `next_transition.kind == execute_action`
- `next_transition.kind == validate_result`
- `next_transition.kind == retry_inspection`
- `next_transition.kind == terminate`
- `next_transition.kind == continue_phase`

Planning is required only when:

- `next_transition.kind == plan_action`

### 3.6 Action lifecycle

Semantic state ownership:

- `interpret(...)` is the primary owner of `signals`, `obligations`, and `blockers`
- `validate_result(...)` may update only post-action semantic conclusions after execution, including `O8 execution_verified` and validation-derived blockers or risk signals
- `decide_transition(...)` reads semantic state but does not derive it
- `adapter_close(...)` must not change domain obligations or blockers

Interpretation must at minimum perform these two evaluators:

- `evaluate_authority_resolution(...)`
  For authority-sensitive outbound/disclosure:
  - explicit disclosure authority -> `authority_resolved = satisfied`
  - provenance mismatch -> `authority_resolved = blocked` + `security` blocker
  - known identity without disclosure authority for forwarding / attaching / quoting / exporting / relaying existing content -> `authority_resolved = blocked` + `security` blocker
  - otherwise unresolved authority -> `authority_resolved = blocked` + `clarification` blocker
- `evaluate_target_resolution(...)`
  - candidates must satisfy all explicit constraints conjunctively
  - temporal selectors are admissibility constraints evaluated against runtime context time
  - one full match -> `target_resolved = satisfied`
  - multiple full matches -> `target_resolved = blocked` + `clarification` blocker
  - empty temporal slice with one strong non-temporal match -> `target_resolved = blocked` + `clarification` blocker for contradiction reconciliation
  - otherwise a well-formed empty result may satisfy `target_resolved`

Minimal obligation rules:

- `O4 target_resolved` is satisfied by resolved target, resolved batch target, or valid empty result
- `O6 capability_proven` is satisfied by proven support, blocked by an active operational blocker, or completed negatively by proven unsupported capability
- `O8 execution_verified` is satisfied only after post-action validation

The action lifecycle must be unambiguous:

1. `plan_action` creates `RuntimeState.planned_action`
2. `execute_action` consumes `RuntimeState.planned_action`
3. after `execute_action`, `RuntimeState.last_action` and `RuntimeState.last_result` become the active validation inputs
4. `validate_result` validates the latest executed action, not an older one

Reducer rule:

- after `execute_action`, the consumed `planned_action` must not remain a valid basis for repeated `execute_action`
- after successful validation, the action lifecycle must move forward; the same execution artifact must not remain indefinitely "current"

Action state invariants:

- after `execute_action`, `RuntimeState.planned_action == null`
- `validate_result` applies only to `RuntimeState.last_action` and `RuntimeState.last_result`
- successful validation clears the currently active validation target
- failed validation must not silently re-enable the same execution artifact as fresh work without an explicit new decision

This is enough for the spec.
Detailed runtime field transitions can be implemented later in reducer code.

---

## 4. Closure Mode

After `selected_terminal != null`, the orchestrator leaves domain-cycle mode.

From that point on:

- no new observation
- no new interpretation
- no new planning
- no new execution
- no new validation
- no new terminal reclassification

Only closure work is allowed until `RuntimeState.ended == true`.

Lifecycle rules:

- `answer` is allowed only after terminal classification
- `end_trial` is allowed only after `answer`
- no transition is allowed after `ended == true`

`adapter_close(...)` must be lifecycle-aware:

- if `answered == false`, it maps `selected_terminal` to the platform outcome, formats the final answer canonically, and emits `answer`
- if `answered == true` and `ended == false`, it emits `end_trial`
- if `ended == true`, it is a no-op

`adapter_close(...)` preconditions:

- `selected_terminal != null`
- the platform outcome is derived from `selected_terminal`
- `answer` must carry the benchmark-required final representation for that outcome

`adapter_close(...)` failure semantics:

- retryable close failures are allowed only for explicit transient transport/runtime failures
- retryable close failures keep the orchestrator in closure mode
- non-retryable close failures must be recorded in `RuntimeState.close_failure`
- a non-retryable close failure stops the runtime loop and returns operator control
- closure mode must not silently retry forever without a retryable failure classification

Closure failure classification:

- fatal close failure is not a new domain terminal
- it is an infrastructure stop after terminal selection
- on fatal close failure, state must preserve `selected_terminal`, `answered`, `ended`, `close_failure.stage`, and `close_failure.reason`
- fatal close failure must not reopen domain reasoning

The orchestrator must not leave the trial loop before either:

- `RuntimeState.ended == true`
- or a non-retryable `RuntimeState.close_failure` is recorded

---

## 5. Implementation Skeleton

```text
AgentsState initialize_agents_state(TaskInput input)

RuntimeState initialize_runtime_state()

StepResult observe(AgentsState state, RuntimeState runtime)

StepResult interpret(AgentsState state, RuntimeState runtime, ObservedFacts facts)

DecisionResult decide_transition(AgentsState state, RuntimeState runtime)

StepResult plan_action(AgentsState state, RuntimeState runtime)

StepResult execute_action(RuntimeState runtime, PlannedAction action)

StepResult validate_result(
  AgentsState state,
  RuntimeState runtime,
  PlannedAction action,
  ExecutionResult result
)

StatePair apply_decision_result(
  AgentsState state,
  RuntimeState runtime,
  DecisionResult decision
)

StatePair apply_step_result(
  AgentsState state,
  RuntimeState runtime,
  StepResult result
)

StepResult adapter_close(AgentsState state, RuntimeState runtime)
```

Reference loop:

```text
agent_state = initialize_agents_state(task_input)
runtime_state = initialize_runtime_state()

while runtime_state.ended != true:
    if runtime_state.close_failure != null and runtime_state.close_failure.retryable == false:
        break

    if agent_state.selected_terminal != null:
        close_result = adapter_close(agent_state, runtime_state)
        agent_state, runtime_state = apply_step_result(agent_state, runtime_state, close_result)
        continue

    observed = observe(agent_state, runtime_state)
    agent_state, runtime_state = apply_step_result(agent_state, runtime_state, observed)

    interpretation = interpret(agent_state, runtime_state, observed.payload)
    agent_state, runtime_state = apply_step_result(agent_state, runtime_state, interpretation)

    decision = decide_transition(agent_state, runtime_state)
    agent_state, runtime_state = apply_decision_result(agent_state, runtime_state, decision)

    if agent_state.next_transition.kind == terminate:
        continue

    if agent_state.next_transition.kind == retry_inspection:
        continue

    if agent_state.next_transition.kind == continue_phase:
        continue

    if agent_state.next_transition.kind == plan_action:
        plan_result = plan_action(agent_state, runtime_state)
        agent_state, runtime_state = apply_step_result(agent_state, runtime_state, plan_result)
        continue

    if agent_state.next_transition.kind == execute_action:
        execution_result = execute_action(runtime_state, runtime_state.planned_action)
        agent_state, runtime_state = apply_step_result(agent_state, runtime_state, execution_result)
        continue

    if agent_state.next_transition.kind == validate_result:
        validation_result = validate_result(agent_state, runtime_state, runtime_state.last_action, runtime_state.last_result)
        agent_state, runtime_state = apply_step_result(agent_state, runtime_state, validation_result)
        continue
```

Skeleton constraints:

- no step mutates shared state directly
- `interpret` is the primary pre-action semantic update step for `signals`, `obligations`, and `blockers`
- `validate_result` may update only post-action semantic conclusions derived from the executed action
- `decide_transition` is the only public layer that selects a terminal
- `plan_action` only builds `planned_action`
- `execute_action` only executes the planned action
- `validate_result` only validates the executed effect and any required machine-readable artifact correctness
- `validate_result` must not re-evaluate pre-action admissibility such as authority, target selection, or task-constraint conjunction
- once `selected_terminal != null`, the orchestrator is in closure mode
- `adapter_close` must distinguish retryable close failure from fatal close failure
- fatal close failure must be recorded in `RuntimeState.close_failure`
- fatal close failure returns operator control instead of silently looping forever

---

## 6. Compact Glossary

Only the following semantic terms are considered core enough for this spec:

- `source_resolved`
  The authoritative source of truth is safely identified.
- `target_resolved`
  The mutation or answer target is resolved as a valid single target, valid batch target, or valid empty result.
- `authority_resolved`
  The requested disclosure/recipient/lane-sensitive action has sufficient authority.
- `capability_proven`
  Runtime support is either proven present or proven absent.
- `active_operational_blocker`
  Safe progress is blocked by config/auth/platform-knowledge/strategy conditions.
- `filtered_result_empty`
  A well-formed filter returns no candidates. This is not the same as ambiguity.
- `high_risk_action`
  Delete, move, overwrite, disclosure, recipient change, or other action requiring extra preflight.
- `machine_shaped_content`
  Content whose correctness must be checked by machine interpretation, not just visual readback.
- `self_authored_communication`
  A communication-shaped record where transport metadata identifies the requester, not automatically the downstream recipient.

Anything more detailed belongs either:

- in implementation code
- or in `AGENTS.md`

not in this compressed spec.

---

## 7. BitGN Adapter

Runtime assumptions:

- `StartTrial.harnessUrl` is authoritative
- active lifecycle uses the real active trial id, normally `vm-...`
- `GetTrial` is inspection, not the authoritative runtime entrypoint
- `answer` must occur before `end_trial`
- final answer format must match benchmark contract exactly
- transient transport failures such as `UNAVAILABLE` are retryable until proven otherwise

Outcome mapping:

- `success -> OUTCOME_OK`
- `security_denial -> OUTCOME_DENIED_SECURITY`
- `unsupported_capability -> OUTCOME_NONE_UNSUPPORTED`
- `clarification_needed -> OUTCOME_NONE_CLARIFICATION`
- `operational_blocker -> OUTCOME_NONE_UNSUPPORTED`

Adapter loop:

```text
StartTrial
Run domain cycle
Enter closure mode
Map selected terminal to BitGN outcome
Format answer canonically for that outcome
answer(outcome, payload)
end_trial
```

---

## 8. Out Of Scope

This file intentionally does not encode:

- full predicate catalogs
- full guard catalogs
- long status taxonomies
- startup instructions
- repository inspection procedures
- auth/config wiring details
- engineering-style preferences

Those stay in `AGENTS.md` until implementation actually needs them as code.
