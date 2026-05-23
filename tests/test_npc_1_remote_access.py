"""NPC Phase 1 — `npc_remote_access` foundation (pure tests)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest


def test_module_has_no_side_effects_on_import():
    import importlib
    import app.radius.services.npc_remote_access as m
    importlib.reload(m)
    assert m.RISK_LOW == "low"
    assert callable(m.assess_policy)


# ─── Service catalogue ───────────────────────────────────────


def test_service_catalogue_contains_six_entries():
    from app.radius.services import npc_remote_access as ra
    keys = {s.key for s in ra.list_services()}
    assert {"winbox", "ssh", "api", "api_ssl",
            "webfig_http", "webfig_https"} <= keys


def test_get_service_returns_none_for_unknown():
    from app.radius.services import npc_remote_access as ra
    assert ra.get_service("ftp") is None


def test_winbox_port_and_protocol():
    from app.radius.services import npc_remote_access as ra
    svc = ra.get_service("winbox")
    assert svc.port == 8291
    assert svc.protocol == "tcp"


# ─── expires_at validator ────────────────────────────────────


_NOW = datetime(2026, 5, 23, 12, 0, 0, tzinfo=timezone.utc)


def test_expires_at_rejects_empty():
    from app.radius.services import npc_remote_access as ra
    v = ra.validate_expires_at("", now=_NOW)
    assert not v.ok
    assert v.reason


def test_expires_at_rejects_unparseable():
    from app.radius.services import npc_remote_access as ra
    v = ra.validate_expires_at("not a date", now=_NOW)
    assert not v.ok
    assert "ISO" in v.reason


def test_expires_at_rejects_in_past():
    from app.radius.services import npc_remote_access as ra
    past = (_NOW - timedelta(hours=1)).isoformat()
    v = ra.validate_expires_at(past, now=_NOW)
    assert not v.ok
    assert "بعد" in v.reason


def test_expires_at_rejects_beyond_max_horizon():
    from app.radius.services import npc_remote_access as ra
    far = (_NOW + timedelta(hours=ra.MAX_EXPIRY_HOURS + 1)).isoformat()
    v = ra.validate_expires_at(far, now=_NOW)
    assert not v.ok
    assert "يوماً" in v.reason


def test_expires_at_accepts_near_horizon_and_z_suffix():
    from app.radius.services import npc_remote_access as ra
    fut = (_NOW + timedelta(hours=2))
    v = ra.validate_expires_at(
        fut.isoformat().replace("+00:00", "Z"),
        now=_NOW,
    )
    assert v.ok
    assert v.ttl_minutes == 120
    assert v.parsed.tzinfo is not None


# ─── source address-list validator ───────────────────────────


def test_source_address_list_accepts_empty():
    from app.radius.services import npc_remote_access as ra
    v = ra.validate_source_address_list("")
    assert v.ok
    assert v.cleaned == ""


def test_source_address_list_accepts_simple_name():
    from app.radius.services import npc_remote_access as ra
    v = ra.validate_source_address_list("ops_bastion-01")
    assert v.ok
    assert v.cleaned == "ops_bastion-01"


def test_source_address_list_rejects_bad_chars():
    from app.radius.services import npc_remote_access as ra
    for bad in ("with space", "تسمية", "name!exclaim"):
        v = ra.validate_source_address_list(bad)
        assert not v.ok


# ─── selected_ports + safe_source_cidrs ──────────────────────


def test_selected_ports_returns_tuples_for_enabled_only():
    from app.radius.services import npc_remote_access as ra
    out = ra.selected_ports(
        allow_winbox=True,
        allow_ssh=False,
        allow_webfig_https=True,
    )
    keys = [k for (k, _, _) in out]
    assert "winbox" in keys
    assert "webfig_https" in keys
    assert "ssh" not in keys
    # Stable shape: (key, port, protocol).
    for k, p, proto in out:
        assert isinstance(k, str)
        assert isinstance(p, int)
        assert proto == "tcp"


def test_safe_source_cidrs_filters_private_and_invalid():
    from app.radius.services import npc_remote_access as ra
    out = ra.safe_source_cidrs([
        "8.8.8.8",
        "8.8.0.0/16",
        "10.0.0.0/8",     # private — dropped
        "0.0.0.0/0",      # blackhole — dropped
        "garbage",        # not an IP — dropped
        "  1.1.1.1  ",
    ])
    assert set(out) == {"8.8.8.8", "8.8.0.0/16", "1.1.1.1"}


# ─── assess_policy ───────────────────────────────────────────


def test_assess_blocks_when_no_services_chosen():
    from app.radius.services import npc_remote_access as ra
    a = ra.assess_policy(now=_NOW)
    assert not a.is_applicable
    assert any("خدمة" in r for r in a.blockers_ar)


def test_assess_blocks_when_no_source_and_no_expiry():
    from app.radius.services import npc_remote_access as ra
    a = ra.assess_policy(allow_winbox=True, now=_NOW)
    assert not a.is_applicable
    assert any("مصدر" in r or "انتهاء" in r
               for r in a.blockers_ar)


def test_assess_allows_winbox_with_expiry_only():
    from app.radius.services import npc_remote_access as ra
    exp = (_NOW + timedelta(hours=2)).isoformat()
    a = ra.assess_policy(allow_winbox=True, expires_at=exp,
                          now=_NOW)
    assert a.is_applicable
    # Risk is at least medium when no source-IP allowlist set.
    assert a.risk in {"medium", "high"}


def test_assess_allows_webfig_https_with_source_list():
    from app.radius.services import npc_remote_access as ra
    a = ra.assess_policy(allow_webfig_https=True,
                          source_address_list="ops-bastion",
                          now=_NOW)
    assert a.is_applicable
    # Soft warning that there is no expiry.
    assert any("انتهاء" in w for w in a.warnings_ar)


def test_assess_picks_highest_risk_across_enabled_services():
    from app.radius.services import npc_remote_access as ra
    exp = (_NOW + timedelta(hours=1)).isoformat()
    a = ra.assess_policy(
        allow_winbox=True,       # high
        allow_webfig_https=True, # low
        expires_at=exp,
        source_address_list="ops",
        now=_NOW,
    )
    assert a.is_applicable
    assert a.risk == "high"


def test_assess_warning_for_api_without_source_allowlist():
    from app.radius.services import npc_remote_access as ra
    exp = (_NOW + timedelta(hours=1)).isoformat()
    a = ra.assess_policy(
        allow_api=True, expires_at=exp, now=_NOW,
    )
    assert a.is_applicable
    assert any("API" in w or "للإنترنت" in w
               for w in a.warnings_ar)
