"""NPC Phase B — conflict detector."""
from __future__ import annotations

from app.radius.services import npc_conflict_detector as cd
from app.radius.services.npc_conflict_detector import PeerPolicy


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_conflict_detector as m
    importlib.reload(m)
    assert m.SEV_LOW == "low"
    assert callable(m.analyze)


def _peer(**kw):
    base = {"service": "web_block", "id": 99,
            "name": "peer", "slug": "peer",
            "router_id": 1, "enabled": True,
            "hotspot_profile": "", "children": ()}
    base.update(kw)
    return PeerPolicy(**base)


def _t(value, status="active"):
    return {"value": value,
            "normalized_value": value.lower(),
            "target_type": "domain",
            "category": "x", "status": status}


def _e(value, entry_type="dst_host", status="active"):
    return {"value": value,
            "normalized_value": value.lower(),
            "entry_type": entry_type, "status": status}


# ─── no conflicts → empty list ───────────────────────────────


def test_no_conflicts_for_isolated_policy():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "t",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[_t("tiktok.com")],
        peers=[],
    )
    assert not out.has_conflicts
    assert out.severity == cd.SEV_LOW
    assert out.conflicts == ()


# ─── block_vs_allow ──────────────────────────────────────────


def test_block_vs_allow_detected_high():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "block-tt",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[_t("tiktok.com"), _t("facebook.com")],
        peers=[_peer(
            service="walled_garden", id=99,
            name="allow-tt", slug="allow-tt",
            router_id=1,
            children=(_e("TIKTOK.COM"),),
        )],
    )
    assert out.has_conflicts
    assert out.severity == cd.SEV_HIGH
    kinds = {c.kind for c in out.conflicts}
    assert "block_vs_allow" in kinds
    # Reverse direction also detected.
    rev = cd.analyze(
        current_service="walled_garden",
        current_policy={"id": 5, "slug": "x", "router_id": 1,
                         "hotspot_profile": ""},
        current_children=[_e("tiktok.com")],
        peers=[_peer(
            service="web_block", id=8, name="b", slug="b",
            router_id=1, children=(_t("tiktok.com"),),
        )],
    )
    assert rev.has_conflicts
    assert any(c.kind == "block_vs_allow"
               for c in rev.conflicts)


def test_block_vs_allow_ignores_different_routers():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "x", "router_id": 1,
                         "hotspot_profile": ""},
        current_children=[_t("tiktok.com")],
        peers=[_peer(
            service="walled_garden", id=99,
            router_id=2,  # different router
            children=(_e("tiktok.com"),),
        )],
    )
    assert not out.has_conflicts


# ─── duplicate_policy ────────────────────────────────────────


def test_duplicate_policy_same_slug_same_router():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "tiktok",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[],
        peers=[_peer(id=99, slug="tiktok", router_id=1)],
    )
    assert out.severity == cd.SEV_HIGH
    assert any(c.kind == "duplicate_policy"
               for c in out.conflicts)


def test_duplicate_policy_ignores_self():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "tiktok",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[],
        peers=[_peer(id=7, slug="tiktok", router_id=1)],
    )
    assert not any(c.kind == "duplicate_policy"
                   for c in out.conflicts)


# ─── overlapping_router ──────────────────────────────────────


def test_overlapping_router_same_service_medium():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "a",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[],
        peers=[_peer(id=99, slug="b", router_id=1)],
    )
    # 99 is web_block, same router → overlapping_router.
    assert any(c.kind == "overlapping_router"
               for c in out.conflicts)


def test_overlapping_router_skips_disabled_peers():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "a",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[],
        peers=[_peer(id=99, slug="b", router_id=1,
                      enabled=False)],
    )
    assert not any(c.kind == "overlapping_router"
                   for c in out.conflicts)


# ─── overlapping_target / overlapping_entry ──────────────────


def test_overlapping_target_low_severity():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "a",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[_t("tiktok.com")],
        peers=[_peer(id=99, slug="b", router_id=1,
                      children=(_t("tiktok.com"),))],
    )
    overlapping = [c for c in out.conflicts
                    if c.kind == "overlapping_target"]
    assert overlapping
    assert overlapping[0].severity == cd.SEV_LOW


# ─── hotspot_profile_overlap ────────────────────────────────


def test_hotspot_profile_overlap_medium():
    out = cd.analyze(
        current_service="walled_garden",
        current_policy={"id": 5, "slug": "a",
                         "router_id": 1,
                         "hotspot_profile": "hsprof1"},
        current_children=[],
        peers=[_peer(
            service="walled_garden", id=99,
            slug="b", router_id=1,
            hotspot_profile="hsprof1",
        )],
    )
    assert any(c.kind == "hotspot_profile"
               for c in out.conflicts)
    sev = next(c.severity for c in out.conflicts
                if c.kind == "hotspot_profile")
    assert sev == cd.SEV_MEDIUM


def test_hotspot_profile_only_walled_garden():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "a", "router_id": 1,
                         "hotspot_profile": "hsprof1"},
        current_children=[],
        peers=[_peer(service="walled_garden",
                      hotspot_profile="hsprof1",
                      router_id=1)],
    )
    assert not any(c.kind == "hotspot_profile"
                   for c in out.conflicts)


# ─── Severity is max across conflicts ────────────────────────


def test_severity_is_max_across_conflicts():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "block-tt",
                         "router_id": 1, "hotspot_profile": ""},
        current_children=[_t("tiktok.com")],
        peers=[
            # one low (same target in another web_block)
            _peer(id=98, slug="other", router_id=1,
                  children=(_t("tiktok.com"),)),
            # one high (block_vs_allow with walled_garden)
            _peer(service="walled_garden", id=99,
                  slug="g", router_id=1,
                  children=(_e("tiktok.com"),)),
        ],
    )
    assert out.severity == cd.SEV_HIGH


# ─── JSON projection ─────────────────────────────────────────


def test_as_dict_is_json_friendly():
    out = cd.analyze(
        current_service="web_block",
        current_policy={"id": 7, "slug": "tt", "router_id": 1,
                         "hotspot_profile": ""},
        current_children=[_t("tiktok.com")],
        peers=[_peer(service="walled_garden", id=99,
                      slug="g", router_id=1,
                      children=(_e("tiktok.com"),))],
    )
    d = out.as_dict()
    assert d["has_conflicts"] is True
    assert d["severity"] in (cd.SEV_LOW, cd.SEV_MEDIUM,
                              cd.SEV_HIGH)
    assert isinstance(d["conflicts"], list)
    assert all(isinstance(c["policy_id"], int)
               for c in d["conflicts"])
