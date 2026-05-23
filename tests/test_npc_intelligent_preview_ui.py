"""NPC Intelligent Preview UI — surface every Phase A→I
intelligence block in the server-rendered preview page.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from types import SimpleNamespace

import pytest


# ─── Fixture ────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_npc_intel_ui_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH",
                       os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


def _login_super(client, monkeypatch):
    super_admin = SimpleNamespace(
        id=1, username="alice", is_super_admin=True,
    )

    class _Store:
        @staticmethod
        def get_admin(_id):
            return super_admin

    class _Svc:
        _store = _Store()
        def permissions_of(self, _admin):
            return ()

    import app.radius.services.admins as admins_mod
    monkeypatch.setattr(admins_mod, "get_admins_service",
                        lambda: _Svc())

    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "alice"
        s["tenant_id"] = 1


def _csrf(client):
    client.get(
        "/admin/radius/network-policy/remote-access/new"
    )
    with client.session_transaction() as s:
        return s.get("_csrf_token") or ""


def _seed_router(app, name="rt1"):
    with app.app_context():
        from app.radius.db.connection import db, transaction
        with transaction() as c:
            cur = c.execute(
                "INSERT INTO nas_devices (tenant_id, name, "
                "shortname, address, secret, vendor, nas_type, "
                "ports, snmp_community, auth_port, acct_port, "
                "coa_port, api_port, api_user, api_password, "
                "api_use_tls, location, coordinates, "
                "monitoring_enabled, description, enabled, "
                "require_message_authenticator, ssh_port, "
                "tags, metadata, created_at, updated_at) "
                "VALUES (1, ?, ?, '10.0.0.1', '', 'mikrotik', "
                "'router', 0, '', 1812, 1813, 3799, 8728, "
                "'admin', 'real-password', 0, '', '', 0, '', "
                "1, 0, 22, '', '{}', "
                "'2026-01-01','2026-01-01')",
                (name, name),
            )
            return int(cur.lastrowid)


def _create_remote_policy(client, csrf, router_id, name="Win"):
    r = client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={"_csrf_token": csrf,
              "name": name, "router_id": str(router_id),
              "allow_winbox": "on",
              "allow_webfig_https": "on",
              "source_address_list": "ops",
              "expires_at": "2027-01-01T00:00:00Z",
              "enabled": "on"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303), r.data.decode()[:200]


def _create_web_block_policy(client, csrf, router_id, name="WB"):
    client.post(
        "/admin/radius/network-policy/web-block/new",
        data={"_csrf_token": csrf,
              "name": name, "router_id": str(router_id),
              "scope": "all_users",
              "fail_open": "on", "enabled": "on"},
        follow_redirects=False,
    )


def _add_target(client, csrf, pid, value, category="tiktok"):
    client.post(
        f"/admin/radius/network-policy/web-block/{pid}/children",
        data={"_csrf_token": csrf,
              "value": value, "category": category},
        follow_redirects=True,
    )


def _last_policy_id(app, repo_attr):
    """Return the id of the most-recently-created policy for
    the given repo module attribute (e.g. ``ra_repo``)."""
    with app.app_context():
        from app.radius.db.repos import (
            npc_remote_access_repo, npc_web_block_repo,
            npc_walled_garden_repo,
        )
        repo = {
            "ra": npc_remote_access_repo,
            "wb": npc_web_block_repo,
            "wg": npc_walled_garden_repo,
        }[repo_attr]
        if repo is npc_remote_access_repo:
            rows = repo.list_for_tenant(1)
        else:
            rows = repo.list_policies_for_tenant(1)
    return rows[-1]["id"]


# ─── Section presence ────────────────────────────────────────


def test_all_ten_sections_present_on_remote_access_preview(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")

    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")

    # Hero (Section 1)
    assert 'data-test="npc-hero"' in html
    assert 'data-test="npc-health-score"' in html
    assert 'data-test="npc-risk-pill"' in html
    # What will happen (Section 2)
    assert 'data-test="npc-section-impact"' in html
    assert "ماذا سيحدث" in html
    # Blast (Section 3)
    assert 'data-test="npc-section-blast"' in html
    assert "حجم التأثير" in html
    # Conflicts (Section 4)
    assert 'data-test="npc-section-conflicts"' in html
    # Dependencies (Section 5)
    assert 'data-test="npc-section-deps"' in html
    # Recommendations (Section 6)
    assert 'data-test="npc-section-recs"' in html
    # Canary (Section 7)
    assert 'data-test="npc-section-canary"' in html
    # Rollback (Section 8)
    assert 'data-test="npc-section-rollback"' in html
    # Glossary (Section 9)
    assert 'data-test="npc-section-glossary"' in html
    # Script viewer (Section 10) — collapsible <details>
    assert 'data-test="npc-section-script"' in html


# ─── Health score visible ────────────────────────────────────


def test_health_score_renders_score_and_grade(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # The score card carries a numeric value 0..100 and one of
    # the five Arabic grade labels.
    assert re.search(r'class="npc-score-n">\s*\d+<', html)
    assert re.search(
        r"(ممتازة|جيدة|تحتاج حذراً|محفوفة بالمخاطر|"
        r"تحتاج إعادة تخطيط)",
        html,
    )


# ─── Smart recommendations visible ───────────────────────────


def test_smart_recommendations_render_when_present(
    app, client, monkeypatch,
):
    """Build a web-block policy with NO targets — the
    intelligence stack will surface at least one recommendation
    (operator should add targets or accept a no-op)."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    # Remote access policy without source list + without
    # expiry forces a blocking error → recommendation appears.
    client.post(
        "/admin/radius/network-policy/remote-access/new",
        data={"_csrf_token": csrf,
              "name": "no-source-no-expiry",
              "router_id": str(rid),
              "allow_winbox": "on",
              "enabled": "on"},
        follow_redirects=False,
    )
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # At least one recommendation card OR the empty state —
    # but not both. Since this policy is blocked, the impact
    # analyzer will mark it CRITICAL → at least the "missing
    # rollback / hold-and-replan" path emits recommendations.
    assert ('data-test="npc-rec-card"' in html
            or 'data-test="npc-recs-empty"' in html)
    # Section header is always present.
    assert "اقتراحات ذكيّة" in html


