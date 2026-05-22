"""Q1 — Programming plan generator (read-only).

Two layers:

  - Service: `mt_programming.HotspotProgrammingSpec.validate()` +
    `plan_hotspot()` — pure functions, no Flask, no router.
  - Route: GET shows the form, POST renders the plan. Apply is
    intentionally not a route in Q1; the apply button on the page
    must render disabled.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_q1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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
    u = f"q1_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="q1-pass", full_name="Q1 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "q1-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    """Mint a CSRF token by visiting any GET in the radius app."""
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'q1-rtr', '203.0.113.14', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


# ─── Service-layer validation ──────────────────────────────────


def test_validate_minimal_spec_uses_sensible_defaults():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs",
    )
    v = spec.validate()
    assert str(v.gateway) == "192.168.10.1"
    # Default pool skips first 9 hosts; for /24 that's .10 → .254.
    assert str(v.pool_start) == "192.168.10.10"
    assert str(v.pool_end)   == "192.168.10.254"
    assert v.dns_servers == ["8.8.8.8", "1.1.1.1"]


def test_validate_rejects_invalid_cidr():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="not-a-cidr",
        hotspot_name="hs",
    )
    with pytest.raises(ValueError):
        spec.validate()


def test_validate_rejects_gateway_outside_cidr():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs", gateway="10.0.0.1",
    )
    with pytest.raises(ValueError):
        spec.validate()


def test_validate_rejects_gateway_inside_pool():
    """RouterOS will happily run with the gateway inside the
    address-pool, but it eventually hands the gateway address to
    a client and the network goes sideways. Catch the foot-gun
    at plan time."""
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs",
        gateway="192.168.10.50",
        pool_start="192.168.10.10",
        pool_end="192.168.10.100",
    )
    with pytest.raises(ValueError):
        spec.validate()


def test_validate_rejects_dangerous_interface_names():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    for bad in (";reboot", "ether2 reboot", "$INJECT", "../etc"):
        spec = HotspotProgrammingSpec(
            interface=bad, cidr="192.168.10.0/24",
            hotspot_name="hs",
        )
        with pytest.raises(ValueError):
            spec.validate()


def test_validate_rejects_unsafe_lease_and_rate_strings():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec,
    )
    bad_lease = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs", lease_time="forever",
    )
    with pytest.raises(ValueError):
        bad_lease.validate()
    bad_rate = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs", rate_limit="fast/slow",
    )
    with pytest.raises(ValueError):
        bad_rate.validate()


# ─── Script generation ─────────────────────────────────────────


def test_script_includes_hoberadius_comment_on_every_command():
    """Every emitted command MUST carry comment=hoberadius:hs so
    Q4 unprogram can find it. This is the Q1↔Q4 contract."""
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, render_hotspot_script, HOTSPOT_COMMENT,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs", rate_limit="10M/10M",
    )
    script = render_hotspot_script(spec.validate())
    # The comment is the contract — every `add` line must end with it.
    for line in script.splitlines():
        if line.strip().startswith("/ip ") and " add " in line:
            assert HOTSPOT_COMMENT in line, (
                f"missing hoberadius comment on: {line!r}"
            )


def test_script_uses_validated_inputs_verbatim():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, render_hotspot_script,
    )
    spec = HotspotProgrammingSpec(
        interface="ether3", cidr="10.20.30.0/24",
        hotspot_name="guest",
    )
    s = render_hotspot_script(spec.validate())
    assert "interface=ether3" in s
    assert "address=10.20.30.1/24" in s
    assert "name=guest " in s        # /ip hotspot add name=guest …
    assert "name=guest-pool" in s
    assert "name=guest-prof" in s


def test_script_does_not_include_unsafe_chars():
    """Defence-in-depth: even though the validator whitelists the
    inputs, the generated script must not contain shell-meta or
    quote chars that could break /import."""
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, render_hotspot_script,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs",
    )
    s = render_hotspot_script(spec.validate())
    for ch in ("`", "$(", "\"", "\\\""):
        assert ch not in s, f"unsafe char in script: {ch!r}"


# ─── Conflict detection ────────────────────────────────────────


def test_plan_warns_on_existing_address_on_same_interface():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, plan_hotspot,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs",
    )
    addrs = [{"address": "10.0.0.1/24", "interface": "ether2"}]
    ifaces = [{"name": "ether2", "type": "ether", "disabled": "false"}]
    plan = plan_hotspot(
        {}, spec, existing_addresses=addrs,
        existing_interfaces=ifaces,
    )
    assert any("بالفعل" in w for w in plan.warnings)


def test_plan_risks_on_overlapping_network():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, plan_hotspot,
    )
    spec = HotspotProgrammingSpec(
        interface="ether2", cidr="192.168.10.0/24",
        hotspot_name="hs",
    )
    addrs = [{"address": "192.168.10.5/24", "interface": "ether3"}]
    ifaces = [{"name": "ether2", "type": "ether"}]
    plan = plan_hotspot(
        {}, spec, existing_addresses=addrs,
        existing_interfaces=ifaces,
    )
    assert any("يتداخل" in r for r in plan.risks)


def test_plan_risks_when_interface_does_not_exist():
    from app.radius.services.mt_programming import (
        HotspotProgrammingSpec, plan_hotspot,
    )
    spec = HotspotProgrammingSpec(
        interface="ether99", cidr="192.168.10.0/24",
        hotspot_name="hs",
    )
    plan = plan_hotspot(
        {}, spec, existing_addresses=[],
        existing_interfaces=[{"name": "ether1"}],
    )
    assert any("لم نجد الواجهة" in r for r in plan.risks)


# ─── Routes ────────────────────────────────────────────────────


def test_program_form_is_login_guarded(client):
    res = client.get("/admin/radius/mt/1/program",
                     follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_program_form_returns_404_for_unknown_router(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/program")
    assert res.status_code == 404


def test_program_form_renders_shell(app, client):
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/program")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-form" in html
    assert "data-mt-program-interface" in html
    assert "data-mt-program-cidr" in html
    assert "data-mt-program-name" in html
    # No plan block on the GET form.
    assert "data-mt-program-plan-card" not in html


def test_program_plan_renders_script_with_disabled_apply(app, client, monkeypatch):
    """POST with a valid spec renders the script + an apply button
    that's disabled in Q1. Q2 will flip it on with a confirmation
    modal."""
    _seed(app, nas_id=1)
    _login(client)
    # Don't actually hit the router during this UI test.
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult
    monkeypatch.setattr(
        mac, "interface_list",
        lambda nas: MtResult(ok=True, data=[
            {"name": "ether2", "type": "ether", "disabled": "false"},
        ]),
    )
    monkeypatch.setattr(
        mac, "ip_addresses",
        lambda nas: MtResult(ok=True, data=[]),
    )
    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/program/plan", data={
        "_csrf_token": token,
        "interface": "ether2",
        "cidr": "192.168.10.0/24",
        "hotspot_name": "hs",
        "dns_servers": "8.8.8.8,1.1.1.1",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-program-plan-card" in html
    assert "data-mt-program-script" in html
    assert "data-mt-program-apply" in html
    # Apply must be disabled in Q1 — operator can't accidentally fire it.
    apply_idx = html.index("data-mt-program-apply")
    button_open = html.rfind("<button", 0, apply_idx)
    button_block = html[button_open:apply_idx + 80]
    assert "disabled" in button_block, (
        "the Q1 apply button must render with `disabled` set"
    )
    # The generated script is rendered in the page.
    assert "/ip pool add name=hs-pool" in html
    assert "hoberadius:hs" in html


def test_program_plan_surfaces_validation_error(app, client):
    _seed(app, nas_id=1)
    _login(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/1/program/plan", data={
        "_csrf_token": token,
        "interface": "ether2",
        "cidr": "not-a-cidr",
        "hotspot_name": "hs",
    })
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "CIDR غير صالح" in html
    # And the plan block must not render.
    assert "data-mt-program-plan-card" not in html
