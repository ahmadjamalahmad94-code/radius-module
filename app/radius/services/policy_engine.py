"""
Policy Engine — قرارات Accept/Reject الديناميكية لـ RADIUS auth.

يُستدعى من /api/internal/auth (الذي يُناديه FreeRADIUS rlm_rest module).

ترتيب الفحوصات (يفشل عند أول خرق):
1. وجود الـ subscriber
2. password صحيحة
3. status = enabled
4. لم تنتهِ الصلاحية
5. ضمن ساعات الدوام (إن حُدِّدت)
6. ضمن أيام الدوام
7. الكوتا لم تنفد
8. MAC binding (إن وُجد)
9. عدد الجلسات المتزامنة < الحد

يُرجع AuthDecision:
- ok=True → reply attrs (rate-limit, session-timeout, ...)
- ok=False → reason + message للمستخدم
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core.types import AccessPlan, Subscriber
from ..db.connection import db
from ..db.repos import plans_repo, subscribers_repo

_LOG = logging.getLogger(__name__)


@dataclass
class AuthRequest:
    username: str
    password: str
    tenant_id: int = 1
    calling_station_id: str = ""   # MAC العميل
    called_station_id: str = ""    # MAC الـ NAS / SSID
    nas_ip: str = ""
    nas_port_type: str = ""


@dataclass
class AuthDecision:
    ok: bool
    reason: str = ""                # internal code: password_wrong / disabled / expired / ...
    message: str = ""               # نص عربي للمستخدم
    reply_attrs: dict = field(default_factory=dict)


# الرسائل العربية (Reply-Message)
_MSG = {
    "user_not_found":    "اسم المستخدم غير موجود",
    "password_wrong":    "كلمة المرور غير صحيحة",
    "disabled":          "الحساب معطَّل — راجع الإدارة",
    "expired":           "انتهت صلاحية الاشتراك",
    "outside_hours":     "خارج أوقات الدوام المسموحة",
    "outside_days":      "خارج أيام الدوام المسموحة",
    "quota_exhausted":   "نفدت الكوتا — يلزم تجديد",
    "mac_mismatch":      "هذا الجهاز غير مصرَّح بالدخول لهذا الحساب",
    "concurrent_limit":  "تجاوزت الحد الأقصى للجلسات المتزامنة",
    "ok_welcome":        "أهلًا بك",
    "ok_expires_soon":   "اشتراكك ينتهي قريبًا — جدّد قبل الانقطاع",
}


# ─────────────── الفحوصات ───────────────


def _check_password(sub: Subscriber, req: AuthRequest) -> Optional[AuthDecision]:
    """ملاحظة: نستخدم cleartext compare لأن FreeRADIUS يخزّن cleartext في DB.
    لو في المستقبل خزّنا hashed → نُغيّر هنا فقط.
    """
    if sub.password != req.password:
        return _reject("password_wrong")
    return None


def _check_status(sub: Subscriber) -> Optional[AuthDecision]:
    if sub.status == "enabled": return None
    if sub.status == "expired": return _reject("expired")
    return _reject("disabled")


def _check_expiration(sub: Subscriber) -> Optional[AuthDecision]:
    if sub.expire_at and sub.expire_at < datetime.utcnow():
        return _reject("expired")
    return None


def _check_hours(plan: Optional[AccessPlan], now: datetime) -> Optional[AuthDecision]:
    """يتحقّق من allowed_hours_from / allowed_hours_to ضمن نفس اليوم."""
    if not plan: return None
    if not plan.allowed_hours_from or not plan.allowed_hours_to:
        return None
    try:
        h_from = _parse_hm(plan.allowed_hours_from)
        h_to = _parse_hm(plan.allowed_hours_to)
    except ValueError:
        return None
    cur = (now.hour, now.minute)
    if h_from <= h_to:
        if not (h_from <= cur <= h_to):
            return _reject("outside_hours",
                           extra_message=f" ({plan.allowed_hours_from}-{plan.allowed_hours_to})")
    else:
        # نطاق يعبر منتصف الليل: مثل 22:00-06:00
        if not (cur >= h_from or cur <= h_to):
            return _reject("outside_hours",
                           extra_message=f" ({plan.allowed_hours_from}-{plan.allowed_hours_to})")
    return None


def _check_days(plan: Optional[AccessPlan], now: datetime) -> Optional[AuthDecision]:
    if not plan or not plan.allowed_days: return None
    day_map = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    today = day_map[now.weekday()]
    if today not in plan.allowed_days:
        return _reject("outside_days")
    return None


def _check_quota(sub: Subscriber, plan: Optional[AccessPlan]) -> Optional[AuthDecision]:
    if not plan or not plan.quota_total_mb: return None
    used_mb = (sub.used_bytes_in + sub.used_bytes_out) / 1_048_576
    if used_mb >= plan.quota_total_mb:
        return _reject("quota_exhausted")
    return None


def _check_mac(sub: Subscriber, req: AuthRequest) -> Optional[AuthDecision]:
    if not sub.mac_lock: return None
    incoming = (req.calling_station_id or "").upper().replace("-", ":")
    expected = sub.mac_lock.upper().replace("-", ":")
    if not incoming or incoming != expected:
        return _reject("mac_mismatch")
    return None


def _check_concurrent(sub: Subscriber, plan: Optional[AccessPlan]) -> Optional[AuthDecision]:
    limit = sub.override_concurrent or (plan.concurrent_sessions if plan else 0) or 0
    if limit <= 0: return None
    cur = db().execute("""
        SELECT COUNT(*) AS c FROM radacct
        WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL
    """, (sub.tenant_id, sub.username)).fetchone()
    active = cur["c"] if cur else 0
    if active >= limit:
        return _reject("concurrent_limit")
    return None


# ─────────────── المنفّذ الرئيسي ───────────────


def authorize(req: AuthRequest) -> AuthDecision:
    """يقرّر السماح/الرفض ويعيد attrs الـ Access-Accept."""
    if not req.username:
        return _reject("user_not_found")
    sub = subscribers_repo.get_subscriber(req.tenant_id, req.username)
    if not sub:
        return _reject("user_not_found")

    plan: Optional[AccessPlan] = None
    if sub.plan_id:
        try: plan = plans_repo.get_plan(req.tenant_id, sub.plan_id)
        except Exception: plan = None

    now = datetime.utcnow()

    for fn in (
        lambda: _check_password(sub, req),
        lambda: _check_status(sub),
        lambda: _check_expiration(sub),
        lambda: _check_hours(plan, now),
        lambda: _check_days(plan, now),
        lambda: _check_quota(sub, plan),
        lambda: _check_mac(sub, req),
        lambda: _check_concurrent(sub, plan),
    ):
        bad = fn()
        if bad is not None:
            _log_attempt(req, accepted=False, reason=bad.reason)
            return bad

    # ─ ✅ Accept ─
    reply = _build_accept_attrs(sub, plan)
    msg = _MSG["ok_welcome"]
    if sub.expire_at:
        days_left = (sub.expire_at - now).days
        if 0 <= days_left <= 3:
            msg = _MSG["ok_expires_soon"] + f" ({days_left} يوم)"
    reply["Reply-Message"] = msg
    _log_attempt(req, accepted=True)
    return AuthDecision(ok=True, message=msg, reply_attrs=reply)


def _build_accept_attrs(sub: Subscriber, plan: Optional[AccessPlan]) -> dict:
    out: dict = {}
    if plan:
        if plan.speed_down_kbps or plan.speed_up_kbps:
            rate = plan.burst_raw or f"{plan.speed_up_kbps}k/{plan.speed_down_kbps}k"
            out["Mikrotik-Rate-Limit"] = rate
        # Session-Timeout: استخدم الأقل بين plan + ما تبقى من expire
        timeout = plan.session_timeout_sec or 0
        if not timeout and plan.duration_minutes:
            timeout = plan.duration_minutes * 60
        if sub.expire_at:
            remaining = int((sub.expire_at - datetime.utcnow()).total_seconds())
            if remaining > 0:
                timeout = remaining if not timeout else min(timeout, remaining)
        if timeout > 0:
            out["Session-Timeout"] = str(timeout)
        if plan.idle_timeout_sec > 0:
            out["Idle-Timeout"] = str(plan.idle_timeout_sec)
        if plan.address_pool:
            out["Mikrotik-Address-List"] = plan.address_pool
    if sub.static_ip:
        out["Framed-IP-Address"] = sub.static_ip
    out["Acct-Interim-Interval"] = "60"
    return out


def _reject(reason: str, *, extra_message: str = "") -> AuthDecision:
    msg = _MSG.get(reason, "غير مصرَّح") + extra_message
    return AuthDecision(ok=False, reason=reason, message=msg,
                         reply_attrs={"Reply-Message": msg})


def _parse_hm(s: str) -> tuple[int, int]:
    s = s.strip()
    if ":" not in s:
        return (int(s), 0)
    h, m = s.split(":", 1)
    return (int(h), int(m))


def _log_attempt(req: AuthRequest, *, accepted: bool, reason: str = "") -> None:
    """يكتب في radpostauth."""
    try:
        from ..db.connection import transaction
        from ..db.helpers import now_iso
        reply = "Access-Accept" if accepted else "Access-Reject"
        with transaction() as conn:
            conn.execute("""
                INSERT INTO radpostauth(tenant_id, username, pass, reply, authdate, class, nas)
                VALUES(?,?,?,?,?,?,?)
            """, (req.tenant_id, req.username,
                  "***" if accepted else req.password,   # حماية: لا نسجّل password صحيحة
                  reply, now_iso(), reason, req.nas_ip))
    except Exception:  # noqa: BLE001
        _LOG.warning("radpostauth insert failed", exc_info=True)
