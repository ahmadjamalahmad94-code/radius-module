"""admin_alerts — جرد تنبيهات الإدارة + إرسالها عبر تلجرام (مركزي).

feat/telegram-admin-alerts. مصدر واحد لكل «إشعارات الإدارة» التي يريد المالك
استقبالها على تلجرام: لكل تنبيه مفتاح ثابت، مجموعة، تسمية عربية، قالب رسالة
عربي مركزي بحقوله، وعيّنة بيانات للمعاينة/الاختبار.

نقطة الإرسال الوحيدة:
    admin_alerts.dispatch(tenant_id, "subscriber_new", {...})
تتحقّق من: (1) تفعيل هذا التنبيه، (2) ضبط بوت تلجرام للمستأجر — ثم تُصيّر
القالب وترسل عبر ``telegram_notifier.send_to_tenant`` في خيط خلفي (لا تحجب
الطلب، لا ترفع استثناء أبدًا، ومُزال التكرار ضمن نافذة قصيرة).

التفعيل/التعطيل لكل تنبيه يُخزَّن في tenant_settings تحت
``alerts.telegram.enabled.<key>`` (الافتراضي من السجلّ). أزرار الاختبار
تستخدم العيّنة لإرسال نموذج ومعاينة الشكل.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from ..db.repos import tenant_telegram_settings_repo, tenants_repo
from . import telegram_notifier

_LOG = logging.getLogger(__name__)

_TRUE = {"1", "true", "t", "on", "yes"}


# ════════════════════════════════════════════════════════════════════════
# السجلّ (الجرد) — كل تنبيهات الإدارة
# ════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AlertSpec:
    key: str
    group: str
    label: str            # تسمية عربية
    description: str       # شرح + مصدر الحدث
    template: str          # قالب عربي (HTML خفيف: <b>)
    sample: dict           # بيانات عيّنة للمعاينة/الاختبار
    default_enabled: bool = True


# مجموعات العرض (ترتيب + عنوان + أيقونة).
GROUPS: list[tuple[str, str, str]] = [
    ("subscribers", "المشتركون", "users"),
    ("network", "الشبكة والمايكروتيك", "network-wired"),
    ("finance", "المال والتحصيل", "money-bill-transfer"),
    ("store", "المتجر والموزّعون", "store"),
    ("security", "الأمان", "shield-halved"),
    ("system", "النظام", "server"),
]

ALERTS: list[AlertSpec] = [
    # ── المشتركون ──────────────────────────────────────────────────────
    AlertSpec(
        "subscriber_new", "subscribers", "إضافة مشترك جديد",
        "يُرسل عند إنشاء مشترك جديد (services/users.UsersService.create).",
        "🆕 <b>مشترك جديد</b>\n"
        "الاسم: {full_name}\n"
        "اسم المستخدم: <code>{username}</code>\n"
        "الباقة: {plan}\n"
        "الجوال: {mobile}\n"
        "أضافه: {actor}",
        {"full_name": "أحمد علي", "username": "ahmad99", "plan": "10 ميجا شهري",
         "mobile": "0599123456", "actor": "المدير"},
    ),
    AlertSpec(
        "subscriber_edited", "subscribers", "تعديل بيانات مشترك",
        "يُرسل عند تعديل بيانات مشترك قائم (UsersService.update).",
        "✏️ <b>تعديل بيانات مشترك</b>\n"
        "اسم المستخدم: <code>{username}</code>\n"
        "الاسم: {full_name}\n"
        "غُيّر: {changed}\n"
        "بواسطة: {actor}",
        {"username": "ahmad99", "full_name": "أحمد علي",
         "changed": "الباقة، الجوال", "actor": "المدير"},
    ),
    AlertSpec(
        "loan_granted", "subscribers", "سلفة وقت",
        "يُرسل عند منح/طلب سلفة وقت (customer_portals.submit_loan_request).",
        "💳 <b>سلفة وقت</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المدة: {minutes} دقيقة\n"
        "الحالة: {status}\n"
        "السبب: {reason}",
        {"username": "ahmad99", "minutes": "2880", "status": "مقبولة تلقائيًا",
         "reason": "طلب من البوابة"},
    ),
    AlertSpec(
        "speed_boost", "subscribers", "رفع سرعة مؤقت",
        "يُرسل عند تطبيق سرعة مؤقتة على مشترك (temp_speed.apply_temp_speed).",
        "🚀 <b>رفع سرعة مؤقت</b>\n"
        "المشترك: <code>{username}</code>\n"
        "السرعة: {down}↓ / {up}↑ كbps\n"
        "المدة: {duration} دقيقة\n"
        "تنتهي: {ends_at}\n"
        "بواسطة: {actor}",
        {"username": "ahmad99", "down": "20480", "up": "10240",
         "duration": "60", "ends_at": "2026-06-16 21:00", "actor": "المدير"},
    ),
    AlertSpec(
        "quota_exhausted", "subscribers", "انتهاء كوتة",
        "يُرسل عند رفض الدخول بسبب نفاد الكوتة (policy_engine) أو فرض الانتهاء.",
        "📉 <b>انتهاء كوتة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المستهلَك: {used_mb} م.بايت من {quota_mb}\n"
        "الباقة: {plan}",
        {"username": "ahmad99", "used_mb": "10240", "quota_mb": "10240",
         "plan": "10 جيجا شهري"},
        default_enabled=False,
    ),
    AlertSpec(
        "subscriber_expired", "subscribers", "انتهاء اشتراك",
        "يُرسل عند انتهاء صلاحية اشتراك (expiry_enforcer).",
        "⏳ <b>انتهاء اشتراك</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الاسم: {full_name}\n"
        "انتهى: {expired_at}",
        {"username": "ahmad99", "full_name": "أحمد علي", "expired_at": "2026-06-16"},
        default_enabled=False,
    ),
    AlertSpec(
        "portal_message", "subscribers", "رسالة من بوابة المشترك",
        "يُرسل عند إرسال مشترك رسالة/شكوى من بوابته تحتاج ردًّا "
        "(customer_portals.submit_renewal_request، نوع support). يحمل رابط الردّ.",
        "📨 <b>رسالة من بوابة المشترك</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الرسالة: {message}",
        {"username": "ahmad99", "message": "الإنترنت بطيء منذ الصباح، أرجو المتابعة."},
    ),
    # ── الشبكة والمايكروتيك ────────────────────────────────────────────
    AlertSpec(
        "mikrotik_connection_problem", "network", "مشاكل اتصال المايكروتيك",
        "يُرسل عند تعذّر الاتصال بجهاز المايكروتيك (API/الوصول).",
        "🛑 <b>مشكلة اتصال مايكروتيك</b>\n"
        "الجهاز: {router}\n"
        "العنوان: <code>{address}</code>\n"
        "الخطأ: {error}",
        {"router": "MT-Main", "address": "10.0.0.1", "error": "انتهت المهلة (timeout)"},
    ),
    AlertSpec(
        "network_disconnect", "network", "فصل شبكة / بنج سيّئ",
        "يُرسل عند فصل جهاز شبكة أو ارتفاع زمن الاستجابة (device-health poller).",
        "📡 <b>فصل/بنج سيّئ</b>\n"
        "الجهاز: {device}\n"
        "العنوان: <code>{address}</code>\n"
        "الحالة: {status}\n"
        "زمن الاستجابة: {latency_ms} مل.ثانية",
        {"device": "AP-Floor2", "address": "192.168.88.20", "status": "down",
         "latency_ms": "—"},
    ),
    AlertSpec(
        "loop_detected", "network", "كشف لوب (Loop)",
        "يُرسل عند اكتشاف لوب على منفذ/واجهة (loop probe / HR-LoopDetect).",
        "🔁 <b>كشف لوب</b>\n"
        "الراوتر: {router}\n"
        "الواجهة: {interface}\n"
        "التفاصيل: {details}",
        {"router": "MT-Main", "interface": "ether5", "details": "تكرار MAC على المنفذ"},
    ),
    AlertSpec(
        "device_health", "network", "تتبّع الراوترات والأكسس بوينت",
        "صحة أجهزة الشبكة (هبوط/تعافٍ) — خدمة تتبّع الأجهزة (device_health_alerts).",
        "💓 <b>تتبّع الأجهزة</b>\n"
        "الجهاز: {device} ({device_type})\n"
        "العنوان: <code>{address}</code>\n"
        "الحالة: {status}\n"
        "الراوتر: {router}",
        {"device": "AP-Floor2", "device_type": "access_point",
         "address": "192.168.88.20", "status": "تعافى (up)", "router": "MT-Main"},
    ),
    # ── المال ──────────────────────────────────────────────────────────
    AlertSpec(
        "payment_received", "finance", "دفعة/تحصيل",
        "يُرسل عند تسجيل دفعة من مشترك (collection / payments).",
        "💰 <b>دفعة جديدة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المبلغ: {amount} {currency}\n"
        "بواسطة: {actor}",
        {"username": "ahmad99", "amount": "20.0", "currency": "₪", "actor": "المحصّل"},
        default_enabled=False,
    ),
    # ── الشبكة (إضافات smart_alerts) ───────────────────────────────────
    AlertSpec(
        "router_offline", "network", "راوتر غير متصل",
        "يُرسل عند توقّف راوتر عن دفع بياناته (smart_alerts.sweep_offline، "
        "auto.router.offline). ⚑ يُرسل حاليًا عبر مسار smart_alerts المستقل.",
        "🔌 <b>راوتر غير متصل</b>\n"
        "الراوتر: {router}\n"
        "آخر اتصال: منذ {minutes} دقيقة",
        {"router": "MT-Main", "minutes": "12"},
    ),
    AlertSpec(
        "router_high_traffic", "network", "حركة مرور مرتفعة على راوتر",
        "يُرسل عند تجاوز ذروة السرعة الحدّ (smart_alerts، auto.router.high_traffic).",
        "📈 <b>حركة مرور مرتفعة</b>\n"
        "الراوتر: {router}\n"
        "الذروة: {peak_mbps} م.بت/ث على {interface}",
        {"router": "MT-Main", "peak_mbps": "920", "interface": "ether1"},
        default_enabled=False,
    ),
    AlertSpec(
        "router_high_usage", "network", "استهلاك مرتفع على راوتر",
        "يُرسل عند تجاوز الاستهلاك في النافذة الحدّ (smart_alerts، auto.router.high_usage).",
        "📊 <b>استهلاك مرتفع</b>\n"
        "الراوتر: {router}\n"
        "الاستهلاك: {usage_gb} ج.بايت خلال {window}",
        {"router": "MT-Main", "usage_gb": "350", "window": "اليوم"},
        default_enabled=False,
    ),
    # ── المتجر والموزّعون ──────────────────────────────────────────────
    AlertSpec(
        "store_registration", "store", "تسجيل عميل متجر جديد",
        "يُرسل عند تسجيل مستخدم بطاقات ذاتيًّا (store_alerts.notify_registration).",
        "🛒 <b>تسجيل متجر جديد</b>\n"
        "العميل: {name}\n"
        "الجوال: {mobile}",
        {"name": "سالم", "mobile": "0599000111"},
    ),
    AlertSpec(
        "store_deposit", "store", "طلب إيداع في المتجر",
        "يُرسل عند طلب إيداع رصيد (store_alerts.notify_deposit) — بانتظار التأكيد.",
        "💵 <b>طلب إيداع</b>\n"
        "العميل: {name}\n"
        "المبلغ: {amount} {currency}\n"
        "رقم الطلب: <code>{request_id}</code>",
        {"name": "سالم", "amount": "50.0", "currency": "₪", "request_id": "1042"},
    ),
    AlertSpec(
        "store_withdrawal", "store", "طلب سحب من المتجر",
        "يُرسل عند طلب سحب رصيد (store_alerts.notify_withdrawal) — بانتظار التأكيد.",
        "🏧 <b>طلب سحب</b>\n"
        "العميل: {name}\n"
        "المبلغ: {amount} {currency}\n"
        "رقم الطلب: <code>{request_id}</code>",
        {"name": "سالم", "amount": "30.0", "currency": "₪", "request_id": "1043"},
    ),
    AlertSpec(
        "store_chat", "store", "رسالة دعم في المتجر",
        "يُرسل مرّة عند فتح دور «بانتظار ردّ» في شات المتجر (بداية محادثة أو "
        "عودة الزبون بعد ردّ الموظّف) — لا لكل رسالة (store_alerts.notify_chat).",
        "💬 <b>رسالة دعم (متجر)</b>\n"
        "العميل: {name}",
        {"name": "سالم"},
    ),
    AlertSpec(
        "store_chat_unanswered", "store", "رسالة دعم متأخّرة (بانتظار ردّ)",
        "تذكير دوري: رسالة زبون في شات المتجر بقيت بلا ردّ ولا حالة «مُعالَجة» "
        "أطول من العتبة (alerts.store_chat.unanswered_reminder_minutes، افتراضي "
        "60) — store_chat_reminder_worker. يحمل رابط الردّ.",
        "⏰ <b>رسالة دعم بانتظار ردّ</b>\n"
        "العميل: {name}\n"
        "بانتظار منذ: {since}",
        {"name": "سالم", "since": "ساعة و٢٠ دقيقة"},
    ),
    # ── المال/العمليات (إضافات) ────────────────────────────────────────
    AlertSpec(
        "payment_pending_review", "finance", "دفعة بانتظار المراجعة",
        "يُرسل عند تحويل يدوي/دفعة تنتظر موافقة المدير (payment_review_queue). "
        "⚑ موقع المُطلِق متابعة.",
        "🧾 <b>دفعة بانتظار المراجعة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المبلغ: {amount} {currency}\n"
        "الطريقة: {method}",
        {"username": "ahmad99", "amount": "20.0", "currency": "₪", "method": "تحويل بنكي"},
    ),
    AlertSpec(
        "service_request_new", "finance", "طلب خدمة جديد",
        "يُرسل عند طلب مشترك خدمة مدفوعة (service_requests).",
        "🆕 <b>طلب خدمة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الخدمة: {service}\n"
        "الحالة: {status}",
        {"username": "ahmad99", "service": "IP ثابت", "status": "بانتظار الموافقة"},
    ),
    AlertSpec(
        "service_request_approved", "finance", "اعتماد طلب خدمة",
        "يُرسل عند الموافقة على طلب خدمة مدفوعة (service_requests).",
        "✅ <b>اعتماد طلب خدمة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الخدمة: {service}\n"
        "بواسطة: {actor}",
        {"username": "ahmad99", "service": "IP ثابت", "actor": "المدير"},
    ),
    AlertSpec(
        "card_batch_low", "finance", "حزمة بطاقات شارفت على النفاد",
        "يُرسل عند قرب نفاد حزمة بطاقات (cards). ⚑ موقع المُطلِق متابعة.",
        "🎟️ <b>حزمة بطاقات شارفت على النفاد</b>\n"
        "الحزمة: {batch}\n"
        "المتبقّي: {remaining} من {total}",
        {"batch": "بطاقة 5 ساعات", "remaining": "8", "total": "200"},
        default_enabled=False,
    ),
    # ── الأمان ─────────────────────────────────────────────────────────
    AlertSpec(
        "auto_block_triggered", "security", "حظر تلقائي (fail2ban)",
        "يُرسل عند حظر IP/MAC تلقائيًّا بعد تكرار فشل الدخول "
        "(access_control.register_failed_attempt).",
        "🚫 <b>حظر تلقائي</b>\n"
        "النوع: {block_type}\n"
        "الهدف: <code>{target}</code>\n"
        "السبب: {reason}",
        {"block_type": "IP", "target": "3.3.3.3", "reason": "5 محاولات فاشلة خلال 300ث"},
    ),
    AlertSpec(
        "access_suspended", "security", "تعليق وصول",
        "يُرسل عند تطبيق «تعليق وصول» على نطاق (access_control / access_blocks).",
        "⏸️ <b>تعليق وصول</b>\n"
        "النطاق: {scope}\n"
        "الهدف: <code>{target}</code>\n"
        "المدّة: {duration}",
        {"scope": "مشترك", "target": "ahmad99", "duration": "دائم"},
        default_enabled=False,
    ),
    AlertSpec(
        "mac_clone_detected", "security", "كشف استنساخ MAC",
        "يُرسل عند رصد محاولة دخول من جهاز ببصمة مختلفة بنفس عنوان MAC "
        "(anti_mac_clone). فعّال فقط عند تفعيل الميزة في إعداداتها.",
        "🕵️ <b>كشف استنساخ MAC</b>\n"
        "المشترك: <code>{username}</code>\n"
        "العنوان: <code>{mac}</code>\n"
        "الثقة: {confidence}\n"
        "درجة الخطورة: {score}\n"
        "إشارات متباينة: {diverged}\n"
        "الراوتر: <code>{nas_ip}</code>\n"
        "AP/SSID: <code>{called_station}</code>",
        {"username": "ahmad99", "mac": "AA:BB:CC:DD:EE:FF",
         "confidence": "عالية", "score": "82",
         "diverged": "نوع النظام، ماركة الجهاز",
         "nas_ip": "10.0.0.1", "called_station": "0C:11:22:33:44:55"},
    ),
    AlertSpec(
        "allow_mode_unknown_device", "security", "رفض نمط السماح",
        "يُرسل عند رفض دخول بسبب «نمط السماح» (allow_mode): الجهاز غير "
        "مسجّل ضمن السياسة، أو تجاوز الحدّ في نمط TOFU. الميزة OFF افتراضيًّا.",
        "🛡️ <b>رفض نمط السماح</b>\n"
        "الحساب: <code>{username}</code>\n"
        "العنوان: <code>{mac}</code>\n"
        "النمط: {mode}\n"
        "السبب: {reason}\n"
        "النطاق: {scope} (<code>{scope_id}</code>)",
        {"username": "ahmad99", "mac": "AA:BB:CC:DD:EE:FF",
         "mode": "manual", "reason": "جهاز غير مسجّل (manual)",
         "scope": "حزمة بطاقات", "scope_id": "12"},
        default_enabled=False,
    ),
    # ── النظام (إضافات smart_alerts) ───────────────────────────────────
    AlertSpec(
        "backup_stale", "system", "نسخة احتياطية قديمة",
        "يُرسل عند تقادم آخر نسخة احتياطية (smart_alerts، auto.backup.stale). "
        "⚑ مسار smart_alerts المستقل.",
        "🗄️ <b>نسخة احتياطية قديمة</b>\n"
        "آخر نسخة: منذ {age}\n"
        "الحدّ المسموح: {threshold}",
        {"age": "8 أيام", "threshold": "3 أيام"},
        default_enabled=False,
    ),
    AlertSpec(
        "backup_failed", "system", "فشل نسخة احتياطية",
        "يُرسل عند فشل عملية نسخ احتياطي. ⚑ موقع المُطلِق متابعة.",
        "❌ <b>فشل نسخة احتياطية</b>\n"
        "الوجهة: {target}\n"
        "الخطأ: {error}",
        {"target": "Google Drive", "error": "انتهت صلاحية المصادقة"},
    ),
    AlertSpec(
        "audit_failure", "system", "فشل في سجلّ التدقيق",
        "يُرسل عند رصد فشل/شذوذ في التدقيق (smart_alerts، auto.audit.failure). "
        "⚑ مسار smart_alerts المستقل.",
        "🛡️ <b>تنبيه تدقيق</b>\n"
        "التفاصيل: {details}",
        {"details": "تكرار عمليات حسّاسة فاشلة"},
        default_enabled=False,
    ),
]

_BY_KEY = {a.key: a for a in ALERTS}
_GROUP_LABEL = {g[0]: g[1] for g in GROUPS}


def get_spec(key: str) -> AlertSpec | None:
    return _BY_KEY.get(key)


# ════════════════════════════════════════════════════════════════════════
# التفعيل/التعطيل لكل تنبيه (tenant_settings)
# ════════════════════════════════════════════════════════════════════════
def _toggle_key(key: str) -> str:
    return f"alerts.telegram.enabled.{key}"


def is_enabled(tenant_id: int, key: str) -> bool:
    spec = _BY_KEY.get(key)
    if not spec:
        return False
    default = "1" if spec.default_enabled else "0"
    raw = tenants_repo.get_setting(int(tenant_id), _toggle_key(key), default)
    return str(raw or "").strip().lower() in _TRUE


def set_enabled(tenant_id: int, key: str, enabled: bool, *, by: int = 0) -> None:
    if key not in _BY_KEY:
        return
    tenants_repo.set_setting(int(tenant_id), _toggle_key(key),
                             "1" if enabled else "0", by=by)


# ════════════════════════════════════════════════════════════════════════
# تصيير القالب (آمن: حقل ناقص → «—»)
# ════════════════════════════════════════════════════════════════════════
class _SafeDict(dict):
    def __missing__(self, k):  # noqa: D401
        return "—"


def _instance_label() -> str:
    """اسم النسخة/العميل (للوضوح متعدّد النسخ) — من إعدادات النظام. فارغ إن
    لم يوجد مصدر معقول (أو الاسم الافتراضي العام)."""
    try:
        from ..core.system_config import system_config
        name = str((system_config() or {}).get("system_name") or "").strip()
        return name if name and name.lower() != "hoberadius" else ""
    except Exception:  # noqa: BLE001
        return ""


def _now_local_str() -> str:
    """الوقت المحلّي للمستأجر (متى وقع الحدث)."""
    from datetime import datetime
    try:
        from ..core.system_config import to_local
        return to_local(datetime.utcnow()).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M") + " UTC"


def _footer() -> str:
    """سطر تذييل خفيف يُلحَق بكل قالب: وقت الحدث + اسم النسخة إن وُجد.

    سطر فارغ قبله (\\n\\n) ليفصله بوضوح عن الجسم، ومائل (<i>) للتخفيف."""
    line = "🕐 " + _now_local_str()
    inst = _instance_label()
    if inst:
        line += "  ·  🏷️ " + inst
    return "\n\n<i>" + line + "</i>"


def _format_body(body: str) -> str:
    """Part A — تنسيق مقروء موحّد لكل القوالب (تلجرام HTML mode).

    يحوّل ناتج format_map إلى شكل «متنفّس»:
      • سطر فارغ بعد العنوان (السطر الأول) ليفصله عن الحقول.
      • تغميق تسمية كل حقل: «التسمية: القيمة» → «<b>التسمية:</b> القيمة»
        (الفصل على أوّل «: » فقط، فلا تتأثّر القيم التي تحوي «:» كالأوقات).
      • الحقول التي تبدأ أصلًا بوسم HTML (مثل <code>/<b>) تُترك كما هي.

    يعمل مركزيًّا فيبقى كل القوالب موحّدة دون تعديل كلٍّ منها، ويشمل أيّ قالب
    يُضاف لاحقًا. القيم تبقى كما هي (بما فيها وسوم <code>)."""
    lines = body.split("\n")
    if not lines:
        return body
    title = lines[0].rstrip()
    fields: list[str] = []
    for raw in lines[1:]:
        s = raw.strip()
        if not s:
            continue
        if not s.startswith("<") and ": " in s:
            label, _sep, value = s.partition(": ")
            s = "<b>" + label + ":</b> " + value
        fields.append(s)
    if not fields:
        return title
    return title + "\n\n" + "\n".join(fields)


def render(key: str, context: dict | None = None) -> str:
    spec = _BY_KEY.get(key)
    if not spec:
        return ""
    ctx = _SafeDict({k: ("" if v is None else v) for k, v in (context or {}).items()})
    try:
        body = spec.template.format_map(ctx)
    except Exception:  # noqa: BLE001 — قالب لا يكسر الإرسال أبدًا
        body = spec.template
    # Part A: تنسيق مقروء موحّد (سطر فارغ بعد العنوان + تغميق التسميات).
    # سطر «🔗 للتدخّل» للتنبيهات التي تتطلّب إجراءً فقط (مركزيّ عبر
    # alert_links.action_link؛ الإخباري بلا رابط). ثمّ تذييل الوقت/النسخة.
    try:
        return _format_body(body) + _action_line(key, context) + _footer()
    except Exception:  # noqa: BLE001 — التنسيق لا يكسر الإرسال أبدًا
        return body


def _action_line(key: str, context: dict | None) -> str:
    """سطر رابط التدخّل المباشر — فقط للتنبيهات الإجرائية؛ وإلا فارغ."""
    try:
        from .alert_links import action_link
        url = action_link(key, context or {})
        return ("\n\n🔗 للتدخّل: " + url) if url else ""
    except Exception:  # noqa: BLE001 — الرابط لا يكسر الإرسال أبدًا
        return ""


def preview(key: str) -> str:
    """تصيير القالب ببيانات العيّنة (للمعاينة في الواجهة)."""
    spec = _BY_KEY.get(key)
    return render(key, spec.sample) if spec else ""


# ════════════════════════════════════════════════════════════════════════
# إزالة التكرار (نافذة قصيرة، داخل العملية)
# ════════════════════════════════════════════════════════════════════════
_DEDUP_WINDOW_SEC = 60.0
_dedup_lock = threading.Lock()
_dedup: dict[tuple, float] = {}


def _dedup_ok(tenant_id: int, key: str, dedup_key: str) -> bool:
    """True إذا لم تُرسَل نفس الرسالة خلال النافذة (ويُسجّل الإرسال)."""
    if not dedup_key:
        return True
    now = time.monotonic()
    k = (int(tenant_id), key, dedup_key)
    with _dedup_lock:
        # كنس كسول للمنتهية كي لا ينمو القاموس.
        for kk in [kk for kk, exp in _dedup.items() if exp <= now]:
            _dedup.pop(kk, None)
        if _dedup.get(k, 0) > now:
            return False
        _dedup[k] = now + _DEDUP_WINDOW_SEC
        return True


# ════════════════════════════════════════════════════════════════════════
# الإرسال
# ════════════════════════════════════════════════════════════════════════
def telegram_ready(tenant_id: int) -> bool:
    return tenant_telegram_settings_repo.is_configured(int(tenant_id))


def _send_now(tenant_id: int, text: str) -> tuple[bool, str]:
    try:
        return telegram_notifier.send_to_tenant(int(tenant_id), text)
    except Exception as exc:  # noqa: BLE001 — لا يكسر الخيط/المستدعي أبدًا
        _LOG.warning("admin_alerts: telegram send raised: %s", exc)
        return False, str(exc)[:200]


def dispatch(tenant_id: int, key: str, context: dict | None = None, *,
             dedup_key: str | None = None) -> None:
    """نقطة الإرسال الوحيدة من المُطلِقات. غير حاجبة (خيط خلفي)، لا ترفع
    استثناء، تتحقّق من التفعيل + الضبط + إزالة التكرار قبل الإرسال."""
    try:
        if key not in _BY_KEY:
            return
        tid = int(tenant_id)
        if not is_enabled(tid, key):
            return
        if not telegram_ready(tid):
            return
        if not _dedup_ok(tid, key, dedup_key or ""):
            return
        text = render(key, context)
        if not text:
            return

        def _worker():
            ok, err = _send_now(tid, text)
            if not ok and err:
                _LOG.info("admin_alerts: %s not delivered: %s", key, err)

        threading.Thread(target=_worker, name=f"tg-alert-{key}",
                         daemon=True).start()
    except Exception:  # noqa: BLE001 — التنبيه لا يكسر الطلب أبدًا
        _LOG.warning("admin_alerts.dispatch failed for %s", key, exc_info=True)


def send_test(tenant_id: int, key: str) -> dict:
    """إرسال نموذج (بيانات العيّنة) متزامنًا لزرّ الاختبار. يُعيد
    ``{ok, error, text}``."""
    spec = _BY_KEY.get(key)
    if not spec:
        return {"ok": False, "error": "تنبيه غير معروف.", "text": ""}
    if not telegram_ready(int(tenant_id)):
        return {"ok": False, "error": "بوت تلجرام غير مُفعَّل/مضبوط.",
                "text": preview(key)}
    text = preview(key)
    ok, err = _send_now(int(tenant_id), "🧪 (اختبار)\n" + text)
    return {"ok": ok, "error": err, "text": text}


def test_connection(tenant_id: int) -> dict:
    """زر «اختبار الاتصال» العام — يرسل رسالة تحقّق ويُعيد النتيجة."""
    if not telegram_ready(int(tenant_id)):
        return {"ok": False, "error": "أكمل توكن البوت ومعرّف المحادثة وفعّل الإشعارات."}
    ok, err = _send_now(
        int(tenant_id),
        "✅ <b>اختبار اتصال HobeRadius</b>\n"
        "إذا وصلتك هذه الرسالة فإعدادات بوت التلجرام صحيحة وستصلك التنبيهات.")
    return {"ok": ok, "error": err}


# ════════════════════════════════════════════════════════════════════════
# الجرد للعرض في الواجهة
# ════════════════════════════════════════════════════════════════════════
def catalogue(tenant_id: int) -> list[dict]:
    tid = int(tenant_id)
    out = []
    for spec in ALERTS:
        out.append({
            "key": spec.key,
            "group": spec.group,
            "group_label": _GROUP_LABEL.get(spec.group, spec.group),
            "label": spec.label,
            "description": spec.description,
            "enabled": is_enabled(tid, spec.key),
            "template": spec.template,
            "preview": preview(spec.key),
        })
    return out


__all__ = [
    "AlertSpec", "ALERTS", "GROUPS", "get_spec",
    "is_enabled", "set_enabled", "render", "preview",
    "dispatch", "send_test", "test_connection", "telegram_ready", "catalogue",
]
