#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""أداة قياس المتبقي من التدويل (i18n) — وحدة RADIUS / Flask-Babel.

الغرض
    تمسح قوالب Jinja في ``app/templates/**/*.html`` وتكشف **النصوص العربية
    الصلبة غير المغلّفة** بدالة الترجمة، أي كل نص يحوي حروفًا عربية ولم
    يُغلَّف بعد بـ ``{{ _('...') }}`` أو ``{% trans %}``. تُنتِج تقريرًا
    يقيس مقدار المتبقّي ونسبة التغطية التقريبية، ليتابع المنفّذون تقدّم
    تعميم الترجمة قطاعًا قطاعًا.

النموذج (مذكّر)
    لغة المصدر = العربية: النص العربي نفسه هو الـ msgid. لذا «المُغلَّف»
    يعني نصًّا عربيًا داخل ``_( )`` أو ``trans``، و«غير المُغلَّف» يعني نصًّا
    عربيًا ظاهرًا خام في القالب لم يُلتقط بعد.

التشغيل
    python tools/i18n_extract.py                 # تقرير نصّي عربي كامل
    python tools/i18n_extract.py --summary       # الملخّص الإجمالي فقط
    python tools/i18n_extract.py --top 10        # أكثر 10 ملفات تبقّيًا
    python tools/i18n_extract.py --file <path>   # ملف واحد بالتفصيل
    python tools/i18n_extract.py --json          # مخرج JSON للأدوات

لا تعتمد على أي حزمة خارجية (مكتبة بايثون القياسية فقط)، ولا تكسر على أي
ملف (أخطاء الترميز تُعالَج بأمان وتُتجاوز).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# ─────────────── ثوابت المسارات ───────────────

#: جذر المشروع (مجلد أعلى من tools/).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: جذر القوالب الممسوحة.
TEMPLATES_ROOT = os.path.join(PROJECT_ROOT, "app", "templates")

#: طول عيّنة النص المعروضة في التقرير (تُقتطع إن طالت).
SAMPLE_LEN = 60

# ─────────────── أنماط الكشف ───────────────

#: نطاقات يونيكود العربية: الأساسي + الملحق + النماذج التقديمية (A/B).
_ARABIC_RANGES = (
    "؀-ۿ"  # العربية الأساسية
    "ݐ-ݿ"  # الملحق العربي
    "ࢠ-ࣿ"  # الملحق العربي الموسّع
    "ﭐ-﷿"  # النماذج التقديمية-أ
    "ﹰ-﻿"  # النماذج التقديمية-ب
)
ARABIC_RE = re.compile("[" + _ARABIC_RANGES + "]")

#: المناطق «المغلّفة» التي يجب تجاهل أي عربية بداخلها (تمّت ترجمتها):
#:   - {{ _('...') }} و {{ gettext('...') }} و {{ ngettext(...) }}
#:   - {% trans %}...{% endtrans %}  و  {%- trans ... -%}
#: نُحيّد محتواها قبل البحث عن العربية الخام.
WRAPPED_PATTERNS = [
    # {% trans %} ... {% endtrans %}  (بأي whitespace/معاملات)
    re.compile(r"\{%-?\s*trans\b.*?%\}.*?\{%-?\s*endtrans\s*-?%\}",
               re.DOTALL),
    # استدعاءات الدوال داخل {{ ... }} : _(...) gettext(...) ngettext(...)
    # _l(...) lazy_gettext(...) pgettext(...) — نطابق التعبير كاملًا.
    # ملاحظة حاسمة: محتوى {{ }} يجب ألّا يعبر حدّ '}}'، وإلّا ابتلع تعبيرٌ
    # سابقٌ (مثل {{ url_for(...) }}) نصوصًا لاحقة حتى يصل لاستدعاء ترجمة بعيد.
    # لذا نمنع '}}' داخل الجسم عبر (?:(?!\}\}).) مع DOTALL.
    re.compile(
        r"\{\{(?:(?!\}\}).)*?"
        r"\b(?:_l?|n?gettext|lazy_gettext|pgettext|npgettext)\s*\("
        r"(?:(?!\}\}).)*?\}\}",
        re.DOTALL,
    ),
    # {% trans ... %} السطري (بلا endtrans، صيغة `{% trans x=y %}msg{% endtrans %}`
    # غُطّيت أعلاه؛ هنا نغطّي {% trans count %}... لو وُجدت بلا إغلاق ضمن السطر)
]

#: تعليقات Jinja:  {# ... #}
JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
#: تعليقات HTML:  <!-- ... -->
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
#: كتل <script> ... </script>
SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
#: كتل <style> ... </style>
STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)


def _blank_match(m: "re.Match") -> str:
    """يستبدل المطابقة بفراغات تحافظ على أرقام الأسطر (يبقي '\\n')."""
    return re.sub(r"[^\n]", " ", m.group(0))


# ─────────────── منطق الكشف ───────────────

