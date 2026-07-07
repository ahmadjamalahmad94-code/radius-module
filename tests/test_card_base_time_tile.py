"""«وقت البطاقة» tile — the card's BASE/total time budget (its original
allotment), not the remaining countdown.

The Card Checker gained a KPI tile that shows the ORIGINAL time budget resolved
from the card's batch (the SAME source the remaining-time countdown uses —
``accounting_budget_seconds``, per the batch's from-first-connect budget). So a
card on the «امواج البحر» batch (3h budget) reads «وقت البطاقة = 3 ساعات» even
when «الوقت المتبقّي = 0» (fully consumed) — which now reads sensibly together.

These tests prove:
  1. fmt_base_time_ar resolves whole units to friendly Arabic words
     (3h → «3 ساعات», 30m → «30 دقيقة») and mixed budgets to the shared
     bidi-safe Latin abbreviation.
  2. A card with no from-first-connect budget yields "" (caller shows
     «حسب الصلاحية», never 0).
  3. The base value resolves from the BATCH budget (3h batch →
     accounting_budget_seconds == 3h) and the rendered checker page shows both
     the «وقت البطاقة» tile label and its «3 ساعات» value.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


# ── Pure-function unit tests (no app/DB needed) ───────────────────────────────
class TestFmtBaseTimeAr:
    def test_whole_hours_render_arabic_words(self):
        from app.radius.core.duration_fmt import fmt_base_time_ar
        assert fmt_base_time_ar(3 * 3600) == ("3 ساعات", False)   # «امواج البحر»
        assert fmt_base_time_ar(4 * 3600) == ("4 ساعات", False)
        assert fmt_base_time_ar(3600) == ("1 ساعة", False)
        assert fmt_base_time_ar(2 * 3600) == ("2 ساعتان", False)

    def test_whole_minutes_and_days(self):
        from app.radius.core.duration_fmt import fmt_base_time_ar
        assert fmt_base_time_ar(30 * 60) == ("30 دقيقة", False)
        assert fmt_base_time_ar(5 * 60) == ("5 دقائق", False)
        assert fmt_base_time_ar(5 * 86400) == ("5 أيام", False)

    def test_no_budget_is_empty(self):
        from app.radius.core.duration_fmt import fmt_base_time_ar
        assert fmt_base_time_ar(0) == ("", False)
        assert fmt_base_time_ar(None) == ("", False)

    def test_mixed_budget_uses_latin_bidi_safe(self):
        # 1h 38m is not a whole unit → shared Latin formatter, is_latin=True so
        # the caller wraps it in <bdi dir="ltr">.
        from app.radius.core.duration_fmt import fmt_base_time_ar
        text, is_latin = fmt_base_time_ar(3600 + 38 * 60)
        assert (text, is_latin) == ("1h 38m", True)
        # No RTL / Arabic unit letters leaked into the Latin token.
        for ch in "سديثش":
            assert ch not in text


# ── End-to-end: batch budget → tile value in the rendered checker ─────────────
@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_basetime_")
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


def _seed_3h_batch_card(conn, *, username, first_connect_dt):
    """Seed plan + «امواج البحر» batch (from-first-connect, 3h budget) + card +
    one open session that started ``first_connect_dt`` ago."""
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
        VALUES (1, 'B-WAVES', 'امواج البحر', ?, 1, 1, 0, 1, 0, 3,
                'hours', 'seed', 'active', ?, '{}')
        """,
        (plan_id, now),
    )
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id, used,
             first_used_at, expire_at, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 1, ?, NULL, 0, ?)
        """,
        (batch_id, username, plan_id, _iso(first_connect_dt), now),
    )
    conn.execute(
        """
        INSERT INTO radacct
            (tenant_id, acctsessionid, acctuniqueid, username,
             nasipaddress, acctstarttime, acctsessiontime, callingstationid)
        VALUES (1, 's1', 'u1', ?, '10.0.0.1', ?, ?, 'AA:BB:CC:DD:EE:FF')
        """,
        (username, _iso(first_connect_dt),
         int((datetime.utcnow() - first_connect_dt).total_seconds())),
    )
    return username


def test_base_time_resolves_from_batch_budget(app):
    # 3h batch → accounting_budget_seconds == 3h, independent of how much is left.
    first = datetime.utcnow() - timedelta(hours=1)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_3h_batch_card(c, username="5698046", first_connect_dt=first)

        from app.radius.services.card_checker import check_card
        result = check_card(1, "5698046")

    assert result["exists"] is True
    assert result["accounting_budget_seconds"] == 3 * 3600

    from app.radius.core.duration_fmt import fmt_base_time_ar
    assert fmt_base_time_ar(result["accounting_budget_seconds"]) == ("3 ساعات", False)


def test_checker_page_renders_base_time_tile(app):
    # End-to-end: the rendered checker HTML shows the «وقت البطاقة» tile label
    # and its «3 ساعات» value (the batch's base budget, not the remaining time).
    first = datetime.utcnow() - timedelta(hours=1)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed_3h_batch_card(c, username="5698046", first_connect_dt=first)

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
    resp = client.get("/admin/radius/cards/checker?query=5698046")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "وقت البطاقة" in html
    assert "3 ساعات" in html
