# -*- coding: utf-8 -*-
"""Stress-campaign fixes (2026-09-28) — card generation / import / actions.

Each test pins one finding of the a06 stress report:

* C1  — a generated card must never take over a subscriber (or radcheck) name.
* H1  — batch + cards + auth rows are one transaction (no ghost/partial batch).
* H2  — batch code computed inside the write lock (parallel generates all 201).
* H3  — parallel disable/enable on different cards never 500.
* H4  — hard max per batch + idempotency key (a retry returns the same batch).
* H5  — too-small digit space → 422 with the max possible count, no 19-char names.
* M1  — GET /cards/<id> is a direct lookup.
* M2/M3 — unknown plan / bad MAC / no session → 422/422/409, never 500.
* M4  — API «رقم فقط» (password_length 0 / login_without_password).
* M5  — batch PATCH validates and names locked fields.
* M6  — prefix charset/length validated.
* M7  — CSV import sniffs «;».
"""
from __future__ import annotations

import threading
import uuid

import pytest


@pytest.fixture(scope="module")
def app():
    from app import create_app
    return create_app()


@pytest.fixture
def client(app):
    return app.test_client()


def _token(client) -> dict:
    res = client.post("/api/admin/login", json={"username": "admin", "password": "admin"})
    return {"Authorization": f"Bearer {res.get_json()['data']['token']}"}


@pytest.fixture
def auth(client):
    return _token(client)


def _px() -> str:
    """A fresh lower-case ascii prefix so tests never collide."""
    return "t" + uuid.uuid4().hex[:5]


def _db():
    from app.radius.db.connection import db
    return db()


def _gen(client, auth, **body):
    body.setdefault("plan_id", 1)
    return client.post("/api/v1/cards/generate", json=body, headers=auth)


# ── C1: namespace-wide uniqueness ─────────────────────────────────────

def test_generation_never_takes_over_a_subscriber(client, auth, app):
    """9 of the 10 names «<px>0..9» are subscribers → the one card generated
    is the free 10th, and every subscriber keeps its type/password."""
    px = _px()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        for i in range(9):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, username=f"{px}{i}", password=f"subpw{i}", tenant_id=1,
                plan_id=1))
    res = _gen(client, auth, count=1, username_prefix=px, username_length=len(px) + 1)
    assert res.status_code == 201, res.get_json()
    card = res.get_json()["data"]["cards"][0]
    assert card["username"] == f"{px}9"
    for i in range(9):
        row = _db().execute(
            "SELECT user_type, password, card_batch_id FROM subscribers "
            "WHERE tenant_id = 1 AND username = ?", (f"{px}{i}",)).fetchone()
        assert row["user_type"] == "subscriber"
        assert row["password"] == f"subpw{i}"
        assert row["card_batch_id"] is None


def test_generation_422_when_every_free_name_is_a_subscriber(client, auth, app):
    px = _px()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        for i in range(10):
            subscribers_repo.upsert_subscriber(Subscriber(
                id=None, username=f"{px}{i}", password="x", tenant_id=1, plan_id=1))
    before = _db().execute("SELECT COUNT(*) AS c FROM card_batches").fetchone()["c"]
    res = _gen(client, auth, count=1, username_prefix=px, username_length=len(px) + 1)
    assert res.status_code == 422
    assert "أقصى عدد ممكن" in res.get_json()["error"]["message"]
    after = _db().execute("SELECT COUNT(*) AS c FROM card_batches").fetchone()["c"]
    assert after == before, "a failed generation must leave no batch behind"


def test_radcheck_names_are_taken_too(app):
    from app.radius.db.repos import cards_repo
    px = _px()
    with app.app_context():
        conn = _db()
        conn.execute("INSERT INTO radcheck(tenant_id, username, attribute, op, value) "
                     "VALUES(1, ?, 'Cleartext-Password', ':=', 'x')", (f"{px}5",))
        taken = cards_repo.taken_login_names(conn, 1)
    assert f"{px}5" in taken
    names = cards_repo.pick_unique_usernames(
        count=9, prefix=px, username_length=len(px) + 1, taken=taken)
    assert f"{px}5" not in names and len(set(names)) == 9


