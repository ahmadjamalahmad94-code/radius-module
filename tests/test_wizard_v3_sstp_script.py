"""SSTP tunnel variant of the Setup Wizard v3 script — RouterOS 7 output shape."""
from __future__ import annotations


def _script():
    from app.radius.services.setup_wizard_v3_sstp import render_sstp_unified_script
    return render_sstp_unified_script(
        run_id=7, router_name="Test Router", tunnel_ip="10.50.0.5",
        accel_host="vpn.example.net", accel_port=443, radius_server_ip="10.50.0.1",
        tunnel_user="rtr-test-router", tunnel_password="pw1234567890abc",
        radius_secret="RadSecret1234567890", api_user="hr-api-7",
        api_password="ApiPass1234567890", short_code="ABCD")


def test_sstp_script_dials_accel_and_wires_radius():
    s = _script()
    # SSTP client dials the accel-ppp server
    assert "/interface sstp-client add" in s
    assert "vpn.example.net" in s
    assert 'user="rtr-test-router"' in s
    assert "port=443" in s
    # RADIUS: server = accel gateway, src-address = this router's tunnel IP
    assert "address=10.50.0.1" in s
    assert "src-address=10.50.0.5" in s
    assert 'secret="RadSecret1234567890"' in s
    # dedicated API user + management ACL (tunnel-only)
    assert '/user add name="hr-api-7"' in s
    assert "/ip service set" in s


def test_sstp_script_has_ntp_before_tunnel_and_no_wireguard():
    s = _script()
    assert "/system ntp client set enabled=yes" in s
    assert "$rosMajor >= 7" in s                      # version-branched
    # clock is fixed BEFORE the tunnel dials
    assert s.index("/system ntp client set") < s.index("/interface sstp-client add")
    # this is the SSTP path — no WireGuard, no key-paste round-trip
    assert "wireguard" not in s.lower()
    assert "HOBERADIUS_PUBLIC_KEY" not in s


def test_sstp_script_is_idempotent_remove_before_add():
    s = _script()
    assert "/interface sstp-client remove [find" in s
    assert s.index("/interface sstp-client remove") < s.index("/interface sstp-client add")
    assert '/radius remove [find where comment~"HOBERADIUS_SETUP:7:radius"]' in s
    assert s.index("/radius remove [find") < s.index("/radius add ")
    assert '/user remove [find where name="hr-api-7"]' in s
