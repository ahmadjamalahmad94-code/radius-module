"""QA: VPN-only CoA targeting.

find_all_nas_for_sessions must dial the resolved WireGuard peer for a VPN-mode
NAS — even if the recorded accounting source IP (radacct.nasipaddress) is a
public address — so CoA/Disconnect can never leak onto the public IP. A
direct-mode NAS still dials its accounting/address IP.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_vpn_coa_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app
    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _seed(app, *, acct_ip, mode, vpn_peer):
    ts = "2026-06-03T00:00:00Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO tenants(id, slug, name, created_at) "
                         "VALUES (1,'t1','T1',?)", (ts,))
            conn.execute(
                "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
                "nasipaddress, framedipaddress, callingstationid, acctstarttime, "
                "acctupdatetime, acctinputoctets, acctoutputoctets, acctsessiontime) "
                "VALUES (1,'S1','U1','u1',?, '10.19.6.254','AA:BB:CC:DD:EE:FF',?,?,0,0,0)",
                (acct_ip, ts, ts))
            conn.execute(
                "INSERT INTO nas_devices(tenant_id, name, address, secret, vendor, "
                "nas_type, coa_port, enabled, connection_mode, vpn_peer_address, "
                "created_at, updated_at) "
                "VALUES (1,'ccr3',?, 'sekret','mikrotik','hotspot',3799,1,?,?,?,?)",
                (acct_ip, mode, vpn_peer, ts, ts))


def _infos(app):
    with app.app_context():
        from app.radius.integration.radius_coa import find_all_nas_for_sessions
        return find_all_nas_for_sessions(1, "u1")


def test_vpn_mode_coa_targets_tunnel_even_if_accounting_was_public(app):
    # Router authenticated/accounted from a PUBLIC IP, but it is VPN-mode.
    _seed(app, acct_ip="203.0.113.5", mode="vpn", vpn_peer="10.10.0.2")
    infos = _infos(app)
    assert len(infos) == 1
    i = infos[0]
    assert i["nas_ip"] == "10.10.0.2"          # resolved tunnel peer, NOT the public IP
    assert i["nas_secret"] == "sekret"
    assert i["coa_port"] == 3799
    assert i["framed_ip"] == "10.19.6.254"
    assert i["calling_station_id"] == "AA:BB:CC:DD:EE:FF"


def test_direct_mode_coa_uses_accounting_address(app):
    _seed(app, acct_ip="10.50.0.9", mode="direct", vpn_peer="")
    infos = _infos(app)
    assert len(infos) == 1
    assert infos[0]["nas_ip"] == "10.50.0.9"   # direct → the accounting/address IP
