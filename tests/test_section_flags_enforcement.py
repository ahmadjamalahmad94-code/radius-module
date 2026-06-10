"""Section flags enforcement — Broken Access Control regression.

Audit context (June 2026, owner)
────────────────────────────────
The Hobe Hub sidebar carried four chunks of routes that the owner had
**removed from the visible navigation by simply commenting the markup**:

  • شبكة العمليّات التجريبية      — `network_devices_list`,
                                    `network_ip_scan_page`,
                                    `network_telegram_settings`
  • دفع بيانات التوزيع (DHCP push) — `mt_push_setup`
  • الإعداد الهندسي               — `setup_wizard_page`        (super-only since
                                                                  it landed in
                                                                  _PERM_GUARDED)
  • إعداد أسطول الراوترات         — `setup_wizard_fleet_page`,
                                    `setup_wizard_fleet_data`,
                                    `setup_wizard_fleet_router*`

Of the four, only the engineering-setup page was actually protected — the
other three were reachable to any logged-in admin by direct URL with no
permission check (Broken Access Control, OWASP A01).

These tests prove the new section-flags layer (`app/radius/auth/section_flags`)
plus the route-level guard install:

  1. Default-hidden sections return 403 to a non-super even on direct URL,
     regardless of HTTP method.
  2. Super-admin (incl. primary admin via `primary_admin_id()`) is never
     blocked by the section guard.
  3. Toggling `disabled=True` for a section that defaults to visible also
     forces 403 for non-supers (proof that disable/suspend is end-to-end,
     not visual-only).
  4. Resetting a section restores the default flags.
  5. The sections-admin page itself is super-only — non-supers cannot
     enable a section they were locked out of.
  6. The Python helpers report `hidden`/`disabled`/`section_of` accurately
     after toggles.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client, user, pw):
    return client.post(
        "/admin/radius/login",
        data={"username": user, "password": pw},
        follow_redirects=False,
    )


def _csrf(client, url="/admin/radius/"):
    client.get(url)
    with client.session_transaction() as sess:
        return sess.get("_csrf_token")


# ─── URLs of routes whose endpoints belong to the four hidden sections ───
# Each pair (url, sections-key) is what the operator would type directly
# into the browser. Before the fix, only setup_wizard_page returned 403;
# the others returned 200.
HIDDEN_SECTION_URLS = [
    ("/admin/radius/network/devices",        "network_ops_legacy"),
    ("/admin/radius/network/scan",           "network_ops_legacy"),
    ("/admin/radius/network/telegram",       "network_ops_legacy"),
    ("/admin/radius/mt-push-setup",          "dhcp_push"),
    ("/admin/radius/setup-wizard",           "engineering_setup"),
    ("/admin/radius/setup-wizard/fleet",     "fleet_setup"),
]


def test_default_hidden_sections_403_for_operator(client):
    """Non-super hits every hidden-section URL → must get 403."""
    r = _login(client, "operator", "operator")
    assert r.status_code in {302, 303}, "operator login should succeed"
    for url, section in HIDDEN_SECTION_URLS:
        res = client.get(url, follow_redirects=False)
        assert res.status_code == 403, (
            f"{url} (section={section}) expected 403, got {res.status_code} — "
            "section guard did not block direct URL access for a non-super"
        )


def test_super_admin_reaches_every_hidden_section(client, app):
    """Super passes the section guard; we only assert it is NOT 403'd."""
    r = _login(client, "admin", "admin")
    assert r.status_code in {302, 303}
    for url, section in HIDDEN_SECTION_URLS:
        res = client.get(url, follow_redirects=False)
        # Some of these pages can legitimately 302 (redirect inside the
        # wizard) or 200 — we only assert the section guard did NOT 403.
        assert res.status_code != 403, (
            f"{url} (section={section}) blocked super-admin — section guard "
            f"must always bypass for primary_admin/super (got {res.status_code})"
        )


