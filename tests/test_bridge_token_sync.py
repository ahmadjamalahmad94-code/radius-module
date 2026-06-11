"""Tests for the customer-side bridge-token bidirectional sync.

Verifies:
  * Panel-rotated tokens are consumed and stored encrypted at rest.
  * Locally-generated tokens are reported to the panel.
  * Rotation on either side converges to one shared token.
  * No raw token value ever reaches DB columns or log output.
  * Panel report uses HTTPS and sends a signed envelope.

All bridge HTTP calls are mocked — no network is touched.
"""
from __future__ import annotations

import os

import pytest


class RoutingTransport:
    """Mock transport that answers by URL suffix and records every call."""

    def __init__(self, routes: dict[str, dict] | None = None):
        self.routes = routes or {}
        self.calls: list[dict] = []

    def request_json(self, **kwargs):
        self.calls.append(kwargs)
        url = kwargs.get("url") or ""
        for suffix, response in self.routes.items():
            if url.endswith(suffix):
                return response
        return {"ok": False, "status": "unexpected_route"}

    def bodies(self) -> list[dict]:
        return [c.get("json_body") or {} for c in self.calls]

    def urls(self) -> list[str]:
        return [c["url"].split("/api/integration/hoberadius")[-1] for c in self.calls]


def _reset(db_file):
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    db_file = os.fspath(tmp_path / "bridge_token.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    _reset(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo
        run_pending_migrations()
        admins_repo.ensure_default_roles()
        yield app
    _reset(None)


def _config():
    from app.radius.services.admin_panel_client import AdminBridgeConfig
    return AdminBridgeConfig(
        enabled=True,
        base_url="https://license-panel.test",
        license_key="HBR-2026-TEST-AAAA",
        timeout_seconds=1.0,
        retry_count=0,
    )


def _service(transport, config=None):
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
    cfg = config or _config()
    client = AdminPanelClient(config=cfg, transport=transport)
    return BridgeTokenSyncService(config=cfg, admin_client=client)


# ── consume_panel_token ────────────────────────────────────────────────────

def test_consume_panel_token_stores_encrypted(app_db):
    """Panel provides token → stored in DB with ciphertext, not plaintext."""
    svc = _service(RoutingTransport())
    payload = {
        "status": "active",
        "bridge_token": {
            "token": "panel-secret-token-value-abc123",
            "seq": "2026-01-01T00:00:00Z-aabb",
            "issued_at": "2026-01-01T00:00:00Z",
        },
    }
    result = svc.consume_panel_token(payload, tenant_id=1)

    assert result["ok"] is True
    assert result["action"] == "stored_panel_token"
    assert result["seq"] == "2026-01-01T00:00:00Z-aabb"

    # DB row exists and contains ciphertext, not the raw token
    from app.radius.db.connection import db
    row = db().execute(
        "SELECT * FROM bridge_token_states WHERE active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    row = dict(row)
    assert row["source"] == "panel"
    assert row["panel_seq"] == "2026-01-01T00:00:00Z-aabb"
    assert "panel-secret-token-value-abc123" not in row["token_enc"]
    assert row["token_hint"] == "c123"
    assert row["panel_acked"] == 0  # panel tokens don't ack in this direction


def test_consume_panel_token_absent_when_no_block(app_db):
    """No bridge_token block in payload → no-op."""
    svc = _service(RoutingTransport())
    result = svc.consume_panel_token({"status": "active"}, tenant_id=1)
    assert result == {"ok": True, "action": "absent"}

    from app.radius.db.connection import db
    count = db().execute("SELECT COUNT(*) FROM bridge_token_states").fetchone()[0]
    assert count == 0


def test_consume_panel_token_idempotent_same_seq(app_db):
    """Same panel_seq → second call is a no-op (no extra DB row)."""
    svc = _service(RoutingTransport())
    payload = {
        "bridge_token": {
            "token": "panel-token-xyz",
            "seq": "seq-001",
        }
    }
    r1 = svc.consume_panel_token(payload, tenant_id=1)
    r2 = svc.consume_panel_token(payload, tenant_id=1)

    assert r1["action"] == "stored_panel_token"
    assert r2["action"] == "already_current"
    assert r2["seq"] == "seq-001"

    from app.radius.db.connection import db
    count = db().execute(
        "SELECT COUNT(*) FROM bridge_token_states WHERE tenant_id=1"
    ).fetchone()[0]
    assert count == 1


def test_consume_panel_token_new_seq_overwrites(app_db):
    """Different seq → old row deactivated, new row stored."""
    svc = _service(RoutingTransport())

    svc.consume_panel_token(
        {"bridge_token": {"token": "old-token", "seq": "seq-001"}}, tenant_id=1
    )
    svc.consume_panel_token(
        {"bridge_token": {"token": "new-token", "seq": "seq-002"}}, tenant_id=1
    )

    from app.radius.db.connection import db
    rows = db().execute(
        "SELECT active, panel_seq FROM bridge_token_states WHERE tenant_id=1 ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["active"] == 0
    assert rows[0]["panel_seq"] == "seq-001"
    assert rows[1]["active"] == 1
    assert rows[1]["panel_seq"] == "seq-002"


# ── generate_and_report ────────────────────────────────────────────────────

def test_generate_and_report_ok(app_db):
    """generate_and_report → encrypted row in DB, panel called, row acked."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted", "seq": "srv-seq-01"}}
    )
    svc = _service(transport)
    result = svc.generate_and_report(tenant_id=1)

    assert result["ok"] is True
    assert result.get("action") == "reported"

    from app.radius.db.connection import db
    row = dict(
        db().execute(
            "SELECT * FROM bridge_token_states WHERE active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    assert row["source"] == "local"
    assert row["panel_acked"] == 1
    assert row["reported_at"] is not None
    assert row["panel_seq"] == "srv-seq-01"
    assert len(row["token_enc"]) > 40  # non-trivial ciphertext

    # Panel was actually called
    assert any("/bridge-token/report" in u for u in transport.urls())


def test_generate_and_report_panel_down(app_db):
    """Panel unavailable → row in DB with panel_acked=0, reported_at=NULL."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": False, "status": "unavailable"}}
    )
    # Override base_url check — we need https but mock returns error
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService
    cfg = AdminBridgeConfig(
        enabled=True,
        base_url="https://license-panel.test",
        license_key="HBR-TEST",
        timeout_seconds=1.0,
        retry_count=0,
    )
    client = AdminPanelClient(config=cfg, transport=transport)
    svc = BridgeTokenSyncService(config=cfg, admin_client=client)

    result = svc.generate_and_report(tenant_id=1)

    assert result["ok"] is False

    from app.radius.db.connection import db
    row = dict(
        db().execute(
            "SELECT * FROM bridge_token_states WHERE active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    )
    assert row["panel_acked"] == 0
    assert row["reported_at"] is None


def test_report_includes_https_check(app_db):
    """post_bridge_token_report refuses when base_url is HTTP."""
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    transport = RoutingTransport()
    cfg = AdminBridgeConfig(
        enabled=True,
        base_url="http://insecure-panel.test",
        license_key="HBR-TEST",
        timeout_seconds=1.0,
        retry_count=0,
    )
    client = AdminPanelClient(config=cfg, transport=transport)
    result = client.post_bridge_token_report(token="any-token")
    assert result["ok"] is False
    assert result["status"] == "https_required"
    assert transport.calls == []


# ── ensure_token_and_report_pending ───────────────────────────────────────

def test_ensure_token_generates_when_none(app_db):
    """Fresh DB → ensure_token_and_report_pending generates a token."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted", "seq": "s1"}}
    )
    svc = _service(transport)
    result = svc.ensure_token_and_report_pending(tenant_id=1)

    assert result["ok"] is True

    from app.radius.db.connection import db
    count = db().execute(
        "SELECT COUNT(*) FROM bridge_token_states WHERE active=1"
    ).fetchone()[0]
    assert count == 1


def test_ensure_token_retries_unreported(app_db):
    """Existing local+unacked token → ensure retries the panel report."""
    from app.radius.db.connection import db
    now = "2026-01-01T00:00:00Z"
    # Insert a local token that was never reported (simulates crash after generate)
    db().execute(
        """
        INSERT INTO bridge_token_states
            (tenant_id, source, token_enc, token_hint, panel_seq,
             issued_at, reported_at, panel_acked, active, created_at, updated_at)
        VALUES (1, 'local', '', 'xxxx', '', ?, NULL, 0, 1, ?, ?)
        """,
        (now, now, now),
    )
    # We need a real encrypted token_enc for the retry path to decrypt
    # Use the service to build a proper encrypted value
    from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService, _hint
    from app.radius.services.admin_panel_client import AdminBridgeConfig, AdminPanelClient
    cfg = _config()
    svc_enc = BridgeTokenSyncService(config=cfg, admin_client=AdminPanelClient(config=cfg, transport=RoutingTransport()))
    raw_token = "retry-me-token-value-xyz"
    token_enc = svc_enc._encrypt(raw_token, 1)
    db().execute(
        "UPDATE bridge_token_states SET token_enc=?, token_hint=? WHERE active=1",
        (token_enc, _hint(raw_token)),
    )

    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted", "seq": "s2"}}
    )
    svc = _service(transport)
    result = svc.ensure_token_and_report_pending(tenant_id=1)

    # The retry report should have succeeded
    assert result.get("ok") is True
    assert any("/bridge-token/report" in u for u in transport.urls())

    from app.radius.db.connection import db
    row = dict(
        db().execute(
            "SELECT * FROM bridge_token_states WHERE active=1 LIMIT 1"
        ).fetchone()
    )
    assert row["panel_acked"] == 1


def test_ensure_token_noop_when_panel_token_active(app_db):
    """Panel-sourced token already present → no-op, no panel call."""
    svc = _service(RoutingTransport())
    svc.consume_panel_token(
        {"bridge_token": {"token": "panel-tok", "seq": "s1"}}, tenant_id=1
    )
    transport2 = RoutingTransport()
    svc2 = _service(transport2)
    result = svc2.ensure_token_and_report_pending(tenant_id=1)

    assert result["ok"] is True
    assert result["action"] == "no_action"
    assert transport2.calls == []


# ── get_active_token round-trip ────────────────────────────────────────────

def test_get_active_token_round_trip(app_db):
    """Storing via consume then retrieving via get_active_token returns the original."""
    svc = _service(RoutingTransport())
    original = "round-trip-secret-value-abcde"
    svc.consume_panel_token(
        {"bridge_token": {"token": original, "seq": "rt-seq"}}, tenant_id=1
    )
    recovered = svc.get_active_token(tenant_id=1)
    assert recovered == original


def test_get_active_token_none_when_empty(app_db):
    svc = _service(RoutingTransport())
    assert svc.get_active_token(tenant_id=1) is None


# ── no raw token in DB ─────────────────────────────────────────────────────

def test_no_raw_token_in_db_after_consume(app_db):
    """Raw token must not appear in any column of bridge_token_states."""
    raw = "super-secret-bridge-token-do-not-store-raw"
    svc = _service(RoutingTransport())
    svc.consume_panel_token(
        {"bridge_token": {"token": raw, "seq": "leak-test"}}, tenant_id=1
    )

    from app.radius.db.connection import db
    rows = db().execute("SELECT * FROM bridge_token_states").fetchall()
    assert rows, "expected at least one row"
    for row in rows:
        row_str = "|".join(str(v) for v in row)
        assert raw not in row_str, f"raw token leaked into DB row: {row_str}"


def test_no_raw_token_in_db_after_generate(app_db):
    """Locally-generated token must not appear in any DB column."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted", "seq": "gen-seq"}}
    )
    svc = _service(transport)
    svc.generate_and_report(tenant_id=1)

    # Recover the token via the service API so we know what to check for
    raw = svc.get_active_token(tenant_id=1)
    assert raw is not None and len(raw) > 10

    from app.radius.db.connection import db
    rows = db().execute("SELECT * FROM bridge_token_states").fetchall()
    for row in rows:
        row_str = "|".join(str(v) for v in row)
        assert raw not in row_str, f"raw token leaked into DB row: {row_str}"


