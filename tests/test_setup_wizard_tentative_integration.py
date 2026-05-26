"""T2 — Integration tests for tentative-reservation wiring.

Verifies:
  1. RouterProvisioningService.reserve_for_run() stamps a
     fresh TTL on every new reservation.
  2. RouterLifecycleService.transition() clears the TTL once
     the lifecycle reaches a permanent state (vpn_verified+).
  3. A failed lifecycle stays tentative — janitor can sweep it.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    token = "wiz-int-" + secrets.token_hex(8)
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp_path, "t.db"))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", token)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_VPN_POOL", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_SETUP_WIZARD_SERVER_VPN_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT", "1.2.3.4:51820")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "A" * 43 + "=")
    reset_for_tests(os.path.join(tmp_path, "t.db"))
    from app import create_app

    return create_app()


def _new_run(actor: str = "qa") -> int:
    from app.radius.services.setup_wizard import get_setup_wizard_service

    run = get_setup_wizard_service().create_run(
        tenant_id=1, actor=actor,
    )
    return int(run["id"])


def test_reserve_stamps_tentative_ttl(app):
    from app.radius.services.setup_wizard_router_provisioning import (
        RouterProvisioningService,
    )
    with app.app_context():
        run_id = _new_run()
        result = RouterProvisioningService().reserve_for_run(  # noqa: E501
            tenant_id=1,
            wizard_run_id=run_id,
            router_label="r1",
        )
        registry_id = int(result["id"])
        row = db().execute(
            """SELECT tentative_started_at, tentative_expires_at
               FROM router_provisioning_registry WHERE id=?""",
            (registry_id,),
        ).fetchone()
    assert row["tentative_started_at"], "started timestamp missing"
    assert row["tentative_expires_at"], "expires timestamp missing"
    # Expires should be ~30 min in the future (default TTL).
    expires_dt = datetime.fromisoformat(
        row["tentative_expires_at"].rstrip("Z")
    )
    future = datetime.utcnow() + timedelta(minutes=25)
    assert expires_dt > future, (
        "expires_at should be at least 25 min in the future"
    )


def test_lifecycle_promotion_clears_ttl(app):
    """When the router reaches vpn_verified, the TTL is removed
    so the janitor leaves it alone."""
    from app.radius.services.setup_wizard_router_lifecycle import (
        RouterLifecycleService,
    )
    from app.radius.services.setup_wizard_router_provisioning import (
        RouterProvisioningService,
    )
    with app.app_context():
        run_id = _new_run()
        result = RouterProvisioningService().reserve_for_run(  # noqa: E501
            tenant_id=1,
            wizard_run_id=run_id,
            router_label="ok",
        )
        registry_id = int(result["id"])
        # Walk through the transitions: reserved → script_generated
        # → waiting_router_key → peer_pending → peer_ready
        # → vpn_verified.
        svc = RouterLifecycleService()
        for state in (
            "script_generated", "waiting_router_key",
            "router_key_received", "peer_pending",
            "peer_ready", "vpn_verified",
        ):
            svc.transition(
                tenant_id=1, registry_id=registry_id,
                to_state=state, actor="qa",
            )
        row = db().execute(
            """SELECT tentative_expires_at, lifecycle_state
               FROM router_provisioning_registry WHERE id=?""",
            (registry_id,),
        ).fetchone()
    assert row["lifecycle_state"] == "vpn_verified"
    assert row["tentative_expires_at"] == "", (
        "TTL must be cleared once we hit a permanent state"
    )


def test_failed_lifecycle_keeps_ttl(app):
    """A 'failed' transition is NOT permanent — TTL stays so the
    janitor can reclaim the row on schedule."""
    from app.radius.services.setup_wizard_router_lifecycle import (
        RouterLifecycleService,
    )
    from app.radius.services.setup_wizard_router_provisioning import (
        RouterProvisioningService,
    )
    with app.app_context():
        run_id = _new_run()
        result = RouterProvisioningService().reserve_for_run(  # noqa: E501
            tenant_id=1,
            wizard_run_id=run_id,
            router_label="fail",
        )
        registry_id = int(result["id"])
        RouterLifecycleService().transition(
            tenant_id=1, registry_id=registry_id,
            to_state="failed", actor="qa", reason="test",
        )
        row = db().execute(
            """SELECT tentative_expires_at, lifecycle_state
               FROM router_provisioning_registry WHERE id=?""",
            (registry_id,),
        ).fetchone()
    assert row["lifecycle_state"] == "failed"
    assert row["tentative_expires_at"], (
        "failed lifecycle must keep its TTL"
    )


def test_default_ttl_env_override_propagates(app, monkeypatch):
    """Setting HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN affects the
    next reservation's expires_at."""
    monkeypatch.setenv("HOBERADIUS_WIZARD_TENTATIVE_TTL_MIN", "60")
    from app.radius.services.setup_wizard_router_provisioning import (
        RouterProvisioningService,
    )
    with app.app_context():
        run_id = _new_run()
        result = RouterProvisioningService().reserve_for_run(  # noqa: E501
            tenant_id=1,
            wizard_run_id=run_id,
            router_label="ttl60",
        )
        row = db().execute(
            """SELECT tentative_started_at, tentative_expires_at
               FROM router_provisioning_registry WHERE id=?""",
            (int(result["id"]),),
        ).fetchone()
    started = datetime.fromisoformat(
        row["tentative_started_at"].rstrip("Z")
    )
    expires = datetime.fromisoformat(
        row["tentative_expires_at"].rstrip("Z")
    )
    delta = (expires - started).total_seconds() / 60
    assert 55 < delta < 65, (
        f"TTL=60min expected, got {delta:.1f}min"
    )
