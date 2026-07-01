"""طابور اعتماد الإجراءات عالية القيمة (backlog النهائيّ).

المالك يَضبط عتبةً (``require_approval_above`` على سياسة المدير). إجراءات المدير
التي تتجاوزها (سلفة/استرداد/شحن كبير) لا تُنفَّذ فورًا — بل تَدخل طابور اعتماد
يُقرّه المالك (→ تُنفَّذ) أو يَرفضه (→ تُلغى). المدير يَرى «بانتظار موافقة المالك».

يُعيد استخدام العتبة القائمة على صفّ manager_distributor_policies
(require_approval_above_minor) والمحاسبة القائمة (AccountingService.create_loan)
للتنفيذ عند الاعتماد — لا مسار مالٍ موازٍ.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from ..db.connection import db
from ..db.helpers import now_iso, row_to_dict


class ApprovalError(ValueError):
    """خطأ تحقّق آمن لطابور الاعتماد."""


def _tid(tenant_id: int) -> int:
    return int(tenant_id or 1)


def threshold_minor(admin_id: Optional[int], *, tenant_id: int = 1) -> int:
    """عتبة «الاعتماد فوق» للمدير (minor). 0 = لا اعتماد مطلوب."""
    if not admin_id:
        return 0
    try:
        row = db().execute(
            "SELECT require_approval_above_minor FROM manager_distributor_policies "
            "WHERE tenant_id=? AND entity_type='manager' AND entity_id=?",
            (_tid(tenant_id), int(admin_id)),
        ).fetchone()
        return int(row["require_approval_above_minor"]) if row and row["require_approval_above_minor"] else 0
    except Exception:  # noqa: BLE001
        return 0


def needs_approval(admin_id: Optional[int], amount_minor: int, *, tenant_id: int = 1) -> bool:
    """هل يَتجاوز المبلغ عتبة الاعتماد؟ (عتبة>0 والمبلغ فوقها)."""
    th = threshold_minor(admin_id, tenant_id=tenant_id)
    return th > 0 and int(amount_minor or 0) > th


def enqueue(admin_id: int, action_key: str, *, amount_minor: int, payload: dict,
            summary: str = "", tenant_id: int = 1) -> dict[str, Any]:
    """يُدرِج طلبًا معلّقًا (لا يُنفَّذ حتى يَعتمده المالك)."""
    cur = db().execute(
        "INSERT INTO manager_pending_approvals"
        "(tenant_id, admin_id, action_key, amount_minor, payload_json, summary, status, created_at) "
        "VALUES(?,?,?,?,?,?,'pending',?)",
        (_tid(tenant_id), int(admin_id), action_key, int(amount_minor or 0),
         json.dumps(payload or {}, ensure_ascii=False), summary or "", now_iso()),
    )
    return get(int(cur.lastrowid), tenant_id=tenant_id)


def get(approval_id: int, *, tenant_id: int = 1) -> dict[str, Any]:
    row = db().execute(
        "SELECT * FROM manager_pending_approvals WHERE tenant_id=? AND id=?",
        (_tid(tenant_id), int(approval_id)),
    ).fetchone()
    if not row:
        raise ApprovalError("طلب الاعتماد غير موجود.")
    out = row_to_dict(row)
    try:
        out["payload"] = json.loads(out.get("payload_json") or "{}")
    except (TypeError, ValueError):
        out["payload"] = {}
    return out


def list_pending(*, tenant_id: int = 1, status: str = "pending") -> list[dict[str, Any]]:
    rows = db().execute(
        "SELECT * FROM manager_pending_approvals WHERE tenant_id=? AND status=? ORDER BY id DESC LIMIT 500",
        (_tid(tenant_id), status),
    ).fetchall()
    return [row_to_dict(r) for r in rows]


# ── مُنفّذات الاعتماد: مفتاح الفعل → دالة تُنفّذ الـpayload المخزَّن ──
def _execute_loan(payload: dict, *, tenant_id: int, actor: str) -> dict[str, Any]:
    from .accounting import AccountingService
    return AccountingService(_tid(tenant_id)).create_loan(dict(payload), actor=actor)


_EXECUTORS = {
    "subscriber.loan": _execute_loan,
}


def approve(approval_id: int, *, decided_by: int, tenant_id: int = 1) -> dict[str, Any]:
    """يَعتمد الطلب: يُنفّذ الفعل المخزَّن ثم يُعلّمه approved. يَرفع إن كان
    مُقرَّرًا سلفًا أو بلا مُنفّذ."""
    ap = get(approval_id, tenant_id=tenant_id)
    if ap["status"] != "pending":
        raise ApprovalError("الطلب مُقرَّر سلفًا.")
    executor = _EXECUTORS.get(ap["action_key"])
    if not executor:
        raise ApprovalError("لا يوجد مُنفّذ لهذا النوع.")
    result = executor(ap.get("payload") or {}, tenant_id=tenant_id,
                      actor=f"owner_approved:{decided_by}")
    db().execute(
        "UPDATE manager_pending_approvals SET status='approved', decided_at=?, decided_by=? WHERE id=?",
        (now_iso(), int(decided_by or 0), int(approval_id)),
    )
    return {"approval": get(approval_id, tenant_id=tenant_id), "result": result}


def reject(approval_id: int, *, decided_by: int, tenant_id: int = 1) -> dict[str, Any]:
    ap = get(approval_id, tenant_id=tenant_id)
    if ap["status"] != "pending":
        raise ApprovalError("الطلب مُقرَّر سلفًا.")
    db().execute(
        "UPDATE manager_pending_approvals SET status='rejected', decided_at=?, decided_by=? WHERE id=?",
        (now_iso(), int(decided_by or 0), int(approval_id)),
    )
    return get(approval_id, tenant_id=tenant_id)


__all__ = [
    "ApprovalError", "threshold_minor", "needs_approval", "enqueue", "get",
    "list_pending", "approve", "reject",
]