def test_import_skips_a_subscriber_name_and_leaves_it_alone(client, auth, app):
    px = _px()
    with app.app_context():
        from app.radius.core.types import Subscriber
        from app.radius.db.repos import subscribers_repo
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username=f"{px}sub", password="keepme", tenant_id=1, plan_id=1))
    res = client.post("/api/v1/cards/batches/import", json={
        "plan_id": 1, "source_type": "imported", "sync_to_radius": True,
        "cards": [{"username": f"{px}sub", "password": "p"},
                  {"username": f"{px}new", "password": "p2"}],
    }, headers=auth)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["inserted_count"] == 1
    assert [s["username"] for s in data["skipped"]] == [f"{px}sub"]
    row = _db().execute("SELECT user_type, password FROM subscribers WHERE username = ?",
                        (f"{px}sub",)).fetchone()
    assert (row["user_type"], row["password"]) == ("subscriber", "keepme")
    new = _db().execute("SELECT user_type FROM subscribers WHERE username = ?",
                        (f"{px}new",)).fetchone()
    assert new["user_type"] == "card"


# ── H1: all-or-nothing ────────────────────────────────────────────────

def test_failure_while_writing_accounts_leaves_nothing(client, auth, monkeypatch):
    from app.radius.db.repos import subscribers_repo
    px = _px()

    def boom(conn, subs):
        raise RuntimeError("simulated failure after the cards were inserted")

    monkeypatch.setattr(subscribers_repo, "insert_new_accounts", boom)
    try:
        res = _gen(client, auth, count=20, username_prefix=px,
                   username_length=len(px) + 4, package_name=f"ghost-{px}")
        assert res.status_code == 500
    except RuntimeError:
        pass  # propagated by the test client — same contract
    assert _db().execute("SELECT COUNT(*) AS c FROM card_batches WHERE package_name = ?",
                         (f"ghost-{px}",)).fetchone()["c"] == 0
    assert _db().execute("SELECT COUNT(*) AS c FROM cards WHERE username LIKE ?",
                         (px + "%",)).fetchone()["c"] == 0


def test_every_generated_card_has_its_auth_account(client, auth):
    px = _px()
    res = _gen(client, auth, count=50, username_prefix=px, username_length=len(px) + 4)
    assert res.status_code == 201
    batch = res.get_json()["data"]["batch"]
    assert batch["generated"] == 50
    n = _db().execute("SELECT COUNT(*) AS c FROM subscribers WHERE card_batch_id = ? "
                      "AND user_type = 'card'", (batch["id"],)).fetchone()["c"]
    assert n == 50
    jobs = _db().execute("SELECT COUNT(*) AS c FROM sync_queue WHERE kind = 'subscriber_upsert' "
                         "AND entity_key LIKE ?", (px + "%",)).fetchone()["c"]
    assert jobs == 50


