"""حارس قنوات الحدث — حادثة client1 (2026-07-27): «بشتري بطاقة ما بتصل بياناتها».

`notif.store_cards_purchased.channels` كانت محفوظة «telegram» وحدها بينما
الحدث يدعم SMS/واتساب فقط (المشتري card_user بلا تيليجرام) — فكان إشعار
بيانات البطاقة يموت بصمت رغم أن الحدث مفعّل، بينما رسالة شحن الرصيد
(قنواتها sms,whatsapp) تصل. الواجهة القديمة عرضت القنوات الثلاث لكل حدث
فسمحت بحفظ المزيج الميت.

الحارس في `_parse_channels`: قناة محفوظة لا يدعمها الحدث تُرشَّح؛ ولو كانت
كل المحفوظات غير مدعومة (misconfig لا «إيقاف صريح») نعود لقنوات الحدث
الافتراضية. الفارغ الصريح يبقى فارغًا (المشغّل أزال الكل عمدًا).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
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


def _set(app, key: str, value: str) -> None:
    from app.radius.db.repos import tenants_repo

    tenants_repo.set_setting(1, key, value, by=0)


def test_dead_channel_only_falls_back_to_event_defaults(app):
    """قناة telegram وحدها على حدث sms/whatsapp ⇒ ترجع قنوات الحدث (السيناريو الحقيقي)."""
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        _set(app, "notif.store_cards_purchased.enabled", "1")
        _set(app, "notif.store_cards_purchased.channels", "telegram")
        rule = ne.load_rule(1, "store_cards_purchased")
        assert rule.enabled is True
        assert rule.channels == ["sms", "whatsapp"]


def test_unsupported_channel_filtered_supported_kept(app):
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        _set(app, "notif.store_cards_purchased.channels", "telegram,sms")
        rule = ne.load_rule(1, "store_cards_purchased")
        assert rule.channels == ["sms"]


def test_explicit_empty_still_means_none(app):
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        _set(app, "notif.store_cards_purchased.channels", "")
        rule = ne.load_rule(1, "store_cards_purchased")
        assert rule.channels == []


def test_unset_uses_event_defaults(app):
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        rule = ne.load_rule(1, "store_balance_withdraw")
        assert rule.channels == ["sms", "whatsapp"]


def test_subscriber_event_with_telegram_untouched(app):
    """حدث يدعم تيليجرام فعلًا لا يتأثر بالحارس."""
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        _set(app, "notif.subscriber_expired.channels", "telegram,sms")
        rule = ne.load_rule(1, "subscriber_expired")
        assert rule.channels == ["telegram", "sms"]


def test_billing_event_keeps_saved_telegram(app):
    """الحارس مقصور على مجموعة المتجر — تيليجرام محفوظة لحدث فوترة تبقى
    (تصل المشترك الرابط حسابه حتى لو لم تكن ضمن قنوات الحدث الافتراضية)."""
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        _set(app, "notif.recharge_added.channels", "telegram")
        rule = ne.load_rule(1, "recharge_added")
        assert rule.channels == ["telegram"]


def test_save_rules_filters_unsupported(app):
    with app.app_context():
        from app.radius.services import notifications_engine as ne

        ne.save_rules(1, {
            "store_cards_purchased__enabled": "1",
            "store_cards_purchased__channels": ["telegram", "whatsapp"],
            "store_cards_purchased__template": "x",
        }, only_keys=["store_cards_purchased"])
        rule = ne.load_rule(1, "store_cards_purchased")
        assert rule.channels == ["whatsapp"]
