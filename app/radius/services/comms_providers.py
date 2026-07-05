"""Generic HTTP notification senders (SMS + WhatsApp) — Phase 1.

These providers turn the previously queued-only notification system into one
that *actually sends* through a tenant-supplied HTTP endpoint, without adding
any third-party dependency. The recipient phone and the message body are
URL-encoded into a template such as::

    https://gateway.example.com/send?to={phone}&text={msg}

The call is performed with the Python standard library only (``urllib``) and a
short timeout. Providers are intentionally *defensive*: ``send`` never raises —
on any error it returns a ``failed`` :class:`ProviderResult` carrying the error
text, so a misconfigured gateway can never break the notification pipeline.

Per-channel configuration is stored in ``tenant_settings`` under the keys
``comms.sms.*`` / ``comms.whatsapp.*`` (see :func:`load_channel_config` /
:func:`save_channel_config`). No external secrets schema is required for
Phase 1 — the full send URL (which may itself embed a token/api-key) lives in
``send_url_template``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from .notification_campaigns import NotificationProvider, ProviderConfig, ProviderResult

# Channels that can dispatch through the generic HTTP sender. telegram / internal
# stay queued-only and are handled by the existing QueuedOnlyProvider.
HTTP_CHANNELS = ("sms", "whatsapp")

# Channel mode. SMS & WhatsApp are now FREE bring-your-own-provider services:
# the customer always plugs in their own gateway/account, so ``self_api`` is the
# only mode. (The retired ``admin_quota`` "sell message bundles" model has been
# removed.) The field is kept for backward compatibility and always normalises
# to ``self_api``.
CHANNEL_MODES = ("self_api",)

DEFAULT_MODE = "self_api"
DEFAULT_METHOD = "GET"

# How much of the gateway response we keep for the delivery log. Plenty for a
# human to diagnose, small enough to never bloat result_json.
_RESPONSE_EXCERPT_LIMIT = 600
_HTTP_TIMEOUT_SECONDS = 8.0


def _settings_key(channel: str, field: str) -> str:
    return f"comms.{channel}.{field}"


# ── مفتاح إعداد «مفتاح الدولة» (صفحة الإعدادات → هوية النظام) ──
DIAL_CODE_SETTING = "comms.country_dial_code"
DEFAULT_DIAL_CODE = "+970"


def normalize_msisdn(phone: str, dial_code: str = "") -> str:
    """تطبيع رقم الجوال إلى الصيغة الدولية قبل الإرسال عبر SMS/واتساب.

    القاعدة بسيطة وآمنة: الرقم المحلي الذي يبدأ بصفر واحد (0599...)
    يُستبدل صفره بمفتاح الدولة (+970599...). الأرقام الدولية أصلًا
    (تبدأ بـ + أو 00) تُترك كما هي، وكذلك أي رقم لا نفهم صيغته —
    لا نخمّن أبدًا حتى لا نكسر إرسالًا كان يعمل.
    """
    raw = str(phone or "").strip().replace(" ", "").replace("-", "")
    if not raw:
        return ""
    code = str(dial_code or "").strip()
    # دوليّ أصلًا — لا تلمسه.
    if raw.startswith("+"):
        return raw
    if raw.startswith("00"):
        return "+" + raw[2:]
    # محلي يبدأ بصفر واحد + يوجد مفتاح دولة مضبوط → استبدال الصفر بالمفتاح.
    if code and raw.startswith("0") and not raw.startswith("00") and raw[1:].isdigit():
        return code + raw[1:]
    return raw


def tenant_dial_code(tenant_id: int) -> str:
    """قراءة مفتاح الدولة المضبوط للمستأجر (فارغ = الافتراضي +970).

    القراءة دفاعية: أي فشل في الوصول للإعدادات يعيد القيمة الافتراضية
    حتى لا يتعطّل خط الإرسال بسبب الإعدادات.
    """
    try:
        from ..db.repos import tenants_repo

        return (tenants_repo.get_setting(int(tenant_id or 1), DIAL_CODE_SETTING, DEFAULT_DIAL_CODE) or "").strip()
    except Exception:  # noqa: BLE001 — قراءة الإعداد يجب ألا تكسر الإرسال
        return DEFAULT_DIAL_CODE


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _method(value: Any) -> str:
    method = str(value or DEFAULT_METHOD).strip().upper()
    return method if method in ("GET", "POST") else DEFAULT_METHOD


def _mode(value: Any) -> str:
    mode = str(value or DEFAULT_MODE).strip().lower()
    return mode if mode in CHANNEL_MODES else DEFAULT_MODE


def _channel(channel: str) -> str:
    ch = str(channel or "").strip().lower()
    if ch not in HTTP_CHANNELS:
        raise ValueError("unsupported http channel")
    return ch


def load_channel_config(tenant_id: int, channel: str) -> dict[str, Any]:
    """Read a channel's HTTP-sender config from ``tenant_settings``.

    Always returns a fully-populated dict with safe defaults so callers and
    templates never have to guard against missing keys.
    """
    ch = _channel(channel)
    # Imported lazily to avoid a circular import at module load.
    from ..db.repos import tenants_repo

    def _get(field: str, default: str = "") -> str:
        return tenants_repo.get_setting(int(tenant_id or 1), _settings_key(ch, field), default)

    return {
        "channel": ch,
        "enabled": _truthy(_get("enabled", "0")),
        "mode": _mode(_get("mode", DEFAULT_MODE)),
        "send_url_template": (_get("send_url_template", "") or "").strip(),
        "http_method": _method(_get("http_method", DEFAULT_METHOD)),
        "balance_url": (_get("balance_url", "") or "").strip(),
    }


def save_channel_config(tenant_id: int, channel: str, values: dict[str, Any], *, by: int = 0) -> dict[str, Any]:
    """Persist a channel's HTTP-sender config to ``tenant_settings``.

    Unknown / unsafe values are normalised before saving. Returns the freshly
    loaded config so the caller can render the saved state immediately.
    """
    ch = _channel(channel)
    from ..db.repos import tenants_repo

    normalised = {
        "enabled": "1" if _truthy(values.get("enabled")) else "0",
        "mode": _mode(values.get("mode")),
        "send_url_template": str(values.get("send_url_template") or "").strip(),
        "http_method": _method(values.get("http_method")),
        "balance_url": str(values.get("balance_url") or "").strip(),
    }
    for field, value in normalised.items():
        tenants_repo.set_setting(int(tenant_id or 1), _settings_key(ch, field), value, by=by)
    return load_channel_config(tenant_id, ch)


def is_channel_active(config: dict[str, Any]) -> bool:
    """A channel can actually send only when enabled *and* it has a URL with a
    ``{phone}`` placeholder."""
    return bool(config.get("enabled")) and "{phone}" in (config.get("send_url_template") or "")


def build_send_url(template: str, *, phone: str, message: str) -> str:
    """URL-encode ``phone`` / ``message`` into the template placeholders.

    ``{phone}`` and ``{msg}`` are replaced; both values are percent-encoded so
    spaces, Arabic text and symbols travel safely in a query string.
    """
    encoded_phone = urllib.parse.quote(str(phone or ""), safe="")
    encoded_msg = urllib.parse.quote(str(message or ""), safe="")
    return (
        str(template or "")
        .replace("{phone}", encoded_phone)
        .replace("{msg}", encoded_msg)
        .replace("{message}", encoded_msg)  # tolerate {message} as an alias
    )


@dataclass(frozen=True)
class HttpSendOutcome:
    """Low-level result of one HTTP attempt (independent of delivery rows)."""

    ok: bool
    status_code: int = 0
    body_excerpt: str = ""
    error: str = ""
    final_url: str = ""


def http_send(
    *,
    template: str,
    method: str,
    phone: str,
    message: str,
    timeout: float = _HTTP_TIMEOUT_SECONDS,
) -> HttpSendOutcome:
    """Perform one HTTP send. Never raises — failures come back as ``ok=False``.

    A 2xx response is treated as success. Anything else (non-2xx, network
    error, bad URL) is a failure with a human-readable ``error``.
    """
    full_url = build_send_url(template, phone=phone, message=message)
    if not full_url or not full_url.lower().startswith(("http://", "https://")):
        return HttpSendOutcome(ok=False, error="رابط الإرسال غير صالح (يجب أن يبدأ بـ http/https).", final_url=full_url)
    # SEC H2 — the SMS gateway URL is tenant-supplied; block SSRF to internal /
    # metadata hosts (a public gateway never resolves to a private IP).
    from app.radius.core.ssrf_guard import SSRFBlocked, assert_public_url
    try:
        assert_public_url(full_url)
    except SSRFBlocked:
        return HttpSendOutcome(
            ok=False,
            error="رابط الإرسال يشير إلى عنوان داخليّ/غير عامّ — مرفوض لأسباب أمنيّة.",
            final_url=full_url)

    verb = _method(method)
    try:
        if verb == "POST":
            # For POST we still encode placeholders into the URL (gateways that
            # read query params), and additionally send the raw phone/msg as a
            # urlencoded body for gateways that expect form fields.
            body = urllib.parse.urlencode({"phone": phone, "msg": message, "message": message}).encode("utf-8")
            req = urllib.request.Request(full_url, data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            req = urllib.request.Request(full_url, method="GET")
        req.add_header("User-Agent", "HobeRadius-Comms/1.0")

        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — tenant-supplied URL, validated http(s)
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            raw = resp.read(_RESPONSE_EXCERPT_LIMIT + 1)
            excerpt = _decode_excerpt(raw)
            ok = 200 <= status < 300
            return HttpSendOutcome(
                ok=ok,
                status_code=status,
                body_excerpt=excerpt,
                error="" if ok else f"رد غير ناجح من المزود (HTTP {status}).",
                final_url=full_url,
            )
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        excerpt = ""
        try:
            excerpt = _decode_excerpt(exc.read(_RESPONSE_EXCERPT_LIMIT + 1))
        except Exception:  # noqa: BLE001
            excerpt = ""
        return HttpSendOutcome(
            ok=False,
            status_code=int(getattr(exc, "code", 0) or 0),
            body_excerpt=excerpt,
            error=f"رد خطأ من المزود (HTTP {getattr(exc, 'code', '?')}).",
            final_url=full_url,
        )
    except urllib.error.URLError as exc:
        return HttpSendOutcome(ok=False, error=f"تعذّر الاتصال بالمزود: {_reason(exc)}", final_url=full_url)
    except TimeoutError:
        return HttpSendOutcome(ok=False, error="انتهت مهلة الاتصال بالمزود.", final_url=full_url)
    except Exception as exc:  # noqa: BLE001 — providers must never raise
        return HttpSendOutcome(ok=False, error=f"خطأ غير متوقع أثناء الإرسال: {exc}", final_url=full_url)


def _reason(exc: urllib.error.URLError) -> str:
    reason = getattr(exc, "reason", None)
    return str(reason) if reason is not None else str(exc)


def _decode_excerpt(raw: bytes) -> str:
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        text = ""
    text = text.strip()
    if len(text) > _RESPONSE_EXCERPT_LIMIT:
        text = text[:_RESPONSE_EXCERPT_LIMIT] + "…"
    return text


def direct_send(tenant_id: int, channel: str, phone: str, message: str) -> tuple[bool, str]:
    """Send a message straight to a phone via the tenant's HTTP channel config,
    WITHOUT logging a ``message_deliveries`` row. NEVER raises.

    This is the unlogged sibling of the campaign path: it is the ONLY way a
    cleartext-credential body (subscriber/card username + password) may leave
    over WhatsApp — the body must never be persisted in the delivery log. Mirrors
    the direct branch of :func:`notifications_engine._send_http_channel`.

    Returns ``(ok, error_ar)``:
      * ``(False, "لا يوجد رقم هاتف للمستلم.")``   → no recipient number.
      * ``(False, "قناة … غير مهيأة للإرسال.")``    → channel off / no URL.
      * ``(True, "")`` / ``(False, <gateway error>)`` otherwise.
    """
    tid = int(tenant_id or 1)
    number = str(phone or "").strip()
    if not number:
        return False, "لا يوجد رقم هاتف للمستلم."
    try:
        ch = _channel(channel)
        cfg = load_channel_config(tid, ch)
        if not cfg.get("enabled") or "{phone}" not in (cfg.get("send_url_template") or ""):
            return False, f"قناة {ch} غير مهيأة للإرسال."
        out = http_send(
            template=cfg["send_url_template"],
            method=cfg.get("http_method") or DEFAULT_METHOD,
            # تطبيع الرقم بمفتاح الدولة (0599… → +970599…) قبل المزوّد
            phone=normalize_msisdn(number, tenant_dial_code(tid)),
            message=message,
        )
        return bool(out.ok), ("" if out.ok else (out.error or "فشل الإرسال."))
    except Exception as exc:  # noqa: BLE001 — providers must never raise
        return False, f"خطأ غير متوقع أثناء الإرسال: {exc}"


class GenericHttpProvider(NotificationProvider):
    """Sends a notification through a tenant-configured HTTP gateway.

    The provider is constructed per (tenant, channel). ``config`` is the dict
    returned by :func:`load_channel_config`. If the channel is disabled or has
    no usable URL the provider degrades gracefully to ``skipped`` (it keeps the
    queued-only behaviour rather than reporting a false failure).
    """

    provider_key = "generic_http"

    def __init__(self, *, channel: str, config: dict[str, Any], tenant_id: int = 1) -> None:
        self.channel = _channel(channel)
        self.config = config or {}
        # Needed by the Phase-4 quota gate to bill the right tenant's balance.
        self.tenant_id = int(tenant_id or 1)
        self.provider_config = ProviderConfig(provider_key=self.provider_key, channel=self.channel)

    def _phone(self, delivery: dict[str, Any], notification: dict[str, Any]) -> str:
        # The recipient address is stamped on the delivery row by the service.
        addr = (delivery or {}).get("recipient_address") or ""
        if not addr:
            meta = (notification or {}).get("metadata") or {}
            addr = str(meta.get("address") or "")
        return str(addr or "").strip()

    def send(self, *, delivery: dict[str, Any], notification: dict[str, Any]) -> ProviderResult:
        template = (self.config.get("send_url_template") or "").strip()
        mode = _mode(self.config.get("mode"))

        if not self.config.get("enabled"):
            return ProviderResult(
                status="skipped",
                provider_key=self.provider_key,
                error_message="القناة متوقفة — تم الاحتفاظ بالرسالة في الطابور فقط.",
                result={"external_send": False, "reason": "channel_disabled", "mode": mode},
            )
        if "{phone}" not in template:
            return ProviderResult(
                status="skipped",
                provider_key=self.provider_key,
                error_message="لم يتم ضبط رابط إرسال صالح لهذه القناة.",
                result={"external_send": False, "reason": "no_url", "mode": mode},
            )

        phone = self._phone(delivery, notification)
        if not phone:
            return ProviderResult(
                status="failed",
                provider_key=self.provider_key,
                error_message="لا يوجد رقم هاتف للمستلم.",
                result={"external_send": False, "reason": "no_recipient_phone", "mode": mode},
            )

        # ── تطبيع الرقم بمفتاح الدولة (إعداد comms.country_dial_code) ──
        # الرقم المحلي 0599... يصبح +970599... قبل أن يصل للمزوّد، حتى
        # تقبل بوابات SMS/واتساب الدولية الرقم بدون تدخل يدوي.
        phone = normalize_msisdn(phone, tenant_dial_code(self.tenant_id))

        message = str((notification or {}).get("body") or "")

        outcome = http_send(
            template=template,
            method=self.config.get("http_method") or DEFAULT_METHOD,
            phone=phone,
            message=message,
        )
        result_payload = {
            "external_send": True,
            "mode": mode,
            "channel": self.channel,
            "http_method": _method(self.config.get("http_method")),
            "http_status": outcome.status_code,
            "response_excerpt": outcome.body_excerpt,
        }
        if outcome.ok:
            return ProviderResult(
                status="sent",
                provider_key=self.provider_key,
                provider_message_id=_provider_message_id(outcome.body_excerpt),
                result=result_payload,
            )
        return ProviderResult(
            status="failed",
            provider_key=self.provider_key,
            error_message=outcome.error or "فشل الإرسال عبر المزود.",
            result=result_payload,
        )


def _provider_message_id(body_excerpt: str) -> str:
    """Best-effort extraction of a provider message id from a JSON response.

    Many gateways return ``{"id": "...", "message_id": "..."}``; we surface it
    when present so the delivery log can reference it. Falls back to empty.
    """
    text = (body_excerpt or "").strip()
    if not text.startswith("{"):
        return ""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    for key in ("message_id", "messageId", "id", "msg_id", "sid", "reference"):
        value = data.get(key)
        if value not in (None, ""):
            return str(value)[:120]
    return ""


def provider_for_channel(tenant_id: int, channel: str):
    """Pick the delivery provider for a channel, or ``None`` for channels that
    are not externally dispatchable (telegram/internal/email/push).

    SMS now dispatches through the per-tenant TweetSMS account (BYO provider) —
    the customer connects it on the SMS connection page. WhatsApp keeps the
    generic HTTP provider for backward compatibility.
    """
    ch = str(channel or "").strip().lower()
    if ch == "sms":
        # Imported lazily to avoid a circular import (tweetsms → comms_providers).
        from .tweetsms import TweetSmsProvider

        return TweetSmsProvider(tenant_id=int(tenant_id or 1))
    if ch not in HTTP_CHANNELS:
        return None
    config = load_channel_config(tenant_id, ch)
    return GenericHttpProvider(channel=ch, config=config, tenant_id=int(tenant_id or 1))


def channel_status(tenant_id: int, channel: str) -> dict[str, Any]:
    """Small status surface for the UI: enabled/active + mode + config."""
    config = load_channel_config(tenant_id, channel)
    return {
        "channel": config["channel"],
        "enabled": config["enabled"],
        "active": is_channel_active(config),
        "mode": config["mode"],
        "config": config,
    }
