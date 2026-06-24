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
