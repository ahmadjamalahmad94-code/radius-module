"""«وقت اليوم» must be wall-clock (union of session intervals), not the sum
of every device's acctsessiontime — otherwise 2 devices online for 2h each
would read 4h instead of 2h.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_daytime_")
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


def test_daily_time_is_wallclock_union_not_device_sum(app):
    with app.app_context():
        from app.radius.db.connection import transaction
        from app.radius.services.policy_engine import daily_used_seconds_bulk

        with transaction() as c:
            def add(u, start, dur):
                c.execute(
                    "INSERT INTO radacct (tenant_id, username, acctstarttime, "
                    "acctsessiontime) VALUES (?,?,?,?)", (1, u, start, dur))

            # 2 devices, SAME 2h window → real time online = 2h (not 4h)
            add("u_ov", "2026-07-11 10:00:00", 7200)
            add("u_ov", "2026-07-11 10:00:00", 7200)
            # 2 non-overlapping 1h sessions → 2h total
            add("u_seq", "2026-07-11 10:00:00", 3600)
            add("u_seq", "2026-07-11 11:00:00", 3600)
            # partial overlap [10:00-12:00] + [11:00-13:00] → 3h
            add("u_part", "2026-07-11 10:00:00", 7200)
            add("u_part", "2026-07-11 11:00:00", 7200)

        # far-past `since` so the elapsed-since-midnight cap never clamps here
        res = daily_used_seconds_bulk(
            1, ["u_ov", "u_seq", "u_part"], since_iso="2000-01-01T00:00:00")

        assert res["u_ov"] == 7200      # concurrent devices collapse (NOT 14400)
        assert res["u_seq"] == 7200     # sequential sums normally
        assert res["u_part"] == 10800   # partial overlap counted once
