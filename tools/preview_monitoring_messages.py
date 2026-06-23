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
              "weak": [{"name": "rb-1", "items": ["المعالج: " + dha.isolate("91%"),
                                                  "الحرارة: " + dha.isolate("78°م")]}],
              "high_latency": [{"name": "cam-3", "detail": "210 ms"}]}
    out["تقرير دوريّ: بملاحظات"] = md.build_digest_message(issues)
    return out


# «قبل» — هذه الجولة (fix/monitoring-short-lines) غيّرت **التقرير الدوريّ فقط**:
# الأسطر الطويلة المجمَّعة بـ«·» → سطور قصيرة، بندٌ واحد لكل سطر (تلجرام يلفّ
# الطويل فيكسر المعنى). «قبل» للتقرير = نسخة الدمج السابقة (سطر حالة واحد +
# ضعف مجمَّع بـ«·»). باقي الأنواع لم تتغيّر هذه الجولة (قبل = بعد).
BEFORE_DIGEST = {
    "تقرير دوريّ: كل شيء سليم":
        "✅ الفحص الدوري — كل شيء سليم\nالأجهزة والراوترات (2) تعمل بشكل سليم.\n🕒 الوقت: 2026-06-23 13:11",
    "تقرير دوريّ: بملاحظات":
        ("⚠️ الفحص الدوري — توجد ملاحظات\n"
         "الحالة: 🔴 مفصول 2 · 🟠 ضعف 1 · 🐌 بنج عالٍ 1 · ✅ سليم 0\n\n"
         "🔴 مفصول:\n• «test» (جهاز) — منذ 5 دقيقة\n• «ccr3» (راوتر) — منذ 5 دقيقة\n\n"
         "🟠 ضعف موارد:\n• «rb-1» — المعالج 91% · الحرارة 78°م\n\n"
         "🐌 بنج عالٍ:\n• «cam-3» — 210 ms\n\n"
         "🕒 الوقت: 2026-06-23 13:11"),
}


def main() -> None:
    os.makedirs(PREVIEW, exist_ok=True)
    after = _after()
    blocks = ["قبل / بعد — أسطر قصيرة لرسائل المراقبة (fix/monitoring-short-lines)",
              "الهدف: بندٌ واحد لكل سطر — تلجرام يلفّ الأسطر الطويلة فيكسر المعنى.",
              "هذه الجولة غيّرت التقرير الدوريّ فقط (الحالة + ضعف الموارد).",
              "ملاحظة: «بعد» يحوي عوازل FSI…PDI غير مرئيّة حول الأرقام (RTL-safe).",
              "=" * 64]
    for key in after:
        before = BEFORE_DIGEST.get(key, after[key])     # غير التقرير: بلا تغيير
        unchanged = key not in BEFORE_DIGEST
        blocks.append(f"\n### {key}" + ("  (بلا تغيير هذه الجولة)" if unchanged else "") + "\n")
        if not unchanged:
            blocks.append("— قبل —")
            blocks.append(before)
            blocks.append("\n— بعد —")
        blocks.append(after[key])
        blocks.append("-" * 64)
    text = "\n".join(blocks)
    with open(os.path.join(PREVIEW, "monitoring_messages_before_after.txt"), "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)


if __name__ == "__main__":
    main()
