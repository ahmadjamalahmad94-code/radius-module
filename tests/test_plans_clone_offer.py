"""نسخ العرض (duplicate/clone) — العرض يُستنسخ بكل مواصفاته إلى عرض جديد
مستقلّ باسم «— نسخة»، والمسار يُعيد التوجيه لصفحة تعديل النسخة، وتعديل
النسخة لا يمسّ الأصل.

يثبت أنّ النسخ **عامّ حقل-بحقل**: أيّ عمود في العرض (سرعة/CIR/burst،
كوتا يومية/شهرية، صلاحية/مدّة، جلسات، شبكة/خدمات/قيود، سعر، metadata…)
يُنسخ دون سرد يدويّ — عدا الهويّة (id) والطوابع الزمنية.

يعمل في CI (لا بيانات عميل). شغّل هذا الملف وحده."""
from __future__ import annotations

import os
from dataclasses import asdict, fields

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "plans_clone.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    return flask_app


def _rich_plan():
    """عرض بمواصفات مميّزة عبر كل المجموعات — كي يفشل الاختبار لو أُسقط أيّ حقل."""
    from app.radius.core.types import AccessPlan
    return AccessPlan(
        id=None, tenant_id=1, name="4 ميجا", code="MEGA4",
        plan_type="hybrid", service_type="Both", service_scope="both",
        duration_minutes=8 * 60, validity_days=30,
        max_daily_minutes=120, max_monthly_minutes=3000,
        quota_total_mb=51200, quota_daily_mb=2048, quota_monthly_mb=40960,
        speed_down_kbps=4096, speed_up_kbps=2048,
        burst_enabled=True, burst_down_kbps=6144, burst_up_kbps=3072,
        burst_threshold_kbps=3000, burst_time_sec=8,
        speed_control_enabled=True, cir_down_kbps=2048, cir_up_kbps=1024,
        daily_download_quota_mb=1024, daily_upload_quota_mb=512,
        monthly_combined_quota_mb=40960,
        concurrent_sessions=3, session_timeout_sec=7200, idle_timeout_sec=600,
        address_pool="pool-a", framed_pool="framed-b", vlan_id=42,
        bind_mac=True, bind_ip=True,
        hotspot_enabled=True, ppp_enabled=True,
        loan_enabled=True, max_loan_minutes=60,
        allowed_days=("mon", "tue", "wed"),
        allowed_hours_from="08:00", allowed_hours_to="23:00",
        price=9.5, currency="JOD", priority=17, color="#FF8800",
        description="عرض اختبار غنيّ", enabled=True,
        metadata='{"subscription": {"send_alerts": "1"}}',
    )


# الحقول التي يجب أن تختلف بين الأصل والنسخة (هويّة/طوابع/اسم).
_IDENTITY = {"id", "name", "created_at", "updated_at"}


def test_clone_copies_all_specs_new_id_and_suffix(app):
    """النسخ عبر الخدمة: id جديد + اسم «— نسخة» + كل حقل آخر مطابق تمامًا."""
    with app.app_context():
        from app.radius.services.plans import get_plans_service, CLONE_NAME_SUFFIX
        svc = get_plans_service()
        src = svc.create(actor="root", plan=_rich_plan())

        dup = svc.clone(actor="root", plan_id=src.id)

        # هويّة جديدة واسم مُلاحَق.
        assert dup.id is not None and dup.id != src.id
        assert dup.name == "4 ميجا" + CLONE_NAME_SUFFIX

        # كل حقل غير-هويّة متطابق — إثبات النسخ العامّ حقل-بحقل.
        s, d = asdict(src), asdict(dup)
        for f in fields(src):
            if f.name in _IDENTITY:
                continue
            assert d[f.name] == s[f.name], f"clone dropped/changed field {f.name!r}"

        # النسخة تُولد غير مؤرشفة ولها طابع إنشاء جديد.
        assert dup.deleted_at is None and dup.deleted_by == ""
        assert dup.created_at is not None


def test_clone_dedups_name_on_repeat(app):
    """نسخ العرض مرّتين لا يكسر قيد التفرّد (tenant_id, name)."""
    with app.app_context():
        from app.radius.services.plans import get_plans_service, CLONE_NAME_SUFFIX
        svc = get_plans_service()
        src = svc.create(actor="root", plan=_rich_plan())
        d1 = svc.clone(actor="root", plan_id=src.id)
        d2 = svc.clone(actor="root", plan_id=src.id)
        assert d1.name == "4 ميجا" + CLONE_NAME_SUFFIX
        assert d2.name == "4 ميجا" + CLONE_NAME_SUFFIX + " 2"
        assert len({src.id, d1.id, d2.id}) == 3


def test_editing_copy_does_not_affect_original(app):
    """تعديل النسخة (سعر/سرعة) لا يغيّر العرض الأصليّ إطلاقًا."""
    with app.app_context():
        from dataclasses import replace
        from app.radius.services.plans import get_plans_service
        svc = get_plans_service()
        src = svc.create(actor="root", plan=_rich_plan())
        dup = svc.clone(actor="root", plan_id=src.id)

        svc.update(actor="root", plan=replace(
            dup, price=99.0, speed_down_kbps=99999, description="نسخة مُعدَّلة"))

        original = svc.get(src.id)
        assert original.price == 9.5
        assert original.speed_down_kbps == 4096
        assert original.description == "عرض اختبار غنيّ"


def _login_super(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "root"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


def test_clone_route_redirects_to_new_edit_page(app):
    """POST /plans/<id>/clone يُنشئ نسخة ويُعيد التوجيه لصفحة تعديلها."""
    with app.app_context():
        from app.radius.services.plans import get_plans_service
        from flask import url_for
        src = get_plans_service().create(actor="root", plan=_rich_plan())
        with app.test_request_context():
            clone_url = url_for("radius.plans_clone", plan_id=src.id)

    with app.test_client() as client:
        _login_super(client)
        res = client.post(clone_url, data={"_csrf_token": "off-csrf"},
                          follow_redirects=False)

    assert res.status_code == 302, res.status_code
    with app.app_context():
        # النسخة الجديدة موجودة، والتوجيه إلى صفحة تعديلها بالذات.
        row = db().execute(
            "SELECT id, name FROM access_plans WHERE tenant_id=1 AND id<>? "
            "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1", (src.id,)).fetchone()
        assert row is not None and row["name"] == "4 ميجا - نسخة"
        new_id = int(row["id"])
        with app.test_request_context():
            from flask import url_for
            assert res.headers["Location"].endswith(
                url_for("radius.plans_edit", plan_id=new_id))

        # سُجِّل كحدث إنشاء عرض عاديّ (مع علامة النسخ).
        audit = db().execute(
            "SELECT action, payload_json FROM audit_log "
            "WHERE target_type='plan' AND target_id=? ORDER BY id DESC LIMIT 1",
            (str(new_id),)).fetchone()
        assert audit is not None and audit["action"] == "create"
        assert "clone" in (audit["payload_json"] or "")
