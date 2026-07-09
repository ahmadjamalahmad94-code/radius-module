"""المكوّن المشترك «التفاصيل/التغييرات» (_partials/audit_changes.html) يُظهر فرق
الحقول «الحقل: كان X ← صار Y» على الصفحات الثلاث التي تشترك في عمود «التفاصيل»:
  • radius/rep_manager_events.html   (أحداث المدراء)
  • radius/rep_user_events.html      (أحداث المستخدمين)
  • radius/rep_profile_changes.html  (تغييرات الباقات والملفات)

الالتقاط مشترك (reports._decorate_audit_rows يضبط row.changes)؛ هنا نتحقّق أنّ
الـrender على القوالب الثلاثة يعرض الفرق، مع تقنيع كلمة المرور وسقوطٍ آمن للنصّ
البديل للأحداث بلا لقطة.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_chgrender_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _rows():
    """صفّ تعديل (له changes) + صفّ بلا فرق (نصّ بديل فقط)."""
    return [
        {
            "id": 1, "created_at": "2026-07-07 10:00:00",
            "actor": "owner", "actor_label": "المالك",
            "action": "update", "action_label": "تعديل",
            "target_type": "user", "target_type_label": "مشترك",
            "target_id": "0566100443", "ip_address": "10.0.0.5",
            "detail_display": "مشترك: 0566100443",
            "payload_summary": "مشترك: 0566100443",
            "changes": [
                {"field": "mobile", "label": "الجوال",
                 "old": "000000", "new": "1111111"},
                {"field": "plan", "label": "العرض",
                 "old": "الادارة", "new": "2 ميجا"},
                {"field": "password", "label": "كلمة المرور",
                 "old": "••••", "new": "••••"},
            ],
        },
        {
            "id": 2, "created_at": "2026-07-07 09:00:00",
            "actor": "system", "actor_label": "النظام",
            "action": "create", "action_label": "إنشاء",
            "target_type": "user", "target_type_label": "مشترك",
            "target_id": "0599", "ip_address": "",
            "detail_display": "مشترك: 0599، باقة: الادارة",
            "payload_summary": "مشترك: 0599، باقة: الادارة",
            "changes": [],                       # لا فرق → نصّ بديل
        },
    ]


_TEMPLATES = {
    "radius/rep_manager_events.html": "radius.rep_manager_events",
    "radius/rep_user_events.html": "radius.rep_user_events",
    "radius/rep_profile_changes.html": "radius.rep_profile_changes",
}


@pytest.mark.parametrize("template", list(_TEMPLATES))
def test_shared_change_render_on_all_three_pages(app, template):
    with app.app_context(), app.test_request_context():
        from flask import render_template
        html = render_template(template, items=_rows(), total=2,
                               filters={"q": "", "date_from": "", "date_to": ""})
    # الأعمدة الثلاثة المنفصلة تظهر عبر المكوّن المشترك (change_cells)
    assert "chg-c-field" in html and "chg-c-old" in html and "chg-c-new" in html, \
        f"missing separate change columns in {template}"
    assert "1111111" in html and "2 ميجا" in html, template
    assert "الجوال" in html and "العرض" in html, template
    # تقنيع كلمة المرور: يظهر «••••» ولا تُسرَّب بصمة/قيمة خام
    assert "••••" in html and "pw:" not in html, template
    # سقوط آمن: الصفّ بلا فرق يعرض نصّه البديل (colspan=3)
    assert "باقة: الادارة" in html, f"fallback text missing in {template}"
