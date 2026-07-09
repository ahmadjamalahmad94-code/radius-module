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


# ── granular progress (percent / stage / log) ─────────────────────────
def test_progress_surfaces_percent_stage_and_log(app):
    with app.app_context():
        from app.radius.services import self_update as su
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0", "requested_at": "T1"})
        _write_marker(su, su.STATUS_FILENAME, {
            "state": "running", "request_at": "T1",
            "stage": "build", "stage_label": "بناء الصورة الجديدة",
            "percent": 65, "log": "line1\nline2", "updated_at": "2026-07-08T10:01:00Z",
        })
        p = su.get_progress(1)
        assert p["state"] == "running"
        assert p["percent"] == 65
        assert p["stage"] == "build" and p["stage_label"] == "بناء الصورة الجديدة"
        # un-timestamped log lines pass through unchanged
        assert p["log"] == "line1\nline2"
        # updated_at is now formatted in tenant-local tz (date kept, no raw UTC 'Z'/'T')
        assert p["updated_at"].startswith("2026-07-08")
        assert "Z" not in p["updated_at"] and "T" not in p["updated_at"]


def _pin_tz(offset_hours="5"):
    """Force a deterministic offset-only panel tz (invalid IANA → offset path),
    so the UTC→local shift is unambiguous and independent of tzdata/DST."""
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(1, "billing.timezone", "fixed-test-zone")
    tenants_repo.set_setting(1, "billing.timezone_offset", offset_hours)


def test_progress_log_and_times_are_tenant_local_no_seconds(app):
    """The live log tail + updated time render in tenant-local tz, in the clean
    site format: no 'T', no 'Z', and NO seconds (owner: «لا تجيب أجزاء الثانية»)."""
    with app.app_context():
        from app.radius.services import self_update as su
        _pin_tz("5")   # UTC+5 → 04:36Z becomes 09:36 local
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0", "requested_at": "T1"})
        _write_marker(su, su.STATUS_FILENAME, {
            "state": "running", "request_at": "T1", "percent": 40,
            "updated_at": "2026-07-09T04:36:06Z",
            "log": ("2026-07-09T04:36:04Z — بدء التحديث\n"
                    "2026-07-09T04:37:06Z — سحب التحديث وتبديل الكود"),
        })
        p = su.get_progress(1)
        # localized to +5, minutes precision
        assert "09:36 — بدء التحديث" in p["log"]
        assert "09:37 — سحب التحديث وتبديل الكود" in p["log"]
        # clean: no raw machine tokens, no seconds
        assert "Z" not in p["log"] and "T04:" not in p["log"]
        assert "09:36:04" not in p["log"] and "09:37:06" not in p["log"]
        # updated_at localized + clean (09:36, no Z/T/seconds)
        assert p["updated_at"] == "2026-07-09 09:36"


def test_progress_log_backward_compat_bare_time_no_seconds(app):
    """Old agents emit bare 'HH:MM:SSZ' — the panel localizes using the marker
    date, drops seconds, and never leaves a UTC 'Z' behind."""
    with app.app_context():
        from app.radius.services import self_update as su
        _pin_tz("5")
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0", "requested_at": "T1"})
        _write_marker(su, su.STATUS_FILENAME, {
            "state": "running", "request_at": "T1", "percent": 20,
            "updated_at": "2026-07-09T04:36:04Z",
            "log": "04:36:04Z — بدء التحديث",   # legacy bare-time format
        })
        p = su.get_progress(1)
        assert "09:36 — بدء التحديث" in p["log"]
        assert "Z" not in p["log"] and "09:36:04" not in p["log"]


def test_history_table_timestamps_clean_local(app):
    """The «سجل التحديثات» «التاريخ» cell renders a clean local datetime — no
    raw ISO ('T'…'Z'), no seconds."""
    with app.app_context():
        from app.radius.db.connection import db
        _pin_tz("5")
        db().execute(
            """INSERT INTO self_update_events
                 (tenant_id, event, from_version, to_version, state,
                  requested_by, actor, detail, created_at)
               VALUES (1,'requested','1.0.0','1.2.0','',1,'owner','','2026-07-09T04:36:22Z')""",
        )
    c = app.test_client()
    _super(c)
    body = c.get("/admin/radius/system/update").get_data(as_text=True)
    # local +5 → «2026-07-09 09:36» (minutes precision)
    assert "2026-07-09 09:36" in body
    # the raw machine string + its seconds must NOT leak into the page
    assert "2026-07-09T04:36:22Z" not in body
    assert "09:36:22" not in body


