"""O2 — Router health scoring service.

Pure-function tests over synthetic RouterOverview values. The
scoring is deterministic — no fixtures, no DB.
"""
from __future__ import annotations

import pytest


def _ov(**overrides):
    """Build a minimal RouterOverview for scoring tests."""
    from app.radius.services.mt_router_overview import RouterOverview
    base = dict(
        nas_id=1, name="t", address="10.0.0.1",
        enabled=True, connection_mode="direct", vpn_peer_address="",
        has_snapshot=True,
        snapshot_age_seconds=60,
        snapshot_last_success_at="2026-05-22T18:00:00Z",
        snapshot_last_error="",
        snapshot_status="fresh",
        counters={}, resource={},
        active_alerts_critical=0,
        active_alerts_warning=0,
        active_alerts_info=0,
        active_alerts_total=0,
        has_backup=True,
        last_backup_at="2026-05-21T12:00:00Z",
        last_backup_status="success",
        backup_age_seconds=3600,
        backup_status="fresh",
        last_audit_at="", last_audit_action="",
        last_audit_actor="", last_audit_severity="",
        last_audit_result="", last_audit_id=None,
        last_failed_at="", last_failed_action="", last_failed_id=None,
        last_danger_at="", last_danger_action="", last_danger_id=None,
        safe_to_modify=True, safety_reasons=[],
        suggested_actions=[],
    )
    base.update(overrides)
    return RouterOverview(**base)


# ─── States ──────────────────────────────────────────────────


def test_healthy_state():
    from app.radius.services.mt_health_score import (
        score_health, STATE_HEALTHY,
    )
    h = score_health(_ov())
    assert h.state == STATE_HEALTHY
    assert h.score == 100
    assert h.primary_signal == "ok"


def test_disabled_router_is_offline():
    from app.radius.services.mt_health_score import (
        score_health, STATE_OFFLINE,
    )
    h = score_health(_ov(enabled=False))
    assert h.state == STATE_OFFLINE
    assert h.score == 0
    assert h.primary_signal == "disabled"


def test_failed_snapshot_pushes_to_offline():
    from app.radius.services.mt_health_score import (
        score_health, STATE_OFFLINE,
    )
    h = score_health(_ov(snapshot_status="failed", has_snapshot=False))
    assert h.state == STATE_OFFLINE


def test_critical_alert_pushes_to_risky():
    from app.radius.services.mt_health_score import (
        score_health, STATE_RISKY,
    )
    h = score_health(_ov(active_alerts_critical=1,
                          active_alerts_total=1))
    assert h.state == STATE_RISKY
    assert h.primary_signal == "critical_alert"
    assert h.score < 100


def test_partial_apply_pushes_to_risky():
    from app.radius.services.mt_health_score import (
        score_health, STATE_RISKY,
    )
    h = score_health(_ov(last_audit_result="partial",
                          last_audit_action="mt.programming.apply",
                          last_audit_id=42))
    assert h.state == STATE_RISKY
    assert "غير متّسقة" in " ".join(h.reasons)


def test_stale_backup_only_is_attention():
    from app.radius.services.mt_health_score import (
        score_health, STATE_ATTENTION,
    )
    h = score_health(_ov(backup_status="stale",
                          backup_age_seconds=10 * 24 * 3600))
    assert h.state == STATE_ATTENTION


def test_missing_backup_only_is_attention():
    from app.radius.services.mt_health_score import (
        score_health, STATE_ATTENTION,
    )
    h = score_health(_ov(backup_status="missing",
                          has_backup=False))
    assert h.state == STATE_ATTENTION
    assert h.primary_signal == "missing_backup"


def test_warning_alert_is_attention_not_risky():
    from app.radius.services.mt_health_score import (
        score_health, STATE_ATTENTION,
    )
    h = score_health(_ov(active_alerts_warning=2,
                          active_alerts_total=2))
    assert h.state == STATE_ATTENTION


