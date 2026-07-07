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
