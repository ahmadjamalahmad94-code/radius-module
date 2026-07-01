"""Granular per-manager SECTION access — the owner-configured 3-state
(open / locked / hidden), server-enforced (Stage 1).

Contract (services/manager_grants + routes/blueprint._perm_guard):

  • hidden  → the section vanishes from the sidebar AND every endpoint in it
              returns 403 for a non-super manager (any HTTP method).
  • locked  → the section stays visible and GET works (view-only), but any
              mutating request (POST/PUT/PATCH/DELETE) returns 403, and the
              primary «add» control is hidden in the UI.
  • open / unconfigured → no change (role RBAC still governs — non-regressive).
  • owner / super (primary owner) always bypasses.

Auth/fixture pattern mirrors test_distributor_scoping_rbac.py. We grant the
manager the PRE-EXISTING nav/RBAC perms in the session so the assertions
exercise OUR section gate, not the older route guard (false-pass guard —
see [[rbac-route-guard-parity]]).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "manager_grants_sections.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ──────────────────────────────────────────────────────────────
def _mk_admin(username: str, *, is_super: bool = False) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"A {username}",
        is_super_admin=is_super,
    )
    return int(adm.id)


# Broad nav/RBAC perms so the PRE-EXISTING guard never fires — the section
# gate is what the assertions actually exercise.
_PERMS = [
    "users.view", "users.create", "users.delete", "users.change_status",
    "users.edit", "users.extend", "cards.view", "cards.generate",
    "cards.edit_batch", "plans.view", "plans.create", "reports.view",
    "reports.finance", "nas.view",
]


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = list(_PERMS)


def _set_sections(admin_id: int, mapping: dict[str, str]) -> None:
    from app.radius.services import manager_grants as mg

    mg.set_section_access(admin_id, mapping, tenant_id=1)


# ═══ 1. registry + defaults ════════════════════════════════════════════════
def test_default_state_is_open_and_non_regressive(app):
    from app.radius.services import manager_grants as mg

    assert mg.DEFAULT_SECTION_STATE == mg.OPEN
    with app.app_context():
        mgr = _mk_admin("mgr_def")
        # unconfigured manager: every section resolves to open
        for sec in mg.section_names():
            assert mg.section_state(mgr, sec, tenant_id=1) == mg.OPEN


def test_expected_sections_present(app):
    from app.radius.services import manager_grants as mg

    for sec in ("subscribers", "cards", "plans", "distributors",
                "network", "reports", "finance"):
        assert sec in mg.MANAGER_SECTION_REGISTRY


def test_migration_added_grant_columns(app):
    with app.app_context():
        cols = [r[1] for r in db().execute(
            "PRAGMA table_info(manager_distributor_policies)").fetchall()]
    for c in ("section_access_json", "action_grants_json", "field_grants_json"):
        assert c in cols


# ═══ 2. persistence via the service + the policy route ══════════════════════
def test_set_section_access_persists(app):
    from app.radius.services import manager_grants as mg

    with app.app_context():
        mgr = _mk_admin("mgr_persist")
        mg.set_section_access(mgr, {"subscribers": "hidden", "cards": "locked",
                                    "plans": "open"}, tenant_id=1)
        assert mg.section_state(mgr, "subscribers", tenant_id=1) == mg.HIDDEN
        assert mg.section_state(mgr, "cards", tenant_id=1) == mg.LOCKED
        # open is the default => stored as absence, still reads open
        assert mg.section_state(mgr, "plans", tenant_id=1) == mg.OPEN


def test_policy_route_persists_section_access(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)  # min-id owner
        mgr = _mk_admin("mgr_route")
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = client.post(
            f"/admin/radius/business-operators/manager/{mgr}/policy",
            data={"_csrf_token": "off-csrf",
                  "section_subscribers": "hidden",
                  "section_cards": "locked"},
        )
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services import manager_grants as mg

        assert mg.section_state(mgr, "subscribers", tenant_id=1) == mg.HIDDEN
        assert mg.section_state(mgr, "cards", tenant_id=1) == mg.LOCKED


# ═══ 3. HIDDEN: 403 on every method + not in sidebar ════════════════════════
def test_hidden_section_blocks_get_by_url(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_hidden")
        _set_sections(mgr, {"subscribers": "hidden"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        assert client.get("/admin/radius/users").status_code == 403


def test_hidden_section_blocks_post_by_url(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_hidden_post")
        _set_sections(mgr, {"subscribers": "hidden"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users",
                          data={"_csrf_token": "off-csrf", "username": "z1",
                                "password": "p1234567"})
        assert res.status_code == 403


def test_hidden_section_absent_from_sidebar(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_sidebar")
        _set_sections(mgr, {"subscribers": "hidden"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        # a neutral page that renders the sidebar (dashboard is un-gated)
        html = client.get("/admin/radius/").get_data(as_text=True)
    # the whole sidebar section wrapper (header + all its items) is removed
    assert 'data-hb-section="subscribers"' not in html


def test_non_hidden_section_present_in_sidebar(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_sidebar_ok")
        # only cards hidden; subscribers stays visible
        _set_sections(mgr, {"cards": "hidden"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        html = client.get("/admin/radius/").get_data(as_text=True)
    assert 'data-hb-section="subscribers"' in html


# ═══ 4. LOCKED: GET 200 (view) but writes 403 + control hidden ══════════════
def test_locked_section_allows_get(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_locked_get")
        _set_sections(mgr, {"subscribers": "locked"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        assert client.get("/admin/radius/users").status_code == 200


def test_locked_section_blocks_create(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_locked_create")
        _set_sections(mgr, {"subscribers": "locked"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = client.post("/admin/radius/users",
                          data={"_csrf_token": "off-csrf", "username": "z2",
                                "password": "p1234567"})
        assert res.status_code == 403


def test_locked_section_blocks_delete_and_toggle(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_locked_del")
        _set_sections(mgr, {"subscribers": "locked"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        assert client.post("/admin/radius/users/anyone/delete",
                           data={"_csrf_token": "off-csrf"}).status_code == 403
        assert client.post("/admin/radius/users/anyone/toggle",
                           data={"_csrf_token": "off-csrf"}).status_code == 403


def test_locked_section_hides_add_control(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_locked_ui")
        _set_sections(mgr, {"subscribers": "locked"})
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        html = client.get("/admin/radius/users").get_data(as_text=True)
    assert 'data-testid="users-add-btn"' not in html


def test_open_section_shows_add_control(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_open_ui")  # unconfigured => open
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        html = client.get("/admin/radius/users").get_data(as_text=True)
    assert 'data-testid="users-add-btn"' in html


# ═══ 5. owner / super always bypasses ═══════════════════════════════════════
def test_super_unaffected_by_manager_hidden(app):
    with app.app_context():
        owner = _mk_admin("owner_root", is_super=True)
        # even if a hidden policy row somehow existed for the owner id, super
        # bypasses the gate entirely.
        _set_sections(owner, {"subscribers": "hidden"})
    with app.test_client() as client:
        _login(client, admin_id=owner, is_super=True)
        assert client.get("/admin/radius/users").status_code == 200
        res = client.post("/admin/radius/users",
                          data={"_csrf_token": "off-csrf", "username": "ok1",
                                "password": "p1234567"})
        # not blocked by the section gate (create succeeds → redirect)
        assert res.status_code in (302, 303)


# ═══ 6. unconfigured manager is non-regressive (open) ═══════════════════════
def test_unconfigured_manager_can_view(app):
    with app.app_context():
        _mk_admin("owner_root", is_super=True)
        mgr = _mk_admin("mgr_unconf")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        assert client.get("/admin/radius/users").status_code == 200
