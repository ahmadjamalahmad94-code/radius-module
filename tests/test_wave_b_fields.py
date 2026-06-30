"""Wave-2 «كل حقل يَعمل فعلًا»: اختبارات إصدار/إنفاذ الحقول المخزَّنة سابقًا
دون أثر — للمشترك (Part A: DNS/MikroTik/Framed-Pool/PPP/interim) ولأعلام دفعة
البطاقات (Part B: on_quota_exhaust/count_by_seconds/أول اتصال/MAC/auto_renew/فصل).

كل اختبار يَبني app + DB مؤقّتًا معزولًا (نفس نمط test_policy_engine)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_waveb_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


# ════════════════════ helpers ════════════════════


def _mk_subscriber(username="sub1", *, password="p", metadata=None, **extra):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    md = json.dumps(metadata) if isinstance(metadata, dict) else (metadata or "{}")
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password=password,
        status="enabled", metadata=md, **extra))


def _mk_plan(**kw):
    from app.radius.core.types import AccessPlan
    from app.radius.db.repos import plans_repo
    base = dict(id=None, tenant_id=1, name=kw.pop("name", "P"))
    base.update(kw)
    return plans_repo.upsert_plan(AccessPlan(**base))


def _mk_batch(**kw):
    from app.radius.core.types import CardBatch
    from app.radius.db.repos import cards_repo
    base = dict(id=None, tenant_id=1, batch_code="", plan_id=kw.pop("plan_id"),
                count=1)
    base.update(kw)
    return cards_repo.create_batch(CardBatch(**base))


def _mk_card(batch, plan_id):
    from app.radius.db.repos import cards_repo
    cards = cards_repo.generate_cards(tenant_id=1, batch_id=batch.id,
                                      plan_id=plan_id, count=1)
    return cards[0]


def _insert_radacct(username, *, inb=0, outb=0, sess=0, nas="198.51.100.10",
                    sid=None):
    from app.radius.db.connection import db
    sid = sid or f"sess-{username}"
    db().execute(
        "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctinputoctets, acctoutputoctets, "
        "acctsessiontime) VALUES (1,?,?,?,?,?,?,?,?)",
        (sid, f"u-{sid}", username, nas, "2026-06-01T00:00:00",
         inb, outb, sess))


def _authorize(username, password="p", mac=""):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password=password,
                                 tenant_id=1, calling_station_id=mac))


# ════════════════════ Part A — subscriber reply attrs ════════════════════


def test_dns_attrs_emitted():
    app = _fresh_app()
    with app.app_context():
        _mk_subscriber("dnsuser", primary_dns_ppp="8.8.8.8",
                       secondary_dns_ppp="1.1.1.1")
        d = _authorize("dnsuser")
        assert d.ok is True
        assert d.reply_attrs.get("MS-Primary-DNS-Server") == "8.8.8.8"
        assert d.reply_attrs.get("MS-Secondary-DNS-Server") == "1.1.1.1"


def test_mikrotik_panel_attrs_emitted():
    app = _fresh_app()
    with app.app_context():
        meta = {"mikrotik": {
            "mikrotik_filter_chain": "guest_chain",
            "mikrotik_address_list": "vip_list",
            "mikrotik_framed_route": "10.9.0.0/24 10.9.0.1 1",
            "mikrotik_user_group": "premium",
        }}
        _mk_subscriber("mtuser", metadata=meta)
        d = _authorize("mtuser")
        assert d.ok is True
        assert d.reply_attrs.get("Filter-Id") == "guest_chain"
        assert d.reply_attrs.get("Mikrotik-Address-List") == "vip_list"
        assert d.reply_attrs.get("Framed-Route") == "10.9.0.0/24 10.9.0.1 1"
        assert d.reply_attrs.get("Mikrotik-Group") == "premium"


def test_framed_pool_emitted():
    app = _fresh_app()
    with app.app_context():
        _mk_subscriber("pooluser", metadata={"radius": {"framed_pool": "pppoe-pool"}})
        d = _authorize("pooluser")
        assert d.ok is True
        assert d.reply_attrs.get("Framed-Pool") == "pppoe-pool"


def test_acct_interim_override():
    app = _fresh_app()
    with app.app_context():
        # الافتراض 60 — يُتجاوَز عند ضبط القيمة الفرديّة.
        _mk_subscriber("intuser",
                       metadata={"radius": {"acct_interim_interval_sec": "300"}})
        d = _authorize("intuser")
        assert d.ok is True
        assert d.reply_attrs.get("Acct-Interim-Interval") == "300"


def test_acct_interim_defaults_to_60_when_unset():
    app = _fresh_app()
    with app.app_context():
        _mk_subscriber("intdef")
        d = _authorize("intdef")
        assert d.reply_attrs.get("Acct-Interim-Interval") == "60"


def test_ppp_attributes_extra_parsed_and_emitted():
    app = _fresh_app()
    with app.app_context():
        _mk_subscriber("pppuser", metadata={"radius": {
            "ppp_attributes_extra": "Idle-Timeout := 600\nPort-Limit = 2"}})
        d = _authorize("pppuser")
        assert d.ok is True
        assert d.reply_attrs.get("Idle-Timeout") == "600"
        assert d.reply_attrs.get("Port-Limit") == "2"


def test_queue_priority_augments_rate_limit():
    app = _fresh_app()
    with app.app_context():
        _mk_subscriber("qpuser", bandwidth_control_enabled=True,
                       download_speed_kbps=2000, upload_speed_kbps=1000,
                       metadata={"mikrotik": {"mikrotik_queue_priority": "3"}})
        d = _authorize("qpuser")
        assert d.ok is True
        rate = d.reply_attrs.get("Mikrotik-Rate-Limit")
        # base "1000k/2000k" + priority dropped into the 5th positional token.
        assert rate.startswith("1000k/2000k")
        assert rate.split()[-1] == "3"


def test_equal_share_and_winbox_group_not_emitted_flagged():
    """العَلَمان المُؤجَّلان لا يُنتجان attrs (لا VSA مناسب) — لا يَكسران الـaccept."""
    app = _fresh_app()
    with app.app_context():
        _mk_subscriber("flaguser", equal_share_download=True,
                       equal_share_upload=True,
                       metadata={"mikrotik": {"mikrotik_winbox_group": "full"}})
        d = _authorize("flaguser")
        assert d.ok is True
        # winbox_group يَتقاسم Mikrotik-Group مع user_group → لا يُصدَر منفردًا.
        assert "Mikrotik-Group" not in d.reply_attrs
        # لا يوجد attr «حصة عادلة».
        assert not any("Equal" in k or "Share" in k for k in d.reply_attrs)


# ════════════════════ Part B — card-batch behaviour flags ════════════════════


def test_count_by_seconds_cutoff():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan(duration_minutes=10)        # 600s budget
        batch = _mk_batch(plan_id=plan.id, count_by_seconds=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        _insert_radacct(card.username, sess=700)      # exceeds 600s
        d = _authorize(card.username, password=card.password)
        assert d.ok is False
        assert d.reason == "card_time_exhausted"


def test_count_by_seconds_allows_within_budget():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan(duration_minutes=10)
        batch = _mk_batch(plan_id=plan.id, count_by_seconds=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        _insert_radacct(card.username, sess=120)      # within 600s
        d = _authorize(card.username, password=card.password)
        assert d.ok is True


def test_first_login_validity_materialized():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, count_from_first_connect=True,
                          validity_after_first_login_days=7)
        card = _mk_card(batch, plan.id)
        assert card.expire_at is None
        d = _authorize(card.username, password=card.password, mac="AA:BB:CC:00:11:22")
        assert d.ok is True
        from app.radius.db.repos import cards_repo
        fresh = cards_repo.get_card_by_username(1, card.username)
        assert fresh.expire_at is not None
        # ≈ now + 7 days
        delta = fresh.expire_at - datetime.utcnow()
        assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)


def test_switch_to_mac_on_connect_binds():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, switch_to_mac_on_connect=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        assert not card.locked_mac
        d = _authorize(card.username, password=card.password,
                       mac="DE:AD:BE:EF:00:01")
        assert d.ok is True
        from app.radius.db.repos import cards_repo
        fresh = cards_repo.get_card_by_username(1, card.username)
        assert fresh.locked_mac == "DE:AD:BE:EF:00:01"


def test_transfer_to_student_status_on_connect():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id,
                          transfer_to_student_status_on_connect=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        # mirror subscribers row so the account_type UPDATE is observable.
        _mk_subscriber(card.username, password=card.password, user_type="card",
                       card_batch_id=batch.id, plan_id=plan.id,
                       account_type="Personal")
        d = _authorize(card.username, password=card.password)
        assert d.ok is True
        from app.radius.db.repos import subscribers_repo
        s = subscribers_repo.get_subscriber(1, card.username)
        assert s.account_type == "student"


def test_on_quota_exhaust_stop_rejects():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, total_quota_mb=10,
                          on_quota_exhaust="stop", count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        _insert_radacct(card.username, inb=6 * 1048576, outb=6 * 1048576)  # 12MB>10
        d = _authorize(card.username, password=card.password)
        assert d.ok is False
        assert d.reason == "quota_exhausted"


def test_on_quota_exhaust_reduce_speed_throttles():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan(speed_down_kbps=5000, speed_up_kbps=5000)
        batch = _mk_batch(plan_id=plan.id, total_quota_mb=10,
                          on_quota_exhaust="reduce_speed",
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        _insert_radacct(card.username, inb=6 * 1048576, outb=6 * 1048576)
        d = _authorize(card.username, password=card.password)
        assert d.ok is True                       # NOT rejected
        assert d.reply_attrs.get("Mikrotik-Rate-Limit") == "128k/128k"


def test_on_quota_exhaust_notify_allows():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, total_quota_mb=10,
                          on_quota_exhaust="notify",
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        _insert_radacct(card.username, inb=6 * 1048576, outb=6 * 1048576)
        d = _authorize(card.username, password=card.password)
        assert d.ok is True
        assert d.reason != "quota_exhausted"


def test_auto_renew_after_first_use_renews_expired():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan(validity_days=30)
        batch = _mk_batch(plan_id=plan.id, auto_renew_after_first_use=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        # make it expired + already used.
        from app.radius.db.connection import db
        past = (datetime.utcnow() - timedelta(days=2)).isoformat()
        used = (datetime.utcnow() - timedelta(days=40)).isoformat()
        db().execute("UPDATE cards SET expire_at=?, first_used_at=?, used=1 "
                     "WHERE tenant_id=1 AND id=?", (past, used, card.id))
        d = _authorize(card.username, password=card.password)
        assert d.ok is True
        assert d.reason != "expired"
        from app.radius.db.repos import cards_repo
        fresh = cards_repo.get_card_by_username(1, card.username)
        assert fresh.expire_at > datetime.utcnow()


def test_lock_to_mac_on_close_binds_on_stop():
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, lock_to_mac_on_close=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        from app.radius.services.accounting_events import AccountingEventsService
        svc = AccountingEventsService()
        # open then stop a session carrying the client MAC.
        svc.ingest(tenant_id=1, payload={
            "Acct-Status-Type": "Start", "User-Name": card.username,
            "Acct-Session-Id": "s1", "NAS-IP-Address": "198.51.100.10",
            "Calling-Station-Id": "11:22:33:44:55:66"})
        svc.ingest(tenant_id=1, payload={
            "Acct-Status-Type": "Stop", "User-Name": card.username,
            "Acct-Session-Id": "s1", "NAS-IP-Address": "198.51.100.10",
            "Calling-Station-Id": "11:22:33:44:55:66"})
        from app.radius.db.repos import cards_repo
        fresh = cards_repo.get_card_by_username(1, card.username)
        assert fresh.locked_mac == "11:22:33:44:55:66"


def test_close_user_session_on_disconnect_safe_noop():
    """بلا جلسات أخرى نشِطة، الفصل عند الإغلاق no-op آمن (لا يَكسر المحاسبة)."""
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, close_user_session_on_disconnect=True,
                          count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        from app.radius.services.accounting_events import AccountingEventsService
        svc = AccountingEventsService()
        svc.ingest(tenant_id=1, payload={
            "Acct-Status-Type": "Start", "User-Name": card.username,
            "Acct-Session-Id": "s1", "NAS-IP-Address": "198.51.100.10",
            "Calling-Station-Id": "11:22:33:44:55:66"})
        res = svc.ingest(tenant_id=1, payload={
            "Acct-Status-Type": "Stop", "User-Name": card.username,
            "Acct-Session-Id": "s1", "NAS-IP-Address": "198.51.100.10",
            "Calling-Station-Id": "11:22:33:44:55:66"})
        assert res["status"] == "stopped"


def test_card_quota_not_exhausted_still_accepts():
    """تأكيد عدم الانحدار: بطاقة دون سقف دفعة وبلا استهلاك تُقبَل عاديًّا."""
    app = _fresh_app()
    with app.app_context():
        plan = _mk_plan()
        batch = _mk_batch(plan_id=plan.id, count_from_first_connect=False)
        card = _mk_card(batch, plan.id)
        d = _authorize(card.username, password=card.password)
        assert d.ok is True
