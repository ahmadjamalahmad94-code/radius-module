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

import hashlib
import hmac as _hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from ..core.constants import USER_TYPE_CARD
from ..core.types import AccessPlan, Card, Subscriber
from ..db.connection import db
from ..db.repos import cards_repo, operations_repo, plans_repo, subscribers_repo

_LOG = logging.getLogger(__name__)


@dataclass
class AuthRequest:
    username: str
    password: str = ""                 # User-Password (cleartext PAP)
    chap_password: str = ""            # CHAP-Password hex (17 bytes = chap_id + MD5)
    chap_challenge: str = ""           # CHAP-Challenge hex (may be empty when NAS omits it)
    tenant_id: int = 1
    calling_station_id: str = ""       # MAC العميل
    called_station_id: str = ""        # MAC الـ NAS / SSID
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


def _hex_to_bytes(s: str) -> bytes:
    """Decode hex with optional 0x prefix. Returns b'' on malformed input."""
    s = (s or "").strip()
    if s[:2] in ("0x", "0X"):
        s = s[2:]
    if not s:
        return b""
    try:
        return bytes.fromhex(s)
    except ValueError:
        return b""


def _verify_chap(cleartext_password: str, chap_password_hex: str,
                 chap_challenge_hex: str) -> bool:
    """RFC 1994: CHAP-Password = MD5(CHAP-Id || cleartext-password || challenge)
    where the first byte is CHAP-Id and the next 16 are the MD5 digest.
    NAS clients (incl. MikroTik) may omit CHAP-Challenge — RFC says fall back
    to the Request Authenticator; we can't see it here, so treat empty as
    empty bytes. Most MikroTik hotspot configs do send CHAP-Challenge."""
    chap_bytes = _hex_to_bytes(chap_password_hex)
    if len(chap_bytes) != 17:
        return False
    chap_id = chap_bytes[:1]
    digest_recv = chap_bytes[1:]
    challenge = _hex_to_bytes(chap_challenge_hex)
    digest_calc = hashlib.md5(
        chap_id + cleartext_password.encode("utf-8") + challenge
    ).digest()
    return _hmac.compare_digest(digest_calc, digest_recv)


def _check_password(sub: Subscriber, req: AuthRequest) -> Optional[AuthDecision]:
    """يدعم PAP (User-Password cleartext) و CHAP (CHAP-Password + CHAP-Challenge).

    PAP: مقارنة نصّية مباشرة.
    CHAP: MD5(CHAP-Id || cleartext-password || CHAP-Challenge) ضد الـ digest المُستلم.

    ملاحظة: نخزّن cleartext في `subscribers.password` لأن MikroTik hotspot
    يطلب CHAP افتراضيًا — ولا يمكن التحقّق من CHAP إلا بالـ cleartext.
    لو حدث تخزين hashed مستقبلاً → نُحدّث هنا (PAP ممكن، CHAP يصبح مستحيلاً).
    """
    if req.password:
        if sub.password != req.password:
            return _reject("password_wrong")
        return None
    if req.chap_password:
        if not sub.password or not _verify_chap(
                sub.password, req.chap_password, req.chap_challenge):
            return _reject("password_wrong")
        return None
    # لا PAP ولا CHAP — الطلب ناقص. نُسجّل ونرفض كـ password_wrong.
    _LOG.warning("auth: no User-Password and no CHAP-Password for user=%r — "
                  "هل MikroTik يستخدم MS-CHAP/EAP غير المدعوم؟", req.username)
    return _reject("password_wrong")


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


def _card_to_subscriber(card: Card) -> Subscriber:
    """يُحوّل كارت إلى Subscriber DTO للمعالجة الموحَّدة في policy_engine.
    حقول subscriber غير الموجودة على الكارت تأخذ defaults آمنة (لا quota، لا
    bandwidth override، لا MAC lock — تأتي من الـ plan لو وُجدت).

    Migration 024 — Per-card speed override:
      عندما تكون قِيَم card_speed_down_kbps + card_speed_up_kbps > 0، نُمرّر
      الـ override عبر حقول Subscriber.bandwidth_control_enabled/_kbps،
      ليلتقطه الـ cascade الموجود في _build_accept_attrs (السطر الذي يفضّل
      sub.bandwidth_control قبل plan.speed_*). لا حاجة لتغيير الـ cascade
      نفسه — نستفيد من البنية القائمة.
    """
    has_speed_override = (card.card_speed_down_kbps > 0
                           and card.card_speed_up_kbps > 0)
    return Subscriber(
        id=card.id,
        tenant_id=card.tenant_id,
        username=card.username,
        password=card.password,
        user_type="card",
        plan_id=card.plan_id,
        card_batch_id=card.batch_id,
        status="disabled" if card.revoked else "enabled",
        expire_at=card.expire_at,
        # locked_mac إداري وصريح من مركز عمليات البطاقة. لا نستخدم used_by_mac
        # لأنه observational وقد يُلتقط تلقائياً من أول استخدام.
        mac_lock=card.locked_mac or None,
        # ── Per-card speed override (migration 024) ──
        bandwidth_control_enabled=has_speed_override,
        download_speed_kbps=card.card_speed_down_kbps if has_speed_override else 0,
        upload_speed_kbps=card.card_speed_up_kbps   if has_speed_override else 0,
    )


