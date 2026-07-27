"""عينة معاينة المصمم الحيّة (طلب المالك 2026-07-27).

كانت المعاينة تعرض «—» لليوزر ونقاطًا للباس — فلا يرى المصمم الأحجام
والأطوال الحقيقية أثناء الضبط. الآن عينة واقعية ثابتة: يوزر
«012345678910» وباس «123456» ظاهر نصًّا. القيم وهمية بالكامل (لا
بطاقة حقيقية) — معاينات البطاقات الحقيقية (preview-fragment/المصغّرات)
تبقى مقنّعة كما هي.
"""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    import os

    db_file = os.path.join(tmp_path, "sample_creds.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app

    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo

    u = f"smp_{uuid4().hex[:10]}"
    admins_repo.create_admin(username=u, password="smp-pass",
                             full_name="Sample Tester", is_super_admin=True)
    res = client.post("/admin/radius/login",
                      data={"username": u, "password": "smp-pass"})
    assert res.status_code in {302, 303}


def test_designer_preview_shows_realistic_sample(client):
    _login(client)
    res = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={"background_style": "preset", "design_preset": "modern"},
    )
    assert res.status_code == 200
    svg = res.get_data(as_text=True)
    assert "012345678910" in svg          # اليوزر العينة
    assert "123456" in svg                # الباس ظاهر نصًّا
    assert "•" not in svg                 # لا نقاط إخفاء
    assert "—" not in svg or "012345678910" in svg


def test_typed_sample_wins(client):
    _login(client)
    res = client.post(
        "/admin/radius/print-templates/designer-svg",
        data={"background_style": "preset", "design_preset": "modern",
              "sample_username": "0599000111", "sample_password": "9876"},
    )
    assert res.status_code == 200
    svg = res.get_data(as_text=True)
    assert "0599000111" in svg
    assert "9876" in svg
