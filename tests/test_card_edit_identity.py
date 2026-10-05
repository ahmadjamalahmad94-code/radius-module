# -*- coding: utf-8 -*-
"""card-edit-identity (owner 2026-10-05) — «تعديل بيانات الكرت»: رقم الكرت
و/أو كلمة المرور، فعّالًا من طرفٍ لطرف — لا في الواجهة والقاعدة فقط.

«مش تقلّي تعدّل بالواجهة وبقاعدة البيانات ولا الفري ريدياس ما تعدّل» — لذلك
البرهان هنا عبر **المسار نفسه** الذي يناديه FreeRADIUS (rlm_rest →
/api/v1/internal/auth → policy_engine.authorize):

  * بعد تغيير الرقم: الجديد + الكلمة يُقبل، القديم يُرفض.
  * بعد تغيير الكلمة: الجديدة تُقبل، القديمة تُرفض.
  * المحاسبة (Start/Interim) باسم الجديد تقع على البطاقة نفسها: الوقت
    والاستهلاك يستمرّان، النافذة لا تُعاد، المتبقّي لا يتغيّر.
  * الجلسة الحيّة طُردت (CoA stub) **باسمها القديم** وقبل المتتالية.
  * الرقم وحده / الكلمة وحدها / كلاهما؛ الفارغ = بلا تغيير؛ لا توليد.
  * التفرّد/الصياغة/الصلاحيات/النطاق/حزمة «رقم فقط».
  * الكلّ أو لا شيء: فشلٌ في منتصف العمليّة لا يترك نصف تغيير.

شغّل هذا الملف وحده (عزل الاختبارات لكل ملف).
"""
from __future__ import annotations

import os
from datetime import datetime
from uuid import uuid4

import pytest

MAC = "AA:BB:CC:DD:EE:01"
TID = 1


# ════════════════════════════════════════════════════════════════════════
# Fixture: fresh migrated DB, real sqlite adapter, CoA stubbed
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "card_identity.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("RADIUS_MODE", "sqlite")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app.radius.integration.factory import reset_radius_adapter_for_tests
    reset_radius_adapter_for_tests()
    from app import create_app
    flask_app = create_app()
    flask_app.testing = True
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield flask_app
    reset_radius_adapter_for_tests()


@pytest.fixture
def coa(monkeypatch):
    """CoA-Disconnect stub (no real routers). Records the username the NAS is
    asked to drop AND what radacct said at that instant, then behaves like the
    Acct-Stop that follows a successful Disconnect: closes the open rows."""
    calls: list[dict] = []
    from app.radius.integration.sqlite_adapter import SqliteAdapter

    def _disconnect(self, username, *a, **kw):
        c = _db()
        calls.append({
            "username": username,
            "open_rows_under_name": c.execute(
                "SELECT COUNT(*) FROM radacct WHERE tenant_id=? AND username=? "
                "AND acctstoptime IS NULL", (TID, username)).fetchone()[0],
            "card_name_at_kick": [r[0] for r in c.execute(
                "SELECT username FROM cards WHERE tenant_id=?", (TID,))],
        })
        c.execute("UPDATE radacct SET acctstoptime=datetime('now') "
                  "WHERE tenant_id=? AND username=? AND acctstoptime IS NULL",
                  (TID, username))
        c.commit()
    monkeypatch.setattr(SqliteAdapter, "disconnect", _disconnect)
    return calls


def _db():
    from app.radius.db.connection import db
    return db()


def _svc():
    from app.radius.services.cards import get_cards_service
    return get_cards_service()


def _plan_id():
    cur = _db().execute(
        "INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,"
        " price, currency, speed_down_kbps, speed_up_kbps, quota_total_mb,"
        " created_at, updated_at) VALUES(1,?,0,1,5.0,"
        "'ILS',4096,4096,0,datetime('now'),datetime('now'))",
        ("4ميجا-" + uuid4().hex[:6],))
    _db().commit()
    return int(cur.lastrowid)