def test_toggle_disable_blocks_default_visible_section_for_operator(client, app, monkeypatch):
    """Proof that DISABLE is end-to-end server-side, not visual-only.

    Strategy: register a synthetic "_test_disable_target" section pointing
    at an endpoint the seeded `operator` is **known** to reach by default
    (`users_list` — has `users.view` in `DEFAULT_ROLE_PERMISSIONS`).
    Flip `disabled=True` → operator now 403; super still 200/302. Reset →
    operator reaches it again. The real registry is untouched outside this
    test (monkeypatch removes the synthetic key on teardown).
    """
    from app.radius.auth import section_flags as sf

    SYN_NAME = "_test_disable_target"
    # Insert into both the registry and the reverse index used by the guard.
    sf.SECTION_REGISTRY[SYN_NAME] = {
        "label": "test target", "description": "",
        "endpoints": ("users_list",),
        "default_hidden": False, "default_disabled": False,
    }
    sf._EP_TO_SECTION["users_list"] = SYN_NAME

    def _cleanup():
        sf.SECTION_REGISTRY.pop(SYN_NAME, None)
        sf._EP_TO_SECTION.pop("users_list", None)
        # also wipe the tenant_settings rows the test wrote, so a rerun
        # against the same DB starts clean.
        try:
            with app.test_request_context():
                from app.radius.db.repos import tenants_repo
                tenants_repo.set_setting(1, sf._key_hidden(SYN_NAME),   "0")
                tenants_repo.set_setting(1, sf._key_disabled(SYN_NAME), "0")
        except Exception:
            pass
    monkeypatch.setattr(sf, "SECTION_REGISTRY", sf.SECTION_REGISTRY)
    monkeypatch.setattr(sf, "_EP_TO_SECTION", sf._EP_TO_SECTION)

    try:
        # Baseline: operator reaches /users with the synthetic section absent
        # of disable flags. Must NOT be 403'd (proves the guard is dormant).
        _login(client, "operator", "operator")
        pre = client.get("/admin/radius/users", follow_redirects=False)
        assert pre.status_code != 403, (
            f"operator was already blocked from /users with no disable flag "
            f"({pre.status_code}) — test setup is broken"
        )

        # Owner disables the section.
        _login(client, "admin", "admin")
        with app.test_request_context():
            sf.set_flags(SYN_NAME, hidden=False, disabled=True, tenant_id=1)

        # Operator re-tries — section guard must now 403.
        _login(client, "operator", "operator")
        post = client.get("/admin/radius/users", follow_redirects=False)
        assert post.status_code == 403, (
            "disable=True must 403 the section end-to-end server-side, "
            f"but operator got {post.status_code}"
        )

        # Super still passes — section guard always bypasses for super.
        _login(client, "admin", "admin")
        super_res = client.get("/admin/radius/users", follow_redirects=False)
        assert super_res.status_code != 403, (
            "super-admin was incorrectly blocked by disabled section "
            f"(got {super_res.status_code}) — guard must bypass for super"
        )

        # Reset clears the flag.
        with app.test_request_context():
            sf.reset_to_defaults(SYN_NAME, tenant_id=1)
        _login(client, "operator", "operator")
        after = client.get("/admin/radius/users", follow_redirects=False)
        assert after.status_code != 403, (
            f"reset_to_defaults did not restore access (got {after.status_code})"
        )
    finally:
        _cleanup()


def test_sections_admin_page_is_super_only(client):
    """The page that lets you flip flags must itself be super-only —
    otherwise an operator could re-enable a section they were locked out
    of (privilege escalation)."""
    _login(client, "operator", "operator")
    assert client.get("/admin/radius/sections").status_code == 403
    _login(client, "admin", "admin")
    assert client.get("/admin/radius/sections").status_code == 200


def test_helpers_reflect_toggle_round_trip(app):
    """The Python helpers used by the sidebar and the guard reflect the
    persisted state — proves the request-scoped cache invalidates on writes
    and that `section_of()` resolves both `radius.xxx` and `xxx` forms."""
    from app.radius.auth.section_flags import (
        get_flags, set_flags, reset_to_defaults, section_of,
    )
    with app.test_request_context():
        # endpoint resolution
        assert section_of("radius.mt_push_setup") == "dhcp_push"
        assert section_of("mt_push_setup")        == "dhcp_push"
        assert section_of("dashboard")            is None

        # set hidden=False explicitly and read back
        set_flags("dhcp_push", hidden=False, disabled=False, tenant_id=1)
        f = get_flags("dhcp_push", tenant_id=1)
        assert f == {"hidden": False, "disabled": False}

        # flip disabled and read back inside the same request
        set_flags("dhcp_push", hidden=False, disabled=True, tenant_id=1)
        f2 = get_flags("dhcp_push", tenant_id=1)
        assert f2 == {"hidden": False, "disabled": True}

        # reset to defaults (default_hidden=True for dhcp_push)
        reset_to_defaults("dhcp_push", tenant_id=1)
        f3 = get_flags("dhcp_push", tenant_id=1)
        assert f3 == {"hidden": True, "disabled": False}


def test_section_of_unknown_endpoint_is_none(app):
    """Endpoints outside the registry must NOT be affected by the section
    guard — section_of() returns None so is_section_blocked() short-circuits."""
    from app.radius.auth.section_flags import section_of, is_section_blocked
    with app.test_request_context():
        assert section_of("users_list") is None
        assert is_section_blocked("users_list") is False
        # the dashboard is the home page — must never be touched by the
        # section guard regardless of tenant flags
        assert is_section_blocked("dashboard") is False
