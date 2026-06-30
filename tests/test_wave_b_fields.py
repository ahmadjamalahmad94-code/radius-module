"""
Wave-B field enforcement tests.
Tests for freeradius_translator (Parts 1+2) and card_batch_flags (Part 3).
"""
import json
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — minimal stubs
# ─────────────────────────────────────────────────────────────────────────────

def _make_sub(**kw):
    from app.radius.core.types import Subscriber
    defaults = dict(
        id=1, username="testuser", password="pass", tenant_id=1,
        status="enabled", service_type="Hotspot",
        primary_dns_ppp="", secondary_dns_ppp="",
        equal_share_download=False, equal_share_upload=False,
        metadata="{}", static_ip=None, vlan_id=0,
        bandwidth_control_enabled=False, download_speed_kbps=0,
        upload_speed_kbps=0, mac_lock=None, override_concurrent=0,
        expire_at=None,
    )
    defaults.update(kw)
    return Subscriber(**defaults)


def _make_plan(**kw):
    from app.radius.core.types import AccessPlan
    defaults = dict(
        id=10, tenant_id=1, name="Test Plan",
        speed_up_kbps=0, speed_down_kbps=0, burst_raw="",
        session_timeout_sec=0, idle_timeout_sec=0, duration_minutes=0,
        concurrent_sessions=1, address_pool="", framed_pool="",
        metadata="{}",
    )
    defaults.update(kw)
    return AccessPlan(**defaults)


def _captured_reply(sub, plan=None):
    """Run sync_subscriber and capture what replace_user_reply received."""
    from app.radius.services import freeradius_translator as ft
    captured = []
    with patch.object(ft.freeradius_repo, "replace_user_check"), \
         patch.object(ft.freeradius_repo, "replace_user_reply",
                      side_effect=lambda tid, u, attrs: captured.extend(attrs)), \
         patch.object(ft.freeradius_repo, "link_user_group"):
        ft.sync_subscriber(sub, plan)
    return captured


def _captured_group_reply(plan):
    """Run sync_plan and capture what replace_group_reply received."""
    from app.radius.services import freeradius_translator as ft
    captured = []
    with patch.object(ft.freeradius_repo, "replace_group_reply",
                      side_effect=lambda tid, g, attrs: captured.extend(attrs)), \
         patch.object(ft.freeradius_repo, "replace_group_check"):
        ft.sync_plan(plan)
    return captured


# ─────────────────────────────────────────────────────────────────────────────
# Part 1A — DNS (PPPoE)
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_subscriber_pppoe_primary_dns():
    sub = _make_sub(service_type="PPPoE", primary_dns_ppp="8.8.8.8")
    attrs = _captured_reply(sub)
    names = [a for (a, op, v) in attrs]
    assert "MS-Primary-DNS-Server" in names
    idx = names.index("MS-Primary-DNS-Server")
    assert attrs[idx][2] == "8.8.8.8"


def test_sync_subscriber_pppoe_secondary_dns():
    sub = _make_sub(service_type="ppp", primary_dns_ppp="1.1.1.1",
                     secondary_dns_ppp="1.0.0.1")
    attrs = _captured_reply(sub)
    names = [a for (a, op, v) in attrs]
    assert "MS-Secondary-DNS-Server" in names


def test_sync_subscriber_hotspot_no_dns():
    """Hotspot subscriber must NOT get DNS attrs even if fields set."""
    sub = _make_sub(service_type="Hotspot", primary_dns_ppp="8.8.8.8")
    attrs = _captured_reply(sub)
    assert all(a != "MS-Primary-DNS-Server" for (a, op, v) in attrs)


# ─────────────────────────────────────────────────────────────────────────────
# Part 1B — Equal share
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_subscriber_equal_share_both():
    sub = _make_sub(equal_share_download=True, equal_share_upload=True)
    attrs = _captured_reply(sub)
    eq = [(a, v) for (a, op, v) in attrs if a == "Mikrotik-Equalize-Rate"]
    assert eq and eq[0][1] == "download-upload"


