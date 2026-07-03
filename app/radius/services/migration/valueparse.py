"""محلّلات قيم متسامحة ومتعدّدة اللغات — تحوّل نصوص المصدر إلى أرقام/تواريخ
HobeRadius، أو تُعلِن الفشل بوضوح (لا قيمة خاطئة صامتة).

كل دالّة تُعيد كائن ``Parsed`` فيه ``value`` (المُحوَّل)، ``ok`` (نجح؟)،
``unlimited`` (إن دلّ النصّ على «غير محدود»)، و``raw`` (الأصل). المتّصِل
يقرّر: يستعمل القيمة، أو يُعلّم الصفّ «يحتاج انتباه المالك» عند ``not ok``.

مصمَّمة لتعميم أيّ مصدر (عربيّ/إنجليزيّ) لا لتخصيص «Hobe Hub»:
  • السرعات: Mbps/Kbps/Gbps/bps + M/K/G + ميجابت/كيلوبت/جيجابت + «غير محدود».
  • الأحجام: B/KB/MB/GB/TB + بايت/كيلوبايت/ميجابايت/جيجابايت/تيرابايت + «غير محدود».
  • المدد: y/mo/w/d/h/m + سنة/شهر/أسبوع/يوم/ساعة/دقيقة (مفرد/جمع) + «غير محدود».
  • المال: أيّ صيغة عشريّة/فواصل آلاف/عملة.
  • التواريخ: عدّة صيَغ ISO/محليّة + epoch.
  • المنطقيّات/الحالة: مرادفات مفعّل/معطّل.

دوال خالصة — لا Flask/DB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Parsed:
    value: object = None
    ok: bool = False
    unlimited: bool = False
    raw: str = ""

    def __bool__(self) -> bool:            # يسهّل `if parsed:` = نجح وليس unlimited
        return self.ok


# ── مؤشّرات «غير محدود» / «فارغ» ──────────────────────────────────────
_UNLIMITED = {
    "unlimited", "unlim", "infinite", "infinity", "no limit", "nolimit", "none",
    "n/a", "na", "-", "--", "---",
    "غير محدود", "غيرمحدود", "بلا حدود", "لا محدود", "لانهائي", "مفتوح", "بدون حد",
    "بدون حدود", "لا يوجد",
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def _is_unlimited(s: str) -> bool:
    return _norm(s) in _UNLIMITED


def _num(s: str) -> Optional[float]:
    """يستخرج أوّل رقم عشريّ من نصّ (يتسامح مع فواصل الآلاف). None لو لا رقم."""
    t = str(s or "").strip()
    if not t:
        return None
    # وحّد الفاصلة العربيّة والفواصل.
    t = t.replace("٬", ",").replace("،", ",").replace("٫", ".")
    # التقط أوّل تسلسل رقميّ (مع فواصل/نقطة).
    m = re.search(r"[-+]?\d[\d,\.\s]*", t)
    if not m:
        return None
    token = m.group(0).strip()
    # عامل الفواصل: لو النقطة والفاصلة موجودتان، الأخيرة هي العشريّة.
    if "." in token and "," in token:
        if token.rfind(".") > token.rfind(","):   # 1,234.56
            token = token.replace(",", "")
        else:                                       # 1.234,56 (أوروبيّ)
            token = token.replace(".", "").replace(",", ".")
    elif "," in token:
        # فاصلة وحدها: آلاف لو تتبعها 3 أرقام دومًا، وإلّا عشريّة.
        if re.fullmatch(r"[-+]?\d{1,3}(,\d{3})+", token.replace(" ", "")):
            token = token.replace(",", "")
        else:
            token = token.replace(",", ".")
    token = token.replace(" ", "")
    try:
        return float(token)
    except ValueError:
        return None


# ── السرعة → kbps ────────────────────────────────────────────────────
_SPEED_UNITS = [
    (("gbps", "gbit", "gb/s", "g", "جيجابت", "غيغابت", "جيجا"), 1_000_000),
    (("mbps", "mbit", "mb/s", "m", "ميجابت", "ميغابت", "ميجا", "ميغا"), 1_000),
    (("kbps", "kbit", "kb/s", "k", "كيلوبت", "كيلو"), 1),
    (("bps", "bit", "بت", "بايت/ث"), 0.001),
]


def parse_speed(s: str) -> Parsed:
    """→ kbps (int). «غير محدود» → 0 unlimited. يدعم Mbps/Kbps/Gbps/bps و M/K/G."""
    raw = str(s or "")
    if _is_unlimited(raw):
        return Parsed(value=0, ok=True, unlimited=True, raw=raw)
    n = _num(raw)
    if n is None:
        return Parsed(ok=False, raw=raw)
    low = _norm(raw)
    for names, factor in _SPEED_UNITS:
        for name in names:
            if name in low:
                kbps = n * factor
                val = int(round(kbps)) if (kbps == 0 or kbps >= 1) else 1
                return Parsed(value=val, ok=True, raw=raw)
    # لا وحدة صريحة — رقم مجرّد. الاصطلاح الشائع في ملفّات المزوّدين kbps
    # (rate-limit)، لكن قد يكون bps للأرقام الضخمة. نستدلّ: ≥ 1e6 ⇒ bps.
    if n >= 1_000_000:
        return Parsed(value=int(round(n / 1000)), ok=True, raw=raw)
    return Parsed(value=int(round(n)), ok=True, raw=raw)


def parse_rate_limit(s: str) -> tuple[Parsed, Parsed]:
    """يحلّل قيمة ``Mikrotik-Rate-Limit`` (radreply/radgroupreply) → (نزول, رفع).

    الصيغة القياسيّة لـMikroTik: عدّة حقول مفصولة بمسافات، أوّلها الحدّ الأساس
    ``rx-rate/tx-rate`` (نزول/رفع من منظور المشترك). مثال::

        "7500k/7500k 0k/0k 0k/0k 0/0 8"   →  الحقل-1 = "7500k/7500k"

    نأخذ **الحقل الأوّل** ونقسمه على ``/``: الطرف الأوّل = النزول، الثاني = الرفع.
    كلّ طرف يُمرَّر عبر :func:`parse_speed` (k=kbps، m=mbps، g=gbps، بلا وحدة=رقم
    خام). ``0/0`` → (0, 0) أي «غير محدود» في HobeRadius. طرفٌ مفقود يَرِث الآخر.

    السرعة تُقرأ من هذه السمة المخزَّنة (ما تُنفّذه FreeRADIUS فعلًا) لا من اسم
    الباقة ولا من أعمدة profiles (التي قد تكون معكوسة النزول/الرفع)."""
    raw = str(s or "").strip()
    if not raw:
        empty = Parsed(ok=False, raw=raw)
        return empty, empty
    field1 = raw.split()[0]
    down_s, sep, up_s = field1.partition("/")
    down = parse_speed(down_s)
    up = parse_speed(up_s) if (sep and up_s.strip()) else parse_speed(down_s)
    return down, up


# ── حجم البيانات → MB ────────────────────────────────────────────────
_SIZE_UNITS = [
    (("tb", "tib", "tera", "تيرابايت", "تيرا"), 1024 * 1024),
    (("gb", "gib", "giga", "جيجابايت", "غيغابايت", "جيجا", "غيغا"), 1024),
    (("mb", "mib", "mega", "ميجابايت", "ميغابايت", "ميجا", "ميغا"), 1),
    (("kb", "kib", "kilo", "كيلوبايت", "كيلو"), 1 / 1024),
    (("byte", "bytes", "b", "بايت"), 1 / (1024 * 1024)),
]


def parse_data_size(s: str) -> Parsed:
    """→ ميغابايت (int). «غير محدود»/«--» → 0 unlimited."""
    raw = str(s or "")
    if _is_unlimited(raw):
        return Parsed(value=0, ok=True, unlimited=True, raw=raw)
    n = _num(raw)
    if n is None:
        return Parsed(ok=False, raw=raw)
    low = _norm(raw)
    for names, factor in _SIZE_UNITS:
        for name in names:
            if re.search(r"(?<![a-z])" + re.escape(name) + r"(?![a-z])", low):
                mb = n * factor
                # قيمة موجبة تُقرَّب إلى 0 ⇒ نُبقيها 1 (0 = «غير محدود» في
                # HobeRadius، فلا نحوّل كوتة صغيرة إلى «بلا حدّ» خطأً).
                val = int(round(mb)) if (mb == 0 or mb >= 1) else 1
                return Parsed(value=val, ok=True, raw=raw)
    # لا وحدة — نفترض ميغابايت (الأكثر شيوعًا في حقول الكوتة).
    return Parsed(value=int(round(n)), ok=True, raw=raw)


# ── المدّة → أيّام (+ دقائق) ──────────────────────────────────────────
_DUR_UNITS = [
    (("y", "yr", "yrs", "year", "years", "سنة", "سنوات", "سنه", "عام", "أعوام"), 365 * 24 * 60),
    (("mo", "mon", "month", "months", "شهر", "أشهر", "اشهر", "شهور"), 30 * 24 * 60),
    (("w", "wk", "week", "weeks", "أسبوع", "اسبوع", "أسابيع", "اسابيع"), 7 * 24 * 60),
    (("d", "day", "days", "يوم", "أيام", "ايام", "يومًا", "يوما"), 24 * 60),
    (("h", "hr", "hrs", "hour", "hours", "ساعة", "ساعات", "ساعه", "س"), 60),
    (("m", "min", "mins", "minute", "minutes", "دقيقة", "دقائق", "دقيقه", "د"), 1),
]


def parse_duration(s: str) -> Parsed:
    """→ dict{days,minutes}. «غير محدود» → 0 unlimited. «10 أشهر»→300 يومًا."""
    raw = str(s or "")
    if _is_unlimited(raw):
        return Parsed(value={"days": 0, "minutes": 0}, ok=True, unlimited=True, raw=raw)
    low = _norm(raw)
    total_min = 0.0
    found = False
    # صيَغ مركّبة «1y 2mo 3d» أو «1 شهر و10 أيام».
    for m in re.finditer(r"([-+]?\d+(?:\.\d+)?)\s*([A-Za-z؀-ۿ]+)", low):
        val = float(m.group(1))
        unit = m.group(2)
        for names, factor in _DUR_UNITS:
            if unit in names:
                total_min += val * factor
                found = True
                break
    if not found:
        n = _num(raw)
        if n is None:
            return Parsed(ok=False, raw=raw)
        # رقم مجرّد — نفترض أيّامًا.
        total_min = n * 24 * 60
    days = int(round(total_min / (24 * 60)))
    return Parsed(value={"days": days, "minutes": int(round(total_min))}, ok=True, raw=raw)


# ── المال → float ────────────────────────────────────────────────────
def parse_money(s: str) -> Parsed:
    raw = str(s or "")
    if _norm(raw) in ("", "-", "--", "n/a", "na"):
        return Parsed(value=0.0, ok=True, raw=raw)
    n = _num(raw)
    if n is None:
        return Parsed(ok=False, raw=raw)
    return Parsed(value=float(n), ok=True, raw=raw)


# ── التاريخ → datetime (UTC-naive) ───────────────────────────────────
_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y",
    "%d.%m.%Y", "%b %d %Y", "%b %d, %Y", "%d %b %Y", "%Y%m%d",
    # صيَغ لوحات هوتسبوت (adv) بالوقت: «21 Jul 2026 13:42:23».
    "%d %b %Y %H:%M:%S", "%d %b %Y %H:%M", "%b %d %Y %H:%M:%S",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y",
)


def parse_date(s: str) -> Parsed:
    raw = str(s or "").strip()
    if not raw or _norm(raw) in ("--", "-", "n/a", "na", "0000-00-00", "0000-00-00 00:00:00"):
        return Parsed(ok=False, raw=raw)
    # epoch (ثوانٍ) — نطاق يونكس المعقول 1e9..2e9 (2001..2033) كي لا نلتقط
    # أرقام الهواتف (تبدأ عادةً بـ0/5/9 وتقع خارج هذا النطاق).
    if re.fullmatch(r"1\d{9}", raw):
        try:
            return Parsed(value=datetime.utcfromtimestamp(int(raw)), ok=True, raw=raw)
        except (ValueError, OSError, OverflowError):
            pass
    cleaned = re.sub(r"\s+", " ", raw)
    for fmt in _DATE_FORMATS:
        try:
            return Parsed(value=datetime.strptime(cleaned, fmt), ok=True, raw=raw)
        except ValueError:
            continue
    return Parsed(ok=False, raw=raw)


# ── منطقيّ / حالة ────────────────────────────────────────────────────
_TRUE = {"1", "true", "yes", "y", "on", "enabled", "enable", "active", "ok",
         "مفعل", "مفعّل", "نشط", "فعال", "فعّال", "نعم", "تشغيل"}
_FALSE = {"0", "false", "no", "n", "off", "disabled", "disable", "inactive",
          "blocked", "expired", "معطل", "معطّل", "موقوف", "محظور", "منتهي",
          "غير مفعل", "لا", "ايقاف", "إيقاف"}


def parse_bool(s: str) -> Parsed:
    low = _norm(s)
    if low in _TRUE:
        return Parsed(value=True, ok=True, raw=str(s))
    if low in _FALSE:
        return Parsed(value=False, ok=True, raw=str(s))
    if "disab" in low or "block" in low or "expir" in low:
        return Parsed(value=False, ok=True, raw=str(s))
    if "enab" in low or "activ" in low:
        return Parsed(value=True, ok=True, raw=str(s))
    return Parsed(ok=False, raw=str(s))


def parse_status(s: str) -> str:
    """→ 'enabled'|'disabled' (افتراض enabled عند الغموض — لا نُعطّل بلا يقين)."""
    p = parse_bool(s)
    if p.ok:
        return "enabled" if p.value else "disabled"
    return "enabled"


# ── إشارات الحالة الصريحة في نصّ المصدر (تُميّز «معطّل» عن «منتهي») ──────
_DISABLED_HINTS = ("disab", "block", "suspend", "banned", "معطل", "معطّل",
                   "موقوف", "محظور", "موقف")
_EXPIRED_HINTS = ("expir", "منتهي", "انتهى", "منتهية")
_ENABLED_HINTS = ("enab", "activ", "مفعل", "مفعّل", "نشط", "فعال", "فعّال")


def status_signal(raw) -> str:
    """يُصنّف نصّ حالة المصدر إلى إشارة صريحة واحدة أو «» عند غياب الإشارة.

    → 'disabled' | 'expired' | 'enabled' | '' (لا إشارة).

    مُنفصلة عن :func:`parse_status` كي نُميّز «معطّل» (حظر صريح) عن «منتهي»
    (انتهاء صلاحية) — parse_bool يخلطهما (كلاهما False). الأولويّة:
    disabled > expired > enabled (الحظر الصريح يَغلب الانتهاء)."""
    low = _norm(raw)
    if not low:
        return ""
    if low in _FALSE and low not in _EXPIRED_HINTS:
        # 0/false/no/off/blocked… لكن ليس «expired» نفسها.
        if any(h in low for h in _EXPIRED_HINTS):
            return "expired"
        return "disabled"
    if any(h in low for h in _DISABLED_HINTS):
        return "disabled"
    if any(h in low for h in _EXPIRED_HINTS):
        return "expired"
    if low in _TRUE or any(h in low for h in _ENABLED_HINTS):
        return "enabled"
    return ""


def derive_status(raw_status, *, expire_at=None, now=None) -> str:
    """يشتقّ حالة المشترك من إشارة الحالة الصريحة + تاريخ الانتهاء.

    الأولويّة (مطابِقة لسلوك المصدر):
      1. حظر صريح (disabled/blocked)         → 'disabled'
      2. انتهاء صريح أو ``expire_at`` ماضٍ    → 'expired'
      3. تفعيل صريح                          → 'enabled'
      4. غير ذلك                             → 'enabled'

    الحظر يَغلب الانتهاء («المعطّل يظلّ معطّلًا» ولو انتهت صلاحيته). الانتهاء
    يُشتقّ من التاريخ الماضي حتى لو لم يحمل المصدر عمود حالة — وهذا هو جوهر
    إصلاح «الكلّ فعّال»: منتهي الصلاحية = expire_at < now."""
    sig = status_signal(raw_status)
    if sig == "disabled":
        return "disabled"
    if sig == "expired":
        return "expired"
    if expire_at is not None and now is not None and expire_at < now:
        return "expired"
    return "enabled"


__all__ = [
    "Parsed", "parse_speed", "parse_rate_limit", "parse_data_size",
    "parse_duration", "parse_money", "parse_date", "parse_bool", "parse_status",
    "status_signal", "derive_status",
]
