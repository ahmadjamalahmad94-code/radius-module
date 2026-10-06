"""fields-sched (owner decisions 2026-10-06) — schedule DAYS and policy «مفعّلة».

WIRE 1 — bandwidth-schedule DAYS (``days_csv``):
  a schedule applies ONLY on its selected days, judged on the panel-LOCAL day
  (Asia/Gaza, DST-aware) — in authorize (Mikrotik-Rate-Limit), in the live
  schedule worker (engage/release CoA) and in every «effective rate» path.
  Empty = every day. A window that crosses midnight BELONGS TO THE DAY IT
  STARTS (Fri 22:00→02:00 runs Fri 22:00 … Sat 01:59, not Fri 00:00–01:59).

WIRE 2 — network policy «مفعّلة» (enabled):
  a disabled policy has ZERO effect: its plan is cleanup-only (preview,
  download, apply remove the policy's managed objects and add nothing);
  re-enabling restores the normal plan.

REMOVE — schedule CIR (cir_*_kbps) and the subscriber-group's own
  bandwidth_schedule_id: accepted by the API (old app builds), never written.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# 2026-10-08 = Thursday, 2026-10-09 = Friday. Gaza is UTC+3 (summer time) then.
THU_21_LOCAL = datetime(2026, 10, 8, 18, 0)     # UTC instants (naive = UTC)
FRI_21_LOCAL = datetime(2026, 10, 9, 18, 0)
FRI_2330_LOCAL = datetime(2026, 10, 9, 20, 30)


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_fields_sched_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "test-token")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.db.helpers import now_iso
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        with transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO access_plans(id,tenant_id,name,code,plan_type,"
                "service_type,duration_minutes,validity_days,speed_down_kbps,"
                "speed_up_kbps,price,currency,enabled,created_at) VALUES"
                "(1,1,'Day Plan','DAY','time','PPPoE',1440,30,50000,25000,5,'ILS',1,?)",
                (now_iso(),),
            )
    yield flask_app
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


_H = {"Authorization": "Bearer test-token"}


def _j(resp):
    return json.loads(resp.data.decode("utf-8"))


def _sched(*, start="21:00", end="22:00", days="fri", **extra):
    from app.radius.services.operations import get_operations_service
    data = {"name": f"s-{start}-{days or 'all'}", "target_type": "plan", "plan_id": 1,
            "priority": 5, "starts_at_time": start, "ends_at_time": end,
            "days_csv": days, "speed_down_kbps": 8000, "speed_up_kbps": 4000,
            "restore_mode": "profile_default", "enabled": True}
    data.update(extra)
    return get_operations_service().create_bandwidth_schedule(
        tenant_id=1, actor="t", data=data)


def _resolve(at):
    from app.radius.db.repos import operations_repo
    return operations_repo.resolve_effective_bandwidth_schedule(1, plan_id=1, at=at)


class _Frozen(datetime):
    """``datetime`` whose now() is pinned — freezes the authorize clock."""
    pinned: datetime = FRI_21_LOCAL

    @classmethod
    def now(cls, tz=None):  # noqa: D401
        aware = cls.pinned.replace(tzinfo=timezone.utc)
        return aware.astimezone(tz) if tz else aware.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return cls.pinned


def _authorize_rate(monkeypatch, at: datetime) -> str:
    """Run the REAL policy_engine.authorize with a frozen schedule clock and
    return the Mikrotik-Rate-Limit it replies with."""
    from app.radius.db.repos import operations_repo
    from app.radius.services import policy_engine
    _Frozen.pinned = at
    monkeypatch.setattr(operations_repo, "datetime", _Frozen)
    dec = policy_engine.authorize(policy_engine.AuthRequest(
        tenant_id=1, username="dayuser", password="pw123"))
    assert dec.ok, dec.reason
    return dec.reply_attrs.get("Mikrotik-Rate-Limit", "")


def _mk_subscriber():
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, username="dayuser", password="pw123", tenant_id=1, plan_id=1,
        status="enabled", expire_at="2030-01-01T00:00:00"))


# ══════════════════════════ WIRE 1 — schedule days ══════════════════════════


def test_authorize_friday_only_schedule_active_friday_not_thursday(app, monkeypatch):
    with app.app_context():
        _mk_subscriber()
        _sched(days="fri")
        # Friday 21:00 local → the schedule rate (up/down = 4000k/8000k).
        assert _authorize_rate(monkeypatch, FRI_21_LOCAL) == "4000k/8000k"
        # Thursday, SAME local hour → the plan rate, not the schedule.
        assert _authorize_rate(monkeypatch, THU_21_LOCAL) == "25000k/50000k"


def test_empty_days_means_every_day(app):
    with app.app_context():
        _sched(days="")
        assert _resolve(THU_21_LOCAL) is not None
        assert _resolve(FRI_21_LOCAL) is not None


def test_midnight_crossing_window_belongs_to_its_start_day(app):
    with app.app_context():
        _sched(start="22:00", end="02:00", days="fri")
        # Fri 23:00 local (20:00Z) — inside Friday's window.
        assert _resolve(datetime(2026, 10, 9, 20, 0)) is not None
        # Sat 01:00 local (Fri 22:00Z) — the after-midnight tail of Friday's window.
        assert _resolve(datetime(2026, 10, 9, 22, 0)) is not None
        # Fri 01:00 local (Thu 22:00Z) — tail of THURSDAY's window → not active.
        assert _resolve(datetime(2026, 10, 8, 22, 0)) is None
        # Sat 23:00 local — Saturday's own window → not active.
        assert _resolve(datetime(2026, 10, 10, 20, 0)) is None


def test_local_day_is_dst_aware_gaza_winter(app):
    """Winter (UTC+2): Friday 2026-12-04 21:30 local = 19:30Z. A fixed +3 would
    see 22:30 (out of a 21:00–22:00 window); DST-aware Gaza sees 21:30 (in)."""
    with app.app_context():
        _sched(start="21:00", end="22:00", days="fri")
        assert _resolve(datetime(2026, 12, 4, 19, 30)) is not None
        assert _resolve(datetime(2026, 12, 4, 20, 30)) is None   # 22:30 local


def test_effective_rate_path_respects_days(app):
    with app.app_context():
        from app.radius.services.bandwidth_rate import effective_rate_limit
        _mk_subscriber()
        _sched(days="fri")
        assert effective_rate_limit(1, "dayuser", at=FRI_21_LOCAL) == "4000k/8000k"
        assert effective_rate_limit(1, "dayuser", at=THU_21_LOCAL) == "25000k/50000k"


def test_worker_engages_only_on_selected_day_then_releases(app, monkeypatch):
    with app.app_context():
        from app.radius.services import bandwidth_apply
        from app.workers import bandwidth_schedule_worker as w
        calls: list[str] = []
        monkeypatch.setattr(
            bandwidth_apply, "apply_schedule_users_live",
            lambda tid, sched, at=None, phase="engage": calls.append(phase) or {})
        _sched(days="fri")
        w.reset_state_for_tests()
        assert w.tick_once(now=THU_21_LOCAL) == {"engaged": 0, "released": 0}
        assert w.tick_once(now=FRI_21_LOCAL) == {"engaged": 1, "released": 0}
        assert w.tick_once(now=FRI_2330_LOCAL) == {"engaged": 0, "released": 1}
        assert calls == ["engage", "release"]


def test_days_are_normalised_and_unknown_day_rejected(app):
    with app.app_context():
        from app.radius.core.errors import RadiusValidationError
        saved = _sched(days="Friday,sat,الأحد")
        assert saved["days_csv"] == "sat,sun,fri"
        with pytest.raises(RadiusValidationError):
            _sched(days="fri,funday")


def test_api_patch_days_round_trip_and_cir_ignored(app):
    client = app.test_client()
    r = client.post("/api/v1/bandwidth-schedules", headers=_H, json={
        "name": "night", "target_type": "plan", "plan_id": 1,
        "starts_at_time": "21:00", "ends_at_time": "22:00",
        "speed_down_kbps": 8000, "speed_up_kbps": 4000,
        "cir_down_kbps": 777, "cir_up_kbps": 555, "days_csv": "fri"})
    assert r.status_code == 201, r.data
    sched = _j(r)["data"]["schedule"]
    assert sched["days_csv"] == "fri"
    assert not sched.get("cir_down_kbps") and not sched.get("cir_up_kbps")
    r = client.patch(f"/api/v1/bandwidth-schedules/{sched['id']}", headers=_H,
                     json={"days_csv": "thu,fri", "cir_down_kbps": 999})
    assert r.status_code == 200, r.data
    got = _j(r)["data"]["schedule"]
    assert got["days_csv"] == "thu,fri"
    assert not got.get("cir_down_kbps")


def test_web_schedules_page_saves_days_and_has_no_cir(app):
    client = app.test_client()
    with app.app_context():
        from app.radius.db.repos import admins_repo
        admins_repo.create_admin(
            username="sched_owner", password="sched-pass-1", full_name="Owner",
            is_super_admin=True,
            role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    res = client.post("/admin/radius/login",
                      data={"username": "sched_owner", "password": "sched-pass-1"})
    assert res.status_code in (302, 303)
    page = client.get("/admin/radius/bandwidth-schedules")
    assert page.status_code == 200
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token") or "t0k"
        sess["_csrf_token"] = token
    html = page.data.decode("utf-8")
    assert 'name="cir_down_kbps"' not in html and 'name="cir_up_kbps"' not in html
    assert 'name="days_present"' in html and 'name="days"' in html
    r = client.post("/admin/radius/bandwidth-schedules", data={
        "name": "web-fri", "target_type": "plan", "plan_id": "1", "priority": "5",
        "starts_at_time": "21:00", "ends_at_time": "22:00",
        "speed_down_kbps": "8000", "speed_up_kbps": "4000",
        "restore_mode": "profile_default", "enabled": "1",
        "days_present": "1", "days": ["fri", "sat"], "_csrf_token": token})
    assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import operations_repo
        rows = [x for x in operations_repo.list_bandwidth_schedules(1)
                if x["name"] == "web-fri"]
        assert rows and rows[0]["days_csv"] == "sat,fri"
        sid = rows[0]["id"]
    # An edit that renders no day chips keeps the stored days.
    r = client.post(f"/admin/radius/bandwidth-schedules/{sid}/edit", data={
        "name": "web-fri", "priority": "5", "starts_at_time": "21:00",
        "ends_at_time": "22:00", "speed_down_kbps": "8000", "speed_up_kbps": "4000",
        "restore_mode": "profile_default", "enabled": "1", "_csrf_token": token})
    assert r.status_code in (302, 303)
    with app.app_context():
        from app.radius.db.repos import operations_repo
        assert operations_repo.get_bandwidth_schedule(1, sid)["days_csv"] == "sat,fri"


# ═══════════════ REMOVE — subscriber-group bandwidth_schedule_id ═══════════════


def test_group_schedule_id_ignored_by_api_and_web(app):
    client = app.test_client()
    with app.app_context():
        s = _sched(days="")
    r = client.post("/api/v1/subscriber-groups", headers=_H,
                    json={"name": "G1", "bandwidth_schedule_id": s["id"]})
    assert r.status_code == 201, r.data
    gid = _j(r)["data"]["group"]["id"]
    assert not _j(r)["data"]["group"].get("bandwidth_schedule_id")
    r = client.patch(f"/api/v1/subscriber-groups/{gid}", headers=_H,
                     json={"bandwidth_schedule_id": s["id"], "description": "d"})
    assert r.status_code == 200, r.data
    assert not _j(r)["data"]["group"].get("bandwidth_schedule_id")
    assert _j(r)["data"]["group"]["description"] == "d"


# ══════════════════════ WIRE 2 — network policy «مفعّلة» ══════════════════════


def _script(client, sub, pid):
    r = client.get(f"/api/v1/network-policy/{sub}/policies/{pid}/preview.rsc", headers=_H)
    assert r.status_code == 200, r.data
    return _j(r)["data"]["script"]


def _adds(script: str) -> list[str]:
    return [ln for ln in script.splitlines() if " add " in f" {ln} "]


def _make_policy(client, sub):
    if sub == "web-block":
        r = client.post("/api/v1/network-policy/web-block/policies", headers=_H,
                        json={"name": "Block tiktok", "router_id": 1, "fail_open": True})
        pid = _j(r)["data"]["id"]
        client.post(f"/api/v1/network-policy/web-block/policies/{pid}/targets",
                    headers=_H, json={"value": "tiktok.com", "category": "tiktok"})
    elif sub == "walled-garden":
        r = client.post("/api/v1/network-policy/walled-garden/policies", headers=_H,
                        json={"name": "Free pages", "router_id": 1,
                              "hotspot_profile": "hs1"})
        pid = _j(r)["data"]["id"]
        client.post(f"/api/v1/network-policy/walled-garden/policies/{pid}/entries",
                    headers=_H, json={"value": "example.com", "entry_type": "dst_host"})
    else:
        r = client.post("/api/v1/network-policy/remote-access/policies", headers=_H,
                        json={"name": "Ops", "router_id": 1, "allow_winbox": True,
                              "source_address_list": "ops-list"})
        pid = _j(r)["data"]["id"]
    assert r.status_code == 201, r.data
    return pid


@pytest.mark.parametrize("sub", ["web-block", "walled-garden", "remote-access"])
def test_disabled_policy_has_zero_effect_and_reenable_restores(app, sub):
    client = app.test_client()
    pid = _make_policy(client, sub)
    enabled_script = _script(client, sub, pid)
    assert _adds(enabled_script), enabled_script        # enabled → adds rules

    r = client.patch(f"/api/v1/network-policy/{sub}/policies/{pid}", headers=_H,
                     json={"enabled": False})
    assert r.status_code == 200, r.data
    disabled_script = _script(client, sub, pid)
    assert _adds(disabled_script) == [], disabled_script  # nothing added …
    assert "remove" in disabled_script                     # … only cleanup

    r = client.patch(f"/api/v1/network-policy/{sub}/policies/{pid}", headers=_H,
                     json={"enabled": True})
    assert r.status_code == 200, r.data
    assert _script(client, sub, pid) == enabled_script     # restored


def test_apply_of_disabled_policy_sends_cleanup_only(app, monkeypatch):
    client = app.test_client()
    pid = _make_policy(client, "web-block")
    client.patch(f"/api/v1/network-policy/web-block/policies/{pid}", headers=_H,
                 json={"enabled": False})
    import app.api.v1.network_policy as npc_api
    captured: dict = {}

    class _Res:
        ok = True

        def as_dict(self):
            return {"ok": True, "change_set_id": 1, "status": "succeeded",
                    "targets": [], "blockers": [], "warnings": [], "reason_ar": ""}

    monkeypatch.setattr(npc_api.snapshot_capture_svc, "capture_pre_apply_snapshot",
                        lambda **_: SimpleNamespace(snapshot_id=7))
    monkeypatch.setattr(npc_api.apply_svc, "request_apply",
                        lambda **kw: captured.update(kw) or _Res())
    r = client.post(f"/api/v1/network-policy/web-block/policies/{pid}/apply",
                    headers=_H, json={"execution_mode": "full"})
    assert r.status_code == 200, r.data
    forward = captured["forward_script"]
    assert forward and _adds(forward) == [] and "remove" in forward


def test_planners_gate_on_enabled_directly(app):
    from app.radius.services import (npc_walled_garden_planner as wg,
                                     npc_web_block_planner as wb)
    target = {"value": "x.com", "normalized_value": "x.com", "status": "active",
              "target_type": "domain", "category": "custom"}
    on = wb.plan({"id": 5, "enabled": True}, [target])
    off = wb.plan({"id": 5, "enabled": False}, [target])
    assert on.address_list_ops and on.filter_ops
    assert not off.address_list_ops and not off.filter_ops and off.cleanup_ops
    entry = {"value": "x.com", "normalized_value": "x.com", "status": "active",
             "entry_type": "dst_host"}
    assert wg.plan({"id": 6, "enabled": True}, [entry]).walled_garden_ops
    assert not wg.plan({"id": 6, "enabled": 0}, [entry]).walled_garden_ops
