# -*- coding: utf-8 -*-
"""Deterministic accel-ppp config generation (P3 hardening).

Locks in the invariants that the manual sed/append workflow violated on the
live VPS:
  * every section appears EXACTLY once (no duplicate [radius]/[auth]/…),
  * NO ssl-protocol / ssl-ciphers pinning (the regression that caused
    MikroTik "ssl: no common version (6)"),
  * required modules + sections present,
  * pool / gateway / port wired from panel settings,
  * output is stable (idempotent) for the same params.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import ipaddress

import pytest


def _params(pool="10.50.0.0/24", gw="10.50.0.1", port=443):
    from app.radius.services.accel_config import AccelConfigParams
    return AccelConfigParams(
        pool=ipaddress.ip_network(pool),
        gateway_ip=ipaddress.ip_address(gw),
        sstp_port=port,
        radius_server="127.0.0.1",
        radius_secret="s3cr3t",
        ssl_pemfile="/etc/accel-ppp/accel-selfsigned.pem",
    )


def test_every_section_appears_exactly_once():
    from app.radius.services.accel_config import generate_accel_conf
    conf = generate_accel_conf(_params())
    for section in ("[modules]", "[radius]", "[auth]", "[client-ip-range]",
                    "[sstp]", "[pptp]", "[ip-pool]", "[ppp]", "[log]",
                    "[shaper]", "[core]"):
        assert conf.count(section) == 1, f"{section} not unique"


def test_no_tls_pinning_regression():
    """The bad ssl-protocol=tlsv1.2 + ssl-ciphers=AES256-SHA must NEVER be
    emitted — that pinning broke MikroTik TLS negotiation."""
    from app.radius.services.accel_config import generate_accel_conf
    conf = generate_accel_conf(_params())
    for line in conf.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("ssl-protocol="), line
        assert not stripped.startswith("ssl-ciphers="), line


def test_required_modules_and_mschap_present():
    from app.radius.services.accel_config import generate_accel_conf
    conf = generate_accel_conf(_params())
    mods_block = conf.split("[modules]", 1)[1].split("[", 1)[0]
    for mod in ("log_file", "sstp", "pptp", "radius", "shaper", "ippool",
                "auth_mschap_v2"):
        assert mod in mods_block, f"module {mod} missing from [modules]"


def test_pool_gateway_and_port_wired():
    from app.radius.services.accel_config import generate_accel_conf
    conf = generate_accel_conf(_params(pool="10.77.0.0/24", gw="10.77.0.1",
                                       port=8443))
    assert "gw-ip-address=10.77.0.1" in conf
    assert "10.77.0.0/24" in conf
    assert "port=8443" in conf
    assert "ssl-pemfile=/etc/accel-ppp/accel-selfsigned.pem" in conf
    assert "dae-server=127.0.0.1:3799" in conf


def test_output_is_stable_idempotent():
    from app.radius.services.accel_config import generate_accel_conf
    a = generate_accel_conf(_params())
    b = generate_accel_conf(_params())
    assert a == b  # no timestamps / randomness → installer re-run is a no-op


def test_gateway_outside_pool_rejected():
    from app.radius.services.accel_config import generate_accel_conf
    with pytest.raises(ValueError):
        generate_accel_conf(_params(pool="10.50.0.0/24", gw="10.99.0.1"))


def test_secret_injection_scrubbed():
    """A secret with embedded newlines can't inject a fake config section."""
    from app.radius.services.accel_config import (
        AccelConfigParams, generate_accel_conf)
    p = AccelConfigParams(
        pool=ipaddress.ip_network("10.50.0.0/24"),
        gateway_ip=ipaddress.ip_address("10.50.0.1"),
        sstp_port=443, radius_server="127.0.0.1",
        radius_secret="abc\n[evil]\nx=1", ssl_pemfile="/x.pem")
    conf = generate_accel_conf(p)
    # The newline is what could inject a section; a bracket inline in a value
    # is harmless. Assert no LINE is the injected section header.
    assert "[evil]" not in [ln.strip() for ln in conf.splitlines()]


def test_openssl_cmd_is_argv_list():
    from app.radius.services.accel_config import openssl_selfsigned_cmd
    cmd = openssl_selfsigned_cmd("/etc/accel-ppp/x.pem")
    assert cmd[0] == "openssl" and "-x509" in cmd
    assert cmd[-1] == "/etc/accel-ppp/x.pem"


def test_health_checks_return_rows_without_crashing():
    """run_health_checks must degrade gracefully (no exception) and return one
    row per defined check, even off-Linux."""
    from app.radius.services.accel_config import (
        run_health_checks, HEALTH_CHECKS, AccelConfigParams)
    rows = run_health_checks(AccelConfigParams(
        pool=ipaddress.ip_network("10.50.0.0/24"),
        gateway_ip=ipaddress.ip_address("10.50.0.1"),
        sstp_port=443, radius_server="127.0.0.1",
        radius_secret="s", ssl_pemfile="/x.pem"))
    assert len(rows) == len(HEALTH_CHECKS)
    for r in rows:
        assert r["state"] in ("ok", "fail", "skipped")
        assert "id" in r


