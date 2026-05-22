"""O4 — Human-readable audit presenter + per-router timeline."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


# ─── Presenter (pure) ────────────────────────────────────────


def test_present_known_programming_apply_action():
    from app.radius.services.mt_audit_presenter import present
    e = present({
        "id": 1,
        "action": "mt.programming.hotspot.apply",
        "actor": "alice", "severity": "info",
        "result_status": "success",
        "router_id": 42,
        "created_at": "2026-05-22T18:00:00Z",
        "payload_json": "{}",
    })
    assert "alice" in e.headline_ar
    assert "Hotspot" in e.headline_ar
    assert e.recovery_hint_ar == ""  # success → no recovery


def test_present_partial_apply_includes_recovery_hint():
    from app.radius.services.mt_audit_presenter import present
    e = present({
        "id": 2,
        "action": "mt.programming.hotspot.apply",
        "actor": "alice", "severity": "warning",
        "result_status": "partial",
        "payload_json": "{}",
    })
    assert e.recovery_hint_ar != ""
    assert "Unprogram" in e.recovery_hint_ar


def test_present_failed_backup_recovery_hint():
    from app.radius.services.mt_audit_presenter import present
    e = present({
        "id": 3,
        "action": "mt.backup.save",
        "actor": "bob", "severity": "critical",
        "result_status": "failed",
        "payload_json": "{}",
    })
    assert "/file" in e.recovery_hint_ar


def test_present_unknown_action_falls_back_safely():
    from app.radius.services.mt_audit_presenter import present
    e = present({
        "id": 4,
        "action": "mt.something.totally.new",
        "actor": "alice", "severity": "info",
        "result_status": "",
        "payload_json": "{}",
    })
    # Doesn't crash; raw code appears.
    assert "mt.something.totally.new" in e.headline_ar
    assert e.recovery_hint_ar == ""


def test_present_extracts_related_job_id_from_payload():
    from app.radius.services.mt_audit_presenter import present
    e = present({
        "id": 5, "action": "mt.x", "actor": "a",
        "severity": "info", "result_status": "",
        "payload_json": '{"job_id": 99, "k": "v"}',
    })
    assert e.related_job_id == 99


def test_present_handles_malformed_payload_json():
    """Repo redaction may leave a row with broken JSON if a
    future migration changes shape — presenter should not
    raise."""
    from app.radius.services.mt_audit_presenter import present
    e = present({
        "id": 6, "action": "mt.x", "actor": "a",
        "severity": "info", "result_status": "",
        "payload_json": "not-json",
    })
    assert e.related_job_id is None


def test_risk_label_critical_warning_info():
    from app.radius.services.mt_audit_presenter import present
    crit = present({"id": 7, "action": "mt.x", "actor": "a",
                    "severity": "critical", "result_status": "",
                    "payload_json": "{}"})
    warn = present({"id": 8, "action": "mt.x", "actor": "a",
                    "severity": "warning", "result_status": "",
                    "payload_json": "{}"})
    info = present({"id": 9, "action": "mt.x", "actor": "a",
                    "severity": "info", "result_status": "",
                    "payload_json": "{}"})
    assert crit.risk_label_ar == "حرج"
    assert warn.risk_label_ar == "تحذير"
    assert info.risk_label_ar == ""


# ─── Route ───────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o4_")
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
    u = f"o4_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o4-pass", full_name="O4",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o4-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id=1):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, f"o4-rtr-{nas_id}",
                 f"203.0.113.{nas_id}", now),
            )


def test_timeline_route_login_guarded(client):
    res = client.get("/admin/radius/mt/1/timeline",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_timeline_route_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/9999/timeline")
    assert res.status_code == 404


def test_timeline_route_renders_empty_state(app, client):
    _seed_nas(app, nas_id=20)
    _login(client)
    html = client.get("/admin/radius/mt/20/timeline").get_data(as_text=True)
    assert "data-mt-audit-timeline" in html
    assert "data-mt-timeline-empty" in html


def test_timeline_renders_known_action_as_arabic(app, client):
    _seed_nas(app, nas_id=21)
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(
            tenant_id=1, actor="alice",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="21",
            router_id=21, severity="info",
            result_status="success",
        )
    _login(client)
    html = client.get("/admin/radius/mt/21/timeline").get_data(as_text=True)
    assert "alice" in html
    assert "طبّق برمجة Hotspot" in html
    assert 'data-mt-timeline-action="mt.programming.hotspot.apply"' in html


def test_timeline_renders_recovery_hint_for_partial_apply(app, client):
    _seed_nas(app, nas_id=22)
    with app.app_context():
        from app.radius.db.repos import audit_repo
        audit_repo.record(
            tenant_id=1, actor="bob",
            action="mt.programming.hotspot.apply",
            target_type="mikrotik_nas", target_id="22",
            router_id=22, severity="warning",
            result_status="partial",
        )
    _login(client)
    html = client.get("/admin/radius/mt/22/timeline").get_data(as_text=True)
    assert "data-mt-timeline-recovery" in html
    assert "Unprogram" in html


def test_timeline_links_to_raw_audit_detail(app, client):
    _seed_nas(app, nas_id=23)
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="op", action="mt.backup.save",
            target_type="mikrotik_nas", target_id="23",
            router_id=23, severity="info",
            result_status="success",
        )
    _login(client)
    html = client.get("/admin/radius/mt/23/timeline").get_data(as_text=True)
    assert f"/admin/radius/audit/{aid}" in html
    assert f'data-mt-timeline-raw="{aid}"' in html
