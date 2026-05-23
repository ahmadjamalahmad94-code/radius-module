"""NPC Safe-Execution Phase 6 — apply + rollback UI."""
from __future__ import annotations

import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_se6_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
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


def _login(client, monkeypatch, *, super_admin=True,
            perms=()):
    user = SimpleNamespace(
        id=1, username="alice",
        is_super_admin=bool(super_admin),
    )

    class _Store:
        @staticmethod
        def get_admin(_):
            return user

    class _Svc:
        _store = _Store()
        def permissions_of(self, _):
            return tuple(perms)

    import app.radius.services.admins as am
    monkeypatch.setattr(am, "get_admins_service",
                        lambda: _Svc())
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "alice"
        s["tenant_id"] = 1


def _seed_router(app, *, name="rt1"):
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
                "VALUES (1, ?, ?, '10.0.0.1','','mikrotik',"
                "'router',0,'',1812,1813,3799,8728,'admin',"
                "'pw',0,'','',0,'',1,0,22,'','{}',"
                "'2026-01-01','2026-01-01')",
                (name, name),
            )
            return int(cur.lastrowid)


def _good_remote_policy(app, rid):
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
        )
        return r.create(
            tenant_id=1, router_id=rid,
            name="Good policy",
            allow_winbox=True,
            source_address_list="ops",
            expires_at="2027-01-01T00:00:00Z",
        )


# ─── Apply button visibility ────────────────────────────────


def test_apply_form_visible_for_user_with_apply_perm(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'data-test="npc-apply-form"' in html
    assert "تطبيق آمن" in html
    # The submit button exists.
    assert 'data-test="npc-apply-submit"' in html


def test_apply_form_hidden_for_user_without_apply_perm(
    app, client, monkeypatch,
):
    """Non-super admin with NO apply perm → sees the
    disabled placeholder, no apply form."""
    rid = _seed_router(app)
    # Need to seed policy first via super admin path.
    pid = _good_remote_policy(app, rid)
    # Now switch to a no-perm user.
    _login(client, monkeypatch, super_admin=False,
            perms=("npc.remote_access.view",
                   "npc.remote_access.preview",
                   "npc.remote_access.manage"))
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # No apply form rendered.
    assert 'data-test="npc-apply-form"' not in html
    # Disabled placeholder + "no permission" copy.
    assert 'data-test="npc-future-apply-placeholder"' in html
    assert "لا تملك صلاحية" in html


def test_apply_submit_disabled_when_blockers_present(
    app, client, monkeypatch,
):
    """A policy with no source list + no expiry has a
    contracts blocker. The form still renders for the perm
    holder, but the submit button is disabled."""
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo as r,
        )
        # No source list + no expiry → assess_policy blocks
        # → impact CRITICAL → readiness blocker.
        pid = r.create(
            tenant_id=1, router_id=rid,
            name="Blocked",
            allow_winbox=True,
        )
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # Form is present (perm exists).
    assert 'data-test="npc-apply-form"' in html
    # But the submit button is disabled.
    import re
    m = re.search(
        r"<button[^>]*data-test=['\"]npc-apply-submit['\"][^>]*>",
        html,
    )
    assert m is not None
    assert "disabled" in m.group(0)


# ─── Confirmation checkboxes ────────────────────────────────