# ── host-vs-container health probes (the false-❌ fix) ─────────────────────────
#
# The panel runs inside Docker; accel-ppp runs on the HOST. Container-local
# checks (/dev/ppp, a 443 listener, the accel process) are blind to the host and
# used to report false ❌ even with SSTP up. The probes now dial the REAL public
# endpoint (accel_host:port) and, when containerised, infer host facts from it.

def _state(rows, check_id):
    return next(r["state"] for r in rows if r["id"] == check_id)


def _patch_env(monkeypatch, in_container, endpoint):
    """Stub container detection + the live endpoint probe so tests never touch
    the network or the host."""
    from app.radius.services import accel_config as ac
    monkeypatch.setattr(ac, "_in_container", lambda: in_container)
    monkeypatch.setattr(ac, "_probe_sstp_endpoint",
                        lambda host, port, timeout=ac._PROBE_TIMEOUT: endpoint)
    return ac


def test_container_with_live_endpoint_shows_green_not_red(monkeypatch):
    """Owner's case: SSTP is actually up. In the container, the live endpoint
    probe succeeds → listener/dev_ppp/accel/443/tls must all be OK, never fail."""
    from app.radius.services.accel_config import EndpointProbe
    ac = _patch_env(monkeypatch, in_container=True,
                    endpoint=EndpointProbe(True, True, "up"))
    rows = ac.run_health_checks(_params(), accel_host="187.77.70.18")
    assert _state(rows, "listener_443") == "ok"
    assert _state(rows, "dev_ppp") == "ok"          # inferred from live SSTP
    assert _state(rows, "accel_running") == "ok"    # inferred (host process unseen)
    assert _state(rows, "port_443_free") == "ok"    # 443 in-use-by-accel = good
    assert _state(rows, "tls_handshake") == "ok"
    # NONE of the host-dependent checks may be a false failure.
    for cid in ("listener_443", "dev_ppp", "accel_running", "port_443_free"):
        assert _state(rows, cid) != "fail"


def test_container_dev_ppp_skipped_not_failed_when_endpoint_down(monkeypatch):
    """In a container with the endpoint unreachable we still must NOT false-fail
    /dev/ppp (we genuinely can't see the host's) — it's skipped-with-reason; and
    accel_running is skipped (host process invisible). listener honestly fails."""
    from app.radius.services.accel_config import EndpointProbe
    ac = _patch_env(monkeypatch, in_container=True,
                    endpoint=EndpointProbe(False, None, "refused"))
    rows = ac.run_health_checks(_params(), accel_host="187.77.70.18")
    assert _state(rows, "dev_ppp") == "skipped"
    assert _state(rows, "accel_running") == "skipped"
    assert _state(rows, "listener_443") == "fail"   # honest: endpoint unreachable
    # /dev/ppp must NEVER read 'fail' from inside a container.
    assert _state(rows, "dev_ppp") != "fail"


def test_listener_ok_from_endpoint_on_host_deploy(monkeypatch):
    """Non-container host deploy with a reachable endpoint → listener ✓."""
    from app.radius.services.accel_config import EndpointProbe
    ac = _patch_env(monkeypatch, in_container=False,
                    endpoint=EndpointProbe(True, True, "up"))
    rows = ac.run_health_checks(_params(), accel_host="10.0.0.9")
    assert _state(rows, "listener_443") == "ok"
    assert _state(rows, "tls_handshake") == "ok"


def test_tls_failure_is_honest_fail_listener_still_ok(monkeypatch):
    """TCP answers but TLS handshake fails → tls_handshake fails honestly while
    the listener (TCP) is still reported up."""
    from app.radius.services.accel_config import EndpointProbe
    ac = _patch_env(monkeypatch, in_container=True,
                    endpoint=EndpointProbe(True, False, "tls err"))
    rows = ac.run_health_checks(_params(), accel_host="187.77.70.18")
    assert _state(rows, "listener_443") == "ok"
    assert _state(rows, "tls_handshake") == "fail"


def test_endpoint_probe_empty_host_is_skipped_no_network():
    """No accel_host ⇒ probe returns 'not probed' without any socket call, so
    unit/off-server runs stay network-free."""
    from app.radius.services.accel_config import _probe_sstp_endpoint
    p = _probe_sstp_endpoint("", 443)
    assert p.tcp_ok is None and p.tls_ok is None


def test_no_accel_host_skips_listener_never_fails(monkeypatch):
    """Without an endpoint to dial, listener is skipped (unknown), not failed."""
    from app.radius.services import accel_config as ac
    monkeypatch.setattr(ac, "_in_container", lambda: True)
    rows = ac.run_health_checks(_params(), accel_host="")
    assert _state(rows, "listener_443") == "skipped"
