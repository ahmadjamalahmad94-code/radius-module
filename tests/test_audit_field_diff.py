"""سجل التغييرات يُظهر «الحقل: من X إلى Y» — الفرق الحقليّ لا مجرّد اسم الهدف.

كان تعديل المشترك يُسجَّل بلا تفاصيل (الفعل فقط). الآن update() يُخزّن لقطتين
before/after مقروءتين، و_build_manager_event_detail يعرض الفرق.
"""
from __future__ import annotations

import json


def test_diff_lines_render_field_changes():
    from app.radius.routes.reports import _diff_lines
    before = {"status": "نشط", "plan": "Bronze", "download_speed_kbps": 2000,
              "full_name": "أحمد"}
    after = {"status": "معطّل", "plan": "Gold", "download_speed_kbps": 4000,
             "full_name": "أحمد"}          # الاسم لم يتغيّر → لا يظهر
    lines = _diff_lines(before, after)
    joined = " | ".join(lines)
    assert "الحالة: من نشط إلى معطّل" in joined, joined
    assert "العرض: من Bronze إلى Gold" in joined, joined
    assert "سرعة التنزيل" in joined and "من 2000 إلى 4000" in joined, joined
    assert "الاسم" not in joined            # غير المتغيّر مُستبعَد


def test_subscriber_edit_detail_shows_diff():
    from app.radius.routes.reports import _build_manager_event_detail
    row = {
        "action": "update", "target_type": "user", "target_id": "0599",
        "before_json": json.dumps({"status": "نشط", "plan": "Bronze"}, ensure_ascii=False),
        "after_json": json.dumps({"status": "معطّل", "plan": "Gold"}, ensure_ascii=False),
        "payload_json": "{}",
    }
    detail = _build_manager_event_detail(row)
    assert "من نشط إلى معطّل" in detail, detail
    assert "من Bronze إلى Gold" in detail, detail


def test_generic_edit_detail_shows_diff():
    """أيّ تعديل يحمل before/after (مثل تعديل عرض) يُظهر الفرق أيضًا."""
    from app.radius.routes.reports import _build_manager_event_detail
    row = {
        "action": "update", "target_type": "plan", "target_id": "5",
        "before_json": json.dumps({"name": "باقة أ", "download_speed_kbps": 1000}, ensure_ascii=False),
        "after_json": json.dumps({"name": "باقة أ", "download_speed_kbps": 8000}, ensure_ascii=False),
        "payload_json": "{}",
    }
    detail = _build_manager_event_detail(row)
    assert "من 1000 إلى 8000" in detail, detail


def test_no_before_after_yields_no_false_diff():
    from app.radius.routes.reports import _diff_lines
    assert _diff_lines({}, {}) == []
    assert _diff_lines(None, {"a": 1}) == []


# ─── سجل التغييرات المنظَّم (كان X ← صار Y) + التقنيع ───────────────

def _parse_none(raw):
    return {} if raw is None else json.loads(raw)


def test_change_items_mobile_plan_two_field_diff():
    """تعديل الجوّال + الباقة → عنصران منظَّمان بقيَم old→new صحيحة، والاسم الثابت
    لا يظهر. هذا ما يُغذّي عمود «التغييرات» في القالب."""
    from app.radius.routes.reports import _change_items
    before = {"full_name": "أحمد", "mobile": "000000", "plan": "ميجا"}
    after = {"full_name": "أحمد", "mobile": "1111111", "plan": "2 ميجا"}
    items = _change_items(before, after)
    by_field = {c["field"]: c for c in items}
    assert set(by_field) == {"mobile", "plan"}, by_field   # الاسم الثابت مُستبعَد
    assert by_field["mobile"]["old"] == "000000"
    assert by_field["mobile"]["new"] == "1111111"
    assert by_field["mobile"]["label"] == "الجوال"
    assert by_field["plan"]["old"] == "ميجا"
    assert by_field["plan"]["new"] == "2 ميجا"
    assert by_field["plan"]["label"] == "العرض"


def test_change_items_masks_password():
    """تغيّر كلمة المرور يَظهر «تغيّرت» لكن مُقنَّعًا بـ«••••» — البصمة الخام
    لا تُسرَّب أبدًا للعرض."""
    from app.radius.routes.reports import _change_items
    before = {"password": "pw:aaaaaaaaaaaa", "mobile": "000000"}
    after = {"password": "pw:bbbbbbbbbbbbb", "mobile": "000000"}
    items = _change_items(before, after)
    pw = [c for c in items if c["field"] == "password"]
    assert len(pw) == 1, items                         # تغيّر مكتشَف
    assert pw[0]["old"] == "••••" and pw[0]["new"] == "••••"
    assert "pw:" not in pw[0]["old"] + pw[0]["new"]    # لا تسريب للبصمة
    assert pw[0]["label"] == "كلمة المرور"


