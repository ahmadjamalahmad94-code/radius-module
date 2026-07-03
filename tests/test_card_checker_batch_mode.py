"""FIX 1 regression: the Card Checker resolves a card's accounting mode +
remaining time from its BATCH, not from "has it connected?".

Live symptom (owner's panel): batch «امواج البحر» has count-from-first-connect
ON + a 3h budget, yet the checker showed «طريقة الاحتساب: بالثانية / الوقت يجري
الآن» and «الوقت المتبقّي = 0». Root cause: the checker derived the label from
started_at and remaining purely from cards.expire_at (a stale generation-time
date), ignoring the batch entirely.

These tests prove:
  1. check_card() returns accounting_mode='from_first_connect' + remaining ≈ 2h
     for a from-first-connect batch (3h budget, connected ~1h ago).
  2. The rendered checker page shows the «تبدأ من أول اتصال» label (reads the
     batch, not the connection state).
  3. A by-seconds/legacy batch still resolves to 'by_seconds'.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_fix1_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed_from_first_connect_card(
    conn, *, username, count_from_first_connect=1, count_by_seconds=0,
    time_value=3, time_unit="hours", first_connect_dt=None, expire_at=None,
):
    """Seed plan + batch (with accounting flags) + card + one open session."""
    now = _iso(datetime.utcnow())
    conn.execute(
        "INSERT INTO access_plans (tenant_id, name, enabled, created_at) "
        "VALUES (1, 'p', 1, ?)", (now,))
    plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO card_batches
            (tenant_id, batch_code, package_name, plan_id, count, generated,
             used, count_from_first_connect, count_by_seconds, time_value,
             time_unit, created_by, status, created_at, metadata)
        VALUES (1, 'B-WAVES', 'امواج البحر', ?, 1, 1, 0, ?, ?, ?, ?, 'seed',
                'active', ?, '{}')
        """,
        (plan_id, count_from_first_connect, count_by_seconds, time_value,
         time_unit, now),
    )
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id, used,
             first_used_at, expire_at, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 1, ?, ?, 0, ?)
        """,
        (batch_id, username, plan_id,
         _iso(first_connect_dt) if first_connect_dt else None,
         _iso(expire_at) if expire_at else None, now),
    )
    if first_connect_dt is not None:
        conn.execute(
            """
            INSERT INTO radacct
                (tenant_id, acctsessionid, acctuniqueid, username,
                 nasipaddress, acctstarttime, acctsessiontime,
                 callingstationid)
            VALUES (1, 's1', 'u1', ?, '10.0.0.1', ?, ?, 'AA:BB:CC:DD:EE:FF')
            """,
            (username, _iso(first_connect_dt),
             int((datetime.utcnow() - first_connect_dt).total_seconds())),
        )
    return username


def test_check_card_resolves_from_first_connect_mode_and_remaining(app):
    # «امواج البحر»: 3h budget, first connect ~1h ago → mode=from_first_connect,
    # remaining ≈ 2h. Proves the checker reads the BATCH, not started_at.
    first = datetime.utcnow() - timedelta(hours=1)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_from_first_connect_card(c, username="5698046", first_connect_dt=first)

        from app.radius.services.card_checker import check_card
        result = check_card(1, "5698046")

    assert result["exists"] is True
    assert result["accounting_mode"] == "from_first_connect"
    assert result["accounting_budget_seconds"] == 3 * 3600
    # remaining = first + 3h − now ≈ 2h (allow a couple minutes of test drift).
    rem = result["remaining_seconds"]
    assert rem is not None
    assert 2 * 3600 - 180 <= rem <= 2 * 3600, rem


def test_check_card_ignores_stale_expire_at_for_from_first_connect(app):
    # The migrated card carries a generation-time expire_at in the PAST — it
    # must NOT zero the countdown (the exact live bug on card 5698046).
    first = datetime.utcnow() - timedelta(hours=1)
    stale = datetime.utcnow() - timedelta(days=90)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_from_first_connect_card(
                c, username="5698046", first_connect_dt=first, expire_at=stale)

        from app.radius.services.card_checker import check_card
        result = check_card(1, "5698046")

    assert result["accounting_mode"] == "from_first_connect"
    rem = result["remaining_seconds"]
    assert rem is not None and rem > 0, "stale expire_at must not zero the countdown"
    assert 2 * 3600 - 180 <= rem <= 2 * 3600, rem


def test_by_seconds_batch_resolves_by_seconds_mode(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_from_first_connect_card(
                c, username="bysec-1", count_from_first_connect=0,
                count_by_seconds=1, first_connect_dt=None)

        from app.radius.services.card_checker import check_card
        result = check_card(1, "bysec-1")

    assert result["accounting_mode"] == "by_seconds"


def test_checker_page_renders_from_first_connect_label(app):
    # End-to-end: the rendered checker HTML shows «تبدأ من أول اتصال» — proving
    # the template reads card.accounting_mode (the batch), not the old
    # started_at guess that produced «بالثانية».
    first = datetime.utcnow() - timedelta(hours=1)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_from_first_connect_card(c, username="5698046", first_connect_dt=first)

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
    resp = client.get("/admin/radius/cards/checker?query=5698046")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "تبدأ من أول اتصال" in html
    # And it must NOT fall back to the by-seconds label for this card.
    assert "الوقت يجري الآن" not in html