def test_progress_terminal_states_render_distinctly(app):
    with app.app_context():
        from app.radius.services import self_update as su
        # success → percent pinned to 100
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0", "requested_at": "T1"})
        _write_marker(su, su.STATUS_FILENAME, {
            "state": "success", "request_at": "T1", "percent": 100,
            "stage": "done", "finished_at": "2026-07-08T10:05:00Z",
        })
        ok = su.get_progress(1)
        assert ok["state"] == "success" and ok["percent"] == 100

        # failed → carries error + failed_stage + rolled_back, percent frozen
        _write_marker(su, su.STATUS_FILENAME, {
            "state": "failed", "request_at": "T1", "percent": 85,
            "stage": "migrations", "stage_label": "تشغيل ترحيلات قاعدة البيانات",
            "failed_stage": "migrations", "error": "duplicate column x",
            "rolled_back": True, "log": "boom",
        })
        bad = su.get_progress(1)
        assert bad["state"] == "failed"
        assert bad["percent"] == 85                      # frozen where it died
        assert bad["failed_stage"] == "migrations"
        assert bad["error"] == "duplicate column x"
        assert bad["rolled_back"] is True


def test_progress_queued_reports_waiting_seconds(app):
    """A long-queued request exposes queued_seconds so the panel can warn."""
    with app.app_context():
        from app.radius.services import self_update as su
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0",
                       "requested_at": "2000-01-01T00:00:00Z"})   # ancient
        p = su.get_progress(1)
        assert p["state"] == "queued"
        assert p["percent"] == 0
        assert p["queued_seconds"] > 60          # → panel shows the agent hint


def test_status_route_returns_granular_progress(app):
    with app.app_context():
        from app.radius.services import self_update as su
        _write_marker(su, su.REQUEST_FILENAME,
                      {"requested_version": "1.2.0", "requested_at": "T1"})
        _write_marker(su, su.STATUS_FILENAME, {
            "state": "running", "request_at": "T1",
            "stage_label": "تشغيل ترحيلات قاعدة البيانات", "percent": 85,
            "log": "applying migrations",
        })
    c = app.test_client()
    _super(c)
    prog = c.get("/admin/radius/system/update/status",
                 headers={"Accept": "application/json"}).get_json()["progress"]
    assert prog["percent"] == 85
    assert prog["stage_label"] == "تشغيل ترحيلات قاعدة البيانات"
    assert prog["log"] == "applying migrations"


def test_page_renders_progress_bar_markup(app):
    """The «جارٍ التحديث» modal ships the progress-bar + live elements."""
    c = app.test_client()
    _super(c)
    body = c.get("/admin/radius/system/update").get_data(as_text=True)
    assert 'id="su-bar-fill"' in body          # the progress bar
    assert 'id="su-stage-label"' in body       # the live «جارٍ: …» line
    assert 'id="su-queued-hint"' in body       # the waiting-for-agent hint
    assert 'id="su-log"' in body               # the live log tail


# ── routes (owner-gated) ──────────────────────────────────────────────
def test_page_renders_for_super(app):
    c = app.test_client()
    _super(c)
    res = c.get("/admin/radius/system/update")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert "تحديث النظام" in body
    assert "1.0.0" in body          # current version shown


def test_page_uses_design_system_and_unified_table(app):
    """Premium redesign: the page uses the hub design-system scaffolding
    (megahero + KPI strip, focal status banner, hub-section, hub-btn) and the
    «سجلّ التحديثات» history renders via the UNIFIED table (hub-table +
    hub-table-wrap), not a raw table."""
    with app.app_context():
        from app.radius.services import self_update as su
        # Seed a history row so the table section renders.
        su.request_update(1, requested_version="1.2.0", requested_by=1, actor="owner")
    c = app.test_client()
    _super(c)
    body = c.get("/admin/radius/system/update").get_data(as_text=True)
    # design-system scaffolding — premium hero (megahero → uds-hero) + KPI strip
    assert "uds-hero" in body
    assert "hub-kpi" in body
    assert "hub-section" in body
    assert "hub-btn" in body
    # focal status banner
    assert "su-banner" in body
    # unified table for the update history
    assert "hub-table-wrap" in body
    assert 'class="hub-table"' in body
    assert "سجلّ التحديثات" in body
    # no leftover bespoke history table
    assert "su-events" not in body


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


