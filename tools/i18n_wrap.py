#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""مغلِّف i18n محافِظ — يغلّف النصوص العربية الظاهرة بـ ``{{ _('...') }}``.

الفلسفة: **آمن بالبناء**. لغة المصدر عربية، فالنص المغلّف يسقط للعربية نفسها
(ar msgstr فارغة → gettext يُرجع msgid)، إذًا الرندر العربي يبقى مطابقًا تمامًا.
الخطر الوحيد = إنتاج Jinja غير صالح، ويُمسَك ببوابة parse تُرجِع أي ملف يفشل.

ما يُغلَّف (محافِظ — يتجاهل أي حالة ملتبسة):
  • نصوص بين الوسوم (text nodes) النقية أحادية السطر التي تحوي عربية.
  • قيم سمات محدّدة فقط: placeholder, title, aria-label, alt, value, content,
    data-confirm, aria-placeholder — وفقط إن كانت القيمة بلا Jinja/أقواس.

ما يُتجاهَل دائمًا (يُحيَّد قبل المعالجة): ``{# #}``، ``<!-- -->``،
``<script>``، ``<style>``، ``{% %}``، ``{{ }}`` (يشمل المغلّف سلفًا).

الاستخدام:
  python tools/i18n_wrap.py --check  app/templates/radius/users_list.html
  python tools/i18n_wrap.py --apply  <ملف> [<ملف> ...]
  python tools/i18n_wrap.py --apply  --sector radius     # كل radius غير docs
  python tools/i18n_wrap.py --apply  --all               # كل القوالب
شغّل دائمًا بـ PYTHONUTF8=1 على ويندوز.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

# نطاقات العربية (مطابقة لـ i18n_extract).
_ARABIC = ("؀-ۿ" "ݐ-ݿ" "ࢠ-ࣿ" "ﭐ-﷿" "ﹰ-﻿")
ARABIC_RE = re.compile("[" + _ARABIC + "]")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_ROOT = os.path.join(ROOT, "app", "templates")

#: سمات يُغلَّف محتواها العربي (عرض للمستخدم فقط).
ATTR_WHITELIST = (
    "placeholder", "title", "aria-label", "alt", "value", "content",
    "data-confirm", "aria-placeholder", "data-tooltip", "data-title",
)

#: مناطق محميّة تُستبدل بعلامات نائبة قبل المعالجة (تُعاد حرفيًا بعدها).
_PROTECT = re.compile(
    r"\{#.*?#\}"                                   # تعليق Jinja
    r"|<!--.*?-->"                                 # تعليق HTML
    r"|<script\b[^>]*>.*?</script>"               # سكربت
    r"|<style\b[^>]*>.*?</style>"                 # ستايل
    r"|\{%.*?%\}"                                  # عبارة Jinja
    r"|\{\{.*?\}\}",                               # تعبير Jinja (يشمل المغلّف)
    re.DOTALL | re.IGNORECASE,
)

_SENT = "\x00\x01{}\x01\x00"  # علامة نائبة فريدة لا تظهر في القوالب.


def _protect(text: str) -> tuple[str, list[str]]:
    """يستبدل المناطق المحميّة بعلامات نائبة؛ يُعيد (النص, قائمة الأصول)."""
    store: list[str] = []

    def repl(m: "re.Match") -> str:
        store.append(m.group(0))
        return _SENT.format(len(store) - 1)

    return _PROTECT.sub(repl, text), store


def _restore(text: str, store: list[str]) -> str:
    for i, orig in enumerate(store):
        text = text.replace(_SENT.format(i), orig)
    return text


def _wrap_literal(seg: str) -> str | None:
    """يبني ``_('seg')`` باختيار مُحدِّد آمن؛ None إن تعذّر بأمان."""
    if "\n" in seg or "{{" in seg or "{%" in seg or _SENT[:2] in seg:
        return None
    if "&" in seg:
        return None  # كيان HTML (&amp; &nbsp; …) → تجنّب مضاعفة الهروب.
    if "%" in seg:
        return None  # gettext يطبّق rv % vars دائمًا → '%' المفرد يكسر الرندر.
    has_s, has_d = "'" in seg, '"' in seg
    if not has_s:
        return "_('" + seg + "')"
    if not has_d:
        return '_("' + seg + '")'
    return None  # كلا النوعين من الاقتباس → تجاهل (نادر).


# نص بين وسوم: نلتقط ما بعد '>' وقبل '<' (لا يحوي أقواس زاوية ولا علامة نائبة).
_TEXTNODE = re.compile(r"(>)([^<>]+)(<)", re.DOTALL)
# سمة من القائمة البيضاء بقيمة بلا Jinja/أقواس/علامات نائبة.
_ATTR = re.compile(
    r"""(\b(?:%s)\s*=\s*)(["'])([^"'<>{}\x00]*?)(\2)"""
    % "|".join(a.replace("-", r"\-") for a in ATTR_WHITELIST),
    re.IGNORECASE,
)


def _wrap_text_nodes(text: str) -> tuple[str, int]:
    n = 0

    def repl(m: "re.Match") -> str:
        nonlocal n
        gt, body, lt = m.group(1), m.group(2), m.group(3)
        if not ARABIC_RE.search(body):
            return m.group(0)
        stripped = body.strip()
        if not stripped or not ARABIC_RE.search(stripped):
            return m.group(0)
        lead = body[: len(body) - len(body.lstrip())]
        trail = body[len(body.rstrip()):]
        wrapped = _wrap_literal(stripped)
        if wrapped is None:
            return m.group(0)
        n += 1
        return gt + lead + "{{ " + wrapped + " }}" + trail + lt

    return _TEXTNODE.sub(repl, text), n


def _wrap_attrs(text: str) -> tuple[str, int]:
    n = 0

    def repl(m: "re.Match") -> str:
        nonlocal n
        pre, q, val, q2 = m.group(1), m.group(2), m.group(3), m.group(4)
        if not ARABIC_RE.search(val):
            return m.group(0)
        stripped = val.strip()
        wrapped = _wrap_literal(stripped)
        if wrapped is None:
            return m.group(0)
        n += 1
        return pre + q + "{{ " + wrapped + " }}" + q2

    return _ATTR.sub(repl, text), n


def wrap_source(raw: str) -> tuple[str, int]:
    """يغلّف نصّ قالب كامل؛ يُعيد (النص الجديد, عدد التغليفات)."""
    protected, store = _protect(raw)
    protected, n1 = _wrap_attrs(protected)      # السمات أولًا (داخل الوسوم)
    protected, n2 = _wrap_text_nodes(protected)  # ثم نصوص الوسوم
    return _restore(protected, store), n1 + n2


# ─────────────── بوابة التحقّق Jinja ───────────────

_JENV = None


def _jinja_ok(source: str) -> bool:
    """يتحقّق أن المصدر يُحلَّل كـ Jinja صالح (نحو فقط، لا رندر)."""
    global _JENV
    if _JENV is None:
        from jinja2 import Environment
        _JENV = Environment(
            extensions=["jinja2.ext.i18n", "jinja2.ext.do",
                        "jinja2.ext.loopcontrols"],
            trim_blocks=True, lstrip_blocks=True, autoescape=True,
        )
        _JENV.install_null_translations()  # كي يُحلَّل {% trans %}
    try:
        _JENV.parse(source)
        return True
    except Exception:  # noqa: BLE001
        return False


def process_file(path: str, apply: bool) -> tuple[int, str]:
    """يعالج ملفًا. يُعيد (عدد التغليفات, الحالة)."""
    with open(path, "r", encoding="utf-8") as fh:
        raw = fh.read()
    new, n = wrap_source(raw)
    if n == 0:
        return 0, "—"
    if not _jinja_ok(new):
        return 0, "REVERTED(parse)"
    if not _jinja_ok(raw):
        # الأصل نفسه لا يُحلَّل ببيئتنا (إضافة مفقودة) — لا نخاطر.
        return 0, "SKIP(orig-unparsable)"
    if apply:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    return n, "OK" if apply else "DRY"


def _gather(args) -> list[str]:
    if args.all:
        return sorted(glob.glob(os.path.join(TEMPLATES_ROOT, "**", "*.html"),
                                 recursive=True))
    if args.sector:
        pat = os.path.join(TEMPLATES_ROOT, args.sector, "*.html")
        files = sorted(glob.glob(pat))
        if args.no_docs:
            files = [f for f in files
                     if not os.path.basename(f).startswith("docs_")]
        return files
    return [os.path.abspath(f) for f in args.files]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--sector")
    ap.add_argument("--no-docs", action="store_true")
    args = ap.parse_args()

    files = _gather(args)
    if not files:
        print("لا ملفات.")
        return 1

    total, reverted, skipped = 0, 0, 0
    for path in files:
        n, status = process_file(path, apply=args.apply)
        rel = os.path.relpath(path, ROOT).replace("\\", "/")
        if status.startswith("REVERTED"):
            reverted += 1
            print(f"  ⚠ {status:20} {rel}")
        elif status.startswith("SKIP"):
            skipped += 1
            print(f"  · {status:20} {rel}")
        elif n:
            total += n
            print(f"  ✓ {n:4} {rel}")
    print(f"\nالإجمالي: غُلِّف {total} | ملفات مُرجَعة(parse) {reverted} | "
          f"متخطّاة {skipped} | مفحوصة {len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