def _mask_regions(text: str, *, mask_script_style: bool) -> str:
    """يُحيّد المناطق التي لا نريد كشف العربية الخام بداخلها.

    يستبدل كل منطقة (مغلّفة/تعليق/سكربت/ستايل) بفراغات بنفس الطول مع إبقاء
    أحرف السطر الجديد، كي تبقى أرقام الأسطر دقيقة في التقرير.
    اختياريًا يُبقي script/style لو أردنا تصنيفها لاحقًا (mask_script_style=False).
    """
    for pat in (JINJA_COMMENT_RE, HTML_COMMENT_RE):
        text = pat.sub(_blank_match, text)
    if mask_script_style:
        for pat in (SCRIPT_RE, STYLE_RE):
            text = pat.sub(_blank_match, text)
    for pat in WRAPPED_PATTERNS:
        text = pat.sub(_blank_match, text)
    return text


def _split_segments(line: str) -> list[str]:
    """يفصل سطرًا إلى مقاطع نصية تحوي عربية، متجاهلًا وُسوم HTML/تعابير Jinja.

    نزيل وُسوم ``<...>`` وتعابير ``{{...}}`` و``{%...%}`` المتبقّية، ثم نُعيد
    المقاطع التي ما تزال تحوي حروفًا عربية (نصوص عرض حقيقية).
    """
    # أزل تعابير Jinja المتبقّية (غير العربية، مثل {{ var }}) ووسوم HTML.
    cleaned = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", " ", line)
    cleaned = re.sub(r"<[^>]*>", "\x00", cleaned)  # علّم حدود الوسوم
    parts = [p.strip() for p in cleaned.split("\x00")]
    return [p for p in parts if p and ARABIC_RE.search(p)]


def _count_wrapped(raw: str) -> int:
    """عدد النصوص العربية *المغلّفة* في الملف (تقريبيًا) لحساب التغطية.

    نَعُدّ كل استدعاء ``_()``/``gettext``/``trans`` يحوي عربية. ليس عدًّا
    لغويًا دقيقًا بل مؤشّر نسبي متّسق عبر كل الملفات.
    """
    count = 0
    # استدعاءات الدوال
    for m in re.finditer(
        r"\b(?:_l?|n?gettext|lazy_gettext|pgettext|npgettext)\s*\((.*?)\)",
        raw, re.DOTALL,
    ):
        if ARABIC_RE.search(m.group(1)):
            count += 1
    # كتل trans
    for m in re.finditer(
        r"\{%-?\s*trans\b.*?%\}(.*?)\{%-?\s*endtrans\s*-?%\}",
        raw, re.DOTALL,
    ):
        if ARABIC_RE.search(m.group(1)):
            count += 1
    return count


def scan_text(raw: str) -> dict:
    """يفحص نصّ قالب واحد ويُعيد نتائجه.

    المُخرَج dict:
        unwrapped : list[(line_no, sample)]  النصوص العربية غير المغلّفة
        in_script : list[(line_no, sample)]  عربية داخل script/style (تحتاج
                     معالجة JS/CSS خاصة — مُصنّفة منفصلة بعلم)
        wrapped   : int                       عدد النصوص المغلّفة (تقريبي)
    """
    # 1) النص الأساسي: نحيّد المغلّف + التعليقات + script/style.
    masked = _mask_regions(raw, mask_script_style=True)
    unwrapped: list[tuple[int, str]] = []
    for i, line in enumerate(masked.splitlines(), start=1):
        if not ARABIC_RE.search(line):
            continue
        for seg in _split_segments(line):
            sample = seg if len(seg) <= SAMPLE_LEN else seg[:SAMPLE_LEN] + "…"
            unwrapped.append((i, sample))

    # 2) عربية داخل script/style فقط (نُحيّد كل شيء عداها) — مُصنّفة منفصلة.
    only_scripts = _mask_regions(raw, mask_script_style=False)
    # أبقِ script/style فقط: احسب أسطرها بمطابقتها على النص الأصلي المحيّد.
    in_script: list[tuple[int, str]] = []
    masked_lines = masked.splitlines()
    for i, line in enumerate(only_scripts.splitlines(), start=1):
        if not ARABIC_RE.search(line):
            continue
        # إن كان السطر ظهر أصلًا في المسح الأساسي فهو ليس داخل script/style.
        base_line = masked_lines[i - 1] if i - 1 < len(masked_lines) else ""
        if ARABIC_RE.search(base_line):
            continue
        for seg in _split_segments(line):
            sample = seg if len(seg) <= SAMPLE_LEN else seg[:SAMPLE_LEN] + "…"
            in_script.append((i, sample))

    return {
        "unwrapped": unwrapped,
        "in_script": in_script,
        "wrapped": _count_wrapped(raw),
    }