def _gen(count=2, **kw):
    """A real batch through the real generator (cards + subscriber mirrors +
    router-sync jobs) — 4-hour cards counted from first login."""
    batch, cards = _svc().generate_batch(
        actor="admin", plan_id=_plan_id(), count=count, package_name="أربع ساعات",
        time_value=4, time_unit="hours", **{"password_length": 6, **kw})
    return batch, sorted(cards, key=lambda c: c.id)


def _card(card_id):
    from app.radius.db.repos import cards_repo
    return cards_repo.get_card(TID, card_id)


def _auth(username, password="", **kw):
    """The exact decision FreeRADIUS rlm_rest asks for (internal/auth)."""
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password=password,
                                 tenant_id=TID, calling_station_id=MAC,
                                 nas_ip="10.0.0.1", **kw))


def _acct_start(username, sid, seconds=0, inp=0, out=0, stop=False):
    """What FreeRADIUS rlm_sql writes on Acct-Start/Interim (mods-enabled/sql)."""
    _db().execute(
        "INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username, "
        "nasipaddress, acctstarttime, acctupdatetime, acctstoptime, acctsessiontime, "
        "acctinputoctets, acctoutputoctets, callingstationid, framedipaddress) "
        "VALUES(1,?,?,?,'10.0.0.1',datetime('now', ?),datetime('now'),"
        + ("datetime('now')" if stop else "NULL") + ",?,?,?,?,'10.5.50.7')",
        (sid, f"10.0.0.1-{sid}-{username}", username, f"-{int(seconds)} seconds",
         int(seconds), int(inp), int(out), MAC))
    _db().commit()


def _acct_interim(sid, seconds, inp, out):
    _db().execute(
        "UPDATE radacct SET acctupdatetime=datetime('now'), acctsessiontime=?, "
        "acctinputoctets=?, acctoutputoctets=? WHERE acctsessionid=? "
        "AND nasipaddress='10.0.0.1' AND acctstoptime IS NULL",
        (int(seconds), int(inp), int(out), sid))
    _db().commit()


def _count(table, col, value):
    return _db().execute(f"SELECT COUNT(*) FROM {table} WHERE tenant_id=? AND {col}=?",
                         (TID, value)).fetchone()[0]


def _raw(card_id):
    """The time/usage/sale/ownership columns a rename must never touch."""
    r = _db().execute(
        "SELECT first_used_at, expire_at, extra_seconds, usage_reset_at, used, "
        "used_by_mac, batch_id, plan_id, wallet_value, purchase_id, revoked, "
        "frozen_remaining_seconds FROM cards WHERE id=?", (card_id,)).fetchone()
    return tuple(r)


def _store_purchase(card_id, username, password):
    """A store sale («بطاقاتي») holding the card credentials by value. FK to
    card_users/packages is irrelevant to what we test → inserted orphan."""
    c = _db()
    c.commit()
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("INSERT INTO card_user_purchases(tenant_id, card_user_id, package_id, card_id,"
              " amount_minor, currency, status, delivery_status, created_at, cred_username,"
              " cred_password) VALUES(1, 1, 1, ?, 100, 'ILS', 'completed', 'sent',"
              " datetime('now'), ?, ?)", (card_id, username, password))
    c.commit()
    c.execute("PRAGMA foreign_keys=ON")


def _check(name):
    from app.radius.services.card_checker import check_card
    return check_card(TID, name)


def _started_card(coa=None):
    """A sold, started card: first login through the engine (stamps the
    window), then a live session with usage under its number."""
    _batch, cards = _gen()
    c = cards[0]
    d = _auth(c.username, c.password)
    assert d.ok, d.reason
    _acct_start(c.username, "S-OLD", seconds=600, inp=1_000_000, out=5_000_000)
    return _card(c.id)


