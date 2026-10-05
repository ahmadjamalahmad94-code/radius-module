# -*- coding: utf-8 -*-
"""A batch/plan time change reaches the cards that ALREADY started.

client20 · 2026-10-05 · batch 66 «علاء نت» (time_value=0, from first connect)
was imported on plan «ساعة» (60 min) and then moved to «2 ميجا - 16 ساعة»
(960 min). Card 77821145 had logged in on the 1-hour plan ⇒ ``expire_at`` was
stamped first_used + 1h. After the move the checker / app said «متبقّي ≈ 15h57m»
while RADIUS rejected at one hour («انتهت صلاحية الاشتراك»).

Owner decision («أ»): started cards take the NEW duration counted from their
own first login (expire_at = first_used_at + new budget + extra_seconds), and
every display reads the stamped ``expire_at`` — what RADIUS honours.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

TOKEN = "restamp-token"
AUTH = {"Authorization": "Bearer " + TOKEN}
MAC = "AA:BB:CC:DD:EE:01"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "restamp.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "restamp-secret")
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
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
        yield application


@pytest.fixture
def live(monkeypatch):
    """Stub every router call: CoA / disconnect are recorded, never sent; the
    background push runs inline; the policy reconciler is silenced."""
    from app.radius.integration import radius_coa
    from app.radius.services import policy_reconciler
    try:
        from app.radius.services import card_restamp
    except ImportError:          # the pre-fix tree (proof run): nothing to inline
        card_restamp = None

    calls = {"timeout": [], "disconnect": []}

    class _Res:
        def __init__(self, ok):
            self.ok = ok
            self.code_name = "ack" if ok else "nak"

    state = {"coa_ok": True}

    def _timeout(tenant_id, username, *, session_timeout):
        calls["timeout"].append((username, int(session_timeout)))
        return _Res(state["coa_ok"])

    def _disconnect(tenant_id, username, *, session_ids=None):
        calls["disconnect"].append(username)
        return _Res(True)

    monkeypatch.setattr(radius_coa, "change_user_session_timeout", _timeout)
    monkeypatch.setattr(radius_coa, "disconnect_user", _disconnect)
    if card_restamp is not None:
        monkeypatch.setattr(card_restamp, "_spawn", lambda fn: fn())
    monkeypatch.setattr(policy_reconciler, "reconcile_active_sessions_against_policy",
                        lambda *a, **k: None)
    calls["state"] = state
    return calls


# ─────────────── helpers ───────────────

def _db():
    from app.radius.db.connection import db
    return db()


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(str(s).replace("Z", "").replace(" ", "T"))


def _plan(name: str, minutes: int, days: int = 0) -> int:
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days, price,"
        " currency, speed_down_kbps, speed_up_kbps, quota_total_mb, enabled,"
        " created_at, updated_at) VALUES(1,?,?,?,1,'ILS',2048,1024,0,1,"
        " datetime('now'),datetime('now'))", (name, minutes, days))
    return int(cur.lastrowid)


def _imported_batch(plan_id: int, *, count: int = 1, time_value: int = 0,
                    time_unit: str = "days", from_first: int = 1, by_seconds: int = 0,
                    validity_days: int = 0):
    """A batch like client20's batch 66: the time budget comes from the PLAN
    (time_value = 0, from first connect)."""
    from app.radius.services.cards import get_cards_service
    batch, cards = get_cards_service().generate_batch(
        actor="t", plan_id=plan_id, count=count, username_length=8,
        password_length=6, price_per_card=1.0, package_name="علاء نت")
    _db().execute(
        "UPDATE card_batches SET time_value=?, time_unit=?, count_from_first_connect=?,"
        " count_by_seconds=?, validity_after_first_login_days=? WHERE id=?",
        (time_value, time_unit, from_first, by_seconds, validity_days, batch.id))
    rows = _db().execute("SELECT id, username, password FROM cards WHERE batch_id=?"
                         " ORDER BY id", (batch.id,)).fetchall()
    for r in rows:   # the auth mirror the import writes
        if not _db().execute("SELECT 1 FROM subscribers WHERE username=?",
                             (r["username"],)).fetchone():
            _db().execute(
                "INSERT INTO subscribers(tenant_id, username, password, user_type, status,"
                " plan_id, card_batch_id, created_at) VALUES(1,?,?,'card','enabled',?,?,?)",
                (r["username"], r["password"], plan_id, batch.id, _iso(datetime.utcnow())))
    return int(batch.id), [dict(r) for r in rows]


def _first_login(username: str) -> None:
    """The REAL first-login stamp path (policy_engine + card_batch_flags)."""
    from types import SimpleNamespace
    from app.radius.services.policy_engine import _do_update_login_timestamps
    req = SimpleNamespace(tenant_id=1, username=username, calling_station_id=MAC)
    _do_update_login_timestamps(req, source="card", now=datetime.utcnow())


def _age(username: str, hours: float) -> None:
    """Pretend the first login happened ``hours`` earlier (shift the stamp)."""
    row = _db().execute("SELECT first_used_at, expire_at FROM cards WHERE username=?",
                        (username,)).fetchone()
    d = timedelta(hours=hours)
    first = _iso(_parse(row["first_used_at"]) - d)
    exp = _iso(_parse(row["expire_at"]) - d) if row["expire_at"] else None
    _db().execute("UPDATE cards SET first_used_at=?, expire_at=? WHERE username=?",
                  (first, exp, username))
    _db().execute("UPDATE subscribers SET expire_at=? WHERE username=?", (exp, username))


def _ends(username: str):
    card = _db().execute("SELECT first_used_at, expire_at FROM cards WHERE username=?",
                         (username,)).fetchone()
    mirror = _db().execute("SELECT expire_at FROM subscribers WHERE username=?",
                           (username,)).fetchone()
    return (_parse(card["first_used_at"]),
            _parse(card["expire_at"]) if card["expire_at"] else None,
            _parse(mirror["expire_at"]) if mirror["expire_at"] else None)


def _authorize(card: dict):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=card["username"], password=card["password"],
                                 tenant_id=1, calling_station_id=MAC))


def _open_session(username: str) -> None:
    _db().execute(
        "INSERT INTO radacct(tenant_id, username, acctsessionid, acctuniqueid,"
        " acctstarttime, acctsessiontime, nasipaddress, callingstationid)"
        " VALUES(1,?,?,?,?,0,'10.0.0.1',?)",
        (username, "s-" + username, "u-" + username,
         datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"), MAC))


def _web_edit(app, batch_id: int, **fields):
    data = {"_csrf_token": "off-csrf", "count": "1", "username_length": "8",
            "password_length": "6", "count_from_first_connect": "1",
            "time_value": "0", "time_unit": "days", "price_per_card": "1"}
    data.update({k: str(v) for k, v in fields.items()})
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["admin_user"] = "owner_root"
            sess["is_super_admin"] = True
            sess["tenant_id"] = 1
            sess["_csrf_token"] = "off-csrf"
        return client.post(f"/admin/radius/cards/batches/{batch_id}/edit",
                           data=data, follow_redirects=False)


def _close(a: datetime, b: datetime, tol: float = 5) -> bool:
    return abs((a - b).total_seconds()) <= tol


# ═══ 1. the incident — web batch edit ═════════════════════════════════════

def test_incident_web_move_to_16h_plan_restamps_started_card_and_auth_accepts(app, live):
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("2 ميجا - 16 ساعة", 960)
        bid, cards = _imported_batch(hour)
        card = cards[0]
        _first_login(card["username"])
        first, exp, mirror = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=1))   # stamped on the 1h plan
        _age(card["username"], 2)                         # two hours later…
        before = _authorize(card)
        assert not before.ok and before.reason == "expired", before

    res = _web_edit(app, bid, plan_id=sixteen)
    assert res.status_code in (302, 303), res.data[:400]

    with app.app_context():
        assert int(_db().execute("SELECT plan_id FROM card_batches WHERE id=?",
                                 (bid,)).fetchone()[0]) == sixteen
        first, exp, mirror = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=16)), (first, exp)
        assert mirror == exp, "the auth mirror must move with the card"
        after = _authorize(card)
        assert after.ok, after
        st = int(after.reply_attrs.get("Session-Timeout") or 0)
        assert 13.9 * 3600 < st <= 14 * 3600 + 5, st
        audit = _db().execute("SELECT payload_json FROM audit_log WHERE action="
                              "'cards.restamp_started' AND target_id=?",
                              (str(bid),)).fetchall()
        assert len(audit) == 1 and '"changed": 1' in audit[0][0]


# ═══ 2. the incident — API PATCH ══════════════════════════════════════════

def test_incident_api_patch_plan_restamps_started_card(app, live):
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 2)
        assert _authorize(card).reason == "expired"
    res = app.test_client().patch(f"/api/v1/cards/batches/{bid}", headers=AUTH,
                                  json={"plan_id": sixteen})
    assert res.status_code == 200, res.get_json()
    with app.app_context():
        first, exp, mirror = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=16))
        assert mirror == exp
        assert _authorize(card).ok


# ═══ 3. plan duration edit (PlansService.update) ═════════════════════════

def test_plan_duration_edit_restamps_cards_of_batches_on_that_plan(app, live):
    with app.app_context():
        hour = _plan("ساعة", 60)
        bid, cards = _imported_batch(hour, count=2)
        own_bid, own_cards = _imported_batch(hour, time_value=3, time_unit="hours")
        for c in cards + own_cards:
            _first_login(c["username"])
            _age(c["username"], 2)
        from app.radius.db.repos import plans_repo
        from app.radius.services.plans import get_plans_service
        from dataclasses import replace
        plan = plans_repo.get_plan(1, hour)
        get_plans_service().update(actor="owner", plan=replace(plan, duration_minutes=960))
        for c in cards:
            first, exp, mirror = _ends(c["username"])
            assert _close(exp, first + timedelta(hours=16)), c
            assert mirror == exp
            assert _authorize(c).ok
        # a batch with its own window («3 ساعات») does not take the plan's.
        first, exp, _ = _ends(own_cards[0]["username"])
        assert _close(exp, first + timedelta(hours=3))


# ═══ 4. shortening: already over ⇒ expired now, rejected, disconnected ═══

def test_shortening_below_used_time_expires_now_and_disconnects(app, live):
    with app.app_context():
        sixteen = _plan("16 ساعة", 960)
        hour = _plan("ساعة", 60)
        bid, cards = _imported_batch(sixteen)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 2)
        _open_session(card["username"])
        assert _authorize(card).reason != "expired"
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        svc.update_batch(actor="owner", batch_id=bid, data={"plan_id": hour})
        first, exp, mirror = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=1))
        assert mirror == exp
        assert svc.last_realign_summary()["expired_now"] == 1
        assert _authorize(card).reason == "expired"
        assert live["disconnect"] == [card["username"]]
        assert live["timeout"] == []


def test_lengthening_pushes_new_session_timeout_to_live_session(app, live):
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 0.5)
        _open_session(card["username"])
        from app.radius.services.cards import get_cards_service
        get_cards_service().update_batch(actor="owner", batch_id=bid,
                                         data={"plan_id": sixteen})
        assert len(live["timeout"]) == 1
        user, secs = live["timeout"][0]
        assert user == card["username"] and abs(secs - 15.5 * 3600) < 10
        assert live["disconnect"] == []


def test_refused_coa_on_a_shorter_window_falls_back_to_disconnect(app, live):
    live["state"]["coa_ok"] = False      # MikroTik: Unsupported-Extension
    with app.app_context():
        sixteen = _plan("16 ساعة", 960)
        four = _plan("4 ساعات", 240)
        bid, cards = _imported_batch(sixteen)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 1)
        _open_session(card["username"])
        from app.radius.services.cards import get_cards_service
        get_cards_service().update_batch(actor="owner", batch_id=bid,
                                         data={"plan_id": four})
        assert len(live["timeout"]) == 1
        assert live["disconnect"] == [card["username"]]


# ═══ 5. what must survive: grants, revoked, unstarted, thaw ═══════════════

def test_operator_grant_is_kept_revoked_and_unstarted_untouched(app, live):
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour, count=3)
        granted, revoked, unstarted = cards
        for c in (granted, revoked):
            _first_login(c["username"])
            _age(c["username"], 0.5)
        from app.radius.db.repos import cards_repo
        cards_repo.grant_card_time(1, granted["id"], 2 * 3600)      # +2h «إضافة وقت»
        _db().execute("UPDATE cards SET revoked=1 WHERE id=?", (revoked["id"],))
        rev_before = _ends(revoked["username"])
        from app.radius.services.cards import get_cards_service
        get_cards_service().update_batch(actor="owner", batch_id=bid,
                                         data={"plan_id": sixteen})
        first, exp, mirror = _ends(granted["username"])
        assert _close(exp, first + timedelta(hours=18)), (first, exp)   # 16h + 2h
        assert mirror == exp
        assert _ends(revoked["username"]) == rev_before
        row = _db().execute("SELECT first_used_at, expire_at FROM cards WHERE id=?",
                            (unstarted["id"],)).fetchone()
        assert row["first_used_at"] is None and row["expire_at"] is None


def test_thawed_card_keeps_its_paused_time(app, live):
    """«تعطيل» ثمّ «تفعيل» يمدّ النهاية بزمن الإيقاف (expire = now + frozen)
    — وقتٌ أضافه المشغّل خارج الميزانية فلا يُمحى بإعادة الختم."""
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour)
        card = cards[0]
        _first_login(card["username"])
        first, exp, _ = _ends(card["username"])
        paused = timedelta(hours=3)
        _db().execute("UPDATE cards SET expire_at=? WHERE id=?",
                      (_iso(exp + paused), card["id"]))
        from app.radius.services.cards import get_cards_service
        get_cards_service().update_batch(actor="owner", batch_id=bid,
                                         data={"plan_id": sixteen})
        first, exp, _ = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=16) + paused), exp


def test_stale_stamp_is_corrected_on_a_plain_resave(app, live):
    """Data already broken before this fix (client20's card): the batch is
    already on the 16h plan and the card still carries its 1h stamp — the
    owner only has to re-save the batch."""
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 2)
        _db().execute("UPDATE card_batches SET plan_id=? WHERE id=?", (sixteen, bid))
    res = _web_edit(app, bid, plan_id=sixteen)
    assert res.status_code in (302, 303)
    with app.app_context():
        first, exp, mirror = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=16))
        assert _authorize(card).ok


# ═══ 6. by-seconds: only the calendar cap, grant shift carried ═══════════

def test_by_seconds_card_moves_only_its_calendar_cap(app, live):
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour, time_value=10, time_unit="hours",
                                     from_first=0, by_seconds=1, validity_days=30)
        card = cards[0]
        _first_login(card["username"])
        first, exp, _ = _ends(card["username"])
        assert _close(exp, first + timedelta(days=30))        # the calendar cap
        # an operator grant shifted the cap by +1 day
        _db().execute("UPDATE cards SET expire_at=? WHERE id=?",
                      (_iso(exp + timedelta(days=1)), card["id"]))
        from app.radius.services.cards import get_cards_service
        svc = get_cards_service()
        # the plan is NOT a calendar cap for by-seconds: a plan move keeps it
        svc.update_batch(actor="owner", batch_id=bid, data={"plan_id": sixteen})
        _, exp2, _ = _ends(card["username"])
        assert _close(exp2, first + timedelta(days=31))
        # the cap itself changes 30 → 10 days: moved, the +1 day grant carried
        svc.update_batch(actor="owner", batch_id=bid,
                         data={"validity_after_first_login_days": 10})
        _, exp3, mirror = _ends(card["username"])
        assert _close(exp3, first + timedelta(days=11)), exp3
        assert mirror == exp3


# ═══ 7. display: every reader honours the stamp ══════════════════════════

def test_display_readers_use_the_stamped_end_not_the_current_budget(app, live):
    """Before the restamp ran (or for any stamp that differs from the current
    budget) the checker, the /online brief and the time cell must show what
    RADIUS will honour: expire_at − now."""
    with app.app_context():
        hour = _plan("ساعة", 60)
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(hour)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 0.5)                  # 30 min left on the stamp
        # the batch moved WITHOUT restamp (old code / direct SQL)
        _db().execute("UPDATE card_batches SET plan_id=? WHERE id=?", (sixteen, bid))
        _db().execute("UPDATE cards SET plan_id=? WHERE id=?", (sixteen, card["id"]))
        from app.radius.services.card_checker import card_time_brief, check_card
        full = check_card(1, card["username"])
        assert abs(full["remaining_seconds"] - 1800) < 10, full["remaining_seconds"]
        assert abs(full["consumed_seconds"] - 1800) < 10, full["consumed_seconds"]
        brief = card_time_brief(1, card["username"])
        assert abs(brief["remaining_seconds"] - 1800) < 10, brief
        assert abs(brief["used_seconds"] - 1800) < 10, brief
        from app.radius.services.online_time_budget import _card_cells
        cell = _card_cells(1, [card["username"]], datetime.utcnow())[card["username"]]
        assert cell, cell
        assert abs(int(cell["used_sec"]) - 1800) < 10, cell
        assert abs(int(cell["total_sec"]) - 3600) < 10, cell   # the enforced window


# ═══ 8. sibling: «إضافة وقت» on a plan-budget batch ═══════════════════════

def test_grant_on_a_plan_budget_card_extends_from_the_plan_window(app, live):
    """client20's batch takes its budget from the PLAN (time_value=0). The
    grant computed a zero budget there ⇒ «إضافة ساعة» on a live card stamped
    first_used + 1h and killed it instead of extending 16h to 17h."""
    with app.app_context():
        sixteen = _plan("16 ساعة", 960)
        bid, cards = _imported_batch(sixteen)
        card = cards[0]
        _first_login(card["username"])
        _age(card["username"], 2)
        from app.radius.db.repos import cards_repo
        res = cards_repo.grant_card_time(1, card["id"], 3600)
        first, exp, mirror = _ends(card["username"])
        assert _close(exp, first + timedelta(hours=17)), (first, exp)
        assert mirror == exp
        assert abs(res["remaining_after"] - 15 * 3600) < 10, res
        assert _authorize(card).ok
