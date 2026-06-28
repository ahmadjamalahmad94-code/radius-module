"""Feature 1 — bandwidth profile REALLY enforces (Finding-1 → option A).

A profile referenced by a plan (access_plans.bandwidth_id) must drive the
Mikrotik-Rate-Limit in BOTH reply paths (group sync + live authorize), overriding
the plan's own speed fields; plans without a profile fall back unchanged. The
«apply to sessions» action pushes the profile rate live via CoA.
"""
from __future__ import annotations

from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    from app import create_app

    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso

        tenants_repo.ensure_default_tenant()
        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO access_plans(id,tenant_id,name,code,plan_type,"
                "service_type,duration_minutes,validity_days,speed_down_kbps,"
                "speed_up_kbps,price,currency,enabled,created_at) VALUES"
                "(1,1,'Enf Plan','ENF','time','PPPoE',1440,30,50000,25000,5,'JOD',1,?)",
                (now_iso(),),
            )
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _profile(app, *, down=100, down_u="Mbps", up=50, up_u="Mbps", burst=""):
    from app.radius.core.types_saas import BandwidthProfile
    from app.radius.db.repos import bandwidth_repo

    return bandwidth_repo.upsert(BandwidthProfile(
        id=None, tenant_id=1, name=f"prof_{uuid4().hex[:6]}",
        rate_down=down, rate_down_unit=down_u, rate_up=up, rate_up_unit=up_u,
        burst=burst))


# ───────────────────────── plan_rate_limit precedence ─────────────────────────
def test_plan_rate_limit_falls_back_to_plan_fields(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.bandwidth_rate import plan_rate_limit
        plan = AccessPlan(id=1, name="P", tenant_id=1,
                          speed_down_kbps=50000, speed_up_kbps=25000)
        assert plan_rate_limit(plan) == "25000k/50000k"


def test_plan_rate_limit_profile_overrides_plan_fields(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.bandwidth_rate import plan_rate_limit
        prof = _profile(app, down=100, down_u="Mbps", up=50, up_u="Mbps")
        plan = AccessPlan(id=1, name="P", tenant_id=1, bandwidth_id=prof.id,
                          speed_down_kbps=50000, speed_up_kbps=25000)
        # 100/50 Mbps → 102400/51200 kbps (1 Mbps = 1024 kbps), profile wins
        assert plan_rate_limit(plan) == "51200k/102400k"


def test_plan_rate_limit_profile_burst_wins(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.bandwidth_rate import plan_rate_limit
        prof = _profile(app, burst="20M/40M 30M/60M 25M/50M 8/8 8 30/30")
        plan = AccessPlan(id=1, name="P", tenant_id=1, bandwidth_id=prof.id,
                          speed_down_kbps=50000, speed_up_kbps=25000)
        assert plan_rate_limit(plan) == "20M/40M 30M/60M 25M/50M 8/8 8 30/30"


def test_plan_rate_limit_missing_profile_falls_back(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.bandwidth_rate import plan_rate_limit
        # bandwidth_id points to a non-existent profile → use plan fields
        plan = AccessPlan(id=1, name="P", tenant_id=1, bandwidth_id=99999,
                          speed_down_kbps=50000, speed_up_kbps=25000)
        assert plan_rate_limit(plan) == "25000k/50000k"


# ───────────────────────── group-sync reply path ─────────────────────────
def test_sync_plan_emits_profile_rate(app):
    with app.app_context():
        from app.radius.core.types import AccessPlan
        from app.radius.services.freeradius_translator import sync_plan
        from app.radius.db.repos import freeradius_repo
        prof = _profile(app, down=200, down_u="Mbps", up=100, up_u="Mbps")
        plan = AccessPlan(id=1, name="P", tenant_id=1, bandwidth_id=prof.id,
                          speed_down_kbps=50000, speed_up_kbps=25000)
        sync_plan(plan)
        reply = freeradius_repo.list_group_reply(1, "plan_1")
        rate = next(r["value"] for r in reply if r["attribute"] == "Mikrotik-Rate-Limit")
        assert rate == "102400k/204800k"  # profile, not the plan's 25000k/50000k


# ───────────────────────── effective-rate cascade base ─────────────────────────
def test_effective_rate_limit_uses_profile_for_subscriber(app):
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        from app.radius.db.connection import transaction
        from app.radius.services.bandwidth_rate import effective_rate_limit

        prof = _profile(app, down=100, down_u="Mbps", up=50, up_u="Mbps")
        with transaction() as conn:
            conn.execute("UPDATE access_plans SET bandwidth_id=? WHERE id=1", (prof.id,))
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username="enfuser", password="p", tenant_id=1, plan_id=1))
        # no schedule, no subscriber override → base tier = profile
        assert effective_rate_limit(1, "enfuser") == "51200k/102400k"


# ───────────────────────── live gate default ─────────────────────────
def test_live_apply_enabled_defaults_on(app, monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", raising=False)
    with app.app_context():
        from app.radius.services.bandwidth_rate import live_apply_enabled
        assert live_apply_enabled() is True


def test_live_apply_enabled_off_when_zero(app, monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "0")
    with app.app_context():
        from app.radius.services.bandwidth_rate import live_apply_enabled
        assert live_apply_enabled() is False


# ───────────────────────── apply-to-sessions route + RBAC ─────────────────────────
def _login_super(client):
    from app.radius.db.repos import admins_repo
    u = f"enf_super_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="p", full_name="S", is_super_admin=True)
    assert client.post("/admin/radius/login", data={"username": u, "password": "p"},
                       follow_redirects=False).status_code in {302, 303}


def _login_perms(client, perms):
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import json_dump, now_iso
    from app.radius.db.repos import admins_repo
    rn = f"enf_role_{uuid4().hex[:6]}"
    with transaction() as conn:
        rid = conn.execute(
            "INSERT INTO roles(tenant_id,name,display_name,description,permissions,"
            "is_system,created_at) VALUES(NULL,?,?,'',?,0,?)",
            (rn, rn, json_dump(list(perms)), now_iso())).lastrowid
    u = f"enf_user_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="p", full_name="L", role_id=rid,
                             is_super_admin=False)
    assert client.post("/admin/radius/login", data={"username": u, "password": "p"},
                       follow_redirects=False).status_code in {302, 303}


def _tok(client, url="/admin/radius/bandwidth"):
    client.get(url)
    with client.session_transaction() as s:
        return s["_csrf_token"]


def test_bw_apply_route_super_ok(client, app):
    prof = _profile(app)
    _login_super(client)
    tok = _tok(client)
    res = client.post(f"/admin/radius/bandwidth/{prof.id}/apply",
                      data={"_csrf_token": tok}, follow_redirects=False)
    assert res.status_code in {302, 303}  # not 403/404/500


def test_bw_apply_route_unauthorized_403(client, app):
    prof = _profile(app)
    _login_perms(client, ("dashboard.view", "plans.view"))  # no plans.edit
    # page guard for /bandwidth is plans.view (granted), so GET works for the token
    tok = _tok(client)
    res = client.post(f"/admin/radius/bandwidth/{prof.id}/apply",
                      data={"_csrf_token": tok}, follow_redirects=False)
    assert res.status_code == 403
