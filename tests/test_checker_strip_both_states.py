"""The top KPI strip on the Card Checker must render for BOTH a connected and a
disconnected («غير متصلة» / «استُعملت») card — identical layout, five tiles.

Owner report: for a disconnected card the top 5-KPI strip (إجمالي الاستهلاك /
الوقت المتبقّي / وقت البطاقة / أجهزة متّصلة الآن / الجلسات) did not appear, so the
page jumped straight from the search bar to the detail panel. Two facts:

  1. Server render: the megahero strip is populated for ANY existing card, so a
     full-page load shows all five tiles for a disconnected card too. The
     live-only metrics (أجهزة متّصلة الآن / الجلسات-online) read 0, while
     الوقت المتبقّي / وقت البطاقة / إجمالي الاستهلاك keep their real stored values.
  2. AJAX parity: the strip lives OUTSIDE #cc-result, so an in-page search (which
     swaps only #cc-result) used to leave it stale. cards_checker_v2.js now
     mirrors `.uds-hero-kpis` from the fetched response (syncHeroKpis) so the
     strip shows on every lookup, connected or not.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile
from datetime import datetime, timedelta

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]

_STRIP_LABELS = (
    "الجلسات",
    "أجهزة متّصلة الآن",
    "وقت البطاقة",
    "الوقت المتبقّي",
    "إجمالي الاستهلاك",
)


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_strip_")
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


def _seed(conn, *, username, connected):
    """Seed a plan + 3h from-first-connect batch + card + one session.

    connected=True  → an OPEN radacct row (active_session True).
    connected=False → a CLOSED radacct row 20m ago (used / disconnected).
    """
    now = _iso(datetime.utcnow())
    conn.execute(
        "INSERT INTO access_plans (tenant_id, name, enabled, created_at) "
        "VALUES (1, ?, 1, ?)", ("p-" + username, now))
    plan_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO card_batches
            (tenant_id, batch_code, package_name, plan_id, count, generated,
             used, count_from_first_connect, count_by_seconds, time_value,
             time_unit, created_by, status, created_at, metadata)
        VALUES (1, ?, 'امواج البحر', ?, 1, 1, 0, 1, 0, 3, 'hours', 'seed',
                'active', ?, '{}')
        """,
        ("B-" + username, plan_id, now),
    )
    batch_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    first = datetime.utcnow() - timedelta(hours=1)
    conn.execute(
        """
        INSERT INTO cards
            (tenant_id, batch_id, username, password, plan_id, used,
             first_used_at, expire_at, revoked, created_at)
        VALUES (1, ?, ?, 'pw', ?, 1, ?, NULL, 0, ?)
        """,
        (batch_id, username, plan_id, _iso(first), now),
    )
    if connected:
        conn.execute(
            """
            INSERT INTO radacct
                (tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
                 acctstarttime, acctsessiontime, callingstationid)
            VALUES (1, ?, ?, ?, '10.0.0.1', ?, ?, 'AA:BB:CC:DD:EE:FF')
            """,
            ("s-" + username, "u-" + username, username, _iso(first),
             int((datetime.utcnow() - first).total_seconds())),
        )
    else:
        stop = datetime.utcnow() - timedelta(minutes=20)
        conn.execute(
            """
            INSERT INTO radacct
                (tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
                 acctstarttime, acctstoptime, acctsessiontime, callingstationid)
            VALUES (1, ?, ?, ?, '10.0.0.1', ?, ?, ?, 'AA:BB:CC:DD:EE:FF')
            """,
            ("s-" + username, "u-" + username, username, _iso(first),
             _iso(stop), int((stop - first).total_seconds())),
        )
    return username


def _render(app, username):
    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["admin_user"] = "test"
        s["tenant_id"] = 1
    resp = client.get(f"/admin/radius/cards/checker?query={username}")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_disconnected_card_still_renders_full_kpi_strip(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="disc1", connected=False)

        from app.radius.services.card_checker import check_card
        result = check_card(1, "disc1")
    # It IS a used/disconnected card, not active.
    assert result["exists"] is True
    assert result["active_session"] is False

    html = _render(app, "disc1")
    # The megahero KPI strip is present with all five tiles.
    assert "uds-hero-kpis" in html
    assert html.count('class="hub-kpi hub-kpi--') == 5
    for label in _STRIP_LABELS:
        assert label in html, f"missing KPI label: {label}"
    # Stored metrics keep real values; base time = «3 ساعات».
    assert "3 ساعات" in html


def test_connected_and_disconnected_strips_are_equivalent(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            _seed(c, username="conn1", connected=True)
            _seed(c, username="disc1", connected=False)

    html_conn = _render(app, "conn1")
    html_disc = _render(app, "disc1")
    # Both states render the same five-tile strip — no state-gated omission.
    assert "uds-hero-kpis" in html_conn
    assert "uds-hero-kpis" in html_disc
    assert html_conn.count('class="hub-kpi hub-kpi--') == 5
    assert html_disc.count('class="hub-kpi hub-kpi--') == 5


def test_ajax_lookup_syncs_the_top_strip():
    """cards_checker_v2.js must refresh the megahero strip on AJAX lookups so it
    is not left stale — the strip lives outside the swapped #cc-result."""
    js = (_ROOT / "app/static/js/cards_checker_v2.js").read_text(encoding="utf-8")
    # The sync helper exists, is invoked on lookup, and targets the strip.
    assert "function syncHeroKpis(" in js
    assert "syncHeroKpis(doc)" in js
    assert ".uds-hero-kpis" in js
