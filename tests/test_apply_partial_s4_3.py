"""S4.3 — Safe partial-apply reporting.

Pins:
  - ApplyResult.was_partial() correctly distinguishes "nothing
    applied / first command failed" from "some applied then
    failed".
  - result_status() emits 'success' / 'partial' / 'failed'.
  - recovery_hint_ar() returns the right Arabic guidance per
    outcome.
  - The /program/apply route writes result_status='partial' to
    audit when only some commands landed.
  - The route response shows the recovery banner in the partial
    case.
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
    tmp = tempfile.mkdtemp(prefix="hr_s4_3_")
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
    u = f"s4_3_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s4-pass", full_name="S4.3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s4-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    """Mint a CSRF token from an authenticated GET."""
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token") or ""


def _seed_nas(app, nas_id=42):
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
                           1, ?, 'direct', 'hr', 'p')""",
                (nas_id, f"s43-rtr-{nas_id}",
                 f"203.0.113.{(nas_id % 250) + 1}", now),
            )


# ─── Service-layer ────────────────────────────────────────────


def test_was_partial_true_when_some_applied_then_failed(app):
    from app.radius.services.mt_programming import (
        ApplyResult, StepResult,
    )
    r = ApplyResult(
        ok=False,
        steps=[
            StepResult(path="/ip/pool/add", attrs={}, ok=True),
            StepResult(path="/ip/address/add", attrs={},
                        ok=False, error="bad"),
        ],
        error="bad",
    )
    assert r.was_partial() is True
    assert r.result_status() == "partial"


def test_was_partial_false_when_first_command_failed(app):
    from app.radius.services.mt_programming import (
        ApplyResult, StepResult,
    )
    r = ApplyResult(
        ok=False,
        steps=[StepResult(path="/ip/pool/add", attrs={},
                          ok=False, error="bad")],
        error="bad",
    )
    assert r.was_partial() is False
    assert r.result_status() == "failed"


def test_was_partial_false_when_only_skips_then_failed(app):
    """Skipped (already-exists) steps don't count as "applied"
    for partial purposes — they're idempotent no-ops, the router
    state didn't change."""
    from app.radius.services.mt_programming import (
        ApplyResult, StepResult,
    )
    r = ApplyResult(
        ok=False,
        steps=[
            StepResult(path="/ip/pool/add", attrs={},
                        ok=True, skipped="already_exists"),
            StepResult(path="/ip/address/add", attrs={},
                        ok=False, error="bad"),
        ],
        error="bad",
    )
    assert r.was_partial() is False
    assert r.result_status() == "failed"


def test_result_status_success_on_ok(app):
    from app.radius.services.mt_programming import (
        ApplyResult, StepResult,
    )
    r = ApplyResult(ok=True, steps=[
        StepResult(path="/x", attrs={}, ok=True),
    ])
    assert r.was_partial() is False
    assert r.result_status() == "success"
    assert r.recovery_hint_ar() == ""


def test_recovery_hint_partial_mentions_unprogram(app):
    from app.radius.services.mt_programming import (
        ApplyResult, StepResult,
    )
    r = ApplyResult(
        ok=False,
        steps=[
            StepResult(path="/ip/pool/add", attrs={}, ok=True),
            StepResult(path="/ip/address/add", attrs={},
                        ok=False, error="bad"),
        ],
        error="bad",
    )
    hint = r.recovery_hint_ar()
    assert "تراجع" in hint or "Unprogram" in hint


# ─── Route layer ──────────────────────────────────────────────


class _FailAtIndexClient:
    """Fake MikrotikClient that succeeds for the first N
    commands then raises. Lets us simulate a partial apply."""

    def __init__(self, fail_after: int):
        self.fail_after = fail_after
        self.calls: list[tuple[str, dict]] = []

    def connect(self): pass
    def close(self): pass

    def run(self, path, attrs=None):
        idx = len(self.calls)
        self.calls.append((path, dict(attrs or {})))
        if idx >= self.fail_after:
            raise RuntimeError("router rejected: simulated mid-apply fail")
        return []


def _stub_state(monkeypatch):
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether"},
        ]),
    )
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]),
    )
    monkeypatch.setattr(
        mac, "ip_routes",
        lambda nas: MtResult(ok=True, data=[]),
    )


def test_partial_apply_route_writes_partial_audit(app, client, monkeypatch):
    _seed_nas(app, nas_id=42)
    _login(client)
    _stub_state(monkeypatch)
    # Apply the first 2 commands then fail on the 3rd.
    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: _FailAtIndexClient(fail_after=2))

    token = _csrf(client)
    res = client.post("/admin/radius/mt/42/program/apply", data={
        "_csrf_token": token,
        "kind": "hotspot",
        "interface": "ether2",
        "cidr": "192.168.10.0/24",
        "hotspot_name": "hs",
        "dns_servers": "8.8.8.8,1.1.1.1",
        "lease_time": "1h",
        "confirm": "1",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # The page shows the recovery banner.
    assert "data-mt-apply-recovery" in html
    assert 'data-mt-apply-status="partial"' in html
    # And the audit row carries the partial status.
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(
            1, action="mt.programming.hotspot.apply")
    assert len(rows) >= 1
    assert rows[0]["result_status"] == "partial"
    assert rows[0]["severity"] == "warning"


def test_fully_failed_apply_writes_failed_audit(app, client, monkeypatch):
    _seed_nas(app, nas_id=43)
    _login(client)
    _stub_state(monkeypatch)
    # Fail on the very first command.
    from app.radius.routes import mt_programming as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas: _FailAtIndexClient(fail_after=0))

    token = _csrf(client)
    client.post("/admin/radius/mt/43/program/apply", data={
        "_csrf_token": token,
        "kind": "hotspot",
        "interface": "ether2",
        "cidr": "192.168.10.0/24",
        "hotspot_name": "hs",
        "dns_servers": "8.8.8.8,1.1.1.1",
        "confirm": "1",
    })
    with app.app_context():
        from app.radius.db.repos import audit_repo
        rows = audit_repo.recent(
            1, action="mt.programming.hotspot.apply")
    # Newest first; should be the apply we just ran on router 43.
    target = next((r for r in rows if r["router_id"] == 43), None)
    assert target is not None
    assert target["result_status"] == "failed"
    assert target["severity"] == "critical"
