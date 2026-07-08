"""Single source of truth for the running application version.

Before this module the "running version" was scattered: an env var
(``HOBERADIUS_BUILD_SHA`` / ``HOBERADIUS_VERSION``) read ad-hoc by the
license heartbeat, and a hard-coded ``0.1.0-foundation`` string in the
``/api/v1/version`` endpoint. The self-update feature needs ONE canonical,
comparable version, so it lives here.

Resolution order for :func:`running_version`:
  1. ``HOBERADIUS_VERSION`` env var (operator/CI override — must be semver).
  2. :data:`APP_VERSION` baked into the image at this line.

``HOBERADIUS_BUILD_SHA`` is kept separate (:func:`build_sha`) — it is a git
commit identifier, NOT an orderable version, so it never participates in the
"is a newer version available?" comparison. The update check compares semver
strings only; a non-semver running version simply never flags an update
(fail-safe: no false "update available" banner).
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

# ── Baked release version ─────────────────────────────────────────────
# Bump this on every release cut. CI may also inject HOBERADIUS_VERSION at
# build/run time to override it without editing the source.
APP_VERSION = "1.1.0"

_SEMVER_RE = re.compile(
    r"^\s*v?(\d+)\.(\d+)(?:\.(\d+))?(?:[-+].*)?\s*$"
)


def running_version() -> str:
    """The version string this instance is currently running."""
    env = os.environ.get("HOBERADIUS_VERSION")
    if env and env.strip():
        return env.strip()
    return APP_VERSION


def build_sha() -> str:
    """The git commit SHA baked at build time, or empty string."""
    return (os.environ.get("HOBERADIUS_BUILD_SHA") or "").strip()


def parse_version(value: Optional[str]) -> Optional[Tuple[int, int, int]]:
    """Parse a semver-ish string to a ``(major, minor, patch)`` tuple.

    Tolerates a leading ``v`` and a ``-pre``/``+build`` suffix (both ignored
    for ordering). Returns ``None`` when the string is not semver-shaped
    (e.g. a git SHA) — callers must treat ``None`` as "not comparable".
    """
    if not value:
        return None
    m = _SEMVER_RE.match(str(value))
    if not m:
        return None
    major, minor, patch = m.group(1), m.group(2), m.group(3)
    return (int(major), int(minor), int(patch or 0))


def is_newer(remote: Optional[str], local: Optional[str]) -> bool:
    """True iff ``remote`` is a strictly newer semver than ``local``.

    Fail-safe: if EITHER side is unparseable, returns ``False`` so we never
    surface a bogus "update available" state. Pre-release/build suffixes are
    ignored for ordering (``1.2.0-rc1`` == ``1.2.0`` here) — the panel should
    only advertise stable versions to the update channel.
    """
    r = parse_version(remote)
    l = parse_version(local)
    if r is None or l is None:
        return False
    return r > l


def meets_min(local: Optional[str], min_version: Optional[str]) -> bool:
    """True iff ``local`` >= ``min_version`` (or min unknown/unparseable).

    Used to flag an instance running BELOW the minimum supported version.
    Fail-safe: unknown/unparseable ``min_version`` → ``True`` (no warning).
    """
    m = parse_version(min_version)
    if m is None:
        return True
    l = parse_version(local)
    if l is None:
        return True
    return l >= m