# ════════════════════════════════════════════════════════════════════════
# (1) The owner's main demand — effective in RADIUS, not just the UI/DB
# ════════════════════════════════════════════════════════════════════════
def test_rename_and_password_take_effect_in_radius_auth(app_ctx, coa):
    c = _started_card()
    old, old_pw = c.username, c.password
    res = _svc().update_card_identity(actor="admin", card_id=c.id,
                                      username="ZX-777001", password="Np4455")
    assert res["renamed"] and res["password_changed"]
    assert res["username"] == "zx-777001"          # stored lowercase like generation

    # NEW number + NEW password → Access-Accept (the rlm_rest decision)
    assert _auth("zx-777001", "Np4455").ok
    # what the customer types as printed (upper case) still works
    assert _auth("ZX-777001", "Np4455").ok
    # OLD number is gone, with either password
    d = _auth(old, old_pw)
    assert not d.ok and d.reason == "user_not_found"
    assert not _auth(old, "Np4455").ok
    # NEW number with the OLD password → rejected
    d = _auth("zx-777001", old_pw)
    assert not d.ok and d.reason == "password_wrong"


def test_internal_auth_endpoint_accepts_new_rejects_old(app_ctx, coa):
    """Same proof over HTTP — the exact endpoint FreeRADIUS rlm_rest posts to."""
    c = _started_card()
    old, old_pw = c.username, c.password
    _svc().update_card_identity(actor="admin", card_id=c.id,
                                username="88001122", password="pw9911")
    cl = app_ctx.test_client()

    def post(u, p):
        r = cl.post("/api/v1/internal/auth", json={
            "User-Name": u, "User-Password": p, "NAS-IP-Address": "10.0.0.1",
            "Calling-Station-Id": MAC})
        return (r.get_json() or {}).get("control:Auth-Type")

    assert post("88001122", "pw9911") == "Accept"
    assert post(old, old_pw) == "Reject"
    assert post("88001122", old_pw) == "Reject"


def test_live_session_kicked_under_old_name_before_cascade(app_ctx, coa):
    c = _started_card()
    old = c.username
    res = _svc().update_card_identity(actor="admin", card_id=c.id, username="55667788")
    assert res["kicked"] and res["had_live_session"]
    assert len(coa) == 1
    assert coa[0]["username"] == old                   # the name the NAS knows
    assert coa[0]["open_rows_under_name"] == 1         # radacct not renamed yet
    assert old in coa[0]["card_name_at_kick"]          # cascade not run yet


def test_accounting_under_new_name_lands_on_the_card_window_untouched(app_ctx, coa):
    c = _started_card()
    before = _check(c.username)
    first_used, expire_at = c.first_used_at, c.expire_at
    raw_before = _raw(c.id)
    assert first_used is not None and expire_at is not None
    rem_before = int(before["remaining_seconds"] or 0)
    used_before = int(before["used_session_seconds"] or 0)
    assert used_before >= 600

    _svc().update_card_identity(actor="admin", card_id=c.id, username="44332211")
    after = _card(c.id)
    # window / time / batch / price / ownership preserved
    assert after.first_used_at == first_used
    assert after.expire_at == expire_at
    assert after.batch_id == c.batch_id and after.plan_id == c.plan_id
    assert _raw(c.id) == raw_before
    # history moved with the name → nothing left under the old one
    assert _count("radacct", "username", c.username) == 0
    assert _count("radacct", "username", "44332211") == 1

    chk = _check("44332211")
    assert chk["exists"] and chk["id"] == c.id
    assert abs(int(chk["remaining_seconds"] or 0) - rem_before) <= 5
    assert int(chk["used_session_seconds"] or 0) == used_before

    # re-login under the new name does NOT restart the window
    assert _auth("44332211", c.password).ok
    assert _card(c.id).expire_at == expire_at
    # FreeRADIUS Acct-Start + Interim under the new name → the card keeps counting
    _acct_start("44332211", "S-NEW", seconds=0)
    _acct_interim("S-NEW", 300, 2_000_000, 3_000_000)
    chk2 = _check("44332211")
    assert int(chk2["used_session_seconds"] or 0) == used_before + 300
    assert abs(int(chk2["remaining_seconds"] or 0) - rem_before) <= 5


