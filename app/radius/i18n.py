"""أساس التدويل (i18n) لوحدة RADIUS — Flask-Babel.

لماذا Flask-Babel؟
    - الحل القياسي لـ Flask: يدمج gettext مع Jinja تلقائيًا (`{{ _('...') }}`،
      `{% trans %}`) ويوفّر أداة `pybabel` لاستخراج/تحديث/ترجمة النصوص.
    - خفيف جدًا (≈10KB) ومبني فوق Babel الناضج المدقّق.
    - لا يفرض بنية: نتحكّم بمنتقي اللغة والاتجاه والخط بأنفسنا.

النموذج المعتمد (مهم):
    لغة المصدر = العربية. أي أن النص العربي نفسه هو الـ msgid، وكتالوج
    الإنجليزية (translations/en) يترجمه. هذا يجعل التعميم *ميكانيكيًا*:
    تغلّف النص العربي بـ `_( )` فقط، و pybabel يلتقطه، ثم تُملأ ترجمته
    الإنجليزية. عند اختيار العربية لا نحتاج كتالوجًا (gettext يُرجع الـ
    msgid = العربية)، فالموقع يبقى عربيًا 100% افتراضيًا — الترجمة طبقة فوقية.

سلّم أولوية اختيار اللغة (locale selector):
    1. session['locale']            ← اختيار المستخدم الحالي من قائمة الهيدر
    2. لغة المسؤول المسجَّل           ← admins.locale (عمود migration 104)
    3. general.default_locale        ← الإعداد العام (tenant_settings)
    4. 'ar'                          ← السقوط النهائي الآمن
"""
from __future__ import annotations

from flask import g, request, session
from flask_babel import Babel

# ─────────────── الثوابت ───────────────

#: اللغات المدعومة. لإضافة لغة: أضف رمزها هنا + مجلد translations/<code> + سطر
#: في LANGUAGES أدناه. لا شيء آخر في الكود يحتاج تعديلًا (انظر I18N.md).
SUPPORTED_LOCALES: tuple[str, ...] = ("ar", "en", "fr", "tr", "es")

#: لغة المصدر/السقوط — يجب أن تبقى أول عنصر منطقيًا في كل المسارات.
DEFAULT_LOCALE = "ar"

#: اللغات ذات الكتابة من اليمين لليسار (RTL). أي لغة خارجها = LTR.
RTL_LOCALES: frozenset[str] = frozenset({"ar", "he", "fa", "ur"})

#: بيانات العرض لكل لغة (لقائمة مبدّل اللغة بالهيدر): الاسم بلغته + رمز/علم.
LANGUAGES: dict[str, dict[str, str]] = {
    "ar": {"name": "العربية", "flag": "🇸🇦", "dir": "rtl"},
    "en": {"name": "English", "flag": "🇬🇧", "dir": "ltr"},
    "fr": {"name": "Français", "flag": "🇫🇷", "dir": "ltr"},
    "tr": {"name": "Türkçe", "flag": "🇹🇷", "dir": "ltr"},
    "es": {"name": "Español", "flag": "🇪🇸", "dir": "ltr"},
}

babel = Babel()


# ─────────────── منتقي اللغة ───────────────

def select_locale() -> str:
    """يحسم لغة الطلب الحالي حسب سلّم الأولوية الموثّق أعلاه.

    آمن تمامًا: أي خطأ في القراءة (جلسة/قاعدة بيانات) يسقط للعربية بدل كسر
    الصفحة — الترجمة لا تكسر شيئًا أبدًا."""
    # 1) اختيار المستخدم الصريح من القائمة (محفوظ في الجلسة)
    try:
        chosen = (session.get("locale") or "").strip().lower()
        if chosen in SUPPORTED_LOCALES:
            return chosen
    except Exception:  # noqa: BLE001
        pass

    # 2) لغة المسؤول المسجَّل (عمود admins.locale)
    try:
        admin_locale = (session.get("admin_locale") or "").strip().lower()
        if admin_locale in SUPPORTED_LOCALES:
            return admin_locale
    except Exception:  # noqa: BLE001
        pass

    # 3) الإعداد العام general.default_locale
    try:
        from .core.tenant import DEFAULT_TENANT_ID
        from .db.repos import tenants_repo
        tid = int(getattr(g, "tenant_id", None) or session.get("tenant_id") or DEFAULT_TENANT_ID)
        default = (tenants_repo.get_setting(tid, "general.default_locale", "") or "").strip().lower()
        if default in SUPPORTED_LOCALES:
            return default
    except Exception:  # noqa: BLE001
        pass

    # 4) السقوط النهائي
    return DEFAULT_LOCALE


def current_locale() -> str:
    """رمز اللغة الفعّالة للطلب الحالي كنص ('ar'/'en'). يُستخدم في القوالب."""
    try:
        from flask_babel import get_locale as _gl
        loc = _gl()
        code = (str(loc) if loc else DEFAULT_LOCALE).split("_")[0].lower()
        return code if code in SUPPORTED_LOCALES else DEFAULT_LOCALE
    except Exception:  # noqa: BLE001
        return DEFAULT_LOCALE


def text_dir(locale: str | None = None) -> str:
    """اتجاه الكتابة ('rtl'/'ltr') للغة المعطاة أو للغة الطلب الحالي."""
    code = (locale or current_locale()).split("_")[0].lower()
    return "rtl" if code in RTL_LOCALES else "ltr"


# ─────────────── التهيئة ───────────────

def init_i18n(app) -> None:
    """يهيّئ Flask-Babel على الـ app ويحقن متغيّرات القوالب (dir/lang/font).

    يُستدعى مرّة واحدة من create_app. كل ما يُحقن هنا متاح في *كل* قالب:
        - get_locale()  : رمز اللغة الحالية
        - text_dir      : 'rtl' أو 'ltr' (للسمة dir على <html>)
        - LANGUAGES     : خريطة اللغات لقائمة المبدّل
        - SUPPORTED_LOCALES
    """
    # دليل الترجمات النسبي لجذر التطبيق (app/) — translations/ بجذر المشروع.
    import os
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    app.config.setdefault("BABEL_DEFAULT_LOCALE", DEFAULT_LOCALE)
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES",
                          os.path.join(project_root, "translations"))

    babel.init_app(app, locale_selector=select_locale)

    @app.context_processor
    def _inject_i18n():
        # get_locale يُمرَّر كدالة ليستدعيها القالب: {{ get_locale() }}.
        return {
            "get_locale": current_locale,
            "current_locale": current_locale(),
            "text_dir": text_dir(),
            "LANGUAGES": LANGUAGES,
            "SUPPORTED_LOCALES": SUPPORTED_LOCALES,
        }
