"""
mt_action_log — the WRITE side of the unified MikroTik-actions feed.

The reader (`mikrotik_actions.py`) unions audit_log + radpostauth +
sync_queue. Historically the live CoA dispatch path (set-speed / set-ip /
disconnect) returned a result that vanished — the outcome was flashed to the
UI and never persisted, so those actions could not appear in any log with a
router + success/fail. This module closes that gap: it records a normalized
`mt.coa.*` audit row at the dispatch site with result_status + router_id +
before/after + error, so the feed is complete going forward.

Fail-safe: recording never raises (the underlying audit service swallows its
own errors); a logging hiccup can never break a live control action.
"""
from __future__ import annotations

from typing import Any, Optional

from ..core.tenant import DEFAULT_TENANT_ID
from .audit import get_audit_service

# live-control action → normalized audit action key (mirrors the reader's
# classifier overrides so the row lands in the right section).
_ACTION_KEY = {
    "set_speed":  "mt.coa.set_speed",
    "set_ip":     "mt.coa.set_ip",
    "disconnect": "mt.coa.disconnect",
}


def _router_id_for_ip(tenant_id: int, nas_ip: str) -> Optional[int]:
    """Resolve a NAS ip → nas_devices.id so audit_log.router_id is populated
    (the reader prefers router_id, then a nas_ip in the payload)."""
    ip = (nas_ip or "").strip()
    if not ip:
        return None
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT id FROM nas_devices WHERE tenant_id = ? AND address = ? LIMIT 1",
            (tenant_id, ip)).fetchone()
        return int(row["id"]) if row else None
    except Exception:  # noqa: BLE001
        return None


def record_live_outcome(outcome: Any, *, actor: str, username: str,
                        tenant_id: Optional[int] = None,
                        target_type: str = "session",
                        reason: str = "",
                        before: Optional[dict] = None,
                        after: Optional[dict] = None) -> None:
    """Persist one live-CoA `LiveControlOutcome` to audit_log.

    `outcome` is a `live_session_control.LiveControlOutcome` (duck-typed:
    .ok, .action, .nas_ip, .session_id, .code_name, .reply_message, .detail).
    Router resolves from the outcome's nas_ip; result_status from .ok.
    `reason` (disconnect only) is stored in the payload for the feed."""
    try:
        tid = int(tenant_id) if tenant_id is not None else _current_tenant()
        action_raw = str(getattr(outcome, "action", "") or "")
        action = _ACTION_KEY.get(action_raw, f"mt.coa.{action_raw or 'action'}")
        ok = bool(getattr(outcome, "ok", False))
        nas_ip = str(getattr(outcome, "nas_ip", "") or "")
        err = ""
        if not ok:
            err = (str(getattr(outcome, "reply_message", "") or "")
                   or str(getattr(outcome, "code_name", "") or "")
                   or str(getattr(outcome, "detail", "") or ""))
        _sid = str(getattr(outcome, "session_id", "") or "")
        payload = {
            "nas_ip": nas_ip,
            "session_id": _sid,
            "sid": _sid,   # redaction-safe dedup key (see record_disconnect)
            "code_name": str(getattr(outcome, "code_name", "") or ""),
        }
        if reason:
            payload["reason"] = str(reason)
        get_audit_service().record(
            actor=actor or "system", action=action,
            target_type=target_type, target_id=str(username or ""),
            result_status="success" if ok else "failed",
            severity="info" if ok else "warning",
            router_id=_router_id_for_ip(tid, nas_ip),
            error_message=err,
            before=before or {}, after=after or {},
            payload=payload,
        )
    except Exception:  # noqa: BLE001 — audit must never break a live action
        pass


def _session_nas_ip(tenant_id: int, username: str) -> str:
    """The NAS ip of the user's most-recent OPEN radacct session, or '' when the
    user has no live session. Lets a speed-change row resolve the real router the
    live CoA was pushed to. Never raises."""
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT nasipaddress FROM radacct "
            "WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL "
            "ORDER BY radacctid DESC LIMIT 1",
            (int(tenant_id), str(username or "")),
        ).fetchone()
        return str(row["nasipaddress"] or "").strip() if row else ""
    except Exception:  # noqa: BLE001
        return ""


