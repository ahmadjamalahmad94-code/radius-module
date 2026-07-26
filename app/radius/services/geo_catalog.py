"""MT67 — كتالوج الدول والمناطق الزمنية (مصدرٌ واحد للقوائم المنسدلة).

كان حقل «المنطقة الزمنية» نصًّا حرًّا: خطأ حرفٍ واحد (`Asia/amman`) يَجعل
كل التواريخ المحليّة والجداول الزمنيّة تسقط للـUTC صامتةً. والدولة لم تكن
تُسأَل أصلًا رغم أنّها تُحدّد المنطقة الزمنية والعملة ولهجة التواصل.

القائمتان مرتّبتان بالأولويّة الفعليّة لسوق المنتج (العربيّة أوّلًا) ثمّ
البقيّة، ولكل دولة **منطقتها الزمنية الافتراضيّة** كي تُملأ تلقائيًّا عند
الاختيار — فلا يَكتبها المستخدم يدويًّا.

قاعدة توافق: قيمةٌ محفوظةٌ خارج القائمة (نسخةٌ قديمة أو تحريرٌ يدويّ) لا
تُفقَد — `timezone_options()` تُضيفها في رأس القائمة بدل أن تَبتلعها.
"""
from __future__ import annotations

# (رمز ISO، الاسم العربيّ، المنطقة الزمنية الافتراضيّة)
COUNTRIES: tuple[tuple[str, str, str], ...] = (
    # ── الوطن العربيّ ──
    ("JO", "الأردن", "Asia/Amman"),
    ("PS", "فلسطين", "Asia/Hebron"),
    ("SA", "السعودية", "Asia/Riyadh"),
    ("AE", "الإمارات", "Asia/Dubai"),
    ("QA", "قطر", "Asia/Qatar"),
    ("KW", "الكويت", "Asia/Kuwait"),
    ("BH", "البحرين", "Asia/Bahrain"),
    ("OM", "عُمان", "Asia/Muscat"),
    ("YE", "اليمن", "Asia/Aden"),
    ("IQ", "العراق", "Asia/Baghdad"),
    ("SY", "سوريا", "Asia/Damascus"),
    ("LB", "لبنان", "Asia/Beirut"),
    ("EG", "مصر", "Africa/Cairo"),
    ("SD", "السودان", "Africa/Khartoum"),
    ("LY", "ليبيا", "Africa/Tripoli"),
    ("TN", "تونس", "Africa/Tunis"),
    ("DZ", "الجزائر", "Africa/Algiers"),
    ("MA", "المغرب", "Africa/Casablanca"),
    ("MR", "موريتانيا", "Africa/Nouakchott"),
    ("SO", "الصومال", "Africa/Mogadishu"),
    ("DJ", "جيبوتي", "Africa/Djibouti"),
    ("KM", "جزر القمر", "Indian/Comoro"),
    # ── الجوار والأسواق المجاورة ──
    ("TR", "تركيا", "Europe/Istanbul"),
    ("IR", "إيران", "Asia/Tehran"),
    ("PK", "باكستان", "Asia/Karachi"),
    ("AF", "أفغانستان", "Asia/Kabul"),
    ("IN", "الهند", "Asia/Kolkata"),
    ("BD", "بنغلاديش", "Asia/Dhaka"),
    ("ID", "إندونيسيا", "Asia/Jakarta"),
    ("MY", "ماليزيا", "Asia/Kuala_Lumpur"),
    ("AZ", "أذربيجان", "Asia/Baku"),
    ("KZ", "كازاخستان", "Asia/Almaty"),
    ("UZ", "أوزبكستان", "Asia/Tashkent"),
    # ── أفريقيا ──
    ("NG", "نيجيريا", "Africa/Lagos"),
    ("KE", "كينيا", "Africa/Nairobi"),
    ("ET", "إثيوبيا", "Africa/Addis_Ababa"),
    ("TZ", "تنزانيا", "Africa/Dar_es_Salaam"),
    ("GH", "غانا", "Africa/Accra"),
    ("SN", "السنغال", "Africa/Dakar"),
    ("ZA", "جنوب أفريقيا", "Africa/Johannesburg"),
    # ── أوروبا وأمريكا ──
    ("GB", "المملكة المتحدة", "Europe/London"),
    ("DE", "ألمانيا", "Europe/Berlin"),
    ("FR", "فرنسا", "Europe/Paris"),
    ("NL", "هولندا", "Europe/Amsterdam"),
    ("ES", "إسبانيا", "Europe/Madrid"),
    ("IT", "إيطاليا", "Europe/Rome"),
    ("SE", "السويد", "Europe/Stockholm"),
    ("RO", "رومانيا", "Europe/Bucharest"),
    ("RU", "روسيا", "Europe/Moscow"),
    ("UA", "أوكرانيا", "Europe/Kiev"),
    ("US", "الولايات المتحدة", "America/New_York"),
    ("CA", "كندا", "America/Toronto"),
    ("BR", "البرازيل", "America/Sao_Paulo"),
    ("AU", "أستراليا", "Australia/Sydney"),
)

