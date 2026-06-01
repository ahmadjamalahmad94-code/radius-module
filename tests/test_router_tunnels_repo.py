"""Tests for migration 092 + the router tunnel-profile repo."""
from __future__ import annotations

import os

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "router_tunnels.db")
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


def _make_router(tenant_id=1):
    from app.radius.core.types import NasDevice
    from app.radius.db.repos import nas_repo

    saved = nas_repo.upsert_nas(NasDevice(
        id=None, name="LAB-R6", address="10.0.0.6", secret="x", vendor="mikrotik",
        tenant_id=tenant_id,
    ))
    return saved.id


def test_migration_defaults_are_safe(app):
    with app.app_context():
        nas_id = _make_router()
        from app.radius.db.repos import router_tunnels_repo as repo
        prof = repo.get_tunnel_profile(1, nas_id)
    assert prof is not None
    assert prof["management_tunnel_type"] == "none"
    assert prof["traffic_tunnel_type"] == "none"
    assert prof["traffic_mode"] == "disabled"
    assert prof["traffic_enabled"] == 0
    assert prof["sstp_verify_certificate"] == 0
    # no plaintext secret columns — only masked refs, empty by default
    assert prof["management_secret_ref"] == ""
    assert prof["traffic_ipsec_secret_ref"] == ""


def test_update_tunnel_profile_roundtrips(app):
    with app.app_context():
        nas_id = _make_router()
        from app.radius.db.repos import router_tunnels_repo as repo
        ok = repo.update_tunnel_profile(
            1, nas_id,
            management_tunnel_type="sstp_mgmt",
            management_tunnel_interface_name="sstp-hoberadius-mgmt",
            management_tunnel_status="pending",
            sstp_verify_certificate=1,
            traffic_tunnel_type="l2tp_ipsec_traffic",
            traffic_mode="policy_routing",
            traffic_enabled=1,
            management_secret_ref="ref:abcd1234",
        )
        prof = repo.get_tunnel_profile(1, nas_id)
    assert ok is True
    assert prof["management_tunnel_type"] == "sstp_mgmt"
    assert prof["traffic_mode"] == "policy_routing"
    assert prof["traffic_enabled"] == 1
    assert prof["sstp_verify_certificate"] == 1
    assert prof["management_secret_ref"] == "ref:abcd1234"
    assert prof["tunnel_updated_at"] > 0


def test_update_rejects_unknown_and_secret_columns(app):
    with app.app_context():
        nas_id = _make_router()
        from app.radius.db.repos import router_tunnels_repo as repo
        with pytest.raises(ValueError):
            repo.update_tunnel_profile(1, nas_id, password="leak")
        with pytest.raises(ValueError):
            repo.update_tunnel_profile(1, nas_id, ipsec_secret="leak")
        with pytest.raises(ValueError):
            repo.update_tunnel_profile(1, nas_id, not_a_column="x")


def test_tenant_isolation(app):
    with app.app_context():
        nas_id = _make_router(tenant_id=1)
        from app.radius.db.repos import router_tunnels_repo as repo
        # another tenant can't see or update this router's tunnel
        assert repo.get_tunnel_profile(2, nas_id) is None
        assert repo.update_tunnel_profile(2, nas_id, traffic_mode="full_tunnel") is False