def test_cascade_moves_every_reference_and_requeues_routers(app_ctx, coa, monkeypatch):
    from app.radius.integration import router_sync
    monkeypatch.setattr(router_sync, "tenant_has_sync_targets", lambda t: True)
    c = _started_card()
    old = c.username
    # extra references stored by value (derived from the schema)
    _db().execute("INSERT INTO device_limit_claims(tenant_id, username, device_key, claimed_at)"
                  " VALUES(1, ?, 'mac:x', datetime('now'))", (old,))
    _db().execute("INSERT INTO panel_notifications(tenant_id, type, severity, title, body,"
                  " subscriber_username, created_at) VALUES(1,'t','info','x','y',?,datetime('now'))",
                  (old,))
    _store_purchase(c.id, old, c.password)
    _db().commit()

    claims_before = _count("device_limit_claims", "username", old)
    assert claims_before >= 1
    _svc().update_card_identity(actor="admin", card_id=c.id, username="99112233",
                                password="NewPw1")
    for table, col in (("cards", "username"), ("subscribers", "username"),
                       ("radacct", "username"), ("radpostauth", "username"),
                       ("device_limit_claims", "username"),
                       ("panel_notifications", "subscriber_username"),
                       ("card_user_purchases", "cred_username")):
        assert _count(table, col, old) == 0, table
    assert _count("subscribers", "username", "99112233") == 1
    assert _count("device_limit_claims", "username", "99112233") == claims_before
    row = _db().execute("SELECT cred_password FROM card_user_purchases WHERE card_id=?",
                        (c.id,)).fetchone()
    assert row[0] == "NewPw1"                       # «بطاقاتي» shows the new password
    mirror = _db().execute("SELECT password, user_type FROM subscribers WHERE username=?",
                           ("99112233",)).fetchone()
    assert mirror[0] == "NewPw1" and mirror[1] == "card"
    # routers: drop the old account, push the new one (after the cascade)
    jobs = _db().execute("SELECT kind, entity_key FROM sync_queue WHERE tenant_id=1 "
                         "ORDER BY id").fetchall()
    jobs = [(j[0], j[1]) for j in jobs]
    assert ("subscriber_delete", old) in jobs
    assert ("subscriber_upsert", "99112233") in jobs


# ════════════════════════════════════════════════════════════════════════
# (2) Number only · password only · both · empty = unchanged · no generation
# ════════════════════════════════════════════════════════════════════════
@pytest.fixture
def no_generation(monkeypatch):
    from app.radius.db.repos import cards_repo

    def _boom(*a, **kw):
        raise AssertionError("the edit dialog must never generate a value")

    def arm():
        monkeypatch.setattr(cards_repo, "_random_str", _boom)
    return arm


def test_number_only_keeps_password(app_ctx, coa, no_generation):
    _b, cards = _gen()
    no_generation()
    c = cards[0]
    res = _svc().update_card_identity(actor="admin", card_id=c.id, username="30303030",
                                      password=None)
    assert res["renamed"] and not res["password_changed"]
    assert _card(c.id).password == c.password
    assert _auth("30303030", c.password).ok
    assert not _auth(c.username, c.password).ok


def test_password_only_keeps_number(app_ctx, coa, no_generation):
    _b, cards = _gen()
    no_generation()
    c = cards[0]
    _acct_start(c.username, "S1")                   # online → must be kicked
    res = _svc().update_card_identity(actor="admin", card_id=c.id, username=None,
                                      password="Only9999")
    assert not res["renamed"] and res["password_changed"]
    assert _card(c.id).username == c.username
    assert _auth(c.username, "Only9999").ok
    d = _auth(c.username, c.password)
    assert not d.ok and d.reason == "password_wrong"
    assert [x["username"] for x in coa] == [c.username]


def test_same_number_typed_back_counts_as_unchanged(app_ctx, coa, no_generation):
    _b, cards = _gen()
    no_generation()
    c = cards[0]
    res = _svc().update_card_identity(actor="admin", card_id=c.id,
                                      username=c.username.upper(), password="NewOne1")
    assert not res["renamed"] and res["password_changed"]
    assert _card(c.id).username == c.username


def test_empty_password_means_unchanged_and_nothing_generated(app_ctx, coa, no_generation):
    _b, cards = _gen()
    no_generation()
    c = cards[0]
    for empty in ("", "   ", None):
        res = _svc().update_card_identity(actor="admin", card_id=c.id,
                                          username=None, password=empty)
        assert res["changed"] is False
    assert _card(c.id).password == c.password
    assert coa == []                                  # nothing changed → nobody kicked


