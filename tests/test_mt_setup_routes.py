"""L3 — Setup-wizard backend tests.

Exercise the three routes through Flask's test client:

- GET  /admin/radius/mt/setup           form shell renders
- POST /admin/radius/mt/setup           generates credentials,
                                        writes nas_devices row,
                                        backfills ros_version +
                                        provisioned_at, redirects
                                        to /script
- GET  /admin/radius/mt/<id>/script     renders the RouterOS script
                                        with every credential inlined
"""
from __future__ import annotations

import os
import sys
import tempfile
from urllib.parse import urlparse
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    tmp = tempfile.mkdtemp(prefix="hr_l3_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # Phase M — point the WG peer manager at a tmp peers dir so
    # the wizard's v7 path runs end-to-end without touching the
    # host's real /etc/hoberadius/wg-peers.d.
    peers = tmp_path / "wg-peers.d"
    peers.mkdir()
    monkeypatch.setenv("HOBERADIUS_WG_PEERS_DIR", str(peers))
    monkeypatch.setenv("HOBERADIUS_WG_SUBNET", "10.10.0.0/24")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY",
                        "TestServerPubKey00000000000000000000000000A=")
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_ENDPOINT",
                        "203.0.113.10:51820")
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
    username = f"wiz_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=username, password="wiz-pass",
        full_name="Wizard Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "wiz-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client, url: str = "/admin/radius/mt/setup") -> str:
    """Fetch a GET page to mint the CSRF token, then return its value.

    Mirrors the pattern in tests/test_web_backups_ui.py — every
    POST/PUT/DELETE to a non-/api/ route is gated by this token.
    """
    res = client.get(url)
    assert res.status_code == 200
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


# ─── Route registration ──────────────────────────────────────────


def test_routes_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.mt_setup_form" in rules
    assert "radius.mt_setup_create" in rules
    assert "radius.mt_setup_script" in rules


# ─── Login guard ─────────────────────────────────────────────────


