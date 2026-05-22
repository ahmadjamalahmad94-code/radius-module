"""P7 — per-router risk-signal scanner + diagnostics tab.

Two angles:

  - Service layer (`mt_health.check_*`): drive each check with
    synthetic interface / address lists so we pin exact verdicts
    (duplicate MACs, loop-protect, subnet overlap, flapping).
    No network, no DB.

  - UI / endpoint: dashboard renders the panel shell, and the
    /api/v1/.../health endpoint is registered + reachable. We
    monkeypatch the admin-client readers to a controlled payload
    so the endpoint test doesn't depend on a live router.
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
    tmp = tempfile.mkdtemp(prefix="hr_p7_")
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
    u = f"p7_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="p7-pass", full_name="P7 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "p7-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'p7-rtr', '203.0.113.13', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


# ─── Service-layer checks ──────────────────────────────────────


def test_duplicate_macs_detects_two_physical_ports():
    from app.radius.services.mt_health import (
        check_duplicate_macs, SEVERITY_CRITICAL, SEVERITY_OK,
    )
    interfaces = [
        {"name": "ether1", "type": "ether",
         "mac-address": "AA:BB:CC:DD:EE:01"},
        {"name": "ether2", "type": "ether",
         "mac-address": "AA:BB:CC:DD:EE:01"},  # dup with ether1
        {"name": "ether3", "type": "ether",
         "mac-address": "AA:BB:CC:DD:EE:02"},
    ]
    res = check_duplicate_macs(interfaces)
    assert res["severity"] == SEVERITY_CRITICAL
    assert res["kind"] == "duplicate_macs"
    assert any(
        e["mac"] == "aa:bb:cc:dd:ee:01"
        and set(e["interfaces"]) == {"ether1", "ether2"}
        for e in res["evidence"]
    )


def test_duplicate_macs_ignores_bridge_and_vlan_children():
    """A bridge legitimately shares its MAC with one of its
    member ports — flagging that as a duplicate would generate
    constant noise. Same goes for vlan / bond / ppp tunnels."""
    from app.radius.services.mt_health import (
        check_duplicate_macs, SEVERITY_OK,
    )
    interfaces = [
        {"name": "ether1",  "type": "ether",
         "mac-address": "AA:BB:CC:DD:EE:01"},
        {"name": "bridge1", "type": "bridge",
         "mac-address": "AA:BB:CC:DD:EE:01"},
        {"name": "vlan10",  "type": "vlan",
         "mac-address": "AA:BB:CC:DD:EE:01"},
    ]
    res = check_duplicate_macs(interfaces)
    assert res["severity"] == SEVERITY_OK


def test_loop_protect_critical_on_disable_on_loop():
    from app.radius.services.mt_health import (
        check_loop_protect, SEVERITY_CRITICAL,
    )
    interfaces = [
        {"name": "ether1", "loop-protect-status": "off"},
        {"name": "ether2", "loop-protect-status": "disable-on-loop"},
    ]
    res = check_loop_protect(interfaces)
    assert res["severity"] == SEVERITY_CRITICAL
    assert res["evidence"] == [{"interface": "ether2"}]


def test_subnet_overlap_flags_different_interfaces():
    from app.radius.services.mt_health import (
        check_subnet_overlap, SEVERITY_WARNING,
    )
    addresses = [
        {"address": "10.0.0.1/24",  "interface": "bridge-lan"},
        {"address": "10.0.0.65/26", "interface": "ether5"},  # overlaps
        {"address": "192.168.88.1/24", "interface": "bridge-guest"},
    ]
    res = check_subnet_overlap(addresses)
    assert res["severity"] == SEVERITY_WARNING
    assert res["evidence"], "expected at least one overlap row"
    pair = res["evidence"][0]
    ifs = {pair["a"]["interface"], pair["b"]["interface"]}
    assert ifs == {"bridge-lan", "ether5"}


def test_subnet_overlap_ignores_same_interface_secondary():
    """RouterOS supports stacking multiple /ip/address rows on one
    interface — that's not a routing conflict, just a config style."""
    from app.radius.services.mt_health import (
        check_subnet_overlap, SEVERITY_OK,
    )
    addresses = [
        {"address": "10.0.0.1/24", "interface": "ether1"},
        {"address": "10.0.0.2/24", "interface": "ether1"},
    ]
    res = check_subnet_overlap(addresses)
    assert res["severity"] == SEVERITY_OK


