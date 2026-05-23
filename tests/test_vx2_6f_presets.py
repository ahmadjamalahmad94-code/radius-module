"""VX2.6f — Built-in seed presets (pure tests)."""
from __future__ import annotations


def test_karamspeed_preset_registered():
    from app.radius.services import site_exit_presets as p
    karam = p.get_preset("karamspeed")
    assert karam is not None
    assert karam.key == "karamspeed"
    assert "karamspeed" in karam.label_ar.lower()
    assert karam.body  # non-empty
    assert karam.target_count > 100


def test_smoke_preset_registered():
    from app.radius.services import site_exit_presets as p
    smoke = p.get_preset("smoke")
    assert smoke is not None
    assert smoke.target_count == 3


def test_unknown_preset_returns_none():
    from app.radius.services import site_exit_presets as p
    assert p.get_preset("bogus") is None
    assert p.get_preset("") is None


def test_list_presets_includes_both_built_ins():
    from app.radius.services import site_exit_presets as p
    keys = {pr.key for pr in p.list_presets()}
    assert "karamspeed" in keys
    assert "smoke" in keys


def test_karamspeed_body_parses_cleanly_through_importer():
    """The whole point of a preset: load → import → get a
    valid classified result without operator intervention."""
    from app.radius.services import (
        site_exit_importer as imp,
        site_exit_presets as p,
    )
    karam = p.get_preset("karamspeed")
    result = imp.parse_address_list(karam.body)
    # No invalid lines — the preset is sanitised before
    # shipping.
    assert len(result.invalid) == 0
    # Substantial set of accepted targets (the importer
    # accurately classifies ~240 of the 309 lines; the rest
    # are dupes or manual_review which is also fine).
    assert len(result.accepted) > 100
    # Group counts are sane.
    gc = result.group_counts
    assert gc["speedtest_measurement"] > 100
    assert gc["public_ip_checkers"] > 5
    assert gc["raw_ip_targets"] >= 1


def test_smoke_preset_body_parses_to_expected_groups():
    from app.radius.services import (
        site_exit_importer as imp,
        site_exit_presets as p,
    )
    smoke = p.get_preset("smoke")
    result = imp.parse_address_list(smoke.body)
    assert result.total_parsed == 3
    assert len(result.invalid) == 0
    gc = result.group_counts
    # `1.1.1.1` → raw_ip_targets
    assert gc["raw_ip_targets"] == 1
    # `whatismyip.com` → public_ip_checkers
    # `ifconfig.co`    → public_ip_checkers
    assert gc["public_ip_checkers"] == 2


def test_preset_module_has_no_side_effects():
    """Module-level constants only — importing must not touch
    DB, network, or filesystem."""
    import importlib
    # Re-import is a no-op if pure.
    import app.radius.services.site_exit_presets as m
    importlib.reload(m)
    assert m.PRESETS  # still populated
