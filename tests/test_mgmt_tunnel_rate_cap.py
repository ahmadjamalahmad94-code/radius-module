# -*- coding: utf-8 -*-
"""Management-tunnel bandwidth cap (SSTP/PPTP via accel shaper + RADIUS Filter-Id).

Run alone (per-file isolation)."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_cap_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_ACCEL_SERVER_HOST", "187.77.70.18")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    application = create_app()
    yield application
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


# ── rate helpers ──
def test_rate_helpers_default_and_kbit(app, monkeypatch):
    with app.app_context():
        monkeypatch.delenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", raising=False)
        from app.radius.services import router_mgmt_tunnel as r
        assert r.mgmt_rate_mbps() == 10
        assert r.mgmt_rate_kbit() == 10000
        assert r.mgmt_filter_id_value() == "10000/10000"


def test_rate_disabled_yields_empty_filter_id(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "0")
        from app.radius.services import router_mgmt_tunnel as r
        assert r.mgmt_rate_mbps() == 0
        assert r.mgmt_filter_id_value() == ""


def test_rate_clamped_to_max(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "999999")
        from app.radius.services import router_mgmt_tunnel as r
        assert r.mgmt_rate_mbps() == 1000


# ── provisioning writes the cap ──
def test_provision_writes_filter_id_reply(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "10")
        from app.radius.services import router_mgmt_tunnel as r
        from app.radius.db.repos import freeradius_repo
        res = r.provision_tunnel("Cafe Noor", transport="sstp", tenant_id=1)
        replies = freeradius_repo.list_user_reply(1, res.tunnel_username)
        attrs = {row["attribute"]: row["value"] for row in replies}
        assert attrs.get("Filter-Id") == "10000/10000"
        assert attrs.get("Framed-IP-Address")            # still present


def test_provision_no_filter_id_when_disabled(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "0")
        from app.radius.services import router_mgmt_tunnel as r
        from app.radius.db.repos import freeradius_repo
        res = r.provision_tunnel("No Cap", transport="sstp", tenant_id=1)
        attrs = {row["attribute"]: row["value"]
                 for row in freeradius_repo.list_user_reply(1, res.tunnel_username)}
        assert "Filter-Id" not in attrs
        assert attrs.get("Framed-IP-Address")


# ── reconcile applies/strips the cap on existing accounts ──
def test_reconcile_applies_cap_to_existing(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "0")
        from app.radius.services import router_mgmt_tunnel as r
        from app.radius.db.repos import freeradius_repo
        res = r.provision_tunnel("Later Capped", transport="sstp", tenant_id=1)
        # initially uncapped
        attrs = {x["attribute"]: x["value"]
                 for x in freeradius_repo.list_user_reply(1, res.tunnel_username)}
        assert "Filter-Id" not in attrs
        # turn the cap on and reconcile
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "5")
        n = r.reconcile_rate_caps(tenant_id=1)
        assert n >= 1
        attrs = {x["attribute"]: x["value"]
                 for x in freeradius_repo.list_user_reply(1, res.tunnel_username)}
        assert attrs.get("Filter-Id") == "5000/5000"
        assert attrs.get("Framed-IP-Address")            # preserved


def test_reconcile_strips_cap_when_disabled(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "10")
        from app.radius.services import router_mgmt_tunnel as r
        from app.radius.db.repos import freeradius_repo
        res = r.provision_tunnel("Uncap Me", transport="sstp", tenant_id=1)
        assert any(x["attribute"] == "Filter-Id"
                   for x in freeradius_repo.list_user_reply(1, res.tunnel_username))
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "0")
        r.reconcile_rate_caps(tenant_id=1)
        attrs = {x["attribute"]: x["value"]
                 for x in freeradius_repo.list_user_reply(1, res.tunnel_username)}
        assert "Filter-Id" not in attrs
        assert attrs.get("Framed-IP-Address")            # preserved


# ── accel config carries the cap into [shaper] ──
def test_accel_conf_has_rate_limit(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "10")
        from app.radius.services import accel_config as ac
        params = ac.params_from_settings()
        assert params.rate_limit_kbit == 10000
        conf = ac.generate_accel_conf(params)
        shaper = conf[conf.index("[shaper]"):conf.index("[cli]")]
        assert "rate-limit=10000/10000" in shaper
        assert "attr=Filter-Id" in shaper


def test_accel_conf_no_rate_limit_when_disabled(app, monkeypatch):
    with app.app_context():
        monkeypatch.setenv("HOBERADIUS_MGMT_TUNNEL_RATE_MBPS", "0")
        from app.radius.services import accel_config as ac
        conf = ac.generate_accel_conf(ac.params_from_settings())
        shaper = conf[conf.index("[shaper]"):conf.index("[cli]")]
        assert "rate-limit" not in shaper
