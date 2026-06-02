"""VX2.4 — Site-exit UI + preview flow (route tests)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_vx2_4_")
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


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"vx2_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="vx2-pass", full_name="VX2",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "vx2-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    """Bootstrap a `_csrf_token` in the test client's session
    (same pattern Q2 apply tests use)."""
    client.get("/admin/radius/")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _post(client, url: str, data: dict, **kwargs):
    """POST helper that injects `_csrf_token` so it survives
    the project's global CSRF check."""
    data = dict(data or {})
    data.setdefault("_csrf_token", _csrf(client))
    return client.post(url, data=data, **kwargs)


def _seed_nas(app, *, nas_id=1, enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret,
                     vendor, nas_type, enabled, created_at,
                     connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik',
                           'hotspot', ?, ?, 'direct')""",
                (nas_id, f"vx2-rtr-{nas_id}",
                 f"203.0.113.{nas_id}",
                 1 if enabled else 0, now),
            )


def _seed_node(app, name="vps-test"):
    with app.app_context():
        from app.radius.db.repos import vps_exit_nodes_repo as v
        return v.create(
            tenant_id=1, name=name,
            public_ip="203.0.113.99",
            wireguard_interface_name="wg-vps",
            wireguard_gateway_ip="10.10.0.1",
            enabled=True,
        )


def _seed_policy(app, *, nas_id=1, node_id=None):
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
        )
        if node_id is None:
            node_id = _seed_node(app)
        return p.create(
            tenant_id=1, router_id=nas_id,
            exit_node_id=node_id,
            name="speedtest-policy",
            fail_mode="block_when_vps_down",
            include_subdomains=False,
        )


# ─── Auth ───────────────────────────────────────────────────


def test_site_exit_page_login_guarded(client):
    res = client.get("/admin/radius/mt/1/site-exit",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_site_exit_page_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/9999/site-exit")
    assert res.status_code == 404


# ─── Page render ────────────────────────────────────────────


def test_site_exit_page_renders_for_valid_router(app, client):
    """Empty-state render — no policy yet, the page still works
    and shows the policy form."""
    _seed_nas(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/site-exit")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-site-exit" in html
    assert 'data-mt-site-exit-nas="1"' in html
    assert "توجيه مواقع عبر خادم الربط" in html
    # No policy yet → the create-policy form is visible.
    assert "data-mt-site-exit-policy-form" in html


def test_site_exit_apply_button_disabled_without_preview(app, client):
    """When a policy exists but no preview has been generated
    yet, the apply card renders with the button DISABLED. The
    operator must run preview first."""
    _seed_nas(app, nas_id=12)
    _seed_policy(app, nas_id=12)
    _login(client)
    html = client.get(
        "/admin/radius/mt/12/site-exit").get_data(as_text=True)
    assert "data-mt-site-exit-apply-button" in html
    # Find the apply-button element (multi-line attribute list)
    # and confirm `disabled` is one of its attributes (because
    # no preview_plan was generated).
    import re
    btn = re.search(
        r"<button[^>]*data-mt-site-exit-apply-button[^>]*>",
        html, re.DOTALL,
    )
    assert btn, "apply button tag not found"
    assert "disabled" in btn.group(0).lower()
    # Form is NOT yet rendered (no preview).
    assert "data-mt-site-exit-apply-form" not in html
    # Operator-facing reason is in human Arabic, not raw Jinja.
    assert "apply_disabled_reason" not in html


def test_site_exit_apply_form_renders_after_successful_preview(
    app, client,
):
    """Once a preview lands successfully, the apply form is
    rendered with all 5 confirmation checkboxes."""
    _seed_nas(app, nas_id=13)
    nid = _seed_node(app, name="vps-form")
    pid = _seed_policy(app, nas_id=13, node_id=nid)
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        t.add(policy_id=pid, value="speedtest.net",
               normalized_value="speedtest.net",
               target_type="domain",
               group_name="speedtest_measurement")
    _login(client)
    html = _post(
        client,
        f"/admin/radius/mt/13/site-exit/policies/{pid}/preview",
        {"wan_interface_list": "WAN"},
    ).get_data(as_text=True)
    # Form + all 5 confirmation checkboxes present.
    assert "data-mt-site-exit-apply-form" in html
    for name in (
        "confirm_preview_seen",
        "confirm_backup_status",
        "confirm_vps_exit_understood",
        "confirm_fail_mode_understood",
        "confirm_selected_sites_only",
    ):
        assert f'name="{name}"' in html
    # Submit button is NOT disabled when preview is ready.
    import re
    btn = re.search(
        r"<button[^>]*data-mt-site-exit-apply-button[^>]*>",
        html, re.DOTALL,
    )
    assert btn, "apply button tag not found after preview"
    assert "disabled" not in btn.group(0).lower()


def test_site_exit_page_lists_vps_nodes(app, client):
    _seed_nas(app, nas_id=2)
    nid = _seed_node(app, name="vps-A")
    _login(client)
    html = client.get(
        "/admin/radius/mt/2/site-exit").get_data(as_text=True)
    assert "vps-A" in html
    assert f'data-mt-site-exit-node="{nid}"' in html


def test_site_exit_page_renders_no_nodes_empty_state(app, client):
    _seed_nas(app, nas_id=3)
    _login(client)
    html = client.get(
        "/admin/radius/mt/3/site-exit").get_data(as_text=True)
    assert "data-mt-site-exit-no-nodes" in html


# ─── Policy create ──────────────────────────────────────────


def test_policy_create_rejects_missing_name(app, client):
    _seed_nas(app, nas_id=4)
    _seed_node(app, name="vps-b")
    _login(client)
    res = _post(
        client,
        "/admin/radius/mt/4/site-exit/policies",
        {"name": "", "exit_node_id": "1",
          "fail_mode": "block_when_vps_down"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    # flash-and-redirect — page itself doesn't 500.
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
        )
        assert p.list_for_router(1, 4) == []


def test_policy_create_succeeds_and_redirects(app, client):
    _seed_nas(app, nas_id=5)
    nid = _seed_node(app, name="vps-c")
    _login(client)
    res = _post(
        client,
        "/admin/radius/mt/5/site-exit/policies",
        {"name": "policy A",
          "exit_node_id": str(nid),
          "fail_mode": "fallback_to_wan",
          "include_subdomains": "on"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert "/site-exit?policy_id=" in res.headers["Location"]
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_policies_repo as p,
        )
        policies = p.list_for_router(1, 5)
    assert len(policies) == 1
    assert policies[0]["name"] == "policy A"
    assert policies[0]["fail_mode"] == "fallback_to_wan"


# ─── Seed import ────────────────────────────────────────────


def test_seed_import_shows_summary_in_html(app, client):
    _seed_nas(app, nas_id=6)
    pid = _seed_policy(app, nas_id=6)
    _login(client)
    seed = (
        "/ip firewall address-list\n"
        "add address=speedtest.net list=speedtest\n"
        "add address=whatismyip.com list=speedtest\n"
        "add address=0.0.0.0/0 list=bad\n"  # invalid
        "add address=speedtest.net list=speedtest\n"  # dup
    )
    res = _post(
        client,
        f"/admin/radius/mt/6/site-exit/policies/{pid}/import",
        {"seed_text": seed},
    )
    html = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "data-mt-site-exit-import-summary" in html
    # Summary KPIs are present in the rendered output.
    assert ">4<" in html  # total parsed
    assert "speedtest_measurement" in html
    assert "public_ip_checkers" in html
    # An invalid entry's reason surfaces in the details panel.
    assert "0.0.0.0/0" in html


def test_seed_import_does_not_persist_targets_yet(app, client):
    _seed_nas(app, nas_id=7)
    pid = _seed_policy(app, nas_id=7)
    _login(client)
    seed = "add address=speedtest.net list=x\n"
    _post(
        client,
        f"/admin/radius/mt/7/site-exit/policies/{pid}/import",
        {"seed_text": seed},
    )
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        # Import is preview-only — nothing was written yet.
        assert t.list_for_policy(pid) == []


def test_dangerous_groups_unchecked_in_default_render(app, client):
    """default_enabled=False for vpn_provider_pages /
    general_probe_sites / manual_review. The checkbox renders
    without `checked`."""
    _seed_nas(app, nas_id=8)
    pid = _seed_policy(app, nas_id=8)
    _login(client)
    seed = (
        "add address=speedtest.net list=safe\n"
        "add address=expressvpn.com list=v\n"
        "add address=google.com list=v\n"
    )
    html = _post(
        client,
        f"/admin/radius/mt/8/site-exit/policies/{pid}/import",
        {"seed_text": seed},
    ).get_data(as_text=True)
    # Find each <input ... value="<group>"> element across the
    # multi-line attribute layout, and check the `checked`
    # attribute presence per group.
    import re
    def _input_for(group: str) -> str:
        m = re.search(
            r'<input[^>]*value="' + re.escape(group) + r'"[^>]*>',
            html, re.DOTALL,
        )
        return (m.group(0) if m else "").lower()
    vpn_input    = _input_for("vpn_provider_pages")
    google_input = _input_for("general_probe_sites")
    speed_input  = _input_for("speedtest_measurement")
    assert vpn_input,    "vpn_provider_pages input not found"
    assert google_input, "general_probe_sites input not found"
    assert speed_input,  "speedtest_measurement input not found"
    # Risky groups default to unchecked.
    assert "checked" not in vpn_input
    assert "checked" not in google_input
    # Safe group with count > 0 defaults to checked.
    assert "checked" in speed_input


# ─── Targets persist + preview ──────────────────────────────


def test_targets_save_only_enabled_groups(app, client):
    _seed_nas(app, nas_id=9)
    pid = _seed_policy(app, nas_id=9)
    _login(client)
    seed = (
        "add address=speedtest.net list=x\n"
        "add address=whatismyip.com list=x\n"
        "add address=expressvpn.com list=x\n"
        "add address=google.com list=x\n"
    )
    res = _post(
        client,
        f"/admin/radius/mt/9/site-exit/policies/{pid}/targets",
        {
            "seed_text": seed,
            "enabled_groups": [
                "speedtest_measurement",
                "public_ip_checkers",
                # vpn_provider_pages NOT selected
            ],
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        rows = t.list_for_policy(pid)
    normalized = {r["normalized_value"] for r in rows}
    assert "speedtest.net" in normalized
    assert "whatismyip.com" in normalized
    assert "expressvpn.com" not in normalized
    assert "google.com" not in normalized


def test_preview_renders_forward_and_rollback_scripts(app, client):
    _seed_nas(app, nas_id=10)
    nid = _seed_node(app, name="vps-prev")
    pid = _seed_policy(app, nas_id=10, node_id=nid)
    _login(client)
    # add a target so the planner produces a real plan
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        t.add(policy_id=pid, value="speedtest.net",
               normalized_value="speedtest.net",
               target_type="domain",
               group_name="speedtest_measurement")
    res = _post(
        client,
        f"/admin/radius/mt/10/site-exit/policies/{pid}/preview",
        {"wan_interface_list": "WAN"},
    )
    html = res.get_data(as_text=True)
    assert "data-mt-site-exit-forward-script" in html
    assert "data-mt-site-exit-rollback-script" in html
    # Critical safety wording — both forward and rollback are
    # the exact-prefix anchored form from VX2.3a.
    assert "^HOBE_VX2_SITE_EXIT:" in html
    # FastTrack advisory must be visible.
    assert "FastTrack" in html


def test_preview_does_not_invoke_any_router_client(app, client,
                                                     monkeypatch):
    """The preview path must NEVER call the live router. We
    short-circuit MikrotikClient.connect to detect a call —
    any invocation = test failure."""
    _seed_nas(app, nas_id=11)
    nid = _seed_node(app, name="vps-noinvoke")
    pid = _seed_policy(app, nas_id=11, node_id=nid)
    with app.app_context():
        from app.radius.db.repos import (
            site_exit_targets_repo as t,
        )
        t.add(policy_id=pid, value="fast.com",
               normalized_value="fast.com",
               target_type="domain",
               group_name="speedtest_measurement")

    called = {"count": 0}
    try:
        from app.radius.integration.mikrotik import client as mkc

        def boom(*a, **kw):
            called["count"] += 1
            raise AssertionError(
                "preview must not connect to the router")
        monkeypatch.setattr(mkc.MikrotikClient, "connect", boom)
    except (ImportError, AttributeError):
        pass

    _login(client)
    res = _post(
        client,
        f"/admin/radius/mt/11/site-exit/policies/{pid}/preview",
        {"wan_interface_list": "WAN"},
    )
    assert res.status_code == 200
    assert called["count"] == 0


def test_preview_for_unknown_router_returns_404(app, client):
    _login(client)
    res = _post(
        client,
        "/admin/radius/mt/9999/site-exit/policies/1/preview",
        {},
    )
    assert res.status_code == 404


def test_preview_for_policy_of_other_router_returns_404(app, client):
    """A policy created on router A cannot be operated on under
    router B's URL — the route refuses cross-router calls."""
    _seed_nas(app, nas_id=20)
    _seed_nas(app, nas_id=21)
    pid = _seed_policy(app, nas_id=20)
    _login(client)
    res = _post(
        client,
        f"/admin/radius/mt/21/site-exit/policies/{pid}/preview",
        {},
    )
    assert res.status_code == 404
