# -*- coding: utf-8 -*-
"""Stdlib-only accel-ppp generator (deploy/accel-ppp/accel_conf_gen.py).

The whole point of this module is that the HOST installer can run it with plain
`python3` — no Flask, no `app`, no venv — even when the panel runs in Docker.
These tests PROVE that property (static + a real subprocess with a sanitized
environment) and lock in param resolution + the CLI.

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_GEN_DIR = os.path.join(_REPO, "deploy", "accel-ppp")
_GEN = os.path.join(_GEN_DIR, "accel_conf_gen.py")


def _import_gen():
    if _GEN_DIR not in sys.path:
        sys.path.insert(0, _GEN_DIR)
    import accel_conf_gen
    return accel_conf_gen


# ════════════ 1) stdlib-only (no app / flask) ════════════
def test_source_has_no_app_or_flask_import():
    """No import STATEMENT may reference flask or the app package (comments may
    mention them, e.g. 'must match app/radius/core/env_settings')."""
    import re
    bad = re.compile(r"^\s*(?:from|import)\s+(?:app|flask)\b")
    with open(_GEN, encoding="utf-8") as fh:
        offenders = [ln for ln in fh if bad.match(ln)]
    assert not offenders, f"forbidden imports: {offenders}"


def test_module_exposes_pure_api_when_imported():
    g = _import_gen()
    assert hasattr(g, "generate_accel_conf") and hasattr(g, "resolve_params")
    assert hasattr(g, "openssl_selfsigned_cmd") and hasattr(g, "AccelParams")


def test_subprocess_runs_with_sanitized_env():
    """Invoke the CLI the way the HOST installer does — a fresh interpreter with
    NO PYTHONPATH to the app — and confirm it produces a valid config."""
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and not k.startswith("HOBERADIUS_")}
    out = subprocess.run([sys.executable, _GEN, "config"],
                         capture_output=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    conf = out.stdout.decode("utf-8")
    assert conf.count("[radius]") == 1 and conf.count("[client-ip-range]") == 1
    assert "auth_mschap_v2" in conf
    # No directive line pins TLS (comments mentioning it are fine).
    for line in conf.splitlines():
        s = line.strip()
        assert not s.startswith("ssl-protocol=") and not s.startswith("ssl-ciphers=")


def test_subprocess_imports_no_flask_or_app():
    """Runtime proof of stdlib-only: importing the module in a clean interpreter
    pulls in neither flask nor the app package."""
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and not k.startswith("HOBERADIUS_")}
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import accel_conf_gen; "
        "print('flask' in sys.modules, 'app' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code, _GEN_DIR],
                         capture_output=True, env=env, timeout=30)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")
    assert out.stdout.decode().strip() == "False False"


# ════════════ 2) parameter resolution ════════════
def test_defaults_and_gateway_is_first_host():
    g = _import_gen()
    p = g.resolve_params(env={}, overrides={})
    assert str(p.pool) == "10.50.0.0/24"
    assert str(p.gateway_ip) == "10.50.0.1"        # first host of the pool
    assert p.sstp_port == 443
    assert p.radius_server == "127.0.0.1"
    assert p.radius_secret == "accel-local-secret"


def test_precedence_overrides_beat_env_beat_default():
    g = _import_gen()
    env = {g.ENV_POOL: "10.9.0.0/24", g.ENV_SSTP_PORT: "8443"}
    p = g.resolve_params(env=env, overrides={"sstp_port": "9443"})
    assert str(p.pool) == "10.9.0.0/24"            # from env
    assert p.sstp_port == 9443                     # override beats env
    assert str(p.gateway_ip) == "10.9.0.1"         # first host of env pool


def test_gateway_outside_pool_rejected():
    g = _import_gen()
    with pytest.raises(ValueError):
        g.resolve_params(env={}, overrides={"gateway": "10.99.0.1"})


def test_load_env_file(tmp_path):
    g = _import_gen()
    f = tmp_path / "panel.env"
    f.write_text(
        "# comment\n"
        "export HOBERADIUS_ACCEL_RADIUS_SECRET='from-file'\n"
        "HOBERADIUS_MGMT_TUNNEL_POOL=10.5.0.0/24\n"
        "IGNORED_KEY=nope\n",
        encoding="utf-8",
    )
    loaded = g.load_env_file(str(f))
    assert loaded[g.ENV_RADIUS_SECRET] == "from-file"
    assert loaded[g.ENV_POOL] == "10.5.0.0/24"
    assert "IGNORED_KEY" not in loaded


# ════════════ 3) CLI surface ════════════
def _cli(*args, env_extra=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("HOBERADIUS_")}
    if env_extra:
        env.update(env_extra)
    return subprocess.run([sys.executable, _GEN, *args],
                          capture_output=True, env=env, timeout=30)


def test_cli_print_and_openssl():
    out = _cli("--sstp-port", "8443", "print", "sstp_port")
    assert out.returncode == 0
    assert out.stdout.decode().strip() == "8443"

    out = _cli("openssl-cmd")
    line = out.stdout.decode().strip()
    assert line.startswith("openssl req -x509") and line.endswith(".pem")


def test_cli_config_out_file_is_utf8(tmp_path):
    target = tmp_path / "accel-ppp.conf"
    out = _cli("config", "--out", str(target))
    assert out.returncode == 0
    text = target.read_text(encoding="utf-8")          # must decode as UTF-8
    assert "[sstp]" in text and "port=443" in text


def test_cli_env_file_drives_values(tmp_path):
    f = tmp_path / "p.env"
    f.write_text("HOBERADIUS_ACCEL_SSTP_PORT=7443\n", encoding="utf-8")
    out = _cli("--env-file", str(f), "print", "sstp_port")
    assert out.stdout.decode().strip() == "7443"


# ════════════ 4) app wrapper parity ════════════
def test_app_wrapper_reexports_same_objects():
    """The app module must reuse the SAME generator (single source), so output
    is byte-identical and AccelConfigParams IS the stdlib AccelParams."""
    g = _import_gen()
    from app.radius.services import accel_config as ac
    assert ac.generate_accel_conf is g.generate_accel_conf
    assert ac.AccelConfigParams is g.AccelParams
    assert ac.openssl_selfsigned_cmd is g.openssl_selfsigned_cmd


# ════════════ 5) ssl cert/key are SEPARATE files (the handshake fix) ════════════
def test_sstp_emits_pemfile_and_keyfile():
    g = _import_gen()
    conf = g.generate_accel_conf(g.resolve_params(env={}, overrides={}))
    sstp = conf.split("[sstp]", 1)[1].split("[pptp]", 1)[0]
    assert "ssl-pemfile=/etc/accel-ppp/accel-selfsigned.pem" in sstp
    assert "ssl-keyfile=/etc/accel-ppp/accel-selfsigned.key" in sstp


def test_keyfile_defaults_to_sibling_of_cert():
    g = _import_gen()
    p = g.resolve_params(env={g.ENV_SSL_PEMFILE: "/etc/ssl/sstp.pem"}, overrides={})
    assert p.ssl_keyfile == "/etc/ssl/sstp.key"
    # explicit override wins
    p2 = g.resolve_params(env={}, overrides={"ssl_keyfile": "/custom/k.key"})
    assert p2.ssl_keyfile == "/custom/k.key"


def test_openssl_cmd_writes_separate_key_and_cert():
    g = _import_gen()
    cmd = g.openssl_selfsigned_cmd("/etc/a/sstp.pem", "/etc/a/sstp.key")
    assert "-keyout" in cmd and "-out" in cmd
    key = cmd[cmd.index("-keyout") + 1]
    cert = cmd[cmd.index("-out") + 1]
    assert key == "/etc/a/sstp.key" and cert == "/etc/a/sstp.pem"
    assert key != cert, "key and cert MUST be different files"
    # default keyfile is derived when omitted
    cmd2 = g.openssl_selfsigned_cmd("/etc/a/sstp.pem")
    assert cmd2[cmd2.index("-keyout") + 1] == "/etc/a/sstp.key"


def test_cli_print_ssl_keyfile():
    out = _cli("print", "ssl_keyfile")
    assert out.returncode == 0
    assert out.stdout.decode().strip() == "/etc/accel-ppp/accel-selfsigned.key"


def test_app_wrapper_params_carry_keyfile():
    from app.radius.services import accel_config as ac
    p = ac.params_from_settings()
    assert p.ssl_keyfile and p.ssl_keyfile.endswith(".key")
    lines = ac.export_env_lines()
    assert any(l.startswith("HOBERADIUS_ACCEL_SSL_KEYFILE=") for l in lines)
