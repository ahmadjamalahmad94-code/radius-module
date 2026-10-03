"""One validator for tenant settings shared by the web settings page and
``PATCH /api/v1/settings`` (parity-b, 2026-10-02).

The web page offered selects/toggles/colour pickers and validated a few keys
in its route; the API accepted any free text for the same keys — the app's
generic settings dialog could store «شيكل» as the currency, ``970`` as the
dial code (numbers sent without «+»), ``yes`` for a toggle the policy engine
reads as OFF, or CSS in ``branding.primary_color``. ``clean_setting`` returns
the normalised value or raises ``ValueError`` with an Arabic message.
"""
from __future__ import annotations
from app.i18n_text import _tr

import re

#: Toggles stored as "1"/"0" (the web toggle values).
BOOL_KEYS = frozenset({
    "auth.allow_password_reset",
    "cards.login_without_password_default",
    "security.block_random_mac_cards",
    "security.block_random_mac_subscribers",
    "portal.show_usage", "portal.show_sessions", "portal.show_invoices",
    "portal.allow_password_change", "portal.allow_renewal_request",
    "portal.allow_loan_request", "portal.show_support",
    "portal.allow_self_purchase", "portal.allow_plan_change",
})

_TRUE = {"1", "true", "yes", "on", "t", "y", "نعم", "مفعل", "مفعّل", "تفعيل"}
_FALSE = {"0", "false", "no", "off", "f", "n", "لا", "معطل", "معطّل", "إيقاف", ""}

_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
_HOST = re.compile(r"[A-Za-z0-9.\-]{1,253}")


def currency_codes() -> tuple[str, ...]:
    from .system_config import CURRENCY_CHOICES
    return tuple(code for code, _label in CURRENCY_CHOICES)


def clean_setting(key: str, raw) -> str:
    """Normalised value for ``key`` or ``ValueError`` (Arabic message)."""
    val = "" if raw is None else str(raw).strip()
    if key in BOOL_KEYS:
        low = val.lower()
        if low in _TRUE:
            return "1"
        if low in _FALSE:
            return "0"
        raise ValueError(_tr("قيمة غير صالحة — مفعّل (1) أو معطّل (0)."))
    if key == "billing.currency":
        val = val.upper()
        if val and val not in currency_codes():
            raise ValueError(_tr("العملة غير مدعومة — اختر من: ") + "، ".join(currency_codes()) + ".")
        return val
    if key == "branding.primary_color":
        if val and not _HEX_COLOR.fullmatch(val):
            raise ValueError(_tr("اللون غير صالح — استخدم صيغة ‎#RRGGBB مثل ‎#2BAACC."))
        return val
    if key == "comms.country_dial_code" and val:
        digits = val.lstrip("+").replace(" ", "")
        if not digits.isdigit() or not (1 <= len(digits) <= 4):
            raise ValueError(_tr("مفتاح الدولة غير صالح — استخدم الصيغة الدولية مثل ‎+970 أو ‎+962."))
        return "+" + digits
    if key == "network.radius_server_ip" and val:
        host = val.removeprefix("http://").removeprefix("https://").rstrip("/")
        if not _HOST.fullmatch(host):
            raise ValueError(_tr("عنوان IP سيرفر الراديوس غير صالح — اكتب IP مثل ‎10.10.0.1 أو اسم مضيف."))
        return host
    if key in ("device_limit.subscribers.mode", "device_limit.cards.mode"):
        val = val.lower()
        if val not in ("reject", "replace"):
            raise ValueError(_tr("السلوك يجب أن يكون reject (رفض الجهاز الجديد) أو replace (استبدال الأقدم)."))
        return val
    if key in ("device_limit.subscribers.count", "device_limit.cards.count"):
        try:
            n = int(val)
        except ValueError:
            raise ValueError(_tr("عدد الأجهزة يجب أن يكون عددًا صحيحًا ≥ 1.")) from None
        if n < 1:
            raise ValueError(_tr("عدد الأجهزة يجب أن يكون عددًا صحيحًا ≥ 1."))
        return str(n)
    if key == "security.unauthorized_ui":
        val = val.lower()
        if val not in ("freeze", "hide"):
            raise ValueError(_tr("القيمة يجب أن تكون freeze (تجميد) أو hide (إخفاء)."))
        return val
    if key == "billing.timezone_offset" and val:
        try:
            float(val)
        except ValueError:
            raise ValueError(_tr("الإزاحة الزمنيّة رقمٌ بالساعات مثل 2 أو 3.")) from None
        return val
    return val