def test_both_empty_is_a_noop(app_ctx, coa, no_generation):
    _b, cards = _gen()
    no_generation()
    c = cards[0]
    res = _svc().update_card_identity(actor="admin", card_id=c.id,
                                      username=c.username, password=c.password)
    assert res["changed"] is False


def test_arabic_digits_are_latinised(app_ctx, coa):
    _b, cards = _gen()
    c = cards[0]
    res = _svc().update_card_identity(actor="admin", card_id=c.id, username="٥٥٥١٢٣")
    assert res["username"] == "555123"
    assert _auth("555123", c.password).ok


def test_audit_rows_without_the_password(app_ctx, coa):
    _b, cards = _gen()
    c = cards[0]
    _svc().update_card_identity(actor="admin", card_id=c.id, username="70707070",
                                password="SecretPw77")
    rows = _db().execute("SELECT action, payload_json, before_json, after_json "
                         "FROM audit_log WHERE target_type='card' AND target_id=?",
                         (str(c.id),)).fetchall()
    acts = [r[0] for r in rows]
    assert "card.rename" in acts and "card.change_password" in acts
    blob = " ".join(" ".join(str(x or "") for x in r) for r in rows)
    assert "SecretPw77" not in blob
    ren = [r for r in rows if r[0] == "card.rename"][0]
    assert c.username in (ren[2] or "") and "70707070" in (ren[3] or "")


# ════════════════════════════════════════════════════════════════════════
# (3) Validation / uniqueness — nothing changes on refusal
# ════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("bad", ["ab", "has space", "بطاقة", "x" * 65, "rtr-abc", "a/b"])
def test_invalid_numbers_refused(app_ctx, coa, bad):
    from app.radius.core.errors import RadiusValidationError
    _b, cards = _gen()
    c = cards[0]
    with pytest.raises(RadiusValidationError):
        _svc().update_card_identity(actor="admin", card_id=c.id, username=bad)
    assert _card(c.id).username == c.username


@pytest.mark.parametrize("bad", ["has space", "x" * 65])
def test_invalid_passwords_refused(app_ctx, coa, bad):
    from app.radius.core.errors import RadiusValidationError
    _b, cards = _gen()
    c = cards[0]
    with pytest.raises(RadiusValidationError):
        _svc().update_card_identity(actor="admin", card_id=c.id, username="61616161",
                                    password=bad)
    assert _card(c.id).username == c.username and _card(c.id).password == c.password


def test_number_taken_by_another_card_is_refused(app_ctx, coa):
    from app.radius.core.errors import RadiusConflict
    _b, cards = _gen()
    a, b = cards
    with pytest.raises(RadiusConflict) as ei:
        _svc().update_card_identity(actor="admin", card_id=a.id,
                                    username=b.username.upper(), password="zz1234")
    assert "الاسم مستخدم" in ei.value.message
    assert _card(a.id).username == a.username and _card(a.id).password == a.password
    assert _auth(a.username, a.password).ok


def test_number_taken_by_subscriber_or_archived_subscriber_is_refused(app_ctx, coa):
    from app.radius.core.errors import RadiusConflict
    _db().execute("INSERT INTO subscribers(tenant_id, username, password, status, user_type,"
                  " created_at) VALUES(1,'Ali.Sub','p','enabled','user',datetime('now'))")
    _db().execute("INSERT INTO subscribers(tenant_id, username, password, status, user_type,"
                  " deleted_at, created_at) VALUES(1,'gone1','p','enabled','user',"
                  " datetime('now'), datetime('now'))")
    _db().commit()
    _b, cards = _gen()
    c = cards[0]
    for name in ("ali.sub", "GONE1"):
        with pytest.raises(RadiusConflict):
            _svc().update_card_identity(actor="admin", card_id=c.id, username=name)
    assert _card(c.id).username == c.username


def test_deleted_card_is_refused(app_ctx, coa):
    from app.radius.core.errors import RadiusValidationError
    _b, cards = _gen()
    c = cards[0]
    _db().execute("UPDATE cards SET deleted_at=datetime('now') WHERE id=?", (c.id,))
    _db().commit()
    with pytest.raises(RadiusValidationError):
        _svc().update_card_identity(actor="admin", card_id=c.id, username="12121212")


