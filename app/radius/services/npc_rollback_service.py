"""npc_rollback_service — rollback NPC-managed changes.

Mirrors the structure of `npc_apply_service` but in reverse:
loads a change_set, verifies tenant + permission, validates
the stored rollback script is safe (managed-prefix only),
then drives `executor.execute_rollback(...)` per router.

A new child change_set is created for the rollback attempt so
the history shows BOTH the original apply and the rollback
side-by-side.

Permission policy: NPC uses the same `npc.<svc>.apply`
permission for rollback. Operators that can apply can roll
back what they applied. The brief explicitly allowed this:
"or dedicated rollback permission if existing permission
catalogue supports it. If adding: npc.<svc>.rollback update
tests and admin implications carefully. Do not imply rollback
blindly if project convention says otherwise." Reusing
`.apply` keeps the perm catalogue tight.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from ..db.repos import (
    npc_change_sets_repo as cs_repo,
)
from . import npc_router_executor as exec_mod
from .audit import get_audit_service


# Allowed comment-prefix roots — the rollback script must
# match `[find comment~"^HOBE_NPC_..."]` only. Anything else
# would risk clobbering unmanaged rules.
_SAFE_PREFIX_ROOT = "^HOBE_NPC_"

_RE_REMOVE_FIND = re.compile(
    r"remove\s+\[find\s+comment~\"([^\"]+)\"\]",
    re.IGNORECASE,
)


class RollbackSafetyError(ValueError):
    """The stored rollback script would touch unmanaged
    objects. Refusing to execute is the safer outcome."""


def _validate_rollback_safety(script: str) -> None:
    """Walk every `remove [find comment~"X"]` in the script
    and assert the pattern starts with the safe prefix root.
    The renderer + contracts engine already enforce this; we
    re-check at the rollback layer so a third path (e.g.
    a future script-edit feature) can't slip past."""
    if not script or not script.strip():
        raise RollbackSafetyError(
            "rollback script is empty — cannot proceed."
        )
    found_any = False
    for m in _RE_REMOVE_FIND.finditer(script):
        found_any = True
        pattern = m.group(1)
        if not pattern.startswith(_SAFE_PREFIX_ROOT):
            raise RollbackSafetyError(
                "rollback script contains an unmanaged "
                f"remove pattern: {pattern!r}. Aborting."
            )
    if not found_any:
        # A rollback script with no `remove` is suspicious —
        # there must be SOMETHING to undo.
        raise RollbackSafetyError(
            "rollback script has no managed `remove` "
            "instruction — refusing to execute."
        )


# ─── Result type ─────────────────────────────────────────────


@dataclass(frozen=True)
class RollbackTargetResult:
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
class RollbackResult:
    ok: bool
    change_set_id: int     # the new rollback change_set
    status: str            # cs_repo.STATUS_*
    targets: tuple[RollbackTargetResult, ...] = field(default_factory=tuple)
    reason_ar: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok":             bool(self.ok),
            "change_set_id":  int(self.change_set_id),
            "status":         self.status,
            "targets":        [t.as_dict() for t in self.targets],
            "reason_ar":      self.reason_ar,
        }


# ─── Public API ──────────────────────────────────────────────


