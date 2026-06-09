"""WhatsApp self-service bot — Phase 2.

A *dead-simple* inbound bot that sits on top of the Phase-1 WhatsApp sender
(:mod:`app.radius.services.comms_providers`). A customer sends a WhatsApp
message to the operator's number; the gateway forwards it to our webhook; we

  1. read the sender phone + text (liberally, from many JSON/form shapes),
  2. find the subscriber by phone,
  3. match the text against the tenant's configured commands (keyword match,
     case/space-insensitive) — or fall back to the greeting / fallback reply,
  4. render the matched reply by substituting ``{username}`` / ``{balance}`` /
     ``{exp}`` … from the subscriber's record, and
  5. send the reply back through the *same* Phase-1 WhatsApp provider.

Design rules (mirrors Phase 1):
  * The webhook never raises and always answers 200 quickly — a flaky gateway
    or a malformed payload can never 500 the endpoint.
  * If the bot is disabled it is a silent no-op (still 200).
  * Configuration lives in ``tenant_settings`` under ``comms.bot.*`` — no new
    table, no migration. Sending reuses the WhatsApp channel config, so once
    Phase-1 WhatsApp is set up the bot works "with one button".

Config keys (``comms.bot.*``):
  * ``enabled``            "0"/"1"
  * ``greeting``           reply for "start"/"menu"/empty/greeting keywords
  * ``fallback``           reply for an unrecognised command
  * ``commands``           JSON list of ``{keyword, reply_template, enabled}``
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# The bot sends through the existing Phase-1 WhatsApp provider.
from . import comms_providers

BOT_CHANNEL = "whatsapp"

# ── tenant_settings keys ──────────────────────────────────────────────
_KEY_PREFIX = "comms.bot."


def _settings_key(field: str) -> str:
    return f"{_KEY_PREFIX}{field}"


# ── sensible Arabic defaults (so the bot works immediately) ───────────
# Greeting doubles as the "menu" — it lists what the customer can ask for.
DEFAULT_GREETING = (
    "👋 أهلًا بك في خدمة الاشتراك الذاتي.\n"
    "أرسل إحدى الكلمات التالية للحصول على معلوماتك:\n"
    "• معلومات الحساب\n"
    "• الرصيد\n"
    "• الباقة\n"
    "• تجديد"
)

DEFAULT_FALLBACK = (
    "لم أفهم طلبك 🤔\n"
    "أرسل «معلومات الحساب» أو «الرصيد» أو «الباقة» أو «تجديد».\n"
    "أرسل «القائمة» لعرض كل الخيارات."
)

# A few ready-to-use commands. Each reply uses Phase-2 variables that are
# substituted from the subscriber's record at send time.
DEFAULT_COMMANDS: list[dict[str, Any]] = [
    {
        "keyword": "معلومات الحساب",
        "reply_template": (
            "📄 معلومات حسابك:\n"
            "المستخدم: {username}\n"
            "الباقة: {prof}\n"
            "الحالة: {status}\n"
            "تاريخ الانتهاء: {exp}\n"
            "الرصيد: {balance}"
        ),
        "enabled": True,
    },
    {
        "keyword": "الرصيد",
        "reply_template": "💰 رصيدك الحالي: {balance}",
        "enabled": True,
    },
    {
        "keyword": "الباقة",
        "reply_template": (
            "📦 باقتك: {prof}\n"
            "السرعة: ↓ {down_speed} / ↑ {up_speed}\n"
            "تنتهي في: {exp}"
        ),
        "enabled": True,
    },
    {
        "keyword": "تجديد",
        "reply_template": (
            "🔄 لتجديد اشتراكك تواصل مع خدمة العملاء.\n"
            "باقتك الحالية: {prof}\n"
            "تنتهي في: {exp}\n"
            "رصيدك: {balance}"
        ),
        "enabled": True,
    },
]

# Built-in keywords that always show the greeting/menu, even if the operator
# never added a matching command. Kept tiny and bilingual on purpose.
GREETING_KEYWORDS = {
    "start", "/start", "menu", "hi", "hello",
    "القائمة", "ابدأ", "بدء", "مرحبا", "مرحباً", "السلام عليكم", "اهلا", "أهلا",
}


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in ("1", "true", "yes", "on")


# ── config dataclass ──────────────────────────────────────────────────
@dataclass
class BotConfig:
    enabled: bool = False
    greeting: str = DEFAULT_GREETING
    fallback: str = DEFAULT_FALLBACK
    commands: list[dict[str, Any]] = field(default_factory=lambda: [dict(c) for c in DEFAULT_COMMANDS])

    def active_commands(self) -> list[dict[str, Any]]:
        return [c for c in self.commands if _truthy(c.get("enabled", True)) and str(c.get("keyword") or "").strip()]


def _normalise(text: Any) -> str:
    """Lower-case + collapse whitespace so matching is forgiving.

    Arabic has no case, but we still strip tatweel (ـ) and squeeze internal
    spaces so "الـرصيد" / " الرصيد  " all match "الرصيد".
    """
    s = str(text if text is not None else "").strip().lower()
    s = s.replace("ـ", "")  # tatweel
    s = re.sub(r"\s+", " ", s)
    return s


def _coerce_commands(raw: Any) -> list[dict[str, Any]]:
    """Parse the stored commands JSON into a clean list of dicts."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError):
            raw = []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        keyword = str(item.get("keyword") or "").strip()
        reply = str(item.get("reply_template") or item.get("reply") or "").strip()
        if not keyword and not reply:
            continue
        out.append({
            "keyword": keyword,
            "reply_template": reply,
            "enabled": _truthy(item.get("enabled", True)),
        })
    return out


