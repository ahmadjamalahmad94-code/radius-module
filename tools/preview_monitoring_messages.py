# -*- coding: utf-8 -*-
"""قبل/بعد لكل نوع رسالة مراقبة → preview/monitoring_messages_before_after.txt
«بعد» يُولَّد من المحرّك الحقيقي؛ «قبل» نصوص النسخة السابقة (من لقطة المالك).
يُظهر إصلاح الترتيب + عزل الأرقام/العناوين (FSI…PDI) داخل العربيّة RTL.

التشغيل:  python tools/preview_monitoring_messages.py
"""
from __future__ import annotations

import datetime as dt
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
PREVIEW = os.path.join(REPO, "preview")

import app.radius.services.device_health_alerts as dha  # noqa: E402
import app.radius.services.router_resource_monitor as rrm  # noqa: E402
import app.radius.services.monitoring_digest as md  # noqa: E402

W = "2026-06-23 13:12"
TH = {"cpu_pct": 85.0, "temp_c": 70.0, "ram_pct": 90.0, "disk_free_pct": 10.0, "traffic_mbps": 0}


def _after():
    fam = dha.format_alert_message
    out = {}
    out["جهاز: انقطاع"] = fam("down", name="test", ip="192.168.15.10",
                              description="كاميرا المدخل", when=W)
    out["جهاز: غير متاح (راوتر مفصول)"] = fam(
        "unavailable", name="test", ip="192.168.15.10", description="كاميرا المدخل",
        reason="الراوتر «ccr3» مفصول", when=W)
    out["جهاز: عودة"] = fam("recovery", name="test", ip="192.168.15.10",
                            description="كاميرا المدخل", ping="12 ms", when=W)
    out["جهاز: بنج عالٍ"] = fam("high_latency", name="cam-3", ip="192.168.15.30",
                                ping="210 ms", when=W)
    out["راوتر: غير متصل"] = fam("router_offline", name="ccr3", ip="192.168.15.1", when=W)
    out["راوتر: عودة"] = fam("router_online", name="ccr3", ip="192.168.15.1", when=W)
    out["مورد: ارتفاع المعالج"] = fam("res_cpu_high", name="rb-1", ip="10.0.0.3",
                                       value=rrm._value_line("cpu", 91, TH), when=W)
    out["تذكير: راوتر مفصول"] = md._reminder_message(
        {"name": "ccr3", "kind": "router", "status": "unreachable"}, "30 دقيقة")
    out["تذكير: جهاز غير متاح"] = md._reminder_message(
        {"name": "test", "kind": "device", "status": "unavailable"}, "30 دقيقة")
    good = {"now": dt.datetime(2026, 6, 23, 13, 11), "total": 2, "healthy": 2,
            "all_good": True, "down": [], "weak": [], "high_latency": []}
    out["تقرير دوريّ: كل شيء سليم"] = md.build_digest_message(good)
    issues = {"now": dt.datetime(2026, 6, 23, 13, 11), "total": 4, "healthy": 0,
              "all_good": False,
              "down": [{"name": "test", "kind": "device", "status": "down", "down_since": "2026-06-23T13:06Z"},
                       {"name": "ccr3", "kind": "router", "status": "unreachable", "down_since": "2026-06-23T13:06Z"}],
              "weak": [{"name": "rb-1", "items": ["المعالج " + dha.isolate("91%"),
                                                  "الحرارة " + dha.isolate("78°م")]}],
              "high_latency": [{"name": "cam-3", "detail": "210 ms"}]}
    out["تقرير دوريّ: بملاحظات"] = md.build_digest_message(issues)
    return out


# «قبل» — النصوص الفعليّة للنسخة السابقة (لقطة المالك + الكود القديم).
BEFORE = {
    "جهاز: انقطاع": "🚨 انقطع الاتصال مع «test»\nالعنوان: 192.168.15.10\nالوصف: كاميرا المدخل\nالوقت: 2026-06-23 13:12",
    "جهاز: غير متاح (راوتر مفصول)": "📵 «test» غير متاح — الراوتر مفصول\nالعنوان: 192.168.15.10\nالوصف: كاميرا المدخل\nالوقت: 2026-06-23 13:12",
    "جهاز: عودة": "✅ عاد الاتصال مع «test»\nالعنوان: 192.168.15.10\nالوصف: كاميرا المدخل\nالبنج: 12 ms\nالوقت: 2026-06-23 13:12",
    "جهاز: بنج عالٍ": "🐌 ارتفاع البنج على «cam-3»\nالعنوان: 192.168.15.30\nالبنج: 210 ms\nالوقت: 2026-06-23 13:12",
    "راوتر: غير متصل": "🔴 الراوتر «ccr3» غير متصل\nالعنوان: 192.168.15.1\nالوقت: 2026-06-23 13:12",
    "راوتر: عودة": "🟢 عاد اتصال الراوتر «ccr3»\nالعنوان: 192.168.15.1\nالوقت: 2026-06-23 13:12",
    "مورد: ارتفاع المعالج": "🔥 ارتفاع حمل المعالج على «rb-1»\nالمعالج: 91% (الحدّ 85%)\nالعنوان: 10.0.0.3\nالوقت: 2026-06-23 13:12",
    "تذكير: راوتر مفصول": "🔴 ما زال الراوتر «ccr3» غير متصل منذ 30 دقيقة.\nالوقت: 2026-06-23 13:12",
    "تذكير: جهاز غير متاح": "📵 ما زال «test» غير متاح (الراوتر مفصول) منذ 30 دقيقة.\nالوقت: 2026-06-23 13:12",
    "تقرير دوريّ: كل شيء سليم": "✅ تم الفحص الدوري — كل الأجهزة والراوترات سليمة (2 عنصرًا مُراقَبًا).\nالوقت: 2026-06-23 13:11",
    "تقرير دوريّ: بملاحظات": "⚠️ تقرير الفحص الدوري — 2026-06-23 13:11\n🔴 مفصول: «test» (منذ أقل من دقيقة)، «ccr3» (منذ أقل من دقيقة)\n🟠 ضعف موارد: «rb-1» (المعالج 91% · الحرارة 78°م)\n🐌 بنج عالٍ: «cam-3» (210ms)\n✅ سليم: 0 من 4",
}


def main() -> None:
    os.makedirs(PREVIEW, exist_ok=True)
    after = _after()
    blocks = ["قبل / بعد — تنسيق رسائل المراقبة (fix/monitoring-message-formatting)",
              "ملاحظة: «بعد» يحوي عوازل اتجاهيّة غير مرئيّة (FSI U+2068 … PDI U+2069)",
              "حول كل رقم/عنوان/وقت — تظهر صحيحة في تلجرام والجرس RTL.",
              "=" * 64]
    for key in after:
        blocks.append(f"\n### {key}\n")
        blocks.append("— قبل —")
        blocks.append(BEFORE.get(key, "(لا يوجد)"))
        blocks.append("\n— بعد —")
        blocks.append(after[key])
        blocks.append("-" * 64)
    text = "\n".join(blocks)
    with open(os.path.join(PREVIEW, "monitoring_messages_before_after.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
