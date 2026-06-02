"""S2.2 — Audit log center UI.

Pins:
  - /admin/radius/audit renders (login-guarded, tenant-scoped)
  - Filters (router_id, action, severity, result_status, q) work
  - Detail page renders for a real id, 404s for missing/foreign
  - Secrets stay redacted in the rendered HTML (no plaintext leak)
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s2_2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"s2_2_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s2-pass", full_name="S2.2 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s2-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _record(*, action="mt.x", router_id=None, severity="info",
            result_status="", payload=None, target_id="1"):
    from app.radius.db.repos import audit_repo
    return audit_repo.record(
        tenant_id=1, actor="op", action=action,
        target_type="mikrotik_nas", target_id=str(target_id),
        router_id=router_id, severity=severity,
        result_status=result_status,
        payload=payload or {},
    )


# ─── Index ────────────────────────────────────────────────────


def test_audit_index_is_login_guarded(client):
    res = client.get("/admin/radius/audit", follow_redirects=False)
    assert res.status_code in {302, 303}


def test_audit_index_renders_shell(app, client):
    _login(client)
    res = client.get("/admin/radius/audit")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-audit-log-page" in html
    assert "data-audit-log-filters" in html


def test_audit_index_lists_rows(app, client):
    with app.app_context():
        _record(action="mt.programming.hotspot.apply",
                router_id=42, severity="warning",
                result_status="success")
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    # The row now shows the Arabic action label (the raw English code was
    # removed from the list for clarity; it remains on the detail page).
    assert "تطبيق إعدادات Hotspot" in html
    assert "data-audit-log-rows" in html


def test_audit_index_renders_empty_state_when_no_rows(app, client):
    _login(client)
    html = client.get("/admin/radius/audit?q=__no_such_audit_row__").get_data(as_text=True)
    assert "data-audit-empty" in html


# ─── Filters ──────────────────────────────────────────────────


def test_filter_by_router_id(app, client):
    with app.app_context():
        _record(action="apply", router_id=10)
        _record(action="apply", router_id=20)
    _login(client)
    html = client.get(
        "/admin/radius/audit?router_id=10").get_data(as_text=True)
    # Both rows have action="apply"; the filter should show 1 row.
    assert html.count('data-audit-row="') == 1


def test_filter_by_severity(app, client):
    with app.app_context():
        _record(severity="critical", action="boom")
        _record(severity="info", action="benign")
    _login(client)
    html = client.get(
        "/admin/radius/audit?severity=critical").get_data(as_text=True)
    # Raw action codes are no longer printed in the list, so assert the filter
    # by row count + the critical badge: only the critical row survives.
    assert html.count('data-audit-row="') == 1
    assert "حرجة" in html


def test_filter_by_action_and_search(app, client):
    with app.app_context():
        _record(action="mt.deploy", target_id="alpha-zone")
        _record(action="mt.apply",  target_id="beta-zone")
    _login(client)
    html = client.get(
        "/admin/radius/audit?q=alpha").get_data(as_text=True)
    assert "alpha-zone" in html
    assert "beta-zone" not in html


# ─── Detail ───────────────────────────────────────────────────


def test_detail_renders_full_picture(app, client):
    with app.app_context():
        aid = _record(action="mt.detail",
                       payload={"k": "v",
                                "api_password": "pwd-LEAK"})
    _login(client)
    html = client.get(
        f"/admin/radius/audit/{aid}").get_data(as_text=True)
    assert "data-audit-detail-page" in html
    assert f'data-audit-id="{aid}"' in html
    assert "mt.detail" in html


def test_detail_404_for_unknown_id(app, client):
    _login(client)
    res = client.get("/admin/radius/audit/999999")
    assert res.status_code == 404


def test_detail_redacts_secrets_in_rendered_html(app, client):
    """The repo redacts at write-time, so the secret should
    already be '***' in the row. This test catches a regression
    where someone bypasses the redact path."""
    with app.app_context():
        aid = _record(action="mt.x",
                       payload={"api_password": "PWD-MUST-NOT-APPEAR",
                                "username": "op"})
    _login(client)
    html = client.get(
        f"/admin/radius/audit/{aid}").get_data(as_text=True)
    assert "PWD-MUST-NOT-APPEAR" not in html
    assert "***" in html
    # And the non-secret key shows through.
    assert "op" in html


def test_detail_shows_before_and_after_blocks_when_present(app, client):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="op",
            action="mt.toggle", target_type="mikrotik_nas",
            target_id="42", router_id=42,
            before={"enabled": True},
            after={"enabled": False},
            severity="warning",
            result_status="success",
        )
    _login(client)
    html = client.get(
        f"/admin/radius/audit/{aid}").get_data(as_text=True)
    assert "data-audit-detail-before" in html
    assert "data-audit-detail-after" in html
    assert "قبل التغيير" in html
    assert "بعد التغيير" in html
