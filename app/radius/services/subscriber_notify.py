"""subscriber_notify — تسليم إشعارات المشترك على قنواته الخاصّة.

يكمل «إشعارات المشتركين»: عند وقوع حدث تجاريّ (قرب الانتهاء، دفعة، تغيير باقة،
إيقاف/تفعيل…) نُرسل للمشترك نفسه على قنواته المُفعَّلة:

  • Telegram (الأساس، بلا مفاتيح خارجية): إلى ``subscribers.telegram_chat_id``
    عبر بوت المستأجر (telegram_notifier.send_to_chat) — يربطه المشترك بضغطة.
  • WhatsApp / SMS: إلى هاتف ملف المشترك عبر مزوّد HTTP للمستأجر
    (comms_providers.http_send) — يُتخطّى بنظافة إن لم يُهيّأ.

التحكّم: لكل حدث قنوات مُختارة تُخزَّن في tenant_settings تحت
``subnotify.channels.<key>`` (مرآة admin_alerts.channels). صفحة «إشعارات
المشتركين» تضبطها فتتحكّم بالتسليم الفعليّ. dispatch لا يرفع استثناء أبدًا.

خارج النطاق (مُبلَّغ): دفع الجوّال (Firebase) — يحتاج مفاتيح المالك.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from ..db.connection import db
from ..db.repos import tenants_repo
from . import telegram_notifier, comms_providers

_LOG = logging.getLogger(__name__)

# قنوات المشترك (لا «جرس» — الجرس للإدارة). الترتيب = ترتيب العرض.
CHANNELS: tuple[str, ...] = ("telegram", "whatsapp", "sms")
CHANNEL_LABELS = {"telegram": "تيليجرام", "whatsapp": "واتساب", "sms": "SMS"}


@dataclass(frozen=True)
class SubEvent:
    key: str
    label: str
    hint: str
    template: str          # قالب عربي (HTML خفيف؛ {حقول})
    sample: dict
    default_channels: tuple[str, ...] = ("telegram",)
    wired: bool = False    # هل مُوصَّل بمصدر حدث فعليّ الآن؟


# الجرد. wired=True ⇒ مُوصَّل بمصدر حدث حقيقيّ في هذا الإصدار.
EVENTS: list[SubEvent] = [
    SubEvent(
        "expiry_soon", "قرب انتهاء الاشتراك", "قبل الانتهاء بأيام (عامل التذكير)",
        "⏳ <b>تنبيه انتهاء الاشتراك</b>\n"
        "مرحبًا {name}، ينتهي اشتراكك «{plan}» خلال {days} يوم"
        " (بتاريخ {expiry}). جدّد للاستمرار دون انقطاع.",
        {"name": "أحمد", "plan": "10 ميجا شهري", "days": "3", "expiry": "2026-07-01"},
        wired=True,
    ),
    SubEvent(
        "payment_received", "استلام دفعة/تجديد", "بعد تسجيل دفعة للمشترك",
        "✅ <b>تم استلام دفعتك</b>\n"
        "شكرًا {name}. المبلغ: {amount} {currency}."
        " اشتراكك «{plan}» سارٍ حتى {expiry}.",
        {"name": "أحمد", "amount": "20", "currency": "₪", "plan": "10 ميجا شهري",
         "expiry": "2026-07-23"},
        wired=True,
    ),
    SubEvent(
        "plan_changed", "تغيير/ترقية الباقة", "عند تغيير باقة المشترك",
        "🔄 <b>تم تغيير باقتك</b>\n"
        "مرحبًا {name}، باقتك الآن «{plan}». اشتراكك سارٍ حتى {expiry}.",
        {"name": "أحمد", "plan": "20 ميجا شهري", "expiry": "2026-07-23"},
        wired=True,
    ),
    SubEvent(
        "disabled", "إيقاف الخدمة", "عند إيقاف/تعليق المشترك",
        "⛔ <b>تم إيقاف خدمتك</b>\n"
        "مرحبًا {name}، أُوقفت خدمتك مؤقتًا. للاستفسار تواصل مع الدعم.",
        {"name": "أحمد"},
        wired=True,
    ),
    SubEvent(
        "enabled", "إعادة التفعيل", "بعد إعادة تفعيل المشترك",
        "🟢 <b>تمت إعادة تفعيل خدمتك</b>\n"
        "أهلًا بعودتك {name}. خدمتك تعمل الآن.",
        {"name": "أحمد"},
        wired=True,
    ),
    # ── مُجدوَلة (الجرد + الإعداد جاهز؛ مصدر الحدث يُوصَّل لاحقًا) ──
    SubEvent("expired", "انتهاء الاشتراك", "عند انتهاء الصلاحية فعليًّا",
             "🔴 <b>انتهى اشتراكك</b>\nمرحبًا {name}، انتهى اشتراكك «{plan}»"
             " بتاريخ {expiry}. جدّد لإعادة الخدمة.",
             {"name": "أحمد", "plan": "10 ميجا شهري", "expiry": "2026-06-23"}),
    SubEvent("quota_soon", "قرب نفاد الباقة", "عند بلوغ عتبة الاستهلاك",
             "📊 <b>باقتك تقترب من النفاد</b>\nمرحبًا {name}، استهلكت معظم باقتك.",
             {"name": "أحمد"}),
    SubEvent("welcome", "ترحيب بمشترك جديد", "عند إنشاء الاشتراك",
             "👋 <b>أهلًا بك</b>\nمرحبًا {name}، تم تفعيل اشتراكك «{plan}».",
             {"name": "أحمد", "plan": "10 ميجا شهري"}),
]

_BY_KEY = {e.key: e for e in EVENTS}
_TRUE = {"1", "true", "t", "on", "yes"}


def get_spec(key: str) -> SubEvent | None:
    return _BY_KEY.get(key)


# ── تخزين القنوات لكل حدث (tenant_settings) ──────────────────────────────
def _channels_key(key: str) -> str:
    return f"subnotify.channels.{key}"


def channels_for(tenant_id: int, key: str) -> set[str]:
    """قنوات هذا الحدث المُفعَّلة. إن لم تُضبط → القنوات الافتراضية للحدث."""
    spec = _BY_KEY.get(key)
    if not spec:
        return set()
    raw = str(tenants_repo.get_setting(int(tenant_id), _channels_key(key), "") or "").strip()
    if raw == "-":            # «-» = مضبوط صراحةً على «لا قنوات» (مُعطَّل)
        return set()
    if raw:
        return {c.strip() for c in raw.split(",") if c.strip() in CHANNELS}
    return set(spec.default_channels)


def set_channels(tenant_id: int, key: str, channels, *, by: int = 0) -> set[str]:
    if key not in _BY_KEY:
        return set()
    chans = {str(c).strip() for c in (channels or []) if str(c).strip() in CHANNELS}
    # «-» يميّز «مُعطَّل صراحةً» عن «غير مضبوط» (الذي يقع على الافتراضي).
    tenants_repo.set_setting(int(tenant_id), _channels_key(key),
                             ",".join(sorted(chans)) if chans else "-", by=by)
    return chans


def is_enabled(tenant_id: int, key: str) -> bool:
    return bool(channels_for(tenant_id, key))


# ── تصيير القالب ─────────────────────────────────────────────────────────
class _SafeDict(dict):
    def __missing__(self, k):  # noqa: D401
        return "—"


def render(key: str, context: dict | None = None) -> str:
    spec = _BY_KEY.get(key)
    if not spec:
        return ""
    ctx = _SafeDict({k: ("" if v is None else v) for k, v in (context or {}).items()})
    try:
        return spec.template.format_map(ctx)
    except Exception:  # noqa: BLE001 — قالب لا يكسر الإرسال أبدًا
        return spec.template


def preview(key: str) -> str:
    spec = _BY_KEY.get(key)
    return render(key, spec.sample) if spec else ""


# ── حلّ بيانات المشترك (chat_id + هاتف + باقة + انتهاء) ───────────────────
def _resolve_recipient(tenant_id: int, *, subscriber_id: int = 0,
                       username: str = "", sub=None) -> dict | None:
    """يقرأ مباشرة من جدول subscribers (telegram_chat_id غير مُعرَّض في
    الـdataclass بعد). يُعيد None إن لم يوجد المشترك."""
    tid = int(tenant_id)
    row = None
    try:
        if sub is not None and getattr(sub, "id", 0):
            subscriber_id = int(sub.id)
        if subscriber_id:
            row = db().execute(
                "SELECT id, username, full_name, mobile, telegram_chat_id, "
                "       expire_at, plan_id FROM subscribers "
                "WHERE tenant_id=? AND id=?", (tid, int(subscriber_id))).fetchone()
        elif username:
            row = db().execute(
                "SELECT id, username, full_name, mobile, telegram_chat_id, "
                "       expire_at, plan_id FROM subscribers "
                "WHERE tenant_id=? AND username=?", (tid, username)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    d = dict(row)
    plan_name = ""
    try:
        if d.get("plan_id"):
            p = db().execute("SELECT name FROM access_plans WHERE id=?",
                             (int(d["plan_id"]),)).fetchone()
            plan_name = str((dict(p).get("name") if p else "") or "")
    except Exception:  # noqa: BLE001
        plan_name = ""
    return {
        "id": int(d.get("id") or 0),
        "username": d.get("username") or "",
        "name": str(d.get("full_name") or d.get("username") or "").strip(),
        "phone": str(d.get("mobile") or "").strip(),
        "chat_id": str(d.get("telegram_chat_id") or "").strip(),
        "plan": plan_name,
        "expiry": _fmt_date(d.get("expire_at")),
    }


def _fmt_date(value) -> str:
    s = str(value or "").strip()
    return s[:10] if s else ""


# ── التسليم ───────────────────────────────────────────────────────────────
def deliver(tenant_id: int, key: str, *, subscriber_id: int = 0,
            username: str = "", sub=None, context: dict | None = None) -> dict:
    """يُسلّم إشعار حدث للمشترك على قنواته المُفعَّلة (متزامن، لا يرفع).

    يُعيد {key, sent:{channel:bool}, skipped:[..], recipient:{...}}.
    القنوات غير المهيّأة تُتخطّى بنظافة (sent=False بلا خطأ)."""
    out = {"key": key, "sent": {}, "skipped": [], "recipient": None}
    spec = _BY_KEY.get(key)
    if not spec:
        return out
    tid = int(tenant_id)
    chans = channels_for(tid, key)
    if not chans:
        out["skipped"].append("event_disabled")
        return out
    rcpt = _resolve_recipient(tid, subscriber_id=subscriber_id,
                              username=username, sub=sub)
    if not rcpt:
        out["skipped"].append("no_subscriber")
        return out
    out["recipient"] = {"username": rcpt["username"], "has_chat": bool(rcpt["chat_id"]),
                        "has_phone": bool(rcpt["phone"])}
    # دمج بيانات المشترك مع السياق المُمرَّر (السياق يَغلب).
    merged = dict(rcpt)
    merged.update(context or {})
    text = render(key, merged)
    if not text:
        return out

    # Telegram → chat_id الخاص بالمشترك عبر بوت المستأجر.
    if "telegram" in chans:
        ok, _err = telegram_notifier.send_to_chat(tid, rcpt["chat_id"], text)
        out["sent"]["telegram"] = bool(ok)
        if not ok and not rcpt["chat_id"]:
            out["skipped"].append("telegram_not_connected")

    # WhatsApp / SMS → هاتف المشترك عبر مزوّد HTTP للمستأجر (إن هُيّئ).
    for ch in ("whatsapp", "sms"):
        if ch not in chans:
            continue
        try:
            cfg = comms_providers.load_channel_config(tid, ch)
        except Exception:  # noqa: BLE001
            cfg = {}
        if not comms_providers.is_channel_active(cfg):
            out["skipped"].append(f"{ch}_not_configured")
            continue
        if not rcpt["phone"]:
            out["skipped"].append(f"{ch}_no_phone")
            continue
        phone = comms_providers.normalize_msisdn(
            rcpt["phone"], comms_providers.tenant_dial_code(tid))
        # نصّ عادي للرسائل القصيرة (بلا وسوم HTML).
        plain = _strip_html(text)
        res = comms_providers.http_send(
            template=cfg.get("send_url_template") or "",
            method=cfg.get("http_method") or "GET",
            phone=phone, message=plain)
        out["sent"][ch] = bool(getattr(res, "ok", False))
    return out


def dispatch(tenant_id: int, key: str, *, subscriber_id: int = 0,
             username: str = "", sub=None, context: dict | None = None) -> None:
    """نقطة الإطلاق من مواقع الأحداث: غير حاجبة (خيط خلفي)، لا ترفع أبدًا."""
    try:
        if key not in _BY_KEY:
            return
        # التقط ما يلزم قبل الخيط (db()/g قد لا يتوفّران في الخيط).
        tid = int(tenant_id)
        rcpt = _resolve_recipient(tid, subscriber_id=subscriber_id,
                                  username=username, sub=sub)
        if not rcpt or not channels_for(tid, key):
            return
        ctx = dict(context or {})

        def _worker():
            try:
                deliver(tid, key, subscriber_id=rcpt["id"], context=ctx)
            except Exception:  # noqa: BLE001
                _LOG.warning("subscriber_notify deliver failed: %s", key,
                             exc_info=True)
        threading.Thread(target=_worker, name=f"subnotify-{key}",
                         daemon=True).start()
    except Exception:  # noqa: BLE001 — الإشعار لا يكسر الحدث أبدًا
        _LOG.warning("subscriber_notify.dispatch failed: %s", key, exc_info=True)


def _strip_html(text: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", str(text or "")).strip()


def catalogue(tenant_id: int) -> list[dict]:
    tid = int(tenant_id)
    out = []
    for spec in EVENTS:
        out.append({
            "key": spec.key, "label": spec.label, "hint": spec.hint,
            "wired": spec.wired,
            "channels": sorted(channels_for(tid, spec.key)),
            "preview": preview(spec.key),
        })
    return out


__all__ = ["CHANNELS", "CHANNEL_LABELS", "EVENTS", "SubEvent", "get_spec",
           "channels_for", "set_channels", "is_enabled", "render", "preview",
           "deliver", "dispatch", "catalogue"]