# ── signed-envelope fetch (the ROOT-CAUSE fix) ────────────────────────
# The self-update check must authenticate exactly like every other bridge
# call: the license envelope in the JSON BODY (not a header-only GET). These
# assert the signing is reused and every outcome maps to a distinct state.
class _FakeTransport:
    """Records the outbound request and returns/raises a canned result."""

    def __init__(self, *, response=None, raise_exc=None, capture=None):
        self._response = response if response is not None else {}
        self._raise = raise_exc
        self.capture = capture if capture is not None else {}

    def request_json(self, *, method, url, headers, json_body, timeout_seconds):
        self.capture.update(method=method, url=url, headers=headers,
                            json_body=json_body, timeout=timeout_seconds)
        if self._raise is not None:
            raise self._raise
        return self._response


def _cfg(**over):
    from app.radius.services import admin_panel_client as apc
    base = dict(enabled=True, base_url="https://panel.example",
                license_key="lic_abc", timeout_seconds=3.0, retry_count=0)
    base.update(over)
    return apc.AdminBridgeConfig(**base)


def test_get_update_latest_signs_body_via_post():
    """The license envelope must ride in the BODY of a POST — the bug was a
    header-only GET the panel could not verify."""
    from app.radius.services import admin_panel_client as apc
    cap = {}
    client = apc.AdminPanelClient(
        config=_cfg(), transport=_FakeTransport(response={"ok": True, "version": None}, capture=cap))
    res = client.get_update_latest(current_version="1.0.0")
    assert res["ok"] is True and res["payload"]["version"] is None
    assert cap["method"] == "POST"
    assert cap["json_body"]["license_key"] == "lic_abc"       # signed envelope in body
    assert cap["json_body"]["current_version"] == "1.0.0"
    assert cap["headers"].get("Authorization") == "Bearer lic_abc"
    assert "current_version=1.0.0" in cap["url"]


def test_get_update_latest_classifies_http_and_transport_errors():
    import urllib.error
    from app.radius.services import admin_panel_client as apc
    # 503 JSON error body (transport parsed it, tagged http_status).
    r = apc.AdminPanelClient(config=_cfg(), transport=_FakeTransport(
        response={"ok": False, "http_status": 503})).get_update_latest(current_version="1.0.0")
    assert r["ok"] is False and r["reason"] == "service_unavailable"
    # Raised HTTPError with empty body → classified by code (401 → signature/license).
    err = urllib.error.HTTPError("http://x", 401, "unauth", {}, None)
    r = apc.AdminPanelClient(config=_cfg(), transport=_FakeTransport(
        raise_exc=err)).get_update_latest(current_version="1.0.0")
    assert r["ok"] is False and r["reason"] == "unauthorized"
    # Generic transport error → unreachable.
    r = apc.AdminPanelClient(config=_cfg(), transport=_FakeTransport(
        raise_exc=urllib.error.URLError("down"))).get_update_latest(current_version="1.0.0")
    assert r["ok"] is False and r["reason"] == "unreachable"
    # Disabled / not configured short-circuits before any transport call.
    r = apc.AdminPanelClient(config=_cfg(enabled=False),
                             transport=_FakeTransport()).get_update_latest()
    assert r["ok"] is False and r["reason"] == "disabled"


def _configure_bridge(monkeypatch):
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "1")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BASE_URL", "https://panel.example")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "lic_test_key")


def _patch_transport(monkeypatch, **kw):
    from app.radius.services import admin_panel_client as apc
    fake = _FakeTransport(**kw)
    monkeypatch.setattr(apc, "UrlLibAdminBridgeTransport", lambda: fake)
    return fake


def test_end_to_end_up_to_date_is_ok_not_failure(app, monkeypatch):
    """version:null over the real signed path → SUCCESS + up-to-date, no banner."""
    with app.app_context():
        from app.radius.services import self_update as su
        _configure_bridge(monkeypatch)
        cap = {}
        _patch_transport(monkeypatch, response={"ok": True, "version": None}, capture=cap)
        state = su.check_for_update(1)
        assert state["ok"] is True
        assert state["available"] is False
        assert state["reason"] == "ok"
        # the license envelope really rode in the POST body
        assert cap["method"] == "POST"
        assert cap["json_body"]["license_key"] == "lic_test_key"
        assert cap["json_body"]["current_version"] == "1.0.0"
        # up-to-date is an OK state, never the «تعذّر» banner
        assert su.reason_info(state["reason"])["kind"] == "ok"


