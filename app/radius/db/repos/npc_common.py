"""npc_common — shared helpers across the three NPC repos.

Slug generation + timestamp + service-discriminator constants
live here so each sub-service repo doesn't re-implement them.

Pure module — no DB access, no Flask, no network. Safe to
import from tests without an app context.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime


# Service discriminator literals — used on the shared
# `npc_deployments` and `npc_script_versions` tables to tell
# which sub-service a row belongs to. Stable forever; new
# services append.
SERVICE_REMOTE_ACCESS = "remote_access"
SERVICE_WEB_BLOCK     = "web_block"
SERVICE_WALLED_GARDEN = "walled_garden"

ALLOWED_SERVICES = frozenset({
    SERVICE_REMOTE_ACCESS,
    SERVICE_WEB_BLOCK,
    SERVICE_WALLED_GARDEN,
})


_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def now_iso() -> str:
    """UTC ISO-8601 with a trailing Z — same shape VX2 uses
    so the audit timeline aligns across features."""
    return datetime.utcnow().isoformat() + "Z"


def slugify(value: str) -> str:
    """Lowercase, ASCII, dash-separated.

    Mirrors VX2's `site_exit_policies_repo.slugify` so the
    operator's Arabic policy names get a deterministic
    `policy-<sha1[:10]>` fallback instead of an empty-slug
    error. Length capped at 64.
    """
    if not value:
        return ""
    s = value.strip().lower().replace(" ", "-")
    s = _SLUG_RE.sub("-", s)
    s = s.strip("-")
    if not s:
        h = hashlib.sha1(
            value.strip().encode("utf-8")
        ).hexdigest()[:10]
        return f"policy-{h}"
    return s[:64]


def assert_service(service: str) -> None:
    """Raise ValueError for unknown service discriminators —
    cheap guard for repo entry points that take `service` as
    a parameter."""
    if service not in ALLOWED_SERVICES:
        raise ValueError(
            f"invalid NPC service: {service!r}; "
            f"expected one of {sorted(ALLOWED_SERVICES)}"
        )


__all__ = [
    "SERVICE_REMOTE_ACCESS", "SERVICE_WEB_BLOCK",
    "SERVICE_WALLED_GARDEN", "ALLOWED_SERVICES",
    "now_iso", "slugify", "assert_service",
]
