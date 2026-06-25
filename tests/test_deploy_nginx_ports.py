# -*- coding: utf-8 -*-
"""Guard: nginx must NOT publish host :443 — that port belongs to accel-ppp
(the v6 SSTP management tunnel binds host :443). The panel is served on :80.

Re-adding a 443 host mapping would collide with accel on a fresh VPS, which is
exactly the manual unbind we codified. Dependency-free (text parse).

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
    """Host-side ports from '- \"H:C\"' entries (ignore comments)."""
    out = []
    for line in ports_block.splitlines():
        s = line.strip()
        if not s.startswith("-"):
            continue
        m = re.search(r'"(\d+(?:-\d+)?):', s)
        if m:
            out.append(m.group(1))
    return out


def test_nginx_does_not_publish_443():
    hosts = _published_host_ports(_nginx_ports_block())
    assert "443" not in hosts, f"nginx must not publish :443 (accel owns it): {hosts}"


def test_nginx_still_publishes_80_and_stream_range():
    hosts = _published_host_ports(_nginx_ports_block())
    assert "80" in hosts, "panel must still be served on :80"
    assert "51000-51199" in hosts, "NPC remote-tunnel range must remain"


def test_nginx_publishes_8443_https():
    hosts = _published_host_ports(_nginx_ports_block())
    assert "8443" in hosts, "panel HTTPS must be published on :8443"


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
    # reuses the same upstream as :80 (does not redefine it → no fork)
    assert "proxy_pass http://hoberadius_app;" in tls
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
