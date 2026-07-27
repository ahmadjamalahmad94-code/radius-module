"""Event-driven notifications engine — Phase 3 (the heart).

A single, dead-simple layer that turns *business events* (a subscriber was
created, a balance was withdrawn, a router went down…) into outgoing messages
on the channels the operator chose — SMS / WhatsApp / Telegram — using a
ready-to-use Arabic template per event. The whole thing is built to work
"بكبسة زر": every event ships with sensible defaults, so the settings page is
pre-populated and notifications fire the moment a channel is configured.

Design (mirrors Phases 1 & 2 — *reuse, never rebuild*):
  * Per-event config lives in ``tenant_settings`` under
    ``notif.<event>.{enabled,channels,template,days_before}`` — no migration.
  * The substitution context is the *same* one the WhatsApp bot uses
    (:func:`app.radius.services.comms_bot.build_context`) plus a few
    event-specific extras ({price},{amount},{days},{prof}/{old_prof},
    {exp},{balance},{percent},{device}…). Templates use ``{var}`` single
    braces, exactly like the bot.
  * Dispatch reuses the Phase-1 HTTP provider for sms/whatsapp
    (:func:`app.radius.services.comms_providers.http_send`, with a
    delivery-log row via :class:`NotificationCampaignService.queue_notification`
    when we have a real subscriber recipient) and the Phase-2 Telegram sender
    (:func:`app.radius.services.telegram_notifier.send_to_tenant`).

Golden rule: :func:`notify_event` NEVER raises. A disabled rule, a missing
subscriber, an unconfigured channel or a flaky gateway can never break the
business flow that fired the event — every failure is swallowed and logged.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

_LOG = logging.getLogger(__name__)

# Channels an event can fan out to. sms/whatsapp go through the Phase-1 HTTP
# provider; telegram goes through the Phase-2 tenant Telegram sender.
NOTIF_CHANNELS = ("sms", "whatsapp", "telegram")

_KEY_PREFIX = "notif."


def _settings_key(event_key: str, field_name: str) -> str:
    return f"{_KEY_PREFIX}{event_key}.{field_name}"


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in ("1", "true", "yes", "on")


# ── event registry ────────────────────────────────────────────────────
@dataclass(frozen=True)
class EventDef:
    """A notifiable business event + its out-of-the-box defaults."""

    key: str
    label: str                       # Arabic label shown on the settings card
    template: str                    # default Arabic message body ({var} substitution)
    channels: tuple[str, ...]        # default channels enabled
    group: str = "subscribers"       # ui grouping: subscribers / billing / network
    has_days_before: bool = False    # only near_expiry exposes days_before
    extra_vars: tuple[str, ...] = ()  # event-specific variables (for the UI chips)
    default_enabled: bool = False    # whether on by default (most start OFF)
    sends_credentials: bool = False  # sms/whatsapp channels send username+password (creds)
    sends_card_credentials: bool = False  # sms/whatsapp channels send purchased card login(s)


# The ordered registry. Labels + templates are deliberately friendly and ready
# to send as-is. Defaults are conservative (most OFF) so nothing is sent until
# the operator opts in, except the universally-wanted lifecycle ones.
_EVENTS: tuple[EventDef, ...] = (
    # ── subscriber lifecycle ──────────────────────────────────────────
    EventDef(
        key="subscriber_created",
        label="إنشاء مشترك جديد",
        template="مرحبًا {name} 👋\nتم إنشاء حسابك بنجاح.\nاسم المستخدم: {username}\nالباقة: {prof}",
        channels=("sms", "whatsapp"),
        group="subscribers",
        default_enabled=False,
        # Credentials-bearing: BOTH the SMS and the WhatsApp channels send the
        # new subscriber their login (username + password) — SMS in a short,
        # 60-char-aware body; WhatsApp in a friendlier multi-line one. The
        # password rides ONLY those two direct-to-phone sends (never Telegram,
        # which keeps the password-free ``template`` above, nor the delivery
        # log). Each channel is independently toggled from the settings page.
        # See :mod:`app.radius.services.subscriber_credentials`.
        sends_credentials=True,
    ),
    EventDef(
        key="subscriber_activated",
        label="تفعيل الاشتراك",
        # SMS-bearing → قالب قصير (≤60 حرفًا) للحفاظ على رسالة SMS واحدة.
        template="تم تفعيل اشتراكك. ينتهي بتاريخ {exp}.",
        channels=("telegram", "sms", "whatsapp"),
        group="subscribers",
        default_enabled=True,
    ),
    EventDef(
        key="near_expiry",
        label="تنبيه قرب الانتهاء (التحصيل)",
        template="اشتراكك ينتهي خلال {days} يوم ({exp}). جدّد الآن.",
        channels=("telegram", "sms", "whatsapp"),
        group="subscribers",
        has_days_before=True,
        extra_vars=("days",),
        default_enabled=True,
    ),
    EventDef(
        key="subscriber_expired",
        label="انتهاء الاشتراك",
        template="انتهى اشتراكك. جدّد الآن لاستعادة الخدمة.",
        channels=("telegram", "sms", "whatsapp"),
        group="subscribers",
        default_enabled=True,
    ),
    EventDef(
        key="subscriber_renewed",
        label="تجديد الاشتراك",
        template="تم تجديد اشتراكك. ينتهي بتاريخ {exp}.",
        channels=("telegram", "sms", "whatsapp"),
        group="subscribers",
        default_enabled=False,
    ),
    EventDef(
        key="plan_changed",
        label="تغيير الباقة",
        template="تم تغيير باقتك 🔄\nمن: {old_prof}\nإلى: {prof}\nتاريخ الانتهاء: {exp}",
        channels=("telegram", "whatsapp"),
        group="subscribers",
        extra_vars=("old_prof",),
        default_enabled=False,
    ),
    EventDef(
        key="subscriber_disabled",
        label="إيقاف/تعليق الخدمة",
        template="تم إيقاف خدمتك مؤقتًا. للاستفسار تواصل مع الدعم.",
        channels=("telegram", "sms", "whatsapp"),
        group="subscribers",
        default_enabled=False,
    ),
    EventDef(
        key="subscriber_reactivated",
        label="إعادة تفعيل الخدمة",
        template="تمت إعادة تفعيل خدمتك. أهلًا بعودتك {name}.",
        channels=("telegram", "sms", "whatsapp"),
        group="subscribers",
        default_enabled=False,
    ),
    EventDef(
        key="quota_threshold",
        label="بلوغ حد الاستهلاك",
        template="تنبيه 📊\nوصل استهلاك «{username}» إلى {percent}% من الباقة.\nالمتبقّي: {remain_quota}",
        channels=("whatsapp",),
        group="subscribers",
        extra_vars=("percent",),
        default_enabled=False,
    ),
    EventDef(
        key="login_new_device",
        label="دخول من جهاز جديد",
        template="تنبيه أمان 🔐\nتم تسجيل دخول حساب «{username}» من جهاز جديد.\nالجهاز: {device}",
        channels=("whatsapp",),
        group="subscribers",
        extra_vars=("device",),
        default_enabled=False,
    ),
    # ── billing / wallet ──────────────────────────────────────────────
    EventDef(
        key="recharge_added",
        label="إضافة شحن",
        template="تم شحن {amount}. رصيدك الحالي: {balance}.",
        channels=("sms", "whatsapp"),
        group="billing",
        extra_vars=("amount",),
        default_enabled=False,
    ),
    EventDef(
        key="balance_deposit",
        label="إيداع رصيد",
        template="تم إيداع {amount} في محفظتك. رصيدك: {balance}.",
        channels=("sms", "whatsapp"),
        group="billing",
        extra_vars=("amount",),
        default_enabled=False,
    ),
    EventDef(
        key="balance_withdraw",
        label="سحب رصيد",
        template="تم سحب {amount} من محفظتك. رصيدك: {balance}.",
        channels=("sms", "whatsapp"),
        group="billing",
        extra_vars=("amount",),
        default_enabled=False,
    ),
    EventDef(
        key="payment_received",
        label="استلام دفعة",
        template="تم استلام دفعتك ({amount}). شكرًا لك.",
        channels=("telegram", "sms", "whatsapp"),
        group="billing",
        extra_vars=("amount",),
        default_enabled=False,
    ),
    # ── e-card store movements (buyer-facing, via the store wallet) ────
    # The recipient is a marketplace card_user (mobile on file, no Telegram
    # chat) — so SMS/WhatsApp carry the message; SMS rides the tenant's
    # connected TweetSMS (60-char aware). See
    # :mod:`app.radius.services.store_movement_notifications`.
    EventDef(
        key="store_balance_recharge",
        label="شحن رصيد المتجر",
        template="تم شحن محفظتك بمبلغ {amount}. رصيدك الحالي: {balance}.",
        channels=("sms", "whatsapp"),
        group="store",
        extra_vars=("amount", "balance"),
        default_enabled=False,
    ),
    EventDef(
        key="store_balance_withdraw",
        label="سحب رصيد المتجر",
        template="تم سحب {amount} من محفظتك. رصيدك الحالي: {balance}.",
        channels=("sms", "whatsapp"),
        group="store",
        extra_vars=("amount", "balance"),
        default_enabled=False,
    ),
    EventDef(
        key="store_cards_purchased",
        label="شراء بطاقات من المتجر",
        # Telegram gets this password-free body; the SMS and WhatsApp channels
        # each send the purchased card login(s) instead (sends_card_credentials
        # below) — direct to the buyer's number, never into the delivery log.
        template="تم شراء {count} بطاقة بمبلغ {amount}. التفاصيل على رقمك.",
        channels=("sms", "whatsapp"),
        group="store",
        extra_vars=("count", "amount"),
        default_enabled=False,
        sends_card_credentials=True,
    ),
    # ── network / infrastructure (operator-facing, via Telegram) ───────
    EventDef(
        key="router_down",
        label="انقطاع راوتر/جهاز",
        template="🚨 انقطع الاتصال مع «{device}»\nالعنوان: {ip}\nالوقت: {time}",
        channels=("telegram",),
        group="network",
        extra_vars=("device", "ip", "time"),
        default_enabled=True,
    ),
    EventDef(
        key="router_up",
        label="عودة راوتر/جهاز",
        template="✅ عاد الاتصال مع «{device}»\nالعنوان: {ip}\nالبنج: {latency}",
        channels=("telegram",),
        group="network",
        extra_vars=("device", "ip", "latency"),
        default_enabled=True,
    ),
    EventDef(
        key="network_high_latency",
        label="ارتفاع زمن الاستجابة (بنج)",
        template="🐌 ارتفاع البنج على «{device}»\nالعنوان: {ip}\nالبنج الحالي: {latency}",
        channels=("telegram",),
        group="network",
        extra_vars=("device", "ip", "latency"),
        default_enabled=False,
    ),
)

# Fast lookup by key, preserving order for the UI.
EVENTS: dict[str, EventDef] = {e.key: e for e in _EVENTS}
EVENT_KEYS: tuple[str, ...] = tuple(EVENTS.keys())

# UI groups (ordered) → Arabic group titles.
GROUP_LABELS: dict[str, str] = {
    "subscribers": "أحداث المشتركين",
    "billing": "المالية والمحفظة",
    "store": "إشعارات متجر البطاقات الإلكتروني",
    "network": "الشبكة والأجهزة",
}

# Default days_before for the dunning (near_expiry) reminder.
DEFAULT_DAYS_BEFORE = 3


# ── per-event rule ─────────────────────────────────────────────────────
@dataclass
class NotifRule:
    """A single event's saved (or default) configuration."""

    event: EventDef
    enabled: bool
    channels: list[str]
    template: str
    days_before: int = DEFAULT_DAYS_BEFORE

    @property
    def key(self) -> str:
        return self.event.key

    def active_channels(self) -> list[str]:
        return [c for c in self.channels if c in NOTIF_CHANNELS]