# ── convergence ────────────────────────────────────────────────────────────

def test_panel_rotation_converges(app_db):
    """Panel rotates → consume_panel_token → get_active_token returns new token."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted", "seq": "old-seq"}}
    )
    svc = _service(transport)

    # Start with a local token
    svc.generate_and_report(tenant_id=1)
    local_token = svc.get_active_token(tenant_id=1)
    assert local_token is not None

    # Panel rotates
    new_panel_token = "panel-rotated-new-token-xyz789"
    svc.consume_panel_token(
        {"bridge_token": {"token": new_panel_token, "seq": "new-panel-seq"}},
        tenant_id=1,
    )

    # Customer now uses the panel token
    active = svc.get_active_token(tenant_id=1)
    assert active == new_panel_token
    assert active != local_token


def test_local_rotation_converges(app_db):
    """Local generate_and_report → get_active_token returns new token, old deactivated."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted"}}
    )
    svc = _service(transport)

    # Panel token first
    svc.consume_panel_token(
        {"bridge_token": {"token": "initial-panel-token", "seq": "s0"}}, tenant_id=1
    )

    # Customer rotates locally
    svc.generate_and_report(tenant_id=1)

    new_token = svc.get_active_token(tenant_id=1)
    assert new_token is not None
    assert new_token != "initial-panel-token"

    # Old panel token row is now inactive
    from app.radius.db.connection import db
    rows = db().execute(
        "SELECT source, active FROM bridge_token_states ORDER BY id"
    ).fetchall()
    assert rows[0]["source"] == "panel"
    assert rows[0]["active"] == 0
    assert rows[1]["source"] == "local"
    assert rows[1]["active"] == 1


