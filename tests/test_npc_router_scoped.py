"""NPC into the router dashboard — per-router scoped routes,
sidebar reduced to one entry, dashboard quicknav card."""
from __future__ import annotations

import os
import re
import sys
import tempfile
from types import SimpleNamespace

import pytest


# ─── Fixture (same shape as test_npc_6_admin_ui.py) ─────────


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_scoped_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
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


def _login_super(client, monkeypatch):
    super_admin = SimpleNamespace(
        id=1, username="alice", is_super_admin=True,
    )

    class _Store:
        @staticmethod
        def get_admin(_id):
            return super_admin

    class _Svc:
        _store = _Store()
        def permissions_of(self, _admin):
            return ()

    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())

    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "alice"
        s["tenant_id"] = 1


def _seed_router(app, *, name="rt", address="10.0.0.1"):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices (tenant_id, name, "
                "shortname, address, secret, vendor, nas_type, "
                "ports, snmp_community, auth_port, acct_port, "
                "coa_port, api_port, api_user, api_password, "
                "api_use_tls, location, coordinates, "
                "monitoring_enabled, description, enabled, "
                "require_message_authenticator, ssh_port, "
                "tags, metadata, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, '', 'mikrotik', "
                "'router', 0, '', 1812, 1813, 3799, 8728, "
                "'admin', 'real-password', 0, '', '', 0, '', "
                "1, 0, 22, '', '{}', "
                "'2026-01-01','2026-01-01')",
                (name, name, address),
            )
            return int(cur.lastrowid)


# ─── Dashboard quicknav card ────────────────────────────────


def test_router_dashboard_surfaces_web_block_and_walled_garden_directly(
    app, client, monkeypatch,
):
    """يونيو 2026 — طلب المالك: حُذف غلاف «سياسات الشبكة» (NPC) من
    شبكة خدمات لوحة الراوتر، وأُعيدت «حظر المواقع» و«المواقع
    المسموحة» كبطاقتَين مستقلّتَين تربطان مباشرةً إلى صفحاتهما
    المُجزّأة (npc_*_list_scoped) بلا المرور بصفحة هبوط NPC.
    الـroutes تحت /network-policies/ تبقى مُسجَّلة فمن يصلها
    مباشرةً يصل، فقط سطح الـUI لم يَعُد يَعرض الغلاف."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    r = client.get(f"/admin/radius/mt/{rid}/dashboard")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # «سياسات الشبكة» (الغلاف القديم) لم تَعُد على الصفحة
    assert 'data-test="mt-dashboard-npc-link"' not in html
    assert "سياسات الشبكة" not in html
    # حظر المواقع + المواقع المسموحة بطاقتان مباشرتان (نفس الـURLs
    # المُجزّأة، لكن دون عبور صفحة NPC).
    assert "حظر المواقع" in html
    assert "المواقع المسموحة" in html
    assert f"/admin/radius/mt/{rid}/network-policies/web-block/" in html
    assert f"/admin/radius/mt/{rid}/network-policies/walled-garden/" in html


def test_router_dashboard_has_device_health_card(
    app, client, monkeypatch,
):
    """يونيو 2026 — طلب المالك: أُضيفت بطاقة «تتبع حالة الأجهزة»
    إلى شبكة خدمات لوحة الراوتر. تربط مباشرةً بصفحة المراقبة
    على مستوى المستأجر (/admin/radius/device-health)."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    r = client.get(f"/admin/radius/mt/{rid}/dashboard")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'data-mt-router-link="device-health"' in html
    assert "تتبع حالة الأجهزة" in html
    assert "/admin/radius/device-health" in html


# ─── Router-scoped routes ───────────────────────────────────


