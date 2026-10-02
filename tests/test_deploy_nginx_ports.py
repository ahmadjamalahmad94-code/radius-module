# -*- coding: utf-8 -*-
"""Guard: the nginx HTTPS host port is never HARD-CODED to :443 in compose.

History: host :443 used to belong to accel-ppp (v6 SSTP management tunnel), so
the panel lived on :8443. a87696f7 made the panel's host port configurable via
``${HOBERADIUS_PANEL_HTTPS_PUBLISH:-8443:8443}``, and the owner's port baseline
(bffd151c, 2026-10-01) sets NEW installs to panel 443 / SSTP 4443 through
``deploy/.env.example`` — while EXISTING servers keep the compose default 8443
until each is converted individually. These tests guard both halves, and that
the panel never takes :443 while SSTP still holds it. Dependency-free (text parse).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import re

_REPO = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
_COMPOSE = os.path.join(_REPO, "deploy", "docker-compose.yml")


def _nginx_ports_block() -> str:
    with open(_COMPOSE, encoding="utf-8") as fh:
        text = fh.read()
    # the nginx service block: from "  nginx:" up to the next top-level service
    m = re.search(r"^  nginx:\n(.*?)(?=^  \w|\Z)", text, re.S | re.M)
    assert m, "nginx service not found in docker-compose.yml"
    body = m.group(1)
    # the ports: list (lines until the next same-indent key)
    pm = re.search(r"^    ports:\n(.*?)(?=^    \w)", body, re.S | re.M)
    assert pm, "nginx ports: block not found"
    return pm.group(1)


def _published_host_ports(ports_block: str) -> list:
    """Host-side ports from LITERAL '- \"H:C\"' entries (ignore comments)."""
    out = []
    for line in ports_block.splitlines():
        s = line.strip()
        if not s.startswith("-"):
            continue
        m = re.search(r'"(\d+(?:-\d+)?):', s)
        if m:
            out.append(m.group(1))
    return out


def _variable_port_entries(ports_block: str) -> dict:
    """``- "${VAR:-default}"`` entries → {VAR: default}."""
    out = {}
    for line in ports_block.splitlines():
        m = re.match(r'\s*-\s*"\$\{(\w+):-([^}]*)\}"', line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _env_example() -> dict:
    path = os.path.join(_REPO, "deploy", ".env.example")
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^(HOBERADIUS_[A-Z0-9_]+)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def test_nginx_does_not_publish_443():
    """:443 is never a literal in compose — only reachable via the opt-in
    variable, so an existing server (SSTP still on :443) can't collide."""
    hosts = _published_host_ports(_nginx_ports_block())
    assert "443" not in hosts, f"nginx must not hard-code :443: {hosts}"


def test_nginx_still_publishes_80_and_stream_range():
    hosts = _published_host_ports(_nginx_ports_block())
    assert "80" in hosts, "panel must still be served on :80"
    assert "51000-51199" in hosts, "NPC remote-tunnel range must remain"


def test_nginx_publishes_8443_https():
    """Existing servers keep 8443 (owner, bffd151c): the compose DEFAULT for the
    configurable HTTPS publish (a87696f7) stays 8443:8443, container side 8443."""
    var = _variable_port_entries(_nginx_ports_block())
    assert "HOBERADIUS_PANEL_HTTPS_PUBLISH" in var, (
        "panel HTTPS must be published via ${HOBERADIUS_PANEL_HTTPS_PUBLISH:-8443:8443}"
    )
    assert var["HOBERADIUS_PANEL_HTTPS_PUBLISH"] == "8443:8443", (
        "compose default must stay 8443:8443 — existing servers convert one by one"
    )


def test_new_install_baseline_panel_443_sstp_4443():
    """Owner decision 2026-10-01 (bffd151c): every NEW install is born with the
    panel on host 443 and SSTP on 4443 — real values in .env.example."""
    env = _env_example()
    assert env.get("HOBERADIUS_PANEL_HTTPS_PUBLISH") == "443:8443"
    assert env.get("HOBERADIUS_ACCEL_SSTP_PORT") == "4443"


def test_panel_never_takes_443_while_sstp_holds_it():
    """If the panel's host port is 443, SSTP must have moved off 443 (else the
    nginx container fails to start: address already in use)."""
    env = _env_example()
    panel_host = env.get("HOBERADIUS_PANEL_HTTPS_PUBLISH", "8443:8443").split(":")[0]
    sstp = env.get("HOBERADIUS_ACCEL_SSTP_PORT", "443")
    if panel_host == "443":
        assert sstp != "443", "panel on :443 requires SSTP moved (e.g. 4443)"
    # the container side is always 8443 (entrypoint/TLS template keyed on it)
    assert env.get("HOBERADIUS_PANEL_HTTPS_PUBLISH", "8443:8443").endswith(":8443")


# ─── the :80 server block must stay HTTP-only (provably unchanged) ───
_NGINX_CONF = os.path.join(_REPO, "deploy", "nginx.conf")
_TLS_CONF = os.path.join(_REPO, "deploy", "nginx-tls-8443.conf")


def test_port80_config_is_http_only_and_untouched():
    """The :80 config (default.conf) must NOT gain any TLS/8443/443 listener —
    HTTPS lives entirely in the separate 8443 file. This proves :80 behaviour
    is isolated from the additive HTTPS change."""
    with open(_NGINX_CONF, encoding="utf-8") as fh:
        conf = fh.read()
    assert "listen 80 default_server;" in conf       # :80 still the panel
    assert "listen 443" not in conf
    assert "listen 8443" not in conf
    assert "ssl_certificate" not in conf             # no TLS bleed into :80


def test_8443_block_is_ssl_with_selfsigned_cert():
    with open(_TLS_CONF, encoding="utf-8") as fh:
        tls = fh.read()
    assert "listen 8443 ssl;" in tls
    assert "ssl_certificate     /etc/nginx/tls/selfsigned.crt;" in tls
    assert "ssl_certificate_key /etc/nginx/tls/selfsigned.key;" in tls
    # same app target as :80 — the re-resolved variable upstream (fix3), never
    # a static upstream (a recreated hoberadius container must not 502)
    assert "proxy_pass $hr_app;" in tls
    assert "resolver 127.0.0.11 valid=10s" in tls
    # no upstream DECLARATION (a comment mentioning it is fine)
    import re as _re
    assert not _re.search(r"^\s*upstream\s+\w+\s*\{", tls, _re.M)
    # internal API still shielded on the HTTPS listener
    assert "location ~ ^/api/v1/internal/ {" in tls


def test_entrypoint_enables_8443_only_when_cert_exists():
    """Fail-safe: the entrypoint must gate the 8443 block on cert presence so a
    cert-gen failure can never break :80."""
    ep = os.path.join(_REPO, "deploy", "nginx-entrypoint.sh")
    with open(ep, encoding="utf-8") as fh:
        body = fh.read()
    assert "8443-ssl.conf" in body
    # the copy-into-conf.d is guarded by [ -s "$TLS_CRT" ] ... before nginx -t
    assert '[ -s "$TLS_CRT" ]' in body
    assert body.index("8443-ssl.conf") < body.rindex("nginx -t")