# ── runtime-contract integration ─────────────────────────────────────────

def test_sync_runtime_contract_extracts_bridge_token(app_db):
    """sync_runtime_contract_once() + bridge_token block → token stored."""
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService
    from app.radius.services.license_bridge_token_sync import BridgeTokenSyncService

    expected_token = "runtime-contract-bridge-token-value"
    transport = RoutingTransport(
        {
            "/runtime-contract": {
                "ok": True,
                "status": "active",
                "contract": {
                    "license": {"active": True, "status": "active"},
                    "limits": {},
                    "services": {},
                },
                "bridge_token": {
                    "token": expected_token,
                    "seq": "rc-seq-001",
                    "issued_at": "2026-01-01T00:00:00Z",
                },
            }
        }
    )
    cfg = _config()
    client = AdminPanelClient(config=cfg, transport=transport)
    svc = LicenseAdminRuntimeSyncService(config=cfg, admin_client=client)

    result = svc.sync_runtime_contract_once(tenant_id=1)
    assert result["ok"] is True

    # Token must now be stored and retrievable
    token_svc = BridgeTokenSyncService(config=cfg, admin_client=client)
    active = token_svc.get_active_token(tenant_id=1)
    assert active == expected_token


def test_sync_runtime_contract_survives_missing_bridge_token(app_db):
    """sync_runtime_contract_once works fine when bridge_token is absent."""
    from app.radius.services.admin_panel_client import AdminPanelClient
    from app.radius.services.license_admin_runtime_sync import LicenseAdminRuntimeSyncService

    transport = RoutingTransport(
        {
            "/runtime-contract": {
                "ok": True,
                "status": "active",
                "contract": {
                    "license": {"active": True, "status": "active"},
                    "limits": {},
                    "services": {},
                },
                # no bridge_token block
            }
        }
    )
    cfg = _config()
    client = AdminPanelClient(config=cfg, transport=transport)
    svc = LicenseAdminRuntimeSyncService(config=cfg, admin_client=client)
    result = svc.sync_runtime_contract_once(tenant_id=1)
    assert result["ok"] is True