def test_router_landing_redirects_to_remote_access_scoped(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    r = client.get(
        f"/admin/radius/mt/{rid}/network-policies/",
        follow_redirects=False,
    )
    assert r.status_code in (301, 302, 303, 307, 308)
    loc = r.headers["Location"]
    assert "remote-access" in loc
    assert f"/mt/{rid}/" in loc


def test_router_landing_404_for_unknown_router(
    app, client, monkeypatch,
):
    _login_super(client, monkeypatch)
    # The landing redirects unconditionally to the
    # remote-access list, where the scoped list view performs
    # the tenant + existence check.
    r = client.get(
        "/admin/radius/mt/9999/network-policies/remote-access/",
        follow_redirects=False,
    )
    assert r.status_code == 404


@pytest.mark.parametrize("slug", [
    "remote-access", "web-block", "walled-garden",
])
def test_scoped_list_renders_with_router_context(
    app, client, monkeypatch, slug,
):
    rid = _seed_router(app, name="rtr-A", address="10.0.0.7")
    _login_super(client, monkeypatch)
    r = client.get(
        f"/admin/radius/mt/{rid}/network-policies/{slug}/"
    )
    assert r.status_code == 200, slug
    html = r.data.decode("utf-8")
    # The router-context strip carries the router name and scoped links.
    assert "rtr-A" in html
    assert f"/admin/radius/mt/{rid}/" in html
    assert "npc-tabs" in html
    assert "Dry-Run" not in html
    # The other two sub-service tabs are reachable from the
    # scoped tab bar.
    other_services = (
        {"remote-access", "web-block", "walled-garden"}
        - {slug}
    )
    for other in other_services:
        assert (
            f"/admin/radius/mt/{rid}/network-policies/{other}/"
        ) in html


def test_scoped_list_shows_only_this_router_policies(
    app, client, monkeypatch,
):
    rid_a = _seed_router(app, name="rtr-A")
    rid_b = _seed_router(app, name="rtr-B")
    _login_super(client, monkeypatch)
    # Seed one policy per router via the repo directly.
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as ra,
        )
        ra.create(tenant_id=1, router_id=rid_a,
                   name="policy-on-A",
                   allow_winbox=True,
                   source_address_list="ops",
                   expires_at="2027-01-01T00:00:00Z")
        ra.create(tenant_id=1, router_id=rid_b,
                   name="policy-on-B",
                   allow_winbox=True,
                   source_address_list="ops",
                   expires_at="2027-01-01T00:00:00Z")
    # Scoped to A → sees policy-on-A only.
    r = client.get(
        f"/admin/radius/mt/{rid_a}/network-policies/"
        "remote-access/"
    )
    html = r.data.decode("utf-8")
    assert "policy-on-A" in html
    assert "policy-on-B" not in html
    # Scoped to B → opposite.
    r = client.get(
        f"/admin/radius/mt/{rid_b}/network-policies/"
        "remote-access/"
    )
    html = r.data.decode("utf-8")
    assert "policy-on-B" in html
    assert "policy-on-A" not in html


def test_scoped_list_new_button_carries_router_id(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    r = client.get(
        f"/admin/radius/mt/{rid}/network-policies/"
        "remote-access/"
    )
    html = r.data.decode("utf-8")
    # The "سياسة جديدة" link pre-fills router_id via query.
    assert f"?router_id={rid}" in html


def test_new_policy_form_preselects_router_from_query(
    app, client, monkeypatch,
):
    rid = _seed_router(app, name="preset-target")
    _login_super(client, monkeypatch)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/new"
        f"?router_id={rid}"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # The matching option carries the `selected` attribute.
    import re as _re
    m = _re.search(
        r'<option value="' + str(rid)
        + r'"[^>]*selected[^>]*>',
        html,
    )
    assert m is not None
    # The hint surfaces too so the operator knows why.
    assert "مُحدَّد مسبقاً" in html


# ─── Router-picker landing ──────────────────────────────────


def test_router_picker_lists_seeded_routers_with_counts(
    app, client, monkeypatch,
):
    rid_a = _seed_router(app, name="picker-A",
                          address="10.0.1.1")
    rid_b = _seed_router(app, name="picker-B",
                          address="10.0.1.2")
    _login_super(client, monkeypatch)
    # One policy on A, none on B.
    with app.app_context():
        from app.radius.db.repos import (
            npc_walled_garden_repo as wg,
        )
        wg.create_policy(
            tenant_id=1, router_id=rid_a, name="g-A",
        )
    r = client.get("/admin/radius/network-policy/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # Both routers appear.
    assert "picker-A" in html
    assert "picker-B" in html
    # Cards link to per-router landing.
    assert (
        f"/admin/radius/mt/{rid_a}/network-policies/" in html
    )
    # Counts surfaced in the unified table count badge.
    assert re.search(r'class="npc-rp-count"[^>]*>\s*<i[^>]*></i>\s*1\s*</span>', html)
    # Empty-router gets the muted-pill style.
    assert "data-zero=\"1\"" in html


def test_router_picker_empty_state_when_no_routers(
    app, client, monkeypatch,
):
    _login_super(client, monkeypatch)
    r = client.get("/admin/radius/network-policy/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "لا توجد راوترات في هذا المستأجر بعد" in html


# ─── Existing global routes still work ──────────────────────


def test_global_per_service_list_routes_still_work(
    app, client, monkeypatch,
):
    """Sidebar surfacing changed; the per-service global routes
    still exist (the API and any deep links still resolve)."""
    _login_super(client, monkeypatch)
    for slug in ("remote-access", "web-block", "walled-garden"):
        r = client.get(
            f"/admin/radius/network-policy/{slug}/"
        )
        assert r.status_code == 200, slug


# ─── JSON API surface untouched ─────────────────────────────


def test_json_api_unchanged_after_ui_move(
    app, client, monkeypatch,
):
    """The brief explicitly required keeping /api/v1/network-policy/*
    as-is. Quick smoke: the list endpoint still 401s without a
    token and 200s with one."""
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "smoke-token")
    # Re-create the app so the new env var takes effect.
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    tmp = tempfile.mkdtemp(prefix="hr_npc_api_smoke_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    from app import create_app
    fresh_app = create_app()
    c = fresh_app.test_client()

    r = c.get("/api/v1/network-policy/web-block/policies")
    assert r.status_code == 401
    r = c.get(
        "/api/v1/network-policy/web-block/policies",
        headers={"Authorization": "Bearer smoke-token"},
    )
    assert r.status_code == 200
