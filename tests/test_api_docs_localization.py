"""
يضمن أن صفحة /api/docs مُعرَّبة بالكامل — لا تترك أي مجموعة (أو عنوان نقطة)
بالإنجليزية.

الجذر الذي يحمي منه هذا الاختبار: عند إضافة blueprint جديد تحت api.v1.*،
يظهر مفتاح مجموعة جديد. إن لم يُضَف تعريبه إلى ``_GROUP_INFO`` كان يسقط
سابقًا للاسم الإنجليزي الافتراضي (Router Alerts، Share Groups…). الآن:
  1. كل مفاتيح المجموعات الفعلية من url_map يجب أن يكون لها مدخل في _GROUP_INFO.
  2. كل عنوان مجموعة مُصيَّر يجب أن يحوي حروفًا عربية (لا عنوان إنجليزي صامت).

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import re

import pytest

# نطاق الحروف العربية (يشمل الأشكال الأساسية والملحقة).
_ARABIC = re.compile(r"[؀-ۿ]")


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


def _live_group_keys(app) -> set[str]:
    """كل مفاتيح المجموعات التي تنتجها الصفحة فعلًا من url_map الحقيقي."""
    from app.api.openapi import _group_key

    keys: set[str] = set()
    with app.app_context():
        for rule in app.url_map.iter_rules():
            if not rule.endpoint.startswith("api.v1."):
                continue
            path = re.sub(
                r"<(int:|string:|float:|path:)?([^>]+)>", r"{\2}", rule.rule
            )
            methods = [m for m in rule.methods if m not in {"HEAD", "OPTIONS"}]
            if not methods:
                continue
            keys.add(_group_key(path))
    return keys


def test_every_live_group_has_arabic_entry(app):
    """لا مفتاح مجموعة فعلي بلا تعريب في _GROUP_INFO."""
    from app.api.openapi import _GROUP_INFO

    live = _live_group_keys(app)
    missing = sorted(k for k in live if k not in _GROUP_INFO)
    assert not missing, (
        "مجموعات بلا تعريب عربي في _GROUP_INFO — أضِفها: " + ", ".join(missing)
    )

    # وكل عنوان مُعرَّف فعلًا يحوي حروفًا عربية (لا إنجليزي صامت).
    not_arabic = sorted(
        k for k in live if not _ARABIC.search(_GROUP_INFO[k]["title"])
    )
    assert not not_arabic, (
        "عناوين مجموعات ليست عربية: " + ", ".join(not_arabic)
    )


def test_rendered_group_titles_are_all_arabic(app):
    """تصيير الصفحة الكامل: لا عنوان مجموعة إنجليزي متبقٍّ في الـ HTML."""
    app.testing = True
    body = app.test_client().get("/api/docs").get_data(as_text=True)

    titles = re.findall(r'apidoc-group-title">([^<]+)</div>', body)
    assert titles, "لم يُعثر على أي عنوان مجموعة في الصفحة"

    english_only = [t for t in titles if not _ARABIC.search(t)]
    assert not english_only, (
        "عناوين مجموعات إنجليزية ظهرت في الصفحة: " + ", ".join(english_only)
    )

    # ولا يجب أن تظهر علامة شبكة الأمان «مجموعة غير مُعرّبة» إطلاقًا.
    assert "مجموعة غير مُعرّبة" not in body, (
        "ظهرت شبكة أمان التعريب — مفتاح مجموعة بلا مدخل في _GROUP_INFO"
    )
