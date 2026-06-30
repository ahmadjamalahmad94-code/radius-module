"""card_batch_flags — إنفاذ أعلام السلوك المخزَّنة على «دفعة البطاقات» (CardBatch).

تاريخيًّا كانت هذه الأعلام تُحفَظ في DB لكنها «ميتة» (لا أثر فعليّ). هذا الـ
module يُحوّلها إلى سلوك حقيقيّ، ويُستدعى من:
  • policy_engine.authorize / _update_login_timestamps  → عند المصادقة (Access-Accept)
  • accounting_events._stop                              → عند Accounting-Stop (فصل)

أعلام منفَّذة هنا:
  - on_quota_exhaust            (stop / reduce_speed / notify)
  - count_by_seconds            (رصيد ثواني استخدام — Mode A)
  - count_from_first_connect /
    validity_after_first_login_days  (تثبيت expire_at عند أول دخول — Mode B)
  - switch_to_mac_on_connect    (ربط MAC TOFU عند أول اتصال)
  - lock_to_mac_on_close        (قفل على آخر MAC عند الإغلاق)
  - transfer_to_student_status_on_connect (تحويل الحالة عند أول دخول)
  - close_user_session_on_disconnect       (CoA لإغلاق بقية الجلسات عند الفصل)
  - auto_renew_after_first_use  (تجديد البطاقة المنتهية تلقائيًّا بعد أول استخدام)

أعلام مُؤجَّلة (انظر التقرير) — phone_only_login و
allow_entry_by_previous_card_palestine: أُزيلت مفاتيحهما من واجهة التوليد
(#21) وتنتميان لطبقة البوّابة/قاعدة محلّية، لا لمستوى سياسة RADIUS في البيانات.

كل دالّة محصّنة بـ try/except — لا شيء هنا يَكسر مسار المصادقة أو المحاسبة
(fail-safe: أيّ خطأ داخليّ لا يَحجب مستخدمًا شرعيًّا ولا يُفشل تسجيل المحاسبة).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

_LOG = logging.getLogger(__name__)

# rate افتراضيّ للتخفيف عند on_quota_exhaust=reduce_speed (upload/download).
# قابل للتجاوز per-tenant عبر الإعداد "cards.quota_throttle_rate".
_DEFAULT_THROTTLE_RATE = "128k/128k"


# ─────────────── helpers ───────────────


def _get_card_and_batch(tenant_id: int, username: str):
    """يُرجع (Card, CardBatch) أو (None, None) عند الغياب/الفشل (آمن)."""
    try:
        from ..db.repos import cards_repo
        card = cards_repo.get_card_by_username(tenant_id, username)
        if not card:
            return None, None
        batch = cards_repo.get_batch(tenant_id, card.batch_id)
        return card, batch
    except Exception:  # noqa: BLE001
        _LOG.debug("card_batch_flags: load card/batch failed for %r",
                   username, exc_info=True)
        return None, None


def _norm_mac(mac: str) -> str:
    return (mac or "").strip().upper().replace("-", ":")


def _merge_mac_check(existing: list, mac: str) -> list:
    """يُدرج 'Calling-Station-Id == mac' بعد حذف أيّ قيد MAC قديم، مع إبقاء
    بقية صفوف radcheck (Cleartext-Password/Expiration/…) كما هي."""
    cleaned = [(a, op, v) for (a, op, v) in existing
               if a.lower() != "calling-station-id"]
    cleaned.append(("Calling-Station-Id", "==", mac))
    return cleaned


def _bind_card_to_mac(tenant_id: int, username: str, card_id: int, mac: str,
                      *, actor: str) -> None:
    """ربط البطاقة على MAC: cards.locked_mac + صفّ radcheck (نفس مسار القفل
    اليدويّ في مركز عمليات البطاقة — لا نُعيد اختراع آليّة)."""
    from ..db.repos import cards_repo, freeradius_repo
    cards_repo.set_card_locked_mac(tenant_id, card_id, mac, actor=actor)
    rows = freeradius_repo.list_user_check(tenant_id, username)
    existing = [(r["attribute"], r["op"], r["value"]) for r in rows]
    freeradius_repo.replace_user_check(
        tenant_id, username, _merge_mac_check(existing, mac))


def _card_budget_seconds(batch, plan) -> int:
    """رصيد الثواني الكلّيّ للبطاقة (Mode A). الباقة (duration_minutes) هي
    المصدر الأوّل المعتمد، ثمّ زمن الدفعة (time_value/time_unit). 0 = بلا رصيد."""
    if plan is not None and int(getattr(plan, "duration_minutes", 0) or 0) > 0:
        return int(plan.duration_minutes) * 60
    if batch is not None:
        tv = int(getattr(batch, "time_value", 0) or 0)
        if tv > 0:
            unit = (getattr(batch, "time_unit", "") or "days").lower()
            mult = {"minutes": 60, "hours": 3600, "days": 86400}.get(unit, 86400)
            return tv * mult
    return 0


def _accounted_seconds(tenant_id: int, username: str) -> int:
    """مجموع acctsessiontime من radacct (نفس عدّاد policy_engine لوقت الاتصال)."""
    from ..db.connection import db
    row = db().execute(
        "SELECT COALESCE(SUM(acctsessiontime), 0) AS s FROM radacct "
        "WHERE tenant_id = ? AND username = ?",
        (int(tenant_id), str(username)),
    ).fetchone()
    return int((row["s"] if row else 0) or 0)


# ─────────────── on_quota_exhaust (stop / reduce_speed / notify) ───────────────


def quota_exhaust_mode(tenant_id: int, username: str) -> str:
    """نمط سلوك نفاد الكوتا لبطاقة المستخدم: stop (الافتراض) / reduce_speed /
    notify. غير البطاقات أو غياب الدفعة → 'stop' (السلوك التاريخيّ: رفض)."""
    try:
        _card, batch = _get_card_and_batch(tenant_id, username)
        if not batch:
            return "stop"
        return (batch.on_quota_exhaust or "stop").strip().lower() or "stop"
    except Exception:  # noqa: BLE001
        return "stop"


def quota_throttle_rate(tenant_id: int) -> str:
    """rate التخفيف (reduce_speed) — per-tenant مع افتراض آمن."""
    try:
        from ..db.repos import tenants_repo
        val = tenants_repo.get_setting(
            int(tenant_id), "cards.quota_throttle_rate", _DEFAULT_THROTTLE_RATE)
        return (val or _DEFAULT_THROTTLE_RATE).strip() or _DEFAULT_THROTTLE_RATE
    except Exception:  # noqa: BLE001
        return _DEFAULT_THROTTLE_RATE


def fire_quota_exhaust_notify(tenant_id: int, username: str) -> None:
    """حدث إشعار 'quota_exhausted' (نمط notify). محصّن — لا يرفع أبدًا."""
    try:
        _card, batch = _get_card_and_batch(tenant_id, username)
        from .notifications_engine import notify_event
        notify_event(
            "quota_exhausted",
            tenant_id=int(tenant_id),
            context={"username": username,
                     "batch_id": getattr(_card, "batch_id", None)},
        )
        _LOG.info("card_batch_flags: quota_exhaust notify fired user=%r", username)
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: quota_exhaust notify failed for %r",
                     username, exc_info=True)


# ─────────────── count_by_seconds (رصيد ثواني الاستخدام — Mode A) ───────────────


def check_card_time_budget(tenant_id: int, username: str,
                           plan) -> Optional[str]:
    """يُرجع 'card_time_exhausted' عند تجاوز رصيد الثواني، وإلّا None.
    يُفعَّل فقط حين batch.count_by_seconds=True ووجود رصيد موجب. محصّن: أيّ
    خطأ → None (سماح؛ لا نَحجب مستخدمًا بسبب خطأ داخليّ)."""
    try:
        _card, batch = _get_card_and_batch(tenant_id, username)
        if not batch or not batch.count_by_seconds:
            return None
        budget = _card_budget_seconds(batch, plan)
        if budget <= 0:
            return None
        used = _accounted_seconds(tenant_id, username)
        if used >= budget:
            return "card_time_exhausted"
        return None
    except Exception:  # noqa: BLE001
        _LOG.debug("card_batch_flags: count_by_seconds check failed for %r",
                   username, exc_info=True)
        return None


# ─────────────── أعلام «أول اتصال» (Access-Accept الأوّل) ───────────────


def _materialize_first_login_validity(tenant_id: int, username: str,
                                       card, batch, now: datetime) -> None:
    """Mode B: تثبيت expire_at = أول دخول + الصلاحية (أيام). مصدر الصلاحية:
    validity_after_first_login_days (الدفعة) ثمّ plan.validity_days. لا يُعيَّن
    إلّا حين count_from_first_connect مفعَّل ولم يكن للبطاقة expire_at بعد."""
    if not getattr(batch, "count_from_first_connect", False):
        return
    if card.expire_at:  # سبق التثبيت/التعيين — لا نَدوسه
        return
    days = int(getattr(batch, "validity_after_first_login_days", 0) or 0)
    if days <= 0 and card.plan_id:
        try:
            from ..db.repos import plans_repo
            plan = plans_repo.get_plan(tenant_id, card.plan_id)
            days = int(getattr(plan, "validity_days", 0) or 0) if plan else 0
        except Exception:  # noqa: BLE001
            days = 0
    if days <= 0:
        return
    new_expire = now + timedelta(days=days)
    from ..db.connection import transaction
    with transaction() as conn:
        conn.execute(
            "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
            (new_expire.isoformat(), tenant_id, card.id))
        conn.execute(
            "UPDATE subscribers SET expire_at = ? "
            "WHERE tenant_id = ? AND username = ?",
            (new_expire.isoformat(), tenant_id, username))
    _LOG.info("card_batch_flags: first-login validity user=%r expire_at=%s (+%dd)",
              username, new_expire.date(), days)


def on_first_connect(tenant_id: int, username: str,
                     calling_station_id: str) -> None:
    """يُطلق أعلام «أول دخول/اتصال» مرّة واحدة (يُستدعى من policy_engine حين
    يُكتشَف أوّل first_used_at للبطاقة). محصّن بالكامل."""
    card, batch = _get_card_and_batch(tenant_id, username)
    if not card or not batch:
        return
    now = datetime.utcnow()

    # 1) switch_to_mac_on_connect — ربط TOFU على أوّل MAC إن لم تكن مقفولة.
    try:
        if batch.switch_to_mac_on_connect and not card.locked_mac:
            mac = _norm_mac(calling_station_id)
            if mac:
                _bind_card_to_mac(tenant_id, username, card.id, mac,
                                  actor="auto:switch_to_mac_on_connect")
                _LOG.info("card_batch_flags: switch_to_mac_on_connect user=%r mac=%s",
                          username, mac)
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: switch_to_mac_on_connect failed for %r",
                     username, exc_info=True)

    # 2) transfer_to_student_status_on_connect — تحويل حالة الحساب.
    try:
        if batch.transfer_to_student_status_on_connect:
            from ..db.connection import transaction
            with transaction() as conn:
                conn.execute(
                    "UPDATE subscribers SET account_type = 'student' "
                    "WHERE tenant_id = ? AND username = ?",
                    (tenant_id, username))
            _LOG.info("card_batch_flags: transfer_to_student user=%r", username)
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: transfer_to_student failed for %r",
                     username, exc_info=True)

    # 3) count_from_first_connect / validity_after_first_login_days.
    try:
        _materialize_first_login_validity(tenant_id, username, card, batch, now)
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: first-login validity failed for %r",
                     username, exc_info=True)


# ─────────────── auto_renew_after_first_use ───────────────


def maybe_auto_renew(sub, plan, source: str):
    """يُجدّد بطاقةً منتهيةً سبق استخدامها حين auto_renew_after_first_use مفعَّل،
    قبل بوّابة الانتهاء. يُرجع Subscriber (مُحدَّث expire_at) أو نفس الكائن.

    fail-safe: أيّ خطأ → يُرجع sub كما هو (تُكمل البوّابة الطبيعيّة)."""
    if source != "card":
        return sub
    try:
        card, batch = _get_card_and_batch(sub.tenant_id, sub.username)
        if not card or not batch or not batch.auto_renew_after_first_use:
            return sub
        if not card.first_used_at:
            return sub  # «بعد أول استخدام» فقط
        now = datetime.utcnow()
        if card.expire_at and card.expire_at > now:
            return sub  # لم تنتهِ بعد
        days = int(getattr(batch, "validity_after_first_login_days", 0) or 0)
        if days <= 0 and card.plan_id:
            try:
                from ..db.repos import plans_repo
                p = plans_repo.get_plan(sub.tenant_id, card.plan_id)
                days = int(getattr(p, "validity_days", 0) or 0) if p else 0
            except Exception:  # noqa: BLE001
                days = 0
        if days <= 0:
            return sub
        new_expire = now + timedelta(days=days)
        from ..db.connection import transaction
        with transaction() as conn:
            conn.execute(
                "UPDATE cards SET expire_at = ?, used = 1 "
                "WHERE tenant_id = ? AND id = ?",
                (new_expire.isoformat(), sub.tenant_id, card.id))
            conn.execute(
                "UPDATE subscribers SET expire_at = ? "
                "WHERE tenant_id = ? AND username = ?",
                (new_expire.isoformat(), sub.tenant_id, sub.username))
        _LOG.info("card_batch_flags: auto_renew_after_first_use user=%r expire_at=%s",
                  sub.username, new_expire.date())
        import dataclasses
        return dataclasses.replace(sub, expire_at=new_expire, status="enabled")
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: auto_renew failed for %r",
                     sub.username, exc_info=True)
        return sub


# ─────────────── أعلام «الفصل» (Accounting-Stop) ───────────────


def on_disconnect(tenant_id: int, username: str, calling_station_id: str,
                  *, session_id: str) -> None:
    """يُطلق أعلام Accounting-Stop: lock_to_mac_on_close +
    close_user_session_on_disconnect. محصّن بالكامل."""
    card, batch = _get_card_and_batch(tenant_id, username)
    if not card or not batch:
        return

    # lock_to_mac_on_close — قفل على آخر MAC إن لم تكن مقفولة بعد.
    try:
        if batch.lock_to_mac_on_close and not card.locked_mac:
            mac = _norm_mac(calling_station_id)
            if mac:
                _bind_card_to_mac(tenant_id, username, card.id, mac,
                                  actor="auto:lock_to_mac_on_close")
                _LOG.info("card_batch_flags: lock_to_mac_on_close user=%r mac=%s",
                          username, mac)
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: lock_to_mac_on_close failed for %r",
                     username, exc_info=True)

    # close_user_session_on_disconnect — CoA Disconnect لبقية جلسات الاسم.
    try:
        if batch.close_user_session_on_disconnect:
            from ..integration.radius_coa import (
                disconnect_user, find_all_nas_for_sessions)
            sessions = find_all_nas_for_sessions(tenant_id, username)
            other = [s["session_id"] for s in sessions
                     if s.get("session_id") and s["session_id"] != session_id]
            if other:
                res = disconnect_user(tenant_id, username, session_ids=other)
                _LOG.info("card_batch_flags: close_session_on_disconnect user=%r "
                          "kicked=%d coa=%s", username, len(other),
                          getattr(res, "code_name", "?"))
    except Exception:  # noqa: BLE001
        _LOG.warning("card_batch_flags: close_session_on_disconnect failed for %r",
                     username, exc_info=True)