def _parse_channels(raw: Any, default: tuple[str, ...],
                    allowed: "tuple[str, ...] | None" = None) -> list[str]:
    """CSV (or list) → clean ordered list of known channels.

    ``None``/unset → the event's default channels. An explicitly-saved empty
    string means "no channels" (operator unticked them all) and is honoured.

    ``allowed`` (اختياري) يقيّد القنوات بقائمة يدعمها الحدث فعليًّا — يسدّ
    فخًّا حقيقيًّا (client1، 2026-07-27): واجهة قديمة سمحت بحفظ «telegram»
    وحدها لحدث «شراء بطاقات من المتجر» بينما مشتري المتجر (card_users) بلا
    تيليجرام إطلاقًا — فمات إشعار بيانات البطاقة بصمت. قناة محفوظة خارج
    ``allowed`` تُرشَّح؛ ولو كانت **كلّ** المحفوظات خارجها (misconfig لا
    «إيقاف صريح») نعود لقنوات الحدث الافتراضية كي يصل الإشعار.
    (أحداث المشتركين تبقى بلا تقييد — تيليجرام يصل المشترك الرابط حسابه.)
    """
    if raw is None:
        return list(default)
    if isinstance(raw, (list, tuple)):
        parts = [str(p).strip().lower() for p in raw]
    else:
        text = str(raw).strip()
        if text == "":
            return []  # explicitly none
        parts = [p.strip().lower() for p in text.split(",")]
    known = [p for p in parts if p in NOTIF_CHANNELS]
    scope = tuple(allowed) if allowed else NOTIF_CHANNELS
    seen: list[str] = []
    for p in known:
        if p in scope and p not in seen:
            seen.append(p)
    if not seen and known:
        return list(default)
    return seen


