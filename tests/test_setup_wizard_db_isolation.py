from __future__ import annotations

import os

from app.radius.db.connection import db, reset_for_tests


REQUIRED_SETUP_WIZARD_TABLES = {
    "setup_wizard_runs",
    "setup_wizard_steps",
    "setup_wizard_operations",
    "setup_wizard_router_snapshots",
    "router_provisioning_registry",
    "router_ip_allocations",
    "router_lifecycle_events",
    "prepared_wireguard_peers",
    "prepared_wireguard_peer_operations",
    "setup_wizard_recovery_events",
}


def _configure_env(monkeypatch, db_file) -> None:
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(db_file))
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "db-isolation-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)


def _create_app_for_path(monkeypatch, db_file):
    _configure_env(monkeypatch, db_file)
    from app import create_app

    return create_app()


def _table_names() -> set[str]:
    rows = db().execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    return {str(row["name"]) for row in rows}


def test_standard_test_app_migration_path_creates_complete_setup_wizard_schema(
    monkeypatch, tmp_path
):
    reset_for_tests(None)
    app = _create_app_for_path(monkeypatch, tmp_path / "schema.db")

    with app.app_context():
        missing = REQUIRED_SETUP_WIZARD_TABLES - _table_names()
        assert missing == set()


def test_db_path_env_switch_creates_schema_in_next_test_database_without_manual_reset(
    monkeypatch, tmp_path
):
    reset_for_tests(None)
    first = _create_app_for_path(monkeypatch, tmp_path / "first.db")
    with first.app_context():
        assert "setup_wizard_runs" in _table_names()

    second = _create_app_for_path(monkeypatch, tmp_path / "second.db")
    with second.app_context():
        missing = REQUIRED_SETUP_WIZARD_TABLES - _table_names()
        assert missing == set()

        from app.radius.services.setup_wizard import get_setup_wizard_service

        run = get_setup_wizard_service().create_run(tenant_id=1, actor="db-isolation")
        assert run["id"] >= 1


def test_router_provisioning_lifecycle_and_fleet_services_share_complete_schema(
    monkeypatch, tmp_path
):
    reset_for_tests(None)
    app = _create_app_for_path(monkeypatch, tmp_path / "services.db")

    with app.app_context():
        from app.radius.services.setup_wizard import get_setup_wizard_service
        from app.radius.services.setup_wizard_fleet import RouterFleetProvisioningService
        from app.radius.services.setup_wizard_router_lifecycle import RouterLifecycleService
        from app.radius.services.setup_wizard_router_provisioning import (
            RouterProvisioningService,
        )

        run = get_setup_wizard_service().create_run(tenant_id=1, actor="db-isolation")
        reservation = RouterProvisioningService().reserve_for_run(
            tenant_id=1,
            wizard_run_id=int(run["id"]),
            router_label="Isolation Router",
        )
        assert reservation["router_vpn_ip"] == "10.10.0.2"
        assert (
            RouterLifecycleService().current_state(
                tenant_id=1,
                registry_id=int(reservation["id"]),
            )
            == "reserved"
        )

        fleet = RouterFleetProvisioningService().summary(tenant_id=1)
        assert fleet["metrics"]["total_routers"] == 1
