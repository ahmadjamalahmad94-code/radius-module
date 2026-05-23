"""NPC Phase E — beginner explainer."""
from __future__ import annotations

from app.radius.services import (
    npc_beginner_explainer as be,
    npc_remote_access_planner as ra_planner,
    npc_walled_garden_planner as wg_planner,
    npc_web_block_planner as wb_planner,
)


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_beginner_explainer as m
    importlib.reload(m)
    assert "firewall" in m.GLOSSARY
    assert callable(m.explain)


# ─── Helpers ────────────────────────────────────────────────


def _wb_policy(**kw):
    base = {"id": 7, "tenant_id": 1, "router_id": 1,
            "name": "x", "slug": "x", "scope": "all_users",
            "schedule_id": "", "fail_open": 1, "enabled": 1}
    base.update(kw)
    return base


def _wb_target(value):
    return {"value": value,
            "normalized_value": value.lower(),
            "target_type": "domain",
            "category": "x", "status": "active"}


# ─── Glossary surface ───────────────────────────────────────


def test_glossary_terms_are_arabic():
    for term, entry in be.GLOSSARY.items():
        assert entry["label_ar"]
        assert entry["explanation_ar"]
        assert any(ord(c) > 127 for c in entry["label_ar"])


# ─── Per-service simple prose ───────────────────────────────


def test_web_block_simple_prose_present_and_arabic():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("a.com")])
    out = be.explain(policy_type="web_block", plan=plan,
                      policy=policy)
    assert out.simple_ar
    assert any(ord(c) > 127 for c in out.simple_ar)
    # No raw RouterOS in the simple prose.
    assert "/ip/firewall" not in out.simple_ar


def test_remote_access_simple_prose_mentions_admin():
    policy = {"id": 9, "tenant_id": 1, "router_id": 2,
              "name": "r", "slug": "r",
              "allow_winbox": 1, "allow_ssh": 0,
              "allow_api": 0, "allow_api_ssl": 0,
              "allow_webfig_http": 0, "allow_webfig_https": 1,
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "reason": "", "enabled": 1}
    plan = ra_planner.plan(policy)
    out = be.explain(policy_type="remote_access",
                      plan=plan, policy=policy)
    # The template uses "للمسؤولين" (with ل prefix) — match
    # the bare noun to be tolerant of the article fusion.
    assert "مسؤولين" in out.simple_ar


# ─── Glossary picker ────────────────────────────────────────


def test_glossary_only_contains_terms_used_in_plan():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("a.com")])
    out = be.explain(policy_type="web_block", plan=plan,
                      policy=policy)
    terms = {g.term for g in out.glossary}
    # web_block plan uses address-list + forward-chain + drop.
    assert "address-list" in terms
    assert "forward-chain" in terms
    assert "drop" in terms
    # No remote-access term should appear for a web_block plan.
    assert "remote-access" not in terms


def test_remote_access_glossary_includes_input_chain_and_scheduler():
    policy = {"id": 9, "tenant_id": 1, "router_id": 2,
              "name": "r", "slug": "r",
              "allow_winbox": 1, "allow_ssh": 0,
              "allow_api": 0, "allow_api_ssl": 0,
              "allow_webfig_http": 0, "allow_webfig_https": 1,
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "reason": "", "enabled": 1}
    plan = ra_planner.plan(policy)
    out = be.explain(policy_type="remote_access",
                      plan=plan, policy=policy)
    terms = {g.term for g in out.glossary}
    assert "input-chain" in terms
    assert "scheduler" in terms
    assert "accept" in terms
    assert "remote-access" in terms


def test_walled_garden_glossary_includes_hotspot_terms():
    policy = {"id": 5, "tenant_id": 1, "router_id": 1,
              "name": "g", "slug": "g",
              "hotspot_profile": "hsprof1", "enabled": 1}
    entry = {"value": "api.x.test",
              "normalized_value": "api.x.test",
              "entry_type": "dst_host", "status": "active",
              "dst_port": "", "protocol": ""}
    plan = wg_planner.plan(policy, [entry])
    out = be.explain(policy_type="walled_garden",
                      plan=plan, policy=policy)
    terms = {g.term for g in out.glossary}
    assert "hotspot" in terms
    assert "walled-garden" in terms


# ─── Operator notes ─────────────────────────────────────────


def test_operator_notes_include_dry_run_reminder():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("a.com")])
    out = be.explain(policy_type="web_block", plan=plan,
                      policy=policy)
    notes = "\n".join(out.operator_notes_ar)
    assert "معاينة" in notes


def test_remote_access_notes_warn_about_admin_channel():
    policy = {"id": 9, "tenant_id": 1, "router_id": 2,
              "name": "r", "slug": "r",
              "allow_winbox": 1, "allow_ssh": 0,
              "allow_api": 0, "allow_api_ssl": 0,
              "allow_webfig_http": 0, "allow_webfig_https": 1,
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "reason": "", "enabled": 1}
    plan = ra_planner.plan(policy)
    out = be.explain(policy_type="remote_access",
                      plan=plan, policy=policy)
    notes = "\n".join(out.operator_notes_ar)
    assert "قناة إدارية" in notes


# ─── JSON projection ─────────────────────────────────────────


def test_as_dict_json_friendly():
    policy = _wb_policy()
    plan = wb_planner.plan(policy, [_wb_target("a.com")])
    out = be.explain(policy_type="web_block", plan=plan,
                      policy=policy)
    d = out.as_dict()
    assert isinstance(d["glossary"], list)
    assert isinstance(d["operator_notes_ar"], list)
    assert d["simple_ar"]
    assert set(d["glossary"][0].keys()) == {
        "term", "label_ar", "explanation_ar",
    }
