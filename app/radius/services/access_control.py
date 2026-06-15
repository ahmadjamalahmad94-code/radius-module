"""«الحظر والتحكم بالدخول» — منطق الإنفاذ (feat/access-control-blocking).

طبقة الخدمة فوق ``access_blocks_repo``. تحوي:
  * منطق نمط المدّة (permanent / daily_window / until) — دوال خالصة قابلة
    للاختبار (``is_block_in_effect``، ``in_daily_window``).
  * مطابقة النطاق (subscriber/group/plan/card_batch/ALL_*/ip/mac).
  * نقطة الإنفاذ ``find_active_block`` التي يستدعيها policy_engine وقت الـauth.
  * الحظر التلقائي على محاولات الفشل المتكرّرة (fail2ban):
    ``register_failed_attempt``.
  * قرّاء إعدادات الأمان (tenants_repo، نفس نمط security.block_random_mac_*).

كل شيء tenant-scoped. الإنفاذ هنا — ليس في الواجهة فقط.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ..db.helpers import parse_dt
from ..db.repos import access_blocks_repo as repo

_LOG = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════════
# مفاتيح إعدادات الأمان (tenant_settings) + الافتراضات
# ════════════════════════════════════════════════════════════════════════
# الحظر التلقائي (fail2ban). تُحفظ/تُقرأ من صفحة «الحظر والتحكم بالدخول».
SK_AUTOBLOCK_ENABLED = "security.autoblock_enabled"          # 0/1
SK_AUTOBLOCK_THRESHOLD = "security.autoblock_threshold"      # عدد المحاولات
SK_AUTOBLOCK_WINDOW_SEC = "security.autoblock_window_sec"    # نافذة العدّ (ث)
SK_AUTOBLOCK_DURATION_MIN = "security.autoblock_duration_min"  # مدّة الحظر (دق)
SK_AUTOBLOCK_TARGET = "security.autoblock_target"            # ip | mac | both

# توقيت الـrandom-MAC موجود مسبقًا في policy_engine
# (security.block_random_mac_subscribers / _cards) — تُعرض في نفس الصفحة.

# ملاحظة دلالات IP: وقت مصادقة RADIUS لا يتوفّر إلا عنوان NAS (الراوتر)،
# لا عنوان جهاز العميل. لذا «حظر IP» (يدويًا أو تلقائيًا) يطابق عنوان الـNAS
# المُرسِل. الحظر التلقائي يفترض MAC افتراضيًا لأنه يميّز جهاز العميل فعلًا؛
# اختيار «ip» قد يحظر كل من خلف ذلك الراوتر — يُترك للمشغّل بوعي.
_DEFAULTS = {
    SK_AUTOBLOCK_ENABLED: "0",
    SK_AUTOBLOCK_THRESHOLD: "5",
    SK_AUTOBLOCK_WINDOW_SEC: "300",
    SK_AUTOBLOCK_DURATION_MIN: "60",
    SK_AUTOBLOCK_TARGET: "mac",
}

_TRUTHY = {"1", "true", "t", "on", "yes", "True"}


def _setting(tenant_id: int, key: str) -> str:
    from ..db.repos import tenants_repo
    return tenants_repo.get_setting(tenant_id, key, _DEFAULTS.get(key, ""))


def _setting_int(tenant_id: int, key: str) -> int:
    try:
        return int(str(_setting(tenant_id, key)).strip())
    except (TypeError, ValueError):
        return int(_DEFAULTS.get(key, "0") or 0)


def autoblock_enabled(tenant_id: int) -> bool:
    return str(_setting(tenant_id, SK_AUTOBLOCK_ENABLED)).strip().lower() in _TRUTHY


# ════════════════════════════════════════════════════════════════════════
# تطبيع المدخلات
# ════════════════════════════════════════════════════════════════════════


def normalize_mac(mac: str) -> str:
    """توحيد صيغة الـMAC: أحرف كبيرة و ':' كفاصل (مطابق لـpolicy_engine)."""
    return (mac or "").strip().upper().replace("-", ":")


def _hm_to_minutes(hm: str) -> Optional[int]:
    """'HH:MM' → دقائق من منتصف الليل. يُعيد None لصيغة غير صالحة."""
    s = (hm or "").strip()
    if not s or ":" not in s:
        return None
    try:
        h, m = s.split(":", 1)
        h, m = int(h), int(m)
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m


# ════════════════════════════════════════════════════════════════════════
# منطق نمط المدّة — دوال خالصة
# ════════════════════════════════════════════════════════════════════════


def in_daily_window(start: str, end: str, now: datetime) -> bool:
    """هل ``now`` ضمن نافذة يومية [start, end]؟ تدعم العبور بعد منتصف الليل.

    أمثلة: (16:00, 08:00) → داخل النافذة من الـ16:00 مساءً حتى الـ08:00 صباحًا.
    (08:00, 16:00) → نهارًا فقط. start==end → اليوم كامل (24س). صيغة غير
    صالحة → True (نفشل بأمان نحو الحظر، فالنافذة ضبطها المشغّل عمدًا)."""
    s = _hm_to_minutes(start)
    e = _hm_to_minutes(end)
    if s is None or e is None:
        return True  # نافذة غير مكتملة → نعتبرها سارية (آمن للحظر)
    cur = now.hour * 60 + now.minute
    if s == e:
        return True            # نافذة 24 ساعة
    if s < e:
        return s <= cur < e    # نفس اليوم
    return cur >= s or cur < e  # عابرة منتصف الليل


def is_block_in_effect(block: dict, now: Optional[datetime] = None, *,
                       tz_offset_hours: float = 0.0) -> bool:
    """هل الحظر ساري المفعول الآن؟ يطبّق نمط المدّة. لا يلمس DB.

    ``now`` بتوقيت UTC. ``until`` يُقارن بـUTC (نُخزّن expires_at بـUTC دائمًا:
    التلقائي وكذلك اليدوي بعد تحويله في create_block_from_input). أما
    ``daily_window`` فنافذة حائطية محلّية يدخلها المشغّل، فنحوّل now إلى
    التوقيت المحلّي (UTC + الإزاحة) قبل مقارنة ساعة اليوم."""
    if not block or not int(block.get("active") or 0):
        return False
    now = now or datetime.utcnow()
    mode = str(block.get("duration_mode") or "permanent")
    if mode == "permanent":
        return True
    if mode == "until":
        raw = str(block.get("expires_at") or "").strip()
        if not raw:
            return True  # until بلا تاريخ → عاملها كدائم (لا نُسقط الحظر صامتًا)
        exp = parse_dt(raw)
        if exp is None:
            return True
        return now < exp
    if mode == "daily_window":
        local = now + timedelta(hours=tz_offset_hours)
        return in_daily_window(block.get("window_start") or "",
                               block.get("window_end") or "", local)
    return True  # نمط غير معروف → ساري (آمن للحظر)


def tz_offset_hours(tenant_id: int) -> float:
    """إزاحة توقيت المستأجر بالساعات (billing.timezone_offset) — للنوافذ
    اليومية الحائطية. الافتراضي 0 (UTC) إن لم يُضبط."""
    from ..db.repos import tenants_repo
    try:
        return float(str(tenants_repo.get_setting(tenant_id, "billing.timezone_offset", "0")).strip() or 0)
    except (TypeError, ValueError):
        return 0.0


# ════════════════════════════════════════════════════════════════════════
# مطابقة النطاق
# ════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AuthContext:
    """ما يلزم لمطابقة الحظور على طلب auth."""
    source: str = "subscriber"          # subscriber | card
    username: str = ""
    group: str = ""
    plan_id: Optional[int] = None
    card_batch_id: Optional[int] = None
    service_type: str = ""              # Hotspot | PPPoE | ...
    nas_ip: str = ""
    mac: str = ""


def block_matches(block: dict, ctx: AuthContext) -> bool:
    """هل ينطبق هذا الحظر (بغضّ النظر عن سريانه الزمني) على هذا السياق؟"""
    bt = str(block.get("block_type") or "")
    target = str(block.get("target") or "")
    if bt == "subscriber":
        return bool(ctx.username) and target == ctx.username
    if bt == "group":
        return bool(ctx.group) and target == ctx.group
    if bt == "plan":
        return ctx.plan_id is not None and target == str(ctx.plan_id)
    if bt == "card_batch":
        return ctx.card_batch_id is not None and target == str(ctx.card_batch_id)
    if bt == "all_subscribers":
        return ctx.source == "subscriber"
    if bt == "all_cards":
        return ctx.source == "card"
    if bt == "all_hotspot":
        return (ctx.service_type or "").lower() == "hotspot"
    if bt == "all_pppoe":
        return (ctx.service_type or "").lower() == "pppoe"
    if bt == "ip":
        return bool(ctx.nas_ip) and target == ctx.nas_ip
    if bt == "mac":
        return bool(ctx.mac) and normalize_mac(target) == normalize_mac(ctx.mac)
    return False


def find_active_block(tenant_id: int, ctx: AuthContext, *,
                      now: Optional[datetime] = None) -> Optional[dict]:
    """يُعيد أول حظر فعّال وساري ينطبق على السياق، أو None.

    لا يكتب في DB (مسار auth ساخن): انتهاء ``until`` يُحتسب منطقيًّا في
    ``is_block_in_effect`` فالمنتهي لا يُنفَّذ أبدًا حتى قبل كنسه. تعطيل
    الصفوف المنتهية (active=0) يتم كسولًا في صفحة الإدارة وعبر
    ``repo.deactivate_expired``. محصّن: أي خطأ يُرجع None (لا نكسر الـauth)."""
    now = now or datetime.utcnow()
    try:
        offset = tz_offset_hours(tenant_id)
        for block in repo.list_blocks(tenant_id, active_only=True):
            if block_matches(block, ctx) and is_block_in_effect(
                    block, now, tz_offset_hours=offset):
                return block
    except Exception:  # noqa: BLE001 — never break auth on the block layer
        _LOG.warning("access_control: find_active_block failed for %s",
                     ctx.username, exc_info=True)
    return None


# ════════════════════════════════════════════════════════════════════════
# الحظر التلقائي على محاولات الفشل المتكرّرة (fail2ban)
# ════════════════════════════════════════════════════════════════════════


def register_failed_attempt(tenant_id: int, *, ip: str = "", mac: str = "",
                            username: str = "",
                            now: Optional[datetime] = None) -> Optional[int]:
    """يسجّل محاولة فاشلة، ويُنشئ حظرًا تلقائيًا إن تجاوز العدّ العتبة.

    يُعيد معرّف الحظر المُنشأ (أو أحدها) أو None. محصّن: أي خطأ يُرجع None.
    العدّ يتم على IP و/أو MAC بحسب ``security.autoblock_target``."""
    now = now or datetime.utcnow()
    try:
        ip = (ip or "").strip()
        mac = normalize_mac(mac)
        repo.record_failure(tenant_id=tenant_id, ip=ip, mac=mac, username=username)
        if not autoblock_enabled(tenant_id):
            return None
        threshold = max(1, _setting_int(tenant_id, SK_AUTOBLOCK_THRESHOLD))
        window_sec = max(1, _setting_int(tenant_id, SK_AUTOBLOCK_WINDOW_SEC))
        # تقليم محصور: احذف ما هو أقدم من النافذة (لا يدخل في العدّ أصلًا) كي
        # لا ينمو الجدول بلا حدّ تحت هجوم. نُبقي هامشًا = ضعف النافذة.
        try:
            cutoff = (now - timedelta(seconds=window_sec * 2)).isoformat() + "Z"
            repo.purge_old_failures(tenant_id, before=cutoff)
        except Exception:  # noqa: BLE001
            pass
        duration_min = max(1, _setting_int(tenant_id, SK_AUTOBLOCK_DURATION_MIN))
        target_kind = str(_setting(tenant_id, SK_AUTOBLOCK_TARGET) or "ip").strip().lower()
        since = (now - timedelta(seconds=window_sec)).isoformat() + "Z"
        expires_at = (now + timedelta(minutes=duration_min)).isoformat() + "Z"

        created: Optional[int] = None
        wants = []
        if target_kind in ("ip", "both") and ip:
            wants.append(("ip", ip))
        if target_kind in ("mac", "both") and mac:
            wants.append(("mac", mac))
        for block_type, value in wants:
            count = repo.count_recent_failures(
                tenant_id, ip=value if block_type == "ip" else None,
                mac=value if block_type == "mac" else None, since=since)
            if count < threshold:
                continue
            if repo.has_active_block(tenant_id, block_type=block_type, target=value):
                continue  # حظر فعّال موجود — لا نكرّر
            created = repo.create_block(
                tenant_id=tenant_id, block_type=block_type, target=value,
                reason=f"حظر تلقائي بعد {count} محاولة فاشلة خلال {window_sec}ث",
                duration_mode="until", expires_at=expires_at, source="auto")
            _LOG.warning("access_control: auto-blocked %s=%s after %d failures",
                         block_type, value, count)
        return created
    except Exception:  # noqa: BLE001
        _LOG.warning("access_control: register_failed_attempt failed", exc_info=True)
        return None


class AccessControlError(ValueError):
    """مدخلات حظر غير صالحة (رسالة عربية للمستخدم)."""


_SCOPES_NEED_TARGET = {"subscriber", "group", "plan", "card_batch", "ip", "mac"}
_SCOPES_ALL = {"all_subscribers", "all_hotspot", "all_cards", "all_pppoe"}

_HOST_RE = re.compile(r"^(?=.{1,253}$)[A-Za-z0-9]([A-Za-z0-9\-.]*[A-Za-z0-9])?$")
_MAC_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def _valid_ip_or_host(value: str) -> bool:
    """IPv4/IPv6 صالح (عبر ipaddress) أو اسم مضيف معقول. يرفض مثل 300.300.x."""
    import ipaddress
    v = (value or "").strip()
    if not v:
        return False
    try:
        ipaddress.ip_address(v)
        return True
    except ValueError:
        return bool(_HOST_RE.match(v)) and not v.replace(".", "").isdigit()


def create_block_from_input(
    *, tenant_id: int, block_type: str, target: str = "", reason: str = "",
    duration_mode: str = "permanent", window_start: str = "", window_end: str = "",
    expires_at: str = "", created_by: int = 0,
) -> int:
    """يتحقّق من مدخلات نموذج الحظر ثم يُنشئه. يرفع ``AccessControlError``."""
    block_type = (block_type or "").strip()
    if block_type not in repo.BLOCK_TYPES:
        raise AccessControlError("نطاق الحظر غير صالح.")
    duration_mode = (duration_mode or "permanent").strip()
    if duration_mode not in repo.DURATION_MODES:
        raise AccessControlError("نمط مدّة الحظر غير صالح.")

    target = (target or "").strip()
    if block_type in _SCOPES_ALL:
        target = ""  # النطاقات الشاملة لا تحمل هدفًا
    elif block_type in _SCOPES_NEED_TARGET:
        if not target:
            raise AccessControlError("الهدف مطلوب لهذا النطاق.")
        if block_type == "mac":
            target = normalize_mac(target)
            if not _MAC_RE.match(target):
                raise AccessControlError("صيغة MAC غير صالحة (مثال: AA:BB:CC:DD:EE:FF).")
        elif block_type == "ip":
            if not _valid_ip_or_host(target):
                raise AccessControlError("صيغة العنوان غير صالحة.")
        elif block_type in ("plan", "card_batch"):
            if not str(target).isdigit():
                raise AccessControlError("المعرّف يجب أن يكون رقمًا.")

    if duration_mode == "daily_window":
        if _hm_to_minutes(window_start) is None or _hm_to_minutes(window_end) is None:
            raise AccessControlError("نافذة الحظر اليومية تحتاج وقتي بداية ونهاية صحيحين (HH:MM).")
    elif duration_mode == "until":
        parsed = parse_dt((expires_at or "").strip())
        if parsed is None:
            raise AccessControlError("حدّد تاريخ/وقت انتهاء صالحًا.")
        # المُدخَل من <input datetime-local> توقيت محلّي حائطي — نحوّله إلى
        # UTC (بطرح إزاحة المستأجر) ونُخزّنه موحّدًا (isoformat + Z) كي يطابق
        # الحظر التلقائي ومقارنة is_block_in_effect (التي تعمل بـUTC).
        expires_at = (parsed - timedelta(hours=tz_offset_hours(tenant_id))).isoformat() + "Z"
    else:
        window_start = window_end = expires_at = ""  # permanent: لا حقول مدّة

    return repo.create_block(
        tenant_id=tenant_id, block_type=block_type, target=target,
        reason=(reason or "").strip()[:300], duration_mode=duration_mode,
        window_start=window_start, window_end=window_end, expires_at=expires_at,
        source="manual", created_by=created_by)


__all__ = [
    "AuthContext", "normalize_mac", "AccessControlError", "create_block_from_input",
    "in_daily_window", "is_block_in_effect", "block_matches", "find_active_block",
    "register_failed_attempt", "autoblock_enabled",
    "SK_AUTOBLOCK_ENABLED", "SK_AUTOBLOCK_THRESHOLD", "SK_AUTOBLOCK_WINDOW_SEC",
    "SK_AUTOBLOCK_DURATION_MIN", "SK_AUTOBLOCK_TARGET",
]
