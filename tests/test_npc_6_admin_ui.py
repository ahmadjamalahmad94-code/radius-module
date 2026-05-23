"""NPC Phase 6 — server-rendered admin UI.

Covers:
  * Sidebar entries rendered with the correct URLs.
  * All three sub-services have list / new / edit / preview
    routes that 200 OK.
  * Permission-gated routes refuse callers missing the
    matching `npc.<svc>.<verb>` permission.
  * Preview POST records a script_version + a `preview_generated`
    audit row.
  * No apply route exists anywhere on the new UI surface
    (greppable invariant — the brief forbids it).
  * The dry-run banner renders on every NPC page.
  * Child add / delete records target_added / target_removed
    audit rows; never apply.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from types import SimpleNamespace

import pytest


# ─── Fixture ─────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_6_")
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


def _login_super(client, app, monkeypatch):
    """Stub the admins service to return a super-admin DTO for
    admin_id=1, set the session — the simplest way to bypass
    the `_current_admin` lookup without touching the admins
    repo schema."""
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
            return ()  # super_admin path skips this anyway

    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())

    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "alice"
        s["tenant_id"] = 1


def _login_with_perms(client, app, monkeypatch, perms):
    """Stub the admins service with a non-super admin holding
    exactly `perms`. Used to assert perm-gate behaviour."""
    admin = SimpleNamespace(
        id=2, username="bob", is_super_admin=False,
    )

    class _Store:
        @staticmethod
        def get_admin(_id):
            return admin

    class _Svc:
        _store = _Store()
        def permissions_of(self, _a):
            return tuple(perms)

    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())

    with client.session_transaction() as s:
        s["admin_id"] = 2
        s["admin_user"] = "bob"
        s["tenant_id"] = 1


def _csrf(client):
    """Trigger CSRF-token generation, then read it back from
    the session.

    The platform's _inject_csrf after_request hook only seeds
    the token when the response HTML contains a `<form>`. The
    `/network-policy/.../new` form page reliably has one, so
    we visit it to prime the session token."""
    client.get(
        "/admin/radius/network-policy/remote-access/new"
    )
    with client.session_transaction() as s:
        return s.get("_csrf_token") or ""


def _seed_router(app, name="rt1"):
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
                "VALUES (1, ?, ?, '10.0.0.1', '', 'mikrotik', "
                "'router', 0, '', 1812, 1813, 3799, 8728, "
                "'admin', 'real-password', 0, '', '', 0, '', "
                "1, 0, 22, '', '{}', "
                "'2026-01-01','2026-01-01')",
                (name, name),
            )
            return int(cur.lastrowid)


# ─── Sidebar entries ─────────────────────────────────────────


def test_sidebar_renders_three_npc_entries(app, client, monkeypatch):
    _login_super(client, app, monkeypatch)
    r = client.get("/admin/radius/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # The three sub-service URLs appear in the sidebar.
    assert "/admin/radius/network-policy/remote-access/" in html
    assert "/admin/radius/network-policy/web-block/" in html
    assert "/admin/radius/network-policy/walled-garden/" in html
    # Arabic labels render.
    assert "الوصول البعيد" in html
    assert "حظر المواقع" in html
    assert "المواقع المسموحة" in html


# ─── List page ───────────────────────────────────────────────


@pytest.mark.parametrize("slug,label", [
    ("remote-access", "الوصول البعيد"),
    ("web-block",     "حظر المواقع"),
    ("walled-garden", "المواقع المسموحة"),
])
def test_list_pages_render_with_dry_run_banner(
    app, client, monkeypatch, slug, label,
):
    _login_super(client, app, monkeypatch)
    r = client.get(f"/admin/radius/network-policy/{slug}/")
    assert r.status_code == 200, r.data.decode("utf-8")[:500]
    html = r.data.decode("utf-8")
    assert "معاينة فقط (Dry-Run)" in html
    assert "لم يتم التطبيق على الراوتر" in html
    assert "الوصول البعيد" in html
    assert "حظر المواقع" in html
    assert "المواقع المسموحة" in html
    assert "لا توجد سياسات بعد" in html
    assert label in html


def test_landing_redirects_to_remote_access(
    app, client, monkeypatch,
):
    _login_super(client, app, monkeypatch)
    r = client.get(
        "/admin/radius/network-policy/",
        follow_redirects=False,
    )
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "remote-access" in r.headers["Location"]


# ─── Permission gates ────────────────────────────────────────


def test_unauth_request_redirects_to_login(app, client):
    r = client.get(
        "/admin/radius/network-policy/web-block/",
        follow_redirects=False,
    )
    assert r.status_code in (301, 302, 303, 307, 308)
    assert "/login" in r.headers.get("Location", "")


def test_admin_without_npc_perm_gets_403(
    app, client, monkeypatch,
):
    """Holds only `mikrotik.view` — none of the NPC perms.
    Every NPC list page must 403 (login redirect would mean
    the auth guard, not the permission guard, fired)."""
    _login_with_perms(
        client, app, monkeypatch, ["mikrotik.view"],
    )
    for slug in ("remote-access", "web-block", "walled-garden"):
        r = client.get(
            f"/admin/radius/network-policy/{slug}/"
        )
        assert r.status_code == 403, slug


def test_admin_with_view_only_can_read_but_not_create(
    app, client, monkeypatch,
):
    """A holder of `npc.web_block.view` lists policies fine
    but the create form must 403 — that needs `.manage`."""
    _login_with_perms(
        client, app, monkeypatch, ["npc.web_block.view"],
    )
    r = client.get(
        "/admin/radius/network-policy/web-block/"
    )
    assert r.status_code == 200
    r = client.get(
        "/admin/radius/network-policy/web-block/new"
    )
    assert r.status_code == 403


# ─── New / edit / preview flow ───────────────────────────────


def test_remote_access_full_lifecycle_via_ui(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, app, monkeypatch)
    csrf = _csrf(client)

    # New (GET form renders)
    r = client.get(
        "/admin/radius/network-policy/remote-access/new"
    )
    assert r.status_code == 200
    assert "بيانات السياسة" in r.data.decode("utf-8")

    # POST create
    r = client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={
            "_csrf_token": csrf,
            "name": "Emergency Winbox",
            "router_id": str(rid),
            "allow_winbox": "on",
            "allow_webfig_https": "on",
            "source_address_list": "ops-bastion",
            "expires_at": "2027-01-01T00:00:00Z",
            "enabled": "on",
        },
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    assert "/edit" in r.headers["Location"]

    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as ra,
        )
        rows = ra.list_for_tenant(1)
    assert len(rows) == 1
    pid = rows[0]["id"]

    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}/edit"
    )
    assert r.status_code == 200
    assert "Emergency Winbox" in r.data.decode("utf-8")

    # Preview GET
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # Intelligent-preview UI rephrased the "plan ok" pill —
    # match the noun-only form so the assertion survives copy
    # tweaks in either direction.
    assert "الخطّة سليمة" in html
    assert "HOBE_NPC_REMOTE:" in html
    assert "rollback" in html
    assert "لم يتم التطبيق على الراوتر" in html

    # Preview POST persists + audits
    r = client.post(
        f"/admin/radius/network-policy/remote-access/{pid}/preview",
        data={"_csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code == 200

    with app.app_context():
        from app.radius.db.connection import db
        actions = [r["action"] for r in db().execute(
            "SELECT action FROM audit_log "
            "WHERE target_type='npc_remote_access_policy' "
            "ORDER BY id"
        ).fetchall()]
        from app.radius.db.repos import npc_scripts_repo as ns
        latest = ns.latest_for_policy(
            service="remote_access", policy_id=pid)

    assert "npc.remote_access.policy_created" in actions
    assert "npc.remote_access.preview_generated" in actions
    assert not any(
        a in {"npc.remote_access.applied",
              "npc.remote_access.apply_attempted",
              "npc.remote_access.apply_failed"}
        for a in actions
    )
    assert latest is not None
    assert latest["script_body"].startswith(
        "# === HobeRadius Network Policy Center"
    )


def test_web_block_add_and_delete_target_via_ui(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, app, monkeypatch)
    csrf = _csrf(client)

    client.post(
        "/admin/radius/network-policy/web-block/new",
        data={"_csrf_token": csrf,
              "name": "TikTok block",
              "router_id": str(rid),
              "scope": "all_users",
              "fail_open": "on",
              "enabled": "on"},
        follow_redirects=False,
    )
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.list_policies_for_tenant(1)[0]["id"]

    # Add a valid target
    client.post(
        f"/admin/radius/network-policy/web-block/{pid}/children",
        data={"_csrf_token": csrf,
              "value": "tiktok.com",
              "category": "tiktok"},
        follow_redirects=True,
    )
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        rows = wb.list_targets(pid)
    assert len(rows) == 1
    tid = rows[0]["id"]

    # Invalid target → rejected with Arabic reason
    r = client.post(
        f"/admin/radius/network-policy/web-block/{pid}/children",
        data={"_csrf_token": csrf,
              "value": "*.tiktok.com",
              "category": "tiktok"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "النجمية" in r.data.decode("utf-8")

    # Delete via UI
    r = client.post(
        f"/admin/radius/network-policy/web-block/{pid}/"
        f"children/{tid}/delete",
        data={"_csrf_token": csrf},
        follow_redirects=True,
    )
    assert r.status_code == 200
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        assert wb.list_targets(pid) == []

    # Audit log
    with app.app_context():
        from app.radius.db.connection import db
        actions = [r["action"] for r in db().execute(
            "SELECT action FROM audit_log "
            "WHERE target_type='npc_web_block_policy' "
            "ORDER BY id"
        ).fetchall()]
    assert "npc.web_block.target_added" in actions
    assert "npc.web_block.target_removed" in actions
    assert not any(
        "applied" in a or "apply_attempted" in a
        for a in actions
    )


def test_walled_garden_preview_includes_entries(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, app, monkeypatch)
    csrf = _csrf(client)

    client.post(
        "/admin/radius/network-policy/walled-garden/new",
        data={"_csrf_token": csrf,
              "name": "Payments allowlist",
              "router_id": str(rid),
              "hotspot_profile": "hsprof1",
              "enabled": "on"},
        follow_redirects=False,
    )
    with app.app_context():
        from app.radius.db.repos import (
            npc_walled_garden_repo as wg,
        )
        pid = wg.list_policies_for_tenant(1)[0]["id"]

    client.post(
        f"/admin/radius/network-policy/walled-garden/{pid}/children",
        data={"_csrf_token": csrf,
              "value": "api.payments.test",
              "entry_type": "dst_host"},
        follow_redirects=True,
    )
    r = client.get(
        f"/admin/radius/network-policy/walled-garden/{pid}/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "api.payments.test" in html
    assert "/ip/hotspot/walled-garden add" in html


def test_preview_download_returns_rsc(app, client, monkeypatch):
    rid = _seed_router(app)
    _login_super(client, app, monkeypatch)
    csrf = _csrf(client)
    client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={"_csrf_token": csrf,
              "name": "DL", "router_id": str(rid),
              "allow_winbox": "on",
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "enabled": "on"},
        follow_redirects=False,
    )
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as ra,
        )
        pid = ra.list_for_tenant(1)[0]["id"]
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview.rsc"
    )
    assert r.status_code == 200
    assert r.mimetype.startswith("text/plain")
    assert r.headers["Content-Disposition"].startswith("attachment")
    assert b"HOBE_NPC_REMOTE:" in r.data


# ─── Apply absence pin ───────────────────────────────────────


def test_no_apply_route_exists_anywhere_on_npc_surface(app):
    """Hard invariant of Phase 6: there is no `/apply` URL
    anywhere under `/network-policy/`."""
    with app.app_context():
        urls = [str(rule)
                for rule in app.url_map.iter_rules()
                if "/network-policy/" in str(rule)]
    for u in urls:
        assert "/apply" not in u, (
            f"Phase 6 forbids any /apply route on NPC; found "
            f"{u}"
        )


def test_no_apply_button_in_form_or_preview_html(
    app, client, monkeypatch,
):
    """Defence-in-depth: an apply button must not leak into
    any NPC page."""
    rid = _seed_router(app)
    _login_super(client, app, monkeypatch)
    csrf = _csrf(client)
    client.post(
        "/admin/radius/network-policy/web-block/new",
        data={"_csrf_token": csrf,
              "name": "T", "router_id": str(rid),
              "scope": "all_users", "fail_open": "on",
              "enabled": "on"},
        follow_redirects=False,
    )
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.list_policies_for_tenant(1)[0]["id"]

    for url in (
        "/admin/radius/network-policy/web-block/",
        f"/admin/radius/network-policy/web-block/{pid}/edit",
        f"/admin/radius/network-policy/web-block/{pid}/preview",
    ):
        r = client.get(url)
        assert r.status_code == 200
        html = r.data.decode("utf-8")
        # No apply button text "تطبيق" inside a submit element.
        assert not re.search(
            r"type=['\"]submit['\"][^>]*>[^<]*تطبيق",
            html,
        ), f"apply button leaked into {url}"
        assert "/apply" not in html, (
            f"/apply URL leaked into {url}"
        )


# ─── Dry-run labelling pin ───────────────────────────────────


def test_dry_run_label_appears_on_every_npc_page(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, app, monkeypatch)
    csrf = _csrf(client)
    client.post(
        "/admin/radius/network-policy/web-block/new",
        data={"_csrf_token": csrf, "name": "DD",
              "router_id": str(rid), "scope": "all_users",
              "fail_open": "on", "enabled": "on"},
        follow_redirects=False,
    )
    with app.app_context():
        from app.radius.db.repos import npc_web_block_repo as wb
        pid = wb.list_policies_for_tenant(1)[0]["id"]
    pages = [
        "/admin/radius/network-policy/remote-access/",
        "/admin/radius/network-policy/web-block/",
        "/admin/radius/network-policy/walled-garden/",
        "/admin/radius/network-policy/remote-access/new",
        f"/admin/radius/network-policy/web-block/{pid}/edit",
        f"/admin/radius/network-policy/web-block/{pid}/preview",
    ]
    for url in pages:
        r = client.get(url)
        assert r.status_code == 200, url
        html = r.data.decode("utf-8")
        assert ("Dry-Run" in html or "معاينة فقط" in html), url