# ── load / save ───────────────────────────────────────────────────────
def load_bot_config(tenant_id: int) -> BotConfig:
    """Read the bot config from ``tenant_settings`` with safe defaults.

    First run (nothing saved yet) returns the sensible defaults so the
    settings page is pre-filled and the bot can work after a single toggle.
    """
    from ..db.repos import tenants_repo

    tid = int(tenant_id or 1)

    def _get(field: str, default: str = "") -> str:
        return tenants_repo.get_setting(tid, _settings_key(field), default)

    greeting = (_get("greeting", "") or "").strip() or DEFAULT_GREETING
    fallback = (_get("fallback", "") or "").strip() or DEFAULT_FALLBACK

    raw_commands = _get("commands", "")
    commands = _coerce_commands(raw_commands)
    if not commands and not (raw_commands or "").strip():
        # Never saved → ship the defaults so it works out of the box.
        commands = [dict(c) for c in DEFAULT_COMMANDS]

    return BotConfig(
        enabled=_truthy(_get("enabled", "0")),
        greeting=greeting,
        fallback=fallback,
        commands=commands,
    )


def save_bot_config(tenant_id: int, values: dict[str, Any], *, by: int = 0) -> BotConfig:
    """Persist the bot config to ``tenant_settings`` and return the fresh copy."""
    from ..db.repos import tenants_repo

    tid = int(tenant_id or 1)
    greeting = (str(values.get("greeting") or "").strip()) or DEFAULT_GREETING
    fallback = (str(values.get("fallback") or "").strip()) or DEFAULT_FALLBACK
    commands = _coerce_commands(values.get("commands"))

    tenants_repo.set_setting(tid, _settings_key("enabled"), "1" if _truthy(values.get("enabled")) else "0", by=by)
    tenants_repo.set_setting(tid, _settings_key("greeting"), greeting, by=by)
    tenants_repo.set_setting(tid, _settings_key("fallback"), fallback, by=by)
    tenants_repo.set_setting(
        tid,
        _settings_key("commands"),
        json.dumps(commands, ensure_ascii=False),
        by=by,
    )
    return load_bot_config(tid)