_BY_CODE = {c: (n, tz) for c, n, tz in COUNTRIES}

# مناطق زمنية إضافيّة لا تُغطّيها الدول أعلاه (دولٌ بأكثر من منطقة، وUTC).
_EXTRA_ZONES: tuple[tuple[str, str], ...] = (
    ("UTC", "التوقيت العالميّ الموحّد (UTC)"),
    ("Asia/Gaza", "غزّة"),
    ("Asia/Jerusalem", "القدس"),
    ("Africa/Kampala", "كمبالا"),
    ("America/Chicago", "شيكاغو"),
    ("America/Denver", "دنفر"),
    ("America/Los_Angeles", "لوس أنجلوس"),
    ("Europe/Lisbon", "لشبونة"),
    ("Asia/Singapore", "سنغافورة"),
    ("Asia/Shanghai", "شنغهاي"),
    ("Asia/Tokyo", "طوكيو"),
)


# MT68 — محارف العزل الاتّجاهيّ: FSI … PDI. داخل <option> لا تنفع <bdi>
# (لا وسوم فيها)، وبدون العزل يَقلب خوارزم bidi ترتيب «الأردن — Asia/Amman»
# في صفحةٍ عربيّة فيَظهر مشوَّشًا. كلّ مقطعٍ يُعزَل عن جاره.
_FSI, _PDI = "⁨", "⁩"


def _pair(right: str, left: str) -> str:
    """تسميةٌ مختلطة آمنة اتّجاهيًّا: «عربيّ — لاتينيّ»."""
    return f"{_FSI}{right}{_PDI} — {_FSI}{left}{_PDI}"


def _code_str(v) -> str:
    """نصٌّ مقصوصٌ من أيّ مُدخَل (بلا رفع حالة — المعرّفات حسّاسة للحالة)."""
    if v is None or isinstance(v, bool):
        return ""
    if not isinstance(v, str):
        try:
            v = str(v)
        except Exception:  # noqa: BLE001
            return ""
    return v.strip()


def country_options() -> list[tuple[str, str]]:
    """[(رمز، اسم عربيّ)] بترتيب الكتالوج (العربيّة أوّلًا)."""
    return [(c, n) for c, n, _ in COUNTRIES]


def _code(v) -> str:
    """رمزٌ نظيفٌ من أيّ مُدخَل — الدوالّ تُستدعى من نماذج وAPI وعمّال،
    فقيمةٌ غير نصّيّة يجب ألّا تُسقط أيّ مسار."""
    if v is None or isinstance(v, bool):
        return ""
    if not isinstance(v, str):
        try:
            v = str(v)
        except Exception:  # noqa: BLE001
            return ""
    return v.strip().upper()


def country_name(code) -> str:
    """الاسم العربيّ للرمز — أو الرمز نفسه إن كان مجهولًا (لا نُخفي بيانات)."""
    c = _code(code)
    row = _BY_CODE.get(c)
    return row[0] if row else c


def timezone_for_country(code) -> str:
    """المنطقة الزمنية الافتراضيّة للدولة (فارغة إن مجهولة)."""
    row = _BY_CODE.get(_code(code))
    return row[1] if row else ""


def country_timezone_map() -> dict[str, str]:
    """خريطة رمز→منطقة زمنية — يستعملها JS لملء الحقل عند الاختيار."""
    return {c: tz for c, _, tz in COUNTRIES}


def timezone_options(current: str = "") -> list[tuple[str, str]]:
    """[(المعرّف، التسمية)] — مناطق الدول + الإضافيّة، بلا تكرار.

    ``current`` قيمةٌ محفوظة: إن كانت خارج الكتالوج تُوضَع في الرأس كي لا
    تُفقَد بالحفظ (نسخةٌ قديمة أو ضبطٌ يدويّ مقصود).
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for code, name, tz in COUNTRIES:
        if tz in seen:
            continue
        seen.add(tz)
        out.append((tz, _pair(name, tz)))
    for tz, label in _EXTRA_ZONES:
        if tz in seen:
            continue
        seen.add(tz)
        out.append((tz, _pair(label, tz)))
    cur = _code_str(current)
    if cur and cur not in seen:
        out.insert(0, (cur, _pair("محفوظة", cur)))
    return out


def is_known_timezone(tz: str) -> bool:
    return (tz or "").strip() in {t for _, t in country_timezone_map().items()} | {
        z for z, _ in _EXTRA_ZONES}


def normalize_country(code) -> str:
    """رمزٌ صالحٌ بالأحرف الكبيرة، أو '' — لا نَحفظ قيمًا خارج الكتالوج."""
    c = _code(code)
    return c if c in _BY_CODE else ""
