"""TweetSMS provider adapter (tweetsms.ps legacy HTTP API).

SMS is a FREE bring-your-own-provider service: each customer (tenant) connects
their OWN TweetSMS account (api_key OR username+password + approved sender) on
the SMS connection page, and buys credit from TweetSMS directly. This module is
the single clean adapter that turns a per-tenant config into real sends.

The legacy ``api.php`` contract (plain-text):

  Send     GET …/api.php?comm=sendsms&api_key=<KEY>&to=<MOBILE>&message=<TEXT>&sender=<NAME>
           (or &user=<USER>&pass=<PASS> instead of api_key)
  Balance  GET …/api.php?comm=chk_balance&api_key=<KEY>   (or &user=&pass=)

  Response per number:  ``Result:SMS_ID:mobileNumber``  (one line each)
    Result 1   → success (SMS_ID is the server id)
    Result -2  → invalid destination / unsupported country
    Result -999→ failed by provider
    Result u   → unknown status
  Request-level error codes (returned bare):
    -100 missing params · -110 bad credentials · -113 not enough balance
    -115 sender not available · -116 invalid sender name

Design rules honoured here:
  * EVERY public entry point is defensive — it NEVER raises into the caller; a
    misconfigured account or a dead network comes back as a ``failed`` result
    with an Arabic message, so the notification pipeline can't break.
  * The (possibly Arabic) message body is URL-encoded.
  * Secrets are never logged.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .comms_providers import normalize_msisdn, tenant_dial_code
from .notification_campaigns import NotificationProvider, ProviderConfig, ProviderResult

BASE_URL = "https://www.tweetsms.ps/api.php"
COMM_SEND = "sendsms"
COMM_BALANCE = "chk_balance"

PROVIDER_KEY = "tweetsms"

_TIMEOUT_SECONDS = 12.0
_RESPONSE_EXCERPT_LIMIT = 400

# ── خريطة الأكواد → رسالة عربية واضحة للمستخدم ──────────────────────────────
# success (1) و حالات لكل رقم (-2/-999/u) + أخطاء على مستوى الطلب (-100…-116).
_ARABIC_ERRORS: dict[str, str] = {
    "1": "تم الإرسال بنجاح",
    "-2": "رقم غير صالح أو دولة غير مدعومة",
    "-999": "فشل لدى المزوّد",
    "u": "حالة غير معروفة من المزوّد",
    "-100": "بيانات ناقصة في الطلب",
    "-110": "بيانات الدخول خاطئة (مفتاح API أو اسم المستخدم/كلمة المرور)",
    "-113": "الرصيد غير كافٍ",
    "-115": "اسم المرسل غير متاح",
    "-116": "اسم المرسل غير صالح",
}

# أكواد تعني فشلًا على مستوى الطلب كلّه (مصادقة/رصيد/مرسِل) لا رقمًا بعينه.
_REQUEST_LEVEL_ERRORS = {"-100", "-110", "-113", "-115", "-116"}
# الكود الوحيد الذي يعني نجاحًا.
_SUCCESS_CODE = "1"


def arabic_for_code(code: str) -> str:
    """Map a TweetSMS result/error code to a clear Arabic message. Unknown
    codes fall back to a generic provider-failure message (never empty)."""
    return _ARABIC_ERRORS.get(str(code or "").strip(), "فشل غير معروف لدى المزوّد")


def is_success_code(code: str) -> bool:
    return str(code or "").strip() == _SUCCESS_CODE


def is_request_level_error(code: str) -> bool:
    return str(code or "").strip() in _REQUEST_LEVEL_ERRORS


# ── تطبيع الرقم لصيغة TweetSMS الدولية (9725XXXXXXXX بلا + ولا 00) ───────────
def normalize_recipient(phone: str, dial_code: str = "") -> str:
    """Normalise a phone to TweetSMS's international form (digits only, country
    code first, NO leading ``+`` / ``00``). Reuses the shared msisdn normaliser
    (0599…→+970599…) then strips the ``+`` / ``00`` prefix."""
    intl = normalize_msisdn(phone, dial_code)
    intl = (intl or "").strip()
    if intl.startswith("+"):
        intl = intl[1:]
    elif intl.startswith("00"):
        intl = intl[2:]
    return intl


def _auth_params(*, api_key: str = "", username: str = "", password: str = "") -> dict[str, str]:
    """Pick the auth params: api_key wins; else username+password. Returns an
    empty dict when nothing usable is supplied (caller treats as «not set»)."""
    api_key = (api_key or "").strip()
    if api_key:
        return {"api_key": api_key}
    username = (username or "").strip()
    password = (password or "").strip()
    if username and password:
        return {"user": username, "pass": password}
    return {}


def _build_url(comm: str, params: dict[str, str]) -> str:
    """Build a fully URL-encoded api.php URL. ``urlencode`` percent-encodes the
    (possibly Arabic) values via ``quote_via=quote`` so UTF-8 travels safely."""
    query = {"comm": comm, **{k: v for k, v in params.items() if v != ""}}
    return BASE_URL + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def build_send_url(
    *,
    to: str,
    message: str,
    sender: str,
    api_key: str = "",
    username: str = "",
    password: str = "",
) -> str:
    """Build the ``comm=sendsms`` URL (api_key or user/pass variant). ``to`` may
    be a single number or a comma-joined list."""
    auth = _auth_params(api_key=api_key, username=username, password=password)
    return _build_url(COMM_SEND, {**auth, "to": str(to or ""), "message": str(message or ""), "sender": str(sender or "")})


def build_balance_url(*, api_key: str = "", username: str = "", password: str = "") -> str:
    """Build the ``comm=chk_balance`` URL (api_key or user/pass variant)."""
    auth = _auth_params(api_key=api_key, username=username, password=password)
    return _build_url(COMM_BALANCE, auth)


def _http_get(url: str, timeout: float = _TIMEOUT_SECONDS) -> tuple[bool, int, str, str]:
    """Perform one GET. Never raises. Returns (ok, status, text, error_ar)."""
    if not url.lower().startswith("https://"):
        return False, 0, "", "رابط المزوّد غير صالح."
    try:
        req = urllib.request.Request(url, method="GET")
        req.add_header("User-Agent", "HobeRadius-SMS/1.0")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https host
            status = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            raw = resp.read(4096)
            text = raw.decode("utf-8", errors="replace").strip()
            return (200 <= status < 300), status, text, ("" if 200 <= status < 300 else f"رد غير ناجح من المزوّد (HTTP {status}).")
    except urllib.error.HTTPError as exc:
        return False, int(getattr(exc, "code", 0) or 0), "", f"رد خطأ من المزوّد (HTTP {getattr(exc, 'code', '?')})."
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        return False, 0, "", f"تعذّر الاتصال بالمزوّد: {reason if reason is not None else exc}"
    except TimeoutError:
        return False, 0, "", "انتهت مهلة الاتصال بالمزوّد."
    except Exception as exc:  # noqa: BLE001 — adapter must never raise
        return False, 0, "", f"خطأ غير متوقع أثناء الاتصال بالمزوّد: {exc}"


def parse_send_response(text: str, recipients: list[str] | None = None) -> list[dict[str, Any]]:
    """Parse a ``sendsms`` plain-text response into per-number result dicts.

    Each provider line is ``Result:SMS_ID:mobileNumber``. A bare request-level
    error code (e.g. ``-110``) with no colons applies to ALL recipients. Returns
    a list of ``{to, code, sms_id, ok, message_ar}``.
    """
    recipients = [str(r).strip() for r in (recipients or []) if str(r).strip()]
    lines = [ln.strip() for ln in str(text or "").splitlines() if ln.strip()]

    # حالة الخطأ على مستوى الطلب: سطر واحد كود مجرّد (بلا ":") ينطبق على الجميع.
    if len(lines) == 1 and ":" not in lines[0]:
        code = lines[0]
        targets = recipients or [""]
        return [
            {"to": to, "code": code, "sms_id": "", "ok": is_success_code(code), "message_ar": arabic_for_code(code)}
            for to in targets
        ]

    results: list[dict[str, Any]] = []
    for ln in lines:
        parts = ln.split(":")
        code = parts[0].strip() if parts else ""
        sms_id = parts[1].strip() if len(parts) > 1 else ""
        mobile = parts[2].strip() if len(parts) > 2 else ""
        results.append(
            {
                "to": mobile,
                "code": code,
                "sms_id": sms_id if is_success_code(code) else "",
                "ok": is_success_code(code),
                "message_ar": arabic_for_code(code),
            }
        )
    # لو لم يُرجِع المزوّد شيئًا مفهومًا، اعتبره فشلًا واحدًا للمستلمين.
    if not results:
        targets = recipients or [""]
        return [
            {"to": to, "code": "u", "sms_id": "", "ok": False, "message_ar": arabic_for_code("u")}
            for to in targets
        ]
    return results


def parse_balance_response(text: str) -> dict[str, Any]:
    """Parse a ``chk_balance`` response. Success is a numeric balance; an error
    code maps to its Arabic message."""
    raw = str(text or "").strip()
    first = raw.splitlines()[0].strip() if raw else ""
    # كود خطأ معروف؟
    if first in _ARABIC_ERRORS and not is_success_code(first):
        return {"ok": False, "balance": None, "error_ar": arabic_for_code(first), "raw": raw[:_RESPONSE_EXCERPT_LIMIT]}
    # رصيد رقمي؟
    cleaned = first.replace(",", "").strip()
    try:
        balance = float(cleaned)
    except (TypeError, ValueError):
        return {"ok": False, "balance": None, "error_ar": "تعذّر قراءة الرصيد من ردّ المزوّد.", "raw": raw[:_RESPONSE_EXCERPT_LIMIT]}
    return {"ok": True, "balance": balance, "error_ar": "", "raw": raw[:_RESPONSE_EXCERPT_LIMIT]}


# ── العمليات على مستوى المستأجر (تقرأ الاعتماد المشفّر من الريبو) ────────────
def _load_cfg(tenant_id: int) -> dict[str, Any] | None:
    from ..db.repos import tenant_sms_settings_repo

    return tenant_sms_settings_repo.get(int(tenant_id or 1))


def is_connected(tenant_id: int) -> bool:
    """True when the tenant has an enabled TweetSMS config with creds + sender."""
    from ..db.repos import tenant_sms_settings_repo

    return tenant_sms_settings_repo.is_configured(int(tenant_id or 1))


def send_sms(tenant_id: int, to: str | list[str], message: str) -> dict[str, Any]:
    """Send an SMS to one or more recipients via the tenant's TweetSMS account.

    Never raises. Returns ``{ok, results, error_ar, raw, sent_count}``. ``ok`` is
    True when the request reached the provider with no request-level error AND at
    least one recipient succeeded.
    """
    cfg = _load_cfg(tenant_id)
    from ..db.repos import tenant_sms_settings_repo

    if not cfg or not cfg.get("enabled"):
        return {"ok": False, "results": [], "error_ar": "قناة SMS غير مُفعّلة. اربط حساب TweetSMS أولًا.", "raw": "", "sent_count": 0}
    if not tenant_sms_settings_repo.has_credentials(cfg):
        return {"ok": False, "results": [], "error_ar": "لم يتم ضبط بيانات الدخول (مفتاح API أو اسم المستخدم/كلمة المرور).", "raw": "", "sent_count": 0}
    sender = (cfg.get("sender") or "").strip()
    if not sender:
        return {"ok": False, "results": [], "error_ar": "اسم المرسل غير مضبوط.", "raw": "", "sent_count": 0}

    dial = tenant_dial_code(int(tenant_id or 1))
    raw_list = to if isinstance(to, (list, tuple)) else [to]
    recipients = [r for r in (normalize_recipient(x, dial) for x in raw_list) if r]
    if not recipients:
        return {"ok": False, "results": [], "error_ar": "لا يوجد رقم هاتف صالح للمستلم.", "raw": "", "sent_count": 0}

    url = build_send_url(
        to=",".join(recipients),
        message=str(message or ""),
        sender=sender,
        api_key=cfg.get("api_key") or "",
        username=cfg.get("username") or "",
        password=cfg.get("password") or "",
    )
    ok_http, _status, text, http_err = _http_get(url)
    if not ok_http:
        return {"ok": False, "results": [], "error_ar": http_err or "فشل الاتصال بالمزوّد.", "raw": "", "sent_count": 0}

    results = parse_send_response(text, recipients)
    # خطأ على مستوى الطلب (مصادقة/رصيد/مرسِل) ينطبق على كل المستلمين بكود واحد.
    request_err = ""
    if results and all(is_request_level_error(r["code"]) for r in results):
        request_err = results[0]["message_ar"]
    sent_count = sum(1 for r in results if r["ok"])
    return {
        "ok": bool(sent_count > 0 and not request_err),
        "results": results,
        "error_ar": request_err,
        "raw": str(text or "")[:_RESPONSE_EXCERPT_LIMIT],
        "sent_count": sent_count,
    }


def check_balance(tenant_id: int) -> dict[str, Any]:
    """Query the tenant's TweetSMS balance. Never raises."""
    cfg = _load_cfg(tenant_id)
    from ..db.repos import tenant_sms_settings_repo

    if not cfg or not tenant_sms_settings_repo.has_credentials(cfg):
        return {"ok": False, "balance": None, "error_ar": "لم يتم ضبط بيانات الدخول لحساب TweetSMS.", "raw": ""}

    url = build_balance_url(
        api_key=cfg.get("api_key") or "",
        username=cfg.get("username") or "",
        password=cfg.get("password") or "",
    )
    ok_http, _status, text, http_err = _http_get(url)
    if not ok_http:
        return {"ok": False, "balance": None, "error_ar": http_err or "فشل الاتصال بالمزوّد.", "raw": ""}
    return parse_balance_response(text)


