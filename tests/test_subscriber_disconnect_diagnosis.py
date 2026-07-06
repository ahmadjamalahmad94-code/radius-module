"""Subscriber disconnect diagnosis — verdict from Acct-Terminate-Cause.

Owner request: "how do I know who dropped the session — RADIUS or the
subscriber?" The subscriber 360 page now buckets each closed session's
terminate cause into a plain verdict.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture(scope="module")
def app():
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tempfile.mkdtemp(), "dx.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    a = create_app()
    with a.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    return a


def _svc(app):
    with app.app_context():
        from app.radius.services.subscriber_360 import Subscriber360Service
        return Subscriber360Service(tenant_id=1)


def _sess(cause, secs=600, stopped=True):
    return {
        "acctstoptime": "2026-07-06 08:40:00" if stopped else "",
        "acctsessiontime": secs,
        "acctterminatecause": cause,
    }


def test_verdict_points_at_subscriber(app):
    svc = _svc(app)
    sessions = [_sess("Lost-Carrier"), _sess("User-Request"),
                _sess("Lost-Carrier"), _sess("Idle-Timeout")]
    dx = svc._disconnect_diagnosis(sessions)
    assert dx["total"] == 4
    assert dx["verdict"]["key"] == "subscriber"     # 3/4 → dominant
    assert dx["verdict"]["pct"] == 75
    assert dx["panel_kicks"] == 0


def test_verdict_flags_panel_kicks(app):
    svc = _svc(app)
    sessions = [_sess("Device-Limit-Replace"), _sess("Device-Limit-Replace"),
                _sess("Admin-Force-Close"), _sess("User-Request")]
    dx = svc._disconnect_diagnosis(sessions)
    assert dx["verdict"]["key"] == "panel"
    assert dx["panel_kicks"] == 3                   # 2 replace + 1 force-close


def test_flapping_signal_on_short_sessions(app):
    svc = _svc(app)
    sessions = [_sess("Lost-Carrier", secs=40) for _ in range(6)]
    dx = svc._disconnect_diagnosis(sessions)
    assert dx["flapping"] is True
    assert dx["short_sessions"] == 6
    assert dx["avg_minutes"] < 1


def test_router_vs_idle_buckets(app):
    svc = _svc(app)
    sessions = [_sess("NAS-Request"), _sess("NAS-Reboot"), _sess("Idle-Timeout")]
    dx = svc._disconnect_diagnosis(sessions)
    keys = {b["key"] for b in dx["buckets"]}
    assert "router" in keys and "idle" in keys
    assert dx["verdict"]["key"] == "router"         # 2 router vs 1 idle


def test_open_sessions_are_ignored(app):
    svc = _svc(app)
    sessions = [_sess("User-Request"), _sess("", secs=0, stopped=False)]
    dx = svc._disconnect_diagnosis(sessions)
    assert dx["total"] == 1                          # the still-open one excluded


def test_empty_history_has_no_verdict(app):
    dx = _svc(app)._disconnect_diagnosis([])
    assert dx["total"] == 0
    assert dx["verdict"] is None


def test_page_renders_diagnosis_panel(app):
    """The subscriber 360 page renders the diagnosis section with a verdict
    computed from real radacct rows (end-to-end template smoke)."""
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import transaction
    from app.radius.db.repos import subscribers_repo

    with app.app_context():
        sub = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username="dxuser", password="pw", tenant_id=1, status="enabled"))
        with transaction() as conn:
            for i, cause in enumerate(
                    ["Lost-Carrier", "Lost-Carrier", "User-Request", "Idle-Timeout"]):
                conn.execute(
                    "INSERT INTO radacct(tenant_id, username, acctsessionid, "
                    "acctstarttime, acctstoptime, acctsessiontime, acctterminatecause) "
                    "VALUES(1, 'dxuser', ?, '2026-07-06 08:00:00', "
                    "'2026-07-06 08:05:00', 300, ?)", (f"s{i}", cause))
        sid = sub.id

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "preview"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    res = client.get(f"/admin/radius/subscribers/{sid}")
    assert res.status_code == 200, res.status_code
    html = res.get_data(as_text=True)
    # The diagnosis section + verdict (subscriber dominates 3/4) rendered.
    assert "تشخيص الانقطاع" in html
    assert "من طرف المشترك" in html
    # The raw-cause snapshot uses the Arabic label, not the raw code.
    assert "انقطاع الاتصال" in html
    assert "Lost-Carrier" not in html


def test_users_profile_page_has_diagnosis_tab(app):
    """The REAL subscriber page (users_profile — the one opened from the list)
    now carries the «تشخيص الانقطاع» tab + panel."""
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import transaction
    from app.radius.db.repos import subscribers_repo

    with app.app_context():
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username="dxprof", password="pw", tenant_id=1, status="enabled"))
        with transaction() as conn:
            for i, cause in enumerate(["NAS-Request", "NAS-Reboot", "Idle-Timeout"]):
                conn.execute(
                    "INSERT INTO radacct(tenant_id, username, acctsessionid, "
                    "acctstarttime, acctstoptime, acctsessiontime, acctterminatecause) "
                    "VALUES(1, 'dxprof', ?, '2026-07-06 08:00:00', "
                    "'2026-07-06 08:05:00', 300, ?)", (f"p{i}", cause))

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "preview"
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    res = client.get("/admin/radius/users/dxprof/profile")
    assert res.status_code == 200, res.status_code
    html = res.get_data(as_text=True)
    assert 'data-tab="diag"' in html          # the tab button
    assert 'data-pane="diag"' in html          # the pane
    assert "تشخيص الانقطاع" in html
    assert "من الراوتر (NAS)" in html          # verdict (2 router vs 1 idle)