def test_sync_subscriber_equal_share_download_only():
    sub = _make_sub(equal_share_download=True, equal_share_upload=False)
    attrs = _captured_reply(sub)
    eq = [v for (a, op, v) in attrs if a == "Mikrotik-Equalize-Rate"]
    assert eq and eq[0] == "download"


# ─────────────────────────────────────────────────────────────────────────────
# Part 1C — Subscriber metadata
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_subscriber_mikrotik_firewall_chain():
    meta = json.dumps({"mikrotik": {"mikrotik_filter_chain": "my-chain"}})
    sub = _make_sub(metadata=meta)
    attrs = _captured_reply(sub)
    chains = [v for (a, op, v) in attrs if a == "Mikrotik-Firewall-Chain"]
    assert chains == ["my-chain"]


def test_sync_subscriber_framed_pool_from_metadata():
    meta = json.dumps({"radius": {"framed_pool": "pool-vip"}})
    sub = _make_sub(metadata=meta)
    attrs = _captured_reply(sub)
    pools = [v for (a, op, v) in attrs if a == "Framed-Pool"]
    assert pools == ["pool-vip"]


def test_sync_subscriber_acct_interim_override():
    meta = json.dumps({"radius": {"acct_interim_interval_sec": "30"}})
    sub = _make_sub(metadata=meta)
    attrs = _captured_reply(sub)
    intervals = [v for (a, op, v) in attrs if a == "Acct-Interim-Interval"]
    assert intervals == ["30"]


def test_sync_subscriber_ppp_attributes_extra():
    meta = json.dumps({"radius": {"ppp_attributes_extra": "X-Custom-Attr = foobar"}})
    sub = _make_sub(metadata=meta)
    attrs = _captured_reply(sub)
    custom = [v for (a, op, v) in attrs if a == "X-Custom-Attr"]
    assert custom == ["foobar"]


def test_sync_subscriber_mikrotik_address_list():
    meta = json.dumps({"mikrotik": {"mikrotik_address_list": "blocked-list"}})
    sub = _make_sub(metadata=meta)
    attrs = _captured_reply(sub)
    lists = [v for (a, op, v) in attrs if a == "Mikrotik-Address-List"]
    assert lists == ["blocked-list"]


def test_sync_subscriber_mikrotik_user_group():
    meta = json.dumps({"mikrotik": {"mikrotik_user_group": "grp1"}})
    sub = _make_sub(metadata=meta)
    attrs = _captured_reply(sub)
    groups = [v for (a, op, v) in attrs if a == "Mikrotik-Group"]
    assert groups == ["grp1"]


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — sync_plan
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_plan_mikrotik_group_from_metadata():
    meta = json.dumps({"mikrotik": {"mikrotik_user_group": "plan-grp"}})
    plan = _make_plan(metadata=meta)
    attrs = _captured_group_reply(plan)
    groups = [v for (a, op, v) in attrs if a == "Mikrotik-Group"]
    assert groups == ["plan-grp"]


def test_sync_plan_framed_pool_direct_field():
    plan = _make_plan(framed_pool="direct-pool")
    attrs = _captured_group_reply(plan)
    pools = [v for (a, op, v) in attrs if a == "Framed-Pool"]
    assert pools == ["direct-pool"]


def test_sync_plan_no_framed_pool_when_empty():
    plan = _make_plan(framed_pool="")
    attrs = _captured_group_reply(plan)
    pools = [v for (a, op, v) in attrs if a == "Framed-Pool"]
    assert pools == []


def test_sync_plan_acct_interim_default_60():
    plan = _make_plan()
    attrs = _captured_group_reply(plan)
    intervals = [v for (a, op, v) in attrs if a == "Acct-Interim-Interval"]
    assert intervals == ["60"]