def test_flapping_flags_high_link_down_counter():
    from app.radius.services.mt_health import (
        check_interface_flapping, SEVERITY_WARNING,
    )
    interfaces = [
        {"name": "ether1", "type": "ether", "link-downs": "2"},
        {"name": "ether2", "type": "ether", "link-downs": "47"},
        {"name": "ether3", "type": "ether", "link-downs": "0"},
    ]
    res = check_interface_flapping(interfaces)
    assert res["severity"] == SEVERITY_WARNING
    assert res["evidence"][0]["name"] == "ether2"
    assert res["evidence"][0]["link_downs"] == 47


def test_scan_router_aggregates_summary(monkeypatch):
    """When every individual check fires, the summary counts must
    match the actual severities — that's what drives the chips at
    the top of the panel."""
    from app.radius.services import mt_health, mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult

    fake_ifaces = [
        {"name": "ether1", "type": "ether",
         "mac-address": "AA:BB:CC:DD:EE:01",
         "link-downs": "0", "loop-protect-status": "off"},
        {"name": "ether2", "type": "ether",
         "mac-address": "AA:BB:CC:DD:EE:01",
         "link-downs": "100", "loop-protect-status": "disable-on-loop"},
    ]
    fake_addrs = [
        {"address": "10.0.0.1/24", "interface": "ether1"},
        {"address": "10.0.0.5/24", "interface": "ether2"},
    ]
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=fake_ifaces),
    )
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=fake_addrs),
    )
    report = mt_health.scan_router({"host": "x"})
    assert report["ok"] is True
    summary = report["summary"]
    # duplicate_macs + loop_protect → critical = 2
    # subnet_overlap + flapping     → warning  = 2
    assert summary["critical"] == 2
    assert summary["warning"] == 2
    assert summary["ok"] == 0


def test_scan_router_surfaces_fetch_errors(monkeypatch):
    """If interfaces / addresses can't be fetched, the report must
    still come back so the UI can paint 'unreachable' instead of
    silently rendering 'all green.'"""
    from app.radius.services import mt_health, mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=False, error="boom"),
    )
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=False, error="boom2"),
    )
    report = mt_health.scan_router({"host": "x"})
    assert "interfaces: boom" in report["fetch_errors"]
    assert "addresses: boom2" in report["fetch_errors"]


# ─── UI / endpoint ─────────────────────────────────────────────


def test_diagnostics_panel_shell(app, client):
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    for marker in (
        'data-mt-tab-panel="diagnostics"',
        "data-mt-health-card",
        "data-mt-health-list",
        "data-mt-health-msg",
        "data-mt-health-refresh",
        "data-mt-health-summary-critical",
        "data-mt-health-summary-warning",
        "data-mt-health-summary-ok",
    ):
        assert marker in html, f"missing marker: {marker}"


def test_diagnostics_panel_is_not_placeholder(app, client):
    _seed(app, nas_id=1)
    _login(client)
    html = client.get("/admin/radius/mt/1/dashboard").get_data(as_text=True)
    idx = html.index('data-mt-tab-panel="diagnostics"')
    block = html[idx:]
    assert "mt-tab-empty" not in block, (
        "diagnostics panel must be a real card after P7"
    )


def test_router_health_endpoint_registered(app):
    with app.app_context():
        rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/v1/mikrotik/<int:nas_id>/health" in rules