# ════════════════════════════════════════════════════════════════════════
# (4) «رقم فقط» (login-without-password) batches
# ════════════════════════════════════════════════════════════════════════
def test_passwordless_batch_refuses_password_but_allows_number(app_ctx, coa):
    from app.radius.core.errors import RadiusValidationError
    _b, cards = _gen(login_without_password=True, password_length=0)
    c = cards[0]
    with pytest.raises(RadiusValidationError) as ei:
        _svc().update_card_identity(actor="admin", card_id=c.id, password="abc123")
    assert "رقم فقط" in ei.value.message
    with pytest.raises(RadiusValidationError):
        _svc().change_card_password(actor="admin", card_id=c.id, new_password="abc123")
    # the number IS the secret → renaming it is the remedy for a leak
    _svc().update_card_identity(actor="admin", card_id=c.id, username="80808080")
    assert _auth("80808080", "").ok
    assert not _auth(c.username, "").ok


# ════════════════════════════════════════════════════════════════════════
# (5) All-or-nothing
# ════════════════════════════════════════════════════════════════════════
def test_failure_mid_cascade_rolls_everything_back(app_ctx, coa, monkeypatch):
    from app.radius.db.repos import subscribers_repo
    _b, cards = _gen()
    c = cards[0]
    real = subscribers_repo.rename_subscriber_username

    def _half(*a, **kw):
        real(*a, **kw)
        raise RuntimeError("disk full mid-cascade")
    monkeypatch.setattr(subscribers_repo, "rename_subscriber_username", _half)
    with pytest.raises(RuntimeError):
        _svc().update_card_identity(actor="admin", card_id=c.id, username="23232323",
                                    password="Pw55555")
    assert _card(c.id).username == c.username and _card(c.id).password == c.password
    assert _count("subscribers", "username", c.username) == 1
    assert _count("subscribers", "username", "23232323") == 0
    assert _auth(c.username, c.password).ok


def test_password_change_rolls_back_when_radius_side_fails(app_ctx, coa, monkeypatch):
    """Old code wrote `cards` first and then raised on the RADIUS side — the
    printed password and the authenticating one diverged."""
    from app.radius.integration.sqlite_adapter import SqliteAdapter
    _b, cards = _gen()
    c = cards[0]

    def _fail(self, *a, **kw):
        raise RuntimeError("router queue down")
    monkeypatch.setattr(SqliteAdapter, "reset_password", _fail)
    with pytest.raises(RuntimeError):
        _svc().update_card_identity(actor="admin", card_id=c.id, password="Fresh123")
    assert _card(c.id).password == c.password
    assert _auth(c.username, c.password).ok


def test_change_password_updates_store_credentials(app_ctx, coa):
    """«بطاقاتي» reads card_user_purchases.cred_password FIRST — it kept
    showing the leaked password after a change."""
    _b, cards = _gen()
    c = cards[0]
    _store_purchase(c.id, c.username, c.password)
    _db().commit()
    _svc().change_card_password(actor="admin", card_id=c.id, new_password="Rotated9")
    row = _db().execute("SELECT cred_password FROM card_user_purchases WHERE card_id=?",
                        (c.id,)).fetchone()
    assert row[0] == "Rotated9"


# ════════════════════════════════════════════════════════════════════════
# (6) The card still shows correctly everywhere
# ════════════════════════════════════════════════════════════════════════
def test_card_visible_in_batch_counts_checker_and_online(app_ctx, coa):
    batch, cards = _gen()
    c = _card(cards[0].id)
    assert _auth(c.username, c.password).ok
    _svc().update_card_identity(actor="admin", card_id=c.id, username="64646464",
                                kick=False)
    # still online (no kick) → the online page joins the renamed open row
    _acct_start("64646464", "S-ON")
    from app.radius.routes.cards import _batch_cards_index
    idx = _batch_cards_index(TID, batch.id)
    names = {r.get("username") for r in idx}
    assert "64646464" in names and c.username not in names
    assert len(idx) == 2
    chk = _check("64646464")
    assert chk["exists"] and chk["batch"] and chk["active_session"] is True
    from app.radius.services import live_sessions
    assert "64646464" in live_sessions.live_usernames(TID)