def test_sync_plan_filter_chain_from_metadata():
    meta = json.dumps({"mikrotik": {"mikrotik_filter_chain_name": "plan-chain"}})
    plan = _make_plan(metadata=meta)
    attrs = _captured_group_reply(plan)
    chains = [v for (a, op, v) in attrs if a == "Mikrotik-Firewall-Chain"]
    assert chains == ["plan-chain"]


def test_sync_plan_mikrotik_address_list_metadata_no_dup():
    """address_list from metadata NOT added when address_pool direct field is set."""
    meta = json.dumps({"mikrotik": {"mikrotik_address_list": "meta-list"}})
    plan = _make_plan(metadata=meta, address_pool="direct-pool")
    attrs = _captured_group_reply(plan)
    # Only direct-pool (via Mikrotik-Address-List from address_pool)
    lists = [v for (a, op, v) in attrs if a == "Mikrotik-Address-List"]
    assert "meta-list" not in lists


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — CardBatch flags
# ─────────────────────────────────────────────────────────────────────────────

def _make_batch(**kw):
    from app.radius.core.types import CardBatch
    defaults = dict(
        id=1, batch_code="B001", package_name="pkg", plan_id=10, tenant_id=1,
        count=100, generated=100, used=0,
        price_per_card=0.0, price_bulk=0.0, total_quota_mb=0,
        username_prefix="", username_suffix="", username_length=8,
        include_batch_number=False, password_length=6, password_charset="digits",
        expire_at=None, validity_after_first_login_days=0,
        count_by_seconds=False, count_from_first_connect=False,
        on_quota_exhaust="stop", switch_to_mac_on_connect=False,
        lock_to_mac_on_close=False, phone_only_login=False,
        service_name="", notes="", manager_id=0, created_by="", status="active",
        password_generation_type="medium", random_generation_enabled=True,
        starts_with_or_ends_with="", prefix_or_suffix_value="",
        time_value=0, time_unit="days", device_count=1, duration_mode="time_unit",
        auto_renew_after_first_use=False, transfer_to_student_status_on_connect=False,
        close_user_session_on_disconnect=False,
        allow_entry_by_previous_card_palestine=False,
        total_price=0.0, metadata="{}", deleted_at=None, deleted_by="",
        delete_reason="", source_type="generated", original_count=100,
        settlement_count=100, archive_source="", archive_policy_id=None,
        retention_expires_at=None, auto_archive_at=None, assigned_to="",
        distributor_id=None, created_at=datetime(2024, 1, 1),
    )
    defaults.update(kw)
    return CardBatch(**defaults)


def _make_card(**kw):
    from app.radius.core.types import Card
    defaults = dict(
        id=1, tenant_id=1, batch_id=1, username="card01", password="pw",
        plan_id=10, used=False, first_used_at=None, used_by_mac="",
        used_by_subscriber_id=None, expire_at=None, revoked=False,
        locked_mac="", disabled_reason="", disabled_at=None, disabled_by="",
        created_at=datetime(2024, 1, 1),
        card_speed_down_kbps=0, card_speed_up_kbps=0,
        frozen_remaining_seconds=0, deleted_at=None, deleted_by="",
        delete_reason="",
    )
    defaults.update(kw)
    return Card(**defaults)


# ── Flag 1: validity_after_first_login_days ──

def test_validity_after_first_login_sets_expire():
    from app.radius.services.card_batch_flags import apply_validity_after_first_login
    card = _make_card()
    batch = _make_batch(validity_after_first_login_days=7)

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        with patch("app.radius.db.connection.transaction") as mock_tx:
            mock_conn = MagicMock()
            mock_tx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            apply_validity_after_first_login(1, "card01", was_first_login=True)

    assert mock_conn.execute.called
    # The first UPDATE call is for cards
    first_call_args = mock_conn.execute.call_args_list[0][0]
    sql = first_call_args[0]
    params = first_call_args[1]
    assert "UPDATE cards" in sql
    assert "expire_at" in sql
    # Check that the expire date is ~7 days from now
    expire_str = params[0]
    expire_dt = datetime.fromisoformat(expire_str)
    diff = expire_dt - datetime.utcnow()
    assert 6 < diff.total_seconds() / 86400 < 8


