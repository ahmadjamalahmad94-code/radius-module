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
