# NPC Safe Execution

This document describes the safety architecture of the Network
Policy Center's apply / rollback path. It is intended for
operators and on-call responders, not end users.

> **Application-level safety only.** The gates documented here
> are enforced inside this codebase. They do not — and cannot —
> guarantee correctness of any individual MikroTik device,
> network behaviour, or vendor edge case. A live executor
> adapter is not shipped with this repository; the default
> `NullRouterExecutor` refuses every execute call. Until a live
> adapter is opted in by a future change, no live MikroTik
> traffic leaves this process.

## High-level workflow

```
edit policy ───► generate preview ───► review intelligence ──┐
                                                              │
                                  ┌───────────────────────────┘
                                  ▼
                          execution readiness
                                  │
        ready? ── no ─► blockers → operator resolves
                                  │
                                  ▼ yes
                          confirmations checked
                                  │
                                  ▼
                          snapshot captured  ◄── fails closed
                                  │            (no live reader →
                                  ▼             no_snapshot blocker)
                          change_set created
                                  │
                          per-router execute_forward
                                  │
                          ┌───────┴───────┐
                          ▼               ▼
                       succeeded      failed
                                          │
                                          ▼
                                   rollback button
                                          │
                                          ▼
                              per-router execute_rollback
                                          │
                                          ▼
                              change_set status mirrored
```

## Components

| Layer | Module | Responsibility |
|---|---|---|
| Intelligence | `npc_impact_analyzer`, `npc_conflict_detector`, `npc_dependency_detector`, `npc_blast_radius`, `npc_beginner_explainer`, `npc_policy_health`, `npc_canary_planner`, `npc_recommendations` | Pure data → intelligence outputs. No router contact. |
| Contracts | `npc_execution_contracts.evaluate(ContractInputs)` | Rule engine; emits blockers + warnings + required confirmations. |
| Readiness | `npc_execution_readiness.evaluate_for_preview(...)` | Thin orchestrator that composes the upstream signals into a `ContractInputs` and calls `evaluate`. |
| Snapshot reader | `npc_router_state_reader` | Single read-only adapter boundary. Default `NullStateReader` refuses every call. Tests inject `FakeStateReader`. |
| Snapshot capture | `npc_snapshot_capture_service.capture_pre_apply_snapshot(...)` | Reads, redacts secret-shaped fields (drops the keys entirely), persists via `npc_snapshots_repo`. |
| Apply | `npc_apply_service.request_apply(...)` | Re-runs contracts; creates change_set + per-router targets; drives executor per router; aggregates status; emits audit. |
| Rollback | `npc_rollback_service.request_rollback(...)` | Verifies the change_set + safety re-checks every stored rollback script (`^HOBE_NPC_` prefix only); drives executor per router; mirrors result onto the original change_set. |
| Executor | `npc_router_executor` | Single write-side adapter boundary. Default `NullRouterExecutor` refuses every call. Tests inject `FakeRouterExecutor`. |

## Persistent state

Migration 044 introduced the policy + target + deployment + script_versions tables.
Migration 045 added the snapshot foundation (`network_policy_snapshots` + items).
Migration 046 introduced the execution envelope:

* **`npc_change_sets`** — one row per apply or rollback attempt:
  tenant_id, service, policy_id, action_type, parent_change_set_id, execution_mode, status, preview_hash, health_score, health_grade, risk_level, snapshot_id, requested_router_ids, confirmations_json, dry_run, created_by/created_at, executed_at, finished_at, rolled_back_at, error_message, notes.
* **`npc_change_set_targets`** — one row per (change_set, router):
  status, rendered_script, rollback_script, stdout, stderr, error_message, started_at, finished_at.

None of these tables carry a `password`, `private_key`, or `secret` column. Tests pin this invariant on every NPC table.

## Hard gates that cannot be bypassed

The contracts engine refuses apply when **any** of these are present, regardless of confirmations or operator role:

