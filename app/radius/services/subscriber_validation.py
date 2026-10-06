"""Field rules for subscriber create / edit — one source for API, web and app.

Re-test R01/R10 (2026-09-29): the API stored ``status:"weird"`` (the row then
matched no status filter), ``static_ip:"999.1.1.1"``, ``email:"not-an-email"``,
negative speeds/devices/VLANs, and an ``expire_at`` of year 0001 or 9999 that
took the web subscribers list down with a 500. A few inputs (a dict as a name,
a 1e20 VLAN, a plan id that does not exist) reached SQLite and came back as
HTTP 500.

``validate_subscriber_fields`` checks a DTO. On an edit the caller passes only
the fields that changed, so a legacy row with an old odd value stays editable.
Every error is a ``RadiusValidationError`` (Arabic; API 422, web flash).
"""
from __future__ import annotations
from app.i18n_text import N_, _tr

import ipaddress
import re
from datetime import datetime, timedelta
from typing import Iterable, Optional

from ..core.errors import RadiusValidationError
from ..core import limits
from ..core.numbers import EXPIRY_LIMIT, EXTEND_MAX_DAYS, EXTEND_TOO_LONG_AR

# Arabic-Indic (U+0660..) and Extended/Persian (U+06F0..) digits → Latin.
_DIGITS = {ord(c): str(i) for i, c in enumerate("٠١٢٣٤٥٦٧٨٩")}
_DIGITS.update({ord(c): str(i) for i, c in enumerate("۰۱۲۳۴۵۶۷۸۹")})


def latin_digits(value) -> str:
    """«٠٥٩٩» → «0599». Arabic keyboards type these digits by default."""
    return str(value or "").translate(_DIGITS)


# ── expiry ────────────────────────────────────────────────────────────
EXPIRY_MIN = datetime(2000, 1, 1)
EXPIRY_MAX = EXPIRY_LIMIT - timedelta(seconds=1)   # 2100-12-31 23:59:59
# One shared cap (core/numbers): the same 365 days and the same owner text
# as extend / payment→minutes / loans.
MAX_EXPIRY_JUMP = timedelta(days=EXTEND_MAX_DAYS)

EXPIRY_RANGE_MSG = N_("تاريخ الانتهاء يجب أن يكون بين عامي 2000 و 2100.")
EXPIRY_JUMP_MSG = EXTEND_TOO_LONG_AR


# (الثوابت أعلاه افتراضات؛ الفعّال من «الحدود»: آخر سنة و«أقصى أيام في المرة».)


def validate_expiry_range(expire_at: Optional[datetime]) -> None:
    if expire_at is None:
        return
    year = limits.max_expiry_year()
    if not (EXPIRY_MIN <= expire_at < limits.expiry_limit()):
        raise RadiusValidationError(_tr('تاريخ الانتهاء يجب أن يكون بين عامي 2000 و %(year)s.', year=year))


def validate_expiry_jump(current: Optional[datetime], new: Optional[datetime], *,
                         now: Optional[datetime] = None) -> None:
    """Owner rule (2026-09-29): one change may move the expiry forward by at
    most a year: ``new − max(now, current) ≤ 365 days``. Shortening is free."""
    if new is None:
        return
    validate_expiry_range(new)
    now = now or datetime.utcnow()
    anchor = max(now, current) if current is not None else now
    if new - anchor > timedelta(days=limits.max_extend_days()):
        raise RadiusValidationError(limits.extend_too_long_msg())


# ── per-field rules ───────────────────────────────────────────────────
ALLOWED_STATUSES = ("enabled", "disabled", "suspended", "banned", "pending", "expired")
ALLOWED_SERVICE_TYPES = ("hotspot", "pppoe", "both")
MIN_USERNAME_LENGTH = 3

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MOBILE_RE = re.compile(r"^\+?[0-9][0-9 \-]{2,23}$")
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$")
_MAC_SPLIT_RE = re.compile(r"[\s,;|]+")

