"""Per-customer OPT-IN self-update — RADIUS-MODULE side.

Covers:
  * semver compare — flags "update available" when remote > local, not when
    equal/older; min_version floor.
  * cumulative changelog spanning current→latest (releases[]).
  * min_version → blocked direct jump + intermediate target.
  * endpoint missing/unreachable degrades silently (no banner, no crash).
  * confirm writes a well-formed update-request marker.
  * status poll renders queued/running/success/failed.
  * the «تحديث النظام» page + request/status routes (owner-gated).
  * the safe markdown renderer.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest


# ── pure logic (no app/DB) ────────────────────────────────────────────
def test_semver_is_newer():
    from app.radius.core import app_version as v
    assert v.is_newer("1.1.0", "1.0.0") is True
    assert v.is_newer("2.0.0", "1.9.9") is True
    assert v.is_newer("1.0.1", "1.0.0") is True
    # equal / older → NOT newer
    assert v.is_newer("1.0.0", "1.0.0") is False
    assert v.is_newer("1.0.0", "1.2.0") is False
    # unparseable (e.g. a git SHA) → fail-safe False (no false banner)
    assert v.is_newer("abc123", "1.0.0") is False
    assert v.is_newer("1.1.0", "deadbeef") is False


def test_semver_meets_min():
    from app.radius.core import app_version as v
    assert v.meets_min("1.2.0", "1.2.0") is True
    assert v.meets_min("1.3.0", "1.2.0") is True
    assert v.meets_min("1.1.0", "1.2.0") is False       # below floor
    assert v.meets_min("1.0.0", "") is True             # unknown floor → ok
    assert v.meets_min("1.0.0", None) is True


def test_markdown_lite_renders_and_escapes():
    from app.radius.core import markdown_lite as md
    html = str(md.render("# Title\n\n- one\n- two\n\n**bold** and `code`"))
    assert "<h1>Title</h1>" in html
    assert "<li>one</li>" in html and "<li>two</li>" in html
    assert "<strong>bold</strong>" in html and "<code>code</code>" in html
    # XSS is escaped, never emitted raw
    evil = str(md.render("<script>alert(1)</script>"))
    assert "<script>" not in evil
    assert "&lt;script&gt;" in evil


# ── app fixture (temp DB) ─────────────────────────────────────────────
@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_selfupd_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    # Isolate the host-mounted marker dir into the temp dir.
    monkeypatch.setenv("HOBERADIUS_UPDATE_DIR", os.path.join(tmp, "updir"))
    # Deterministic running version.
    monkeypatch.setenv("HOBERADIUS_VERSION", "1.0.0")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
    yield app
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _super(client):
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
        s["_csrf_token"] = "tk"


# ── check_for_update ──────────────────────────────────────────────────
def test_check_flags_available_when_remote_newer(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        monkeypatch.setattr(su, "_fetch_latest", lambda tid: {
            "ok": True,
            "payload": {
                "version": "1.2.0",
                "released_at": "2026-07-01",
                "changelog_md": "## 1.2.0\n- new feature",
                "mandatory": False,
                "min_version": "1.0.0",
            },
        })
        state = su.check_for_update(1)
        assert state["ok"] is True
        assert state["available"] is True
        assert state["latest"] == "1.2.0"
        assert state["blocked_direct_jump"] is False
        assert state["target_version"] == "1.2.0"
        # cached
        assert su.get_cached_state(1)["available"] is True


def test_check_not_available_when_equal_or_older(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        for remote in ("1.0.0", "0.9.0"):
            monkeypatch.setattr(su, "_fetch_latest", lambda tid, r=remote: {
                "ok": True, "payload": {"version": r},
            })
            state = su.check_for_update(1)
            assert state["ok"] is True
            assert state["available"] is False, remote


def test_endpoint_missing_degrades_silently(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        monkeypatch.setattr(su, "_fetch_latest",
                            lambda tid: {"ok": False, "reason": "not_found"})
        state = su.check_for_update(1)          # must NOT raise
        assert state["available"] is False
        assert state["ok"] is False
        assert state["reason"] == "not_found"


def test_unreachable_degrades_silently(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        monkeypatch.setattr(su, "_fetch_latest",
                            lambda tid: {"ok": False, "reason": "unreachable"})
        state = su.check_for_update(1)
        assert state["available"] is False and state["ok"] is False


def test_cumulative_changelog_spans_current_to_latest(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        # current is 1.0.0; releases 1.1, 1.2, 1.3 — customer skipped 1.1 & 1.2.
        monkeypatch.setattr(su, "_fetch_latest", lambda tid: {
            "ok": True,
            "payload": {
                "version": "1.3.0",
                "min_version": "1.0.0",
                "changelog_md": "only latest notes",
                "releases": [
                    {"version": "1.1.0", "changelog_md": "one-one note"},
                    {"version": "1.2.0", "changelog_md": "one-two note"},
                    {"version": "1.3.0", "changelog_md": "one-three note"},
                ],
            },
        })
        state = su.check_for_update(1)
        cl = state["changelog_md"]
        # All skipped releases present, oldest→newest.
        assert "one-one note" in cl and "one-two note" in cl and "one-three note" in cl
        assert cl.index("one-one note") < cl.index("one-two note") < cl.index("one-three note")


def test_min_version_blocks_direct_jump(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        # current 1.0.0 is BELOW the latest's min_version 1.2.0 → blocked.
        monkeypatch.setattr(su, "_fetch_latest", lambda tid: {
            "ok": True,
            "payload": {"version": "1.5.0", "min_version": "1.2.0"},
        })
        state = su.check_for_update(1)
        assert state["available"] is True
        assert state["below_min"] is True
        assert state["blocked_direct_jump"] is True
        # target is the intermediate step, NOT the latest
        assert state["target_version"] == "1.2.0"


# ── request marker ────────────────────────────────────────────────────
def test_request_writes_well_formed_marker(app):
    with app.app_context():
        from app.radius.services import self_update as su
        res = su.request_update(1, requested_version="1.2.0",
                                requested_by=7, actor="owner")
        assert res["ok"] is True
        path = su.request_path()
        assert path.exists()
        marker = json.loads(path.read_text(encoding="utf-8"))
        assert marker["requested_version"] == "1.2.0"
        assert marker["requested_by"] == 7
        assert marker["requested_by_name"] == "owner"
        assert marker["current_version"] == "1.0.0"
        assert marker["requested_at"]           # non-empty timestamp
        assert marker["marker_schema"] == 1
        # audit row recorded
        events = su.recent_events(1)
        assert any(e["event"] == "requested" and e["to_version"] == "1.2.0"
                   for e in events)


# ── status poll ───────────────────────────────────────────────────────
def _write_marker(su, name, data):
    p = su.update_dir() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def test_progress_queued_running_success_failed(app):
    with app.app_context():
        from app.radius.services import self_update as su

        # No markers → idle
        assert su.get_progress(1)["state"] == "idle"

        # Request written, agent hasn't responded → queued
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0", "requested_at": "T1"})
        assert su.get_progress(1)["state"] == "queued"

        # Agent reports running for THIS request → running
        _write_marker(su, su.STATUS_FILENAME,
                      {"state": "running", "request_at": "T1", "log": "building"})
        prog = su.get_progress(1)
        assert prog["state"] == "running"
        assert prog["log"] == "building"

        # Success for THIS request
        _write_marker(su, su.STATUS_FILENAME,
                      {"state": "success", "request_at": "T1", "finished_at": "T2"})
        assert su.get_progress(1)["state"] == "success"

        # Failed
        _write_marker(su, su.STATUS_FILENAME,
                      {"state": "failed", "request_at": "T1", "log": "boom"})
        prog = su.get_progress(1)
        assert prog["state"] == "failed" and prog["log"] == "boom"


def test_progress_stale_status_is_queued(app):
    """A status for an OLDER request must read as queued, not success."""
    with app.app_context():
        from app.radius.services import self_update as su
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "2.0.0", "requested_at": "NEW"})
        _write_marker(su, su.STATUS_FILENAME,
                      {"state": "success", "request_at": "OLD"})
        assert su.get_progress(1)["state"] == "queued"


# ── routes (owner-gated) ──────────────────────────────────────────────
def test_page_renders_for_super(app):
    c = app.test_client()
    _super(c)
    res = c.get("/admin/radius/system/update")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "تحديث النظام" in body
    assert "1.0.0" in body          # current version shown


def test_page_forbidden_for_non_super(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s["admin_id"] = 99
        s["is_super_admin"] = False
        s["tenant_id"] = 1
    res = c.get("/admin/radius/system/update")
    assert res.status_code == 403


def test_request_route_writes_marker(app, monkeypatch):
    # Seed an "available" cached state so the request is authorised.
    with app.app_context():
        from app.radius.services import self_update as su
        monkeypatch.setattr(su, "_fetch_latest", lambda tid: {
            "ok": True, "payload": {"version": "1.4.0", "min_version": "1.0.0"},
        })
        su.check_for_update(1)

    c = app.test_client()
    _super(c)
    res = c.post("/admin/radius/system/update/request",
                 headers={"X-Requested-With": "XMLHttpRequest",
                          "X-CSRFToken": "tk", "Accept": "application/json"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["request"]["requested_version"] == "1.4.0"

    # status route now reports queued/running
    res2 = c.get("/admin/radius/system/update/status",
                 headers={"Accept": "application/json"})
    assert res2.status_code == 200
    assert res2.get_json()["progress"]["state"] in {"queued", "running"}


def test_request_rejected_when_no_update(app):
    c = app.test_client()
    _super(c)
    res = c.post("/admin/radius/system/update/request",
                 headers={"X-Requested-With": "XMLHttpRequest",
                          "X-CSRFToken": "tk", "Accept": "application/json"})
    assert res.status_code == 409
    assert res.get_json()["ok"] is False
