"""Operations-assistant executor — pure units: unit conversions (1024), calendar
months in Asia/Gaza, the one-year cap, local→UTC with DST, the catalog's
forbidden keys, the dependency-free schema checker (cross-checked against
``jsonschema`` when available) and the proposal hash. No app, no DB."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.radius.services.ops_assistant import catalog, units
from app.radius.services.ops_assistant.schema_check import SchemaError, validate
from app.radius.services.ops_assistant.validator import canonical_hash

GAZA = ZoneInfo("Asia/Gaza")


@pytest.fixture(autouse=True)
def _gaza(monkeypatch):
    monkeypatch.setattr(units, "tzinfo_for", lambda _tid: GAZA)
    monkeypatch.setattr(units, "cap_minutes", lambda _tid=None: 365 * 1440)


def test_latin_digits():
    assert units.latin_digits("٠٥٩٩-١٢٣ ۴۵۶") == "0599-123 456"


@pytest.mark.parametrize("dur,minutes", [
    ({"value": 90, "unit": "minutes"}, 90), ({"value": 3, "unit": "hours"}, 180),
    ({"value": 2, "unit": "days"}, 2880)])
def test_duration_minutes(dur, minutes):
    assert units.duration_minutes(dur, tenant_id=1) == minutes


def test_calendar_months_clamp_and_dst():
    # 31 Jan 10:00 local (UTC+2) + 1 month → 28 Feb 10:00 local
    anchor = datetime(2026, 1, 31, 8, 0)
    end = units.add_calendar_months(anchor, 1, 1)
    assert end == datetime(2026, 2, 28, 8, 0)
    # across the spring DST change the LOCAL wall clock is kept (10:00 → 10:00)
    end = units.add_calendar_months(datetime(2026, 3, 1, 8, 0), 3, 1)
    assert end.replace(tzinfo=ZoneInfo("UTC")).astimezone(GAZA).hour == 10
    assert units.duration_minutes({"value": 1, "unit": "months"}, tenant_id=1,
                                  anchor_utc=datetime(2026, 2, 1, 8, 0)) == 28 * 1440


def test_twelve_months_cap_depends_on_leap_years():
    ok_span = units.duration_minutes({"value": 12, "unit": "months"}, tenant_id=1,
                                     anchor_utc=datetime(2026, 10, 6, 9, 0))
    assert units.enforce_cap(ok_span, 1) == 365 * 1440
    leap_span = units.duration_minutes({"value": 12, "unit": "months"}, tenant_id=1,
                                       anchor_utc=datetime(2027, 6, 1, 9, 0))
    assert leap_span == 366 * 1440
    with pytest.raises(units.UnitError) as e:
        units.enforce_cap(leap_span, 1)
    assert e.value.code == "over_one_year"
    with pytest.raises(units.UnitError) as e:
        units.enforce_cap(0, 1)
    assert e.value.code == "non_positive_duration"


def test_local_to_utc_with_dst():
    assert units.local_to_utc("2026-07-01T12:00", 1) == datetime(2026, 7, 1, 9, 0)   # UTC+3
    assert units.local_to_utc("2026-01-15T12:00", 1) == datetime(2026, 1, 15, 10, 0)  # UTC+2
    assert units.local_to_utc("٢٠٢٦-٠١-١٥T12:00", 1) == datetime(2026, 1, 15, 10, 0)
    with pytest.raises(units.UnitError):
        units.local_to_utc("tomorrow", 1)


def test_speed_and_quota_units_use_1024():
    assert units.KBPS_PER_MBPS == 1024 and units.MB_PER_GB == 1024
    assert units.mbps_to_kbps(2) == 2048 and units.mbps_to_kbps(1.5) == 1536
    assert units.gb_to_mb(1) == 1024 and units.gb_to_mb(0.5) == 512
    assert units.format_speed(2048) == "2 Mbps"
    assert units.format_speed(1536) == "1.5 Mbps"
    assert units.format_speed(512) == "512 kbps"
    assert units.format_speed(1024 * 1024) == "1 Gbps"
    assert units.format_speed(0) == "unlimited"
    assert units.format_quota(2048) == "2 GB" and units.format_quota(100) == "100 MB"


def test_temp_speed_bounds():
    assert units.temp_speed_kbps(0) == 0 and units.temp_speed_kbps(64) == 64
    assert units.temp_speed_kbps(1_000_000) == 1_000_000
    for bad in (1, 63, 1_000_001):
        with pytest.raises(units.UnitError):
            units.temp_speed_kbps(bad)
    assert units.temp_speed_minutes(1) == 1 and units.temp_speed_minutes(1440) == 1440
    for bad in (0, 1441):
        with pytest.raises(units.UnitError):
            units.temp_speed_minutes(bad)


@pytest.mark.parametrize("key", ["password", "new_password", "pppoe_password", "secret", "token",
                                 "api_key", "pin", "balance", "metadata", "tenant_id", "status",
                                 "expire_at", "first_login_at", "last_login_at", "temporary_speed",
                                 "custom_speed", "idempotency_key", "client_request_id",
                                 "version", "PASSWORD", "router_secret", "access_token", "apikey",
                                 "admin_pwd"])
def test_forbidden_keys(key):
    assert catalog.is_forbidden_key(key)


@pytest.mark.parametrize("key", ["login_without_password", "password_length",
                                 "password_generation_type", "username", "plan_id", "pinned_note",
                                 "spinner"])
def test_allowed_keys(key):
    assert not catalog.is_forbidden_key(key)


def test_catalog_shape():
    assert catalog.catalog_version() == "ops-v2"
    acts = set(catalog.catalog()["actions"])
    assert set(catalog.EXECUTABLE_ACTIONS) <= acts
    assert set(catalog.INFO_ACTIONS) | set(catalog.LOOKUP_ACTIONS) <= acts
    assert set(catalog.ACTION_PERMISSION) >= set(catalog.EXECUTABLE_ACTIONS)
    assert set(catalog.ACTION_PERMISSION) >= set(catalog.INFO_ACTIONS) | set(catalog.LOOKUP_ACTIONS)
    assert set(catalog.CONTROL_ACTIONS) == set(catalog.catalog()["control_actions"])
    assert "reply" in catalog.CONTROL_ACTIONS
    vocab = set(catalog.catalog()["conventions"]["permission_vocabulary"])
    assert {k for k, _e, _m in catalog.ACTION_PERMISSION.values()} | {
        catalog.DIRECT_GENERATION_KEY} <= vocab


_V1_SAMPLES = [
    {"action": "create_subscriber", "fields": {"username": "ali.2026", "plan_id": 3,
                                               "duration": {"value": 30, "unit": "days"}},
     "missing": [], "summary_ar": "x"},
    {"action": "create_subscriber", "fields": {"username": "ali", "plan_id": 3,
                                               "duration": {"value": 1, "unit": "days"},
                                               "no_expiry": True}, "missing": [], "summary_ar": "x"},
    {"action": "renew_or_extend_subscriber", "fields": {"username": "a", "mode": "until",
                                                        "until_local": "2026-12-01T10:00",
                                                        "charge_mode": "paid", "amount": 20},
     "missing": [], "summary_ar": "x"},
    {"action": "renew_or_extend_subscriber", "fields": {"username": "a", "mode": "duration",
                                                        "charge_mode": "free"},
     "missing": [], "summary_ar": "x"},
    {"action": "temporary_speed", "fields": {"username": "a", "operation": "apply",
                                             "down_kbps": 2048, "up_kbps": 1024,
                                             "duration": {"value": 1, "unit": "hours"},
                                             "until_local": "2026-12-01T10:00"},
     "missing": [], "summary_ar": "x"},
    {"action": "temporary_speed", "fields": {"username": "a", "operation": "cancel"},
     "missing": [], "summary_ar": "x"},
    {"action": "create_plan", "fields": {"name": "p", "speed_unlimited": True},
     "missing": [], "summary_ar": "x"},
    {"action": "create_plan", "fields": {"name": "p"}, "missing": [], "summary_ar": "x"},
    {"action": "create_card_batch", "fields": {"source": "plan", "plan_id": 1, "offer_id": 2,
                                               "count": 5}, "missing": [], "summary_ar": "x"},
    {"action": "create_card_batch", "fields": {"source": "plan", "plan_id": 1, "count": 5,
                                               "password_length": 0,
                                               "login_without_password": False},
     "missing": [], "summary_ar": "x"},
    {"action": "ask", "fields": {}, "missing": [], "summary_ar": "x"},
    {"action": "ask", "fields": {"username": "a"}, "missing": ["plan_id"], "summary_ar": "x"},
    {"action": "refuse", "fields": {"x": 1}, "missing": [], "summary_ar": "x"},
    {"action": "choose", "fields": {"source": "list_plans"}, "missing": [], "summary_ar": "x"},
    {"action": "create_offer", "fields": {"name": "o", "plan_id": 1,
                                          "duration": {"value": 1, "unit": "days"},
                                          "wholesale": 1, "selling": 2,
                                          "visible_manager_ids": [4]},
     "missing": [], "summary_ar": "x"},
    {"action": "list_plans", "fields": {"query": "x" * 101}, "missing": [], "summary_ar": "x"},
    {"action": "find_subscriber", "fields": {"query": "ali"}, "missing": [], "summary_ar": ""},
    {"action": "create_subscriber", "fields": {"username": "ali", "plan_id": True},
     "missing": [], "summary_ar": "x"},
]
# ops-v2: every object carries ``message`` (same verdicts as under ops-v1) …
_SAMPLES = [{**s, "message": "m"} for s in _V1_SAMPLES] + [
    # … and the v2 shapes: reply, INFO actions, message rules, summary only for actions
    {"action": "reply", "fields": {}, "missing": [], "message": "أهلين"},
    {"action": "reply", "fields": {"x": 1}, "missing": [], "message": "x"},
    {"action": "reply", "fields": {}, "missing": []},
    {"action": "card_batch_status", "fields": {"batch_id": 3}, "missing": [], "message": "m"},
    {"action": "card_batch_status", "fields": {}, "missing": [], "message": "m"},
    {"action": "subscriber_info", "fields": {"username": "a.b"}, "missing": [], "message": "m"},
    {"action": "online_sessions", "fields": {"query": "a"}, "missing": [], "message": "m"},
    {"action": "list_card_batches", "fields": {"query": "alaa", "limit": 5}, "missing": [],
     "message": "m"},
    {"action": "choose", "fields": {"source": "list_card_batches", "query": "alaa"},
     "missing": [], "message": "m"},
    {"action": "ask", "fields": {}, "missing": ["password"], "message": "m"},
    {"action": "enable_subscriber", "fields": {"username": "a"}, "missing": [], "message": "m"},
    {"action": "ask", "fields": {}, "missing": ["count"], "message": ""},
]
_V2_VERDICTS = [True, False, False, True, False, True, True, True, True, False, False, False]


@pytest.mark.parametrize("sample", _SAMPLES)
def test_schema_checker_agrees_with_jsonschema(sample):
    js = pytest.importorskip("jsonschema")
    schema = catalog.output_schema()
    ours = not validate(sample, schema)
    theirs = js.Draft202012Validator(schema).is_valid(sample)
    assert ours == theirs, (sample, validate(sample, schema))


def test_schema_checker_expected_verdicts():
    schema = catalog.output_schema()
    verdicts = [not validate(s, schema) for s in _SAMPLES]
    assert verdicts == [True, False, True, False, False, True, True, False, False, False,
                        False, True, False, True, False, False, False, False] + _V2_VERDICTS


def test_schema_checker_fails_closed_on_unknown_keywords():
    with pytest.raises(SchemaError):
        validate({"a": 1}, {"type": "object", "patternProperties": {}})


def test_proposal_hash_binds_conversation_and_fields_not_wording():
    p = {"action": "enable_subscriber", "fields": {"username": "a"}, "missing": [],
         "summary_ar": "x"}
    h = canonical_hash("c1", p)
    assert h == canonical_hash("c1", {**p, "summary_ar": "y"})
    assert h != canonical_hash("c2", p)
    assert h != canonical_hash("c1", {**p, "fields": {"username": "b"}})
    plan = {"action": "plan", "steps": [{"action": "enable_subscriber",
                                         "fields": {"username": "a"}}],
            "missing": [], "summary_ar": "x"}
    assert canonical_hash("c1", plan) != h
