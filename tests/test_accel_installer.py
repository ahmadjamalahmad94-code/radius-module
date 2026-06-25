# -*- coding: utf-8 -*-
"""Guards for deploy/accel-ppp/install-accel-selfsigned.sh.

Locks in the two production fixes:
  * the :443 conflict check must WHITELIST accel-pppd (our own SSTP server) and
    only abort on a FOREIGN holder (false-positive that aborted the live run),
  * a no-host-python fallback that runs the generator via the panel container.

Plus a `bash -n` syntax check + the port-whitelist logic exercised with a
mocked `ss` (skipped when bash is unavailable).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_INSTALLER = os.path.join(_REPO, "deploy", "accel-ppp", "install-accel-selfsigned.sh")


def _src() -> str:
    with open(_INSTALLER, encoding="utf-8") as fh:
        return fh.read()


def test_whitelists_accel_pppd():
    src = _src()
    assert "accel-pppd|accel-ppp" in src, "accel-pppd must be whitelisted"
    assert "FOREIGN" in src and "أجنبية" in src, "only foreign holders abort"


def test_has_container_fallback_and_panel_name():
    src = _src()
    assert "_gen_via_container" in src
    assert "params_from_settings" in src        # container path uses the app
    assert 'name=hoberadius' in src             # default panel container


def test_bash_syntax_ok():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    out = subprocess.run([bash, "-n", _INSTALLER], capture_output=True)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")


def test_port_whitelist_logic_with_mocked_ss():
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash not available")
    # Reproduce the installer's holder-parsing + whitelist decision in isolation.
    script = r'''
decide() {
  HOLDERS="$(printf '%s' "$1" | grep -oE 'users:\(\("[^"]+"' | grep -oE '"[^"]+"' | tr -d '"' | sort -u)"
  FOREIGN=""
  for h in $HOLDERS; do case "$h" in accel-pppd|accel-ppp) : ;; *) FOREIGN="$FOREIGN $h";; esac; done
  FOREIGN="$(printf '%s' "$FOREIGN" | tr -s ' ' | sed -e 's/^ //' -e 's/ $//')"
  [ -n "$FOREIGN" ] && echo "ABORT:$FOREIGN" || echo "OK"
}
decide 'LISTEN 0 16 *:443 *:* users:(("accel-pppd",pid=42,fd=7))'
decide 'LISTEN 0 511 *:443 *:* users:(("nginx",pid=9,fd=8))'
decide ''
'''
    out = subprocess.run([bash, "-c", script], capture_output=True, timeout=20)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    lines = out.stdout.decode().split()
    assert lines[0] == "OK"               # accel-pppd → continue
    assert lines[1] == "ABORT:nginx"      # foreign → abort
    assert lines[2] == "OK"               # nothing listening → free


def test_loopback_ip_and_systemd_unit():
    src = _src()
    assert "ip addr replace" in src and "dev lo" in src
    assert "hoberadius-accel-mgmt-ip.service" in src
    assert "Before=accel-ppp.service" in src
    assert "RemainAfterExit=yes" in src


def test_freeradius_client_autoprovision_single_secret():
    src = _src()
    assert "freeradius-clients-wizard" in src
    assert "client accel_local_sstp" in src
    assert ".reload-trigger" in src
    # secret comes from the generator (single source), NOT hardcoded
    assert "secret    = ${RAD_SECRET}" in src
    assert 'RAD_SECRET="$(gen print radius_secret)"' in src
    # never APPENDS the client into clients.conf (would be a duplicate client)
    assert "clients.conf\"" not in src and "clients.conf'" not in src
    assert ">> $FR" not in src
    # clean up stray .bak in the include dir
    assert "name '*.bak'" in src


def test_tls_probe_is_selfsigned_safe_tls12():
    src = _src()
    assert "-tls1_2" in src
    assert "Master-Key" in src        # completion signal, self-signed-safe
