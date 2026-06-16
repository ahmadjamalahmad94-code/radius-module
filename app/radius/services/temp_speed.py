"""Temporary per-session speed control with LIVE apply + auto-revert.

The legacy temp-speed flow (set on the subscriber form) only wrote DB flags:
the throttle took effect on the next Access-Accept, and expiry was *lazy* —
``_expire_temporary_speeds`` in :mod:`app.radius.routes.sessions` cleared the
flag only when somebody opened the «المتصلون الآن» page, and it pushed **no
CoA**. So a live session was never throttled immediately, and never restored
at expiry until the user re-authenticated.

This module closes both gaps and is the single source of truth for temp speed:

* :func:`apply_temp_speed` — persist the window AND immediately push a rate-CoA
  so the active session is throttled within seconds (reusing the proven
  ``change_user_rate`` path), with the prior rate stored for an exact revert.
* :func:`expire_due_temp_speeds` — push a *revert* CoA restoring the normal
  (override-or-plan) rate the moment the window ends, then clear the flags.
  Called BOTH by the background worker (immediate, every few seconds) and by
  the online page's on-load sweep, so neither can clear the flag without also
  restoring the live session.

All functions take an explicit ``tenant_id`` (no request/session context) so the
worker can call them from its own thread. Tenant isolation is enforced on every
query.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from ..db.connection import db

_LOG = logging.getLogger(__name__)

# Metadata keys (stored top-level on subscribers.metadata JSON). The online page
# reader (`_meta_value`) checks both `advanced` and top-level, so top-level is
# visible to the existing countdown UI.
_K_FROM = "temporary_speed_from"
_K_TO = "temporary_speed_to"
_K_DURATION = "temporary_speed_duration_minutes"
_K_RESTORE_RATE = "temporary_speed_restore_rate"
_K_PREV_BWCTRL = "temporary_speed_prev_bwctrl"
_K_PREV_DOWN = "temporary_speed_prev_down_kbps"
_K_PREV_UP = "temporary_speed_prev_up_kbps"
_K_PREV_CUSTOM = "temporary_speed_prev_custom"
# #50a: explicit "temp active" flag, written alongside the DB temporary_speed
# column. The readers no longer INFER active-ness from timestamp arithmetic;
# a window is active iff the column/flag is set AND its strict end is future.
_K_ACTIVE = "temporary_speed_active"

_TEMP_META_KEYS = (
    _K_FROM, _K_TO, _K_DURATION, _K_RESTORE_RATE,
    _K_PREV_BWCTRL, _K_PREV_DOWN, _K_PREV_UP, _K_PREV_CUSTOM, _K_ACTIVE,
)

# Guardrails on operator input.
_MIN_KBPS = 64           # never throttle below 64k — that's effectively a cut
_MAX_KBPS = 1_000_000    # 1 Gbit ceiling — sanity bound
_MIN_MINUTES = 1
_MAX_MINUTES = 24 * 60   # a temp window longer than a day is almost certainly a mistake


def _utcnow() -> datetime:
    return datetime.utcnow()


def _parse_meta(raw: Any) -> dict:
    try:
        data = json.loads(raw or "{}") or {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _meta_value(meta: dict, key: str) -> str:
    advanced = meta.get("advanced") if isinstance(meta.get("advanced"), dict) else {}
    return str(advanced.get(key) or meta.get(key) or "").strip()


def _int_or_zero(raw: Any) -> int:
    try:
        return int(float(str(raw or "").strip()))
    except (TypeError, ValueError):
        return 0


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1]
    try:
        return datetime.fromisoformat(value).replace(tzinfo=None)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt)
            except ValueError:
                continue
    return None


def _ends_at(meta: dict, updated_at: Any = None) -> datetime | None:
    """Window end computed STRICTLY from the temp-speed window fields.

    #50a: NEVER fall back to ``updated_at``. The old fallback meant that any
    later ``UPDATE subscribers ... SET updated_at = now`` (e.g. a routine
    profile re-save, a quota top-up, a balance change) silently slid the
    apparent window-end forward/backward and could make a window look expired
    seconds after it was applied. The window is now derived only from the
    explicit ``temporary_speed_from`` + ``temporary_speed_duration_minutes``
    (or an explicit ``temporary_speed_to``). ``updated_at`` is accepted but
    ignored for signature compatibility with callers.
    """
    started = _parse_dt(_meta_value(meta, _K_FROM))
    ends = _parse_dt(_meta_value(meta, _K_TO))
    duration = _int_or_zero(_meta_value(meta, _K_DURATION))
    if not ends and started and duration > 0:
        ends = started + timedelta(minutes=duration)
    return ends


def _rate_str(up_kbps: int, down_kbps: int) -> str:
    return f"{int(up_kbps)}k/{int(down_kbps)}k"


def _plan_rate(tenant_id: int, plan_id: int | None) -> str:
    """The plan's Mikrotik-Rate-Limit string, or '' when the plan sets none."""
    if not plan_id:
        return ""
    try:
        from ..db.repos import plans_repo
        plan = plans_repo.get_plan(tenant_id, int(plan_id))
    except Exception:  # noqa: BLE001
        return ""
    if not plan:
        return ""
    burst = getattr(plan, "burst_raw", "") or ""
    if burst:
        return str(burst)
    up = int(getattr(plan, "speed_up_kbps", 0) or 0)
    down = int(getattr(plan, "speed_down_kbps", 0) or 0)
    return _rate_str(up, down) if (up or down) else ""


