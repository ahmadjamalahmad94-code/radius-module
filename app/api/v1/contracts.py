"""Real read-only API capability manifest.

Previously this module only held a `not_implemented_contract` helper that
returned 501 envelopes and was never registered on any blueprint (dead stub).
It now exposes a real endpoint — `GET /api/v1/contracts` — that introspects the
live URL map and reports exactly which v1 resources/methods the running server
offers. This is genuine, self-describing API data (useful for clients probing
capabilities) with no stub/501 path.
"""
from __future__ import annotations

from flask import Blueprint, current_app

from ..auth import require_api_token
from ..responses import ok

_SKIP_METHODS = {"HEAD", "OPTIONS"}
_V1_MARKER = "/api/v1/"


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/contracts", "api_contracts",
        require_api_token(api_contracts), methods=["GET"],
    )


def _v1_endpoints() -> list[dict]:
    items: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for rule in current_app.url_map.iter_rules():
        path = str(rule)
        if _V1_MARKER not in path:
            continue
        methods = sorted(m for m in (rule.methods or set()) if m not in _SKIP_METHODS)
        key = (path, ",".join(methods))
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "path": path,
            "methods": methods,
            "endpoint": rule.endpoint,
        })
    items.sort(key=lambda item: item["path"])
    return items


def api_contracts():
    """GET /api/v1/contracts — the live v1 API surface (real introspection)."""
    endpoints = _v1_endpoints()
    return ok({
        "version": "v1",
        "count": len(endpoints),
        "endpoints": endpoints,
    })