# ── subscriber lookup by phone ─────────────────────────────────────────
def _digits(phone: Any) -> str:
    """Keep only the digits of a phone number for tolerant matching.

    Gateways deliver the sender in many shapes — ``+962790000000``,
    ``962790000000``, ``00962790000000``, ``0790000000`` — so we compare on
    the trailing digits only (national part), which is robust to the country
    prefix being present or not.
    """
    return re.sub(r"\D+", "", str(phone if phone is not None else ""))


def find_subscriber_by_phone(tenant_id: int, phone: str):
    """Best-effort subscriber lookup by mobile number. Never raises.

    Matching is done on digits-only, comparing the last 9 digits so that a
    stored ``0790000000`` matches an inbound ``+962790000000``.
    """
    wanted = _digits(phone)
    if not wanted:
        return None
    tail = wanted[-9:]
    try:
        from ..db.repos import subscribers_repo

        # Pull a generous page of subscribers that share the trailing digits.
        rows = subscribers_repo.list_subscribers(int(tenant_id or 1), search=tail, limit=50)
    except Exception:  # noqa: BLE001 — lookup must never break the webhook
        return None

    for sub in rows:
        mob = _digits(getattr(sub, "mobile", "") or "")
        if not mob:
            continue
        if mob == wanted or mob[-9:] == tail or wanted[-9:] == mob[-9:]:
            return sub
    return None


# ── variable substitution ──────────────────────────────────────────────
def _fmt_dt(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value or "").strip()
    if not text:
        return ""
    # ISO "2026-05-31T07:49:08" → "2026-05-31"
    if "T" in text and len(text) >= 10:
        return text[:10]
    return text


def _plan_name(tenant_id: int, plan_id: Any) -> str:
    if not plan_id:
        return ""
    try:
        from ..db.repos import plans_repo

        plan = plans_repo.get_plan(int(tenant_id or 1), int(plan_id))
    except Exception:  # noqa: BLE001
        return ""
    return str(getattr(plan, "name", "") or "") if plan else ""


def _money(value: Any) -> str:
    """Format a balance using the tenant's currency symbol when available."""
    try:
        from ..core.system_config import format_money

        return str(format_money(value))
    except Exception:  # noqa: BLE001
        try:
            return f"{float(value or 0):.2f}"
        except (TypeError, ValueError):
            return str(value or "0")


def build_context(tenant_id: int, subscriber) -> dict[str, str]:
    """Build the substitution context from a subscriber record.

    Returns *string* values for every supported variable plus aliases, so a
    template referencing ``{exp}`` or ``{expiration}`` both work.
    """
    if subscriber is None:
        return {}

    tid = int(tenant_id or 1)
    username = str(getattr(subscriber, "username", "") or "")
    full_name = str(getattr(subscriber, "full_name", "") or "")
    plan = _plan_name(tid, getattr(subscriber, "plan_id", None))
    exp = _fmt_dt(getattr(subscriber, "expire_at", None))
    balance = _money(getattr(subscriber, "balance", 0))
    status = str(getattr(subscriber, "status", "") or "")
    down = getattr(subscriber, "download_speed_kbps", 0) or 0
    up = getattr(subscriber, "upload_speed_kbps", 0) or 0
    quota = getattr(subscriber, "combined_quota_mb", 0) or 0

    def _speed(kbps: Any) -> str:
        try:
            kbps = int(kbps or 0)
        except (TypeError, ValueError):
            return "—"
        if kbps <= 0:
            return "—"
        if kbps >= 1000:
            mbps = kbps / 1000
            return (f"{mbps:.0f}" if mbps.is_integer() else f"{mbps:.1f}") + " Mbps"
        return f"{kbps} Kbps"

    def _quota(mb: Any) -> str:
        try:
            mb = int(mb or 0)
        except (TypeError, ValueError):
            return "—"
        if mb <= 0:
            return "غير محدود"
        if mb >= 1024:
            gb = mb / 1024
            return (f"{gb:.0f}" if gb.is_integer() else f"{gb:.1f}") + " GB"
        return f"{mb} MB"

    ctx = {
        "username": username,
        "name": full_name or username,
        "full_name": full_name,
        "exp": exp,
        "expiration": exp,
        "expire": exp,
        "prof": plan,
        "plan": plan,
        "profile": plan,
        "balance": balance,
        "money": balance,
        "status": status,
        "down_speed": _speed(down),
        "up_speed": _speed(up),
        "remain_quota": _quota(quota),
        "quota": _quota(quota),
    }
    return ctx


