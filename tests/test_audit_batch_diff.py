"""تعديل دفعة الكروت يلتقط before/after مقروءتين فيَظهر «الحقل: كان X ← صار Y»
في سجل أحداث المدراء (package_name/price/status…) — امتداد سجل التغييرات للكروت.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db
    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "batch_diff.db")
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
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, name, 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def test_batch_update_records_field_diff(app):
    with app.app_context():
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        plan = _plan_id()
        batch, _cards = svc.generate_batch(
            actor="test", plan_id=plan, count=3,
            username_length=8, password_length=6, price_per_card=2.0,
            time_value=1, time_unit="days", package_name="دفعة أولى",
        )
        # تعديل اسم الدفعة + السعر + الحالة
        svc.update_batch(actor="owner", batch_id=batch.id, data={
            "package_name": "دفعة مُحدَّثة",
            "price_per_card": 3.5,
            "status": "revoked",
        })

        row = db().execute(
            "SELECT before_json, after_json FROM audit_log "
            "WHERE target_type='card_batch' AND action='update' "
            "ORDER BY id DESC LIMIT 1").fetchone()
        assert row and row["before_json"] and row["after_json"], row

        import json
        from app.radius.routes.reports import _change_items
        changes = _change_items(json.loads(row["before_json"]),
                                json.loads(row["after_json"]))
        by = {c["field"]: c for c in changes}
        assert "package_name" in by, changes
        assert by["package_name"]["old"] == "دفعة أولى"
        assert by["package_name"]["new"] == "دفعة مُحدَّثة"
        assert by["package_name"]["label"] == "اسم الدفعة"
        # الحالة معرَّبة (revoked → «ملغاة»)
        assert "status" in by and by["status"]["new"] == "ملغاة", changes