def authorize(req: AuthRequest) -> AuthDecision:
    """يقرّر السماح/الرفض ويعيد attrs الـ Access-Accept."""
    # ـ WARNING مؤقت للتشخيص: يُسجّل بدون أيّ password ـ
    _LOG.warning(
        "auth_attempt tenant=%d user=%r nas_ip=%s mac=%s pap=%s chap=%s",
        req.tenant_id, req.username, req.nas_ip, req.calling_station_id,
        "yes" if req.password else "no",
        "yes" if req.chap_password else "no",
    )
    if not req.username:
        _LOG.warning("auth_decision user='' reason=user_not_found")
        return _reject("user_not_found")

    sub = subscribers_repo.get_subscriber(req.tenant_id, req.username)
    source = "subscriber"
    if sub and (sub.user_type == USER_TYPE_CARD or sub.card_batch_id):
        card = cards_repo.get_card_by_username(req.tenant_id, req.username)
        if card:
            sub = _card_to_subscriber(card)
            source = "card"
    elif not sub:
        # ـ fallback: حاول إيجاد الـ username كـ كارت ـ
        card = cards_repo.get_card_by_username(req.tenant_id, req.username)
        if card:
            sub = _card_to_subscriber(card)
            source = "card"
    if not sub:
        _LOG.warning("auth_decision user=%r reason=user_not_found "
                      "(لا subscriber ولا card في tenant=%d)",
                      req.username, req.tenant_id)
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
            _LOG.warning("auth_decision user=%r source=%s rejected reason=%s",
                          req.username, source, bad.reason)
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
    _LOG.warning("auth_decision user=%r source=%s accepted attrs=%d",
                  req.username, source, len(reply))
    _log_attempt(req, accepted=True)
    # R9.2: حدّث first_login_at + last_login_at + last_seen_at على الـ
    # subscriber الحقيقي، و first_used_at + used_by_mac على الـ card.
    # لا نرفع لو فشلت الكتابة — auth قد نجح فعلاً، لا نحوّله إلى Reject.
    _update_login_timestamps(req, source=source, now=now)
    return AuthDecision(ok=True, message=msg, reply_attrs=reply)


def _update_login_timestamps(req: AuthRequest, *, source: str, now: datetime) -> None:
    """يحدّث حقول وقت الدخول بعد قبول الـ auth.

    - `subscribers.first_login_at`: يُعَيَّن مرّة واحدة فقط (COALESCE).
    - `subscribers.last_login_at` و `last_seen_at`: يُحدَّثان دائمًا.
    - `cards.first_used_at`: يُعَيَّن مرّة واحدة (COALESCE) + `used=1`.
    - `cards.used_by_mac`: نضع الـ Calling-Station-Id لو موجود وفارغ سابقاً.

    fail-safe: try/except حول كل UPDATE كي لا تتسرّب أخطاء الـ DB إلى
    مسار الـ auth (الذي نجح بالفعل).
    """
    try:
        from ..db.connection import transaction
        from ..db.helpers import now_iso
        ts = now_iso()
        mac = (req.calling_station_id or "").strip()
        with transaction() as conn:
            # المشترك الحقيقي: حدّث جدول subscribers (الـ row قد يكون
            # mirror لكارت بـ user_type='card' — لا بأس، نفس الجدول).
            conn.execute("""
                UPDATE subscribers
                   SET first_login_at = COALESCE(first_login_at, ?),
                       last_login_at  = ?,
                       last_seen_at   = ?
                 WHERE tenant_id = ? AND username = ?
            """, (ts, ts, ts, req.tenant_id, req.username))
            # الكارت: إذا الـ source = card، حدّث cards.first_used_at + used.
            if source == "card":
                conn.execute("""
                    UPDATE cards
                       SET first_used_at = COALESCE(first_used_at, ?),
                           used          = 1,
                           used_by_mac   = CASE
                               WHEN COALESCE(used_by_mac, '') = '' AND ? != ''
                               THEN ? ELSE used_by_mac END
                     WHERE tenant_id = ? AND username = ?
                """, (ts, mac, mac, req.tenant_id, req.username))
    except Exception:  # noqa: BLE001
        _LOG.warning("policy_engine: failed to update login timestamps for %r",
                      req.username, exc_info=True)


def _build_accept_attrs(sub: Subscriber, plan: Optional[AccessPlan]) -> dict:
    out: dict = {}
    active_rule = operations_repo.resolve_effective_bandwidth_schedule(
        sub.tenant_id,
        subscriber_username=sub.username,
        card_batch_id=sub.card_batch_id,
        plan_id=plan.id if plan else sub.plan_id,
    )
    if active_rule:
        out["Mikrotik-Rate-Limit"] = (
            f"{int(active_rule.get('speed_up_kbps') or 0)}k/"
            f"{int(active_rule.get('speed_down_kbps') or 0)}k"
        )
    elif sub.bandwidth_control_enabled and (sub.download_speed_kbps or sub.upload_speed_kbps):
        out["Mikrotik-Rate-Limit"] = f"{sub.upload_speed_kbps}k/{sub.download_speed_kbps}k"
    if plan:
        if "Mikrotik-Rate-Limit" not in out and (plan.speed_down_kbps or plan.speed_up_kbps):
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
