"""Profile-aware Mikrotik-Rate-Limit resolution — single source of truth.

Owner decision (June 2026, Finding-1 → option A): a bandwidth profile referenced
by a plan (``access_plans.bandwidth_id``) must REALLY drive the speed, not just
sit in the UI. Both RADIUS reply paths call :func:`plan_rate_limit` for the plan
"base" tier so they stay consistent:

  * ``services/freeradius_translator.sync_plan`` (radgroupreply / group sync)
  * ``services/policy_engine._build_accept_attrs`` (live authorize path)

Precedence is owned by the caller. Within the plan BASE tier this module's rule
is: **profile (if the plan references one and it exists) overrides the plan's own
speed fields**; otherwise fall back to the plan fields so plans without a profile
are untouched. The full cascade in the live path stays:

    active schedule  >  subscriber override  >  [ profile  or  plan default ]
"""
from __future__ import annotations

from typing import Optional

from ..core import units

# Accept either the unit CODE (units.SPEED_UNITS first col, e.g. "kbps"/"Mbps")
# or the LABEL stored by the profile form ("Kbps"/"Mbps") — case-insensitive.
_SPEED_UNIT_TO_CODE: dict[str, str] = {}
for _code, _label, _ratio in units.SPEED_UNITS:
    _SPEED_UNIT_TO_CODE[_code.lower()] = _code
    _SPEED_UNIT_TO_CODE[_label.lower()] = _code


def _speed_to_kbps(value, unit) -> int:
    code = _SPEED_UNIT_TO_CODE.get((unit or "kbps").strip().lower(), "kbps")
    try:
        return int(units.to_base(value or 0, code, "speed"))
    except Exception:  # noqa: BLE001 — never break a RADIUS reply on a bad unit
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0


def profile_rate_kbps(profile) -> tuple[int, int]:
    """Return ``(down_kbps, up_kbps)`` for a BandwidthProfile, honoring its units."""
    down = _speed_to_kbps(getattr(profile, "rate_down", 0), getattr(profile, "rate_down_unit", "kbps"))
    up = _speed_to_kbps(getattr(profile, "rate_up", 0), getattr(profile, "rate_up_unit", "kbps"))
    return down, up


def resolve_plan_profile(plan):
    """Return the BandwidthProfile a plan references, or None.

    Lazy import of the repo keeps this module import-cycle-free (the translator
    and policy_engine both import it very early)."""
    bw_id = getattr(plan, "bandwidth_id", None)
    if not bw_id:
        return None
    try:
        from ..db.repos import bandwidth_repo
        return bandwidth_repo.get(plan.tenant_id, int(bw_id))
    except Exception:  # noqa: BLE001 — missing profile must not break auth
        return None


def plan_rate_limit(plan) -> Optional[str]:
    """Mikrotik-Rate-Limit string for a plan's BASE tier, profile-aware.

    Profile wins over the plan's own fields when the plan references one that
    exists (its raw burst string if set, else ``up_k/down_k`` from its rates).
    Falls back to the plan's ``burst_raw`` / ``speed_*_kbps``. ``None`` when
    neither yields a rate (caller then emits no Mikrotik-Rate-Limit).
    """
    profile = resolve_plan_profile(plan)
    if profile is not None:
        burst = (getattr(profile, "burst", "") or "").strip()
        if burst:
            return burst
        down, up = profile_rate_kbps(profile)
        if down or up:
            return f"{up}k/{down}k"
    if plan.speed_down_kbps or plan.speed_up_kbps:
        return plan.burst_raw or f"{plan.speed_up_kbps}k/{plan.speed_down_kbps}k"
    return None


def effective_rate_limit(tenant_id: int, username: str, *, at=None) -> str:
    """The Mikrotik-Rate-Limit a live session SHOULD have right now, by the full
    cascade — identical ordering to policy_engine._build_accept_attrs:

        active schedule (time window)  >  subscriber override  >  plan/profile base

    Used by the auto-schedule worker and the profile «apply now» action to push
    the cascade-correct rate via CoA (so we never fight a higher-precedence
    subscriber override or subscriber-scope schedule). Returns "" if unknown.
    """
    try:
        from ..db.repos import operations_repo, plans_repo, subscribers_repo
    except Exception:  # noqa: BLE001
        return ""
    sub = subscribers_repo.get_subscriber(tenant_id, username)
    if not sub:
        return ""
    plan = None
    if getattr(sub, "plan_id", None):
        try:
            plan = plans_repo.get_plan(tenant_id, sub.plan_id)
        except Exception:  # noqa: BLE001
            plan = None
    rule = operations_repo.resolve_effective_bandwidth_schedule(
        tenant_id,
        subscriber_username=username,
        card_batch_id=getattr(sub, "card_batch_id", None),
        plan_id=(plan.id if plan else getattr(sub, "plan_id", None)),
        at=at,
    )
    if rule:
        return (f"{int(rule.get('speed_up_kbps') or 0)}k/"
                f"{int(rule.get('speed_down_kbps') or 0)}k")
    if getattr(sub, "bandwidth_control_enabled", False) and (
        getattr(sub, "download_speed_kbps", 0) or getattr(sub, "upload_speed_kbps", 0)
    ):
        return f"{sub.upload_speed_kbps}k/{sub.download_speed_kbps}k"
    if plan:
        return plan_rate_limit(plan) or ""
    return ""


def live_apply_enabled() -> bool:
    """Whether live CoA application is the operative path.

    Owner decision (June 2026): schedules/profiles must actually take effect on
    live sessions, so this defaults **ON**. Set HOBERADIUS_ENABLE_LIVE_SPEED_APPLY
    to 0/false/no/off to force dry-run only (kill-switch for the whole live path:
    manual «apply now», profile apply, and the auto-schedule worker)."""
    try:
        from ..core import env_settings
        raw = env_settings.env("HOBERADIUS_ENABLE_LIVE_SPEED_APPLY", "1")
    except Exception:  # noqa: BLE001
        raw = "1"
    return str("1" if raw is None else raw).strip().lower() not in (
        "0", "false", "no", "off")


__all__ = ["plan_rate_limit", "profile_rate_kbps", "resolve_plan_profile",
           "effective_rate_limit", "live_apply_enabled"]
