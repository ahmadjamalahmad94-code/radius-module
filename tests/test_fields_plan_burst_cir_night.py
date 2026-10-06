"""FIELDS (team plan, owner 2026-10-06) — plan Burst / CIR / «غير محدود ليلًا» WIRED,
dead plan fields REMOVED.

* Burst (burst_enabled + burst_down/up + threshold + time) and CIR (cir_down/up)
  were saved and never reached the router. They now build MikroTik's full
  ``rx/tx burst-rx/burst-tx thr-rx/thr-tx time/time priority min-rx/min-tx``
  string — in authorize AND in the CoA / effective-rate path, with the device
  split scaling every rate token and a schedule/override tier winning (plain).
* «غير محدود ليلًا» + new «من/إلى» (panel time, Asia/Gaza): bytes used inside the
  night window are NOT counted toward any quota (total/daily/monthly).
* REMOVED: speed_control_enabled, VLAN, bind MAC/IP, force MAC, plan tier, speed
  override, plan device count and ~38 metadata inputs — gone from the web form;
  the API accepts and ignores the keys (old app builds), stored values untouched.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

MB = 1_048_576
TOKEN = "fields-plan-token"
AUTH = {"Authorization": "Bearer " + TOKEN}

# 4 Mbps down / 1 Mbps up, burst 8M/2M, threshold 3M (down), 16 s, CIR 1M/256k.
BURST_PLAN = dict(speed_down_kbps=4096, speed_up_kbps=1024, burst_enabled=1,
                  burst_down_kbps=8192, burst_up_kbps=2048,
                  burst_threshold_kbps=3072, burst_time_sec=16,
                  cir_down_kbps=1024, cir_up_kbps=256)
FULL = "1024k/4096k 2048k/8192k 768k/3072k 16/16 8 256k/1024k"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_plan.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fields-plan-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_fp")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-fp")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        tenants_repo.set_setting(1, "billing.timezone", "Asia/Gaza")
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def _no_pod(monkeypatch):
    import app.radius.services.policy_reconciler as pr
    monkeypatch.setattr(pr, "reconcile_active_sessions_against_policy",
                        lambda tid, **kw: None)


def _db():
    from app.radius.db.connection import db
    return db()


def _plan(**cols) -> int:
    now = datetime.utcnow().isoformat()
    fields = {"tenant_id": 1, "name": "fp_" + uuid4().hex[:6], "duration_minutes": 30 * 1440,
              "price": 30.0, "currency": "ILS", "speed_down_kbps": 4096,
              "speed_up_kbps": 1024, "enabled": 1, "created_at": now, "updated_at": now}
    fields.update(cols)
    cur = _db().execute(
        f"INSERT INTO access_plans({', '.join(fields)}) VALUES({', '.join('?' * len(fields))})",
        tuple(fields.values()))
    return int(cur.lastrowid)


def _sub(plan_id: int, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = "fp_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret", plan_id=plan_id,
        full_name="Fields Plan", mobile="0599000000", status="enabled",
        expire_at=datetime.utcnow() + timedelta(days=400), **kw))
    return subscribers_repo.get_subscriber(1, username)


def _session(username, *, start, upd, up_mb, down_mb, stop=None) -> int:
    cur = _db().execute(
        "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid, acctstarttime, "
        "acctupdatetime, acctstoptime, acctsessiontime, acctinputoctets, acctoutputoctets, "
        "nasipaddress) VALUES(1,?,?,?,?,?,?,60,?,?,'10.0.0.1')",
        (username, "s-" + uuid4().hex[:10], uuid4().hex, start, upd, stop,
         int(up_mb * MB), int(down_mb * MB)))
    return int(cur.lastrowid)


def _authorize(username):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password="secret", tenant_id=1))


# ───────────────────────────── Burst + CIR ─────────────────────────────

def test_authorize_reply_carries_burst_and_cir_in_mikrotik_order(app):
    s = _sub(_plan(**BURST_PLAN))
    dec = _authorize(s.username)
    assert dec.ok, dec.reason
    assert dec.reply_attrs["Mikrotik-Rate-Limit"] == FULL


def test_cir_without_burst_pads_burst_positions_and_priority(app):
    s = _sub(_plan(cir_down_kbps=2048, cir_up_kbps=512))
    assert _authorize(s.username).reply_attrs["Mikrotik-Rate-Limit"] == \
        "1024k/4096k 0/0 0/0 0/0 8 512k/2048k"


def test_plain_plan_rate_string_is_unchanged(app):
    s = _sub(_plan())
    assert _authorize(s.username).reply_attrs["Mikrotik-Rate-Limit"] == "1024k/4096k"


def test_coa_effective_rate_is_the_same_string(app, monkeypatch):
    from app.radius.services import bandwidth_apply, bandwidth_rate
    s = _sub(_plan(**BURST_PLAN))
    assert bandwidth_rate.effective_rate_limit(1, s.username) == FULL
    sent = []
    from app.radius.integration import radius_coa
    monkeypatch.setattr(radius_coa, "change_user_rate",
                        lambda tid, u, *, new_rate_limit: sent.append(new_rate_limit))
    bandwidth_apply.apply_users_effective(1, [s.username])
    assert sent == [FULL]


def test_device_split_scales_every_rate_token(app):
    from app.radius.services import bandwidth_rate
    s = _sub(_plan(**BURST_PLAN))
    _db().execute("UPDATE subscribers SET equal_share_download=1 WHERE username=?",
                  (s.username,))
    for _ in range(2):
        _session(s.username, start="2026-07-15 08:00:00", upd="2026-07-15 08:01:00",
                 up_mb=1, down_mb=1)
    assert bandwidth_rate.effective_rate_limit(1, s.username) == \
        "1024k/2048k 2048k/4096k 768k/1536k 16/16 8 256k/512k"


def test_subscriber_override_tier_wins_without_plan_burst(app):
    from app.radius.services import bandwidth_rate
    s = _sub(_plan(**BURST_PLAN), bandwidth_control_enabled=True,
             download_speed_kbps=2048, upload_speed_kbps=512)
    assert _authorize(s.username).reply_attrs["Mikrotik-Rate-Limit"] == "512k/2048k"
    assert bandwidth_rate.effective_rate_limit(1, s.username) == "512k/2048k"


def test_api_refuses_incoherent_burst_and_cir(client):
    base = {"name": "fp-api-" + uuid4().hex[:5], "speed_down_kbps": 4096,
            "speed_up_kbps": 1024}
    bad_burst = dict(base, burst_enabled=True, burst_down_kbps=2048,
                     burst_up_kbps=2048, burst_threshold_kbps=1000, burst_time_sec=8)
    r = client.post("/api/v1/profiles", json=bad_burst, headers=AUTH)
    assert r.status_code == 422, r.get_json()
    r = client.post("/api/v1/profiles", json=dict(base, cir_down_kbps=8000), headers=AUTH)
    assert r.status_code == 422, r.get_json()
    good = dict(base, **{k: (bool(v) if k == "burst_enabled" else v)
                         for k, v in BURST_PLAN.items()})
    r = client.post("/api/v1/profiles", json=good, headers=AUTH)
    assert r.status_code == 201, r.get_json()


# ───────────────────────────── «غير محدود ليلًا» ─────────────────────────────
# Gaza in July = UTC+3. Night window 00:00–06:00 local = 21:00–03:00 UTC.
FROZEN = datetime(2026, 7, 15, 12, 0, 0)


@pytest.fixture
def frozen(monkeypatch):
    from app.radius.services import quota_night
    monkeypatch.setattr(quota_night, "_utcnow", lambda: FROZEN)
    return FROZEN


def _night_plan(enabled=True, **kw):
    return _plan(quota_total_mb=100, nightly_unlimited_enabled=1 if enabled else 0,
                 nightly_from="00:00", nightly_to="06:00", **kw)


def test_night_usage_is_not_counted_toward_total_quota(app, frozen):
    s = _sub(_night_plan())
    # 01:00–05:00 local (22:00–02:00 UTC): 200 MB, fully inside the night.
    _session(s.username, start="2026-07-14 22:00:00", upd="2026-07-15 02:00:00",
             stop="2026-07-15 02:00:00", up_mb=20, down_mb=180)
    dec = _authorize(s.username)
    assert dec.ok, dec.reason


def test_same_usage_without_the_flag_exhausts_the_quota(app, frozen):
    s = _sub(_night_plan(enabled=False))
    _session(s.username, start="2026-07-14 22:00:00", upd="2026-07-15 02:00:00",
             stop="2026-07-15 02:00:00", up_mb=20, down_mb=180)
    dec = _authorize(s.username)
    assert not dec.ok and dec.reason == "quota_exhausted"


@pytest.mark.parametrize("total_mb,ok", [(160, True), (220, False)])
def test_session_straddling_the_night_end_counts_only_the_day_part(app, frozen, total_mb, ok):
    s = _sub(_night_plan())
    # 05:00–07:00 local = 02:00–04:00 UTC: half inside the night.
    _session(s.username, start="2026-07-15 02:00:00", upd="2026-07-15 04:00:00",
             stop="2026-07-15 04:00:00", up_mb=0, down_mb=total_mb)
    dec = _authorize(s.username)
    assert dec.ok is ok, dec.reason
    from app.radius.services import quota_period
    used = quota_period.period_used_bytes(s) / MB
    assert round(used) == total_mb // 2


def test_window_crossing_midnight_and_daily_quota(app, frozen):
    from app.radius.services import quota_period
    s = _sub(_plan(quota_daily_mb=50, nightly_unlimited_enabled=1,
                   nightly_from="22:00", nightly_to="06:00"))
    # 23:00 (14th) → 07:00 (15th) local = 20:00 → 04:00 UTC; 8 h, 7 of them night.
    _session(s.username, start="2026-07-14 20:00:00", upd="2026-07-15 04:00:00",
             stop="2026-07-15 04:00:00", up_mb=0, down_mb=800)
    u = quota_period.usage(s, now=FROZEN)
    # counted = the 06:00–07:00 hour only (100 MB) — for the period AND for the
    # local day (which starts at 00:00 = 6 night hours + that hour).
    assert round(sum(u["period"]) / MB) == 100
    assert round(sum(u["daily"]) / MB) == 100
    plan = __import__("app.radius.db.repos.plans_repo", fromlist=["x"]).get_plan(1, s.plan_id)
    assert quota_period.window_exhaustion(s, plan, now=FROZEN) == "daily"


def test_night_window_follows_palestine_dst(app, frozen):
    from app.radius.core import system_config
    from app.radius.services.quota_night import night_intervals
    tz = system_config.tenant_tzinfo(1)
    w = ((0, 0), (6, 0))
    summer = night_intervals(datetime(2026, 7, 14, 12), datetime(2026, 7, 15, 12), w, tz)
    winter = night_intervals(datetime(2026, 12, 14, 12), datetime(2026, 12, 15, 12), w, tz)
    assert summer == [(datetime(2026, 7, 14, 21), datetime(2026, 7, 15, 3))]
    assert winter == [(datetime(2026, 12, 14, 22), datetime(2026, 12, 15, 4))]


def test_api_nightly_requires_window_and_round_trips(client):
    name = "fp-night-" + uuid4().hex[:5]
    body = {"name": name, "speed_down_kbps": 2048, "speed_up_kbps": 512,
            "nightly_unlimited_enabled": True}
    assert client.post("/api/v1/profiles", json=body, headers=AUTH).status_code == 422
    body.update(nightly_from="23:00", nightly_to="07:00")
    r = client.post("/api/v1/profiles", json=body, headers=AUTH)
    assert r.status_code == 201, r.get_json()
    d = r.get_json()["data"]
    assert (d["nightly_from"], d["nightly_to"]) == ("23:00", "07:00")


# ───────────────────────────── REMOVED fields ─────────────────────────────

REMOVED_INPUTS = (
    "speed_control_enabled", "vlan_id", "bind_mac", "bind_ip", "speed_override_allowed",
    "download_policy_cisco", "upload_policy_cisco", "renewal_method", "billing_method",
    "subscription_expiry_date", "send_alerts", "shared_voucher_fup",
    "user_can_change_offer", "user_can_request_offer_change",
    "prevent_user_from_changing_subscription",
    "prevent_admin_from_changing_user_subscription", "subscriber_control_panel_enabled",
    "hide_invoice", "force_subscriber_to_purchase_card",
    "save_remaining_quota_on_activation", "daily_connection_time",
    "internet_connection_time", "use_old_quota_and_sessions_on_activation",
    "delete_usage_data_and_sessions_on_activation", "carry_remaining_time_on_activation",
    "notify_before_quota_expiry", "notify_before_daily_quota_expiry",
    "notification_channels", "auto_renew_when_quota_expires",
    "auto_renew_when_time_expires", "time_expiry_policy", "only_available_for",
    "available_for_all", "enable_mtu", "expiry_day_limit_toggle",
    "expiry_hour_limit_toggle", "mikrotik_address_list", "mikrotik_filter_chain_name",
    "mikrotik_user_group", "mikrotik_queue_priority_simple_queue",
    "stop_user_when_time_expires", "notify_when_quota_reaches_zero",
    "equal_quota_sharing",
)


def _web_login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_fp", "password": "owner-pass-fp"})
    assert res.status_code in {302, 303}, res.status_code


def test_web_form_no_longer_renders_removed_inputs(client, app):
    _web_login(client)
    pid = _plan()
    html = client.get(f"/admin/radius/plans/{pid}/edit").get_data(as_text=True)
    for name in REMOVED_INPUTS:
        assert f'name="{name}"' not in html, name
    for name in ("nightly_from", "nightly_to", "burst_threshold_kbps", "cir_down_kbps"):
        assert f'name="{name}"' in html, name


def test_web_save_keeps_old_values_of_removed_fields(client, app):
    _web_login(client)
    meta = {"mikrotik": {"enable_mtu": "1"}, "notifications": {"notification_channels": "sms"}}
    pid = _plan(vlan_id=7, bind_mac=1, speed_control_enabled=1, plan_tier="Business",
                metadata=json.dumps(meta))
    client.get(f"/admin/radius/plans/{pid}/edit")
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    res = client.post(f"/admin/radius/plans/{pid}", data={
        "_csrf_token": csrf,
        "name": "fp-web-" + uuid4().hex[:5], "plan_type": "time", "service_type": "Hotspot",
        "speed_down_kbps": "4096", "speed_up_kbps": "1024", "enabled": "1",
        "vlan_id": "99", "bind_mac": "", "speed_control_enabled": "",
        "nightly_unlimited_enabled": "1", "nightly_from": "23:30", "nightly_to": "06:00",
    })
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    assert "/login" not in (res.headers.get("Location") or "")
    row = _db().execute("SELECT * FROM access_plans WHERE id=?", (pid,)).fetchone()
    assert (row["vlan_id"], row["bind_mac"], row["speed_control_enabled"],
            row["plan_tier"]) == (7, 1, 1, "Business")
    assert (row["nightly_from"], row["nightly_to"]) == ("23:30", "06:00")
    stored = json.loads(row["metadata"])
    assert stored["mikrotik"]["enable_mtu"] == "1"
    assert stored["notifications"]["notification_channels"] == "sms"


def test_api_ignores_removed_keys_silently(client, app):
    pid = _plan(vlan_id=7, allowed_devices_count=3, plan_tier="Business")
    r = client.patch(f"/api/v1/profiles/{pid}", headers=AUTH, json={
        "vlan_id": 99, "bind_mac": True, "bind_ip": True, "force_mac_address": True,
        "speed_control_enabled": True, "speed_override_allowed": True,
        "allowed_devices_count": 9, "plan_tier": "Personal", "description": "x"})
    assert r.status_code == 200, r.get_json()
    row = _db().execute("SELECT * FROM access_plans WHERE id=?", (pid,)).fetchone()
    assert (row["vlan_id"], row["bind_mac"], row["bind_ip"], row["force_mac_address"],
            row["speed_control_enabled"], row["speed_override_allowed"],
            row["allowed_devices_count"], row["plan_tier"]) == \
        (7, 0, 0, 0, 0, 0, 3, "Business")
    assert row["description"] == "x"
