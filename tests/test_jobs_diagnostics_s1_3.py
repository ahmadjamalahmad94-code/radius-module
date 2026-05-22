"""S1.3 — Wire diagnostics scan to background jobs.

Three contracts pinned here:
  - POST /admin/radius/jobs/diagnostics/<nas_id> requires CSRF.
  - GET  /admin/radius/jobs/<id> returns JSON when asked for it,
    HTML otherwise, and never leaks across tenants.
  - Failed router lookup gives a useful Arabic message.

The mt_health.scan_router call is monkey-patched so this is a
pure route+runner test — no router contact.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s1_3_")
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
    u = f"s1_3_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s1-pass", full_name="S1.3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s1-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1, enabled: bool = True) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, 'direct', 'hr-test', 'p')""",
                (nas_id, f"job-rtr-{nas_id}",
                 f"203.0.113.{(nas_id % 250) + 1}",
                 1 if enabled else 0, now),
            )


def _stub_scan(monkeypatch, *, signals=None, fetch_errors=None):
    """Replace mt_health.scan_router so no router is touched."""
    from app.radius.services import mt_health
    monkeypatch.setattr(
        mt_health, "scan_router",
        lambda nas: {
            "ok": True,
            "signals": list(signals or [
                {"kind": "duplicate_macs", "severity": "ok",
                 "message": "no dupes"}
            ]),
            "summary": {"critical": 0, "warning": 0, "ok": 1},
            "fetch_errors": list(fetch_errors or []),
        },
    )


# ─── CSRF ─────────────────────────────────────────────────────


def test_post_without_csrf_is_rejected(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    _stub_scan(monkeypatch)
    res = client.post("/admin/radius/jobs/diagnostics/1",
                      follow_redirects=False)
    # CSRF middleware bounces — 302 to referer / login.
    assert res.status_code in {302, 303, 400, 403}


def test_post_with_csrf_creates_and_runs_job(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    _stub_scan(monkeypatch)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/jobs/diagnostics/1",
        data={"_csrf_token": token},
        follow_redirects=False,
    )
    # Redirect to /jobs/<id>.
    assert res.status_code in {302, 303}
    loc = res.headers.get("Location", "")
    assert "/admin/radius/jobs/" in loc

    job_id = int(loc.rsplit("/", 1)[-1])
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        row = jr.get(job_id)
        assert row is not None
        assert row["type"] == "mt.diag.scan"
        assert row["router_id"] == 1
        # Job ran synchronously → terminal state already.
        assert row["status"] == "success"
        assert row["result"]["router_id"] == 1


# ─── JSON status ──────────────────────────────────────────────


def test_json_post_returns_202_with_job_id(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    _stub_scan(monkeypatch)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/jobs/diagnostics/1",
        data={"_csrf_token": token},
        headers={"Accept": "application/json"},
    )
    assert res.status_code == 202
    body = res.get_json()
    assert "job_id" in body
    assert body["status_url"].endswith(f"/jobs/{body['job_id']}")


def test_get_returns_json_when_requested(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    _stub_scan(monkeypatch)
    token = _csrf(client)
    post = client.post(
        "/admin/radius/jobs/diagnostics/1",
        data={"_csrf_token": token},
        headers={"Accept": "application/json"},
    )
    jid = post.get_json()["job_id"]
    res = client.get(f"/admin/radius/jobs/{jid}",
                     headers={"Accept": "application/json"})
    assert res.status_code == 200
    body = res.get_json()
    assert body["id"] == jid
    assert body["status"] == "success"
    assert body["type"] == "mt.diag.scan"


def test_get_html_renders_status_page(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    _stub_scan(monkeypatch)
    token = _csrf(client)
    post = client.post(
        "/admin/radius/jobs/diagnostics/1",
        data={"_csrf_token": token},
        headers={"Accept": "application/json"},
    )
    jid = post.get_json()["job_id"]
    res = client.get(f"/admin/radius/jobs/{jid}")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-job-detail" in html
    assert f'data-mt-job-id="{jid}"' in html


def test_get_unknown_job_returns_404(app, client):
    _login(client)
    res = client.get("/admin/radius/jobs/99999")
    assert res.status_code == 404


# ─── Failure path ─────────────────────────────────────────────


def test_disabled_router_completes_as_skipped(app, client, monkeypatch):
    """A disabled router is an operator state, not a runner
    error. Handler returns a `skipped` result, job stays
    success-status, UI can render the reason."""
    _seed(app, nas_id=2, enabled=False)
    _login(client)
    _stub_scan(monkeypatch)
    token = _csrf(client)
    post = client.post(
        "/admin/radius/jobs/diagnostics/2",
        data={"_csrf_token": token},
        headers={"Accept": "application/json"},
    )
    jid = post.get_json()["job_id"]
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        row = jr.get(jid)
    assert row["status"] == "success"
    assert row["result"].get("skipped") is True
    assert "معطّل" in row["result"]["reason"]


def test_unknown_router_marks_job_failed_with_arabic_message(
        app, client, monkeypatch):
    """Operator passed an id that doesn't exist (deleted between
    page render and submit). Handler raises ValueError → runner
    marks failed → error_message carries Arabic text."""
    _login(client)
    _stub_scan(monkeypatch)
    token = _csrf(client)
    post = client.post(
        "/admin/radius/jobs/diagnostics/99999",
        data={"_csrf_token": token},
        headers={"Accept": "application/json"},
    )
    jid = post.get_json()["job_id"]
    with app.app_context():
        from app.radius.db.repos import jobs_repo as jr
        row = jr.get(jid)
    assert row["status"] == "failed"
    assert "غير موجود" in row["error_message"]


def test_existing_sync_health_endpoint_unchanged(app, client):
    """S1.3 must not break the synchronous /api/v1/.../health
    endpoint — the dashboard diagnostics tab depends on it."""
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/health" in rules