def _event_allowed_channels(event: "EventDef") -> "tuple[str, ...] | None":
    """قائمة القنوات المسموح حفظها للحدث — مقيّدة لأحداث المتجر فقط.

    مستلم أحداث المتجر card_user (بلا تيليجرام أبدًا)، فقنوات الحدث هي
    السقف. بقية المجموعات بلا تقييد (تيليجرام المشترك مشروع)."""
    return event.channels if event.group == "store" else None


def _coerce_days(raw: Any, default: int = DEFAULT_DAYS_BEFORE) -> int:
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    # Keep it sane: 1..60 days.
    return max(1, min(60, n))


# ── load / save ─────────────────────────────────────────────────────────
def load_rule(tenant_id: int, event_key: str) -> NotifRule | None:
    """Load a single event's rule (with defaults). ``None`` for unknown keys."""
    event = EVENTS.get(event_key)
    if event is None:
        return None
    from ..db.repos import tenants_repo

    tid = int(tenant_id or 1)

    def _get(name: str, default: str = "") -> str:
        return tenants_repo.get_setting(tid, _settings_key(event_key, name), default)

    enabled_raw = _get("enabled", "")
    enabled = _truthy(enabled_raw) if enabled_raw.strip() != "" else event.default_enabled

    channels_raw = tenants_repo.get_setting(tid, _settings_key(event_key, "channels"), "__unset__")
    channels = _parse_channels(None if channels_raw == "__unset__" else channels_raw,
                               event.channels, allowed=_event_allowed_channels(event))

    template = (_get("template", "") or "").strip() or event.template
    days_before = _coerce_days(_get("days_before", ""), DEFAULT_DAYS_BEFORE)

    return NotifRule(
        event=event,
        enabled=enabled,
        channels=channels,
        template=template,
        days_before=days_before,
    )