def record_speed_change(*, tenant_id: int, actor: str, username: str,
                        action: str, old_rate: str = "", new_rate: str = "",
                        ok: bool = True, nas_ip: str = "", note: str = "",
                        error: str = "", session_id: str = "") -> None:
    """Record ONE audit row for a subscriber speed change that feeds ALL THREE
    logs, because every one of them reads ``audit_log``:

      • MikroTik-actions feed  — ``action`` classifies to CAT_SPEED (see
        mikrotik_actions._ACTION_OVERRIDES), and the resolved ``router_id`` +
        before/after rate carry the real router and the «من X إلى Y» detail: this
        is the record of the actual CoA/command pushed to the NAS.
      • Manager audit log       — ``target_type='subscriber'`` + ``target_id``
        surface it in the per-subscriber «الأحداث الإداريّة» pane with the
        old→new diff (payload ``speed``), attributed to ``actor``.
      • Subscriber event timeline — same subscriber-targeted row shows in the
        «أحداث المشترك» timeline.

    Used by BOTH the manual/temporary speed path (``temp_speed``) and the
    automatic bandwidth-schedule worker (``bandwidth_apply``). Writes via
    ``audit_repo.record`` with an EXPLICIT ``tenant_id`` (no flask.g), so the
    background scheduler thread records against the right tenant. NEVER raises —
    an audit hiccup can never break a live speed change. No plaintext password is
    ever passed (rate strings only)."""
    try:
        from ..db.repos import audit_repo
        tid = int(tenant_id)
        ip = (nas_ip or "").strip() or _session_nas_ip(tid, username)
        # Pretty «X ميجا» for the human panes; the raw rate stays in before/after
        # for the MikroTik-actions feed's own formatter.
        try:
            from .mikrotik_actions import _fmt_speed_value
            frm = _fmt_speed_value(old_rate) or "غير معروف"
            to = _fmt_speed_value(new_rate) or ""
        except Exception:  # noqa: BLE001
            frm, to = (old_rate or "غير معروف"), (new_rate or "")
        payload: dict = {"speed": f"من {frm} إلى {to}" if to else frm}
        if note:
            payload["note"] = note
        if ip:
            payload["nas_ip"] = ip          # feed router fallback; hidden in panes
        if session_id:
            payload["sid"] = session_id
        audit_repo.record(
            tenant_id=tid, actor=actor or "system", action=action,
            target_type="subscriber", target_id=str(username or ""),
            result_status="success" if ok else "failed",
            severity="info" if ok else "warning",
            router_id=_router_id_for_ip(tid, ip),
            error_message=error or "",
            payload=payload,
            before={"rate_limit": old_rate} if old_rate else None,
            after={"rate_limit": new_rate} if new_rate else None,
        )
    except Exception:  # noqa: BLE001 — audit must never break a speed change
        pass


def record_disconnect(*, actor: str, username: str,
                      tenant_id: Optional[int] = None,
                      ok: bool, reason: str = "", nas_ip: str = "",
                      router_id: Optional[int] = None, error: str = "",
                      session_id: str = "") -> None:
    """Record a session disconnect (action="disconnect") with its REASON +
    result + router. Used by automated evictors (policy_reconciler /
    device-limit) and the manual path so every disconnect lands in the
    unified feed with a real outcome and a human reason. Fail-safe."""
    try:
        tid = int(tenant_id) if tenant_id is not None else _current_tenant()
        rid = router_id if router_id is not None else _router_id_for_ip(tid, nas_ip)
        # `sid` = redaction-safe copy of the acct session id (the audit repo masks
        # "session_id" because it contains the fragment "session"); the report
        # uses it to de-dup this row against the router's radacct Acct-Stop.
        payload = {"session_id": session_id or "", "sid": session_id or "",
                   "nas_ip": nas_ip or ""}
        if reason:
            payload["reason"] = str(reason)
        get_audit_service().record(
            actor=actor or "system", action="disconnect",
            target_type="session", target_id=str(username or ""),
            result_status="success" if ok else "failed",
            severity="info" if ok else "warning",
            router_id=rid, error_message=error or "",
            payload=payload,
        )
    except Exception:  # noqa: BLE001
        pass


def _current_tenant() -> int:
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except Exception:  # noqa: BLE001
        return DEFAULT_TENANT_ID
