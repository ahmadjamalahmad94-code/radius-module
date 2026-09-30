"""«تعديلات عامة» (bulk disable / enable / extend / reset password) — ONE
source for the web tool page and ``POST /api/v1/tools/general-adjustments``.

Stress 2026-09-28 (A08 LOW-3): the API dry run checked nothing — unknown
users, ``extend`` without minutes and non-string usernames were all listed as
«targets», so the preview did not predict what the real run would refuse; the
real run then reported raw English per-user errors («minutes»). Now:

* the request is validated ONCE up front (action, minutes, new password), so a
  bad request is a 422 / flash before anything runs;
* ``plan()`` is the dry run: every username is resolved against the tenant and
  gets the exact outcome the real run would have (ok / not found / no change),
  with the new expiry for ``extend``;
* ``run()`` executes and reports Arabic per-user errors.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..core.errors import RadiusError, RadiusNotFound, RadiusValidationError
from ..core import limits

ACTIONS = ("disable", "enable", "extend", "reset_password")
MAX_USERNAMES = 500
# One extend ≤ «أقصى عدد أيام تفعيل/تمديد» (settings «الحدود», default 1 year —
# same cap as extend_time) — was 10 years here, so the preview promised what the
# real run refused. Read per request: limits.max_extend_minutes().

_ACTION_LABELS = {
    "disable": "تعطيل",
    "enable": "تفعيل",
    "extend": "إضافة وقت",
    "reset_password": "تغيير كلمة المرور",
}


def parse_usernames(raw: Any) -> list[str]:
    """A list (or a comma/newline separated string) → clean, de-duplicated
    usernames in input order. Non-string items (numbers) become strings."""
    if isinstance(raw, str):
        items = raw.replace(",", "\n").split("\n")
    elif isinstance(raw, (list, tuple)):
        items = [x for x in raw if x is not None and not isinstance(x, (dict, list, bool))]
    else:
        items = []
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        name = str(item).strip()
        if name and name not in seen:
            seen.add(name)
            out.append(name)
    return out


def validate_request(action: Any, usernames: list[str], *, minutes: Any = None,
                     new_password: Any = None) -> dict:
    """Validate the whole request once. Returns the normalised params or
    raises ``RadiusValidationError`` (Arabic)."""
    act = str(action or "").strip()
    if act not in ACTIONS:
        raise RadiusValidationError("إجراء التعديل غير معروف.")
    if not usernames:
        raise RadiusValidationError("أدخل اسم مستخدم واحدًا على الأقل.")
    if len(usernames) > MAX_USERNAMES:
        raise RadiusValidationError(
            f"عدد أسماء المستخدمين كبير جدًا لطلب واحد (الحدّ {MAX_USERNAMES}).")
    params: dict[str, Any] = {"action": act}
    if act == "extend":
        if isinstance(minutes, bool):
            minutes = None
        try:
            mins = int(str(minutes).strip()) if minutes not in (None, "") else 0
        except (TypeError, ValueError):
            raise RadiusValidationError("عدد الدقائق يجب أن يكون رقمًا صحيحًا.") from None
        if mins <= 0:
            raise RadiusValidationError("أدخل عدد الدقائق المراد إضافتها (أكبر من صفر).")
        if mins > limits.max_extend_minutes():
            # F03 N4: the owner's per-operation rule, up front (422) — the
            # preview listed +416 days as «ok» and the real run then failed
            # every user one by one.
            raise RadiusValidationError(limits.extend_too_long_msg())
        params["minutes"] = mins
    if act == "reset_password":
        pw = "" if new_password is None else str(new_password)
        if not pw.strip():
            raise RadiusValidationError("كلمة المرور الجديدة مطلوبة.")
        from .users import validate_new_password
        validate_new_password(pw)
        params["new_password"] = pw
    return params


def _lookup(tenant_id: int, usernames: list[str]) -> dict[str, dict]:
    from ..db.connection import db
    found: dict[str, dict] = {}
    for i in range(0, len(usernames), 400):
        chunk = usernames[i:i + 400]
        marks = ",".join("?" for _ in chunk)
        rows = db().execute(
            f"SELECT username, status, expire_at FROM subscribers "
            f"WHERE tenant_id = ? AND deleted_at IS NULL AND username IN ({marks})",
            (int(tenant_id), *chunk)).fetchall()
        for r in rows:
            found[r["username"]] = dict(r)
    return found


def plan(tenant_id: int, usernames: list[str], params: dict) -> dict:
    """The dry run: what the real run would do for each username. Writes
    nothing."""
    from ..db.helpers import parse_dt
    act = params["action"]
    rows = _lookup(tenant_id, usernames)
    now = datetime.utcnow()
    items: list[dict] = []
    for name in usernames:
        row = rows.get(name)
        if row is None:
            items.append({"username": name, "ok": False, "status": "not_found",
                          "error": "المستخدم غير موجود."})
            continue
        item: dict[str, Any] = {"username": name, "ok": True, "status": "ok",
                                "current_status": row.get("status")}
        if act == "disable" and row.get("status") == "disabled":
            item["status"] = "no_change"
            item["note"] = "معطّل مسبقًا."
        elif act == "enable" and row.get("status") == "enabled":
            item["status"] = "no_change"
            item["note"] = "مفعّل مسبقًا."
        elif act == "extend":
            cur = parse_dt(row.get("expire_at")) if row.get("expire_at") else None
            if cur is not None and cur.tzinfo is not None:
                cur = cur.replace(tzinfo=None)
            anchor = max(cur, now) if cur else now  # same anchor as extend_time
            # ISO-8601 UTC with «Z» like every other API timestamp (re-test
            # R07 N8); the web preview converts it to the panel's local time.
            from ..core.strict_input import iso_utc_z
            item["old_expire_at"] = iso_utc_z(cur) if cur else None
            new_exp = anchor + timedelta(minutes=params["minutes"])
            if new_exp >= limits.expiry_limit():
                # same expiry cap as the real run (extend_time → add_minutes_capped)
                item.update({"ok": False, "status": "refused",
                             "error": limits.expiry_too_far_msg()})
            else:
                item["new_expire_at"] = iso_utc_z(new_exp)
        items.append(item)
    ok_count = sum(1 for i in items if i["ok"])
    return {
        "dry_run": True,
        "action": params["action"],
        "action_label": _ACTION_LABELS[act],
        "targets": [i["username"] for i in items if i["ok"]],
        "not_found": [i["username"] for i in items if i["status"] == "not_found"],
        "refused": [i["username"] for i in items if i["status"] == "refused"],
        "would_succeed": ok_count,
        "would_fail": len(items) - ok_count,
        "success": 0,
        "failed": 0,
        "items": items,
    }


def _error_text(exc: Exception) -> str:
    if isinstance(exc, RadiusNotFound):
        return "المستخدم غير موجود."
    if isinstance(exc, RadiusError):
        return getattr(exc, "message", None) or str(exc)
    return "تعذّر تنفيذ الإجراء على هذا المستخدم."


def run(usernames: list[str], params: dict, *, actor: str) -> dict:
    """Execute on every username; one failure never stops the batch."""
    from .users import get_users_service
    svc = get_users_service()
    act = params["action"]
    items: list[dict] = []
    success = failed = 0
    for name in usernames:
        try:
            if act == "disable":
                svc.disable(actor=actor, username=name)
            elif act == "enable":
                svc.enable(actor=actor, username=name)
            elif act == "extend":
                svc.extend_time(actor=actor, username=name, minutes=params["minutes"])
            elif act == "reset_password":
                svc.reset_password(actor=actor, username=name,
                                   new_password=params["new_password"])
            success += 1
            items.append({"username": name, "ok": True})
        except Exception as exc:  # noqa: BLE001 — per-user outcome, batch continues
            failed += 1
            items.append({"username": name, "ok": False, "error": _error_text(exc)})
    return {
        "dry_run": False,
        "action": act,
        "action_label": _ACTION_LABELS[act],
        "success": success,
        "failed": failed,
        "items": items,
    }