# Match ``{var}`` (single braces, the format the screenshots/spec use).
_VAR_RE = re.compile(r"\{\s*([a-zA-Z0-9_]+)\s*\}")


def render_reply(template: str, context: dict[str, str]) -> str:
    """Substitute ``{var}`` placeholders. Unknown vars become empty strings."""
    def repl(match: "re.Match[str]") -> str:
        key = match.group(1).strip().lower()
        value = context.get(key)
        return "" if value is None else str(value)

    return _VAR_RE.sub(repl, str(template or ""))


# ── command matching ────────────────────────────────────────────────────
def match_command(config: BotConfig, text: str) -> dict[str, Any] | None:
    """Return the matching command dict, or ``None`` for greeting/fallback.

    Matching is case/space-insensitive and forgiving: an exact normalised
    match wins first; otherwise a command whose keyword is contained in the
    message (e.g. "اريد الرصيد" → "الرصيد") matches.
    """
    norm = _normalise(text)
    if not norm:
        return None
    commands = config.active_commands()

    # 1) exact normalised match
    for cmd in commands:
        if _normalise(cmd.get("keyword")) == norm:
            return cmd
    # 2) keyword contained in the message (longest keyword first, so a more
    #    specific command beats a shorter one).
    for cmd in sorted(commands, key=lambda c: len(_normalise(c.get("keyword"))), reverse=True):
        kw = _normalise(cmd.get("keyword"))
        if kw and kw in norm:
            return cmd
    return None


# ── inbound payload parsing ──────────────────────────────────────────────
# Common field names different gateways use for sender / message.
_PHONE_FIELDS = ("phone", "from", "sender", "msisdn", "wa_id", "waid", "mobile", "number", "chatId", "chat_id")
_TEXT_FIELDS = ("msg", "message", "text", "body", "content", "caption", "question")


def _dig(payload: Any, names: tuple[str, ...]) -> str:
    """Find the first non-empty value among ``names`` anywhere in the payload.

    Searches the top-level mapping first, then recurses one level into nested
    dicts/lists (covers shapes like ``{"messages":[{"from": ..., "text":
    {"body": ...}}]}``). Always returns a plain string.
    """
    def _scan(obj: Any, depth: int) -> str:
        if depth < 0:
            return ""
        if isinstance(obj, dict):
            for name in names:
                if name in obj and _scalar(obj[name]):
                    return _scalar(obj[name])
            # `text` is often a nested object {"body": "..."} on WhatsApp Cloud.
            for value in obj.values():
                found = _scan(value, depth - 1)
                if found:
                    return found
        elif isinstance(obj, list):
            for value in obj:
                found = _scan(value, depth - 1)
                if found:
                    return found
        return ""

    return _scan(payload, 3)


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    return ""


def parse_inbound(*, json_body: Any, form: Any, args: Any) -> tuple[str, str]:
    """Extract ``(phone, text)`` from the request, liberally.

    Reads JSON body first (most gateways), then form fields, then query args.
    Never raises — missing values come back as empty strings.
    """
    sources: list[Any] = []
    if isinstance(json_body, (dict, list)):
        sources.append(json_body)
    # form / args are Werkzeug MultiDicts → treat as plain dicts.
    for md in (form, args):
        try:
            if md:
                sources.append({k: md.get(k) for k in md.keys()})
        except Exception:  # noqa: BLE001
            continue

    phone = ""
    text = ""
    for src in sources:
        phone = phone or _dig(src, _PHONE_FIELDS)
        text = text or _dig(src, _TEXT_FIELDS)
        if phone and text:
            break
    return phone.strip(), text.strip()


