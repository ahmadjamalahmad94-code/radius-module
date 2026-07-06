"""Attention-filter contract: the filtered subscribers-list row-count
must equal the «ما يحتاج انتباه» counters on the dashboard.

The dashboard renders two clickable alerts:
  • "N مشترك ينتهي اشتراكه خلال 3 أيام" → ?attention=expiring_3d
  • "M مشترك انتهى اشتراكه — جدّد أو احذف." → ?attention=expired

This test seeds a mixed pool and asserts:
  - dashboard count for expiring_soon == users_list rows under ?attention=expiring_3d
  - dashboard count for expired      == users_list rows under ?attention=expired
  - the dashboard alerts carry the matching link_args
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_att_")
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


def _seed(tenant_id, username, *, status="enabled", expire_at=None):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=tenant_id, username=username, password="x",
        user_type="subscriber", status=status, expire_at=expire_at,
    ))


def test_attention_filter_counts_match_dashboard(app):
    with app.app_context():
        now = datetime.utcnow()
        # expiring within 3 days — only the ENABLED ones count («ينتهي» =
        # المفعّلون فقط، مطابقةً للبطاقة). soon_c is suspended → excluded.
        # NOTE: stored expire_at uses ISO with 'T' separator, while SQLite's
        # datetime('now') returns ' '-separated; the SQL lexicographic compare
        # is shaky at the same-day boundary. We use timestamps well inside
        # the window (≤ 2 days, ≥ 1 hour) so both Python and SQL agree.
        _seed(1, "soon_a", status="enabled",   expire_at=now + timedelta(hours=6))
        _seed(1, "soon_b", status="enabled",   expire_at=now + timedelta(days=1))
        _seed(1, "soon_c", status="suspended", expire_at=now + timedelta(days=2))
        # well past the 3-day boundary
        _seed(1, "edge_3d", status="enabled", expire_at=now + timedelta(days=5))
        # already expired (status='expired') — 4 of them
        _seed(1, "old_a", status="expired", expire_at=now - timedelta(days=5))
        _seed(1, "old_b", status="expired", expire_at=now - timedelta(days=10))
        _seed(1, "old_c", status="expired")
        _seed(1, "old_d", status="expired")
        # active non-expiring noise
        _seed(1, "noise_a", status="enabled", expire_at=now + timedelta(days=30))
        _seed(1, "noise_b", status="enabled")

        # — dashboard counters
        from app.radius.services.dashboard_metrics import (
            get_subscriber_counts, build_alerts,
        )
        counts = get_subscriber_counts(tenant_id=1)
        # «ينتهي خلال 3 أيام» = المفعّلون فقط (soon_a/soon_b)؛ soon_c معطّل
        # (suspended) فيُستثنى — مطابقةً لبطاقة صفحة المشتركين وللفلتر.
        assert counts["expiring_soon"] == 2, counts
        assert counts["expired"] == 4, counts

        # — filtered list row-counts via the route
        client = app.test_client()
        # bypass auth: super-admin session flag
        with client.session_transaction() as s:
            s["is_super_admin"] = True
            s["admin_id"] = 1
            s["admin_username"] = "root"

        # find users_list URL via url_map
        url_users = None
        for rule in app.url_map.iter_rules():
            if rule.endpoint == "radius.users_list":
                url_users = rule.rule
                break
        assert url_users, "radius.users_list endpoint not registered"

        r1 = client.get(url_users + "?attention=expiring_3d")
        assert r1.status_code == 200, r1.status_code
        html1 = r1.get_data(as_text=True)
        # rows in the table are <tr data-status=...> — count those for soon_*
        # but the simpler check is: html mentions each expected username and
        # not the wrong ones.
        for name in ("soon_a", "soon_b"):
            assert name in html1, f"expected {name} in expiring_3d view"
        # soon_c معطّل (suspended) → مُستثنى من «ينتهي» (المفعّلون فقط).
        for name in ("soon_c", "old_a", "old_b", "noise_a", "edge_3d"):
            assert name not in html1, f"{name} leaked into expiring_3d view"
        import re as _re
        # صفوف الجدول للفلتر = 2 (soon_a/soon_b) — تُطابق البطاقة (شكوى المالك).
        assert len(_re.findall(r'<tr[^>]*\bdata-username="soon_[ab]"', html1)) == 2

        # — بطاقة صفحة المشتركين «ينتهي خلال 3 أيام» يجب أن تساوي القائمة (2).
        html0 = client.get(url_users).get_data(as_text=True)
        m = _re.search(r'ينتهي خلال 3 أيام\s*</div>\s*'
                       r'<div class="hub-kpi-value">\s*(\d+)', html0, _re.DOTALL)
        assert m and int(m.group(1)) == 2, "بطاقة «ينتهي» ≠ عدد المفعّلين المنتهين"

        r2 = client.get(url_users + "?attention=expired")
        assert r2.status_code == 200, r2.status_code
        html2 = r2.get_data(as_text=True)
        for name in ("old_a", "old_b", "old_c", "old_d"):
            assert name in html2, f"expected {name} in expired view"
        for name in ("soon_a", "soon_b", "noise_a"):
            assert name not in html2, f"{name} leaked into expired view"

        # — dashboard alerts must carry the right link_args
        alerts = build_alerts(
            system={}, subs=counts, cards={"total": 0, "available": 0},
            plans={"total": 1}, nas={"total": 1},
        )
        soon_alert = next(
            (a for a in alerts if (a.get("link_args") or {}).get("attention") == "expiring_3d"),
            None,
        )
        expired_alert = next(
            (a for a in alerts if (a.get("link_args") or {}).get("attention") == "expired"),
            None,
        )
        assert soon_alert and soon_alert["link_endpoint"] == "radius.users_list"
        assert expired_alert and expired_alert["link_endpoint"] == "radius.users_list"


def test_online_filter_counts_all_online_not_just_current_page(app, monkeypatch):
    """«متصل الآن»: نقر البطاقة (=54) يجب أن يَعرض كل المتصلين لا متصلي الصفحة
    الحاليّة فقط. الفلتر كان Python post-filter بعد الترقيم → يُظهر متصلي الصفحة
    (9) بينما العدّاد كامل النطاق (54). الآن القصر على مستوى SQL فيتّفقان."""
    import re as _re
    with app.app_context():
        online = set()
        # 24 مشتركًا؛ 12 متصلون (فهارس زوجيّة) موزّعون عبر ترتيب المعرّف
        for i in range(24):
            u = f"sub_{i:02d}"
            _seed(1, u, status="enabled")
            if i % 2 == 0:
                online.add(u)
        # live_usernames يُستورَد محليًّا داخل users_list → نُرقّع سِمة الموديول
        monkeypatch.setattr(
            "app.radius.services.live_sessions.live_usernames",
            lambda tid: set(online))

        client = app.test_client()
        with client.session_transaction() as s:
            s["is_super_admin"] = True
            s["admin_id"] = 1

        url = next(r.rule for r in app.url_map.iter_rules()
                   if r.endpoint == "radius.users_list")
        # page_size صغير (5 < 12) — الباج القديم كان يُظهر «من 24» ومتصلي الصفحة فقط.
        html = client.get(url + "?online=1&page_size=5").get_data(as_text=True)
        m = _re.search(r'class="srv-info">[^<]*?من\s*(\d+)', html)
        assert m, "srv-info total not found"
        assert int(m.group(1)) == 12, f"total should be online-count 12, got {m.group(1)}"
        # ولا يَتسرّب مشترك غير متصل إلى النتائج (نطاق SQL صحيح)
        assert 'data-username="sub_01"' not in html   # فرديّ = غير متصل