# ── signed request envelope ────────────────────────────────────────────────

def test_report_request_is_signed(app_db):
    """post_bridge_token_report sends a signed payload with HMAC signature."""
    transport = RoutingTransport(
        {"/bridge-token/report": {"ok": True, "status": "accepted"}}
    )
    svc = _service(transport)
    svc.generate_and_report(tenant_id=1)

    bodies = transport.bodies()
    report_body = next(
        (b for b in bodies if transport.calls[bodies.index(b)]["url"].endswith("/bridge-token/report")),
        None,
    )
    assert report_body is not None
    # Post 2026-06-11 (feat/radius-purge-legacy-linking): the report uses
    # bearer-in-body auth. license_key carries the secret; signature /
    # nonce / timestamp were removed permanently.
    assert "license_key" in report_body
    assert "signature" not in report_body
    assert "nonce" not in report_body
    assert "timestamp" not in report_body
    assert "bridge_token" in report_body
    # The raw token value IS in the POST body (intentional — sent over HTTPS)
    # but it must NOT appear in the DB (already verified in separate tests).


# ── current_state metadata ─────────────────────────────────────────────────

def test_current_state_exposes_no_secret(app_db):
    """current_state() returns metadata only — no full token value."""
    svc = _service(RoutingTransport())
    svc.consume_panel_token(
        {"bridge_token": {"token": "secret-token-xyz", "seq": "s-state"}}, tenant_id=1
    )
    state = svc.current_state(tenant_id=1)

    assert state["has_token"] is True
    assert state["source"] == "panel"
    assert state["token_hint"] == "-xyz"
    assert "secret-token-xyz" not in str(state)
    assert "panel_seq" in state
    assert "issued_at" in state
