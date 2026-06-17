"""store_alerts — يربط أحداث المتجر المتقدّم بجرس التنبيهات الموجود.

نعيد استخدام نظام التنبيهات نفسه الذي يغذّي «التنبيهات المفتوحة»
(alerts_repo + جدول alerts) الذي يعرض تنبيهات الراوتر — لا نظام موازٍ.
أربعة أحداث تُولّد تنبيهًا للمالك:

  • تسجيل ذاتي لمشترك بطاقات جديد.
  • رسالة شات جديدة من زبون (مجمَّعة لكل زبون: تنبيه واحد للخيط يتجدّد).
  • طلب شحن (إيداع) جديد بانتظار المراجعة.
  • طلب سحب جديد بانتظار التنفيذ.

كل تنبيه له `dedup_key` فريد فلا يتكرّر، ويُحلّ (resolve) تلقائيًا عند
معالجة المدير (تأكيد/رفض الطلب، أو الردّ على الشات/قراءته). كل النداءات
أفضل-جهد: فشل التنبيه لا يكسر تدفّق المتجر أو المال أبدًا.

التعميق (deep-link): نضع رابط الوجهة في evidence.link (لوحة
store-support للقسم المناسب، أو صفحة الزبون) ليستخدمه عرض التنبيه.
"""
from __future__ import annotations

import logging

from ..db.connection import db

_LOG = logging.getLogger(__name__)

RULE_REGISTRATION = "store.registration"
RULE_CHAT = "store.chat"
RULE_DEPOSIT = "store.deposit"
RULE_WITHDRAWAL = "store.withdrawal"

_SUPPORT = "/admin/radius/store-support"


def _tg(tenant_id, key: str, ctx: dict, dedup_key: str = "") -> None:
    """تنبيه إدارة عبر تلجرام (catalogue) — محصّن، لا يكسر تدفّق المتجر."""
    try:
        from .admin_alerts import dispatch
        dispatch(int(tenant_id or 1), key, ctx, dedup_key=dedup_key)
    except Exception:  # noqa: BLE001
        pass


def _open(tenant_id, rule, dedup_key, title_ar, *, severity="info",
          recommended_action_ar="", link=""):
    try:
        from ..db.repos import alerts_repo
        alerts_repo.open(
            tenant_id=int(tenant_id or 1), rule=rule, dedup_key=dedup_key,
            title_ar=title_ar, severity=severity,
            recommended_action_ar=recommended_action_ar,
            evidence={"link": link} if link else None,
        )
    except Exception:  # noqa: BLE001 — التنبيه لا يكسر تدفّق المتجر
        _LOG.exception("store alert open failed: %s", dedup_key)


def _resolve(tenant_id, dedup_key):
    try:
        from ..db.repos import alerts_repo
        alerts_repo.resolve(int(tenant_id or 1), dedup_key)
    except Exception:  # noqa: BLE001
        _LOG.exception("store alert resolve failed: %s", dedup_key)


def _card_user_name(tenant_id, card_user_id) -> str:
    try:
        row = db().execute(
            "SELECT display_name, mobile FROM card_users WHERE tenant_id=? AND id=?",
            (int(tenant_id or 1), int(card_user_id)),
        ).fetchone()
        if row:
            return str(row["display_name"] or row["mobile"] or f"#{card_user_id}")
    except Exception:  # noqa: BLE001
        pass
    return f"#{card_user_id}"


def _card_user_mobile(tenant_id, card_user_id) -> str:
    try:
        row = db().execute(
            "SELECT mobile FROM card_users WHERE tenant_id=? AND id=?",
            (int(tenant_id or 1), int(card_user_id)),
        ).fetchone()
        if row:
            return str(row["mobile"] or "")
    except Exception:  # noqa: BLE001
        pass
    return ""


# ───────────────────────── تسجيل ذاتي ─────────────────────────

def notify_registration(tenant_id, card_user_id, name):
    _open(tenant_id, RULE_REGISTRATION, f"{RULE_REGISTRATION}:{int(card_user_id)}",
          f"مشترك بطاقات جديد: {name}", severity="info",
          recommended_action_ar="افتح «مستخدمو البطاقات» لمراجعة الحساب الجديد.",
          link="/admin/radius/card-users")
    _tg(tenant_id, "store_registration",
        {"name": name or _card_user_name(tenant_id, card_user_id),
         "mobile": _card_user_mobile(tenant_id, card_user_id)},
        dedup_key=f"store_registration:{int(card_user_id)}")


# ───────────────────────── شات الدعم ─────────────────────────

def notify_chat(tenant_id, card_user_id, name=""):
    nm = name or _card_user_name(tenant_id, card_user_id)
    _open(tenant_id, RULE_CHAT, f"{RULE_CHAT}:{int(card_user_id)}",
          f"رسالة دعم جديدة من {nm}", severity="info",
          recommended_action_ar="افتح محادثات الدعم في لوحة «دعم وطلبات المتجر».",
          link=f"{_SUPPORT}?chat={int(card_user_id)}#chat")
    _tg(tenant_id, "store_chat",
        {"name": nm, "card_user_id": int(card_user_id)},
        dedup_key=f"store_chat:{int(card_user_id)}")


def resolve_chat(tenant_id, card_user_id):
    _resolve(tenant_id, f"{RULE_CHAT}:{int(card_user_id)}")


# ───────────────────────── الإيداع (الشحن) ─────────────────────────

def notify_deposit(tenant_id, request_id, amount, currency, name=""):
    who = f" — {name}" if name else ""
    _open(tenant_id, RULE_DEPOSIT, f"{RULE_DEPOSIT}:{int(request_id)}",
          f"طلب شحن جديد: {amount} {currency}{who}", severity="warning",
          recommended_action_ar="راجع الوصل وأكّد/ارفض من «طلبات الشحن».",
          link=f"{_SUPPORT}?tab=deposits")
    _tg(tenant_id, "store_deposit",
        {"name": name or "—", "amount": amount, "currency": currency,
         "request_id": request_id},
        dedup_key=f"store_deposit:{int(request_id)}")


def resolve_deposit(tenant_id, request_id):
    _resolve(tenant_id, f"{RULE_DEPOSIT}:{int(request_id)}")


# ───────────────────────── السحب ─────────────────────────

def notify_withdrawal(tenant_id, request_id, amount, currency, name=""):
    who = f" — {name}" if name else ""
    _open(tenant_id, RULE_WITHDRAWAL, f"{RULE_WITHDRAWAL}:{int(request_id)}",
          f"طلب سحب جديد: {amount} {currency}{who}", severity="warning",
          recommended_action_ar="نفّذ التحويل ثم أكّد/ارفض من «طلبات السحب».",
          link=f"{_SUPPORT}?tab=withdrawals")
    _tg(tenant_id, "store_withdrawal",
        {"name": name or "—", "amount": amount, "currency": currency,
         "request_id": request_id},
        dedup_key=f"store_withdrawal:{int(request_id)}")


def resolve_withdrawal(tenant_id, request_id):
    _resolve(tenant_id, f"{RULE_WITHDRAWAL}:{int(request_id)}")


__all__ = [
    "notify_registration", "notify_chat", "resolve_chat",
    "notify_deposit", "resolve_deposit",
    "notify_withdrawal", "resolve_withdrawal",
]