def test_unknown_signal_for_truly_fresh_router():
    """A brand-new router with NO data anywhere → primary signal
    is 'no_data'. Even ATTENTION is acceptable as final state
    because missing backup nudges out of UNKNOWN; what matters
    is the primary_signal flag so the topology page can group
    "needs first scan" routers."""
    from app.radius.services.mt_health_score import (
        score_health, STATE_ATTENTION, STATE_UNKNOWN,
    )
    h = score_health(_ov(
        has_snapshot=False, snapshot_status="unknown",
        last_audit_id=None,
        backup_status="missing", has_backup=False,
    ))
    assert h.state in {STATE_UNKNOWN, STATE_ATTENTION}
    assert h.primary_signal == "no_data"


def test_none_input_returns_unknown_state():
    from app.radius.services.mt_health_score import (
        score_health, STATE_UNKNOWN,
    )
    h = score_health(None)
    assert h.state == STATE_UNKNOWN


# ─── Fold + ordering ─────────────────────────────────────────


def test_critical_alert_wins_over_warning():
    """Both signals fire; the worse state takes the verdict."""
    from app.radius.services.mt_health_score import (
        score_health, STATE_RISKY,
    )
    h = score_health(_ov(
        active_alerts_critical=1,
        active_alerts_warning=3,
        active_alerts_total=4,
    ))
    assert h.state == STATE_RISKY


def test_disabled_skips_other_signals():
    """A disabled router short-circuits — only the disabled
    reason is reported (other signals don't matter when the
    router isn't running)."""
    from app.radius.services.mt_health_score import (
        score_health,
    )
    h = score_health(_ov(
        enabled=False,
        active_alerts_critical=99,
        backup_status="missing",
    ))
    assert h.primary_signal == "disabled"
    assert len(h.reasons) == 1


def test_recent_failure_lowers_score_and_recommends_review():
    from app.radius.services.mt_health_score import score_health
    h = score_health(_ov(
        last_failed_id=42,
        last_failed_action="mt.programming.apply",
    ))
    assert h.score < 100
    assert "فاشلة" in h.recommended_action_ar


# ─── Recommended action text ─────────────────────────────────


def test_healthy_recommendation_is_no_action():
    from app.radius.services.mt_health_score import score_health
    h = score_health(_ov())
    assert "لا إجراء" in h.recommended_action_ar


def test_critical_alert_recommendation_points_to_alerts():
    from app.radius.services.mt_health_score import score_health
    h = score_health(_ov(active_alerts_critical=1,
                          active_alerts_total=1))
    assert "التنبيهات" in h.recommended_action_ar


def test_partial_apply_recommendation_mentions_unprogram():
    from app.radius.services.mt_health_score import score_health
    h = score_health(_ov(last_audit_result="partial",
                          last_audit_id=42))
    text = h.recommended_action_ar
    assert "تراجع" in text or "Unprogram" in text


# ─── to_dict shape ───────────────────────────────────────────


def test_to_dict_carries_state_score_reasons_signal():
    from app.radius.services.mt_health_score import score_health
    h = score_health(_ov(active_alerts_critical=1,
                          active_alerts_total=1))
    d = h.to_dict()
    assert d["state"] == "risky"
    assert 0 <= d["score"] <= 100
    assert isinstance(d["reasons"], list)
    assert d["primary_signal"] == "critical_alert"


# ─── Wiring into the route ───────────────────────────────────


def test_overview_route_renders_health_pill(app, client):
    """The route now passes the health score to the template;
    pin the marker so a future refactor that drops it is caught.
    """
    _seed_nas(app, nas_id=20)
    _login(client)
    html = client.get("/admin/radius/mt/20/overview").get_data(as_text=True)
    assert "data-mt-health-pill" in html
    assert 'data-mt-health-state="' in html
    assert 'data-mt-health-score="' in html
    assert 'data-mt-health-recommendation' in html


# ─── Light fixtures for the wiring test (deliberately mirror
#      the O1 test fixtures so this file stays independent) ──


import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_o2_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
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


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"o2_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="o2-pass", full_name="O2",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "o2-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _seed_nas(app, *, nas_id):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           1, ?, 'direct')""",
                (nas_id, f"o2-rtr-{nas_id}", f"203.0.113.{nas_id}", now),
            )
