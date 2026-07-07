"""الالتقاط الحقيقيّ لـbefore/after في مواقع التعديل الفعليّة — كي يمتلئ
`row.changes` للحالات التي يهتمّ بها المالك ويَعرضها المكوّن المشترك «كان X ← صار Y»:

  1) تعديل العرض (card_offers) — كان بلا أيّ سجلّ تدقيق إطلاقًا.
  2) إضافة/تمديد وقت المشترك — «تاريخ الانتهاء: كان X ← صار Y» في تغييرات الباقات.
  3) أيّام الاتصال في لقطة المشترك.
"""
from __future__ import annotations

import json
import os

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "audit_diff.db")
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


def _plan_id(name="ميجا") -> int:
    cur = db().execute(
        """INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
             price, currency, created_at, updated_at)
           VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
        (1, name, 8 * 60, 1, 5.0, "JOD"),
    )
    return int(cur.lastrowid)


def _login_super(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "root"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"


def test_offer_edit_records_field_diff(app):
    """تعديل سعر العرض عبر المسار الحقيقيّ يُسجِّل before/after فيَظهر
    «سعر البيع: كان 5.00 ← صار 9.50»."""
    with app.app_context():
        from app.radius.services.card_offers import CardOffersService
        pid = _plan_id()
        offer = CardOffersService(tenant_id=1).create_offer(
            name="بطاقة 8 ساعات", duration_minutes=8 * 60,
            wholesale="2.00", selling="5.00", plan_id=pid, created_by="super")
        oid = offer["id"]
        from flask import url_for
        with app.test_request_context():
            edit_url = url_for("radius.cards_offer_edit", offer_id=oid)

    with app.test_client() as client:
        _login_super(client)
        res = client.post(edit_url, data={
            "_csrf_token": "off-csrf",
            "name": "بطاقة 8 ساعات",
            "duration_minutes": str(8 * 60),
            "wholesale": "2.00",
            "selling": "9.50",              # ← السعر تغيّر
            "plan_id": str(pid),
            "currency": "JOD",
            "device_count": "1",
        }, follow_redirects=False)
    assert res.status_code in (302, 200), res.status_code

    with app.app_context():
        row = db().execute(
            "SELECT before_json, after_json FROM audit_log "
            "WHERE target_type='offer' AND action='update' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row and row["before_json"] and row["after_json"], row
        from app.radius.routes.reports import _change_items
        changes = _change_items(json.loads(row["before_json"]),
                                json.loads(row["after_json"]))
        by = {c["field"]: c for c in changes}
        assert "selling" in by, changes
        assert by["selling"]["old"] == "5.00" and by["selling"]["new"] == "9.50"
        assert by["selling"]["label"] == "سعر البيع"


def test_extend_time_records_expiry_diff(app):
    """إضافة وقت للمشترك تُسجِّل «تاريخ الانتهاء» before/after فيَظهر كان→صار
    في صفحة تغييرات الباقات."""
    with app.app_context():
        from datetime import datetime
        from app.radius.core.types import AccessPlan, Subscriber
        from app.radius.services.plans import get_plans_service
        from app.radius.services.users import get_users_service
        p = get_plans_service().create(actor="t", plan=AccessPlan(
            id=None, tenant_id=1, name="ميجا", speed_down_kbps=1000, speed_up_kbps=500))
        us = get_users_service()
        us.create(actor="t", sub=Subscriber(
            id=None, username="ext1", password="x", tenant_id=1,
            plan_id=p.id, expire_at=datetime(2026, 1, 1, 0, 0, 0)))
        us.extend_time(actor="admin", username="ext1", minutes=1440)

        row = db().execute(
            "SELECT before_json, after_json FROM audit_log "
            "WHERE action='extend_time' ORDER BY id DESC LIMIT 1").fetchone()
        assert row and row["before_json"] and row["after_json"], row
        from app.radius.routes.reports import _change_items
        changes = _change_items(json.loads(row["before_json"]),
                                json.loads(row["after_json"]))
        by = {c["field"]: c for c in changes}
        assert "expiry" in by, changes
        assert by["expiry"]["label"] == "تاريخ الانتهاء"
        assert by["expiry"]["old"] != by["expiry"]["new"]


def test_connection_days_snapshot_renders(app):
    """أيّام الاتصال في لقطة المشترك تُترجَم للعربية وتَظهر كفرق عند تغيّرها."""
    with app.app_context():
        from app.radius.services.users import _days_ar
        from app.radius.routes.reports import _change_items
        assert _days_ar("") == ""                       # بلا جدول → لا ضجيج
        assert "السبت" in _days_ar("sat,sun")
        before = {"connection_days": _days_ar("sat,sun")}
        after = {"connection_days": _days_ar("sat,sun,mon")}
        by = {c["field"]: c for c in _change_items(before, after)}
        assert "connection_days" in by
        assert by["connection_days"]["label"] == "أيّام الاتصال"
