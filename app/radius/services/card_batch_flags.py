"""
card_batch_flags — إنفاذ أعلام السلوك على CardBatch.

يُستدعى من:
  • policy_engine._update_login_timestamps()  → حدث أول دخول (auth accept)
  • accounting_events.AccountingEventsService._start()  → Accounting-Start
  • accounting_events.AccountingEventsService._stop()   → Accounting-Stop

كل دالة هنا محاطة بـ try/except — لا شيء يكسر مسار المصادقة أو المحاسبة.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

_LOG = logging.getLogger(__name__)


# ─────────────── helpers ───────────────

def _get_card_and_batch(tenant_id: int, username: str):
    """يُرجع (Card, CardBatch) أو (None, None) عند الفشل."""
    try:
        from ..db.repos import cards_repo
        card = cards_repo.get_card_by_username(tenant_id, username)
        if not card:
            return None, None
        batch = cards_repo.get_batch(tenant_id, card.batch_id)
        return card, batch
    except Exception:
        _LOG.debug("card_batch_flags: failed to load card/batch for %r",
                   username, exc_info=True)
        return None, None


def _utcnow() -> datetime:
    return datetime.utcnow()


def _list_check_as_tuples(tenant_id: int, username: str) -> list:
    """يُرجع radcheck الحالية كـ list of (attribute, op, value) tuples."""
    from ..db.repos import freeradius_repo
    rows = freeradius_repo.list_user_check(tenant_id, username)
    return [(r["attribute"], r["op"], r["value"]) for r in rows]


def _merge_mac_check(existing: list, mac: str) -> list:
    """يُدرج Calling-Station-Id == mac، يحذف أي قيد MAC قديم."""
    cleaned = [(a, op, v) for (a, op, v) in existing
               if a.lower() != "calling-station-id"]
    cleaned.append(("Calling-Station-Id", "==", mac))
    return cleaned


# ── FLAG 1 — validity_after_first_login_days ──────────────────────────────────

def apply_validity_after_first_login(tenant_id: int, username: str, *,
                                      was_first_login: bool) -> None:
    """يُعيَّن expire_at عند أول دخول إن كانت batch.validity_after_first_login_days > 0."""
    if not was_first_login:
        return
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch:
            return
        days = batch.validity_after_first_login_days
        if not days or days <= 0:
            return
        new_expire = _utcnow() + timedelta(days=days)
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute(
                "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
                (new_expire.isoformat(), tenant_id, card.id),
            )
            conn.execute(
                "UPDATE subscribers SET expire_at = ? WHERE tenant_id = ? AND username = ?",
                (new_expire.isoformat(), tenant_id, username),
            )
        _LOG.info("card_batch_flags: validity_after_first_login user=%r "
                  "expire_at=%s (+%d days)", username, new_expire.date(), days)
    except Exception:
        _LOG.warning("card_batch_flags: validity_after_first_login failed for %r",
                     username, exc_info=True)


# ── FLAG 2 — count_by_seconds ─────────────────────────────────────────────────

def check_time_limit_by_seconds(tenant_id: int, username: str, plan) -> Optional[str]:
    """يتحقّق من الوقت المتبقّي بالثواني.
    يُرجع None (مسموح) أو 'time_exhausted' (منتهي).
    يُستدعى من policy_engine بعد _check_quota.
    """
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.count_by_seconds:
            return None
        if not plan:
            return None
        total_sec = (plan.duration_minutes or 0) * 60
        if total_sec <= 0:
            return None
        from ..db.connection import db
        row = db().execute(
            "SELECT COALESCE(SUM(acctsessiontime), 0) AS used_sec "
            "FROM radacct WHERE tenant_id = ? AND username = ?",
            (tenant_id, username),
        ).fetchone()
        used_sec = int(row["used_sec"] if row else 0)
        if used_sec >= total_sec:
            return "time_exhausted"
        return None
    except Exception:
        _LOG.debug("card_batch_flags: count_by_seconds failed for %r",
                   username, exc_info=True)
        return None


# ── FLAG 3 — switch_to_mac_on_connect ────────────────────────────────────────

def apply_switch_to_mac_on_connect(tenant_id: int, username: str,
                                    calling_station_id: str) -> None:
    """يُسجّل MAC عند أول Accounting-Start ويضيف radcheck قفل MAC."""
    mac = (calling_station_id or "").strip().upper().replace("-", ":")
    if not mac:
        return
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.switch_to_mac_on_connect:
            return
        if card.locked_mac:
            return  # سبق القفل
        from ..db.repos import cards_repo, freeradius_repo
        cards_repo.set_card_locked_mac(
            tenant_id, card.id, mac, actor="auto:switch_to_mac_on_connect"
        )
        existing = _list_check_as_tuples(tenant_id, username)
        freeradius_repo.replace_user_check(
            tenant_id, username, _merge_mac_check(existing, mac)
        )
        _LOG.info("card_batch_flags: switch_to_mac_on_connect user=%r mac=%s", username, mac)
    except Exception:
        _LOG.warning("card_batch_flags: switch_to_mac_on_connect failed for %r",
                     username, exc_info=True)


# ── FLAG 4 — lock_to_mac_on_close ─────────────────────────────────────────────

def apply_lock_to_mac_on_close(tenant_id: int, username: str,
                                calling_station_id: str) -> None:
    """يُسجّل MAC عند Accounting-Stop إن لم يكن هناك locked_mac بعد."""
    mac = (calling_station_id or "").strip().upper().replace("-", ":")
    if not mac:
        return
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.lock_to_mac_on_close:
            return
        if card.locked_mac:
            return
        from ..db.repos import cards_repo, freeradius_repo
        cards_repo.set_card_locked_mac(
            tenant_id, card.id, mac, actor="auto:lock_to_mac_on_close"
        )
        existing = _list_check_as_tuples(tenant_id, username)
        freeradius_repo.replace_user_check(
            tenant_id, username, _merge_mac_check(existing, mac)
        )
        _LOG.info("card_batch_flags: lock_to_mac_on_close user=%r mac=%s", username, mac)
    except Exception:
        _LOG.warning("card_batch_flags: lock_to_mac_on_close failed for %r",
                     username, exc_info=True)


# ── FLAG 5 — phone_only_login ─────────────────────────────────────────────────

def check_phone_only_login(tenant_id: int, username: str,
                            calling_station_id: str) -> Optional[str]:
    """يتحقّق من قيد الجوال. يُرجع None (مسموح) أو 'phone_only_violation'.

    TODO: يحتاج خريطة mobile→MAC للتحقّق الكامل.
    حالياً يقارن locked_mac إن وُجد؛ يسمح بالدخول الأول.
    """
    if not calling_station_id:
        return None
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.phone_only_login:
            return None
        incoming = calling_station_id.strip().upper().replace("-", ":")
        if card.locked_mac:
            locked = card.locked_mac.strip().upper().replace("-", ":")
            if incoming != locked:
                return "phone_only_violation"
        # TODO: إضافة خريطة mobile→MAC لتحقّق من الجوال المُسجَّل
        return None
    except Exception:
        _LOG.debug("card_batch_flags: phone_only_login failed for %r",
                   username, exc_info=True)
        return None


# ── FLAG 6 — allow_entry_by_previous_card_palestine ──────────────────────────

def check_allow_previous_card_grace(tenant_id: int, username: str) -> bool:
    """يتحقّق من وجود بطاقة سابقة في نفس الـ batch للسماح بـ grace period.

    TODO: قيّد الوصول زمنياً أو أرسل إشعاراً عند ACCEPT.
    """
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.allow_entry_by_previous_card_palestine:
            return False
        if not card.used_by_subscriber_id:
            return False
        from ..db.connection import db
        row = db().execute("""
            SELECT id FROM cards
             WHERE tenant_id = ? AND batch_id = ? AND id != ?
               AND used_by_subscriber_id = ? AND used = 1
             ORDER BY id DESC LIMIT 1
        """, (tenant_id, card.batch_id, card.id,
              card.used_by_subscriber_id)).fetchone()
        if row:
            _LOG.info("card_batch_flags: allow_previous_card_grace user=%r allowed", username)
            return True
        return False
    except Exception:
        _LOG.debug("card_batch_flags: allow_previous_card_grace failed for %r",
                   username, exc_info=True)
        return False


# ── FLAG 7 — auto_renew_after_first_use ──────────────────────────────────────

def apply_auto_renew_after_first_use(tenant_id: int, username: str) -> None:
    """يُجدّد البطاقة تلقائياً عند انتهائها.

    TODO: ربطه بـ expiry_enforcer job للتجديد الدوري دون انتظار Acct-Stop.
    """
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.auto_renew_after_first_use:
            return
        now = _utcnow()
        if card.expire_at and card.expire_at > now:
            return  # لم تنتهِ بعد
        days = batch.validity_after_first_login_days or 0
        if days <= 0 and card.plan_id:
            from ..db.repos import plans_repo
            plan = plans_repo.get_plan(tenant_id, card.plan_id)
            if plan:
                days = plan.validity_days or 0
        if days <= 0:
            return
        new_expire = now + timedelta(days=days)
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute(
                "UPDATE cards SET expire_at = ?, used = 1 WHERE tenant_id = ? AND id = ?",
                (new_expire.isoformat(), tenant_id, card.id),
            )
            conn.execute(
                "UPDATE subscribers SET expire_at = ? WHERE tenant_id = ? AND username = ?",
                (new_expire.isoformat(), tenant_id, username),
            )
        _LOG.info("card_batch_flags: auto_renew_after_first_use user=%r new_expire=%s",
                  username, new_expire.date())
    except Exception:
        _LOG.warning("card_batch_flags: auto_renew_after_first_use failed for %r",
                     username, exc_info=True)


# ── FLAG 8 — transfer_to_student_status_on_connect ───────────────────────────

def apply_transfer_to_student_on_connect(tenant_id: int, username: str, *,
                                          was_first_login: bool) -> None:
    """يُحوّل account_type إلى 'student' عند أول دخول."""
    if not was_first_login:
        return
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.transfer_to_student_status_on_connect:
            return
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute(
                "UPDATE subscribers SET account_type = 'student' "
                "WHERE tenant_id = ? AND username = ?",
                (tenant_id, username),
            )
        _LOG.info("card_batch_flags: transfer_to_student user=%r done", username)
    except Exception:
        _LOG.warning("card_batch_flags: transfer_to_student failed for %r",
                     username, exc_info=True)


# ── FLAG 9 — close_user_session_on_disconnect ─────────────────────────────────

def apply_close_session_on_disconnect(tenant_id: int, username: str,
                                       current_session_id: str) -> None:
    """يُرسل CoA Disconnect لجلسات أخرى نشطة عند Accounting-Stop.

    TODO: تأكّد أن NAS يدعم Disconnect-Request (RFC 5176) قبل تفعيل الخيار.
    """
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch or not batch.close_user_session_on_disconnect:
            return
        from ..integration.radius_coa import disconnect_user, find_all_nas_for_sessions
        sessions = find_all_nas_for_sessions(tenant_id, username)
        other_ids = [s["session_id"] for s in sessions
                     if s["session_id"] != current_session_id]
        if not other_ids:
            return
        result = disconnect_user(tenant_id, username, session_ids=other_ids)
        _LOG.info("card_batch_flags: close_session_on_disconnect user=%r coa=%s",
                  username, result.code_name)
    except Exception:
        _LOG.warning("card_batch_flags: close_session_on_disconnect failed for %r",
                     username, exc_info=True)


# ── FLAG 10 — on_quota_exhaust ────────────────────────────────────────────────

_THROTTLE_RATE = "128k/128k"


def handle_quota_exhaust(tenant_id: int, username: str) -> None:
    """يُنفّذ سلوك on_quota_exhaust: stop (default) / reduce_speed / notify.

    stop       : الرفض يحدث في policy_engine بالفعل — لا إجراء إضافي.
    reduce_speed: CoA-Request لتقليل السرعة إلى _THROTTLE_RATE.
    notify     : حدث إشعار 'quota_exhausted'.
    """
    try:
        card, batch = _get_card_and_batch(tenant_id, username)
        if not card or not batch:
            return
        mode = (batch.on_quota_exhaust or "stop").strip().lower()

        if mode == "stop":
            return

        if mode == "reduce_speed":
            try:
                from ..integration.radius_coa import change_user_rate
                result = change_user_rate(
                    tenant_id, username, new_rate_limit=_THROTTLE_RATE
                )
                _LOG.info("card_batch_flags: quota_exhaust reduce_speed user=%r coa=%s",
                          username, result.code_name)
            except Exception:
                _LOG.warning("card_batch_flags: quota_exhaust reduce_speed failed for %r",
                             username, exc_info=True)
            return

        if mode == "notify":
            try:
                from .notifications_engine import notify_event
                notify_event(
                    "quota_exhausted",
                    tenant_id=tenant_id,
                    context={"username": username, "batch_id": card.batch_id},
                )
                _LOG.info("card_batch_flags: quota_exhaust notify fired user=%r", username)
            except Exception:
                _LOG.warning("card_batch_flags: quota_exhaust notify failed for %r",
                             username, exc_info=True)
            return

    except Exception:
        _LOG.warning("card_batch_flags: handle_quota_exhaust failed for %r",
                     username, exc_info=True)


# ── Public dispatch helpers (called from accounting_events) ───────────────────

def on_accounting_start(tenant_id: int, username: str,
                         calling_station_id: str, *,
                         is_first_use: bool) -> None:
    """يُطلق كل أعلام Accounting-Start. يُستدعى من accounting_events._start()."""
    if is_first_use:
        apply_switch_to_mac_on_connect(tenant_id, username, calling_station_id)
        apply_transfer_to_student_on_connect(tenant_id, username, was_first_login=True)
        apply_validity_after_first_login(tenant_id, username, was_first_login=True)


def on_accounting_stop(tenant_id: int, username: str,
                        calling_station_id: str, *,
                        session_id: str) -> None:
    """يُطلق كل أعلام Accounting-Stop. يُستدعى من accounting_events._stop()."""
    apply_lock_to_mac_on_close(tenant_id, username, calling_station_id)
    apply_close_session_on_disconnect(tenant_id, username, session_id)
    apply_auto_renew_after_first_use(tenant_id, username)