def _mask(value: str) -> str:
    """Mask a secret for display: keep the last 4 chars, dot out the rest."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]


def connection_status(tenant_id: int) -> dict[str, Any]:
    """Non-secret status surface for the connection page: connected flag, auth
    mode, sender, MASKED credentials, last-update timestamp."""
    cfg = _load_cfg(tenant_id) or {}
    has_key = bool((cfg.get("api_key") or "").strip())
    has_userpass = bool((cfg.get("username") or "").strip() and (cfg.get("password") or "").strip())
    from ..db.repos import tenant_sms_settings_repo

    return {
        "provider": cfg.get("provider") or "tweetsms",
        "enabled": bool(cfg.get("enabled")),
        "connected": tenant_sms_settings_repo.is_configured(int(tenant_id or 1)),
        "has_credentials": has_key or has_userpass,
        "auth_mode": "api_key" if has_key else ("user_pass" if has_userpass else "none"),
        "api_key_masked": _mask(cfg.get("api_key") or ""),
        "username": cfg.get("username") or "",
        "password_set": bool((cfg.get("password") or "").strip()),
        "sender": cfg.get("sender") or "",
        "updated_at": cfg.get("updated_at") or "",
    }


# ── مزوّد التسليم: يجعل قناة SMS تُرسل عبر TweetSMS لكل مستأجر ────────────────
class TweetSmsProvider(NotificationProvider):
    """Notification provider that dispatches the ``sms`` channel through the
    tenant's TweetSMS account. Degrades to ``skipped`` (not ``failed``) when the
    tenant hasn't connected TweetSMS yet, mirroring the generic provider."""

    provider_key = PROVIDER_KEY

    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.provider_config = ProviderConfig(provider_key=self.provider_key, channel="sms")

    def _phone(self, delivery: dict[str, Any], notification: dict[str, Any]) -> str:
        addr = (delivery or {}).get("recipient_address") or ""
        if not addr:
            meta = (notification or {}).get("metadata") or {}
            addr = str(meta.get("address") or "")
        return str(addr or "").strip()

    def send(self, *, delivery: dict[str, Any], notification: dict[str, Any]) -> ProviderResult:
        if not is_connected(self.tenant_id):
            return ProviderResult(
                status="skipped",
                provider_key=self.provider_key,
                error_message="لم يتم ربط حساب TweetSMS — تم الاحتفاظ بالرسالة في الطابور فقط.",
                result={"external_send": False, "reason": "not_connected", "channel": "sms"},
            )
        phone = self._phone(delivery, notification)
        if not phone:
            return ProviderResult(
                status="failed",
                provider_key=self.provider_key,
                error_message="لا يوجد رقم هاتف للمستلم.",
                result={"external_send": False, "reason": "no_recipient_phone", "channel": "sms"},
            )
        message = str((notification or {}).get("body") or "")
        outcome = send_sms(self.tenant_id, phone, message)
        first = (outcome.get("results") or [{}])[0]
        result_payload = {
            "external_send": True,
            "channel": "sms",
            "provider": PROVIDER_KEY,
            "response_excerpt": outcome.get("raw") or "",
            "code": first.get("code") or "",
        }
        if outcome.get("ok"):
            return ProviderResult(
                status="sent",
                provider_key=self.provider_key,
                provider_message_id=str(first.get("sms_id") or ""),
                result=result_payload,
            )
        return ProviderResult(
            status="failed",
            provider_key=self.provider_key,
            error_message=outcome.get("error_ar") or first.get("message_ar") or "فشل الإرسال عبر TweetSMS.",
            result=result_payload,
        )
