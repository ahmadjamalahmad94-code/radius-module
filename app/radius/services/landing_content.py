"""محتوى صفحة المنصّة — MT63.

كل ما يراه الزائر على الجذر (العنوان، الوصف، الأزرار، شارات الثقة،
بطاقات المزايا، خطوات البدء، عناوين الأقسام) صار **يُدار من اللوحة**
بلا لمس كود. كان مكتوبًا داخل القالب، فأيّ تعديل تسويقيّ يَستدعي نشرًا.

التخزين: JSON بمفتاح ``platform.landing`` على مساحة المزوّد (الشبكة ١)
— نفس نمط [[tier_config]].

مبدآن لا يُخالفان:
  • **البذرة = ما هو منشور حرفيًّا**، فلا يتغيّر شكل الصفحة عند التفعيل.
  • أيّ خطأ قراءة/تحليل ⇒ البذرة (لا ترفع أبدًا؛ الصفحة عامّة ولا يجوز
    أن تسقط لخللٍ في إعداد).
"""
from __future__ import annotations

import json
import re
from typing import Any

_SETTING_KEY = "platform.landing"
_PLATFORM_TID = 1

_ICON_ALLOWED = re.compile(r"^[a-z0-9-]{1,40}$")

# ── البذرة: نصّ الصفحة المنشور حرفيًّا (لا تُغيّره إلا مع القالب) ──
_SEED: dict[str, Any] = {
    "eyebrow": "شبكتك جاهزة خلال دقائق",
    "title": "أدِر شبكتك ومشتركيك من",
    "title_hl": "مكانٍ واحد",
    "lede": ("منصّة متكاملة لمزوّدي خدمة الإنترنت: مشتركون وبطاقات وباقات وراوترات "
             "وتقارير ماليّة — بعزلٍ تامّ لكل شبكة عن غيرها، وبلا خادمٍ تشتريه ولا "
             "نظامٍ تُنصّبه بنفسك."),
    "cta_primary": "اطلب شبكتك الآن",
    "cta_secondary": "كيف تبدأ؟",
    "trust": [
        "عزل كامل لبيانات كل شبكة",
        "نسخ احتياطيّ تلقائيّ",
        "واجهة عربيّة كاملة",
    ],
    "features_title": "كلّ ما تحتاجه لتشغيل شبكتك",
    "features_sub": ("نظامٌ واحد يجمع ما كنت تُوزّعه على أدوات متفرّقة — "
                     "من المشترك حتى التقرير الماليّ."),
    "features": [
        {"icon": "users", "title": "إدارة المشتركين",
         "text": "اشتراكات وباقات وحدود أجهزة وسرعات، مع تجديدٍ ومتابعة استهلاك لحظيّة لكل مشترك."},
        {"icon": "credit-card", "title": "بطاقات وسوق إلكترونيّ",
         "text": "وَلِّد بطاقات إنترنت واطبعها، أو بِعها إلكترونيًّا عبر متجرٍ يشحن الرصيد تلقائيًّا."},
        {"icon": "network-wired", "title": "تحكّم بالراوترات",
         "text": "ربطٌ مباشر بأجهزة مايكروتيك: تهيئة، مراقبة صحّة، وقطع أو تسريع لحظيّ بلا دخولٍ يدويّ."},
        {"icon": "chart-line", "title": "تقارير وماليّة",
         "text": "تحصيل ورصيد وديون وأرباح، مع تقارير جاهزة تُجيبك عن حال شبكتك اليوم لا بعد شهر."},
        {"icon": "shield-halved", "title": "عزل تامّ بين الشبكات",
         "text": "بيانات شبكتك ومشتركوها لا تُرى من أيّ شبكة أخرى على المنصّة — عزلٌ مفروض في كل استعلام."},
        {"icon": "database", "title": "نسخ احتياطيّ واستعادة",
         "text": "نسخٌ دوريّ تلقائيّ لبيانات شبكتك، واستعادةٌ عند الحاجة بلا توقّف خدمة يُذكر."},
    ],
    "steps_title": "ثلاث خطوات وتبدأ",
    "steps_sub": "لا خادم تشتريه ولا نظام تُنصّبه — نحن نُجهّز، وأنت تُدير.",
    "steps": [
        {"title": "أرسل طلبك",
         "text": "املأ النموذج بالأسفل باسم شبكتك وبيانات تواصلك. لا يستغرق دقيقة."},
        {"title": "نُراجع ونُفعّل",
         "text": "نتواصل معك لتأكيد التفاصيل، ثمّ نُنشئ شبكتك ونُرسل رابطها وبيانات دخولك."},
        {"title": "ابدأ التشغيل",
         "text": "اربط راوترك، أضف باقاتك ومشتركيك، وابدأ البيع من يومك الأوّل."},
    ],
    "pricing_title": "عروض وأسعار",
    "pricing_sub": "ادفع على قدر ما تحتاج — والاتصالات المتزامنة هي المقياس، لا عدد المشتركين.",
    "signup_title": "اطلب شبكتك",
    "signup_sub": ("املأ بياناتك وسنتواصل معك لتفعيل الشبكة. الطلب لا يُنشئ شبكةً "
                   "تلقائيًّا — نراجعه أوّلًا."),
}