# ════════════════════════════════════════════════════════════════════════
# (7) API — PATCH /api/v1/cards/<id>
# ════════════════════════════════════════════════════════════════════════
def _bearer(admin_id):
    from app.radius.db.repos import api_tokens_repo
    _rec, plain = api_tokens_repo.create_token(
        tenant_id=1, name=f"login:t{uuid4().hex[:6]}", scopes=["admin:full"],
        created_by=int(admin_id))
    return {"Authorization": f"Bearer {plain}"}


def _owner_id():
    from app.radius.db.repos import admins_repo
    pid = admins_repo.primary_admin_id()
    if pid is None:
        admins_repo.create_admin(username="owner_x", password="owner-pass",
                                 full_name="Owner", is_super_admin=True)
        pid = admins_repo.primary_admin_id()
    return pid


def _manager(perms):
    from app.radius.db.repos import admins_repo
    _owner_id()
    role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}", display_name="r",
                                   permissions=tuple(perms))
    a = admins_repo.create_admin(username=f"m_{uuid4().hex[:8]}", password="pw-123456",
                                 full_name="M", role_id=role.id)
    return a


def test_api_patch_number_and_password(app_ctx, coa):
    _b, cards = _gen()
    c = cards[0]
    h = _bearer(_owner_id())
    cl = app_ctx.test_client()
    r = cl.patch(f"/api/v1/cards/{c.id}", json={"username": "٧٧٧٨٨٨٩٩"}, headers=h)
    assert r.status_code == 200, r.get_data(as_text=True)
    data = r.get_json()["data"]
    assert data["renamed"] and not data["password_changed"]
    assert data["item"]["username"] == "77788899"
    assert _auth("77788899", c.password).ok

    r = cl.patch(f"/api/v1/cards/{c.id}", json={"password": "ApiPw42"}, headers=h)
    assert r.status_code == 200
    assert r.get_json()["data"]["password_changed"] is True
    assert _auth("77788899", "ApiPw42").ok
    assert not _auth("77788899", c.password).ok

    r = cl.patch(f"/api/v1/cards/{c.id}", json={"username": "", "password": ""}, headers=h)
    assert r.status_code == 422
    r = cl.patch(f"/api/v1/cards/{c.id}", json={"username": cards[1].username}, headers=h)
    assert r.status_code == 409
    assert "الاسم مستخدم" in r.get_json()["error"]["message"]
    r = cl.patch(f"/api/v1/cards/{c.id}", json={"username": "a b"}, headers=h)
    assert r.status_code == 422
    r = cl.patch("/api/v1/cards/999999", json={"username": "12345678"}, headers=h)
    assert r.status_code == 404


def test_api_patch_refused_without_permission_or_out_of_scope(app_ctx, coa):
    _b, cards = _gen()
    c = cards[0]
    cl = app_ctx.test_client()
    # no cards.verify → 403, nothing changed
    m = _manager(["cards.view"])
    r = cl.patch(f"/api/v1/cards/{c.id}", json={"username": "12312312"},
                 headers=_bearer(m.id))
    assert r.status_code == 403
    # cards.verify but the batch belongs to the owner (not in his scope) → 403
    m2 = _manager(["cards.view", "cards.verify"])
    r = cl.patch(f"/api/v1/cards/{c.id}", json={"username": "12312312"},
                 headers=_bearer(m2.id))
    assert r.status_code == 403
    assert _card(c.id).username == c.username


def test_api_patch_allowed_for_manager_in_scope(app_ctx, coa):
    m = _manager(["cards.view", "cards.verify"])
    _b, cards = _gen(manager_id=m.id)
    c = cards[0]
    r = app_ctx.test_client().patch(f"/api/v1/cards/{c.id}",
                                    json={"username": "45645645", "password": "MgrPw1"},
                                    headers=_bearer(m.id))
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _auth("45645645", "MgrPw1").ok