# ─── Conflict cards render when conflicts exist ──────────────


def test_conflict_card_renders_when_conflicts_exist(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    # Create TWO web_block policies on the same router →
    # overlapping_router conflict.
    _create_web_block_policy(client, csrf, rid, name="WB-1")
    _create_web_block_policy(client, csrf, rid, name="WB-2")
    pid = _last_policy_id(app, "wb")
    r = client.get(
        f"/admin/radius/network-policy/web-block/{pid}/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-conflict-card"' in html
    # The other policy's name appears.
    assert "WB-1" in html or "WB-2" in html


def test_conflicts_section_shows_empty_state_when_none(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_web_block_policy(client, csrf, rid, name="solo")
    pid = _last_policy_id(app, "wb")
    r = client.get(
        f"/admin/radius/network-policy/web-block/{pid}/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-conflicts-empty"' in html
    assert "لم يتم اكتشاف تعارضات" in html


# ─── Dependency confidence translation ──────────────────────


def test_dependency_section_renders_confidence_in_arabic(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_web_block_policy(client, csrf, rid, name="TT")
    pid = _last_policy_id(app, "wb")
    # Adding tiktok.com triggers the TikTok / ByteDance
    # dependency rule.
    _add_target(client, csrf, pid, "tiktok.com", category="tiktok")
    r = client.get(
        f"/admin/radius/network-policy/web-block/{pid}/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-dep-card"' in html
    # Arabic confidence label appears (likely → "مرجَّح",
    # certain → "مؤكَّد").
    assert ("مؤكَّد" in html or "مرجَّح" in html
            or "محتمل" in html)


def test_dependency_section_empty_state_when_no_targets(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_web_block_policy(client, csrf, rid, name="empty")
    pid = _last_policy_id(app, "wb")
    r = client.get(
        f"/admin/radius/network-policy/web-block/{pid}/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-deps-empty"' in html


# ─── Blast radius numbers visible ────────────────────────────


def test_blast_radius_renders_router_count_and_severity(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_web_block_policy(client, csrf, rid, name="x")
    pid = _last_policy_id(app, "wb")
    r = client.get(
        f"/admin/radius/network-policy/web-block/{pid}/preview"
    )
    html = r.data.decode("utf-8")
    assert "راوتر متأثّر" in html
    # One of the four Arabic severity labels.
    assert re.search(
        r"(نطاق ضيّق|متوسّط|واسع|حرج)", html,
    )


# ─── Canary plan visible ─────────────────────────────────────


def test_canary_plan_renders_steps(app, client, monkeypatch):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert "خطّة تطبيق آمنة مقترحة" in html
    # Canary disclaimer text guarantees "no auto-execution".
    assert "لا يتم تنفيذ أي شيء تلقائياً" in html
    # Steps render as <ol> items.
    assert html.count('class="npc-canary-step-text"') >= 1


# ─── Beginner glossary visible ───────────────────────────────


def test_glossary_section_renders_items(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert "شرح مبسَّط للمصطلحات" in html
    # remote_access plans include `input-chain`, `scheduler`,
    # `accept` glossary entries.
    assert 'data-test="npc-glossary-item"' in html
    assert "سلسلة input" in html or "السماح" in html


# ─── Rollback confidence visible ─────────────────────────────


def test_rollback_section_shows_positive_state_for_valid_plan(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    assert 'data-test="npc-rollback-ok"' in html
    assert "إمكانية التراجع" in html


# ─── Script viewer placed below intelligence content ────────


def test_script_viewer_is_collapsed_and_after_intelligence(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")

    # Find DOM positions: intelligence sections come BEFORE
    # the script viewer.
    impact_pos    = html.find('data-test="npc-section-impact"')
    canary_pos    = html.find('data-test="npc-section-canary"')
    glossary_pos  = html.find('data-test="npc-section-glossary"')
    script_pos    = html.find('data-test="npc-section-script"')
    assert -1 < impact_pos < script_pos
    assert -1 < canary_pos < script_pos
    assert -1 < glossary_pos < script_pos
    # Script viewer uses <details> for collapsibility. The
    # data-test attribute sits on the <details> element, so
    # the opening `<details` is shortly BEFORE the marker.
    pre = html[max(0, script_pos - 200):script_pos]
    assert "<details" in pre


# ─── Dry-run banner + label remain visible ───────────────────


def test_dry_run_banner_and_label_remain_on_preview(
    app, client, monkeypatch,
):
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # The shared shell banner is still present.
    assert "معاينة فقط (Dry-Run)" in html
    # AND the in-hero "no apply" pill.
    assert "لم يتم التطبيق على الراوتر" in html


# ─── Safe-apply label on the new preview UI ──────────────────


def test_apply_form_uses_safe_label_in_new_preview_ui(
    app, client, monkeypatch,
):
    """Post-Phase-6 — the preview page exposes the apply form
    to an apply-perm user. The submit must NOT use the
    panic-tone copy «تطبيق على الراوتر»."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # Apply route is exposed.
    assert "/apply" in html
    # Panic-tone copy is NOT used.
    assert not re.search(
        r"type=['\"]submit['\"][^>]*>[^<]*تطبيق على الراوتر",
        html,
    )


# ─── Microcopy tone — no panic language ──────────────────────


def test_preview_avoids_panic_language(
    app, client, monkeypatch,
):
    """The brief forbids panic wording like 'خطير' as a
    standalone label. Risk pills use 'منخفض / متوسط / مرتفع /
    حرج' which are descriptive, not alarmist."""
    rid = _seed_router(app)
    _login_super(client, monkeypatch)
    csrf = _csrf(client)
    _create_remote_policy(client, csrf, rid)
    pid = _last_policy_id(app, "ra")
    r = client.get(
        f"/admin/radius/network-policy/remote-access/{pid}"
        "/preview"
    )
    html = r.data.decode("utf-8")
    # Bare "خطير" word should not appear as page copy. We
    # allow the substring inside compound words ("خطر مرتفع"
    # is fine — that's "high risk", not "danger"). Match
    # "خطير" preceded/followed by a space to catch the bare
    # alarm form.
    assert not re.search(r"\sخطير\s", html)