def scan_file(path: str) -> dict:
    """يقرأ ملفًا ويفحصه. آمن ضد أخطاء الترميز (يقرأ بـ utf-8/تجاهل)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    except OSError as exc:  # noqa: BLE001
        return {"unwrapped": [], "in_script": [], "wrapped": 0,
                "error": str(exc)}
    res = scan_text(raw)
    res["error"] = None
    return res


def collect(root: str, single: str | None = None) -> list[dict]:
    """يجمع نتائج كل القوالب (أو ملف واحد)، مرتّبة تنازليًا حسب المتبقّي."""
    files: list[str] = []
    if single:
        files = [single]
    else:
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if name.endswith(".html"):
                    files.append(os.path.join(dirpath, name))
    files.sort()

    out: list[dict] = []
    for path in files:
        res = scan_file(path)
        rel = os.path.relpath(path, PROJECT_ROOT)
        out.append({
            "file": rel.replace("\\", "/"),
            "remaining": len(res["unwrapped"]),
            "in_script": len(res["in_script"]),
            "wrapped": res["wrapped"],
            "items": res["unwrapped"],
            "script_items": res["in_script"],
            "error": res.get("error"),
        })
    out.sort(key=lambda d: d["remaining"], reverse=True)
    return out


def summarize(results: list[dict]) -> dict:
    """يحسب الإجماليات ونسبة التغطية التقريبية عبر كل القوالب."""
    total_files = len(results)
    files_with_remaining = sum(1 for r in results if r["remaining"] > 0)
    total_remaining = sum(r["remaining"] for r in results)
    total_in_script = sum(r["in_script"] for r in results)
    total_wrapped = sum(r["wrapped"] for r in results)
    denom = total_wrapped + total_remaining
    coverage = (total_wrapped / denom * 100.0) if denom else 100.0
    return {
        "total_files": total_files,
        "files_with_remaining": files_with_remaining,
        "total_remaining": total_remaining,
        "total_in_script": total_in_script,
        "total_wrapped": total_wrapped,
        "coverage_pct": round(coverage, 1),
    }


# ─────────────── الإخراج ───────────────

def _emit_summary(summ: dict, out=sys.stdout) -> None:
    p = lambda s: print(s, file=out)  # noqa: E731
    p("═" * 64)
    p("  ملخّص قياس التدويل (i18n) — القوالب")
    p("═" * 64)
    p(f"  إجمالي الملفات الممسوحة      : {summ['total_files']}")
    p(f"  ملفات بها متبقٍّ              : {summ['files_with_remaining']}")
    p(f"  إجمالي النصوص المغلّفة        : {summ['total_wrapped']}")
    p(f"  إجمالي النصوص المتبقّية       : {summ['total_remaining']}")
    p(f"  نصوص عربية داخل script/style : {summ['total_in_script']}  (تحتاج معالجة خاصة)")
    p(f"  نسبة التغطية التقريبية        : {summ['coverage_pct']}%")
    p("═" * 64)


def _emit_top(results: list[dict], n: int, out=sys.stdout) -> None:
    p = lambda s: print(s, file=out)  # noqa: E731
    top = [r for r in results if r["remaining"] > 0][:n]
    p("")
    p(f"  أكثر {len(top)} ملفًا تبقّيًا (نصوص عربية غير مغلّفة):")
    p("  " + "─" * 60)
    p(f"  {'المتبقّي':>8} │ {'الملف'}")
    p("  " + "─" * 60)
    for r in top:
        p(f"  {r['remaining']:>8} │ {r['file']}")
    p("  " + "─" * 60)


def _emit_full(results: list[dict], out=sys.stdout) -> None:
    p = lambda s: print(s, file=out)  # noqa: E731
    for r in results:
        if r["remaining"] == 0 and r["in_script"] == 0:
            continue
        p("")
        p(f"▶ {r['file']}  —  متبقٍّ: {r['remaining']}"
          + (f" | داخل script/style: {r['in_script']}"
             if r["in_script"] else ""))
        if r.get("error"):
            p(f"    ⚠ خطأ بقراءة الملف: {r['error']}")
        for line_no, sample in r["items"]:
            p(f"    سطر {line_no:>4}: {sample}")
        for line_no, sample in r["script_items"]:
            p(f"    [JS/CSS] سطر {line_no:>4}: {sample}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="قياس المتبقّي من تغليف النصوص العربية للترجمة (i18n).",
    )
    parser.add_argument("--json", action="store_true",
                        help="مخرَج JSON للأدوات بدل التقرير النصّي.")
    parser.add_argument("--file", metavar="PATH",
                        help="افحص ملفًا واحدًا فقط (مسار نسبي أو مطلق).")
    parser.add_argument("--summary", action="store_true",
                        help="اطبع الملخّص الإجمالي فقط.")
    parser.add_argument("--top", type=int, metavar="N", default=None,
                        help="اطبع أكثر N ملفات تبقّيًا فقط.")
    args = parser.parse_args(argv)

    # ضمان طباعة عربية صحيحة على Windows.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    single = None
    if args.file:
        single = args.file if os.path.isabs(args.file) \
            else os.path.join(PROJECT_ROOT, args.file)
        if not os.path.isfile(single):
            print(f"⚠ الملف غير موجود: {args.file}", file=sys.stderr)
            return 2

    results = collect(TEMPLATES_ROOT, single=single)
    summ = summarize(results)

    if args.json:
        payload = {"summary": summ, "files": results}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    _emit_summary(summ)
    if args.summary:
        return 0
    if args.top is not None:
        _emit_top(results, args.top)
        return 0
    _emit_top(results, 10)
    _emit_full(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
