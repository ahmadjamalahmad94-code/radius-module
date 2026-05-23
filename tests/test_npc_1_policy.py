"""NPC Phase 1 — `npc_policy` shared helpers (pure tests)."""
from __future__ import annotations

import pytest


def test_module_has_no_side_effects_on_import():
    import importlib
    import app.radius.services.npc_policy as m
    importlib.reload(m)
    assert m.PREFIX_ROOT == "HOBE_NPC"
    assert callable(m.comment_prefix)


# ─── comment_prefix ──────────────────────────────────────────


def test_comment_prefix_shapes_for_each_service():
    from app.radius.services import npc_policy as p
    assert p.comment_prefix("remote_access", 42) == "HOBE_NPC_REMOTE:42:"
    assert p.comment_prefix("web_block",      7) == "HOBE_NPC_BLOCK:7:"
    assert p.comment_prefix("walled_garden",  9) == "HOBE_NPC_WG:9:"


def test_comment_prefix_rejects_unknown_service():
    from app.radius.services import npc_policy as p
    with pytest.raises(ValueError):
        p.comment_prefix("bogus_service", 1)


def test_comment_prefix_rejects_non_positive_id():
    from app.radius.services import npc_policy as p
    with pytest.raises(ValueError):
        p.comment_prefix("web_block", 0)
    with pytest.raises(ValueError):
        p.comment_prefix("web_block", -1)


def test_cleanup_regex_is_anchored_to_prefix():
    from app.radius.services import npc_policy as p
    rgx = p.cleanup_regex("web_block", 7)
    # `^` anchor mandatory — a substring match elsewhere in
    # an unrelated comment shouldn't collide.
    assert rgx.startswith("^HOBE_NPC_BLOCK:7:")


# ─── Name validation ─────────────────────────────────────────


def test_validate_name_accepts_arabic_visible_text():
    from app.radius.services import npc_policy as p
    v = p.validate_name("  حظر تيك توك  ")
    assert v.ok
    assert v.cleaned == "حظر تيك توك"


def test_validate_name_rejects_empty():
    from app.radius.services import npc_policy as p
    for raw in (None, "", "   ", "\t\n"):
        v = p.validate_name(raw)
        assert not v.ok
        assert v.reason


def test_validate_name_truncates_overlong():
    from app.radius.services import npc_policy as p
    over = "x" * (p.MAX_NAME_LEN + 5)
    v = p.validate_name(over)
    assert not v.ok
    assert v.cleaned and len(v.cleaned) == p.MAX_NAME_LEN


# ─── Lifecycle terminal ──────────────────────────────────────


def test_lifecycle_terminal_recognises_applied_failed_disabled():
    from app.radius.services import npc_policy as p
    assert p.is_lifecycle_terminal(p.LIFECYCLE_APPLIED)
    assert p.is_lifecycle_terminal(p.LIFECYCLE_FAILED)
    assert p.is_lifecycle_terminal(p.LIFECYCLE_DISABLED)
    assert not p.is_lifecycle_terminal(p.LIFECYCLE_DRAFT)
    assert not p.is_lifecycle_terminal(p.LIFECYCLE_PREVIEWED)


# ─── Per-policy cap ──────────────────────────────────────────


def test_assert_target_count_ok_passes_under_cap():
    from app.radius.services import npc_policy as p
    p.assert_target_count_ok(0)
    p.assert_target_count_ok(p.MAX_TARGETS_PER_POLICY)


def test_assert_target_count_ok_raises_over_cap():
    from app.radius.services import npc_policy as p
    with pytest.raises(ValueError):
        p.assert_target_count_ok(p.MAX_TARGETS_PER_POLICY + 1)


# ─── Category labels ─────────────────────────────────────────


def test_category_label_maps_known_and_passes_through_unknown():
    from app.radius.services import npc_policy as p
    assert p.category_label("tiktok") == "تيك توك"
    # Unknown values are echoed verbatim — never drop operator data.
    assert p.category_label("custom_xyz") == "custom_xyz"


def test_known_categories_is_stable_order():
    from app.radius.services import npc_policy as p
    first = list(p.known_categories())
    second = list(p.known_categories())
    assert first == second
    assert "tiktok" in first
    assert "custom" in first