# (field, label, minimum, maximum)
_INT_RULES = {
    "download_speed_kbps": (N_("سرعة التنزيل"), 0, 100_000_000),
    "upload_speed_kbps": (N_("سرعة الرفع"), 0, 100_000_000),
    "vlan_id": ("VLAN", 0, 4094),
    "override_concurrent": (N_("عدد الجلسات المتزامنة"), 0, 10_000),
    "device_count": (N_("عدد الأجهزة"), 0, 10_000),
    "total_connection_time_min": (N_("وقت الاتصال الكلّيّ"), 0, 100_000_000),
    "daily_connection_time_min": (N_("وقت الاتصال اليوميّ"), 0, 1440),
    "download_quota_mb": (N_("كوتة التنزيل"), 0, 1_000_000_000),
    "upload_quota_mb": (N_("كوتة الرفع"), 0, 1_000_000_000),
    "combined_quota_mb": (N_("الكوتة الكلّيّة"), 0, 1_000_000_000),
}

# Free-text fields: must be text, bounded length.
_TEXT_RULES = {
    "full_name": 255, "father_name": 255, "address": 500, "city": 120,
    "district": 120, "state": 120, "zip": 32, "coordinates": 120,
    "national_id": 64, "account_type": 64, "photo_url": 500,
    "pppoe_username": 128, "pppoe_password": 128, "group": 128, "pool": 128,
    "caller_id": 255, "primary_dns_ppp": 64, "secondary_dns_ppp": 64,
    "device_connection_file": 255, "nationality": 64, "country": 64,
    "payment_method": 64, "payment_reference": 128, "working_days": 255,
    "allowed_macs": 4000, "beneficiary_ref": 255, "remark": 2000,
    "mobile": 32, "email": 254, "status": 32, "service_type": 32,
    "device_limit_mode": 32,
}


def _text(sub, field: str) -> str:
    v = getattr(sub, field, "")
    if v is None:
        return ""
    if not isinstance(v, str):
        raise RadiusValidationError(_tr('قيمة «%(field)s» يجب أن تكون نصًّا.', field=field))
    return v


def _valid_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _ip_text(sub, field: str) -> str:
    v = getattr(sub, field, None)
    return v.strip() if isinstance(v, str) else ""


def _check_pppoe_ip(sub) -> None:
    """«عنوان IP للبرودباند (PPPoE)» goes out as ``Framed-IP-Address`` — an
    IPv4 attribute — and may not contradict «IP ثابت» (the same attribute)."""
    ip = _ip_text(sub, "pppoe_ip")
    if not ip:
        return
    try:
        ok = isinstance(ipaddress.ip_address(ip), ipaddress.IPv4Address)
    except ValueError:
        ok = False
    if not ok:
        raise RadiusValidationError(
            _tr("عنوان IP للبرودباند يجب أن يكون عنوان IPv4 صالحًا (مثل 10.0.0.5)."))
    static = _ip_text(sub, "static_ip")
    if static and static != ip:
        raise RadiusValidationError(
            _tr("عنوان IP للبرودباند (%(ip)s) يختلف عن «IP ثابت» (%(static)s) — "
                "كلاهما يُرسَل للراوتر عنوانًا ثابتًا واحدًا. اترك أحدهما فارغًا "
                "أو اجعلهما متطابقين.", ip=ip, static=static))


def _check_framed_ip_unique(sub, tenant_id: Optional[int]) -> None:
    """A fixed address belongs to ONE subscriber of the network: the router
    cannot hand the same Framed-IP-Address to two live sessions."""
    ips = {ip for ip in (_ip_text(sub, "static_ip"), _ip_text(sub, "pppoe_ip")) if ip}
    if not ips:
        return
    from ..db.connection import db
    tid = int(tenant_id or getattr(sub, "tenant_id", None) or 1)
    name = str(getattr(sub, "username", "") or "").strip().lower()
    for ip in sorted(ips):
        row = db().execute(
            "SELECT 1 FROM subscribers WHERE tenant_id = ? AND deleted_at IS NULL "
            "AND lower(trim(username)) <> ? "
            "AND (trim(COALESCE(static_ip, '')) = ? OR trim(COALESCE(pppoe_ip, '')) = ?) "
            "LIMIT 1", (tid, name, ip, ip)).fetchone()
        if row:
            raise RadiusValidationError(
                _tr("العنوان %(ip)s مُعطًى لمشتركٍ آخر — العنوان الثابت لا يُعطى "
                    "لحسابين في الشبكة نفسها.", ip=ip))


