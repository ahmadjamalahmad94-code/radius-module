"""device_health_alerts — Phase 5 deduplicated alert dispatch.

Decides whether a device status transition warrants an operator alert and sends
it via the EXISTING notification channels (Telegram direct, or the
notifications engine for the tenant's configured channels). Records every
decision (sent/skipped/failed) in network_device_monitor_alerts so the cooldown
gate and the audit trail both work.

Dedup rules (per device, per alert type):
  • down          — fires only after N consecutive fails; repeat suppressed for
                    COOLDOWN_SEC['down'] (no alert every poll while down).
  • high_latency  — fires after N consecutive high-latency samples; repeat
                    suppressed for COOLDOWN_SEC['high_latency'].
  • recovery      — fires once when a previously-down device returns; its own
                    short cooldown guards against flap double-sends.

`_send` is the single delivery seam (mockable in tests); the state machine is
what Phase 5 actually guarantees.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ..db.repos import device_health_repo as repo

_LOG = logging.getLogger(__name__)

# How many consecutive bad samples before the first alert.
DOWN_AFTER_N = 2
HIGH_LATENCY_AFTER_N = 3

# Repeat-suppression window per alert type (seconds).
COOLDOWN_SEC = {
    "down": 5 * 60,
    "high_latency": 15 * 60,
    "recovery": 2 * 60,
}

# Internal alert type → notifications-engine event key.
_ENGINE_KEY = {
    "down": "router_down",
    "recovery": "router_up",
    "high_latency": "network_high_latency",
    # Router/NAS reachability transitions (router_health_monitor) — kept here so
    # an explicit sms/whatsapp channel could still resolve a key; the DEFAULT
    # channel goes straight to Telegram (no engine), like every device alert.
    "router_offline": "router_down",
    "router_online": "router_up",
}

# Arabic heading per alert kind (device + router). The body lines (العنوان /
# الوصف / البنج / الوقت) are appended consistently by `format_alert_message`.
_HEADING = {
    "down":           "🚨 انقطع الاتصال مع «{name}»",
    "recovery":       "✅ عاد الاتصال مع «{name}»",
    "high_latency":   "🐌 ارتفاع البنج على «{name}»",
    "router_offline": "🔴 الراوتر «{name}» غير متصل",
    "router_online":  "🟢 عاد اتصال الراوتر «{name}»",
}

# Panel (bell) title + severity per alert kind.
_PANEL_TITLE = {
    "down":           "انقطاع اتصال: {name}",
    "recovery":       "عاد الاتصال: {name}",
    "high_latency":   "ارتفاع بنج: {name}",
    "router_offline": "راوتر غير متصل: {name}",
    "router_online":  "عاد الراوتر: {name}",
}
_SEVERITY = {
    "down": "critical", "recovery": "success", "high_latency": "warning",
    "router_offline": "critical", "router_online": "success",
}


def format_alert_message(alert_type: str, *, name: str, ip: str = "",
                         description: str = "", ping: str = "",
                         when: Optional[str] = None) -> str:
    """يبني نص تنبيه عربي موحّد لكل الأنواع (جهاز/راوتر).

    كل تنبيه يحمل — باتساق — اسم الجهاز/الراوتر (في العنوان)، ثم: العنوان
    (IP)، الوصف (إن وُجد)، البنج (للأنواع ذات القياس)، و«الوقت» دائماً (يُصلح
    نقص الوقت في «عاد الاتصال»). أي حقل فارغ يُحذف سطره."""
    head = _HEADING.get(alert_type, "ℹ️ «{name}»").format(name=name)
    lines = [head]
    if ip:
        lines.append(f"العنوان: {ip}")
    if description:
        lines.append(f"الوصف: {description}")
    if ping:
        lines.append(f"البنج: {ping}")
    lines.append(f"الوقت: {when or _now_human()}")
    return "\n".join(lines)


def dispatch(tenant_id: int, *, alert_type: str, message: str, name: str = "",
             link: str = "/admin/radius/device-health",
             channel: str = "") -> "tuple[bool, str]":
    """يُرسل تنبيهاً واحداً عبر المسار القانوني (تلجرام مباشرة من متجر «تنبيهات
    تلجرام») ويكتبه دائماً في جرس مركز الإشعارات — لا بوّابة notif.*.enabled.

    نقطة الدخول المشتركة لمراقب الراوترات ولأي مُطلِق آخر. يُرجع (ok, reason).
    آمن: لا يرفع استثناء."""
    res = _send(int(tenant_id), channel, alert_type, message, {}, None)
    if isinstance(res, tuple):
        ok, reason = res
    else:
        ok, reason = bool(res), ("sent" if res else "not_delivered")
    title = _PANEL_TITLE.get(alert_type, "{name}").format(name=name or "")
    _panel_write(int(tenant_id),
                 title=title, body=message,
                 severity=_SEVERITY.get(alert_type, "info"),
                 link=link, delivered=ok, reason=reason)
    return ok, reason


def evaluate_and_dispatch(
    *,
    tenant_id: int,
    device: dict,
    prev_status: str,
    new_status: str,
    latency_ms: Optional[float] = None,
) -> list:
    """Evaluate the transition and dispatch any warranted alerts.

    `device` must be the FRESH row (consecutive counters already updated by the
    poller's set_status). Returns the list of alert types actually sent.
    """
    tid = int(tenant_id)
    device_id = int(device["id"])
    channel = (device.get("alert_channel") or "").strip()
    name = device.get("name") or f"#{device_id}"
    ip = device.get("ip_address") or ""
    # «الوصف»: عمود notes في network_device_monitor_devices — يُضاف لكل تنبيه.
    desc = (device.get("notes") or "").strip()
    now_h = _now_human()
    # البنج: يُعرض فقط حين تتوفّر قيمة (لا «—» في الرسالة).
    lat_str = f"{latency_ms} ms" if latency_ms is not None else ""

    pending: list[tuple[str, str]] = []  # (alert_type, message)

    if new_status in ("down", "timeout") \
            and int(device.get("consecutive_down_count") or 0) >= DOWN_AFTER_N:
        pending.append(("down", format_alert_message(
            "down", name=name, ip=ip, description=desc, when=now_h)))

    if prev_status in ("down", "timeout") and new_status == "up":
        # «عاد الاتصال» كان ينقصه «الوقت» — الآن يحمله مثل «انقطع» + الوصف.
        pending.append(("recovery", format_alert_message(
            "recovery", name=name, ip=ip, description=desc, ping=lat_str, when=now_h)))

    if new_status == "high_latency" \
            and int(device.get("consecutive_high_latency_count") or 0) >= HIGH_LATENCY_AFTER_N:
        pending.append(("high_latency", format_alert_message(
            "high_latency", name=name, ip=ip, description=desc, ping=lat_str, when=now_h)))

    fired: list[str] = []
    for alert_type, message in pending:
        dedup_key = f"{device_id}:{alert_type}"
        cooldown = COOLDOWN_SEC.get(alert_type, 0)
        last = repo.last_alert_at(tid, dedup_key)
        if last and cooldown > 0 and _seconds_since(last) < cooldown:
            repo.add_alert(tenant_id=tid, device_id=device_id,
                           alert_type=alert_type, channel=channel or "default",
                           status="skipped", dedup_key=dedup_key,
                           message="cooldown")
            continue
        # `_send` is a mockable seam; tolerate both the new (ok, reason) tuple
        # and a legacy bool stub so existing tests/mocks keep working.
        _res = _send(tid, channel, alert_type, message, device, latency_ms)
        if isinstance(_res, tuple):
            ok, reason = _res
        else:
            ok, reason = bool(_res), ("sent" if _res else "not_delivered")
        # ALWAYS surface the transition in the unified in-app notification
        # center + bell (panel_notifications), regardless of whether an external
        # channel (Telegram) delivered. This is the «no silent drop» guarantee:
        # the operator sees a device going down/up in-panel even with zero
        # external-channel config, and — when Telegram isn't set up — the notice
        # itself tells them how to enable instant phone alerts.
        _surface_in_panel(tid, alert_type, message, device,
                          delivered=ok, reason=reason)
        repo.add_alert(tenant_id=tid, device_id=device_id, alert_type=alert_type,
                       channel=channel or "default",
                       status="sent" if ok else "failed",
                       dedup_key=dedup_key, message=message)
        if ok:
            fired.append(alert_type)
    return fired


def _panel_write(tenant_id: int, *, title: str, body: str, severity: str,
                 link: str, delivered: bool, reason: str) -> None:
    """يكتب صفّاً في جرس مركز الإشعارات (panel_notifications) — يظهر دائماً
    حتى لو لم تُسلَّم أي قناة خارجية («لا إسقاط صامت»). عند تعذّر تلجرام
    بسبب عدم التهيئة، نُلحق تلميح التفعيل بالنص. لا يرفع استثناء أبداً."""
    if not delivered and reason == "telegram_not_configured":
        body += ("\n\n🔕 لم يصل إشعار فوري على جوالك لأن «تنبيهات تلجرام» غير "
                 "مُفعّلة. فعّلها من: الإعدادات ← تنبيهات تلجرام، ليصلك انقطاع/"
                 "عودة الأجهزة فورًا.")
    try:
        from . import notifications as panel
        # dedup_key فارغ عمدًا: المُستدعي يضمن عدم التكرار (cooldown للأجهزة،
        # أو الانتقال نفسه للراوترات)، فكل حدث يستحقّ صفّاً جديداً في الجرس.
        panel.notify(tenant_id, type="system", severity=severity, title=title,
                     body=body, link=link, source="local")
    except Exception:  # noqa: BLE001
        _LOG.debug("[device-health] panel notify failed (%s)", title)


def _surface_in_panel(tenant_id: int, alert_type: str, message: str,
                      device: dict, *, delivered: bool, reason: str) -> None:
    """Bell surface for a DEVICE alert — builds the name-based title then
    delegates to `_panel_write`. Kept for the device poller path."""
    name = device.get("name") or f"#{device.get('id')}"
    title = _PANEL_TITLE.get(alert_type, "{name}").format(name=name)
    _panel_write(int(tenant_id), title=title, body=message,
                 severity=_SEVERITY.get(alert_type, "info"),
                 link="/admin/radius/device-health",
                 delivered=delivered, reason=reason)


def telegram_ready(tenant_id: int) -> bool:
    """هل تلجرام مُهيّأ في المتجر الرسمي (نفس صفحة «تنبيهات تلجرام»)؟

    مصدر الحقيقة الوحيد لحالة تلجرام هو ما تكتبه/تقرؤه صفحة
    ``/admin/radius/alerts/telegram`` (admin_alerts) — أي
    ``tenant_telegram_settings`` عبر ``admin_alerts.telegram_ready``. نَقرأ
    منه هنا (لا من أي متجر آخر) كي تُطابق الشارة/التلميح في صفحة الأجهزة
    حالةَ تلك الصفحة الفعلية. آمن: أي خطأ ⇒ False (يُظهر «فعّل تلجرام»)."""
    try:
        from . import admin_alerts
        return bool(admin_alerts.telegram_ready(int(tenant_id)))
    except Exception:  # noqa: BLE001
        return False


def _send(tenant_id: int, channel: str, alert_type: str, message: str,
          device: dict, latency_ms: Optional[float]) -> "tuple[bool, str]":
    """Deliver via the SAME proven path the «تنبيهات تلجرام» page uses.

    Telegram (the default channel AND an explicit ``channel='telegram'``) goes
    DIRECTLY through the canonical store+sender of
    ``/admin/radius/alerts/telegram``:
      • readiness  → ``admin_alerts.telegram_ready`` (tenant_telegram_settings)
      • delivery   → ``telegram_notifier.send_to_tenant`` (what the page's own
                     «اختبار الاتصال» / send uses)
    We deliberately DO NOT go through ``notifications_engine`` for Telegram: its
    extra per-event ``notif.router_down.enabled`` rule was a second, independent
    gate that silently blocked the push even though the bot was configured and
    the operator's test message worked. Explicit sms/whatsapp still use the
    engine (those channels live there).

    Returns ``(ok, reason)``; ``reason`` ∈ {'sent', 'telegram_not_configured',
    'no_event_key', 'not_delivered'}. Never raises.
    """
    try:
        ch = (channel or "").strip().lower()
        if ch in ("", "telegram"):
            # نفس متجر+مُرسِل صفحة «تنبيهات تلجرام» المُثبَتة عمليًّا.
            if not telegram_ready(int(tenant_id)):
                return False, "telegram_not_configured"
            from . import telegram_notifier
            ok, err = telegram_notifier.send_to_tenant(int(tenant_id), message)
            if ok:
                return True, "sent"
            # ready لكنه رفض الإرسال (شبكة/تلجرام) — ليست مشكلة تهيئة.
            return False, ("not_delivered" if err else "telegram_not_configured")

        # قنوات صريحة أخرى (sms/whatsapp) عبر محرّك الإشعارات.
        from . import notifications_engine as ne
        key = _ENGINE_KEY.get(alert_type)
        if not key:
            return False, "no_event_key"
        lat_str = f"{latency_ms} ms" if latency_ms is not None else "—"
        outcome = ne.notify_event(
            key, tenant_id=int(tenant_id), subscriber=None,
            context={"device": device.get("name") or f"#{device.get('id')}",
                     "ip": device.get("ip_address") or "",
                     "time": _now_human(), "latency": lat_str})
        delivered = bool(getattr(outcome, "fired", False)
                         and any((getattr(outcome, "sent", {}) or {}).values()))
        return (True, "sent") if delivered else (False, "not_delivered")
    except Exception:  # noqa: BLE001 — alerting must never break the poller
        _LOG.debug("[device-health] alert send failed (%s)", alert_type)
        return False, "not_delivered"


# ── time helpers (monkeypatchable in tests) ────────────────────

def _now_human() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _seconds_since(iso_str: str) -> float:
    """Seconds since an ISO-8601 timestamp (now_iso() writes '…Z'). Returns a
    huge number on parse failure so the caller treats the row as old enough."""
    try:
        s = str(iso_str).strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:  # noqa: BLE001
        return 1e9