def request_rollback(
    *,
    tenant_id: int,
    service: str,
    policy_id: int,
    change_set_id: int,
    actor: str,
    actor_has_apply_perm: bool,
    # Test-only injection:
    executor: Optional[exec_mod.RouterExecutor] = None,
) -> RollbackResult:
    """Roll back a previously-applied change_set."""
    # 1. Permission gate
    if not actor_has_apply_perm:
        return RollbackResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            reason_ar="صلاحية التراجع مفقودة لدى المستخدم.",
        )

    # 2. Tenant + existence
    original = cs_repo.get(tenant_id, change_set_id)
    if not original:
        return RollbackResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            reason_ar="change_set غير موجود لهذا المستأجر.",
        )
    if original["service"] != service \
            or int(original["policy_id"]) != int(policy_id):
        return RollbackResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            reason_ar=(
                "change_set لا يطابق السياسة المطلوبة."
            ),
        )

    # 3. Eligible status — rollback only succeeded /
    # partially_succeeded applies.
    if original["action_type"] != cs_repo.ACTION_APPLY:
        return RollbackResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            reason_ar=(
                "العنصر المطلوب ليس تنفيذ apply — لا يمكن "
                "التراجع عنه."
            ),
        )
    if original["status"] not in (
        cs_repo.STATUS_SUCCEEDED,
        cs_repo.STATUS_PARTIALLY_SUCCEEDED,
    ):
        return RollbackResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            reason_ar=(
                "حالة التنفيذ لا تسمح بالتراجع — أعد المحاولة "
                "بعد الانتهاء."
            ),
        )

    # 4. Fetch the per-router targets — we roll back the
    # routers that actually had a `succeeded` status (skipped
    # / failed routers had nothing applied to undo).
    targets = cs_repo.list_targets(int(change_set_id))
    candidates = [
        t for t in targets
        if t["status"] == cs_repo.TARGET_STATUS_SUCCEEDED
        and (t.get("rollback_script") or "").strip()
    ]
    if not candidates:
        return RollbackResult(
            ok=False, change_set_id=0,
            status=cs_repo.STATUS_FAILED,
            reason_ar=(
                "لا يوجد هدف ناجح للتراجع عنه."
            ),
        )

    # 5. Validate every rollback script — fail closed on the
    # first unmanaged pattern.
    for t in candidates:
        try:
            _validate_rollback_safety(t["rollback_script"])
        except RollbackSafetyError as e:
            return RollbackResult(
                ok=False, change_set_id=0,
                status=cs_repo.STATUS_ROLLBACK_FAILED,
                reason_ar=(
                    "سكربت التراجع غير آمن — رُفض تلقائياً: "
                    f"{e}"
                ),
            )

    # 6. Create the rollback change_set (child of original).
    rb_change_set_id = cs_repo.create(
        tenant_id=int(tenant_id),
        service=service, policy_id=int(policy_id),
        action_type=cs_repo.ACTION_ROLLBACK,
        parent_change_set_id=int(change_set_id),
        execution_mode=cs_repo.MODE_ROLLBACK,
        preview_hash=str(original.get("preview_hash") or ""),
        snapshot_id=original.get("snapshot_id"),
        requested_router_ids=[
            int(t["router_id"]) for t in candidates
        ],
        confirmations=(),
        dry_run=False,
        created_by=actor,
    )

    cs_repo.update_status(
        int(tenant_id), rb_change_set_id,
        status=cs_repo.STATUS_ROLLBACK_RUNNING,
        executed_at_now=True,
    )

    # 7. Execute per router.
    executor_obj = (executor
                    if executor is not None
                    else exec_mod.get_router_executor())
    rb_targets: list[RollbackTargetResult] = []
    for t in candidates:
        tid = cs_repo.add_target(
            change_set_id=rb_change_set_id,
            tenant_id=int(tenant_id),
            router_id=int(t["router_id"]),
            rendered_script="",   # no forward script on rollback
            rollback_script=t["rollback_script"],
            status=cs_repo.TARGET_STATUS_RUNNING,
        )
        cs_repo.update_target(tid, started_at_now=True)
        try:
            res = executor_obj.execute_rollback(
                int(t["router_id"]), t["rollback_script"],
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
        target_status = (
            cs_repo.TARGET_STATUS_ROLLED_BACK if res.ok
            else cs_repo.TARGET_STATUS_FAILED
        )
        cs_repo.update_target(
            tid, status=target_status,
            stdout=res.stdout, stderr=res.stderr,
            error_message=res.error_message,
            finished_at_now=True,
        )
        rb_targets.append(RollbackTargetResult(
            router_id=int(t["router_id"]),
            status=target_status,
            stdout=res.stdout, stderr=res.stderr,
            error_message=res.error_message,
        ))

    # 8. Aggregate the rollback status.
    statuses = [t.status for t in rb_targets]
    if all(s == cs_repo.TARGET_STATUS_ROLLED_BACK
           for s in statuses):
        agg = cs_repo.STATUS_ROLLED_BACK
    elif any(s == cs_repo.TARGET_STATUS_ROLLED_BACK
             for s in statuses):
        agg = cs_repo.STATUS_PARTIALLY_ROLLED_BACK
    else:
        agg = cs_repo.STATUS_ROLLBACK_FAILED

    cs_repo.update_status(
        int(tenant_id), rb_change_set_id,
        status=agg, finished_at_now=True,
        rolled_back_at_now=(agg == cs_repo.STATUS_ROLLED_BACK),
    )
    # Mirror the rollback outcome onto the ORIGINAL change_set
    # so the operator can see "this apply was rolled back".
    if agg == cs_repo.STATUS_ROLLED_BACK:
        cs_repo.update_status(
            int(tenant_id), int(change_set_id),
            status=cs_repo.STATUS_ROLLED_BACK,
            rolled_back_at_now=True,
        )
    elif agg == cs_repo.STATUS_PARTIALLY_ROLLED_BACK:
        cs_repo.update_status(
            int(tenant_id), int(change_set_id),
            status=cs_repo.STATUS_PARTIALLY_ROLLED_BACK,
        )

    # 9. Audit.
    try:
        get_audit_service().record(
            actor=actor,
            action=f"npc.{service}.rolled_back",
            target_type=f"npc_{service}_policy",
            target_id=str(policy_id),
            payload={
                "change_set_id":           rb_change_set_id,
                "original_change_set_id":  int(change_set_id),
                "status":                  agg,
                "router_count":            len(candidates),
            },
            router_id=None,
        )
    except Exception:  # noqa: BLE001
        pass

    return RollbackResult(
        ok=(agg == cs_repo.STATUS_ROLLED_BACK),
        change_set_id=int(rb_change_set_id),
        status=agg,
        targets=tuple(rb_targets),
        reason_ar=(
            "تم التراجع بنجاح على كل الراوترات."
            if agg == cs_repo.STATUS_ROLLED_BACK else
            "تراجع جزئي — راجع نتائج كل راوتر."
            if agg == cs_repo.STATUS_PARTIALLY_ROLLED_BACK else
            "تعذّر التراجع — راجع الأخطاء."
        ),
    )


__all__ = [
    "RollbackSafetyError",
    "RollbackTargetResult", "RollbackResult",
    "request_rollback",
]
