"""NPC Safe-Execution Phase 1 — Operator Readiness UI.

Validates the new readiness card on the preview page, the
disabled "apply" placeholder, and that no live-apply route
exists yet."""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_se1_")
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
    sa = SimpleNamespace(id=1, username="alice",
                         is_super_admin=True)

    class _Store:
        @staticmethod
        def get_admin(_):
            return sa

    class _Svc:
        _store = _Store()
        def permissions_of(self, _):
            return ()

    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "alice"
        s["tenant_id"] = 1


def _csrf(client):
    client.get(
        "/admin/radius/network-policy/remote-access/new"
    )
    with client.session_transaction() as s:
        return s.get("_csrf_token") or ""


def _seed_router(app):
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
                "VALUES (1,'rt','rt','10.0.0.1','','mikrotik',"
                "'router',0,'',1812,1813,3799,8728,'admin',"
                "'pw',0,'','',0,'',1,0,22,'','{}',"
                "'2026-01-01','2026-01-01')",
            )
            return int(cur.lastrowid)


def _good_remote_policy(client, csrf, rid):
    client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={"_csrf_token": csrf,
              "name": "good", "router_id": str(rid),
              "allow_winbox": "on",
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "enabled": "on"},
        follow_redirects=False,
    )
    with client.application.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
        )
        return r.list_for_tenant(1)[-1]["id"]


def _bad_remote_policy(client, csrf, rid):
    """A policy with no source list AND no expiry — assess
    returns a blocker → impact CRITICAL → readiness fails."""
    client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={"_csrf_token": csrf,
              "name": "bad", "router_id": str(rid),
              "allow_winbox": "on",
              "enabled": "on"},
        follow_redirects=False,
    )
    with client.application.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
        )
        return r.list_for_tenant(1)[-1]["id"]


# ─── Section presence ────────────────────────────────────────


def test_readiness_section_renders_on_preview(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'data-test="npc-section-readiness"' in html
    assert "جاهزيّة التنفيذ" in html
    # Checklist surfaces with at least the rollback condition.
    assert 'data-test="npc-readiness-checklist"' in html
    assert "rollback" in html


def test_ready_state_for_well_formed_policy(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-readiness-ready"' in html
    assert "جاهزة" in html
    # No blocker block.
    assert 'data-test="npc-readiness-blockers"' not in html


def test_not_ready_state_for_invalid_policy(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _bad_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # Either blocked because no source+no expiry (planner
    # blocker bubbles into impact.risk=critical → readiness
    # blocker) OR risk pill = critical.
    assert 'data-test="npc-readiness-not-ready"' in html
    assert 'data-test="npc-readiness-blockers"' in html


# ─── Disabled apply placeholder ──────────────────────────────


def test_disabled_apply_placeholder_visible(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # Placeholder button exists with disabled attribute + caveat.
    assert 'data-test="npc-future-apply-placeholder"' in html
    assert "التنفيذ غير مفعّل بعد" in html
    # The placeholder element carries the `disabled` HTML
    # attribute (kept on its own button element).
    import re
    m = re.search(
        r"<button[^>]*data-test=['\"]npc-future-apply-placeholder['\"][^>]*>",
        html,
    )
    assert m is not None
    assert "disabled" in m.group(0)


# ─── No live-apply route exists yet ──────────────────────────


def test_no_apply_route_exists_yet_phase_1(app):
    """Phase 1 is UI-polish only. There must be NO route
    matching `/apply` under /network-policy/ in the URL map."""
    with app.app_context():
        urls = [str(rule)
                for rule in app.url_map.iter_rules()
                if "/network-policy/" in str(rule)]
    for u in urls:
        assert "/apply" not in u, (
            f"Phase 1 must not introduce any apply route; "
            f"found {u}"
        )


def test_no_apply_url_anywhere_in_preview_html(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert "/apply" not in html


# ─── Dry-run labels still present ────────────────────────────


def test_dry_run_labels_remain_visible_on_preview(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert "معاينة فقط (Dry-Run)" in html
    assert "لم يتم التطبيق على الراوتر" in html


# ─── Script viewer still secondary ───────────────────────────


def test_script_section_appears_after_readiness(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    pid = _good_remote_policy(client, csrf, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    rdy_pos    = html.find('data-test="npc-section-readiness"')
    script_pos = html.find('data-test="npc-section-script"')
    assert -1 < rdy_pos < script_pos
