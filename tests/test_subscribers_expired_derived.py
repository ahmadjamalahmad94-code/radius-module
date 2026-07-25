"""«منتهي الاشتراك» حالة مشتقّة — regression لحادثة client1 (2026-07-25).

لا شيء في النظام يقلب subscribers.status إلى 'expired' تلقائيًّا؛ الانتهاء
الحقيقي = status='enabled' مع expire_at في الماضي. كانت العدّادات والفلاتر
تعتمد على الحالة المخزّنة فقط → بطاقة «منتهي الاشتراك» صفر دائمًا وفلتر
«منتهي» لا يُرجِع أحدًا (1594 مشتركًا منهم 156 منتهيًا فعليًّا ظهروا 0).

يثبت هذا الملف أن:
  • subscribers_status_counts يحسب المفعّل-المنتهي ضمن 'expired' لا 'enabled'.
  • فلتر status='expired' في list/count يُرجعه، وفلتر 'enabled' يستبعده.
  • المعطّل المنتهي يبقى «معطّلًا» (أولوية الحظر الصريح).
  • صيغة الطابع بـ'T'/'Z' (كما تُخزَّن فعليًّا) تُقارَن صحيحًا حتى على
    حدود اليوم نفسه (datetime() normalization — لا مقارنة نصية خام).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "dev-token-please-change")
    from app import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _tenant(app):
    from app.radius.db.connection import transaction

    with transaction() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO tenants(id, name, slug, created_at) "
            "VALUES (1, 'Default Tenant', 'default', '2026-01-01T00:00:00Z')"
        )


def _seed(status: str, *, expire_iso: str | None = None) -> str:
    """يزرع مشتركًا ثم يفرض صيغة expire_at النصية كما هي (بلا تطبيع الـ ORM)."""
    from app.radius.core.types import Subscriber
    from app.radius.db.connection import transaction
    from app.radius.db.repos import subscribers_repo

    username = "exp_" + secrets.token_hex(6)
    subscribers_repo.upsert_subscriber(
        Subscriber(id=None, tenant_id=1, username=username,
                   password="pw1234", status=status, user_type="subscriber"))
    if expire_iso is not None:
        with transaction() as conn:
            conn.execute(
                "UPDATE subscribers SET expire_at = ? WHERE username = ?",
                (expire_iso, username))
    return username


def _tz(dt: datetime) -> str:
    """صيغة التخزين الفعلية على الخوادم: 2026-07-08T11:04:30Z."""
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_enabled_past_expiry_counts_as_expired(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        base = subscribers_repo.subscribers_status_counts(1, user_type="subscriber")
        now = datetime.utcnow()
        # منتهٍ منذ ساعة (نفس اليوم — يكشف انحراف المقارنة النصية 'T' مقابل مسافة)
        u_expired_today = _seed("enabled", expire_iso=_tz(now - timedelta(hours=1)))
        # منتهٍ منذ أسبوع
        u_expired_week = _seed("enabled", expire_iso=_tz(now - timedelta(days=7)))
        # مفعّل ينتهي غدًا — يبقى فعّالًا
        u_active = _seed("enabled", expire_iso=_tz(now + timedelta(days=1)))
        # مفعّل بلا انتهاء — يبقى فعّالًا
        u_no_exp = _seed("enabled")
        # معطّل منتهٍ — يبقى معطّلًا
        u_disabled = _seed("disabled", expire_iso=_tz(now - timedelta(days=3)))

        counts = subscribers_repo.subscribers_status_counts(1, user_type="subscriber")
        d_expired = counts["by_status"].get("expired", 0) - base["by_status"].get("expired", 0)
        d_enabled = counts["by_status"].get("enabled", 0) - base["by_status"].get("enabled", 0)
        d_disabled = counts["by_status"].get("disabled", 0) - base["by_status"].get("disabled", 0)
        assert d_expired == 2, f"expired derived count wrong: {counts}"
        assert d_enabled == 2, f"enabled must exclude past-expiry: {counts}"
        assert d_disabled == 1

        # الفلتر: «منتهي» يُرجع المنتهين المفعّلين، «فعّال» يستبعدهم
        exp_names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", status="expired", limit=10_000)}
        assert {u_expired_today, u_expired_week} <= exp_names
        assert u_active not in exp_names
        assert u_no_exp not in exp_names
        assert u_disabled not in exp_names, "المعطّل يبقى معطّلًا لا منتهيًا"

        en_names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", status="enabled", limit=10_000)}
        assert {u_active, u_no_exp} <= en_names
        assert u_expired_today not in en_names
        assert u_expired_week not in en_names

        # count يطابق القائمة (نفس _subscriber_filter_sql)
        assert subscribers_repo.count_subscribers(
            1, user_type="subscriber", status="expired") == len(exp_names)


def test_stored_expired_status_still_counts(app):
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        base = subscribers_repo.count_subscribers(
            1, user_type="subscriber", status="expired")
        u = _seed("expired")  # حالة مخزّنة يدويًّا بلا expire_at
        assert subscribers_repo.count_subscribers(
            1, user_type="subscriber", status="expired") == base + 1
        names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", status="expired", limit=10_000)}
        assert u in names


def test_space_format_timestamp_also_derives(app):
    """صيغة «مسافة» (YYYY-MM-DD HH:MM:SS) تعمل أيضًا — الصيغ مختلطة تاريخيًّا."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        now = datetime.utcnow()
        u = _seed("enabled",
                  expire_iso=(now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"))
        names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", status="expired", limit=10_000)}
        assert u in names


def test_expiring_window_excludes_already_expired(app):
    """«ينتهي خلال 3 أيام» لا يلتقط منتهيًا اليوم (انحراف الحدّ النصي سابقًا)."""
    with app.app_context():
        from app.radius.db.repos import subscribers_repo

        now = datetime.utcnow()
        u_gone = _seed("enabled", expire_iso=_tz(now - timedelta(hours=1)))
        u_soon = _seed("enabled", expire_iso=_tz(now + timedelta(days=2)))
        names = {s.username for s in subscribers_repo.list_subscribers(
            1, user_type="subscriber", expiring_within_days=3, limit=10_000)}
        assert u_soon in names
        assert u_gone not in names