def test_old_event_without_diff_falls_back_to_detail():
    """حدث قديم بلا before/after → لا تغييرات منظَّمة (changes فارغة) وتَبقى
    «التفاصيل» تُعرَض — لا انهيار، توافق خلفيّ آمن."""
    from app.radius.routes.reports import _change_items, _build_manager_event_detail
    row = {
        "action": "auth_login", "target_type": "admin", "target_id": "5",
        "ip_address": "10.0.0.9", "payload_json": "{}",
        "before_json": None, "after_json": None,
    }
    assert _change_items(_parse_none(row["before_json"]),
                         _parse_none(row["after_json"])) == []
    # النصّ البديل ما زال يُعرَض (لا يكسر)
    assert _build_manager_event_detail(row) == "دخل من IP: 10.0.0.9"


def test_subscriber_update_records_mobile_plan_diff():
    """تكامل: تعديل جوّال + باقة مشترك يلتقط before/after فيَظهر «كان X ← صار Y»
    لكلٍّ من الحقلين بالسجلّ، وكلمة المرور تُخزَّن كبصمة مُقنَّعة (لا خام)."""
    import os
    import sys
    import tempfile

    tmp = tempfile.mkdtemp(prefix="hr_subdiff_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    try:
        from app import create_app
        app = create_app()
        with app.app_context():
            from app.radius.core.types import AccessPlan, Subscriber
            from app.radius.services.plans import get_plans_service
            from app.radius.services.users import get_users_service

            plans = get_plans_service()
            p1 = plans.create(actor="t", plan=AccessPlan(
                id=None, tenant_id=1, name="ميجا",
                speed_down_kbps=1000, speed_up_kbps=500))
            p2 = plans.create(actor="t", plan=AccessPlan(
                id=None, tenant_id=1, name="2 ميجا",
                speed_down_kbps=2000, speed_up_kbps=1000))

            users = get_users_service()
            users.create(actor="t", sub=Subscriber(
                id=None, username="sub-diff", password="secret1",
                tenant_id=1, plan_id=p1.id, mobile="000000"))
            # تعديل: جوّال + باقة (نُبقي كلمة المرور فارغة لتُحفَظ القديمة)
            users.update(actor="t", sub=Subscriber(
                id=None, username="sub-diff", password="",
                tenant_id=1, plan_id=p2.id, mobile="1111111"))

            from app.radius.db.connection import db
            row = db().execute(
                "SELECT before_json, after_json FROM audit_log "
                "WHERE target_type='user' AND action='update' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            assert row and row["before_json"] and row["after_json"]

            from app.radius.routes.reports import _change_items
            changes = _change_items(json.loads(row["before_json"]),
                                    json.loads(row["after_json"]))
            by = {c["field"]: c for c in changes}
            assert "mobile" in by and by["mobile"]["old"] == "000000" \
                and by["mobile"]["new"] == "1111111", changes
            assert "plan" in by and by["plan"]["old"] == "ميجا" \
                and by["plan"]["new"] == "2 ميجا", changes
            # كلمة المرور مُحفوظة (لم تتغيّر) فلا تظهر، والقيمة الخام غير مخزَّنة
            assert "secret1" not in row["before_json"]
            assert "password" not in by                # لم تتغيّر → لا تظهر
    finally:
        for k in list(sys.modules):
            if k.startswith("app."):
                del sys.modules[k]


def test_plan_update_records_before_after():
    """تعديل عرض/باقة يلتقط لقطتَي before/after فيَظهر «من X إلى Y» بالسجلّ."""
    import os
    import sys
    import tempfile

    tmp = tempfile.mkdtemp(prefix="hr_plandiff_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    try:
        from app import create_app
        app = create_app()
        with app.app_context():
            from app.radius.core.types import AccessPlan
            from app.radius.services.plans import get_plans_service

            import dataclasses
            svc = get_plans_service()
            created = svc.create(actor="t", plan=AccessPlan(
                id=None, tenant_id=1, name="P-diff",
                speed_down_kbps=2000, speed_up_kbps=1000))
            svc.update(actor="t",
                       plan=dataclasses.replace(created, speed_down_kbps=8000))

            from app.radius.db.connection import db
            row = db().execute(
                "SELECT before_json, after_json FROM audit_log "
                "WHERE target_type='plan' AND action='update' "
                "ORDER BY id DESC LIMIT 1").fetchone()
            assert row and row["before_json"] and row["after_json"]
            assert '"speed_down_kbps": 2000' in row["before_json"]
            assert '"speed_down_kbps": 8000' in row["after_json"]
    finally:
        for k in list(sys.modules):
            if k.startswith("app."):
                del sys.modules[k]