def validate_subscriber_fields(sub, fields: Optional[Iterable[str]] = None, *,
                               tenant_id: Optional[int] = None) -> None:
    """Check ``fields`` of ``sub`` (all known fields when ``None``)."""
    names = set(fields) if fields is not None else None

    def want(f: str) -> bool:
        return names is None or f in names

    for f, limit in _TEXT_RULES.items():
        if want(f):
            if len(_text(sub, f)) > limit:
                raise RadiusValidationError(_tr('قيمة «%(f)s» أطول من المسموح (%(limit)s حرفًا).', f=f, limit=limit))

    if want("status"):
        st = _text(sub, "status").strip().lower()
        if st not in ALLOWED_STATUSES:
            raise RadiusValidationError(
                _tr("حالة الحساب غير معروفة (enabled، disabled، suspended، banned)."))
    if want("service_type"):
        svc = _text(sub, "service_type").strip().lower()
        if svc and svc not in ALLOWED_SERVICE_TYPES:
            raise RadiusValidationError(_tr("نوع الخدمة غير معروف (hotspot أو pppoe أو both)."))
    if want("email"):
        email = _text(sub, "email").strip()
        if email and not _EMAIL_RE.match(email):
            raise RadiusValidationError(_tr("البريد الإلكترونيّ غير صالح."))
    if want("mobile"):
        mobile = latin_digits(_text(sub, "mobile")).strip()
        if mobile and not _MOBILE_RE.match(mobile):
            raise RadiusValidationError(_tr("رقم الجوال غير صالح (أرقام فقط، ويجوز + في أوّله)."))
    for f in ("static_ip", "pppoe_ip"):
        if want(f):
            v = getattr(sub, f, None)
            if v not in (None, ""):
                if not isinstance(v, str) or not _valid_ip(v.strip()):
                    raise RadiusValidationError(_tr('عنوان IP في «%(f)s» غير صالح.', f=f))
    if want("static_ip") or want("pppoe_ip"):
        _check_pppoe_ip(sub)
        _check_framed_ip_unique(sub, tenant_id)
    if want("mac_lock"):
        v = getattr(sub, "mac_lock", None)
        if v not in (None, ""):
            if not isinstance(v, str):
                raise RadiusValidationError(_tr("قفل MAC يجب أن يكون نصًّا."))
            for mac in [m for m in _MAC_SPLIT_RE.split(v.strip()) if m]:
                if not _MAC_RE.match(mac):
                    raise RadiusValidationError(_tr('عنوان MAC غير صالح: «%(v)s».', v=mac[:40]))
    for f, (label, lo, hi) in _INT_RULES.items():
        if not want(f):
            continue
        v = getattr(sub, f, 0)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int):
            raise RadiusValidationError(_tr('قيمة «%(label)s» يجب أن تكون رقمًا صحيحًا.', label=label))
        if not (lo <= v <= hi):
            raise RadiusValidationError(_tr('قيمة «%(label)s» يجب أن تكون بين %(lo)s و %(hi)s.', label=label, lo=lo, hi=hi))
    if want("expire_at"):
        validate_expiry_range(getattr(sub, "expire_at", None))
    tid = int(tenant_id or getattr(sub, "tenant_id", None) or 1)
    if want("plan_id"):
        pid = getattr(sub, "plan_id", None)
        if pid not in (None, 0):
            if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0 \
                    or not _plan_exists(tid, pid):
                raise RadiusValidationError(_tr("الباقة المحددة غير موجودة."))
    if want("manager_id"):
        mid = getattr(sub, "manager_id", None)
        if mid not in (None, 0):
            if isinstance(mid, bool) or not isinstance(mid, int) or mid < 0 \
                    or not _admin_exists(tid, mid):
                raise RadiusValidationError(_tr("المدير المحدد غير موجود."))


def _plan_exists(tenant_id: int, plan_id: int) -> bool:
    from ..db.connection import db
    if plan_id > 2**62:
        return False
    return bool(db().execute(
        "SELECT 1 FROM access_plans WHERE tenant_id = ? AND id = ? LIMIT 1",
        (tenant_id, plan_id)).fetchone())


def _admin_exists(tenant_id: int, admin_id: int) -> bool:
    from ..db.connection import db
    if admin_id > 2**62:
        return False
    try:
        return bool(db().execute(
            "SELECT 1 FROM admins WHERE id = ? LIMIT 1", (admin_id,)).fetchone())
    except Exception:  # noqa: BLE001 — no admins table (old DB): don't block
        return True