def test_openapi_documents_the_patch(app_ctx):
    from app.api.openapi import _build_spec
    op = _build_spec()["paths"]["/api/v1/cards/{card_id}"]["patch"]
    props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert set(props) == {"username", "password"}
    assert "409" in op["responses"] and "422" in op["responses"]


# ════════════════════════════════════════════════════════════════════════
# (8) Web — card checker + batch cards page
# ════════════════════════════════════════════════════════════════════════
def _web_login(client, admin_id, *, owner, perms=()):
    with client.session_transaction() as s:
        s["admin_id"] = int(admin_id)
        s["admin_user"] = "test"
        s["tenant_id"] = 1
        s["is_super_admin"] = bool(owner)
        s["permissions"] = list(perms)
        s["_csrf_token"] = "tok"


def test_web_checker_edit_identity(app_ctx, coa):
    _b, cards = _gen()
    c = cards[0]
    cl = app_ctx.test_client()
    _web_login(cl, _owner_id(), owner=True)
    r = cl.post("/admin/radius/cards/checker", data={
        "op": "edit_identity", "card_id": c.id, "username": c.username,
        "query": c.username, "new_username": "31313131", "new_password": "",
        "_csrf_token": "tok"})
    assert r.status_code in (302, 303), r.status_code
    assert "31313131" in r.headers["Location"]
    assert _card(c.id).username == "31313131" and _card(c.id).password == c.password
    assert _auth("31313131", c.password).ok
    page = cl.get("/admin/radius/cards/checker?query=31313131").get_data(as_text=True)
    assert 'data-cc-op="edit-identity"' in page
    assert 'name="new_username"' in page and "identityForm" in page


def test_web_checker_edit_refused_out_of_scope(app_ctx, coa):
    _b, cards = _gen()
    c = cards[0]
    perms = ["cards.view", "cards.verify"]
    m = _manager(perms)
    cl = app_ctx.test_client()
    _web_login(cl, m.id, owner=False, perms=perms)
    r = cl.post("/admin/radius/cards/checker", data={
        "op": "edit_identity", "card_id": c.id, "username": c.username,
        "new_username": "32323232", "_csrf_token": "tok"})
    assert r.status_code == 403
    assert _card(c.id).username == c.username
    # the same manager on HIS OWN batch's card → allowed (the 403 above is the
    # batch scope, not a missing key)
    _b2, own = _gen(manager_id=m.id)
    r = cl.post("/admin/radius/cards/checker", data={
        "op": "edit_identity", "card_id": own[0].id, "username": own[0].username,
        "new_username": "32323232", "_csrf_token": "tok"})
    assert r.status_code in (302, 303), r.status_code
    assert _card(own[0].id).username == "32323232"


def test_web_checker_edit_needs_cards_verify(app_ctx, coa):
    perms = ["cards.view"]
    m = _manager(perms)
    _b, cards = _gen(manager_id=m.id)
    c = cards[0]
    cl = app_ctx.test_client()
    _web_login(cl, m.id, owner=False, perms=perms)
    r = cl.post("/admin/radius/cards/checker", data={
        "op": "edit_identity", "card_id": c.id, "username": c.username,
        "new_username": "33333333", "_csrf_token": "tok"})
    assert r.status_code == 403
    assert _card(c.id).username == c.username


def test_web_batch_row_edit_identity(app_ctx, coa):
    batch, cards = _gen()
    c = cards[0]
    cl = app_ctx.test_client()
    _web_login(cl, _owner_id(), owner=True)
    page = cl.get(f"/admin/radius/cards/batches/{batch.id}/cards").get_data(as_text=True)
    assert "data-bc-edit-identity" in page and 'id="bc-identity-dlg"' in page
    r = cl.post(f"/admin/radius/cards/batches/{batch.id}/cards/actions", data={
        "bulk_action": "edit_identity", "card_ids": str(c.id),
        "new_username": "", "new_password": "RowPw88", "_csrf_token": "tok"})
    assert r.status_code in (302, 303)
    assert _card(c.id).password == "RowPw88" and _card(c.id).username == c.username
    assert _auth(c.username, "RowPw88").ok
