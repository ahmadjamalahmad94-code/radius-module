"""«وقت اليوم» column — today's accumulated connection time per subscriber.

Owner wants to watch daily usage approach the cap (to confirm the 4h/day
disconnect fires). The value uses the SAME counter as enforcement
(SUM(acctsessiontime) since local day-start).
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_dailycol_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_daily_used_bulk_sums_today_only(app):
    from app.radius.db.connection import transaction
    with app.app_context():
        with transaction() as conn:
            for sid, secs, when in [
                ("s1", 3600, "datetime('now')"),        # u1 today: 1h
                ("s2", 600,  "datetime('now')"),        # u1 today: +10m
                ("s3", 9999, "datetime('now','-2 days')"),  # u1 old → excluded
            ]:
                conn.execute(
                    f"INSERT INTO radacct(tenant_id,username,acctsessionid,"
                    f"acctstarttime,acctsessiontime) VALUES(1,'u1',?,{when},?)",
                    (sid, secs))
            conn.execute(
                "INSERT INTO radacct(tenant_id,username,acctsessionid,"
                "acctstarttime,acctsessiontime) VALUES(1,'u2','s4',datetime('now'),120)")
        from app.radius.services.policy_engine import (
            daily_used_seconds_bulk, _elapsed_since, _local_day_start_utc)
        used = daily_used_seconds_bulk(1, ["u1", "u2", "u3"])
        # القيم مُقيَّدة بالمنقضي منذ منتصف الليل المحلّي (لا يتّصل أحد ثوانيَ
        # أكثر ممّا مضى من اليوم) — مهمّ لثبات الاختبار قرب منتصف الليل.
        cap = _elapsed_since(_local_day_start_utc(1))
    # u1's two today sessions start at the SAME instant (concurrent devices):
    # 1h + 10m fully overlap → wall-clock = 1h (3600), NOT the 4200 sum. The
    # old (-2 days) session is excluded by the today filter — if it leaked in,
    # its non-overlapping interval would add 9999s, so 3600 also proves the
    # filter still holds.
    assert used.get("u1") == min(3600, cap)
    assert used.get("u2") == min(120, cap)
    assert used.get("u3", 0) == 0          # no sessions


def test_daily_used_clamped_to_elapsed_since_midnight(app):
    """جلسة بطابع بدء = «الآن» لكن acctsessiontime ضخم (بق «وقت الالتقاط» +
    عبور منتصف الليل) لا تُظهر أكثر ممّا انقضى فعليًّا من اليوم."""
    from app.radius.db.connection import transaction
    with app.app_context():
        with transaction() as conn:
            conn.execute(
                "INSERT INTO radacct(tenant_id,username,acctsessionid,"
                "acctstarttime,acctsessiontime) VALUES(1,'zomb','zz',datetime('now'),999999)")
        from app.radius.services.policy_engine import (
            daily_used_seconds_bulk, _elapsed_since, _local_day_start_utc)
        used = daily_used_seconds_bulk(1, ["zomb"])
        cap = _elapsed_since(_local_day_start_utc(1))
    # مُقيَّد: لا يُساوي 999999، بل ≤ المنقضي منذ منتصف الليل (≤ 24 ساعة).
    assert used.get("zomb") == min(999999, cap)
    assert used.get("zomb") <= 86400


def test_effective_daily_cap_prefers_override(app):
    from app.radius.services.policy_engine import effective_daily_cap_min
    with app.app_context():
        class _P:  # plan-like
            max_daily_minutes = 240
        class _S:  # sub-like, no override
            connection_time_limit_enabled = False
            total_connection_time_min = 0
            daily_connection_time_min = 0
        assert effective_daily_cap_min(_S(), _P()) == 240      # falls back to plan
        _S.daily_connection_time_min = 90
        assert effective_daily_cap_min(_S(), _P()) == 90       # override wins


def test_subscribers_list_renders_daily_time_column(app):
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import transaction
    from app.radius.db.repos import subscribers_repo
    with app.app_context():
        subscribers_repo.upsert_subscriber(Subscriber(
            id=None, username="duser", password="pw", tenant_id=1, status="enabled"))
        with transaction() as conn:
            conn.execute(
                "INSERT INTO radacct(tenant_id,username,acctsessionid,"
                "acctstarttime,acctsessiontime) VALUES(1,'duser','sx',datetime('now'),3660)")

    client = app.test_client()
    with client.session_transaction() as s:
        s["admin_id"] = 1
        s["is_super_admin"] = True
        s["tenant_id"] = 1
    html = client.get("/admin/radius/subscribers").get_data(as_text=True)
    assert "وقت اليوم" in html                 # column header
    assert 'data-col="daily_used"' in html      # the cell
    assert "du-cell" in html                     # duser's row shows a value, not —


def test_daily_cells_are_ltr_isolated():
    """SEC/UX — «وقت اليوم» (مثل «5س 30د / 4س») لا يَنقلب في RTL: يُغلَّف
    بـ<bdi dir="ltr"> والخليّة dir=ltr، تمامًا كإصلاح عناوين MAC."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for tpl in ("app/templates/radius/users_list.html",
                "app/templates/radius/sessions_list.html"):
        html = (root / tpl).read_text(encoding="utf-8")
        # الخليّة نفسها dir=ltr + القيمة داخل bdi dir=ltr
        assert 'data-col="daily_used"' in html and 'dir="ltr"' in html
        assert 'bdi dir="ltr" class="du-cell' in html, f"{tpl}: du-cell غير معزول LTR"
        # isolate وحده لا يكفي (قاعدة W2: رقم بعد حرف عربيّ → رقم عربيّ يَنقلب)؛
        # نَفرض الترتيب المنطقيّ بـ isolate-override.
        assert "unicode-bidi:isolate-override" in html.replace(" ", ""), \
            f"{tpl}: du-cell يجب أن يَفرض LTR بـ isolate-override"
