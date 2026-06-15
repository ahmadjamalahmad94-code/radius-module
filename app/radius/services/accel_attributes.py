"""accel-ppp RADIUS attribute builder (transport = vps_accel).

feat/accel-ppp-radius-attrs (Phase 2a). The DATA-connection transport runs
**accel-ppp** on the customer RADIUS VPS, which is NOT MikroTik: its shaper
reads a configurable RADIUS attribute (``Filter-Id`` by default), not
``Mikrotik-Rate-Limit``. This module builds the Access-Accept reply set for
a ``vps_accel`` subscriber. The existing MikroTik/CHR path in
``freeradius_translator`` is left BYTE-FOR-BYTE unchanged — a subscriber is
one transport or the other, never both (see
docs/design/ACCEL_PPP_DATA_CONNECTIONS.md §3 in radius-module-admin).

Speed: 5 Mbit/connection default. Quota: 5 GB with auto-cutoff — enforced
authoritatively by the server-side quota watcher (accel_quota_watcher.py)
via Disconnect-Request; the ``Session-Octets-Limit``/``Octets-Direction``
attributes here are a best-effort NAS hint only.

LAB-PENDING (do NOT treat as confirmed — single source of truth below):
  * ``ACCEL_FILTER_ID_FORM`` — the exact rate string accel-ppp's shaper
    expects (unit + symmetry). Must be validated against the pinned
    accel-ppp build in the Phase-4 lab step. Changing it is a one-line
    edit here; nothing else in the codebase encodes the form.
"""
from __future__ import annotations

from ..core.types import AccessPlan, Subscriber

# ════════════════════════════════════════════════════════════════════════
# LAB-PENDING configuration — single source of truth for the shaper form
# ════════════════════════════════════════════════════════════════════════

#: The RADIUS attribute accel-ppp's [shaper] module reads (accel.conf
#: `attr=Filter-Id`). If the deployed accel-ppp is configured with a
#: different attr, change BOTH here and in the accel-ppp.conf template.
ACCEL_SHAPER_ATTR = "Filter-Id"

#: Default per-connection speed when neither the subscriber nor the plan
#: pins one: 5 Mbit/s = 5120 kbit/s (the owner-confirmed DATA cap).
ACCEL_DEFAULT_KBIT = 5120

#: LAB-PENDING — how to render the rate into Filter-Id. accel-ppp accepts a
#: few forms across builds; we keep ONE switch so the lab can flip it
#: without touching call sites.
#:   "kbit_symmetric"  -> "5120"          (single number, kbit, down=up)
#:   "kbit_down_up"    -> "5120/5120"     (down/up in kbit)
#: Validate against the pinned accel-ppp version before live use.
ACCEL_FILTER_ID_FORM = "kbit_symmetric"

#: Default monthly/total quota when none configured: 5 GB (decimal, 1e6 MB
#: base to match the panel's GB display convention: 5000 MB = 5 GB).
ACCEL_DEFAULT_QUOTA_MB = 5000

#: RADIUS standard attributes for the quota hint (RFC-ish; vendor-neutral).
ATTR_SESSION_OCTETS_LIMIT = "Session-Octets-Limit"   # 227
ATTR_OCTETS_DIRECTION = "Octets-Direction"           # 228; 0 = total (in+out)

#: Interim accounting cadence (seconds) — drives the quota watcher's
#: octet accumulation. Matches the existing chr path (60s).
ACCEL_ACCT_INTERIM = 60

#: MB → bytes using the decimal convention the panel displays (1 GB = 1e9).
_MB = 1_000_000


def _resolve_kbit(sub: Subscriber, plan: AccessPlan | None) -> int:
    """Per-connection speed in kbit: subscriber override > plan > default."""
    if (getattr(sub, "bandwidth_control_enabled", False)
            and int(getattr(sub, "download_speed_kbps", 0) or 0) > 0):
        return int(sub.download_speed_kbps)
    if plan and int(getattr(plan, "speed_down_kbps", 0) or 0) > 0:
        return int(plan.speed_down_kbps)
    return ACCEL_DEFAULT_KBIT


def _resolve_up_kbit(sub: Subscriber, plan: AccessPlan | None) -> int:
    if (getattr(sub, "bandwidth_control_enabled", False)
            and int(getattr(sub, "upload_speed_kbps", 0) or 0) > 0):
        return int(sub.upload_speed_kbps)
    if plan and int(getattr(plan, "speed_up_kbps", 0) or 0) > 0:
        return int(plan.speed_up_kbps)
    return ACCEL_DEFAULT_KBIT


def filter_id_value(down_kbit: int, up_kbit: int) -> str:
    """Render the shaper rate into the Filter-Id string per the configured
    (lab-pending) form. ``down_kbit`` is authoritative for the symmetric
    form; both are used for the down/up form."""
    down = int(down_kbit) if down_kbit and down_kbit > 0 else ACCEL_DEFAULT_KBIT
    up = int(up_kbit) if up_kbit and up_kbit > 0 else down
    if ACCEL_FILTER_ID_FORM == "kbit_down_up":
        return f"{down}/{up}"
    # default: kbit_symmetric
    return f"{down}"


def quota_bytes(sub: Subscriber, plan: AccessPlan | None) -> int:
    """Total quota in BYTES for the cutoff: subscriber combined override >
    plan total > default 5 GB. 0 ⇒ unlimited (no Session-Octets-Limit, no
    watcher cutoff)."""
    mb = 0
    if getattr(sub, "quota_limit_enabled", False) and int(getattr(sub, "combined_quota_mb", 0) or 0) > 0:
        mb = int(sub.combined_quota_mb)
    elif plan and int(getattr(plan, "quota_total_mb", 0) or 0) > 0:
        mb = int(plan.quota_total_mb)
    elif plan is not None:  # a plan exists but pins no quota → apply the DATA default
        mb = ACCEL_DEFAULT_QUOTA_MB
    return mb * _MB


def accel_reply_attrs(sub: Subscriber, plan: AccessPlan | None) -> list[tuple[str, str, str]]:
    """Build the per-user radreply rows for a vps_accel subscriber.

    Returns ``(attr, op, value)`` tuples in the same shape the translator
    feeds ``freeradius_repo.replace_user_reply``. Includes:
      * Filter-Id (speed, accel-ppp shaper);
      * Session-Octets-Limit (227) + Octets-Direction (228) quota HINT;
      * Acct-Interim-Interval so the watcher gets octet updates.
    """
    down = _resolve_kbit(sub, plan)
    up = _resolve_up_kbit(sub, plan)
    out: list[tuple[str, str, str]] = [
        (ACCEL_SHAPER_ATTR, "=", filter_id_value(down, up)),
    ]
    qb = quota_bytes(sub, plan)
    if qb > 0:
        out.append((ATTR_SESSION_OCTETS_LIMIT, ":=", str(qb)))
        out.append((ATTR_OCTETS_DIRECTION, ":=", "0"))  # 0 = total in+out
    out.append(("Acct-Interim-Interval", ":=", str(ACCEL_ACCT_INTERIM)))
    return out


__all__ = [
    "ACCEL_SHAPER_ATTR",
    "ACCEL_DEFAULT_KBIT",
    "ACCEL_FILTER_ID_FORM",
    "ACCEL_DEFAULT_QUOTA_MB",
    "ACCEL_ACCT_INTERIM",
    "filter_id_value",
    "quota_bytes",
    "accel_reply_attrs",
]
