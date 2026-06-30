"""
Policy Engine — قرارات Accept/Reject الديناميكية لـ RADIUS auth.

يُستدعى من /api/internal/auth (الذي يُناديه FreeRADIUS rlm_rest module).

ترتيب الفحوصات (يفشل عند أول خرق):
1. وجود الـ subscriber
2. password صحيحة
3. status = enabled
4. لم تنتهِ الصلاحية
5. ضمن جدول الدوام (جدول المشترك الخاصّ يَتجاوز جدول الباقة — أيام+ساعات)
6. الكوتا لم تنفد (تجاوز المشترك combined/download/upload يَغلب الباقة)
7. حدود وقت الاتصال (إجماليّ + يوميّ محلّي من acctsessiontime)
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
from datetime import datetime, timezone
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
    nas_port: str = ""                 # NAS-Port
    nas_port_type: str = ""
    user_agent: str = ""               # من البوابة عند توفّره (anti-mac-clone)


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
    # «جدول الاتصال» الخاصّ بالمشترك (connection_schedule/working_days) — يَتجاوز
    # جدول الباقة حين يُضبَط. رسالة واحدة تَجمع اليوم/الساعة (الجدول الموحَّد).
    "outside_schedule":  "خارج أوقات/أيام الدوام المسموحة لهذا الحساب",
    "quota_exhausted":   "نفدت الكوتا — يلزم تجديد",
    # «حدود وقت الاتصال» الخاصّة بالمشترك (total/daily_connection_time_min).
    "time_total_exhausted": "انتهى إجمالي وقت الاتصال المسموح لهذا الحساب",
    "time_daily_exhausted": "انتهى وقت الاتصال المسموح لهذا اليوم — حاول غدًا",
    "mac_mismatch":      "هذا الجهاز غير مصرَّح بالدخول لهذا الحساب",
    "random_mac_blocked": "هذا الجهاز يستخدم عنوان MAC عشوائي/خاص — أوقف «العنوان الخاص» في إعدادات الواي فاي ثم أعد المحاولة",
    "concurrent_limit":  "بلغت الحد الأقصى من الجلسات المسموحة لهذا الحساب",
    # «تعليق الوصول» (الطبقة A): رسالة مهذّبة موجّهة للمستخدم.
    "access_suspended":   "تسجيل الدخول معلّق مؤقتاً — راجع الإدارة",
    # «حظر» أمني (الطبقة B): IP/MAC.
    "access_blocked":     "الدخول محظور حاليًا — راجع الإدارة",
    # «منع استنساخ MAC» (anti-mac-clone): جهاز مختلف بنفس MAC.
    "mac_clone_detected": "تنبيه أمني: تم رصد محاولة دخول من جهاز مختلف بنفس عنوان MAC — الدخول مرفوض",
    # نمط step-up: رفض أوّل لإجبار إعادة كتابة كلمة المرور كتأكيد على
    # «هذا جهازي الجديد». المحاولة الثانية بنفس البصمة الحيّة ضمن النافذة
    # تُعامَل كتأكيد قانوني → سماح + إعادة ربط.
    "stepup_required":    "هذا الجهاز جديد — أعد كتابة كلمة المرور للتأكيد",
    # «نمط السماح» (allow-mode):
    "allow_mode_unknown_device": "هذا الجهاز غير مُسجَّل في قائمة الأجهزة المسموح بها — راجع الإدارة",
    "allow_mode_at_capacity":    "تم الوصول للحدّ الأقصى للأجهزة المربوطة بهذا الحساب — تواصل مع الإدارة",
    "allow_mode_bind_failed":    "تعذّر ربط هذا الجهاز — تواصل مع الإدارة",
    # سقف «اكتف» — العدد الإجمالي للجلسات المتزامنة المتصلة الآن (cards +
    # subscribers + PPPoE + hotspot) عند سقف الباقة من المزوّد. يَرفض
    # الجلسة الجديدة فقط (المُعاد المصادقة لمستخدم قائم لا يُحتَسَب).
    "provider_active_cap": "تم بلوغ الحدّ الأقصى للمتصلين المتزامنين لباقتك — انتظر انتهاء جلسة أو رقّ باقتك",
    "ok_welcome":        "أهلًا بك",
    "ok_expires_soon":   "اشتراكك ينتهي قريبًا — جدّد قبل الانقطاع",
}

# fail2ban — قائمة سماح: العدّاد التلقائي يُحسب **فقط** على فشل المصادقة
# الحقيقي (اعتماد خاطئ). صراحةً نستثني كل رفض سياسة/تفويض (expired,
# quota_exhausted, outside_hours, outside_days, concurrent_limit,
# mac_mismatch, random_mac_blocked) وكذلك access_suspended/access_blocked.
# توسعة هذه القائمة قرار أمني مقصود — لا تُضِف رموز سياسة هنا.
_FAIL2BAN_REASONS = frozenset({"password_wrong", "user_not_found"})


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


# ─── «انتهى اشتراكك» captive funnel (phase 2) ────────────────────────────────
# Unifies the expiry handling for ALL THREE subscriber types (prepaid cards,
# PPPoE broadband, hotspot) — they all share Subscriber.status / expire_at, so
# one gate covers them. Built ON TOP of the existing expiry logic (replaces the
# _check_status + _check_expiration pair in the authorize loop), it does NOT
# duplicate the access-block/suspension path (_check_blocks stays a reject).

def _captive_enabled() -> bool:
    from ..core import env_settings
    return env_settings.get_bool("HOBERADIUS_EXPIRED_CAPTIVE_ENABLED", True)


def _expired_pool_name() -> str:
    from ..core import env_settings
    name = str(env_settings.env("HOBERADIUS_EXPIRED_POOL_NAME",
                                "hr-pool-expired") or "").strip()
    return name or "hr-pool-expired"


def _check_expiry_captive(sub: Subscriber) -> Optional[AuthDecision]:
    """Status + expiry gate. When the subscription has ENDED (status 'expired'
    or expire_at passed) AND the captive page is enabled, ACCEPT the user but
    place them in the expired address-list (Mikrotik-Address-List) — the router
    firewall then confines them to the walled garden and redirects their HTTP to
    the «انتهى اشتراكك» page. When captive is disabled, keep the legacy
    behaviour: expired → reject. A record-level disabled/suspended status is a
    security/admin state (not an expiry) and always rejects (no captive)."""
    if sub.status not in ("enabled", "expired"):
        return _reject("disabled")
    ended = (sub.status == "expired") or (
        sub.expire_at is not None and sub.expire_at < datetime.utcnow())
    if not ended:
        return None
    if not _captive_enabled():
        return _reject("expired")
    return AuthDecision(
        ok=True, reason="expired_captive",
        message=_MSG.get("expired", "انتهى الاشتراك"),
        reply_attrs={
            "Mikrotik-Address-List": _expired_pool_name(),
            # short re-auth window so a renewal takes effect within minutes
            "Session-Timeout": "300",
            "Reply-Message": _MSG.get("expired", "انتهى الاشتراك"),
        },
    )


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


def _effective_quota_mb(sub: Subscriber, plan: Optional[AccessPlan]) -> int:
    """سقف الكوتا الإجماليّ الفعّال (MB). تجاوز المشترك يَغلب الباقة حين يُضبَط
    صراحةً (``quota_limit_enabled`` أو أيّ قيمة كوتا فرديّة غير صفريّة)، وإلّا
    تَسقط للباقة (``plan.quota_total_mb``). يُطابق منطق ملفّ المشترك 360
    (users.py): combined يَغلب، وإلّا download(أو الباقة)+upload. 0 = لا سقف."""
    sub_quota = 0
    if (getattr(sub, "quota_limit_enabled", False)
            or getattr(sub, "combined_quota_mb", 0)
            or getattr(sub, "download_quota_mb", 0)
            or getattr(sub, "upload_quota_mb", 0)):
        sub_quota = int(getattr(sub, "combined_quota_mb", 0) or 0) or (
            int(getattr(sub, "download_quota_mb", 0) or 0)
            + int(getattr(sub, "upload_quota_mb", 0) or 0))
    if sub_quota > 0:
        return sub_quota
    return int(plan.quota_total_mb) if (plan and plan.quota_total_mb) else 0


def _check_quota(sub: Subscriber, plan: Optional[AccessPlan]) -> Optional[AuthDecision]:
    """يَرفض عند نفاد الكوتا. السقف = تجاوز المشترك (إن ضُبط) وإلّا الباقة.

    العدّاد المُحتسَب هو نفسه عدّاد كوتا الباقة (``used_bytes_in + used_bytes_out``)
    — مصدر واحد للاستهلاك المُحاسَب لئلّا يَنفصل تجاوز المشترك عن الباقة، ومسار
    «إضافة كوتا» (users.add_quota) يَرفع ``combined_quota_mb`` فيَتغذّى نفس السقف."""
    cap_mb = _effective_quota_mb(sub, plan)
    if cap_mb <= 0:
        return None
    used_mb = (sub.used_bytes_in + sub.used_bytes_out) / 1_048_576
    if used_mb >= cap_mb:
        return _reject("quota_exhausted")
    return None


# ─── «جدول اتصال» المشترك (يَتجاوز جدول الباقة) ──────────────────────────────


def _subscriber_has_schedule(sub: Subscriber) -> bool:
    """هل لدى المشترك جدول اتصال خاصّ مضبوط فعليًّا؟ نَعتبر الجدول الموحَّد
    (connection_schedule JSON بنوافذ غير فارغة) مصدرًا أوّل، ثمّ working_days
    (CSV) كاحتياط legacy. جدول فارغ/مُشوَّه لا يَخطف مسار الباقة."""
    raw = (getattr(sub, "connection_schedule", "") or "").strip()
    if raw:
        try:
            from ..core import access_schedule
            if access_schedule.parse(raw).get("windows"):
                return True
        except Exception:  # noqa: BLE001
            pass
    return bool((getattr(sub, "working_days", "") or "").strip())


def _check_subscriber_schedule(sub: Subscriber) -> Optional[AuthDecision]:
    """يَفحص جدول المشترك بالتوقيت المحلّي للمستأجر (DST-safe). يُرجع رفضًا
    برسالة واضحة خارج النافذة، None ضمنها. محصّن: أيّ خطأ → سماح (لا نكسر auth)."""
    try:
        from ..core import access_schedule, system_config
        local_dt = system_config.local_now(int(sub.tenant_id))
        raw = (getattr(sub, "connection_schedule", "") or "").strip()
        if raw and access_schedule.parse(raw).get("windows"):
            if access_schedule.is_allowed(raw, local_dt):
                return None
            return _reject("outside_schedule")
        days = (getattr(sub, "working_days", "") or "").strip()
        if days:
            allowed = {d.strip().lower() for d in days.split(",") if d.strip()}
            today = ("mon", "tue", "wed", "thu", "fri", "sat",
                     "sun")[local_dt.weekday()]
            if allowed and today not in allowed:
                return _reject("outside_days")
        return None
    except Exception:  # noqa: BLE001 — لا نكسر الـauth أبدًا بسبب هذا الفحص
        _LOG.warning("policy_engine: subscriber-schedule check failed for %r",
                      sub.username, exc_info=True)
        return None


def _check_schedule(sub: Subscriber, plan: Optional[AccessPlan],
                    now: datetime) -> Optional[AuthDecision]:
    """بوّابة موحَّدة لأيام/ساعات الدوام: جدول المشترك الخاصّ يَتجاوز جدول الباقة
    حين يُضبَط؛ وإلّا تُطبَّق فحوصات الباقة كما هي (سلوك غير متغيّر للمشتركين
    بلا جدول خاصّ — تبقى بتوقيت UTC مثل السابق تمامًا)."""
    if _subscriber_has_schedule(sub):
        return _check_subscriber_schedule(sub)
    bad = _check_hours(plan, now)
    if bad is not None:
        return bad
    return _check_days(plan, now)


# ─── «حدود وقت الاتصال» للمشترك (إجماليّ + يوميّ بالتوقيت المحلّي) ────────────


def _accounted_session_seconds(tenant_id: int, username: str,
                               since_iso: Optional[str] = None) -> int:
    """مجموع acctsessiontime من radacct (جدول المحاسبة القانونيّ) لهذا المستخدم.
    ``since_iso`` (UTC ``YYYY-MM-DDTHH:MM:SS`` مطابق لصيغة isoformat المُخزَّنة)
    يَحصر على الجلسات التي بَدأت عند/بعد تلك اللحظة (للسقف اليوميّ المحلّي).
    محصّن: أيّ خطأ يُرجع 0 (لا يُغلق الباب)."""
    try:
        from ..db.connection import db
        if since_iso:
            # تطبيع العمود والحدّ لصيغة «مسافة» موحَّدة كي يَصِحّ الحصر
            # الزمنيّ لصيغتَي FreeRADIUS «مسافة» وISO معًا — وإلّا لَاستُبعدت
            # جلسات الإنتاج «مسافة» (المسافة < ‎'T') فيَنقُص السقف اليوميّ.
            from .device_limit import acct_norm_sql, to_space_ts
            nrm = acct_norm_sql("acctstarttime")
            row = db().execute(
                "SELECT COALESCE(SUM(acctsessiontime),0) AS s FROM radacct "
                "WHERE tenant_id=? AND username=? "
                f"AND {nrm} >= ?",
                (int(tenant_id), str(username), to_space_ts(since_iso))).fetchone()
        else:
            row = db().execute(
                "SELECT COALESCE(SUM(acctsessiontime),0) AS s FROM radacct "
                "WHERE tenant_id=? AND username=?",
                (int(tenant_id), str(username))).fetchone()
        return int((row["s"] if row else 0) or 0)
    except Exception:  # noqa: BLE001
        _LOG.warning("policy_engine: accounted-seconds read failed for %r",
                      username, exc_info=True)
        return 0


def _local_day_start_utc(tenant_id: int) -> str:
    """بداية اليوم المحلّي (منتصف ليل المستأجر) مُعبَّرًا عنها بـ UTC بصيغة
    isoformat بلا ميكروثوان (``YYYY-MM-DDTHH:MM:SS``) — تُقارَن نصّيًّا ضدّ
    acctstarttime المُخزَّن. يُعيد ضبط السقف اليوميّ لكلّ يومٍ محلّي."""
    from ..core import system_config
    tz = system_config.tenant_tzinfo(int(tenant_id))
    local_now = datetime.now(timezone.utc).astimezone(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _effective_time_caps(sub: Subscriber,
                         plan: Optional[AccessPlan]) -> tuple[int, int]:
    """(إجماليّ_دقائق، يوميّ_دقائق) — سقفا وقت الاتصال الفعّالان. تجاوز المشترك
    يَغلب حين ``connection_time_limit_enabled`` أو أيّ قيمة فرديّة غير صفريّة.
    وإلّا سقوط للباقة: اليوميّ = ``plan.max_daily_minutes`` (مكافئ الباقة)،
    والإجماليّ لا مكافئ له في الباقة (session_timeout مختلف، لكلّ جلسة) → 0."""
    sub_total = int(getattr(sub, "total_connection_time_min", 0) or 0)
    sub_daily = int(getattr(sub, "daily_connection_time_min", 0) or 0)
    if (getattr(sub, "connection_time_limit_enabled", False)
            or sub_total or sub_daily):
        return sub_total, sub_daily
    plan_daily = int(getattr(plan, "max_daily_minutes", 0) or 0) if plan else 0
    return 0, plan_daily


def _check_connection_time(sub: Subscriber, plan: Optional[AccessPlan],
                           req: AuthRequest) -> Optional[AuthDecision]:
    """يَرفض حين بَلغ المستخدم سقف وقت الاتصال (الإجماليّ مدى الحياة أو اليوميّ
    المحلّي) محسوبًا من مجموع acctsessiontime في radacct. محصّن: أيّ خطأ →
    سماح (لا نَحجب مستخدمًا شرعيًّا بسبب خطأ داخليّ)."""
    try:
        total_cap_min, daily_cap_min = _effective_time_caps(sub, plan)
        if total_cap_min <= 0 and daily_cap_min <= 0:
            return None
        tid, user = int(sub.tenant_id), sub.username
        if total_cap_min > 0:
            used = _accounted_session_seconds(tid, user)
            if used >= total_cap_min * 60:
                return _reject("time_total_exhausted")
        if daily_cap_min > 0:
            since = _local_day_start_utc(tid)
            used_today = _accounted_session_seconds(tid, user, since_iso=since)
            if used_today >= daily_cap_min * 60:
                return _reject("time_daily_exhausted")
        return None
    except Exception:  # noqa: BLE001 — لا نَكسر المصادقة على خطأ حدّ الوقت
        _LOG.warning("policy_engine: connection-time check failed for %r",
                      req.username, exc_info=True)
        return None


def _time_cap_remaining_seconds(sub: Subscriber,
                                plan: Optional[AccessPlan]) -> Optional[int]:
    """الثواني المتبقّية ضمن سقوف وقت الاتصال = min(المتبقّي الإجماليّ، المتبقّي
    اليوميّ)، أو None حين لا سقف فعّال. تُستعمَل لإصدار Session-Timeout كي يُنفّذ
    الـNAS الحدّ بنفسه. محصّن: None عند الخطأ."""
    try:
        total_cap_min, daily_cap_min = _effective_time_caps(sub, plan)
        if total_cap_min <= 0 and daily_cap_min <= 0:
            return None
        tid, user = int(sub.tenant_id), sub.username
        remainings: list[int] = []
        if total_cap_min > 0:
            remainings.append(total_cap_min * 60
                              - _accounted_session_seconds(tid, user))
        if daily_cap_min > 0:
            since = _local_day_start_utc(tid)
            remainings.append(daily_cap_min * 60
                              - _accounted_session_seconds(tid, user, since_iso=since))
        if not remainings:
            return None
        return max(0, min(remainings))
    except Exception:  # noqa: BLE001
        return None


def _check_mac(sub: Subscriber, req: AuthRequest) -> Optional[AuthDecision]:
    """Reject if the card has a MAC lock and the incoming MAC doesn't
    match any of the locked entries.

    `sub.mac_lock` may be a comma-separated list (multi-MAC support
    added in the Card Checker rebuild). All entries are normalised to
    UPPER + ':' separators before comparison.
    """
    if not sub.mac_lock: return None
    locked_raw = sub.mac_lock.replace(";", ",").replace("\n", ",")
    locked = {
        m.strip().upper().replace("-", ":")
        for m in locked_raw.split(",")
        if m.strip()
    }
    if not locked:
        return None
    incoming = (req.calling_station_id or "").upper().replace("-", ":")
    if not incoming or incoming not in locked:
        return _reject("mac_mismatch")
    return None


def _check_random_mac(req: AuthRequest, source: str) -> Optional[AuthDecision]:
    """يمنع تسجيل الدخول من أجهزة تستخدم عنوان MAC عشوائي/خاص
    (locally-administered) عند تفعيل المفتاح المناسب لنوع الحساب.

    مفتاحان مستقلّان في الإعدادات (كل تفعيل منفصل عن الآخر):
      • security.block_random_mac_cards       → يخص دخول البطاقات (source='card')
      • security.block_random_mac_subscribers → يخص دخول المشتركين

    الكشف يعتمد على «بت الإدارة المحلية» في أول ثُماني من العنوان (الخانة
    السداسية الثانية ضمن {2,6,A,E}). محصّن: أي خطأ في قراءة الإعداد أو
    تحليل العنوان لا يكسر مسار الـ auth (يُرجع None = سماح).
    """
    try:
        from ..services.device_fingerprint import is_random_mac
        from ..db.repos import tenants_repo
        key = ("security.block_random_mac_cards" if source == "card"
               else "security.block_random_mac_subscribers")
        enabled = tenants_repo.get_setting(req.tenant_id, key, "0") in (
            "1", "true", "t", "on", "True")
        if not enabled:
            return None
        if is_random_mac(req.calling_station_id):
            return _reject("random_mac_blocked")
    except Exception:  # noqa: BLE001 — لا نكسر الـ auth أبدًا بسبب هذا الفحص
        _LOG.warning("policy_engine: random-MAC check failed for %r",
                      req.username, exc_info=True)
    return None


def _check_blocks(sub: Subscriber, plan: Optional[AccessPlan], req: AuthRequest,
                  source: str, now: datetime) -> Optional[AuthDecision]:
    """«التحكم بالدخول» (الطبقتان) — يرفض إذا انطبق سجلّ فعّال وسارٍ:
      • «تعليق وصول» نطاقي (مشترك/مجموعة/عرض/حزمة/شامل) → reason=access_suspended
        مع رسالة مهذّبة موجّهة للمستخدم في Reply-Message (يرى لماذا/متى).
      • «حظر» أمني على IP/MAC → reason=access_blocked برسالة عامّة.

    محصّن: أي خطأ في الطبقة لا يكسر مسار الـauth (يُرجع None = سماح)."""
    try:
        from ..services import access_control as acl
        # service_type الحقيقي يأتي من الباقة (الكارت يرث Hotspot افتراضيًا
        # في _card_to_subscriber)؛ نفضّل plan.service_type لئلّا يُخطئ نطاقا
        # all_hotspot/all_pppoe في تصنيف الكروت.
        svc = (getattr(plan, "service_type", "") if plan else "") \
            or getattr(sub, "service_type", "") or ""
        ctx = acl.AuthContext(
            source=source,
            username=sub.username,
            group=getattr(sub, "group", "") or "",
            plan_id=sub.plan_id,
            card_batch_id=sub.card_batch_id,
            service_type=svc,
            nas_ip=req.nas_ip,
            mac=req.calling_station_id,
        )
        hit = acl.find_active_block(req.tenant_id, ctx, now=now)
        if hit is not None:
            # رمز داخلي بحسب الطبقة + رسالة المستخدم المهذّبة (Reply-Message).
            reason = ("access_blocked"
                      if acl.layer_of(hit.get("block_type")) == acl.LAYER_BLOCK
                      else "access_suspended")
            msg = acl.user_message_for(hit)
            return AuthDecision(ok=False, reason=reason, message=msg,
                                reply_attrs={"Reply-Message": msg})
    except Exception:  # noqa: BLE001 — لا نكسر الـauth أبدًا بسبب هذا الفحص
        _LOG.warning("policy_engine: access-control check failed for %r",
                      req.username, exc_info=True)
    return None


def _check_anti_mac_clone(req: AuthRequest, sub: Subscriber,
                           plan: Optional[AccessPlan],
                           source: str) -> Optional[AuthDecision]:
    """«منع استنساخ MAC»: يُستدعى بعد فحوصات السلامة (password/status/expire/…)
    وقبل إصدار Accept. يفوّض كل المنطق للخدمة المخصّصة (toggle + scope + بصمة
    + قرار + binding/event/alert/CoA).

    Verdict.action:
      • allow / monitor / None → لا رفض (نمضي للقبول).
      • deny                   → نُحوّله لـ AuthDecision Reject مع رسالة عربية.

    محصّن بالكامل: أي خطأ يُسقط الفحص (سماح) كي لا نكسر مسار الـauth."""
    try:
        from .anti_mac_clone import check_after_auth
        v = check_after_auth(
            int(req.tenant_id),
            username=sub.username, source=source,
            plan_id=sub.plan_id,
            group=getattr(sub, "group", "") or "",
            calling_station_id=req.calling_station_id,
            called_station_id=req.called_station_id,
            nas_ip=req.nas_ip,
            nas_port=req.nas_port,
            nas_port_type=req.nas_port_type,
            user_agent=req.user_agent,
        )
        if v is None or v.action != "deny":
            return None
        # تمييز سبب الرفض: stepup_required (نمط step-up أوّل محاولة) عن
        # mac_clone_detected (نمط enforce). كلاهما رفض، لكن الرسالة مختلفة.
        reason = v.reason if v.reason in (
            "mac_clone_detected", "stepup_required") else "mac_clone_detected"
        msg = v.message or _MSG.get(reason) or _MSG["mac_clone_detected"]
        return AuthDecision(ok=False, reason=reason,
                             message=msg,
                             reply_attrs={"Reply-Message": msg})
    except Exception:  # noqa: BLE001 — never break auth
        _LOG.warning("policy_engine: anti-mac-clone check failed for %r",
                      req.username, exc_info=True)
        return None


def _check_allow_mode(sub: Subscriber, req: AuthRequest) -> Optional[AuthDecision]:
    """«نمط السماح»: يُستدعى بعد فحوصات السلامة (password/expiry/MAC/…)
    وقبل حدّ الجلسات المتزامنة. يفوّض كل المنطق للخدمة المخصّصة.

    Verdict.action:
      • None              → لا سياسة (السلوك الطبيعي يستمر).
      • allow             → السماح (TOFU bind / مطابقة allowlist / open).
      • deny              → نُحوّله إلى Reject مع رسالة عربية.

    محصّن بالكامل: أي خطأ يُسقط الفحص (سماح) كي لا نكسر مسار الـauth."""
    try:
        from .allow_mode import check_after_password
        v = check_after_password(
            int(req.tenant_id),
            username=sub.username,
            plan_id=sub.plan_id,
            card_batch_id=sub.card_batch_id,
            calling_station_id=req.calling_station_id,
        )
        if v is None or v.action != "deny":
            return None
        reason = v.reason if v.reason in (
            "allow_mode_unknown_device", "allow_mode_at_capacity",
            "allow_mode_bind_failed") else "allow_mode_unknown_device"
        msg = v.message or _MSG.get(reason) or _MSG["allow_mode_unknown_device"]
        return AuthDecision(ok=False, reason=reason, message=msg,
                             reply_attrs={"Reply-Message": msg})
    except Exception:  # noqa: BLE001 — never break auth
        _LOG.warning("policy_engine: allow-mode check failed for %r",
                      req.username, exc_info=True)
        return None


def _check_concurrent(sub: Subscriber, plan: Optional[AccessPlan],
                      req: AuthRequest) -> Optional[AuthDecision]:
    """«عدد الأجهزة المسموحة» (Simultaneous-Use) — يُنفَّذ فعلاً الآن.

    الحدّ الفعّال + mac_aware يُحسبان في ``device_limit.effective_limit``:
    override_concurrent (سقف خام تاريخيّ) > device_count (عدّ أجهزة مختلفة) >
    plan.concurrent_sessions. العدّ يَستثني الجلسات الزومبي (نافذة الحياة) فلا
    يَحجب راوترٌ أُعيد إقلاعه دخولًا شرعيًّا، ويَستثني جلسات نفس جهاز الطالب في
    مسار device_count (إعادة مصادقة لا تُحتسَب كجهازٍ ثانٍ).

    عند البلوغ: «reject» (الافتراض) → رفض برسالة «بلغت الحد الأقصى…»؛ «replace»
    → فصل أقدم جلسة واحدة (CoA Disconnect + إغلاق قانونيّ) ثمّ السماح. أيّ خطأ
    في منطق الحدّ لا يُغلق الباب (fail-open) — السعة ليست أمانًا.
    """
    try:
        from . import device_limit
        limit, mac_aware = device_limit.effective_limit(sub, plan)
        if limit <= 0:
            return None
        active = device_limit.active_other_devices(
            sub.tenant_id, sub.username, req, mac_aware=mac_aware)
        if len(active) < limit:
            return None
        # بلغ الحدّ — السلوك المضبوط.
        mode = device_limit.effective_mode(sub.tenant_id, sub)
        if mode == device_limit.MODE_REPLACE:
            device_limit.replace_oldest(sub.tenant_id, sub.username, active)
            return None
        return _reject("concurrent_limit")
    except Exception:  # noqa: BLE001 — لا نَكسر المصادقة على خطأ حدّ السعة
        _LOG.warning("policy_engine: device-limit check failed for %r",
                     req.username, exc_info=True)
        return None


def _check_provider_active_cap(sub: Subscriber, req: AuthRequest) -> Optional[AuthDecision]:
    """سقف «اكتف» — أعلى سلطة على عدد الجلسات المتزامنة لهذه النسخة.

    تعريف المالك: «اكتف» = العدد الإجمالي للجلسات المفتوحة حاليًّا عبر
    كل أنواع الاتصال (cards + subscribers + PPPoE + hotspot). كل جلسة
    حيّة في radacct = 1. السقف يَأتي من عقد المزوّد (limits.active_online.max
    أو أحد البدائل). unlimited عند غياب الحقل.

    منطق إعادة المصادقة: المستخدم الذي لديه جلسة مفتوحة الآن يُسمَح
    بإعادة المصادقة حتى لو الإجمالي عند السقف — لن يَزيد العدد فعليًّا
    (جلسته القديمة تُغلَق فور فتح الجديدة). فقط مستخدم جديد بلا جلسة
    قائمة يَنبغي عَدُّه ضدّ السقف.

    fail-safe: أيّ خطأ في القراءة يُسقط الفحص (سماح) كي لا تَنكسر الـauth.
    """
    try:
        from . import provider_grant
        cap = provider_grant.get_active_online_cap(int(req.tenant_id))
        if cap is None or cap <= 0:
            return None  # Unlimited
        # إعفاء re-auth: لو لدى المستخدم جلسة مفتوحة، لن يَزيد العدد.
        if provider_grant.user_has_open_session(int(req.tenant_id), sub.username):
            return None
        # مستخدم جديد ينضمّ — قارن العدد الإجمالي بالسقف.
        current = provider_grant.count_active_sessions(int(req.tenant_id))
        if current >= cap:
            return _reject("provider_active_cap")
        return None
    except Exception:  # noqa: BLE001 — fail-safe (لا نَكسر الـauth)
        _LOG.warning("policy_engine: provider_active_cap check failed for %r",
                      req.username, exc_info=True)
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
    # حدّ الأجهزة للبطاقة يُعرَّف على دفعتها (card_batches.device_count). نَجلبه
    # هنا فيَلتقطه _check_concurrent عبر Subscriber.device_count كما للمشترك.
    batch_device_count = 0
    batch_mode = ""
    try:
        from ..db.repos import cards_repo
        batch = cards_repo.get_batch(card.tenant_id, card.batch_id)
        if batch is not None:
            batch_device_count = int(getattr(batch, "device_count", 0) or 0)
    except Exception:  # noqa: BLE001 — غياب الدفعة لا يَكسر المصادقة
        batch_device_count = 0
    return Subscriber(
        id=card.id,
        tenant_id=card.tenant_id,
        username=card.username,
        password=card.password,
        user_type="card",
        plan_id=card.plan_id,
        card_batch_id=card.batch_id,
        # حدّ الأجهزة من الدفعة (إن وُجد) — وإلّا الافتراض 1 كأيّ مشترك.
        device_count=batch_device_count or 1,
        device_limit_mode=batch_mode,
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
        # محاولة باسم غير موجود = brute-force محتمل → تُحسب في عدّاد fail2ban.
        _register_failed_attempt(req, datetime.utcnow())
        return _reject("user_not_found")

    plan: Optional[AccessPlan] = None
    if sub.plan_id:
        try: plan = plans_repo.get_plan(req.tenant_id, sub.plan_id)
        except Exception: plan = None

    now = datetime.utcnow()

    for fn in (
        # «الحظر والتحكم بالدخول» أولًا: المحظور يُرفض فورًا بصرف النظر عن
        # صحّة كلمة المرور (لا نُسرّب صحّتها ولا نزيد عدّاد fail2ban له).
        lambda: _check_blocks(sub, plan, req, source, now),
        lambda: _check_password(sub, req),
        # status + expiry, unified for cards/PPPoE/hotspot. May return an early
        # ACCEPT (ok=True) that funnels an expired user into the captive pool.
        lambda: _check_expiry_captive(sub),
        # أيام/ساعات الدوام: جدول المشترك الخاصّ يَتجاوز جدول الباقة حين يُضبَط
        # (بالتوقيت المحلّي للمستأجر)، وإلّا فحوصات الباقة كما هي بلا تغيير.
        lambda: _check_schedule(sub, plan, now),
        lambda: _check_quota(sub, plan),
        # «حدود وقت الاتصال» للمشترك (إجماليّ + يوميّ محلّي) من مجموع
        # acctsessiontime — رفض سياسة عند البلوغ (لا يُحتسَب في fail2ban).
        lambda: _check_connection_time(sub, plan, req),
        lambda: _check_mac(sub, req),
        lambda: _check_random_mac(req, source),
        # «نمط السماح» (allow-mode): يأتي بعد سلامة MAC وقبل حدّ الجلسات
        # المتزامنة. يفحص الانتماء لقائمة سماح أو يطبّق TOFU binding.
        lambda: _check_allow_mode(sub, req),
        # «منع استنساخ MAC» (anti-mac-clone): قرار أمني (بصمة الجهاز مقابل MAC)
        # بعد كل فحوصات السلامة + نمط السماح، وقبل حدّ الجلسات المتزامنة —
        # للأمان أولوية على السعة. يفترض أن كلمة المرور صحّت (لا يُعاقب فشل
        # auth)؛ محصّن بالكامل (أي خطأ → سماح آمن).
        lambda: _check_anti_mac_clone(req, sub, plan, source),
        lambda: _check_concurrent(sub, plan, req),
        # سقف «اكتف» — أعلى سلطة على إجمالي الجلسات المتزامنة لهذه النسخة.
        # يَأتي بعد _check_concurrent (per-user) كي لا يَستهلك مستخدمٌ مُتجاوز
        # لحدّه الخاص مَحلًّا من الإجمالي العام. سقف يَأتي من عقد المزوّد
        # (limits.active_online.max). لا يُحتسَب في fail2ban (رفض سعة لا فشل auth).
        lambda: _check_provider_active_cap(sub, req),
    ):
        bad = fn()
        if bad is not None:
            if bad.ok:
                # Early ACCEPT (expired → captive pool). Skip the remaining
                # policy checks — quota/hours/concurrent don't apply to a user
                # we're confining to the renewal walled garden. Logged as an
                # accepted (captive) session, no fail2ban.
                _LOG.info("auth_decision user=%r source=%s captive reason=%s",
                          req.username, source, bad.reason)
                _log_attempt(req, accepted=True, reason=bad.reason)
                return bad
            _LOG.warning("auth_decision user=%r source=%s rejected reason=%s",
                          req.username, source, bad.reason)
            _log_attempt(req, accepted=False, reason=bad.reason)
            # عدّاد الفشل + الحظر التلقائي (fail2ban): يُحسب **فقط** على فشل
            # مصادقة حقيقي (قائمة سماح _FAIL2BAN_REASONS) — لا على رفض
            # السياسة/التفويض (expired/quota_exhausted/outside_hours/
            # concurrent_limit/mac_mismatch/mac_clone_detected/…) كي لا يَحظر
            # مستخدم شرعي نفسه بسبب واي‑فاي متقطّع مثلًا، ولا على التعليق/الحظر.
            if bad.reason in _FAIL2BAN_REASONS:
                _register_failed_attempt(req, now)
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
    # سلسلة أولويّة Mikrotik-Rate-Limit (إنفاذ Finding-1 → option A، يونيو 2026):
    #   جدول سرعة نشِط (نافذة الوقت)  >  تجاوز المشترك  >  [ ملفّ السرعة أو الخطّة ]
    # طبقة «الخطّة» تُحلّ عبر bandwidth_rate.plan_rate_limit: لو الخطّة تُشير لملفّ
    # سرعة (bandwidth_id) موجود فالملفّ هو المصدر، وإلّا حقول الخطّة. نفس الدالّة
    # يستعملها sync_plan فيتطابق المساران.
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
        if "Mikrotik-Rate-Limit" not in out:
            from .bandwidth_rate import plan_rate_limit
            rate = plan_rate_limit(plan)
            if rate:
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
    # «حدود وقت الاتصال» للمشترك: قصّ Session-Timeout على ما تبقّى من السقف
    # (إجماليّ/يوميّ) كي يَفصل الـNAS عند البلوغ. يَعمل مع/بدون باقة. نفس
    # العدّاد المُحاسَب الذي يَستعمله _check_connection_time.
    remaining_cap = _time_cap_remaining_seconds(sub, plan)
    if remaining_cap is not None and remaining_cap > 0:
        existing = int(out.get("Session-Timeout") or 0)
        out["Session-Timeout"] = str(remaining_cap if existing <= 0
                                      else min(existing, remaining_cap))
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


def _register_failed_attempt(req: AuthRequest, now: datetime) -> None:
    """يُمرّر محاولة فاشلة لطبقة «الحظر والتحكم بالدخول» (عدّاد + حظر تلقائي).
    محصّن — لا يكسر مسار الـauth (الذي رفض أصلًا)."""
    try:
        from ..services.access_control import register_failed_attempt
        register_failed_attempt(
            req.tenant_id, ip=req.nas_ip, mac=req.calling_station_id,
            username=req.username, now=now)
    except Exception:  # noqa: BLE001
        _LOG.warning("policy_engine: register_failed_attempt failed for %r",
                      req.username, exc_info=True)


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