| Code | Meaning |
|---|---|
| `missing_apply_perm` | The actor's session lacks `npc.<svc>.apply`. |
| `no_valid_preview` | No script has been generated yet for this policy. |
| `preview_stale` | The policy was edited after the last preview. |
| `preview_hash_mismatch` | The apply request carries a different hash than the stored preview. |
| `no_rollback` | The renderer produced no rollback script. |
| `no_snapshot` | No snapshot id was supplied (snapshot capture failed or the reader is not configured). |
| `no_target_routers` | The requested router list is empty. |
| `critical_risk` | The impact analyzer classified the plan as `critical`. |
| `dangerous_health` | The policy health score dropped to grade `dangerous`. |
| `critical_conflict` | At least one HIGH-severity conflict with another tenant policy. |
| `unsafe_script` | The renderer aborted with `RenderSafetyError`. |
| `unmanaged_deletion` | The script contains a `remove [find comment~"X"]` whose X is NOT anchored with `^HOBE_NPC_`. |
| `target_router_offline` | A caller-supplied flag indicating one of the target routers is unreachable. |
| `all_routers_without_canary` | The policy targets every router AND the operator did not opt into a canary bypass. |
| `secret_like_content` | The rendered forward script contains a tripwire substring (`password=`, `private-key=`, etc.). |

Confirmations (`confirm_large_blast_radius`, `confirm_firewall_drop`, `confirm_all_router_scope`, `confirm_dependency_impact`, `confirm_canary_bypass`) gate soft surfaces only. None of them can bypass the hard gates above.

## Rollback safety

`npc_rollback_service` re-validates the stored rollback script before execution:

* Empty / whitespace-only scripts are refused.
* A rollback script with no `remove [find comment~"X"]` instruction is refused (suspicious — a rollback that doesn't remove anything is not a rollback).
* Any `remove [find comment~"X"]` where `X` is not anchored with `^HOBE_NPC_` is refused — defence in depth against unmanaged deletion.

Permission policy: `npc.<svc>.apply` controls rollback as well. Operators who can apply can roll back what they applied.

## What is NOT guaranteed

* **MikroTik device safety.** RouterOS versions vary; a script that imports cleanly on one ROS version may behave differently on another. The application layer enforces shape and intent, not vendor compatibility.
* **Network behaviour.** Estimated user counts on the blast-radius card come from local DB state, not real-time router telemetry. The heuristic note on every blast result says so.
* **Race conditions on the router itself.** Two concurrent operators applying overlapping policies on the same router are not coordinated by this codebase. The change_set log makes the race visible after the fact.
* **Real-time monitoring.** This codebase does not poll the router after apply to verify the rules survived. The change_set status reflects what the executor reported on its single invocation.

## Emergency operator notes

If a policy applied incorrectly:

1. Open `سجل التغييرات` for the affected policy.
2. Find the most recent successful `apply` change_set.
3. Click `تراجع عن هذا التنفيذ`.
4. The rollback service will execute only commands matching the policy's anchored `HOBE_NPC_<svc>:<id>:` prefix — unmanaged router rules stay untouched.
5. If rollback also fails, the per-router target row carries the executor's stderr. Hand it to the network team.

If the apply path is mis-emitting traffic somehow:

1. Set `HOBERADIUS_NPC_DISABLE_APPLY=1` (a future kill-switch — not currently wired; document it here so the on-call knows where it would go).
2. Until that ships, the operational kill-switch is "revoke the `npc.<svc>.apply` permission from every role" — the route returns 403 immediately, before any executor call.

## Operator checklist

Before clicking «تطبيق آمن»:

* [ ] The Decision Hero shows a green «جاهزة» badge.
* [ ] No red blockers under «جاهزيّة التنفيذ».
* [ ] You have read the «ماذا سيحدث؟» paragraph and it matches your intent.
* [ ] You have ticked every required confirmation checkbox.
* [ ] You picked an execution mode that matches the recommended one (or you have a reason to override).
* [ ] If the canary planner recommended `canary`, you did not silently switch to `full`.
* [ ] After apply, you opened the changes page to verify per-router status.
* [ ] If anything looks wrong, you used the rollback button — not a manual MikroTik shell session.

## What changes when a live executor ships

When a future commit opts in to a live `RouterExecutor`:

1. `set_router_executor(LiveExecutor(...))` is called once at boot.
2. Every other code path in this document continues to work unchanged.
3. The contracts engine remains the only gate — same blockers, same confirmations.
4. The snapshot reader must also be wired to a live `RouterStateReader` so the apply path can capture a real pre-state.

Until then, the apply route returns "no_snapshot" or "executor not configured" for any operator who tries — by design.
