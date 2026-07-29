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
    "unavailable": 5 * 60,
    "high_latency": 15 * 60,
    "recovery": 2 * 60,
}

# Internal alert type → notifications-engine event key.
_ENGINE_KEY = {
    "down": "router_down",
    "unavailable": "router_down",
    "recovery": "router_up",
    "high_latency": "network_high_latency",
    # Router/NAS reachability transitions (router_health_monitor) — kept here so
    # an explicit sms/whatsapp channel could still resolve a key; the DEFAULT
    # channel goes straight to Telegram (no engine), like every device alert.
    "router_offline": "router_down",
    "router_online": "router_up",
    # الإشعارات الدوريّة (monitoring_digest): تذكير ما زال مفصولًا + تقرير الأسطول.
    "reminder_down": "router_down",
    "fleet_digest_ok": "router_up",
    "fleet_digest_issues": "router_down",
}

# رأس عربيّ موحّد لكل نوع — الاسم أوّلًا ثم ما حدث (أسهل مسحًا)، بنمط ثابت
# «EMOJI «{name}» — ماذا». الأسطر اللاحقة (📍 العنوان / ⚠️ السبب / 📝 الوصف /
# 📶 البنج / 🕒 الوقت) يُضيفها `format_alert_message` باتساق، وكلّ رمز LTR
# (IP/وقت/أرقام) معزول بـFSI…PDI ليُعرَض صحيحًا داخل العربيّة RTL.
_HEADING = {
    "down":           "🔴 «{name}» — انقطع الاتصال",
    "unavailable":    "📵 «{name}» — غير متاح",
    "recovery":       "✅ «{name}» — عاد الاتصال",
    "high_latency":   "🐌 «{name}» — بنج عالٍ",
    "router_offline": "🔴 «{name}» — الراوتر غير متصل",
    "router_online":  "🟢 «{name}» — عاد اتصال الراوتر",
}

# Panel (bell) title + severity per alert kind.
_PANEL_TITLE = {
    "down":           "انقطاع اتصال: {name}",
    "unavailable":    "غير متاح (الراوتر مفصول): {name}",
    "recovery":       "عاد الاتصال: {name}",
    "high_latency":   "ارتفاع بنج: {name}",
    "router_offline": "راوتر غير متصل: {name}",
    "router_online":  "عاد الراوتر: {name}",
    "reminder_down":       "ما زال مفصولًا: {name}",
    "fleet_digest_ok":     "الفحص الدوري: كل شيء سليم",
    "fleet_digest_issues": "الفحص الدوري: ملاحظات",
}
# MT90 — نوع التنبيه ← مفتاح صوت الحدث. `type="system"` وحده لا يُميّز
# «راوتر غير متصل» من «عاد الراوتر» من «الفحص الدوريّ»، والمالك يريد لكلٍّ
# صوتَه — خاصّةً الانقطاع الذي يجب أن يوقظه.
#
# MT97 — الفصل بين **الراوتر** و**جهاز الشبكة**. كانت الخريطة تُرسل الاثنين
# إلى `router_down`، فأكسس بوينت ينقطع يُشغّل صوت «انقطاع راوتر» — والمالك
# لا يستطيع أن يعرف من الصوت أيّهما سقط. وهما مُراقبان مختلفان أصلًا:
#   • router_health_monitor  → الراوترات المُسجَّلة (router_offline/online)
#   • device_health          → أجهزة الشبكة المُتتبَّعة (down/recovery/…)
# والفرق عمليّ لا شكليّ: سقوط الراوتر يعني انقطاع زبائنه، وسقوط أكسس
# بوينت يعني منطقةً واحدة. الصوتان يجب أن يختلفا.
_SOUND_EVENT = {
    # ── الراوترات ──
    "router_offline": "router_offline",
    "router_online": "router_up",
    # ── أجهزة الشبكة المُتتبَّعة ──
    "down": "device_down",
    "reminder_down": "device_down",
    "recovery": "device_up",
    # الجهاز غير مُتاحٍ لأنّ راوتره ساقط — ليس عطبَه، فلا يستحقّ صوت عطبه.
    "unavailable": "device_unavailable",
    "high_latency": "network_high_latency",
    # ── موارد المايكروتيك (router_resource_monitor) ──
    # MT99 — كانت غائبةً عن الخريطة تمامًا: معالجٌ يغلي على 95٪ أو ذاكرةٌ
    # تمتلئ كان يسقط على الصوت العامّ فلا يُميَّز عن إشعارٍ عاديّ. وهي
    # تنبيهاتٌ حيّة تعمل فعلًا (بعتباتٍ في الإعدادات) لا تعريفاتٍ ميتة.
    # العودة تحت العتبة تُطلق `_ok` — وهي طمأنةٌ لا إنذار، فصوتها يختلف.
    "res_cpu_high": "res_cpu_high",       "res_cpu_ok": "res_recovered",
    "res_ram_high": "res_ram_high",       "res_ram_ok": "res_recovered",
    "res_temp_high": "res_temp_high",     "res_temp_ok": "res_recovered",
    "res_disk_high": "res_disk_high",     "res_disk_ok": "res_recovered",
    "res_traffic_high": "res_traffic_high", "res_traffic_ok": "res_recovered",
    # ── الفحص الدوريّ ──
    # MT97.2 — حالتان مختلفتان تمامًا كانتا بصوتٍ واحد: «كلّ شيء سليم» طمأنةٌ
    # تُسمَع وتُنسى، و«فيه ملاحظات» نداءٌ يستوجب النظر. صوتٌ واحد لهما يعني
    # أنّ المشغّل يجب أن يفتح اللوحة ليعرف أيّهما — فيَفقد الصوت فائدته.
    "fleet_digest_ok": "health_digest_ok",
    "fleet_digest_issues": "health_digest_issues",
}
_SEVERITY = {
    "down": "critical", "unavailable": "critical",
    "recovery": "success", "high_latency": "warning",
    "router_offline": "critical", "router_online": "success",
    "reminder_down": "critical", "fleet_digest_ok": "success",
    "fleet_digest_issues": "warning",
}