def test_end_to_end_available_when_newer(app, monkeypatch):
    with app.app_context():
        from app.radius.services import self_update as su
        _configure_bridge(monkeypatch)
        _patch_transport(monkeypatch,
                         response={"ok": True, "version": "1.1.0", "min_version": "1.0.0"})
        state = su.check_for_update(1)
        assert state["ok"] is True
        assert state["available"] is True
        assert state["latest"] == "1.1.0"
        assert state["target_version"] == "1.1.0"


def test_end_to_end_failure_has_specific_reason(app, monkeypatch):
    """A 401 signature/license rejection → FAILED with a diagnosable reason."""
    with app.app_context():
        from app.radius.services import self_update as su
        _configure_bridge(monkeypatch)
        _patch_transport(monkeypatch,
                         response={"ok": False, "status": "invalid_signature", "http_status": 401})
        state = su.check_for_update(1)
        assert state["ok"] is False
        assert state["available"] is False
        assert state["reason"] == "unauthorized"
        info = su.reason_info(state["reason"])
        assert info["kind"] == "failed" and "401" in info["message"]


def test_end_to_end_transport_error_is_failed(app, monkeypatch):
    import urllib.error
    with app.app_context():
        from app.radius.services import self_update as su
        _configure_bridge(monkeypatch)
        _patch_transport(monkeypatch, raise_exc=urllib.error.URLError("boom"))
        state = su.check_for_update(1)
        assert state["ok"] is False and state["reason"] == "unreachable"
        assert su.reason_info(state["reason"])["kind"] == "failed"


def test_reason_info_maps_states_distinctly():
    from app.radius.services import self_update as su
    assert su.reason_info("ok")["kind"] == "ok"
    assert su.reason_info("never_checked")["kind"] == "neutral"
    assert su.reason_info("disabled")["kind"] == "neutral"
    assert su.reason_info("timeout")["kind"] == "failed"
    assert su.reason_info("unauthorized")["kind"] == "failed"
    assert su.reason_info("service_unavailable")["kind"] == "failed"
    # unknown http_<code> still yields a diagnosable failed label
    got = su.reason_info("http_418")
    assert got["kind"] == "failed" and "418" in got["message"]


# ── sidebar link ──────────────────────────────────────────────────────
def test_sidebar_shows_system_update_link_for_super(app):
    """The «تحديث النظام» entry appears in the admin sidebar for owner/super.

    The /system/update page itself renders the admin layout + sidebar, so we
    assert the sidebar-specific anchor (hb-side-subitem → the route URL).
    """
    import re
    c = app.test_client()
    _super(c)
    res = c.get("/admin/radius/system/update")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    # A sidebar sub-item anchor pointing at the system-update route.
    assert re.search(
        r'hb-side-subitem[^"]*"\s+href="/admin/radius/system/update"', body
    ), "sidebar link to radius.system_update missing"
    assert "تحديث النظام" in body


def _sidebar_anchor(body):
    import re
    m = re.search(
        r'<a class="hb-side-subitem[^"]*"\s+href="/admin/radius/system/update">.*?</a>',
        body, re.S,
    )
    return m.group(0) if m else ""


def test_sidebar_update_badge_shown_when_available(app, monkeypatch):
    """The «جديد» badge is inside the SIDEBAR item only when an update exists."""
    c = app.test_client()
    _super(c)

    # No update yet → link present, no badge inside the sidebar anchor.
    anchor = _sidebar_anchor(c.get("/admin/radius/system/update").get_data(as_text=True))
    assert anchor and "جديد" not in anchor

    # Update available → badge appears inside the sidebar anchor.
    with app.app_context():
        from app.radius.services import self_update as su
        monkeypatch.setattr(su, "_fetch_latest", lambda tid: {
            "ok": True, "payload": {"version": "9.9.9", "min_version": "1.0.0"},
        })
        su.check_for_update(1)
    anchor2 = _sidebar_anchor(c.get("/admin/radius/system/update").get_data(as_text=True))
    assert anchor2 and "جديد" in anchor2