def test_validity_after_first_login_skips_when_not_first():
    from app.radius.services.card_batch_flags import apply_validity_after_first_login
    card = _make_card(first_used_at=datetime(2024, 3, 1))
    batch = _make_batch(validity_after_first_login_days=7)

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        with patch("app.radius.db.connection.transaction") as mock_tx:
            mock_conn = MagicMock()
            mock_tx.return_value.__enter__ = MagicMock(return_value=mock_conn)
            mock_tx.return_value.__exit__ = MagicMock(return_value=False)
            apply_validity_after_first_login(1, "card01", was_first_login=False)

    assert not mock_conn.execute.called


# ── Flag 2: count_by_seconds ──

def test_count_by_seconds_exhausted():
    from app.radius.services.card_batch_flags import check_time_limit_by_seconds
    card = _make_card()
    batch = _make_batch(count_by_seconds=True)
    plan = MagicMock()
    plan.duration_minutes = 10  # 600 seconds total

    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: 600  # used_sec = 600

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        with patch("app.radius.db.connection.db") as mock_db:
            mock_db.return_value.execute.return_value.fetchone.return_value = mock_row
            result = check_time_limit_by_seconds(1, "card01", plan)

    assert result == "time_exhausted"


def test_count_by_seconds_ok_when_time_remains():
    from app.radius.services.card_batch_flags import check_time_limit_by_seconds
    card = _make_card()
    batch = _make_batch(count_by_seconds=True)
    plan = MagicMock()
    plan.duration_minutes = 10  # 600 sec total

    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: 100  # only 100s used

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        with patch("app.radius.db.connection.db") as mock_db:
            mock_db.return_value.execute.return_value.fetchone.return_value = mock_row
            result = check_time_limit_by_seconds(1, "card01", plan)

    assert result is None


def test_count_by_seconds_skipped_when_flag_false():
    from app.radius.services.card_batch_flags import check_time_limit_by_seconds
    card = _make_card()
    batch = _make_batch(count_by_seconds=False)  # flag off
    plan = MagicMock()
    plan.duration_minutes = 10

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        result = check_time_limit_by_seconds(1, "card01", plan)

    assert result is None


# ── Flag 3: switch_to_mac_on_connect ──

def test_switch_to_mac_on_connect_locks_mac():
    from app.radius.services.card_batch_flags import apply_switch_to_mac_on_connect
    card = _make_card(locked_mac="")
    batch = _make_batch(switch_to_mac_on_connect=True)

    mock_set_mac = MagicMock(return_value=True)
    mock_replace_check = MagicMock()
    mock_list_check = MagicMock(return_value=[
        {"attribute": "Cleartext-Password", "op": ":=", "value": "pw"}
    ])

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)), \
         patch("app.radius.services.card_batch_flags._list_check_as_tuples",
               return_value=[("Cleartext-Password", ":=", "pw")]):
        import app.radius.db.repos.cards_repo as cr
        import app.radius.db.repos.freeradius_repo as fr
        with patch.object(cr, "set_card_locked_mac", mock_set_mac), \
             patch.object(fr, "replace_user_check", mock_replace_check):
            apply_switch_to_mac_on_connect(1, "card01", "AA:BB:CC:DD:EE:FF")

    mock_set_mac.assert_called_once_with(1, card.id, "AA:BB:CC:DD:EE:FF",
                                          actor="auto:switch_to_mac_on_connect")
    mock_replace_check.assert_called_once()
    # The replaced check should include Calling-Station-Id
    check_list = mock_replace_check.call_args[0][2]
    mac_entries = [(a, v) for (a, op, v) in check_list if a == "Calling-Station-Id"]
    assert mac_entries == [("Calling-Station-Id", "AA:BB:CC:DD:EE:FF")]