def _parallel(app, n, fn):
    results = [None] * n

    def run(i):
        with app.test_client() as c:
            results[i] = fn(c, i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
    return results


def test_parallel_same_prefix_generation_is_clean(app, client, auth):
    """H1 repro: 4×300 in parallel on one prefix → all 201, 1200 unique cards,
    every batch complete, every card with an account."""
    px = _px()
    res = _parallel(app, 4, lambda c, i: _gen(
        c, auth, count=300, username_prefix=px, username_length=len(px) + 5,
        package_name=f"par-{px}"))
    assert [r.status_code for r in res] == [201] * 4, [r.get_data(as_text=True)[:200] for r in res]
    rows = _db().execute("SELECT id, generated FROM card_batches WHERE package_name = ?",
                         (f"par-{px}",)).fetchall()
    assert len(rows) == 4 and all(r["generated"] == 300 for r in rows)
    names = [r["username"] for r in _db().execute(
        "SELECT username FROM cards WHERE username LIKE ?", (px + "%",))]
    assert len(names) == len(set(names)) == 1200
    accounts = _db().execute("SELECT COUNT(*) AS c FROM subscribers WHERE username LIKE ?",
                             (px + "%",)).fetchone()["c"]
    assert accounts == 1200


def test_parallel_batch_codes_never_clash(app, auth):
    res = _parallel(app, 6, lambda c, i: _gen(
        c, auth, count=1, username_prefix=_px(), username_length=12))
    assert [r.status_code for r in res] == [201] * 6
    codes = [r.get_json()["data"]["batch"]["batch_code"] for r in res]
    assert len(set(codes)) == 6


# ── H3: parallel disable/enable ───────────────────────────────────────

def test_parallel_disable_enable_on_different_cards_never_500(app, client, auth):
    px = _px()
    cards = _gen(client, auth, count=24, username_prefix=px,
                 username_length=len(px) + 4).get_json()["data"]["cards"]
    ids = [c["id"] for c in cards]
    dis = _parallel(app, len(ids), lambda c, i: c.post(
        f"/api/v1/cards/{ids[i]}/disable", json={"reason": "t"}, headers=auth))
    assert [r.status_code for r in dis] == [200] * len(ids)
    en = _parallel(app, len(ids), lambda c, i: c.post(
        f"/api/v1/cards/{ids[i]}/enable", headers=auth))
    assert [r.status_code for r in en] == [200] * len(ids)


def test_freeze_and_thaw_take_the_write_lock_first():
    import inspect

    from app.radius.db.repos import cards_repo
    for fn in (cards_repo.freeze_card_time, cards_repo.thaw_card_time):
        assert "write_transaction()" in inspect.getsource(fn)


# ── H4: max + idempotency ─────────────────────────────────────────────

def test_hard_max_per_batch_is_422(client, auth):
    res = _gen(client, auth, count=10_001)
    assert res.status_code == 422
    assert "10000" in res.get_json()["error"]["message"]


def test_same_idempotency_key_returns_the_same_batch(client, auth):
    px = _px()
    key = uuid.uuid4().hex
    hdr = {**auth, "Idempotency-Key": key}
    body = {"plan_id": 1, "count": 5, "username_prefix": px, "username_length": len(px) + 4}
    first = client.post("/api/v1/cards/generate", json=body, headers=hdr)
    second = client.post("/api/v1/cards/generate", json=body, headers=hdr)
    assert first.status_code == second.status_code == 201
    b1, b2 = first.get_json()["data"], second.get_json()["data"]
    assert b1["batch"]["id"] == b2["batch"]["id"]
    assert b2["idempotent_replay"] is True and b1["idempotent_replay"] is False
    assert sorted(c["username"] for c in b1["cards"]) == sorted(c["username"] for c in b2["cards"])
    assert _db().execute("SELECT COUNT(*) AS c FROM cards WHERE username LIKE ?",
                         (px + "%",)).fetchone()["c"] == 5


def test_big_batch_is_fast_enough(client, auth):
    """5000 cards used to take ~2 min (per-card transactions) → 504."""
    import time
    px = _px()
    t0 = time.monotonic()
    res = _gen(client, auth, count=5000, username_prefix=px, username_length=len(px) + 6)
    elapsed = time.monotonic() - t0
    assert res.status_code == 201
    assert res.get_json()["data"]["batch"]["generated"] == 5000
    assert elapsed < 30, f"5000 cards took {elapsed:.1f}s"


# ── H5: digit space ───────────────────────────────────────────────────

def test_digit_space_too_small_is_422_with_max_count(client, auth):
    px = _px()
    res = _gen(client, auth, count=150, username_prefix=px, username_length=len(px) + 2)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "100" in msg and "أقصى عدد ممكن" in msg


def test_exactly_full_digit_space_stays_digits_only(client, auth):
    import re
    px = _px()
    res = _gen(client, auth, count=100, username_prefix=px, username_length=len(px) + 2)
    assert res.status_code == 201
    names = [c["username"] for c in res.get_json()["data"]["cards"]]
    assert len(set(names)) == 100
    assert all(re.fullmatch(re.escape(px) + r"[0-9]{2}", n) for n in names)


# ── M1: GET /cards/<id> ───────────────────────────────────────────────

def test_get_card_is_a_direct_lookup(client, auth, monkeypatch):
    from app.radius.db.repos import cards_repo
    card = _gen(client, auth, count=1, username_prefix=_px(),
                username_length=12).get_json()["data"]["cards"][0]

    def no_scan(*a, **k):
        raise AssertionError("GET /cards/<id> must not scan list_cards")

    monkeypatch.setattr(cards_repo, "list_cards", no_scan)
    res = client.get(f"/api/v1/cards/{card['id']}", headers=auth)
    assert res.status_code == 200
    assert res.get_json()["data"]["username"] == card["username"]
    assert client.get("/api/v1/cards/999999999", headers=auth).status_code == 404


# ── M2/M3: status codes ───────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"plan_id": 999999, "count": 1},
    {"plan_id": "abc", "count": 1},
    {"plan_id": 1.5, "count": 1},
    {"plan_id": 1, "count": True},
    {"plan_id": 1, "count": 1, "username_length": "abc"},
    {"plan_id": 1, "count": 1, "price_per_card": "abc"},
    {"plan_id": 1, "count": 1, "time_value": -3},
    {"plan_id": 1, "count": 1, "time_unit": "fortnights", "time_value": 2},
    {"plan_id": 1, "count": 1, "device_count": 1000},
    {"plan_id": 1, "count": 1, "username_length": 500},
])
def test_bad_generate_input_is_422_json(client, auth, body):
    res = client.post("/api/v1/cards/generate", json=body, headers=auth)
    assert res.status_code == 422, res.get_data(as_text=True)[:300]
    assert res.get_json()["ok"] is False


def test_non_object_body_is_422(client, auth):
    res = client.post("/api/v1/cards/generate", json=[1], headers=auth)
    assert res.status_code == 422