# ── تنبيهات موارد الراوتر (router_resource_monitor) — أنواع لكل مقياس ──
# عبور العتبة (res_<m>_high) + العودة تحتها (res_<m>_ok)، تُبنى آليّاً كي تبقى
# الترويسات/العناوين متّسقة عربيًّا مع باقي التنبيهات. القرص بإطار «استخدام»
# (مستخدم%) كي يتطابق مع اللوحة — لا خلط حرّ/مستخدم. الحرارة = حرارة المعالج.
_RES_LABEL = {"cpu": "المعالج", "temp": "حرارة المعالج", "ram": "الذاكرة",
              "disk": "استخدام القرص", "traffic": "حركة الإنترنت"}
_RES_HIGH_HEAD = {
    "cpu": "🔥 «{name}» — ارتفاع حمل المعالج",
    "temp": "🌡️ «{name}» — ارتفاع حرارة المعالج",
    "ram": "🧠 «{name}» — ارتفاع استهلاك الذاكرة",
    "disk": "💽 «{name}» — ارتفاع استخدام القرص",
    "traffic": "📈 «{name}» — ارتفاع حركة الإنترنت",
}
for _m, _lbl in _RES_LABEL.items():
    _HEADING[f"res_{_m}_high"] = _RES_HIGH_HEAD[_m]
    _HEADING[f"res_{_m}_ok"] = f"✅ «{{name}}» — عاد {_lbl} لطبيعته"
    _PANEL_TITLE[f"res_{_m}_high"] = f"تنبيه {_lbl}: {{name}}"
    _PANEL_TITLE[f"res_{_m}_ok"] = f"عاد {_lbl}: {{name}}"
    _SEVERITY[f"res_{_m}_high"] = "critical" if _m in ("temp", "disk") else "warning"
    _SEVERITY[f"res_{_m}_ok"] = "success"


