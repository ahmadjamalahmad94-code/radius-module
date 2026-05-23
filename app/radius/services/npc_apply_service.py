"""npc_apply_service — guarded apply orchestrator.

Calls the contracts engine FIRST, refuses if not ready, then
drives per-router execution through the executor adapter.

No direct MikroTik calls anywhere. The only network-shaped
surface is `RouterExecutor.execute_forward(...)`, and the
default executor (`NullRouterExecutor`) refuses every call.

Flow:

    request_apply
      → load policy
      → load latest preview / script bytes
      → run readiness contracts (must be ready)
      → snapshot id required (caller supplies)
      → create change_set row + per-router target rows
      → for each router:
          - mark target running
          - call executor.execute_forward
          - persist stdout/stderr/error/status
          - if canary mode and first router fails → stop
      → compute aggregate status
      → emit audit event
      → return result envelope
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import (
    npc_change_sets_repo as cs_repo,
    npc_scripts_repo as scripts_repo,
)
from . import (
    npc_execution_contracts as ec,
    npc_execution_readiness as readiness_svc,
    npc_router_executor as exec_mod,
)
from .audit import get_audit_service


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class TargetResult:
    router_id: int
    status: str
    stdout: str = ""
    stderr: str = ""
    error_message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "router_id":     self.router_id,
            "status":        self.status,
            "stdout":        self.stdout,
            "stderr":        self.stderr,
            "error_message": self.error_message,
        }


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    change_set_id: int
    status: str
    targets: tuple[TargetResult, ...] = field(default_factory=tuple)
    blockers: tuple[ec.ContractIssue, ...] = field(default_factory=tuple)
    warnings: tuple[ec.ContractIssue, ...] = field(default_factory=tuple)
    reason_ar: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok":             bool(self.ok),
            "change_set_id":  int(self.change_set_id),
            "status":         self.status,
            "targets":        [t.as_dict() for t in self.targets],
            "blockers":       [b.as_dict() for b in self.blockers],
            "warnings":       [w.as_dict() for w in self.warnings],
            "reason_ar":      self.reason_ar,
        }


# ─── Public API ──────────────────────────────────────────────


def request_apply(
    *,
    tenant_id: int,
    service: str,
    policy: dict,
    policy_children: Iterable[dict],
    forward_script: str,
    rollback_script: str,
    render_error: str,
    preview_hash: str,
    snapshot_id: Optional[int],
    target_router_ids: Iterable[int],
    actor: str,
    actor_has_apply_perm: bool,
    confirmations: Iterable[str],
    execution_mode: str = cs_repo.MODE_FULL,
    canary_opt_in: bool = False,
    all_routers_targeted: bool = False,
    offline_router_ids: Iterable[int] = (),
    # Intelligence — already computed by the caller.
    impact, conflicts, dependencies, blast, health, canary,
    # Test-only injection point; production uses the default
    # null executor which refuses every call.
    executor: Optional[exec_mod.RouterExecutor] = None,
) -> ApplyResult:
    """Run the full guarded apply pipeline. The caller (route)
    is responsible for tenant/permission/policy loading; this
    function trusts its inputs but re-runs the contracts
    engine as the source of truth."""
    target_router_ids = tuple(int(r) for r in target_router_ids)

    # 1. Run the contracts engine. We pass actor permission +
    # snapshot id + target router list verbatim — the engine
    # decides ready/not-ready.
    readiness = readiness_svc.evaluate_for_preview(
        policy=policy, policy_type=service,
        impact=impact, conflicts=conflicts,
        dependencies=dependencies, blast=blast,
        health=health, canary=canary,
        forward_script=forward_script,
        rollback_script=rollback_script,
        render_error=render_error,
        apply_perm=f"npc.{service}.apply",
        actor_has_apply_perm=actor_has_apply_perm,
        snapshot_id=snapshot_id,
        expected_preview_hash=preview_hash,
        preview_hash=preview_hash,
        preview_at="",  # apply route sets this when relevant
        confirmations_provided=tuple(confirmations),
        canary_opt_in=canary_opt_in,
        target_router_ids=target_router_ids,
        offline_router_ids=tuple(offline_router_ids),
        all_routers_targeted=all_routers_targeted,
    )

    if not readiness.decision.ready:
        # Audit the rejected attempt — no change_set row is
        # created so the history stays clean of dead requests.
        try:
            get_audit_service().record(
                actor=actor,
                action=f"npc.{service}.apply_attempted",
                target_type=f"npc_{service}_policy",
                target_id=str(policy.get("id") or 0),
                payload={
                    "blockers": [b.code for b in readiness.decision.blockers],
                    "reason":   readiness.decision.reason_ar,
                },
                router_id=None,
            )
        except Exception:  # noqa: BLE001
            pass
        return ApplyResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            blockers=readiness.decision.blockers,
            warnings=readiness.decision.warnings,
            reason_ar=readiness.decision.reason_ar,
        )

    # 2. Create change_set row.
    change_set_id = cs_repo.create(
        tenant_id=int(tenant_id),
        service=service, policy_id=int(policy.get("id") or 0),
        action_type=cs_repo.ACTION_APPLY,
        execution_mode=execution_mode,
        preview_hash=preview_hash,
        health_score=int(getattr(health, "score", 0)),
        health_grade=str(getattr(health, "grade", "")),
        risk_level=str(getattr(impact, "risk_level", "")),
        snapshot_id=snapshot_id,
        requested_router_ids=target_router_ids,
        confirmations=confirmations,
        dry_run=False,
        created_by=actor,
    )

    # 3. Materialise per-router targets (all start `pending`).
    target_ids: list[int] = []
    for rid in target_router_ids:
        target_ids.append(cs_repo.add_target(
            change_set_id=change_set_id,
            tenant_id=int(tenant_id),
            router_id=int(rid),
            rendered_script=forward_script,
            rollback_script=rollback_script,
            status=cs_repo.TARGET_STATUS_PENDING,
        ))

    cs_repo.update_status(
        int(tenant_id), change_set_id,
        status=cs_repo.STATUS_RUNNING,
        executed_at_now=True,
    )

    # 4. Drive execution per router.
    executor_obj = (executor
                    if executor is not None
                    else exec_mod.get_router_executor())
    target_results: list[TargetResult] = []
    canary_failed = False

    for tid, rid in zip(target_ids, target_router_ids):
        if canary_failed:
            # Canary mode: stop rollout on first failure.
            cs_repo.update_target(
                tid, status=cs_repo.TARGET_STATUS_SKIPPED,
                error_message=(
                    "skipped — canary stopped after first failure"
                ),
                finished_at_now=True,
            )
            target_results.append(TargetResult(
                router_id=int(rid),
                status=cs_repo.TARGET_STATUS_SKIPPED,
                error_message=(
                    "skipped — canary stopped after first failure"
                ),
            ))
            continue

        cs_repo.update_target(
            tid, status=cs_repo.TARGET_STATUS_RUNNING,
            started_at_now=True,
        )
        try:
            res = executor_obj.execute_forward(
                int(rid), forward_script,
            )
        except exec_mod.ExecutorError as e:
            res = exec_mod.ExecutionResult(
                ok=False, status=cs_repo.TARGET_STATUS_FAILED,
                error_message=str(e),
            )
        except Exception as e:  # noqa: BLE001
            res = exec_mod.ExecutionResult(
                ok=False, status=cs_repo.TARGET_STATUS_FAILED,
                error_message=f"executor exception: {e}",
            )

        # Map executor status → repo status.
        target_status = (
            cs_repo.TARGET_STATUS_SUCCEEDED if res.ok
            else cs_repo.TARGET_STATUS_FAILED
        )
        cs_repo.update_target(
            tid, status=target_status,
            stdout=res.stdout, stderr=res.stderr,
            error_message=res.error_message,
            finished_at_now=True,
        )
        target_results.append(TargetResult(
            router_id=int(rid),
            status=target_status,
            stdout=res.stdout,
            stderr=res.stderr,
            error_message=res.error_message,
        ))

        if (not res.ok
            and execution_mode == cs_repo.MODE_CANARY):
            canary_failed = True

    # 5. Aggregate status.
    statuses = [t.status for t in target_results]
    if all(s == cs_repo.TARGET_STATUS_SUCCEEDED
           for s in statuses):
        agg = cs_repo.STATUS_SUCCEEDED
    elif any(s == cs_repo.TARGET_STATUS_SUCCEEDED
             for s in statuses):
        agg = cs_repo.STATUS_PARTIALLY_SUCCEEDED
    else:
        agg = cs_repo.STATUS_FAILED

    cs_repo.update_status(
        int(tenant_id), change_set_id,
        status=agg, finished_at_now=True,
    )

    # 6. Audit.
    try:
        get_audit_service().record(
            actor=actor,
            action=(
                f"npc.{service}.applied"
                if agg == cs_repo.STATUS_SUCCEEDED
                else f"npc.{service}.apply_failed"
            ),
            target_type=f"npc_{service}_policy",
            target_id=str(policy.get("id") or 0),
            payload={
                "change_set_id": change_set_id,
                "status":        agg,
                "router_count":  len(target_router_ids),
                "preview_hash":  preview_hash,
            },
            router_id=None,
        )
    except Exception:  # noqa: BLE001
        pass

    return ApplyResult(
        ok=(agg == cs_repo.STATUS_SUCCEEDED),
        change_set_id=int(change_set_id),
        status=agg,
        targets=tuple(target_results),
        blockers=(),
        warnings=readiness.decision.warnings,
        reason_ar=(
            "تم تنفيذ السياسة بنجاح على كل الراوترات."
            if agg == cs_repo.STATUS_SUCCEEDED else
            "تنفيذ جزئي — راجع نتائج كل راوتر."
            if agg == cs_repo.STATUS_PARTIALLY_SUCCEEDED else
            "تعذّر التنفيذ على أي راوتر — راجع الأخطاء."
        ),
    )


__all__ = [
    "TargetResult", "ApplyResult",
    "request_apply",
]
