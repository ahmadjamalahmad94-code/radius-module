"""accel-ppp RADIUS attribute builder (transport = vps_accel).

feat/accel-ppp-radius-attrs (Phase 2a, simplified per owner). A DATA
connection is served DIRECTLY by accel-ppp on the customer RADIUS VPS
(with a Let's Encrypt cert) — **no CHR, no proxy, no fleet machinery**.

Scope, deliberately minimal:
  * **Speed cap ONLY** — a 5 Mbit per-connection shaper via the single
    RADIUS attribute accel-ppp reads (``Filter-Id`` by default).
  * **UNLIMITED data, never cut off** — NO quota, NO Session-Octets-Limit,
    NO accounting-based enforcement, NO Disconnect.

accel-ppp is NOT MikroTik: its shaper reads ``Filter-Id``, not
``Mikrotik-Rate-Limit``. The existing chr_mikrotik path in
``freeradius_translator`` is left byte-for-byte unchanged (a subscriber is
one transport or the other, never both).

LAB-PENDING (single source of truth below):
  * ``ACCEL_FILTER_ID_FORM`` — the exact rate string accel-ppp's shaper
    expects (unit + symmetry). Validate against the pinned accel-ppp build
    before live use; changing it is a one-line edit here.
"""
from __future__ import annotations

from ..core.types import AccessPlan, Subscriber

# ════════════════════════════════════════════════════════════════════════
# LAB-PENDING configuration — single source of truth for the shaper form
# ════════════════════════════════════════════════════════════════════════

#: The RADIUS attribute accel-ppp's [shaper] module reads (accel.conf
#: `attr=Filter-Id`). Keep in sync with deploy/accel-ppp/accel-ppp.conf.tmpl.
ACCEL_SHAPER_ATTR = "Filter-Id"

#: Per-connection speed: 5 Mbit/s = 5120 kbit/s (owner-confirmed DATA cap).
ACCEL_DEFAULT_KBIT = 5120

#: LAB-PENDING — how to render the rate into Filter-Id. accel-ppp accepts a
#: few forms across builds; ONE switch so the lab can flip it without
#: touching call sites.
#:   "kbit_symmetric" -> "5120"        (single number, kbit, down=up)
#:   "kbit_down_up"   -> "5120/5120"   (down/up in kbit)
ACCEL_FILTER_ID_FORM = "kbit_symmetric"


def _resolve_kbit(sub: Subscriber, plan: AccessPlan | None) -> tuple[int, int]:
    """(down, up) kbit: subscriber override > plan > 5 Mbit default."""
    down = up = ACCEL_DEFAULT_KBIT
    if (getattr(sub, "bandwidth_control_enabled", False)
            and int(getattr(sub, "download_speed_kbps", 0) or 0) > 0):
        down = int(sub.download_speed_kbps)
        up = int(getattr(sub, "upload_speed_kbps", 0) or 0) or down
    elif plan and int(getattr(plan, "speed_down_kbps", 0) or 0) > 0:
        down = int(plan.speed_down_kbps)
        up = int(getattr(plan, "speed_up_kbps", 0) or 0) or down
    return down, up


def filter_id_value(down_kbit: int, up_kbit: int) -> str:
    """Render the shaper rate into the Filter-Id string per the configured
    (lab-pending) form."""
    down = int(down_kbit) if down_kbit and down_kbit > 0 else ACCEL_DEFAULT_KBIT
    up = int(up_kbit) if up_kbit and up_kbit > 0 else down
    if ACCEL_FILTER_ID_FORM == "kbit_down_up":
        return f"{down}/{up}"
    return f"{down}"  # kbit_symmetric (default)


def accel_reply_attrs(sub: Subscriber, plan: AccessPlan | None) -> list[tuple[str, str, str]]:
    """The per-user radreply rows for a vps_accel DATA subscriber.

    Returns EXACTLY ONE row: the Filter-Id speed cap. No quota, no
    accounting, no Disconnect — unlimited data, speed-capped only.
    Shape matches what the translator feeds
    ``freeradius_repo.replace_user_reply``: ``(attr, op, value)``.
    """
    down, up = _resolve_kbit(sub, plan)
    return [(ACCEL_SHAPER_ATTR, "=", filter_id_value(down, up))]


__all__ = [
    "ACCEL_SHAPER_ATTR",
    "ACCEL_DEFAULT_KBIT",
    "ACCEL_FILTER_ID_FORM",
    "filter_id_value",
    "accel_reply_attrs",
]