# ── عزل النصوص LTR داخل العربيّة (RTL) ──────────────────────────
# عناوين IP والتواريخ والأرقام (٪/ms/°م/عدد) تختلط مع العربيّة في الاتجاه RTL
# فتُقرأ مبعثرة. نَلفّ كل مقطع LTR بـFirst-Strong Isolate (U+2068) … Pop
# Directional Isolate (U+2069) فيُعرَض كجزيرة مستقلّة صحيحة في تلجرام والجرس.
_FSI = "⁨"   # FIRST STRONG ISOLATE
_PDI = "⁩"   # POP DIRECTIONAL ISOLATE


def isolate(text) -> str:
    """يَلفّ مقطعًا (IP/وقت/رقم) بعازل اتجاهي كي يُعرَض صحيحًا داخل العربيّة."""
    s = str(text).strip()
    return f"{_FSI}{s}{_PDI}" if s else ""


def format_alert_message(alert_type: str, *, name: str, ip: str = "",
                         description: str = "", ping: str = "", value: str = "",
                         reason: str = "", when: Optional[str] = None) -> str:
    """يبني نص تنبيه عربيّ موحّد ومنظّم لكل الأنواع (جهاز/راوتر/مورد).

    البنية: رأس (الاسم أوّلًا) ثم حقول مُعنوَنة بأيقونة، كلٌّ في سطره، بترتيب
    ثابت: ⚠️ السبب · 📊 القيمة/المقياس · 📍 العنوان · 📝 الوصف · 📶 البنج ·
    🕒 الوقت (دائمًا). كل رمز LTR (IP/وقت/أرقام) معزول. أي حقل فارغ يُحذف سطره."""
    head = _HEADING.get(alert_type, "ℹ️ «{name}»").format(name=name)
    lines = [head]
    if reason:
        lines.append(f"⚠️ السبب: {reason}")
    if value:
        lines.append(value)                       # سطر المقياس (معزول داخليًّا)
    if ip:
        lines.append(f"📍 العنوان: {isolate(ip)}")
    if description:
        lines.append(f"📝 الوصف: {description}")
    if ping:
        lines.append(f"📶 البنج: {isolate(ping)}")
    lines.append(f"🕒 الوقت: {isolate(when or _now_human())}")
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
                 link=link, delivered=ok, reason=reason,
                 event_key=_SOUND_EVENT.get(alert_type, ""))
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

    # «غير متاح» (خلف راوتر مفصول): يَمرّ بنفس عتبة الانقطاع المتتالي (DOWN_AFTER_N)
    # كي لا يُزعِج جهازاً جديداً غير مُهيّأ في أوّل فحص، ثم يُنبّه «غير متاح» بدل
    # الصمت. يستعمل عدّاد consecutive_down_count نفسه (set_status يراكمه للحالتين).
    if new_status == "unavailable" \
            and int(device.get("consecutive_down_count") or 0) >= DOWN_AFTER_N:
        # «السبب» يُسمّي الراوتر الأمّ المفصول — أوضح من «غير متاح» وحدها.
        reason = "الراوتر الأمّ مفصول"
        try:
            from ..db.repos import nas_repo
            _r = nas_repo.get_nas(tid, int(device.get("router_id") or 0))
            _rn = (getattr(_r, "name", "") if _r else "") or ""
            if _rn:
                reason = f"الراوتر «{_rn}» مفصول"
        except Exception:  # noqa: BLE001 — السبب تحسين، لا يكسر التنبيه
            pass
        pending.append(("unavailable", format_alert_message(
            "unavailable", name=name, ip=ip, description=desc,
            reason=reason, when=now_h)))

    if prev_status in ("down", "timeout", "unavailable") and new_status == "up":
        # «عاد الاتصال» كان ينقصه «الوقت» — الآن يحمله مثل «انقطع» + الوصف.
        # يشمل العودة من «غير متاح» (عاد الراوتر الأمّ).
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
                 link: str, delivered: bool, reason: str,
                 event_key: str = "") -> None:
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
                     body=body, link=link, source="local",
                     event_key=event_key)
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
                 delivered=delivered, reason=reason,
                 event_key=_SOUND_EVENT.get(alert_type, ""))


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