# ── the dispatcher ───────────────────────────────────────────────────────
@dataclass(frozen=True)
class BotReply:
    """Outcome of handling one inbound message (for tests / logging)."""

    handled: bool
    reason: str = ""
    phone: str = ""
    matched_keyword: str = ""
    reply_text: str = ""
    sent: bool = False
    send_error: str = ""


_GREETING_NORM = {_normalise(k) for k in GREETING_KEYWORDS}


def decide_reply(config: BotConfig, *, context: dict[str, str], text: str) -> tuple[str, str, str]:
    """Pure function: pick the reply text + reason + matched keyword.

    Returns ``(reply_text, reason, matched_keyword)`` where reason is one of
    ``greeting`` / ``command`` / ``fallback``. ``context`` is the already-built
    substitution dict (see :func:`build_context`).
    """
    norm = _normalise(text)
    if not norm or norm in _GREETING_NORM:
        return config.greeting, "greeting", ""

    cmd = match_command(config, text)
    if cmd is None:
        return config.fallback, "fallback", ""

    reply = render_reply(cmd.get("reply_template") or "", context)
    return reply, "command", str(cmd.get("keyword") or "")


def handle_inbound(tenant_id: int, *, phone: str, text: str) -> BotReply:
    """Core bot logic: match → render → send. Never raises.

    1. If the bot is disabled → no-op (``handled=False, reason='disabled'``).
    2. Look up the subscriber by phone (None is tolerated — variables just
       render empty, greeting/fallback still send).
    3. Decide the reply (greeting / command / fallback).
    4. Send it back through the Phase-1 WhatsApp provider.
    """
    tid = int(tenant_id or 1)
    try:
        config = load_bot_config(tid)
    except Exception:  # noqa: BLE001
        return BotReply(handled=False, reason="config_error", phone=phone)

    if not config.enabled:
        return BotReply(handled=False, reason="disabled", phone=phone)

    clean_phone = (phone or "").strip()
    if not clean_phone:
        return BotReply(handled=False, reason="no_phone", phone="")

    subscriber = find_subscriber_by_phone(tid, clean_phone)
    context = build_context(tid, subscriber)
    reply_text, reason, matched_keyword = decide_reply(config, context=context, text=text)

    sent, send_error = _send_whatsapp(tid, clean_phone, reply_text)
    return BotReply(
        handled=True,
        reason=reason,
        phone=clean_phone,
        matched_keyword=matched_keyword,
        reply_text=reply_text,
        sent=sent,
        send_error=send_error,
    )


def _send_whatsapp(tenant_id: int, phone: str, message: str) -> tuple[bool, str]:
    """Send ``message`` to ``phone`` via the Phase-1 WhatsApp provider.

    Never raises. Returns ``(ok, error)``. If WhatsApp isn't configured the
    provider's ``http_send`` reports an invalid-URL failure (no network hit).
    """
    if not (message or "").strip():
        return False, "رد فارغ — لم يتم الإرسال."
    try:
        cfg = comms_providers.load_channel_config(tenant_id, BOT_CHANNEL)
        if not cfg.get("enabled") or "{phone}" not in (cfg.get("send_url_template") or ""):
            return False, "قناة واتساب غير مهيأة للإرسال."
        outcome = comms_providers.http_send(
            template=cfg["send_url_template"],
            method=cfg.get("http_method") or comms_providers.DEFAULT_METHOD,
            # تطبيع الرقم بمفتاح الدولة (0599... → +970599...) قبل المزوّد
            phone=comms_providers.normalize_msisdn(
                phone, comms_providers.tenant_dial_code(tenant_id)
            ),
            message=message,
        )
        return bool(outcome.ok), ("" if outcome.ok else (outcome.error or "فشل الإرسال."))
    except Exception as exc:  # noqa: BLE001 — sending must never break the webhook
        return False, f"خطأ غير متوقع أثناء الإرسال: {exc}"