def load_rules(tenant_id: int) -> list[NotifRule]:
    """Load every event's rule (defaults included) in registry order.

    First run (nothing saved) returns the full set of sensible defaults so the
    settings page is fully pre-populated and the engine works out of the box.
    """
    tid = int(tenant_id or 1)
    rules: list[NotifRule] = []
    for key in EVENT_KEYS:
        rule = load_rule(tid, key)
        if rule is not None:
            rules.append(rule)
    return rules


def save_rules(tenant_id: int, values: dict[str, Any], *, by: int = 0,
               only_keys: "tuple[str, ...] | list[str] | None" = None) -> list[NotifRule]:
    """Persist event rules from a posted form mapping to ``tenant_settings``.

    ``values`` keys follow the form-field naming convention used by the
    settings page:
        ``<event>__enabled`` / ``<event>__channels`` (list or csv)
        ``<event>__template`` / ``<event>__days_before``

    ``only_keys`` (optional) scopes the save to a subset of events — a page
    that edits only some events (e.g. subscriber-facing groups) MUST pass it
    so untouched events (e.g. operator/network) are NOT reset to defaults.
    Only known event keys are touched. Returns the freshly-loaded rules.
    """
    from ..db.repos import tenants_repo

    tid = int(tenant_id or 1)
    keys = [k for k in (only_keys if only_keys is not None else EVENT_KEYS) if k in EVENTS]
    for key in keys:
        event = EVENTS[key]
        enabled = _truthy(values.get(f"{key}__enabled"))
        channels = _parse_channels(values.get(f"{key}__channels"), event.channels,
                                   allowed=_event_allowed_channels(event))
        template = (str(values.get(f"{key}__template") or "").strip()) or event.template

        tenants_repo.set_setting(tid, _settings_key(key, "enabled"), "1" if enabled else "0", by=by)
        tenants_repo.set_setting(tid, _settings_key(key, "channels"), ",".join(channels), by=by)
        tenants_repo.set_setting(tid, _settings_key(key, "template"), template, by=by)
        if event.has_days_before:
            days = _coerce_days(values.get(f"{key}__days_before"), DEFAULT_DAYS_BEFORE)
            tenants_repo.set_setting(tid, _settings_key(key, "days_before"), str(days), by=by)

    return load_rules(tid)


