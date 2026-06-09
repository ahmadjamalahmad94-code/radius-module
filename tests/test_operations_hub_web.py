"""E3 — Operations Center hub consolidation.

UI-only: the operations landing (/operations) and speed-control
(/operations/speed-control) now share a single in-section pill nav for a
consistent hub feel, matching the comms/events hubs. No operations logic
or dry-run safety is touched.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "ops_hub.db")
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

        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "ops_admin"
        sess["admin_name"] = "Ops Admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "ops-csrf"


def test_operations_landing_renders_with_nav(app):
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'data-testid="operations-nav"' in html
    assert "العمليات" in html


def test_speed_control_left_operations_nav_and_links_back_to_bandwidth(app):
    """Speed-control now belongs to the «التحكم بالسرعة» sidebar group:
    no operations pill nav on the page, and a back-link to the bandwidth
    profiles list instead."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/operations/speed-control")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'data-testid="operations-nav"' not in html
    assert 'data-testid="speed-control-back-bandwidth"' in html
    assert "/admin/radius/bandwidth" in html


def test_speed_control_post_endpoint_stays(app):
    rules = {(r.rule, tuple(sorted(r.methods))) for r in app.url_map.iter_rules()}
    has_post = any(
        rule == "/admin/radius/operations/speed-control" and "POST" in methods
        for rule, methods in rules
    )
    assert has_post


def test_speed_control_split_scheduled_page_has_no_manual_engine(app):
    """صفحة «التحكم المجدول» تحتفظ بنموذج الأوضاع فقط — محرّك السلايدر
    اليدوي انتقل إلى صفحته المستقلة، مع تبويب يربط الصفحتين."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/operations/speed-control")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # the scheduled form stays
    assert 'data-testid="speed-control-form"' in html
    # the manual slider engine is gone from this page
    assert 'data-testid="speed-manual-section"' not in html
    assert "ops_speed_control.js" not in html
    # sibling tabs link to the manual page
    assert 'data-testid="speed-control-tabs"' in html
    assert "/admin/radius/operations/speed-control/manual" in html


def test_speed_control_manual_page_renders_engine_and_links_back(app):
    """صفحة «التحكم اليدوي» المستقلة: محرّك السلايدر/الحلقة + النموذج المخفي
    بنفس عقد الخادم، وتبويب يعود إلى صفحة «التحكم المجدول»."""
    with app.test_client() as client:
        _auth(client)
        res = client.get("/admin/radius/operations/speed-control/manual")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'data-testid="speed-manual-section"' in html
    assert 'data-testid="speed-manual-form"' in html
    assert "ops_speed_control.js" in html
    # no scheduled mode-cards form here
    assert 'data-testid="speed-control-form"' not in html
    # sibling tabs link back to the scheduled page
    assert 'data-testid="speed-control-tabs"' in html
    assert "/admin/radius/operations/speed-control" in html


def test_speed_control_manual_post_endpoint_accepts_save(app):
    """مسار اليدوي يقبل POST بنفس العقد ويحفظ سياسة معاينة بدون تنفيذ."""
    rules = {(r.rule, tuple(sorted(r.methods))) for r in app.url_map.iter_rules()}
    assert any(
        rule == "/admin/radius/operations/speed-control/manual" and "POST" in methods
        for rule, methods in rules
    )
    with app.test_client() as client:
        _auth(client)
        saved = client.post(
            "/admin/radius/operations/speed-control/manual",
            data={
                "_csrf_token": "ops-csrf",
                "policy_key": "manual-smoke",
                "title": "Manual smoke",
                "preset": "normal",
                "multiplier": "0.5",
                "profile_ids": "",
                "save_policy": "1",
            },
            follow_redirects=False,
        )
    # save redirects back to the manual page itself
    assert saved.status_code in {302, 303}
    assert "/admin/radius/operations/speed-control/manual" in saved.headers["Location"]