# الحقول النصّيّة المفردة وأقصى طولٍ لكلٍّ (حمايةٌ من لصقٍ ضخم).
_TEXT_FIELDS = {
    "eyebrow": 120, "title": 160, "title_hl": 80, "lede": 600,
    "cta_primary": 60, "cta_secondary": 60,
    "features_title": 160, "features_sub": 400,
    "steps_title": 160, "steps_sub": 400,
    "pricing_title": 160, "pricing_sub": 400,
    "signup_title": 160, "signup_sub": 400,
}

_MAX_ITEMS = 24          # سقفٌ عاقل للمزايا/الخطوات/الشارات


def _txt(v: Any, cap: int) -> str:
    return str(v if v is not None else "").strip()[:cap]


def _clean_features(raw: Any) -> list[dict[str, str]]:
    out = []
    for it in (raw or [])[:_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        title = _txt(it.get("title"), 80)
        text = _txt(it.get("text"), 320)
        if not (title or text):
            continue                      # صفٌّ فارغ = حذفٌ مقصود
        icon = _txt(it.get("icon"), 40).lower() or "star"
        if not _ICON_ALLOWED.match(icon):
            icon = "star"
        out.append({"icon": icon, "title": title, "text": text})
    return out


def _clean_steps(raw: Any) -> list[dict[str, str]]:
    out = []
    for it in (raw or [])[:_MAX_ITEMS]:
        if not isinstance(it, dict):
            continue
        title = _txt(it.get("title"), 80)
        text = _txt(it.get("text"), 320)
        if not (title or text):
            continue
        out.append({"title": title, "text": text})
    return out


def _clean(raw: dict) -> dict[str, Any]:
    """يُنقّي المحتوى ويَملأ الناقص من البذرة — فالقالب لا يرى مفتاحًا غائبًا."""
    out: dict[str, Any] = {}
    for f, cap in _TEXT_FIELDS.items():
        out[f] = _txt(raw.get(f, _SEED[f]), cap) or _SEED[f]
    out["trust"] = [t for t in (_txt(x, 90) for x in (raw.get("trust") or [])[:_MAX_ITEMS]) if t]
    out["features"] = _clean_features(raw.get("features"))
    out["steps"] = _clean_steps(raw.get("steps"))
    return out


def get_content() -> dict[str, Any]:
    """محتوى الصفحة الحاليّ (مخزَّن، أو المبذور إن غاب). لا يرفع أبدًا."""
    try:
        from ..db.repos import tenants_repo
        raw = tenants_repo.get_setting(_PLATFORM_TID, _SETTING_KEY, "")
        if not raw:
            return _clean(_SEED)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict) or not parsed:
            return _clean(_SEED)
        return _clean(parsed)
    except Exception:  # noqa: BLE001
        return _clean(_SEED)


def save_content(data: dict, *, by: int = 0) -> dict[str, Any]:
    clean = _clean(data or {})
    from ..db.repos import tenants_repo
    tenants_repo.set_setting(_PLATFORM_TID, _SETTING_KEY,
                             json.dumps(clean, ensure_ascii=False), by=by)
    return clean


def reset_content(*, by: int = 0) -> dict[str, Any]:
    """يُعيد النصّ الأصليّ (زرّ «استعادة الافتراضيّ» في اللوحة)."""
    return save_content(dict(_SEED), by=by)