def _push_rate(tenant_id: int, username: str, rate: str):
    """Send a rate-CoA to every live session for the user. Never raises."""
    try:
        from ..integration.radius_coa import change_user_rate
        return change_user_rate(tenant_id, username, new_rate_limit=rate)
    except Exception as exc:  # noqa: BLE001 — CoA must never break the caller
        _LOG.warning("temp-speed CoA failed for %s rate=%s: %s", username, rate, exc)
        return None


def _coa_summary(result) -> dict:
    if result is None:
        return {"ok": False, "code": "exception"}
    return {"ok": bool(getattr(result, "ok", False)),
            "code": getattr(result, "code_name", "")}


def apply_temp_speed(
    *,
    tenant_id: int,
    actor: str,
    username: str,
    down_kbps: int,
    up_kbps: int,
    duration_minutes: int,
    reset_window: bool = True,
    now: datetime | None = None,
) -> dict:
    """Apply a temporary throttle to ``username`` and push it LIVE immediately.

    ``reset_window=False`` preserves an already-running countdown (used by the
    profile/edit save so re-saving the subscriber never restarts the timer);
    ``True`` (the online-page default) always opens a fresh window.

    Persists the temp window + the prior rate (for an exact revert), writes the
    throttle into the speed columns (so a re-auth inside the window keeps it),
    then fires a rate-CoA so the active session is throttled within seconds.

    Returns ``{"ends_at", "rate", "coa": {...}}``. Raises ``ValueError`` on bad
    input (the route maps it to a flash).
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("اسم المستخدم مطلوب")
    down_kbps, up_kbps = int(down_kbps or 0), int(up_kbps or 0)
    duration_minutes = int(duration_minutes or 0)
    # «0 = غير محدود» على أي اتجاه (يطابق المرجع): MikroTik يعامل 0 كـ unlimited
    # في الـ simple queue. أي قيمة موجبة أقل من الحد الأدنى تُرفض (أقل من 64k
    # يساوي عمليًا قطع الخدمة). قيمة سالبة تُرفض أيضًا عبر الشرط نفسه.
    if (down_kbps and down_kbps < _MIN_KBPS) or (up_kbps and up_kbps < _MIN_KBPS):
        raise ValueError(f"السرعة يجب أن تكون 0 (غير محدود) أو {_MIN_KBPS} كيلوبت فأكثر")
    if down_kbps > _MAX_KBPS or up_kbps > _MAX_KBPS:
        raise ValueError("السرعة المدخلة كبيرة جدًا")
    if duration_minutes < _MIN_MINUTES or duration_minutes > _MAX_MINUTES:
        raise ValueError(f"المدة يجب أن تكون بين {_MIN_MINUTES} و{_MAX_MINUTES} دقيقة")

    now = now or _utcnow()
    row = db().execute(
        """
        SELECT id, plan_id, custom_speed, bandwidth_control_enabled,
               download_speed_kbps, upload_speed_kbps, metadata
          FROM subscribers
         WHERE tenant_id = ? AND username = ?
         LIMIT 1
        """,
        (int(tenant_id), username),
    ).fetchone()
    if not row:
        raise ValueError("المشترك غير موجود")

    meta = _parse_meta(row["metadata"])
    prev_custom = bool(row["custom_speed"])
    prev_bwctrl = bool(row["bandwidth_control_enabled"])
    prev_down = int(row["download_speed_kbps"] or 0)
    prev_up = int(row["upload_speed_kbps"] or 0)

    # Restore target: a pre-existing permanent override wins, else the plan rate.
    # Don't re-capture prev_* if a temp window is already running (avoid stamping
    # the *throttle* as the thing to restore to on a re-apply).
    already_temp = _meta_value(meta, _K_RESTORE_RATE) != "" or _meta_value(meta, _K_FROM) != ""
    if not already_temp:
        if prev_custom and (prev_down or prev_up):
            restore_rate = _rate_str(prev_up, prev_down)
        else:
            restore_rate = _plan_rate(tenant_id, row["plan_id"])
        meta[_K_PREV_BWCTRL] = int(prev_bwctrl)
        meta[_K_PREV_DOWN] = prev_down
        meta[_K_PREV_UP] = prev_up
        meta[_K_PREV_CUSTOM] = int(prev_custom)
        meta[_K_RESTORE_RATE] = restore_rate

    existing_to = _parse_dt(_meta_value(meta, _K_TO))
    if reset_window or not (existing_to and existing_to > now):
        ends = now + timedelta(minutes=duration_minutes)
        meta[_K_FROM] = now.isoformat(timespec="seconds")
        meta[_K_TO] = ends.isoformat(timespec="seconds")
        meta[_K_DURATION] = duration_minutes
    else:
        # Keep the running countdown intact (profile re-save path).
        meta.setdefault(_K_FROM, now.isoformat(timespec="seconds"))
        meta.setdefault(_K_DURATION, duration_minutes)
    # #50a: explicit active flag — the single source of truth the readers use,
    # so a window is never "active by timestamp inference" alone.
    meta[_K_ACTIVE] = 1

    rate = _rate_str(up_kbps, down_kbps)
    # #50a: push the rate-limit to MikroTik BEFORE committing the DB so the
    # live session is actually throttled the moment we record the window. If
    # the CoA fails we still commit (the throttle is applied on next auth and
    # the worker keeps the window), but the live push is attempted first.
    coa = _push_rate(tenant_id, username, rate)

    db().execute(
        """
        UPDATE subscribers
           SET temporary_speed = 1,
               bandwidth_control_enabled = 1,
               download_speed_kbps = ?,
               upload_speed_kbps = ?,
               metadata = ?,
               updated_at = ?
         WHERE tenant_id = ? AND id = ?
        """,
        (down_kbps, up_kbps, json.dumps(meta, ensure_ascii=False),
         now.isoformat(timespec="seconds"), int(tenant_id), int(row["id"])),
    )
    db().commit()

    try:
        from .audit import get_audit_service
        get_audit_service().record(
            actor=actor or "anonymous",
            action="temporary_speed.apply",
            target_type="session",
            target_id=username,
            payload={"rate": rate, "duration_minutes": duration_minutes,
                     "ends_at": meta[_K_TO]},
            result_status=_coa_summary(coa).get("code") or "",
        )
    except Exception:  # noqa: BLE001 — audit must never break the action
        _LOG.exception("temp-speed apply audit failed for %s", username)

    # تنبيه إدارة (تلجرام) — محصّن، لا يكسر العملية.
    try:
        from .admin_alerts import dispatch
        dispatch(int(tenant_id), "speed_boost", {
            "username": username, "down": down_kbps, "up": up_kbps,
            "duration": duration_minutes, "ends_at": meta.get(_K_TO) or "—",
            "actor": actor or "—",
        }, dedup_key=f"{username}:{meta.get(_K_TO)}")
    except Exception:  # noqa: BLE001
        pass

    return {"ends_at": meta[_K_TO], "rate": rate, "coa": _coa_summary(coa)}


def _revert_one(tenant_id: int, row: Any, now: datetime, *, actor: str) -> bool:
    """Push the revert CoA + clear the temp window for one subscriber row.

    Two shapes are handled: windows applied by :func:`apply_temp_speed` carry a
    ``prev_*`` snapshot we restore exactly; *legacy* windows (set via the old
    subscriber form, no snapshot) fall back to the original expiry semantics —
    a permanent ``custom_speed`` override is left untouched and we restore to
    it, while a temp-only override is cleared to the plan default.
    """
    meta = _parse_meta(row["metadata"])
    username = row["username"]
    cur_custom = bool(row["custom_speed"])
    cur_down = int(row["download_speed_kbps"] or 0)
    cur_up = int(row["upload_speed_kbps"] or 0)
    has_snapshot = (_meta_value(meta, _K_RESTORE_RATE) != "") or (_K_PREV_CUSTOM in meta)

    if has_snapshot:
        prev_custom = bool(_int_or_zero(meta.get(_K_PREV_CUSTOM)))
        if prev_custom:
            new_bwctrl = _int_or_zero(meta.get(_K_PREV_BWCTRL)) or 1
            new_down = _int_or_zero(meta.get(_K_PREV_DOWN))
            new_up = _int_or_zero(meta.get(_K_PREV_UP))
        else:
            new_bwctrl, new_down, new_up = 0, 0, 0
        restore_rate = _meta_value(meta, _K_RESTORE_RATE) or _plan_rate(tenant_id, row["plan_id"])
    elif cur_custom:
        # Legacy window over a PERMANENT override — keep the override columns,
        # restore to them; never wipe a custom speed we didn't set.
        new_bwctrl, new_down, new_up = 1, cur_down, cur_up
        restore_rate = (_rate_str(cur_up, cur_down) if (cur_up or cur_down)
                        else _plan_rate(tenant_id, row["plan_id"]))
    else:
        # Legacy temp-only window — clear the throttle to the plan default.
        new_bwctrl, new_down, new_up = 0, 0, 0
        restore_rate = _plan_rate(tenant_id, row["plan_id"])

    coa = _push_rate(tenant_id, username, restore_rate) if restore_rate else None

    for key in _TEMP_META_KEYS:
        meta.pop(key, None)
    # Purge any stale copies the profile form's metadata grouper left under
    # `advanced` (older saves mirrored the window/speeds there). They are NOT
    # the source of truth — the top-level keys + speed columns are — but if left
    # behind they shadow the authoritative values on the edit page. Clear both.
    adv = meta.get("advanced")
    if isinstance(adv, dict):
        for key in (*_TEMP_META_KEYS,
                    "temporary_download_speed_kbps", "temporary_upload_speed_kbps"):
            adv.pop(key, None)

    db().execute(
        """
        UPDATE subscribers
           SET temporary_speed = 0,
               bandwidth_control_enabled = ?,
               download_speed_kbps = ?,
               upload_speed_kbps = ?,
               metadata = ?,
               updated_at = ?
         WHERE tenant_id = ? AND id = ?
        """,
        (int(new_bwctrl), int(new_down), int(new_up),
         json.dumps(meta, ensure_ascii=False),
         now.isoformat(timespec="seconds"), int(tenant_id), int(row["id"])),
    )

    try:
        from .audit import get_audit_service
        get_audit_service().record(
            actor=actor,
            action="temporary_speed.revert",
            target_type="session",
            target_id=username,
            payload={"restore_rate": restore_rate},
            result_status=_coa_summary(coa).get("code") or "",
        )
    except Exception:  # noqa: BLE001
        _LOG.exception("temp-speed revert audit failed for %s", username)
    return True


def expire_due_temp_speeds(
    *, tenant_id: int, now: datetime | None = None, actor: str = "system:temp-speed",
) -> int:
    """Revert every temp window for ``tenant_id`` whose end time has passed.

    Pushes a restore CoA to the live session (immediate revert) and clears the
    flags. Idempotent and safe to call from both the worker and the page sweep.
    Returns the number of windows reverted.
    """
    now = now or _utcnow()
    rows = db().execute(
        """
        SELECT id, username, plan_id, custom_speed, bandwidth_control_enabled,
               download_speed_kbps, upload_speed_kbps, metadata, updated_at
          FROM subscribers
         WHERE tenant_id = ? AND temporary_speed = 1
        """,
        (int(tenant_id),),
    ).fetchall()

    reverted = 0
    for row in rows:
        ends = _ends_at(_parse_meta(row["metadata"]), row["updated_at"])
        # Revert when the window has ended OR when the row is an ORPHAN: flagged
        # temporary_speed=1 but with NO computable window (``ends is None``).
        # Orphans come from legacy rows set before this service existed, a
        # half-written state, or metadata wiped by an unrelated path. They would
        # otherwise stay throttled forever and render the broken edit-page UI
        # ("لا يوجد وقت انتهاء محفوظ" / 00:00 / empty speeds), so we treat
        # "flagged but no end" as expired and restore the normal rate now.
        if ends is None or ends <= now:
            try:
                if _revert_one(tenant_id, row, now, actor=actor):
                    reverted += 1
            except Exception:  # noqa: BLE001 — one bad row must not stall the sweep
                _LOG.exception("temp-speed revert failed for id=%s", row["id"])
    if reverted:
        db().commit()
    return reverted


def cancel_temp_speed(
    *, tenant_id: int, actor: str, username: str, now: datetime | None = None,
) -> dict:
    """Cancel a temp window NOW (before its natural expiry) — the same revert
    used at expiry: push a restore CoA to the live session and clear the window.

    Shared by the online-page cancel button AND the profile/edit "Cancel
    temporary speed" path, so a window opened in either place is cancellable
    from the other with identical behaviour. No-op (``reverted=False``) when the
    subscriber has no temp window. Tenant-scoped.
    """
    username = (username or "").strip()
    now = now or _utcnow()
    row = db().execute(
        """
        SELECT id, username, temporary_speed, plan_id, custom_speed,
               bandwidth_control_enabled, download_speed_kbps, upload_speed_kbps,
               metadata, updated_at
          FROM subscribers
         WHERE tenant_id = ? AND username = ?
         LIMIT 1
        """,
        (int(tenant_id), username),
    ).fetchone()
    if not row:
        return {"reverted": False, "reason": "not_found"}
    meta = _parse_meta(row["metadata"])
    has_window = (bool(row["temporary_speed"]) or _meta_value(meta, _K_FROM)
                  or _meta_value(meta, _K_RESTORE_RATE))
    if not has_window:
        return {"reverted": False, "reason": "no_temp_window"}
    _revert_one(tenant_id, row, now, actor=actor)
    db().commit()
    return {"reverted": True}


__all__ = ["apply_temp_speed", "cancel_temp_speed", "expire_due_temp_speeds"]