def test_bad_mac_is_422_and_no_session_disconnect_is_409(client, auth):
    card = _gen(client, auth, count=1, username_prefix=_px(),
                username_length=12).get_json()["data"]["cards"][0]
    res = client.post(f"/api/v1/cards/{card['id']}/lock-mac", json={"mac": "zz"}, headers=auth)
    assert res.status_code == 422
    res = client.post(f"/api/v1/cards/{card['id']}/disconnect", headers=auth)
    assert res.status_code == 409, res.get_json()
    assert "جلسة" in res.get_json()["error"]["message"]


# ── M4: «رقم فقط» via API ─────────────────────────────────────────────

@pytest.mark.parametrize("extra", [{"password_length": 0},
                                   {"login_without_password": True}])
def test_api_can_make_number_only_cards(client, auth, extra):
    res = _gen(client, auth, count=3, username_prefix=_px(), username_length=12, **extra)
    assert res.status_code == 201, res.get_json()
    data = res.get_json()["data"]
    assert data["batch"]["login_without_password"] is True
    assert all(c["password"] == "" for c in data["cards"])


def test_password_charset_alone_is_honoured(client, auth):
    res = _gen(client, auth, count=5, username_prefix=_px(), username_length=12,
               password_charset="digits", password_length=8)
    assert res.status_code == 201
    assert all(c["password"].isdigit() for c in res.get_json()["data"]["cards"])


# ── M5: batch PATCH validation ────────────────────────────────────────

@pytest.fixture
def batch(client, auth):
    return _gen(client, auth, count=2, username_prefix=_px(),
                username_length=12).get_json()["data"]["batch"]


@pytest.mark.parametrize("patch", [
    {"status": "garbage"},
    {"status": "deleted"},
    {"price_per_card": -50},
    {"price_per_card": "abc"},
    {"price_per_card": 1e308 * 10},
    {"time_value": -3},
    {"time_unit": "fortnights"},
    {"distributor_id": 999999},
    {"on_quota_exhaust": "explode"},
    {"metadata": "{bad json"},
    {"expire_at": "not-a-date"},
    {"device_count": 51},
])
def test_batch_patch_rejects_garbage(client, auth, batch, patch):
    res = client.patch(f"/api/v1/cards/batches/{batch['id']}", json=patch, headers=auth)
    assert res.status_code == 422, (patch, res.get_json())


def test_batch_patch_names_changed_locked_fields(client, auth, batch):
    res = client.patch(f"/api/v1/cards/batches/{batch['id']}",
                       json={"count": 999, "username_length": 4}, headers=auth)
    assert res.status_code == 422
    msg = res.get_json()["error"]["message"]
    assert "count" in msg and "username_length" in msg


def test_batch_patch_accepts_unchanged_locked_echo(client, auth, batch):
    res = client.patch(f"/api/v1/cards/batches/{batch['id']}", json={
        "count": batch["count"], "username_length": batch["username_length"],
        "username_prefix": batch["username_prefix"], "price_per_card": 2.5,
        "status": "exhausted"}, headers=auth)
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["batch"]["price_per_card"] == 2.5


def test_batch_patch_non_object_body_is_422(client, auth, batch):
    res = client.patch(f"/api/v1/cards/batches/{batch['id']}", data="not json",
                       headers={**auth, "Content-Type": "application/json"})
    assert res.status_code == 422


# ── M6: prefix validation ─────────────────────────────────────────────

@pytest.mark.parametrize("prefix", ["st\x00n", "a😀", "<b>", "a'b", "x" * 300, "a/b"])
def test_bad_prefix_is_422(client, auth, prefix):
    res = _gen(client, auth, count=1, username_prefix=prefix, username_length=20)
    assert res.status_code == 422, res.get_json()


def test_prefix_is_normalised_to_lower_case(client, auth):
    px = _px()
    res = _gen(client, auth, count=1, username_prefix=px.upper(), username_length=12)
    assert res.status_code == 201
    data = res.get_json()["data"]
    assert data["batch"]["username_prefix"] == px
    assert data["cards"][0]["username"].startswith(px)


# ── M7: CSV import delimiter ──────────────────────────────────────────

def test_csv_import_sniffs_semicolons(client, auth):
    px = _px()
    res = client.post("/api/v1/cards/batches/import", json={
        "plan_id": 1, "source_type": "external",
        "csv_text": f"{px}a;pw1\n{px}b;pw2\n",
    }, headers=auth)
    assert res.status_code == 201, res.get_json()
    names = sorted(c["username"] for c in res.get_json()["data"]["cards"])
    assert names == [f"{px}a", f"{px}b"]