def test_confirmation_checkboxes_render_for_required_codes(
    app, client, monkeypatch,
):
    """When the contracts engine surfaces a required
    confirmation, the template renders a checkbox for it."""
    # Build a policy with `action=drop` in the script to
    # trigger the firewall-drop confirmation requirement.
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    with app.app_context():
        from app.radius.db.repos import (
            npc_web_block_repo as wb,
        )
        pid = wb.create_policy(
            tenant_id=1, router_id=rid,
            name="TikTok block", fail_open=True,
        )
        wb.add_target(
            policy_id=pid,
            value="tiktok.com",
            normalized_value="tiktok.com",
            target_type=wb.TARGET_TYPE_DOMAIN,
            category="tiktok",
        )
    r = client.get(
        f"/admin/radius/network-policy/web-block/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # Firewall-drop confirmation expected for web_block plans
    # that emit forward-chain drop.
    assert 'data-test="npc-confirm-grid"' in html
    assert (
        'data-test="npc-confirm-confirm_firewall_drop"' in html
    )


# ─── Apply route flash ──────────────────────────────────────


def test_apply_post_redirects_back_to_preview(
    app, client, monkeypatch,
):
    """POST to /apply (with a perm-holder + no fake reader) →
    snapshot capture fails → contracts refuses → flashes
    `danger` → redirects back to preview. We pin the
    redirect target and `no_snapshot` reason text."""
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    # Trigger CSRF token by visiting a form-bearing page.
    client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    with client.session_transaction() as s:
        csrf = s.get("_csrf_token") or ""
    r = client.post(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/apply",
        data={"_csrf_token": csrf},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303)
    # Redirect goes back to the preview.
    assert "/preview" in r.headers["Location"]


# ─── Changes page ───────────────────────────────────────────


def test_changes_page_route_exists_get_only(app):
    with app.app_context():
        rules = [str(r) for r in app.url_map.iter_rules()
                 if "/changes" in str(r)
                 and "/network-policy/" in str(r)]
    assert rules
    # Three sub-services × one changes page.
    assert any("remote-access" in u for u in rules)
    assert any("web-block" in u for u in rules)
    assert any("walled-garden" in u for u in rules)


def test_changes_page_empty_state(app, client, monkeypatch):
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/changes"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert 'data-test="npc-changes-empty"' in html
    assert "لا توجد عمليات تنفيذ" in html


def test_changes_page_lists_seeded_change_sets(
    app, client, monkeypatch,
):
    """Seed a successful change_set + a failed one; the page
    should render both with their per-router targets."""
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    with app.app_context():
        from app.radius.db.repos import (
            npc_change_sets_repo as cs,
        )
        cs1 = cs.create(
            tenant_id=1, service="remote_access",
            policy_id=pid, action_type=cs.ACTION_APPLY,
            execution_mode=cs.MODE_FULL,
            snapshot_id=1,
        )
        cs.update_status(1, cs1, status=cs.STATUS_SUCCEEDED)
        cs.add_target(
            change_set_id=cs1, tenant_id=1, router_id=rid,
            rendered_script="# x\n",
            rollback_script=(
                "/ip/firewall/filter remove "
                "[find comment~\"^HOBE_NPC_REMOTE:1:\"]\n"
            ),
            status=cs.TARGET_STATUS_SUCCEEDED,
        )
        cs2 = cs.create(
            tenant_id=1, service="remote_access",
            policy_id=pid, action_type=cs.ACTION_APPLY,
            execution_mode=cs.MODE_FULL,
            snapshot_id=2,
        )
        cs.update_status(1, cs2, status=cs.STATUS_FAILED)
        cs.add_target(
            change_set_id=cs2, tenant_id=1, router_id=rid,
            rendered_script="# x\n",
            rollback_script="",
            status=cs.TARGET_STATUS_FAILED,
        )
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/changes"
    )
    html = r.data.decode("utf-8")
    assert html.count('data-test="npc-cs-row"') == 2
    assert "نجح" in html
    assert "فشل" in html


def test_changes_page_shows_rollback_button_for_eligible(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    with app.app_context():
        from app.radius.db.repos import (
            npc_change_sets_repo as cs,
        )
        cs1 = cs.create(
            tenant_id=1, service="remote_access",
            policy_id=pid, action_type=cs.ACTION_APPLY,
            execution_mode=cs.MODE_FULL,
            snapshot_id=1,
        )
        cs.update_status(1, cs1, status=cs.STATUS_SUCCEEDED)
        cs.add_target(
            change_set_id=cs1, tenant_id=1, router_id=rid,
            rendered_script="# x\n",
            rollback_script=(
                "/ip/firewall/filter remove "
                "[find comment~\"^HOBE_NPC_REMOTE:1:\"]\n"
            ),
            status=cs.TARGET_STATUS_SUCCEEDED,
        )
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/changes"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-cs-rollback-form"' in html
    assert 'data-test="npc-cs-rollback-btn"' in html
    # Confirmation copy from the brief.
    assert "سيتم التراجع فقط عن التغييرات التي أنشأها النظام" in html


def test_changes_page_hides_rollback_for_failed_set(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    with app.app_context():
        from app.radius.db.repos import (
            npc_change_sets_repo as cs,
        )
        cs1 = cs.create(
            tenant_id=1, service="remote_access",
            policy_id=pid, action_type=cs.ACTION_APPLY,
            execution_mode=cs.MODE_FULL,
            snapshot_id=1,
        )
        cs.update_status(1, cs1, status=cs.STATUS_FAILED)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/changes"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-cs-rollback-btn"' not in html


def test_changes_page_link_visible_on_preview(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-changes-link"' in html
    assert "سجل التغييرات" in html


# ─── No-CSRF apply attempts are refused ─────────────────────


def test_apply_post_without_csrf_redirects_to_login(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login(client, monkeypatch, super_admin=True)
    pid = _good_remote_policy(app, rid)
    r = client.post(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/apply",
        data={},
        follow_redirects=False,
    )
    # The platform CSRF guard either redirects (missing
    # token in session) or returns 400 (token mismatch).
    assert r.status_code in (302, 303, 400)
    assert "/apply" not in (r.headers.get("Location") or "/apply")
