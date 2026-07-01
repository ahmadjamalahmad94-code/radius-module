"""Regression: NAS-usage must count REGISTERED NAS, never accounting history.

Root cause this locks down: a customer's migration imported thousands of
``radacct`` accounting sessions referencing many distinct ``nasipaddress``
values, while ZERO real NAS devices were registered. The licensing panel showed
an inflated "NAS used" count. The correct metric — ``nas_count`` — counts the
``nas_devices`` table (the registered routers/APs the «أجهزة الشبكة/NAS» page
manages) and is fully independent of ``radacct``.

These tests assert the invariant directly on the producer side:
  * many radacct nasipaddress rows + 0 registered nas  → nas_count == 0
  * register N nas_devices                             → nas_count == N
  * the heartbeat's ``inventory`` block carries the same real nas_count
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import db, reset_for_tests


@pytest.fixture()
def app_db(monkeypatch, tmp_path):
    reset_for_tests(None)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.fspath(tmp_path / "nasusage.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app import create_app

    app = create_app()
    with app.app_context():
        yield app


def _seed_radacct_history(nas_ips: int, sessions_per_nas: int = 5) -> None:
    """Insert accounting history referencing ``nas_ips`` distinct NAS IPs.

    Mirrors what a customer data migration does — many closed sessions across
    many nasipaddress values — WITHOUT registering a single nas_devices row.
    """
    for i in range(nas_ips):
        nas_ip = f"10.{i // 256}.{i % 256}.1"
        for s in range(sessions_per_nas):
            db().execute(
                """
                INSERT INTO radacct
                    (tenant_id, acctsessionid, username, nasipaddress,
                     acctstarttime, acctstoptime)
                VALUES (1, ?, ?, ?, '2026-05-10T00:00:00Z', '2026-05-10T01:00:00Z')
                """,
                (f"sess-{i}-{s}", f"user-{i}", nas_ip),
            )


def _register_nas(n: int) -> None:
    for i in range(n):
        db().execute(
            """
            INSERT INTO nas_devices (tenant_id, name, address, created_at)
            VALUES (1, ?, ?, '2026-05-01T00:00:00Z')
            """,
            (f"router-{i}", f"192.168.{i}.1"),
        )


def test_nas_count_is_zero_when_only_accounting_history_exists(app_db):
    """2500+ accounting sessions across 500 distinct NAS IPs, 0 registered NAS."""
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    _seed_radacct_history(nas_ips=500, sessions_per_nas=5)  # 2500 radacct rows

    # Sanity: the history really does reference many distinct NAS IPs.
    distinct = db().execute(
        "SELECT COUNT(DISTINCT nasipaddress) AS c FROM radacct WHERE tenant_id = 1"
    ).fetchone()["c"]
    assert distinct == 500

    metrics = UsageMeteringService().collect_metrics(tenant_id=1)
    # The whole point: registered NAS = 0, regardless of accounting history.
    assert metrics["nas_count"] == 0
    assert metrics["routers_count"] == 0


def test_nas_count_equals_registered_devices_regardless_of_history(app_db):
    """Register 3 routers alongside a big history → nas_count == 3 (not 500)."""
    from app.radius.services.license_admin_usage_metering import UsageMeteringService

    _seed_radacct_history(nas_ips=500, sessions_per_nas=2)
    _register_nas(3)

    metrics = UsageMeteringService().collect_metrics(tenant_id=1)
    assert metrics["nas_count"] == 3
    assert metrics["routers_count"] == 3


def test_heartbeat_inventory_reports_real_registered_nas(app_db, monkeypatch):
    """The heartbeat payload carries the same radacct-independent nas_count."""
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_123456789")
    _seed_radacct_history(nas_ips=120, sessions_per_nas=3)  # 360 radacct rows
    _register_nas(2)

    payload = InstanceHealthService().build_payload(tenant_id=1)
    inventory = payload.get("inventory") or {}
    assert inventory.get("nas_count") == 2
    assert inventory.get("routers_count") == 2


def test_heartbeat_inventory_zero_nas_with_history(app_db, monkeypatch):
    from app.radius.services.license_admin_instance_health import InstanceHealthService

    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_123456789")
    _seed_radacct_history(nas_ips=300, sessions_per_nas=4)  # 1200 radacct rows, 0 nas

    payload = InstanceHealthService().build_payload(tenant_id=1)
    inventory = payload.get("inventory") or {}
    assert inventory.get("nas_count") == 0