def test_wizard_form_login_guarded(client):
    res = client.get("/admin/radius/mt/setup", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


# ─── Form render ─────────────────────────────────────────────────


def test_form_renders_with_versions_and_default_ip(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/setup")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Both versions present as radio options.
    assert 'value="6"' in html
    assert 'value="7"' in html
    # Form posts to itself.
    assert 'action="/admin/radius/mt/setup"' in html
    # Required fields exist.
    assert 'name="name"' in html
    assert 'name="ros_version"' in html
    assert 'name="server_ip"' in html
    # The manual router-address field was removed — the address is auto-
    # assigned by the tunnel flow, never typed.
    assert 'name="address"' not in html
    # v6 offers ONLY the two tunnel strategies — no "direct" option anywhere.
    assert 'value="sstp_mgmt"' in html
    assert 'value="pptp_mgmt"' in html
    assert 'value="direct"' not in html
    assert "اتصال مباشر" not in html


# ─── POST creates row + redirects to script ──────────────────────


def test_post_creates_row_and_redirects(app, client):
    """Original L3 happy-path test, adjusted for M2: v7 wizard now
    ignores any operator-typed address and uses the WG-allocated
    /32 instead (10.10.0.2 = first free in the tmp subnet)."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={
            "_csrf_token": token,
            "name": "MT-Wiz-Test",
            "address": "10.50.0.1",      # ignored for v7 — WG IP wins
            "ros_version": "7",
            "server_ip": "203.0.113.99",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    loc = res.headers.get("Location", "")
    assert "/script" in loc
    assert "server_ip=203.0.113.99" in loc

    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name, address, ros_version, provisioned_at, "
            "       api_user, api_password, secret, connection_mode "
            "FROM nas_devices WHERE name = 'MT-Wiz-Test'"
        ).fetchone()
    assert row is not None
    # M2: address comes from the WG allocator, NOT the form.
    assert row["address"] == "10.10.0.2"
    assert row["connection_mode"] == "vpn"
    assert row["ros_version"] == "7"
    assert row["provisioned_at"]
    assert row["api_user"].startswith("hr-")
    assert len(row["api_password"]) == 32
    assert len(row["secret"]) == 32


@pytest.mark.parametrize("missing", ["name", "ros_version"])
def test_post_rejects_missing_required(app, client, missing):
    # M2: `address` is no longer required for v7 (WG auto-allocates).
    # The v6 case lives in test_v6_wizard_rejects_missing_address.
    _login(client)
    token = _csrf(client)
    data = {
        "_csrf_token": token,
        "name": "x", "address": "10.0.0.1",
        "ros_version": "7", "server_ip": "1.1.1.1",
    }
    data[missing] = ""
    res = client.post("/admin/radius/mt/setup", data=data,
                       follow_redirects=False)
    # Redirects back to the form with a flashed error — never falls
    # through to "row created with empty field".
    assert res.status_code in {302, 303}
    assert "/mt/setup" in res.headers.get("Location", "")


def test_post_rejects_invalid_ros_version(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token,
              "name": "x", "address": "1.1.1.1",
              "ros_version": "9", "server_ip": "2.2.2.2"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert "/mt/setup" in res.headers.get("Location", "")


# ─── Script page ─────────────────────────────────────────────────


def _create_via_wizard(client, name="MT-Script-Test"):
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token,
              "name": name, "address": "10.20.30.40",
              "ros_version": "7", "server_ip": "198.51.100.20"},
        follow_redirects=False,
    )
    loc = res.headers["Location"]
    path = urlparse(loc).path
    # path = /admin/radius/mt/<id>/script  →  parts[4] is <id>
    parts = [p for p in path.split("/") if p]
    nas_id = int(parts[3])
    return nas_id, loc


def test_script_page_renders_with_credentials_inlined(app, client):
    import html as _html
    _login(client)
    nas_id, loc = _create_via_wizard(client)
    res = client.get(loc)
    assert res.status_code == 200
    body = _html.unescape(res.get_data(as_text=True))

    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT api_user, api_password, secret FROM nas_devices "
            "WHERE id = ?", (nas_id,)
        ).fetchone()

    # Every credential appears verbatim in the rendered script.
    assert row["api_user"] in body
    assert row["api_password"] in body
    assert row["secret"] in body
    # M2: for the v7 + VPN flow, the RADIUS block targets the
    # server's WG-side IP (10.10.0.1 in the test config). The
    # form's server_ip field is unused on this path — the WG
    # endpoint comes from HOBERADIUS_WG_SERVER_ENDPOINT.
    assert "/radius add address=10.10.0.1" in body
    assert "/user add" in body
    # The WG block carries the public endpoint from env.
    assert "203.0.113.10" in body
    assert "endpoint-port=51820" in body
    # M3 — the script locks the API service to the WG subnet so
    # newly-provisioned routers don't inherit a stale address
    # restriction from earlier experiments.
    assert "/ip service set api address=10.10.0.0/24" in body


def test_script_page_404_for_unknown_nas(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/99999/script")
    assert res.status_code == 404


# ─── L5: Operations Center ───────────────────────────────────────


def test_operations_route_registered(app):
    rules = {r.endpoint for r in app.url_map.iter_rules()}
    assert "radius.mt_operations" in rules


def test_operations_login_guarded(client):
    res = client.get("/admin/radius/mt/operations", follow_redirects=False)
    assert res.status_code in {302, 303}
    assert "/admin/radius/login" in res.headers.get("Location", "")


def test_operations_empty_state(app, client):
    _login(client)
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # Header + CTA is always there.
    assert "غرفة عمليات الراوترات" in html
    assert url_safe(url_for_setup := "/admin/radius/mt/setup") in html
    # Empty-state copy when no NAS rows.
    assert "لا توجد راوترات" in html


def test_operations_lists_wizard_provisioned_router(app, client):
    _login(client)
    # Create a row via the wizard (v7 → WG-allocated 10.10.0.2).
    _, _ = _create_via_wizard(client, name="MT-Ops-Wiz")
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "MT-Ops-Wiz" in html
    # M2: the row's address is now the WG-allocated tunnel IP,
    # NOT the (ignored) operator-typed 10.20.30.40.
    assert "10.10.0.2" in html
    assert "معالَج آليًّا" in html              # provisioned_at pill
    assert "إصدار 7.x" in html


def test_operations_sequential_numbering(app, client):
    """Add three routers — display numbers must be 1, 2, 3 even if
    the DB ids are 1, 2, 3 (or non-contiguous later after deletes)."""
    _login(client)
    for nm in ("R-a", "R-b", "R-c"):
        _create_via_wizard(client, name=nm)
    res = client.get("/admin/radius/mt/operations")
    html = res.get_data(as_text=True)
    # All three names show up.
    for nm in ("R-a", "R-b", "R-c"):
        assert nm in html
    # The first <td class="num"> values are 1, 2, 3 in order.
    import re
    nums = re.findall(r'<td class="num">(\d+)</td>', html)
    assert nums[:3] == ["1", "2", "3"]


# ─── M2: Wizard auto-provisions WG for v7 ────────────────────────


def test_v7_wizard_provisions_wg_peer_and_marks_nas_vpn(app, client, monkeypatch):
    """Submitting the wizard with ros_version=7 must:
    • allocate a WG IP (10.10.0.2 — first free after server .1)
    • write a peers.d/*.conf file
    • set the NAS row's connection_mode='vpn' + vpn_peer_address
    • carry the router's private key into the session for the
      one-shot render on the script page."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={
            "_csrf_token": token,
            "name": "MT-WG-One",
            "address": "",            # left blank on purpose for v7
            "ros_version": "7",
            "server_ip": "203.0.113.10",
        },
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}

    # 1) Peer file exists in the tmp peers.d.
    peers_dir = os.environ["HOBERADIUS_WG_PEERS_DIR"]
    peer_path = os.path.join(peers_dir, "MT-WG-One.conf")
    assert os.path.isfile(peer_path), \
        f"wizard should have written {peer_path}"
    body = open(peer_path, encoding="utf-8").read()
    assert "[Peer]" in body
    assert "PublicKey =" in body
    assert "AllowedIPs = 10.10.0.2/32" in body

    # 2) NAS row reflects VPN mode + the WG IP.
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT address, connection_mode, vpn_peer_address, "
            "       vpn_public_key, ros_version "
            "FROM nas_devices WHERE name = ?", ("MT-WG-One",),
        ).fetchone()
    assert row is not None
    assert row["address"] == "10.10.0.2"
    assert row["connection_mode"] == "vpn"
    assert row["vpn_peer_address"] == "10.10.0.2"
    assert len(row["vpn_public_key"]) == 44   # base64-encoded x25519 pubkey
    assert row["ros_version"] == "7"

    # M4 — the wizard also writes into the FreeRADIUS `nas` table
    # so the router can actually RADIUS-authenticate via the
    # tunnel IP.
    with app.app_context():
        from app.radius.db.connection import db
        nas_row = db().execute(
            "SELECT nasname, shortname, type, secret FROM nas "
            "WHERE nasname = ?", ("10.10.0.2",),
        ).fetchone()
    assert nas_row is not None, "nas-table row not synced"
    assert nas_row["type"] == "mikrotik"
    # secret matches the nas_devices row (= the RADIUS shared key
    # baked into the RouterOS script the operator pasted).
    assert len(nas_row["secret"]) == 32


def test_v7_script_page_includes_wg_block_with_private_key(app, client):
    """The first GET of the script page (right after POST) must
    render the WG setup commands with the router's private key
    inlined. A second GET (refresh) must NOT show the key —
    private keys are issued exactly once."""
    import html as _html
    import re
    _login(client)
    token = _csrf(client)
    post_res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token, "name": "MT-WG-Two",
              "address": "", "ros_version": "7",
              "server_ip": "203.0.113.10"},
        follow_redirects=False,
    )
    loc = post_res.headers["Location"]

    first = client.get(loc)
    assert first.status_code == 200
    # Jinja autoescapes the rendered script body inside <pre>, so
    # `"`  appears as `&#34;`. Unescape before searching.
    body = _html.unescape(first.get_data(as_text=True))

    # WG block present — now FULLY IDEMPOTENT: it wipes peers/address before
    # re-adding and only creates the interface if missing (preserves the key on
    # re-paste). This is the fix for duplicate-peer routing breaks.
    assert ':if ([:len [/interface wireguard find name="hr-wg"]]=0) do={' in body
    assert '/interface/wireguard/peers remove [find interface="hr-wg"]' in body
    assert '/ip address remove [find interface="hr-wg"]' in body
    assert "/interface/wireguard/peers add" in body
    assert "203.0.113.10" in body
    assert "endpoint-port=51820" in body
    # Router private key (44-char base64) is issued ONCE — a single unique
    # key value. (The reusable code-card renders a visible line-numbered view
    # plus a hidden verbatim node for byte-exact copy, so the same one key may
    # appear in more than one DOM node; what matters is it's a single value
    # here and gone on refresh, asserted below.)
    matches = re.findall(r'private-key="([A-Za-z0-9+/=]{44})"', body)
    assert len(set(matches)) == 1, body[:600]
    # Server pubkey appears too.
    assert "TestServerPubKey00000000000000000000000000A=" in body
    # RADIUS block uses the server's tunnel IP (10.10.0.1), NOT
    # the public address.
    assert "/radius add address=10.10.0.1" in body

    # Refresh: private key gone, warning banner present.
    second = client.get(loc)
    assert second.status_code == 200
    html2 = _html.unescape(second.get_data(as_text=True))
    assert "تم إصداره مرة واحدة" in html2
    # No private-key line on refresh.
    matches2 = re.findall(r'private-key="([A-Za-z0-9+/=]{44})"', html2)
    assert len(matches2) == 0


def test_v6_wizard_uses_tunnel_with_auto_address(app, client):
    """v6 has no WG — it now ALWAYS goes through an SSTP/PPTP management
    tunnel. No manual address is posted; the tunnel auto-assigns a stable
    IP that becomes the row's address (connection_mode='vpn'), and the
    script carries the SSTP block, NOT a WG block."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token, "name": "MT-v6-Plain",
              "ros_version": "6", "server_ip": "203.0.113.10"},  # no address
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}

    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT address, connection_mode, vpn_peer_address, "
            "management_tunnel_type FROM nas_devices WHERE name = ?",
            ("MT-v6-Plain",),
        ).fetchone()
    assert row["management_tunnel_type"] == "sstp_mgmt"   # default tunnel
    assert row["connection_mode"] == "vpn"
    assert row["address"] and row["address"].startswith("10.50.")  # auto IP
    assert row["vpn_peer_address"] == row["address"]      # CoA target == IP

    # The wizard lands on the legacy /script which now REDIRECTS a v6 row to the
    # authoritative onboarding generator (single source of truth) — follow it.
    loc = res.headers["Location"]
    page = client.get(loc, follow_redirects=True).get_data(as_text=True)
    assert "sstp-client" in page              # tunnel block present (authoritative)
    # no WG *block* for v6 (the page chrome may have a WireGuard nav link, so we
    # check for the WG interface command, not the bare word).
    assert "/interface wireguard add" not in page


def test_v6_wizard_works_without_any_address(app, client):
    """With the manual address field gone, a bare v6 POST (only name +
    version) still succeeds — the tunnel supplies the address."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token, "name": "MT-v6-Bare", "ros_version": "6"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert "/mt/" in res.headers.get("Location", "")     # forward to script
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT address, management_tunnel_type FROM nas_devices "
            "WHERE name = ?", ("MT-v6-Bare",),
        ).fetchone()
    assert row is not None and row["address"]            # row created + has IP
    assert row["management_tunnel_type"] == "sstp_mgmt"


def test_v7_wizard_handles_missing_wg_env_gracefully(app, client, monkeypatch):
    """If the operator deploys without setting HOBERADIUS_WG_SERVER_PUBKEY,
    the wizard must error cleanly instead of writing a broken row."""
    _login(client)
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_PUBKEY", "")
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/setup",
        data={"_csrf_token": token, "name": "MT-NoWG",
              "address": "", "ros_version": "7",
              "server_ip": "203.0.113.10"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    assert "/mt/setup" in res.headers.get("Location", "")
    with app.app_context():
        from app.radius.db.connection import db
        assert db().execute(
            "SELECT 1 FROM nas_devices WHERE name = ?", ("MT-NoWG",),
        ).fetchone() is None


def test_operations_table_has_live_counter_markers(app, client):
    """O2 — each router row must carry the data-* markers the
    JS poll loop binds to: data-mt-row-counters,
    data-mt-router-id, data-mt-row-status, data-mt-row-hotspot,
    data-mt-row-ppp, data-mt-row-rx, data-mt-row-tx."""
    _login(client)
    _create_via_wizard(client, name="MT-O2-Live")
    res = client.get("/admin/radius/mt/operations")
    assert res.status_code == 200
    html = res.get_data(as_text=True)

    # Table-level config attributes for the JS bootstrap.
    assert "data-mt-ops-table" in html
    assert 'data-mt-api-base="/api/v1"' in html
    assert "data-mt-api-token=" in html

    # Per-row counter markers (rendered once per router).
    assert "data-mt-row-counters" in html
    assert "data-mt-router-id" in html
    assert "data-mt-row-status" in html
    assert "data-mt-row-hotspot" in html
    assert "data-mt-row-ppp" in html
    assert "data-mt-row-rx" in html
    assert "data-mt-row-tx" in html

    # The JS file itself is referenced (defer-loaded).
    assert 'src="/static/js/mt_operations.js"' in html


def test_operations_excludes_soft_deleted(app, client):
    """A NAS row with deleted_at set must NOT show up in the list."""
    _login(client)
    # Seed a non-deleted + a deleted row.
    with app.app_context():
        from app.radius.db.connection import transaction
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices (id, tenant_id, name, address,
                    secret, vendor, nas_type, enabled, created_at)
                   VALUES (501, 1, 'live-rtr', '1.1.1.1', 's', 'mikrotik',
                           'hotspot', 1, ?)""",
                (now,),
            )
            c.execute(
                """INSERT INTO nas_devices (id, tenant_id, name, address,
                    secret, vendor, nas_type, enabled, created_at, deleted_at)
                   VALUES (502, 1, 'dead-rtr', '2.2.2.2', 's', 'mikrotik',
                           'hotspot', 1, ?, ?)""",
                (now, now),
            )
    res = client.get("/admin/radius/mt/operations")
    html = res.get_data(as_text=True)
    assert "live-rtr" in html
    assert "dead-rtr" not in html


# Tiny helper used above so the assertion read better.
def url_safe(s: str) -> str:
    return s


def test_script_page_handles_legacy_row_without_ros_version(app, client):
    """A pre-L2 nas_devices row has ros_version = '' (the default).
    The page must fall back to v7 instead of 500-ing."""
    _login(client)
    with app.app_context():
        from app.radius.db.connection import transaction
        from datetime import datetime
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, api_user, api_password,
                     created_at)
                VALUES (777, 1, 'legacy-rt', '10.0.0.7', 'sek',
                        'mikrotik', 'hotspot', 1, 'hr-legac',
                        'pw-32-chars-aaaaaaaaaaaaaaaaaaaa', ?)
                """,
                (datetime.utcnow().isoformat() + "Z",),
            )
    res = client.get("/admin/radius/mt/777/script?server_ip=1.2.3.4")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "RouterOS 7" in html        # default fallback
    assert "hr-legac" in html
