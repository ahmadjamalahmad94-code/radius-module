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
from app.i18n_text import N_

import logging
import re
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
    ("routers", "راوترات المشتركين (TR-069)", "router"),
    ("finance", "المال والتحصيل", "money-bill-transfer"),
    ("store", "المتجر والموزّعون", "store"),
    ("security", "الأمان", "shield-halved"),
    ("system", "النظام", "server"),
]

ALERTS: list[AlertSpec] = [
    # ── المشتركون ──────────────────────────────────────────────────────
    AlertSpec(
        "subscriber_new", "subscribers", N_("إضافة مشترك جديد"),
        N_("يُرسل عند إنشاء مشترك جديد (services/users.UsersService.create)."),
        N_("🆕 <b>مشترك جديد</b>\n"
        "الاسم: {full_name}\n"
        "اسم المستخدم: <code>{username}</code>\n"
        "الباقة: {plan}\n"
        "الجوال: {mobile}\n"
        "أضافه: {actor}"),
        {"full_name": N_("أحمد علي"), "username": "ahmad99", "plan": N_("10 ميجا شهري"),
         "mobile": "0599123456", "actor": N_("المدير")},
    ),
    AlertSpec(
        "subscriber_edited", "subscribers", N_("تعديل بيانات مشترك"),
        N_("يُرسل عند تعديل بيانات مشترك قائم (UsersService.update)."),
        N_("✏️ <b>تعديل بيانات مشترك</b>\n"
        "اسم المستخدم: <code>{username}</code>\n"
        "الاسم: {full_name}\n"
        "غُيّر:\n{changed}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "full_name": N_("أحمد علي"),
         "changed": N_("• الباقة: ⁦10 ميجا → 20 ميجا⁩\n"
                    "• الجوال: ⁦0599123456 → 0598765432⁩"),
         "actor": N_("المدير")},
    ),
    AlertSpec(
        "loan_granted", "subscribers", N_("سلفة وقت"),
        N_("يُرسل عند منح سلفة وقت — من البوابة (customer_portals.submit_loan_request) "
        "أو من الإدارة (accounting.create_loan)."),
        N_("💳 <b>سلفة وقت</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المدة: {duration}\n"
        "القيمة: {amount}\n"
        "الحالة: {status}\n"
        "بواسطة: {actor}\n"
        "السبب: {reason}"),
        {"username": "ahmad99", "duration": N_("يومان (2880 دقيقة)"),
         "amount": "75.00 ₪", "status": N_("مُسجَّلة (دين)"), "actor": N_("المدير"),
         "reason": N_("طلب من البوابة")},
    ),
    AlertSpec(
        "time_added", "subscribers", N_("إضافة/تمديد وقت"),
        N_("يُرسل عند إضافة/تمديد وقت لمشترك من الإدارة (users.extend_time بأيّ نمط: "
        "«مجاني» أو «مدفوع» أو «على الدين» — النوع والمبلغ في «النوع»)."),
        N_("⏱️ <b>إضافة وقت</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الوقت المضاف: {duration}\n"
        "تاريخ الانتهاء الجديد: {new_expiry}\n"
        "النوع: {kind}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "duration": N_("يومان (2880 دقيقة)"),
         "new_expiry": "2026-07-01 12:00", "kind": N_("مجاني"), "actor": N_("المدير")},
    ),
    AlertSpec(
        "auto_renew", "subscribers", N_("تجديد تلقائي"),
        N_("يُرسل عند كلّ محاولة «تجديد تلقائي» لمشترك عند انتهاء فترته "
        "(services/plan_lifecycle.renew_due) — بالنمط والنتيجة: تمّ، أو لم يُجدَّد "
        "لأنّ الرصيد لا يكفي، أو فشل."),
        N_("🔁 <b>تجديد تلقائي</b>\n"
        "المشترك: <code>{username}</code>\n"
        "النمط: {mode}\n"
        "النتيجة: {result}\n"
        "المبلغ: {amount}\n"
        "تاريخ الانتهاء الجديد: {new_expiry}\n"
        "تفاصيل: {details}"),
        {"username": "ahmad99", "mode": N_("خصم من الرصيد المتاح"),
         "result": N_("تمّ التجديد"), "amount": "75.00 ₪",
         "new_expiry": "2026-07-01 12:00", "details": "—"},
    ),
    AlertSpec(
        "credit_added", "subscribers", N_("إضافة رصيد"),
        N_("يُرسل عند إضافة رصيد نقديّ لمحفظة مشترك (users.add_cash_balance)."),
        N_("💵 <b>إضافة رصيد</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المبلغ: {amount}\n"
        "الرصيد الجديد: {new_balance}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "amount": "50.00 ₪",
         "new_balance": "120.00 ₪", "actor": N_("المدير")},
    ),
    AlertSpec(
        "quota_added", "subscribers", N_("إضافة كوتا"),
        N_("يُرسل عند إضافة كوتا (سعة بيانات) لمشترك (users.add_quota)."),
        N_("📦 <b>إضافة كوتا</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الكوتا المضافة: {quota}\n"
        "الإجمالي الجديد: {new_total}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "quota": N_("5120 م.ب"),
         "new_total": N_("15360 م.ب"), "actor": N_("المدير")},
    ),
    AlertSpec(
        "quota_restored", "subscribers", N_("استعادة كوتا"),
        N_("يُرسل عند استعادة/تصفير كوتا مشترك (users.reset_daily_quota)."),
        N_("♻️ <b>استعادة كوتا</b>\n"
        "المشترك: <code>{username}</code>\n"
        "التفاصيل: {detail}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "detail": N_("تصفير الاستهلاك اليومي"),
         "actor": N_("المدير")},
    ),
    AlertSpec(
        "speed_boost", "subscribers", N_("رفع سرعة مؤقت"),
        N_("يُرسل عند تطبيق سرعة مؤقتة على مشترك (temp_speed.apply_temp_speed)."),
        N_("🚀 <b>رفع سرعة مؤقت</b>\n"
        "المشترك: <code>{username}</code>\n"
        "السرعة: {down}↓ / {up}↑ كbps\n"
        "المدة: {duration} دقيقة\n"
        "تنتهي: {ends_at}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "down": "20480", "up": "10240",
         "duration": "60", "ends_at": "2026-06-16 21:00", "actor": N_("المدير")},
    ),
    AlertSpec(
        "speed_boost_ended", "subscribers", N_("انتهاء السرعة المؤقتة"),
        N_("يُرسل عند انتهاء أو إلغاء سرعة مؤقتة ورجوع الحساب لسرعته (temp_speed)."),
        N_("⏱️ <b>انتهت السرعة المؤقتة</b>\n"
        "الحساب: <code>{username}</code>\n"
        "رجعت السرعة إلى: {rate}\n"
        "تطبيق مباشر على الراوتر: {applied}"),
        {"username": "79876297", "rate": "2048k/2048k", "applied": N_("نعم")},
    ),
    AlertSpec(
        "quota_exhausted", "subscribers", N_("انتهاء كوتة"),
        N_("يُرسل عند رفض الدخول بسبب نفاد الكوتة (policy_engine) أو فرض الانتهاء."),
        N_("📉 <b>انتهاء كوتة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المستهلَك: {used_mb} م.بايت من {quota_mb}\n"
        "الباقة: {plan}"),
        {"username": "ahmad99", "used_mb": "10240", "quota_mb": "10240",
         "plan": N_("10 جيجا شهري")},
        default_enabled=False,
    ),
    AlertSpec(
        "subscriber_expired", "subscribers", N_("انتهاء اشتراك"),
        N_("يُرسل عند انتهاء صلاحية اشتراك (expiry_enforcer)."),
        N_("⏳ <b>انتهاء اشتراك</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الاسم: {full_name}\n"
        "انتهى: {expired_at}"),
        {"username": "ahmad99", "full_name": N_("أحمد علي"), "expired_at": "2026-06-16"},
        default_enabled=False,
    ),
    AlertSpec(
        "portal_message", "subscribers", N_("رسالة من بوابة المشترك"),
        N_("يُرسل عند إرسال مشترك رسالة/شكوى من بوابته تحتاج ردًّا "
        "(customer_portals.submit_renewal_request، نوع support). يحمل رابط الردّ."),
        N_("📨 <b>رسالة من بوابة المشترك</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الرسالة: {message}"),
        {"username": "ahmad99", "message": N_("الإنترنت بطيء منذ الصباح، أرجو المتابعة.")},
    ),
    # ── الشبكة والمايكروتيك ────────────────────────────────────────────
    AlertSpec(
        "mikrotik_connection_problem", "network", N_("مشاكل اتصال المايكروتيك"),
        N_("يُرسل عند تعذّر الاتصال بجهاز المايكروتيك (API/الوصول)."),
        N_("🛑 <b>مشكلة اتصال مايكروتيك</b>\n"
        "الجهاز: {router}\n"
        "العنوان: <code>{address}</code>\n"
        "الخطأ: {error}"),
        {"router": "MT-Main", "address": "10.0.0.1", "error": N_("انتهت المهلة (timeout)")},
    ),
    AlertSpec(
        "network_disconnect", "network", N_("فصل شبكة / بنج سيّئ"),
        N_("يُرسل عند فصل جهاز شبكة أو ارتفاع زمن الاستجابة (device-health poller)."),
        N_("📡 <b>فصل/بنج سيّئ</b>\n"
        "الجهاز: {device}\n"
        "العنوان: <code>{address}</code>\n"
        "الحالة: {status}\n"
        "زمن الاستجابة: {latency_ms} مل.ثانية"),
        {"device": "AP-Floor2", "address": "192.168.88.20", "status": "down",
         "latency_ms": "—"},
    ),
    AlertSpec(
        "loop_detected", "network", N_("كشف لوب (Loop)"),
        N_("يُرسل عند اكتشاف لوب على منفذ/واجهة (loop probe / HR-LoopDetect)."),
        N_("🔁 <b>كشف لوب</b>\n"
        "الراوتر: {router}\n"
        "الواجهة: {interface}\n"
        "التفاصيل: {details}"),
        {"router": "MT-Main", "interface": "ether5", "details": N_("تكرار MAC على المنفذ")},
    ),
    AlertSpec(
        "device_health", "network", N_("تتبّع الراوترات والأكسس بوينت"),
        N_("صحة أجهزة الشبكة (هبوط/تعافٍ) — خدمة تتبّع الأجهزة (device_health_alerts)."),
        N_("💓 <b>تتبّع الأجهزة</b>\n"
        "الجهاز: {device} ({device_type})\n"
        "العنوان: <code>{address}</code>\n"
        "الحالة: {status}\n"
        "الراوتر: {router}"),
        {"device": "AP-Floor2", "device_type": "access_point",
         "address": "192.168.88.20", "status": N_("تعافى (up)"), "router": "MT-Main"},
    ),
    # ── راوترات المشتركين (TR-069، وحدة «المعمل») ──────────────────────
    AlertSpec(
        "router_device_offline", "routers", N_("راوتر مشترك انفصل عن ACS"),
        N_("يُرسل عند توقّف راوتر مشترك عن التبليغ لـ GenieACS أطول من العتبة "
        "(tr069/alerts، الاكتشاف من مزامنة الأجهزة)."),
        N_("🔴 <b>راوتر مشترك مفصول</b>\n"
        "المشترك: <code>{user}</code>\n"
        "الطراز: {model}\n"
        "السيريال: <code>{serial}</code>\n"
        "مفصول منذ: {minutes} دقيقة"),
        {"user": "ahmad-home", "model": "MikroTik hAP", "serial": "ABC123",
         "minutes": "12"},
    ),
    AlertSpec(
        "router_device_online", "routers", N_("عودة راوتر مشترك"),
        N_("يُرسل عند عودة راوتر مشترك للتبليغ بعد انفصال (tr069/alerts)."),
        N_("✅ <b>عاد راوتر مشترك</b>\n"
        "المشترك: <code>{user}</code>\n"
        "الطراز: {model}\n"
        "السيريال: <code>{serial}</code>"),
        {"user": "ahmad-home", "model": "MikroTik hAP", "serial": "ABC123",
         "minutes": "—"},
    ),
    AlertSpec(
        "router_device_no_internet", "routers", N_("راوتر مشترك بلا إنترنت"),
        N_("يُرسل عندما يكون الراوتر متصلًا بـ ACS لكن حالة WAN/PPP «مفصولة» — أي "
        "لا إنترنت خلفه (tr069/alerts). عطل مختلف عن انفصال الراوتر نفسه."),
        N_("🌐 <b>راوتر مشترك بلا إنترنت</b>\n"
        "المشترك: <code>{user}</code>\n"
        "الطراز: {model}\n"
        "السيريال: <code>{serial}</code>\n"
        "الراوتر يعمل لكن WAN/PPP بلا اتصال."),
        {"user": "ahmad-home", "model": "MikroTik hAP", "serial": "ABC123",
         "minutes": "—"},
    ),
    AlertSpec(
        "router_device_internet_back", "routers", N_("عودة إنترنت راوتر مشترك"),
        N_("يُرسل عند عودة اتصال WAN/PPP خلف راوتر مشترك بعد انقطاع (tr069/alerts)."),
        N_("🟢 <b>عاد إنترنت راوتر مشترك</b>\n"
        "المشترك: <code>{user}</code>\n"
        "الطراز: {model}\n"
        "السيريال: <code>{serial}</code>"),
        {"user": "ahmad-home", "model": "MikroTik hAP", "serial": "ABC123",
         "minutes": "—"},
    ),
    # ── المال ──────────────────────────────────────────────────────────
    AlertSpec(
        "payment_received", "finance", N_("دفعة/تحصيل"),
        N_("يُرسل عند تسجيل دفعة من مشترك (accounting.create_payment / collection)."),
        N_("💰 <b>دفعة جديدة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المبلغ: {amount}\n"
        "الطريقة: {method}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "amount": "20.00 ₪", "method": N_("نقدًا"),
         "actor": N_("المحصّل")},
    ),
    # ── الشبكة (إضافات smart_alerts) ───────────────────────────────────
    AlertSpec(
        "router_offline", "network", N_("راوتر غير متصل"),
        N_("يُرسل عند توقّف راوتر عن دفع بياناته (smart_alerts.sweep_offline، "
        "auto.router.offline). ⚑ يُرسل حاليًا عبر مسار smart_alerts المستقل."),
        N_("🔌 <b>راوتر غير متصل</b>\n"
        "الراوتر: {router}\n"
        "آخر اتصال: منذ {minutes} دقيقة"),
        {"router": "MT-Main", "minutes": "12"},
    ),
    AlertSpec(
        "router_high_traffic", "network", N_("حركة مرور مرتفعة على راوتر"),
        N_("يُرسل عند تجاوز ذروة السرعة الحدّ (smart_alerts، auto.router.high_traffic)."),
        N_("📈 <b>حركة مرور مرتفعة</b>\n"
        "الراوتر: {router}\n"
        "الذروة: {peak_mbps} م.بت/ث على {interface}"),
        {"router": "MT-Main", "peak_mbps": "920", "interface": "ether1"},
        default_enabled=False,
    ),
    AlertSpec(
        "router_high_usage", "network", N_("استهلاك مرتفع على راوتر"),
        N_("يُرسل عند تجاوز الاستهلاك في النافذة الحدّ (smart_alerts، auto.router.high_usage)."),
        N_("📊 <b>استهلاك مرتفع</b>\n"
        "الراوتر: {router}\n"
        "الاستهلاك: {usage_gb} ج.بايت خلال {window}"),
        {"router": "MT-Main", "usage_gb": "350", "window": N_("اليوم")},
        default_enabled=False,
    ),
    # ── المتجر والموزّعون ──────────────────────────────────────────────
    AlertSpec(
        "store_registration", "store", N_("تسجيل عميل متجر جديد"),
        N_("يُرسل عند تسجيل مستخدم بطاقات ذاتيًّا (store_alerts.notify_registration)."),
        N_("🛒 <b>تسجيل متجر جديد</b>\n"
        "العميل: {name}\n"
        "الجوال: {mobile}"),
        {"name": N_("سالم"), "mobile": "0599000111"},
    ),
    AlertSpec(
        "store_deposit", "store", N_("طلب إيداع في المتجر"),
        N_("يُرسل عند طلب إيداع رصيد (store_alerts.notify_deposit) — بانتظار التأكيد."),
        N_("💵 <b>طلب إيداع</b>\n"
        "العميل: {name}\n"
        "المبلغ: {amount} {currency}\n"
        "رقم الطلب: <code>{request_id}</code>"),
        {"name": N_("سالم"), "amount": "50.0", "currency": "₪", "request_id": "1042"},
    ),
    AlertSpec(
        "store_withdrawal", "store", N_("طلب سحب من المتجر"),
        N_("يُرسل عند طلب سحب رصيد (store_alerts.notify_withdrawal) — بانتظار التأكيد."),
        N_("🏧 <b>طلب سحب</b>\n"
        "العميل: {name}\n"
        "المبلغ: {amount} {currency}\n"
        "رقم الطلب: <code>{request_id}</code>"),
        {"name": N_("سالم"), "amount": "30.0", "currency": "₪", "request_id": "1043"},
    ),
    AlertSpec(
        "store_chat", "store", N_("رسالة دعم في المتجر"),
        N_("يُرسل مرّة عند فتح دور «بانتظار ردّ» في شات المتجر (بداية محادثة أو "
        "عودة الزبون بعد ردّ الموظّف) — لا لكل رسالة (store_alerts.notify_chat)."),
        N_("💬 <b>رسالة دعم (متجر)</b>\n"
        "العميل: {name}"),
        {"name": N_("سالم")},
    ),
    AlertSpec(
        "store_chat_unanswered", "store", N_("رسالة دعم متأخّرة (بانتظار ردّ)"),
        N_("تذكير دوري: رسالة زبون في شات المتجر بقيت بلا ردّ ولا حالة «مُعالَجة» "
        "أطول من العتبة (alerts.store_chat.unanswered_reminder_minutes، افتراضي "
        "60) — store_chat_reminder_worker. يحمل رابط الردّ."),
        N_("⏰ <b>رسالة دعم بانتظار ردّ</b>\n"
        "العميل: {name}\n"
        "بانتظار منذ: {since}"),
        {"name": N_("سالم"), "since": N_("ساعة و٢٠ دقيقة")},
    ),
    # ── المال/العمليات (إضافات) ────────────────────────────────────────
    AlertSpec(
        "payment_pending_review", "finance", N_("دفعة بانتظار المراجعة"),
        N_("يُرسل عند تحويل يدوي/دفعة تنتظر موافقة المدير (payment_review_queue). "
        "⚑ موقع المُطلِق متابعة."),
        N_("🧾 <b>دفعة بانتظار المراجعة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "المبلغ: {amount} {currency}\n"
        "الطريقة: {method}"),
        {"username": "ahmad99", "amount": "20.0", "currency": "₪", "method": N_("تحويل بنكي")},
    ),
    AlertSpec(
        "service_request_new", "finance", N_("طلب خدمة جديد"),
        N_("يُرسل عند طلب مشترك خدمة مدفوعة (service_requests)."),
        N_("🆕 <b>طلب خدمة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الخدمة: {service}\n"
        "الحالة: {status}"),
        {"username": "ahmad99", "service": N_("IP ثابت"), "status": N_("بانتظار الموافقة")},
    ),
    AlertSpec(
        "service_request_approved", "finance", N_("اعتماد طلب خدمة"),
        N_("يُرسل عند الموافقة على طلب خدمة مدفوعة (service_requests)."),
        N_("✅ <b>اعتماد طلب خدمة</b>\n"
        "المشترك: <code>{username}</code>\n"
        "الخدمة: {service}\n"
        "بواسطة: {actor}"),
        {"username": "ahmad99", "service": N_("IP ثابت"), "actor": N_("المدير")},
    ),
    AlertSpec(
        "card_batch_low", "finance", N_("حزمة بطاقات شارفت على النفاد"),
        N_("يُرسل عند قرب نفاد حزمة بطاقات (cards). ⚑ موقع المُطلِق متابعة."),
        N_("🎟️ <b>حزمة بطاقات شارفت على النفاد</b>\n"
        "الحزمة: {batch}\n"
        "المتبقّي: {remaining} من {total}"),
        {"batch": N_("بطاقة 5 ساعات"), "remaining": "8", "total": "200"},
        default_enabled=False,
    ),
    # ── الأمان ─────────────────────────────────────────────────────────
    AlertSpec(
        "auto_block_triggered", "security", N_("حظر تلقائي (fail2ban)"),
        N_("يُرسل عند حظر IP/MAC تلقائيًّا بعد تكرار فشل الدخول "
        "(access_control.register_failed_attempt)."),
        N_("🚫 <b>حظر تلقائي</b>\n"
        "النوع: {block_type}\n"
        "الهدف: <code>{target}</code>\n"
        "السبب: {reason}"),
        {"block_type": "IP", "target": "3.3.3.3", "reason": N_("5 محاولات فاشلة خلال 300ث")},
    ),
    AlertSpec(
        "access_suspended", "security", N_("تعليق وصول"),
        N_("يُرسل عند تطبيق «تعليق وصول» على نطاق (access_control / access_blocks)."),
        N_("⏸️ <b>تعليق وصول</b>\n"
        "النطاق: {scope}\n"
        "الهدف: <code>{target}</code>\n"
        "المدّة: {duration}"),
        {"scope": N_("مشترك"), "target": "ahmad99", "duration": N_("دائم")},
        default_enabled=False,
    ),
    AlertSpec(
        "mac_clone_detected", "security", N_("كشف استنساخ MAC"),
        N_("يُرسل عند رصد محاولة دخول من جهاز ببصمة مختلفة بنفس عنوان MAC "
        "(anti_mac_clone). فعّال فقط عند تفعيل الميزة في إعداداتها."),
        N_("🕵️ <b>كشف استنساخ MAC</b>\n"
        "المشترك: <code>{username}</code>\n"
        "العنوان: <code>{mac}</code>\n"
        "الثقة: {confidence}\n"
        "درجة الخطورة: {score}\n"
        "إشارات متباينة: {diverged}\n"
        "الراوتر: <code>{nas_ip}</code>\n"
        "AP/SSID: <code>{called_station}</code>"),
        {"username": "ahmad99", "mac": "AA:BB:CC:DD:EE:FF",
         "confidence": N_("عالية"), "score": "82",
         "diverged": N_("نوع النظام، ماركة الجهاز"),
         "nas_ip": "10.0.0.1", "called_station": "0C:11:22:33:44:55"},
    ),
    AlertSpec(
        "allow_mode_unknown_device", "security", N_("رفض نمط السماح"),
        N_("يُرسل عند رفض دخول بسبب «نمط السماح» (allow_mode): الجهاز غير "
        "مسجّل ضمن السياسة، أو تجاوز الحدّ في نمط TOFU. الميزة OFF افتراضيًّا."),
        N_("🛡️ <b>رفض نمط السماح</b>\n"
        "الحساب: <code>{username}</code>\n"
        "العنوان: <code>{mac}</code>\n"
        "النمط: {mode}\n"
        "السبب: {reason}\n"
        "النطاق: {scope} (<code>{scope_id}</code>)"),
        {"username": "ahmad99", "mac": "AA:BB:CC:DD:EE:FF",
         "mode": "manual", "reason": N_("جهاز غير مسجّل (manual)"),
         "scope": N_("حزمة بطاقات"), "scope_id": "12"},
        default_enabled=False,
    ),
    # ── النظام (إضافات smart_alerts) ───────────────────────────────────
    AlertSpec(
        "backup_stale", "system", N_("نسخة احتياطية قديمة"),
        N_("يُرسل عند تقادم آخر نسخة احتياطية (smart_alerts، auto.backup.stale). "
        "⚑ مسار smart_alerts المستقل."),
        N_("🗄️ <b>نسخة احتياطية قديمة</b>\n"
        "آخر نسخة: منذ {age}\n"
        "الحدّ المسموح: {threshold}"),
        {"age": N_("8 أيام"), "threshold": N_("3 أيام")},
        default_enabled=False,
    ),
    AlertSpec(
        "backup_failed", "system", N_("فشل نسخة احتياطية"),
        N_("يُرسل عند فشل عملية نسخ احتياطي. ⚑ موقع المُطلِق متابعة."),
        N_("❌ <b>فشل نسخة احتياطية</b>\n"
        "الوجهة: {target}\n"
        "الخطأ: {error}"),
        {"target": "Google Drive", "error": N_("انتهت صلاحية المصادقة")},
    ),
    AlertSpec(
        "audit_failure", "system", N_("فشل في سجلّ التدقيق"),
        N_("يُرسل عند رصد فشل/شذوذ في التدقيق (smart_alerts، auto.audit.failure). "
        "⚑ مسار smart_alerts المستقل."),
        N_("🛡️ <b>تنبيه تدقيق</b>\n"
        "التفاصيل: {details}"),
        {"details": N_("تكرار عمليات حسّاسة فاشلة")},
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
# نموذج القنوات لكل حدث (الإشعارات الموحّدة) — Phase 1
# ════════════════════════════════════════════════════════════════════════
# الجرس (bell) دائمًا مُفعَّل لكل حدث إدارة (المركز الموحّد = مصدر الحقيقة).
# تلجرام يُسلَّم عبر المُرسِل القانوني.
# «دفع الجوال» (push): يُحوَّل طلب الدفع للوحة التراخيص (سلطة FCM المركزيّة)
#   فتُرسله لأجهزة العميل (أندرويد) — مُنفَّذ حين تُفعَّل القناة لهذا الحدث.
# «ويندوز» (windows): تطبيق ويندوز سطح المكتب يَستطلع مركز الإشعارات الموحّد
#   (panel_notifications عبر /api/v1/notifications) — يَصِله الحدث عبر نفس
#   كتابة الجرس (الدائمة)، فالقناة مُسلَّمة عبر مسار الاستطلاع القائم.
# واتساب/SMS مرحلة لاحقة (تُحفظ تفضيلاتها لكن لا تُسلَّم بعد لقناة الإدارة).
CHANNELS: tuple[str, ...] = ("bell", "telegram", "whatsapp", "sms", "push", "windows")
#: القنوات التي تُسلَّم فعليًّا (bell+telegram+push+windows). البقيّة stubs.
DELIVERABLE_CHANNELS: frozenset[str] = frozenset(
    {"bell", "telegram", "push", "windows"})
#: قنوات مرحلة لاحقة (تُحفظ تفضيلاتها لكن لا تُسلَّم بعد لقناة الإدارة).
DEFERRED_CHANNELS: frozenset[str] = frozenset({"whatsapp", "sms"})

# تعيين مجموعة الحدث → (نوع إشعار المركز، الخطورة) للجرس.
_GROUP_NOTIFY: dict[str, tuple[str, str]] = {
    "subscribers": ("subscription", "info"),
    "network":     ("system", "warning"),
    "routers":     ("system", "warning"),
    "finance":     ("billing", "info"),
    "store":       ("service", "info"),
    "security":    ("system", "warning"),
    "system":      ("system", "info"),
}


def _channels_key(key: str) -> str:
    return f"alerts.channels.{key}"


def channels_for(tenant_id: int, key: str) -> set[str]:
    """مجموعة القنوات المُفعَّلة لهذا الحدث. الجرس دائمًا ضمنها.

    التوافق الخلفي: إن لم تُضبط قنوات صراحةً، نشتقّها من مفتاح تلجرام القديم
    ``alerts.telegram.enabled.<key>`` (الافتراضي من السجلّ) + الجرس."""
    spec = _BY_KEY.get(key)
    if not spec:
        return {"bell"}
    tid = int(tenant_id)
    raw = str(tenants_repo.get_setting(tid, _channels_key(key), "") or "").strip()
    if raw:
        chans = {c.strip() for c in raw.split(",") if c.strip() in CHANNELS}
    else:
        chans = {"bell"}
        if is_enabled(tid, key):
            chans.add("telegram")
    chans.add("bell")  # الجرس لا يُطفأ — المركز الموحّد
    return chans


def set_channels(tenant_id: int, key: str, channels, *, by: int = 0) -> set[str]:
    """يضبط قنوات حدث. الجرس يُضاف دائمًا. يُبقي مفتاح تلجرام القديم متّسقًا."""
    if key not in _BY_KEY:
        return {"bell"}
    tid = int(tenant_id)
    chans = {str(c).strip() for c in (channels or []) if str(c).strip() in CHANNELS}
    chans.add("bell")
    tenants_repo.set_setting(tid, _channels_key(key), ",".join(sorted(chans)), by=by)
    # حافظ على تطابق العلم القديم (يستخدمه أي مستهلك متبقٍّ).
    set_enabled(tid, key, "telegram" in chans, by=by)
    return chans


def set_telegram(tenant_id: int, key: str, enabled: bool, *, by: int = 0) -> None:
    """The per-event Telegram switch (app + old web page).

    Parity-b: once the channel matrix has stored ``alerts.channels.<key>``,
    ``dispatch`` reads ONLY that — writing just the legacy flag left Telegram
    sending while the switch showed OFF. Keep both in step."""
    if key not in _BY_KEY:
        return
    tid = int(tenant_id)
    if str(tenants_repo.get_setting(tid, _channels_key(key), "") or "").strip():
        chans = channels_for(tid, key)
        if enabled:
            chans.add("telegram")
        else:
            chans.discard("telegram")
        set_channels(tid, key, chans, by=by)
    else:
        set_enabled(tid, key, enabled, by=by)


def _strip_html(text: str) -> str:
    """نصّ عادي مختصر للجرس (يزيل وسوم HTML الخفيفة + سطر التذييل/الرابط)."""
    import re
    # خذ ما قبل التذييل (🕐) ورابط التدخّل (🔗).
    for marker in ("\n\n🔗", "\n\n<i>🕐", "\n\n🕐"):
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def _notify_bell(tenant_id: int, spec: "AlertSpec", context: dict | None,
                 *, push: bool = False) -> None:
    """يكتب الحدث في المركز الموحّد (panel_notifications) — دائمًا لكل حدث.

    ``push``: حين تُفعَّل قناة «دفع الجوال» لهذا الحدث، نَطلب من notify() تحويل
    طلب الدفع للوحة التراخيص (سلطة FCM المركزيّة) فتُرسله لأجهزة العميل؛ وإلّا
    نُمرّر push=False فتُكتب رسالة الجرس بلا دفع (toggle لكلّ حدث).

    لا يكسر الإرسال أبدًا (notifications.notify محصّن أصلًا)."""
    try:
        from . import notifications as _notif
        from .alert_links import action_link
        ntype, severity = _GROUP_NOTIFY.get(spec.group, ("system", "info"))
        body = _strip_html(render(spec.key, context))
        # العنوان = تسمية الحدث؛ الجسم = النصّ المُصاغ بلا التذييل/الرابط.
        link = ""
        try:
            url = action_link(spec.key, context or {})
            if url and url.startswith("/"):  # روابط داخليّة فقط (لا تسرّب)
                link = url
        except Exception:  # noqa: BLE001
            link = ""
        # fix3 (F01 F9): the bell is per-admin — record WHO the event is about
        # (subscriber → owner scope), its group and the acting admin.
        sub_name = ""
        if spec.group in ("subscribers", "finance"):
            sub_name = str((context or {}).get("username") or "").strip()
            if sub_name in ("—", "-"):
                sub_name = ""
        try:
            from .subscriber_scope import request_admin_id
            actor_id = request_admin_id()
        except Exception:  # noqa: BLE001
            actor_id = None
        _notif.notify(
            int(tenant_id), type=ntype, severity=severity,
            title=spec.label, body=body, link=link,
            subscriber_username=sub_name, audience=spec.group,
            actor_admin_id=actor_id,
            source="local", source_ref=f"alert:{spec.key}", push=push,
            # MT90 — مفتاح الحدث نفسه هو مفتاح صوته. صفحة الأصوات مُشتقّة من
            # هذا السجلّ، فكلّ تنبيهٍ يُضاف هنا يظهر هناك بلا خطوةٍ إضافيّة.
            event_key=spec.key)
    except Exception:  # noqa: BLE001 — الجرس لا يكسر الإرسال أبدًا
        _LOG.debug("admin_alerts: bell write failed for %s", spec.key, exc_info=True)


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
    # F08-L: الفاعل الخام («api-token:78») → «تطبيق — <المدير>» في كلّ القنوات
    # (الجرس/الدفع/تلجرام) — لا رمز داخليّ في نصّ يقرؤه المالك.
    if ctx.get("actor"):
        try:
            from .actor_names import actor_display
            ctx["actor"] = actor_display(ctx["actor"])
        except Exception:  # noqa: BLE001 — التنسيق لا يكسر الإرسال أبدًا
            pass
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
        return (N_("\n\n🔗 للتدخّل: ") + url) if url else ""
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
    # Shared across processes (leftover wave): the same event raised by the
    # worker process and by a panel process within the window is sent ONCE.
    try:
        from ..db import shared_state
        return shared_state.kv_put_if_absent(
            "alert_dedup", f"{int(tenant_id)}|{key}|{dedup_key}", 1,
            ttl=_DEDUP_WINDOW_SEC)
    except Exception:  # noqa: BLE001 — DB hiccup: the per-process window below
        pass
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
    """نقطة الإرسال الوحيدة (المحرّك الموحّد). غير حاجبة، لا ترفع استثناء.

    لكل حدث إدارة:
      1) الجرس/المركز (panel_notifications) — دائمًا (المركز الموحّد). هذه
         الكتابة هي أيضًا ما يَستطلعه تطبيق «ويندوز» سطح المكتب عبر
         /api/v1/notifications، فقناة «ويندوز» مُسلَّمة عبر مسار الاستطلاع.
      2) «دفع الجوال» (push) — إن فُعِّلت القناة لهذا الحدث، نَطلب من notify()
         تحويل طلب الدفع للوحة التراخيص (سلطة FCM المركزيّة) فتُرسله لأجهزة
         العميل (أندرويد). fire-and-forget داخل notify() (لا يَحجب).
      3) تلجرام — عبر المُرسِل القانوني، إن كانت القناة مُفعَّلة + البوت مضبوط.
      4) واتساب/SMS — مرحلة لاحقة (تفضيلاتها محفوظة، غير مُسلَّمة بعد).
    إزالة التكرار تحمي القنوات معًا."""
    try:
        spec = _BY_KEY.get(key)
        if not spec:
            return
        tid = int(tenant_id)
        if not _dedup_ok(tid, key, dedup_key or ""):
            return
        chans = channels_for(tid, key)

        # 1+2) الجرس — دائمًا (المركز الموحّد + مصدر استطلاع تطبيق ويندوز)؛
        # ودفع الجوال للوحة التراخيص حين تُفعَّل قناة «push» لهذا الحدث فقط.
        _notify_bell(tid, spec, context, push=("push" in chans))

        # 3) تلجرام — عبر المُرسِل القانوني فقط، إن فُعِّلت القناة والبوت مضبوط.
        if "telegram" in chans and telegram_ready(tid):
            text = render(key, context)
            if text:
                def _worker():
                    ok, err = _send_now(tid, text)
                    if not ok and err:
                        _LOG.info("admin_alerts: %s not delivered: %s", key, err)
                threading.Thread(target=_worker, name=f"tg-alert-{key}",
                                 daemon=True).start()
        # 4) واتساب/SMS — stubs (لا تسليم بعد لقناة الإدارة، التفضيل محفوظ).
    except Exception:  # noqa: BLE001 — التنبيه لا يكسر الطلب أبدًا
        _LOG.warning("admin_alerts.dispatch failed for %s", key, exc_info=True)


# سبب فشل/حالة الدفع بالعربيّة (للعرض الواضح بدل رمز إنجليزيّ صامت).
_PUSH_REASON_AR: dict[str, str] = {
    "sent": N_("تم الإرسال"),
    "ok": N_("تم الإرسال"),
    "no_tokens": N_("لا أجهزة مُسجَّلة — افتح التطبيق على الجوّال، سجّل الدخول، واسمح بالإشعارات."),
    "fcm_disabled": N_("الدفع غير مُفعَّل مركزيًّا (لم يُرفَع مفتاح Firebase في لوحة التراخيص)."),
    "https_required": N_("ربط لوحة التراخيص غير مُهيّأ (يلزم رابط HTTPS)."),
    "disabled": N_("ربط لوحة التراخيص غير مُفعَّل."),
    "config_missing": N_("تهيئة ربط لوحة التراخيص ناقصة."),
    "unavailable": N_("تعذّر الوصول إلى لوحة التراخيص."),
    "timeout": N_("انتهت مهلة الاتصال بلوحة التراخيص."),
    "forward_error": N_("تعذّر تحويل الدفع إلى لوحة التراخيص."),
}


def _push_reason_ar(reason: str) -> str:
    r = str(reason or "").strip()
    return _PUSH_REASON_AR.get(r, r or N_("تعذّر الدفع."))


def send_test(tenant_id: int, key: str) -> dict:
    """يُرسل نموذجًا (بيانات العيّنة) عبر **نفس القنوات المُفعَّلة** لهذا الحدث
    (كما يفعل ``dispatch``) لا تلجرام وحده — كي يَختبر المالك السلسلة الكاملة
    لكلّ قناة فعليًّا من زرّ الاختبار:

      • «دفع الجوال» (push): تحويل **متزامن** للوحة التراخيص (FCM المركزيّة)
        عبر ``notifications.send_test_push`` فيُرجع الحالة/العدّ (sent/failed/
        devices) ويُفصِح عن السبب (لا أجهزة · غير مُفعَّل · الجسر غير مهيّأ)
        بدل نجاح صامت.
      • «ويندوز» (windows): يُكتب صفّ في مركز الإشعارات (panel_notifications)
        يَستطلعه تطبيق سطح المكتب.
      • «تلجرام» (telegram): يُرسَل إن فُعِّلت القناة والبوت مضبوط.

    لا يَفشل فشلًا صلبًا حين تلجرام غير جاهز ما دامت push/windows مُفعَّلة —
    يَفشل فقط حين لا قناة تسليم مُفعَّلة، أو لم تنجح أيّ قناة مُفعَّلة.

    يُعيد خريطة نتائج لكلّ قناة مع إبقاء مفاتيح التوافق (``ok``/``error``/
    ``text``):
        {ok, error, text, channels: {push:{ok,reason,sent,failed,devices},
         windows:{ok,...}, telegram:{ok,reason}}}
    """
    spec = _BY_KEY.get(key)
    if not spec:
        return {"ok": False, "error": N_("تنبيه غير معروف."), "text": "",
                "channels": {}}
    tid = int(tenant_id)
    chans = channels_for(tid, key)
    text = preview(key)
    plain = _strip_html(text)
    channels: dict[str, dict] = {}
    errors: list[str] = []

    # ── دفع الجوال — تحويل متزامن للوحة (يُرجع الحالة/العدد) ──
    if "push" in chans:
        try:
            from . import notifications as _notif
            pres = _notif.send_test_push(
                tid, title="🧪 " + spec.label, body=plain)
        except Exception as exc:  # noqa: BLE001 — الاختبار لا يكسر شيئًا
            pres = {"ok": False, "reason": str(exc)[:120]}
        reason = str(pres.get("reason") or "")
        channels["push"] = {
            "ok": bool(pres.get("ok")),
            "reason": reason,
            "reason_text": _push_reason_ar(reason),
            "sent": int(pres.get("sent") or 0),
            "failed": int(pres.get("failed") or 0),
            "devices": int(pres.get("devices") or 0),
        }
        if not channels["push"]["ok"]:
            errors.append(N_("الدفع: ") + channels["push"]["reason_text"])

    # ── ويندوز — اكتب صفّ المركز كي يَستطلعه تطبيق ويندوز ──
    if "windows" in chans:
        try:
            from . import notifications as _notif
            ntype, severity = _GROUP_NOTIFY.get(spec.group, ("system", "info"))
            nid = _notif.notify(
                tid, type=ntype, severity=severity,
                title="🧪 " + spec.label, body=plain, source="local",
                source_ref=f"alert-test:{spec.key}", push=False)
            channels["windows"] = {"ok": nid is not None}
        except Exception as exc:  # noqa: BLE001
            channels["windows"] = {"ok": False, "reason": str(exc)[:120]}
        if not channels["windows"]["ok"]:
            errors.append(N_("ويندوز: تعذّرت كتابة المركز."))

    # ── تلجرام — كما اليوم، إن فُعِّلت القناة والبوت مضبوط ──
    if "telegram" in chans:
        if telegram_ready(tid):
            ok, err = _send_now(tid, N_("🧪 (اختبار)\n") + text)
            channels["telegram"] = {"ok": ok, "reason": err or ""}
            if not ok:
                errors.append(N_("تلجرام: ") + (err or N_("فشل الإرسال")))
        else:
            channels["telegram"] = {"ok": False,
                                    "reason": N_("بوت تلجرام غير مُفعَّل/مضبوط.")}
            errors.append(N_("تلجرام: البوت غير مُفعَّل/مضبوط."))

    # نجاح إجماليّ = نجحت قناة تسليم واحدة على الأقلّ. الجرس (bell) داخليّ
    # دائمًا فلا يُحتسَب قناة اختبار خارجيّة.
    deliver = [c for c in ("push", "windows", "telegram") if c in channels]
    if not deliver:
        return {"ok": False, "channels": channels, "text": text,
                "error": N_("لا قناة تسليم مُفعَّلة لهذا الحدث "
                         "(الجرس داخليّ فقط — فعّل دفع الجوال أو ويندوز أو تلجرام).")}
    any_ok = any(channels[c]["ok"] for c in deliver)
    return {"ok": any_ok, "channels": channels, "text": text,
            "error": "" if any_ok else " · ".join(errors)}


def test_connection(tenant_id: int) -> dict:
    """زر «اختبار الاتصال» العام — يرسل رسالة تحقّق ويُعيد النتيجة."""
    if not telegram_ready(int(tenant_id)):
        return {"ok": False, "error": N_("أكمل توكن البوت ومعرّف المحادثة وفعّل الإشعارات.")}
    ok, err = _send_now(
        int(tenant_id),
        N_("✅ <b>اختبار اتصال HobeRadius</b>\n"
        "إذا وصلتك هذه الرسالة فإعدادات بوت التلجرام صحيحة وستصلك التنبيهات."))
    return {"ok": ok, "error": err}


# ════════════════════════════════════════════════════════════════════════
# الجرد للعرض في الواجهة
# ════════════════════════════════════════════════════════════════════════
_CODE_TOKEN = re.compile(
    r"\b(?=[\w./]*[a-z])[A-Za-z_][A-Za-z0-9_]*(?:[./][A-Za-z_][A-Za-z0-9_]*)+\b"
    r"|\b[a-z][a-z0-9]*_[A-Za-z0-9_]+\b")
_PAREN = re.compile(r"\s*\(([^()]*)\)")


def public_description(text: str) -> str:
    """The event description as shown to the operator (web «إشعارات الإدارة»
    and GET /api/v1/admin-alerts): the spec keeps developer references —
    «(services/users.UsersService.create)», «accounting.create_loan»,
    «⚑ …» follow-up notes — which reached the page as raw code (re-test R13
    L4). They are stripped for display; the spec text itself is unchanged."""
    s = str(text or "")
    s = re.sub(r"\s*⚑[^.]*\.?", "", s)
    s = s.replace("نوع support", N_("نوع «دعم»"))
    s = _CODE_TOKEN.sub("", s)

    def _paren(m: "re.Match") -> str:
        inner = re.sub(r"\b[a-z][a-z0-9]*\b", "", m.group(1))
        inner = re.sub(r"\s*([،؛/,])\s*(?=[،؛/,]|$)", "", inner)
        inner = re.sub(r"^[\s،؛/,]+|[\s،؛/,]+$", "", inner)
        inner = re.sub(r"\s{2,}", " ", inner).strip(" -")
        keep = re.search(r"[A-Za-z0-9\u0600-\u06FF]", inner)
        return f" ({inner})" if keep else ""

    s = _PAREN.sub(_paren, s)
    s = re.sub(r"\s+—\s*(?=[.،]|$)", "", s)
    s = re.sub(r"\s{2,}", " ", s)
    s = re.sub(r"\s+([.،؛])", r"\1", s)
    return s.strip()


def catalogue(tenant_id: int) -> list[dict]:
    tid = int(tenant_id)
    out = []
    for spec in ALERTS:
        chans = channels_for(tid, spec.key)
        out.append({
            "key": spec.key,
            "group": spec.group,
            "group_label": _GROUP_LABEL.get(spec.group, spec.group),
            "label": spec.label,
            "description": public_description(spec.description),
            "enabled": is_enabled(tid, spec.key),
            "channels": sorted(chans),
            "template": spec.template,
            "preview": preview(spec.key),
        })
    return out


__all__ = [
    "AlertSpec", "ALERTS", "GROUPS", "get_spec",
    "is_enabled", "set_enabled", "render", "preview",
    "dispatch", "send_test", "test_connection", "telegram_ready", "catalogue",
    "CHANNELS", "DELIVERABLE_CHANNELS", "DEFERRED_CHANNELS",
    "channels_for", "set_channels",
]
