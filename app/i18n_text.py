"""أدوات تغليف نصوص الواجهة في بايثون (i18n) — بلا أيّ اعتماد على بقيّة التطبيق.

القاعدة (انظر I18N.md):
    • ``_tr('نص')`` — ترجمة **فوريّة** بلغة الطلب الحالي. للرسائل التي تُبنى
      وقت الطلب: flash، أخطاء API، رسائل الاستثناءات، النصوص المركّبة
      (``_tr('تم حذف %(n)s مشترك', n=n)``). بالعربيّة (الافتراضيّ) أو خارج
      سياق الطلب تُعيد النصّ العربيّ نفسه حرفيًّا ⇒ لا يتغيّر أيّ سلوك عربيّ.
    • ``N_('نص')`` — **وَسم** بلا ترجمة فوريّة: يُعيد ``LStr`` (صنف فرعيّ من
      ``str`` مطابق تمامًا للنصّ) فيبقى كلّ منطق بايثون (مقارنة/مفاتيح/تخزين/
      JSON) كما هو. يُترجَم فقط **عند الإخراج**: في قوالب Jinja (``finalize``)
      وفي تسلسل JSON للّوحة (``translate_marked``). مناسب للثوابت على مستوى
      الوحدة (تسميات/قوائم) التي تُقيَّم مرّة واحدة وقت الاستيراد.

كلا الاسمين مفتاح استخراج لـ pybabel (``-k _tr -k N_``) فيدخل النصّ الكتالوج.
"""
from __future__ import annotations

__all__ = ["_tr", "_l", "N_", "LStr", "translate_marked", "translate_text"]


class LStr(str):
    """نصّ واجهة عربيّ موسوم للترجمة عند الإخراج. سلوكه كـ ``str`` تمامًا."""

    __slots__ = ()

    def __reduce__(self):  # pickle/deepcopy ⇒ يبقى موسومًا
        return (LStr, (str(self),))


def N_(s):  # noqa: N802 — اسم اصطلاحيّ لمفتاح الاستخراج
    """وَسم نصّ واجهة (بلا ترجمة فوريّة). انظر شرح الوحدة."""
    if type(s) is str:
        return LStr(s)
    return s


def _translations():
    try:
        from flask_babel import get_translations
        return get_translations()
    except Exception:  # noqa: BLE001 — خارج سياق التطبيق ⇒ بلا ترجمة
        return None


def _active() -> bool:
    """هل لغة الطلب الحالي غير العربيّة؟ (لا ترجمة إطلاقًا بالعربيّة).

    خارج سياق طلب HTTP (عامل خلفيّ، إقلاع، بذر) ⇒ لا ترجمة: يبقى العربيّ كما
    كان، ولا نستدعي get_locale (كي لا تُخزَّن لغة على ``g`` المشترك)."""
    try:
        from flask import has_request_context
        if not has_request_context():
            return False
        # الجلسة لم تُفتح بعد (مثلًا تسلسل كوكي الجلسة نفسه عبر app.json) ⇒ لا
        # نحسم اللغة الآن، وإلا خُزّنت «ar» على g قبل قراءة session['locale'].
        from flask.globals import request_ctx
        if getattr(request_ctx, "session", None) is None:
            return False
        from flask_babel import get_locale
        loc = get_locale()
        return loc is not None and str(loc).split("_")[0].lower() != "ar"
    except Exception:  # noqa: BLE001
        return False


def translate_text(s: str, t=None) -> str:
    """ترجمة نصّ (msgid عربيّ) بلغة الطلب — بلا تنسيق ``%``."""
    if t is None:
        t = _translations()
    if t is None:
        return str(s)
    try:
        return t.ugettext(str(s))
    except Exception:  # noqa: BLE001
        return str(s)


def _tr(__s, /, **kw):  # noqa: N807
    """ترجمة فوريّة + تنسيق ``%(name)s`` اختياريّ (مثل gettext في Flask-Babel).

    • بلا وسائط: لا تنسيق ``%`` إطلاقًا (فالـ ``%`` المفرد آمن).
    • بوسائط: ``%`` الحرفيّة في النصّ يجب أن تكون ``%%``.
    • أيّ ``LStr`` بين الوسائط يُترجَم أيضًا.
    • ترجمة تالفة (نائب ناقص) ⇒ سقوط آمن للنصّ العربيّ."""
    src = str(__s)
    t = _translations() if _active() else None
    out = translate_text(src, t) if t is not None else src
    if not kw:
        return out
    if t is not None:
        kw = {k: (translate_text(v, t) if type(v) is LStr else v) for k, v in kw.items()}
    try:
        return out % kw
    except (KeyError, ValueError, TypeError):
        try:
            return src % kw
        except (KeyError, ValueError, TypeError):
            return src


def _l(__s, /, **kw):  # noqa: N807
    """نسخة كسولة من ``_tr`` لثوابت الوحدة المركّبة (``'%(label)s — بلا حدّ'``):
    تُعيد ``LazyString`` يُترجَم ويُنسَّق لحظة تحويله إلى نصّ (عرض/JSON)."""
    from flask_babel.speaklater import LazyString
    return LazyString(_tr, __s, **kw)


def translate_marked(obj, _t=None):
    """يُعيد نسخة من ``obj`` تُستبدَل فيها كل ``LStr`` بترجمتها (للـ JSON).

    بالعربيّة (أو بلا أيّ LStr) يُعيد ``obj`` نفسه."""
    if _t is None:
        if not _has_lstr(obj) or not _active():
            return obj
        _t = _translations()
        if _t is None:
            return obj
    tp = type(obj)
    if tp is LStr:
        return translate_text(obj, _t)
    if tp is dict:
        # المفاتيح العاديّة لا تُترجَم أبدًا (معرّفات يقرؤها الكود)؛ فقط مفتاح LStr
        # (تسمية عرض وُسمت صراحةً بـ N_() مثل جداول «تسمية: قيمة»).
        return {(translate_text(k, _t) if type(k) is LStr else k): translate_marked(v, _t)
                for k, v in obj.items()}
    if tp is list:
        return [translate_marked(v, _t) for v in obj]
    if tp is tuple:
        return tuple(translate_marked(v, _t) for v in obj)
    return obj


def _has_lstr(obj, _depth: int = 0) -> bool:
    tp = type(obj)
    if tp is LStr:
        return True
    if _depth > 50:
        return False
    if tp is dict:
        return any(_has_lstr(v, _depth + 1) for v in obj.values())
    if tp is list or tp is tuple:
        return any(_has_lstr(v, _depth + 1) for v in obj)
    return False


def jinja_finalize(value):
    """``Environment.finalize``: يترجم ``LStr`` عند إخراجه في القالب."""
    if type(value) is LStr and _active():
        return translate_text(value)
    return value