# ── context building ─────────────────────────────────────────────────────
def _bot_context(tenant_id: int, subscriber) -> dict[str, str]:
    """Reuse the Phase-2 subscriber context builder (same variable set)."""
    try:
        from . import comms_bot

        return comms_bot.build_context(int(tenant_id or 1), subscriber)
    except Exception:  # noqa: BLE001 — context must never break a notify
        return {}


def build_event_context(
    tenant_id: int,
    subscriber=None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Full substitution context for an event template.

    Starts from the Phase-2 subscriber context ({username},{prof},{exp},
    {balance},{status},{down_speed}…) then layers any event-specific extras
    ({amount},{days},{percent},{device},{old_prof}…). Every value is a string;
    unknown ``{var}`` placeholders render empty (handled by the renderer).
    """
    ctx: dict[str, str] = {}
    ctx.update(_bot_context(tenant_id, subscriber))
    for k, v in (extra or {}).items():
        if v is None:
            continue
        # Skip non-scalar extras (e.g. the purchased ``cards`` list, which the
        # SMS branch consumes directly): they must never be stringified into the
        # rendered message — that would leak card passwords into the body/log.
        if isinstance(v, (list, dict, tuple, set)):
            continue
        ctx[str(k).strip().lower()] = str(v)
    return ctx


def render_template(template: str, context: dict[str, str]) -> str:
    """Substitute ``{var}`` placeholders (reusing the bot's renderer)."""
    try:
        from . import comms_bot

        return comms_bot.render_reply(template, context)
    except Exception:  # noqa: BLE001
        return str(template or "")


# ── dispatch result (for tests / logging) ───────────────────────────────
@dataclass
class NotifyOutcome:
    """Outcome of one :func:`notify_event` call."""

    fired: bool = False
    reason: str = ""
    event_key: str = ""
    message: str = ""
    channels: list[str] = field(default_factory=list)
    sent: dict[str, bool] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


# ── the engine ───────────────────────────────────────────────────────────
def notify_event(
    event_key: str,
    *,
    tenant_id: int,
    subscriber=None,
    context: dict[str, Any] | None = None,
) -> NotifyOutcome:
    """Fire one business event's notification. NEVER raises.

    Steps:
      1. Load the event's rule. If unknown / disabled / no channels → no-op.
      2. Build the substitution context (subscriber + extras) and render the
         saved template.
      3. Dispatch to each enabled channel:
           * sms / whatsapp → Phase-1 HTTP provider. When we have a real
             subscriber recipient we go through the campaign service so a
             delivery row is logged; otherwise we fall back to a direct
             ``http_send`` to the subscriber's phone.
           * telegram → Phase-2 :func:`telegram_notifier.send_to_tenant`.

    Returns a :class:`NotifyOutcome` describing what happened (handy for the
    dunning worker + tests). On any internal error the outcome simply reports
    ``fired=False`` with a reason — the caller is never disturbed.
    """
    tid = int(tenant_id or 1)
    outcome = NotifyOutcome(event_key=str(event_key or ""))
    try:
        rule = load_rule(tid, event_key)
    except Exception:  # noqa: BLE001
        outcome.reason = "load_error"
        return outcome

    if rule is None:
        outcome.reason = "unknown_event"
        return outcome
    if not rule.enabled:
        outcome.reason = "disabled"
        return outcome

    channels = rule.active_channels()
    if not channels:
        outcome.reason = "no_channels"
        return outcome

    try:
        ctx = build_event_context(tid, subscriber, context or {})
        message = render_template(rule.template, ctx).strip()
    except Exception:  # noqa: BLE001
        outcome.reason = "render_error"
        return outcome

    if not message:
        outcome.reason = "empty_message"
        return outcome

    outcome.message = message
    outcome.channels = list(channels)

    phone = _subscriber_phone(subscriber)
    recipient_id = _subscriber_id(subscriber)
    # Subscriber-facing events deliver Telegram to the SUBSCRIBER's own chat
    # (subscribers.telegram_chat_id, connected one-click); network/operator
    # events keep going to the tenant/operator chat.
    is_subscriber_event = rule.event.group in ("subscribers", "billing", "store")
    sub_chat_id = _subscriber_chat_id(tid, subscriber) if is_subscriber_event else ""

    for channel in channels:
        try:
            if channel == "telegram":
                if is_subscriber_event:
                    if sub_chat_id:
                        from . import telegram_notifier
                        ok, err = telegram_notifier.send_to_chat(tid, sub_chat_id, message)
                    else:
                        ok, err = False, "تيليجرام غير مرتبط لهذا المشترك"
                else:
                    ok, err = _send_telegram(tid, message)
            elif channel == "sms" and rule.event.sends_card_credentials:
                # Purchased-card login(s) SMS — sent DIRECTLY via the TweetSMS
                # adapter so the cleartext card password(s) never reach the
                # delivery log; a redacted (count-only) audit row is recorded.
                ok, err = _send_card_credentials_sms(tid, subscriber, context or {})
            elif channel == "whatsapp" and rule.event.sends_card_credentials:
                # Purchased-card login(s) WhatsApp — sent DIRECTLY (unlogged) so
                # the cleartext card password(s) never reach the delivery log;
                # a redacted (count-only) audit row is recorded.
                ok, err = _send_card_credentials_whatsapp(tid, subscriber, context or {})
            elif channel == "sms" and rule.event.sends_credentials:
                # Credentials SMS (username + password) — sent DIRECTLY via the
                # TweetSMS adapter so the cleartext password is never persisted
                # in the delivery log; a redacted audit row is recorded instead.
                ok, err = _send_credentials_sms(tid, subscriber)
            elif channel == "whatsapp" and rule.event.sends_credentials:
                # Credentials WhatsApp (username + password) — sent DIRECTLY
                # (unlogged) so the cleartext password is never persisted in the
                # delivery log; a redacted audit row is recorded instead.
                ok, err = _send_credentials_whatsapp(tid, subscriber)
            elif channel == "sms" and rule.event.group == "store":
                # Store movement SMS (recharge/withdraw) — the buyer is a
                # card_user, not a subscriber, so route through the tenant's
                # connected TweetSMS directly (the readiness flag on the page
                # reflects TweetSMS), not the generic comms provider.
                ok, err = _send_store_sms(tid, subscriber, message)
            else:  # sms / whatsapp
                ok, err = _send_http_channel(
                    tid,
                    channel,
                    message,
                    phone=phone,
                    recipient_id=recipient_id,
                    event_key=rule.event.key,
                    reason=rule.event.label,
                )
        except Exception as exc:  # noqa: BLE001 — a single channel can never break the rest
            ok, err = False, f"خطأ غير متوقع: {exc}"
        outcome.sent[channel] = bool(ok)
        if not ok and err:
            outcome.errors[channel] = err

    # We reached the dispatch loop with ≥1 enabled channel, so we *attempted*
    # to notify — that's what ``fired`` means here (the dunning worker uses it
    # to mark "reminded today" so it won't re-nudge, even if a channel was
    # unconfigured). ``sent`` carries the per-channel success detail.
    outcome.fired = True
    outcome.reason = "sent" if any(outcome.sent.values()) else "no_channel_sent"
    return outcome


# ── channel senders ──────────────────────────────────────────────────────
def _send_credentials_sms(tenant_id: int, subscriber) -> tuple[bool, str]:
    """Send the subscriber their login (username+password) by SMS. Never raises.

    Delegates to :mod:`subscriber_credentials` which sends through the TweetSMS
    adapter directly (no body logging) and records a redacted audit row."""
    try:
        from . import subscriber_credentials

        res = subscriber_credentials.send(
            int(tenant_id or 1), subscriber, actor="system:notifications"
        )
        return bool(res.get("ok")), (res.get("error_ar") or "" if not res.get("ok") else "")
    except Exception as exc:  # noqa: BLE001
        return False, f"خطأ غير متوقع في إرسال بيانات الدخول: {exc}"


def _send_credentials_whatsapp(tenant_id: int, subscriber) -> tuple[bool, str]:
    """Send the subscriber their login (username+password) by WhatsApp. Never raises.

    Delegates to :mod:`subscriber_credentials` which sends through the tenant's
    WhatsApp channel DIRECTLY (no body logging) and records a redacted audit row."""
    try:
        from . import subscriber_credentials

        res = subscriber_credentials.send_whatsapp(
            int(tenant_id or 1), subscriber, actor="system:notifications"
        )
        return bool(res.get("ok")), (res.get("error_ar") or "" if not res.get("ok") else "")
    except Exception as exc:  # noqa: BLE001
        return False, f"خطأ غير متوقع في إرسال بيانات الدخول (واتساب): {exc}"


def _send_card_credentials_sms(tenant_id: int, subscriber, context: dict[str, Any]) -> tuple[bool, str]:
    """Send the purchased card login(s) by SMS. Never raises.

    Delegates to :mod:`store_movement_notifications` which sends through the
    TweetSMS adapter directly (no body logging) and records a redacted,
    count-only audit row. The cards (with passwords) ride in
    ``context['cards']``; they are NEVER part of the rendered message/log."""
    try:
        from . import store_movement_notifications as smn

        res = smn.send_cards_credentials_sms(
            int(tenant_id or 1), subscriber, list((context or {}).get("cards") or []),
            actor="system:notifications",
            card_user_id=int((context or {}).get("card_user_id") or 0),
        )
        return bool(res.get("ok")), (res.get("error_ar") or "" if not res.get("ok") else "")
    except Exception as exc:  # noqa: BLE001
        return False, f"خطأ غير متوقع في إرسال بيانات البطاقات: {exc}"


def _send_card_credentials_whatsapp(tenant_id: int, subscriber, context: dict[str, Any]) -> tuple[bool, str]:
    """Send the purchased card login(s) by WhatsApp. Never raises.

    WhatsApp sibling of :func:`_send_card_credentials_sms`: delegates to
    :mod:`store_movement_notifications` which sends through the tenant's WhatsApp
    channel DIRECTLY (no body logging) and records a redacted, count-only audit
    row. The cards (with passwords) ride in ``context['cards']``; they are NEVER
    part of the rendered message/log."""
    try:
        from . import store_movement_notifications as smn

        res = smn.send_cards_credentials_whatsapp(
            int(tenant_id or 1), subscriber, list((context or {}).get("cards") or []),
            actor="system:notifications",
            card_user_id=int((context or {}).get("card_user_id") or 0),
        )
        return bool(res.get("ok")), (res.get("error_ar") or "" if not res.get("ok") else "")
    except Exception as exc:  # noqa: BLE001
        return False, f"خطأ غير متوقع في إرسال بيانات البطاقات (واتساب): {exc}"


def _send_store_sms(tenant_id: int, subscriber, message: str) -> tuple[bool, str]:
    """Send a store-movement SMS to the buyer via TweetSMS. Never raises."""
    try:
        phone = _subscriber_phone(subscriber)
        if not phone:
            return False, "لا يوجد رقم جوال للمشتري"
        from . import tweetsms

        if not tweetsms.is_connected(int(tenant_id or 1)):
            return False, "اربط حساب SMS أولاً"
        out = tweetsms.send_sms(int(tenant_id or 1), phone, message)
        ok = bool(out.get("ok"))
        return ok, ("" if ok else (out.get("error_ar") or "فشل الإرسال عبر TweetSMS."))
    except Exception as exc:  # noqa: BLE001
        return False, f"خطأ غير متوقع أثناء إرسال SMS المتجر: {exc}"


def _send_telegram(tenant_id: int, message: str) -> tuple[bool, str]:
    """Send to the tenant's Telegram chat (Phase 2). Never raises."""
    try:
        from . import telegram_notifier

        ok, err = telegram_notifier.send_to_tenant(int(tenant_id or 1), message)
        # An empty error string with ok=False means "not configured / disabled".
        return bool(ok), (err or ("telegram غير مهيأ" if not ok else ""))
    except Exception as exc:  # noqa: BLE001
        return False, f"خطأ في إرسال telegram: {exc}"


def _send_http_channel(
    tenant_id: int,
    channel: str,
    message: str,
    *,
    phone: str,
    recipient_id: int,
    event_key: str = "",
    reason: str = "",
) -> tuple[bool, str]:
    """Send an sms/whatsapp message via the Phase-1 provider. Never raises.

    Preference order:
      1. Real subscriber recipient → go through the campaign service's
         ``queue_notification`` so a ``message_deliveries`` row is logged and
         the per-channel HTTP provider is auto-selected from tenant settings.
      2. No subscriber id but we have a phone → direct ``http_send`` to that
         phone (still no network unless the channel is configured with a URL).
    """
    from . import comms_providers

    # Path 1 — logged delivery via the campaign service (best: audit trail).
    if recipient_id > 0:
        try:
            from .notification_campaigns import NotificationCampaignService

            svc = NotificationCampaignService(tenant_id=int(tenant_id or 1))
            result = svc.queue_notification(
                recipient_type="subscriber",
                recipient_id=recipient_id,
                channel=channel,
                subject="",
                body=message,
                # يحمل «السبب» (مفتاح الحدث + عنوانه العربي) إلى سجل التسليم كي
                # يعرضه الجدول الزمني للإشعارات، بدل مصدر مبهم.
                metadata={"source": "notifications_engine",
                          "event": event_key, "reason": reason},
                actor="system:notifications",
            )
            status = str((result.get("delivery") or {}).get("status") or "")
            if status == "sent":
                return True, ""
            if status in ("queued", "skipped"):
                # Provider degraded gracefully (channel off / unconfigured).
                err = str((result.get("delivery") or {}).get("error_message") or "")
                return False, (err or "القناة غير مهيأة — تم التسجيل فقط.")
            # failed (e.g. no phone on the recipient)
            err = str((result.get("delivery") or {}).get("error_message") or "فشل الإرسال.")
            return False, err
        except Exception as exc:  # noqa: BLE001 — fall through to direct send
            _LOG.debug("[notif] campaign dispatch failed, trying direct: %s", exc)

    # Path 2 — direct (unlogged) send to a bare phone number.
    return comms_providers.direct_send(int(tenant_id or 1), channel, phone, message)


# ── subscriber helpers ────────────────────────────────────────────────────
def _subscriber_phone(subscriber) -> str:
    if subscriber is None:
        return ""
    return str(getattr(subscriber, "mobile", "") or "").strip()


def _subscriber_id(subscriber) -> int:
    if subscriber is None:
        return 0
    try:
        return int(getattr(subscriber, "id", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _subscriber_chat_id(tenant_id: int, subscriber) -> str:
    """The subscriber's connected Telegram chat_id (``subscribers.telegram_chat_id``).

    The Subscriber dataclass doesn't expose this column yet, so we read it from
    the row directly (by id, then username). Never raises → '' on any miss."""
    if subscriber is None:
        return ""
    cid = str(getattr(subscriber, "telegram_chat_id", "") or "").strip()
    if cid:
        return cid
    sid = _subscriber_id(subscriber)
    uname = str(getattr(subscriber, "username", "") or "").strip()
    if not sid and not uname:
        return ""
    try:
        from ..db.connection import db
        if sid:
            row = db().execute(
                "SELECT telegram_chat_id FROM subscribers WHERE tenant_id=? AND id=?",
                (int(tenant_id or 1), sid)).fetchone()
        else:
            row = db().execute(
                "SELECT telegram_chat_id FROM subscribers WHERE tenant_id=? AND username=?",
                (int(tenant_id or 1), uname)).fetchone()
        return str((dict(row).get("telegram_chat_id") if row else "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def find_subscriber(tenant_id: int, *, username: str = "", subscriber_id: int = 0):
    """Best-effort subscriber lookup for callers that only have a key. Never raises."""
    tid = int(tenant_id or 1)
    try:
        from ..db.repos import subscribers_repo

        if username:
            return subscribers_repo.get_subscriber(tid, username)
        if subscriber_id:
            rows = subscribers_repo.list_subscribers(tid, limit=1000)
            for s in rows:
                if int(getattr(s, "id", 0) or 0) == int(subscriber_id):
                    return s
    except Exception:  # noqa: BLE001
        return None
    return None