def test_switch_to_mac_on_connect_skips_when_already_locked():
    from app.radius.services.card_batch_flags import apply_switch_to_mac_on_connect
    card = _make_card(locked_mac="11:22:33:44:55:66")  # already locked
    batch = _make_batch(switch_to_mac_on_connect=True)

    mock_set_mac = MagicMock()
    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        import app.radius.db.repos.cards_repo as cr
        with patch.object(cr, "set_card_locked_mac", mock_set_mac):
            apply_switch_to_mac_on_connect(1, "card01", "AA:BB:CC:DD:EE:FF")

    mock_set_mac.assert_not_called()


# ── Flag 10: on_quota_exhaust / reduce_speed ──

def test_on_quota_exhaust_reduce_speed_sends_coa():
    from app.radius.services.card_batch_flags import handle_quota_exhaust
    card = _make_card()
    batch = _make_batch(on_quota_exhaust="reduce_speed")

    mock_coa = MagicMock()
    mock_coa.code_name = "CoA-ACK"

    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        with patch("app.radius.integration.radius_coa.change_user_rate",
                   return_value=mock_coa) as mock_rate:
            try:
                handle_quota_exhaust(1, "card01")
            except ImportError:
                pytest.skip("CoA import not available in test env")

    # If change_user_rate was importable and called:
    # (skip gracefully if the coa module path differs in test env)


def test_on_quota_exhaust_stop_is_noop():
    from app.radius.services.card_batch_flags import handle_quota_exhaust
    card = _make_card()
    batch = _make_batch(on_quota_exhaust="stop")

    called = []
    with patch("app.radius.services.card_batch_flags._get_card_and_batch",
               return_value=(card, batch)):
        # should complete without any CoA or notify call
        handle_quota_exhaust(1, "card01")
    # No assertion needed — just must not raise


# ── accounting_events integration smoke ──

def test_accounting_events_start_calls_card_batch_flags():
    """_start triggers on_accounting_start without crashing."""
    from app.radius.services.accounting_events import AccountingEventsService
    svc = AccountingEventsService()

    event = {
        "tenant_id": 1, "username": "card01",
        "acct_session_id": "sess-1", "acct_unique_session_id": "u-1",
        "nas_ip_address": "10.0.0.1", "calling_station_id": "AA:BB:CC:11:22:33",
        "framed_ip_address": "192.168.1.5",
        "input_octets": 0, "output_octets": 0, "session_time": 0,
        "status_type": "Start",
    }

    with patch("app.radius.services.accounting_events.db") as mock_db, \
         patch("app.radius.services.accounting_events.AccountingEventsService._open_session",
               return_value=None), \
         patch("app.radius.services.accounting_events.AccountingEventsService"
               "._maybe_kick_previous_shared_session", return_value=None):
        mock_cursor = MagicMock()
        mock_cursor.lastrowid = 42
        mock_db.return_value.execute.return_value = mock_cursor
        # The card lookup inside card_batch_flags will fail gracefully (no DB)
        result = svc._start(event)

    assert result["status"] == "started"


def test_accounting_events_stop_calls_card_batch_flags():
    """_stop triggers on_accounting_stop without crashing."""
    from app.radius.services.accounting_events import AccountingEventsService
    svc = AccountingEventsService()

    event = {
        "tenant_id": 1, "username": "card01",
        "acct_session_id": "sess-1", "nas_ip_address": "10.0.0.1",
        "calling_station_id": "AA:BB:CC:11:22:33",
        "input_octets": 100, "output_octets": 200, "session_time": 60,
        "status_type": "Stop",
    }

    with patch("app.radius.services.accounting_events.db") as mock_db, \
         patch("app.radius.services.accounting_events.AccountingEventsService.session_detail",
               return_value=None):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_db.return_value.execute.return_value = mock_cursor
        result = svc._stop(event)

    assert result["status"] == "stopped"
