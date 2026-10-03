#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""جرد شامل لكل نصّ عربيّ يمكن أن يظهر للمستخدم — مُغلَّف أم مُسرَّب.

الغرض
    مصدر حقيقة واحد يجيب: «أين كل كلمة عربيّة في النظام، وهل هي داخل
    الكتالوج (مُغلَّفة) أم مكتوبة خامًا (تسريب)؟». تستعمله أداة التغليف،
    وأداة القائمة الشاملة، واختبار الحارس ``tests/test_i18n_no_leak_guard.py``.

ما يُمسَح (كل ما يُنتج نصًّا مرئيًّا)
    1. قوالب Jinja: ``app/templates/**.html`` و ``app/radius/templates/**.html``
       — نصّ HTML، السمات، سلاسل تعابير Jinja، ونصوص JavaScript داخل
       ``<script>`` وسمات ``on*``.
    2. بايثون التطبيق: ``app/**.py`` (رسائل flash، أخطاء API، التسميات…).
    3. JavaScript الثابت: ``app/static/js/*.js``.

الحالات
    wrapped      مُغلَّف: داخل ``_()``/``gettext``/``_l``/``_tr``/``N_``/``hrT``/
                 ``{% trans %}`` ⇒ يدخل الكتالوج.
    leak         عربيّ مرئيّ غير مُغلَّف ⇒ يجب تغليفه.
    ignored      ليس نصّ واجهة (قاعدة صريحة — انظر RULES أدناه).
    allowlisted  تسريب مقبول عمدًا، مُبرَّر في ``tools/i18n_allowlist.txt``.

قواعد «ليس نصّ واجهة» (الإيجابيات الكاذبة) صريحة في القاموس ``RULES``
    أدناه (تُطبع بـ ``--rules``)، والملفّات المستثناة كليًّا في ``SKIP_FILES``.

التشغيل
    python tools/i18n_inventory.py               # ملخّص + أكثر الملفات تسريبًا
    python tools/i18n_inventory.py --leaks       # كل تسريب file:line
    python tools/i18n_inventory.py --file PATH   # ملف واحد بالتفصيل
    python tools/i18n_inventory.py --json OUT    # تصدير JSON كامل
    python tools/i18n_inventory.py --rules       # قواعد الاستثناء الموثّقة
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import os
import re
import sys
from dataclasses import dataclass, field, asdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST_PATH = os.path.join(ROOT, "tools", "i18n_allowlist.txt")

# ═══════════════════════ القواعد الموثّقة ═══════════════════════

RULES: dict[str, str] = {
    "comment":       "تعليق (Jinja {# #}، HTML <!-- -->، JS // و /* */، CSS، بايثون #).",
    "docstring":     "docstring بايثون — توثيق للمطوّر لا يُعرَض.",
    "no-letter":     "لا يحوي حرفًا عربيًّا (أرقام/فواصل/محارف هروب فقط مثل '٫' '٬' '\\u0600').",
    "log":           "رسالة سجلّ (logging/print/warnings، console.* في JS) — للمطوّر لا للمستخدم.",
    "compare":       "طرف مقارنة (== != in === !== case) — قيمة منطق؛ ترجمتها تكسر الشرط.",
    "key":           "مفتاح قاموس/كائن أو فهرس [...] — معرّف لا نصّ عرض.",
    "logic-arg":     "وسيط دالّة منطق (replace/split/startswith/get/includes/indexOf/"
                     "getAttribute/querySelector/re.*/RegExp/selectattr…).",
    "sql":           "نصّ SQL أو وسيط execute — بيانات قاعدة لا واجهة.",
    "regex":         "تعبير نمطيّ (re.* / RegExp / /…/).",
    "logic-attr":    "سمة HTML منطقيّة (name/id/class/href/value لـoption وhidden/data-value…).",
    "css":           "داخل <style> أو سمة style (عدا content:) — تنسيق.",
    "test-file":     "ملفّ اختبار — خارج النطاق.",
    "not-scanned":   "ملفّ خارج النطاق المرئيّ (ترحيلات DB، سكربتات تشغيل) — انظر SKIP_FILES.",
    "typed-confirm": "عبارة تأكيد يكتبها المستخدم حرفيًّا ويقارنها الخادم (CONFIRM_WORD…) — ترجمتها تكسر التأكيد.",
    "parse-vocab":   "مفردات تعرّف مُدخَلات (مرادفات أعمدة الاستيراد، كلمات نعم/لا، وحدات تُحلَّل):"
                     " عناصر قائمة تمزج معرّفات لاتينيّة بعربيّة، أو تُسنَد لاسم مثل _TRUE/_FALSE/"
                     "*_SYNONYMS*/*_PREFIXES/*_KEYWORDS/*_ALIASES/*_UNITS — يقرؤها الكود لا المستخدم.",
}

#: أسماء متغيّرات تحمل مفردات تحليل مدخلات (انظر RULES['parse-vocab']).
PARSE_VOCAB_NAME = re.compile(
    r"^_?(TRUE|FALSE|YES|NO|TRUTHY|FALSY|UNLIMITED)(_[A-Z]+)?$|SYNONYM|_PREFIXES$|KEYWORDS$|ALIASES$|"
    r"^_(DUR|SPEED|SIZE|TIME|DATA)_UNITS$|_HINTS$")

#: ملفّات/أنماط لا تُمسح إطلاقًا — مع السبب (تظهر في التقرير).
SKIP_FILES: dict[str, str] = {
    "app/radius/db/migrations/*": "ترحيلات DB — قيم بذر تُخزَّن بيانات، لا نصوص واجهة.",
    "app/radius/seed.py": "بيانات تجريبيّة (أسماء/باقات وهميّة) تُبذَر في DB — محتوى لا واجهة.",
    # قوالب مكرّرة يحجبها app/templates (محمِّل التطبيق يسبق محمِّل البلوبرنت)
    "app/radius/templates/radius/devices_form.html": "مُحجوب بنسخة app/templates — لا يُعرَض أبدًا.",
    "app/radius/templates/radius/devices_list.html": "مُحجوب بنسخة app/templates — لا يُعرَض أبدًا.",
    "app/radius/templates/radius/sessions_list.html": "مُحجوب بنسخة app/templates — لا يُعرَض أبدًا.",
}

# حرف عربيّ «كتابيّ» (بلا تطويل/تشكيل/أرقام/فواصل).
AR_LETTER = re.compile("[ء-غف-يٱ-ۓۺ-ۿݐ-ݿ]")
AR_ANY = re.compile("[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")

#: دوال الترجمة المعترف بها (تُدخل النصّ في الكتالوج).
PY_TRANS = {"_", "_l", "_tr", "N_", "gettext", "lazy_gettext", "ngettext",
            "pgettext", "npgettext", "lazy_ngettext", "lazy_pgettext"}
JINJA_TRANS = {"_", "gettext", "ngettext", "pgettext", "npgettext", "_l", "lazy_gettext"}
JS_TRANS = {"hrT"}

#: مفاتيح الاستخراج لـ pybabel (يجب أن تطابق ما في i18n_master.py).
EXTRACT_KEYWORDS = ["_l", "_tr", "N_", "hrT", "lazy_gettext", "pgettext:1c,2"]


@dataclass
class Finding:
    file: str
    line: int
    kind: str           # html | py | js
    ctx: str            # html-text | attr:<n> | jinja-str | js-str | js-tpl | py-str | py-fstr | css
    text: str
    status: str         # wrapped | leak | ignored | allowlisted
    reason: str = ""    # قاعدة الاستثناء / سبب السماح / أعلام السياق
    start: int = -1     # إزاحة بداية المحارف في الملف (للمغلِّف الآلي)
    end: int = -1
    scope: str = ""     # بايثون: runtime | import
    flags: list = field(default_factory=list)


def _line_of(src: str, pos: int, _cache: dict = {}) -> int:  # noqa: B006
    key = id(src)
    starts = _cache.get(key)
    if starts is None or starts[0] is not src:
        idx = [0]
        for m in re.finditer("\n", src):
            idx.append(m.end())
        starts = (src, idx)
        _cache.clear()
        _cache[key] = starts
    import bisect
    return bisect.bisect_right(starts[1], pos)


def has_arabic(s: str) -> bool:
    return bool(AR_ANY.search(s or ""))


def has_letter(s: str) -> bool:
    return bool(AR_LETTER.search(s or ""))


# ═══════════════════════ مُرمِّز JavaScript ═══════════════════════

_JS_KEYWORDS_BEFORE_REGEX = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "throw", "case", "do", "else", "yield", "await",
}
_JS_PUNCT3 = ("===", "!==", "**=", "...", "<<=", ">>=", ">>>", "&&=", "||=", "??=")
_JS_PUNCT2 = ("==", "!=", "<=", ">=", "&&", "||", "??", "?.", "=>", "++", "--",
              "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "**")


@dataclass
class JsTok:
    type: str     # str | tpl | comment | regex | punct | ident | num
    start: int
    end: int
    value: str
    # للقوالب الحرفيّة: أجزاء النصّ [(start,end)] وأجزاء التعبير [(start,end)]
    parts: list = field(default_factory=list)
    depth: int = 0   # عمق تداخل ${} (0 = المستوى الأعلى)


def js_tokenize(src: str, start: int = 0, end: int | None = None, depth: int = 0) -> list[JsTok]:
    """مُرمِّز JS متسامح: يميّز السلاسل/القوالب/التعليقات/التعابير النمطيّة.

    لا يرمي أبدًا — أيّ محرف غير متوقَّع يصير punct."""
    return _js_tok(src, start, end, depth, False)[0]


def _js_tok(src, start, end, depth, until_brace):
    end = len(src) if end is None else end
    toks: list[JsTok] = []
    i = start
    level = 0

    def prev_sig():
        for t in reversed(toks):
            if t.type != "comment":
                return t
        return None

    while i < end:
        c = src[i]
        if c in " \t\r\n\x0b\x0c ﻿":
            i += 1
            continue
        if c == "/" and i + 1 < end and src[i + 1] == "/":
            j = src.find("\n", i)
            j = end if j == -1 or j > end else j
            toks.append(JsTok("comment", i, j, src[i:j], depth=depth))
            i = j
            continue
        if c == "/" and i + 1 < end and src[i + 1] == "*":
            j = src.find("*/", i + 2)
            j = end if j == -1 or j + 2 > end else j + 2
            toks.append(JsTok("comment", i, j, src[i:j], depth=depth))
            i = j
            continue
        if c in "'\"":
            j = i + 1
            while j < end and src[j] != c and src[j] != "\n":
                if src[j] == "\\":
                    j += 1
                j += 1
            j = min(j + 1, end)
            toks.append(JsTok("str", i, j, src[i:j], depth=depth))
            i = j
            continue
        if c == "`":
            j = i + 1
            parts = []
            seg_start = j
            inner: list[JsTok] = []
            while j < end and src[j] != "`":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "$" and j + 1 < end and src[j + 1] == "{":
                    parts.append(("text", seg_start, j))
                    # ابحث عن القوس المطابق مع احترام السلاسل المتداخلة
                    k = j + 2
                    sub_toks, close = _js_tok(src, k, end, depth + 1, True)
                    inner.extend(sub_toks)
                    parts.append(("expr", k, close))
                    j = close + 1
                    seg_start = j
                    continue
                j += 1
            parts.append(("text", seg_start, min(j, end)))
            j = min(j + 1, end)
            toks.append(JsTok("tpl", i, j, src[i:j], parts=parts, depth=depth))
            toks.extend(inner)
            i = j
            continue
        if c == "/":
            p = prev_sig()
            regex_ok = (p is None or (p.type == "punct" and p.value not in (")", "]", "}"))
                        or (p.type == "ident" and p.value in _JS_KEYWORDS_BEFORE_REGEX))
            if regex_ok:
                j = i + 1
                in_class = False
                while j < end and src[j] != "\n":
                    ch = src[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    j += 1
                j += 1
                while j < end and (src[j].isalpha()):
                    j += 1
                toks.append(JsTok("regex", i, j, src[i:j], depth=depth))
                i = j
                continue
        if c.isalpha() or c in "_$" or c == "\x01" or ord(c) > 127:
            j = i + 1
            while j < end and (src[j].isalnum() or src[j] in "_$\x01" or ord(src[j]) > 127) \
                    and src[j] not in "'\"`":
                j += 1
            toks.append(JsTok("ident", i, j, src[i:j], depth=depth))
            i = j
            continue
        if c.isdigit():
            j = i + 1
            while j < end and (src[j].isalnum() or src[j] in "._"):
                j += 1
            toks.append(JsTok("num", i, j, src[i:j], depth=depth))
            i = j
            continue
        if until_brace and c == "{":
            level += 1
        elif until_brace and c == "}":
            if level == 0:
                return toks, i
            level -= 1
        for p3 in _JS_PUNCT3:
            if src.startswith(p3, i):
                toks.append(JsTok("punct", i, i + 3, p3, depth=depth))
                i += 3
                break
        else:
            for p2 in _JS_PUNCT2:
                if src.startswith(p2, i):
                    toks.append(JsTok("punct", i, i + 2, p2, depth=depth))
                    i += 2
                    break
            else:
                toks.append(JsTok("punct", i, i + 1, c, depth=depth))
                i += 1
    return toks, end


_JS_LOGIC_CALLEES = {
    "includes", "indexOf", "lastIndexOf", "startsWith", "endsWith", "split",
    "getAttribute", "removeAttribute", "hasAttribute", "toggleAttribute",
    "querySelector", "querySelectorAll", "getElementById", "getElementsByClassName",
    "getElementsByName", "closest", "matches", "getItem", "setItem", "removeItem",
    "has", "get", "delete", "contains", "RegExp", "match", "matchAll", "search",
    "test", "localeCompare", "getPropertyValue", "setProperty", "postMessage",
    "addEventListener", "removeEventListener", "dispatchEvent", "CustomEvent", "Event",
    "find", "findIndex", "filter",
}
_JS_LOGIC_FIRST_ARG_ONLY = {"replace", "replaceAll", "set", "append", "add", "remove", "toggle",
                            "setAttribute"}


def classify_js_tokens(toks: list[JsTok], src: str):
    """يُصنّف رموز السلاسل في قائمة رموز JS: يُعيد [(tok, status, reason, flags)]."""
    sig = [t for t in toks if t.type != "comment"]
    out = []
    # مكدّس الاستدعاءات: (callee, arg_index, kind) — kind: call|array|sub|obj|group
    stack: list[list] = []
    array_ranges: list[tuple[int, int, int]] = []  # (start_idx, end_idx) للمصفوفات
    open_idx: list[int] = []
    for idx, t in enumerate(sig):
        if t.type == "punct" and t.value in "([{":
            p = sig[idx - 1] if idx > 0 else None
            if t.value == "(":
                callee = ""
                if p is not None and p.type == "ident":
                    callee = p.value
                    # سلسلة a.b.c ⇒ نحتفظ بالجذر أيضًا (console.log)
                    chain = [p.value]
                    k = idx - 2
                    while k >= 1 and sig[k].type == "punct" and sig[k].value in (".", "?.") \
                            and sig[k - 1].type == "ident":
                        chain.insert(0, sig[k - 1].value)
                        k -= 2
                    callee = ".".join(chain)
                stack.append([callee, 0, "call"])
            elif t.value == "[":
                is_sub = p is not None and (p.type in ("ident", "str", "tpl") and p.value not in
                                            _JS_KEYWORDS_BEFORE_REGEX or
                                            (p.type == "punct" and p.value in (")", "]")))
                stack.append(["", 0, "sub" if is_sub else "array"])
            else:
                stack.append(["", 0, "obj"])
            open_idx.append(idx)
            continue
        if t.type == "punct" and t.value in ")]}":
            if stack:
                fr = stack.pop()
                oi = open_idx.pop()
                if fr[2] == "array":
                    array_ranges.append((oi, idx))
            continue
        if t.type == "punct" and t.value == "," and stack:
            stack[-1][1] += 1
            continue
        if t.type not in ("str", "tpl"):
            continue
        text = js_string_text(t, src)
        if not has_arabic(text):
            continue
        prev = sig[idx - 1] if idx > 0 else None
        nxt = sig[idx + 1] if idx + 1 < len(sig) else None
        flags = []
        status, reason = "leak", ""
        callees = [fr[0] for fr in stack if fr[2] == "call"]
        top = stack[-1] if stack else None
        if not has_letter(text):
            status, reason = "ignored", "no-letter"
        elif any(c.split(".")[0] == "console" for c in callees):
            status, reason = "ignored", "log"
        elif top and top[2] == "call" and top[0].split(".")[-1] in JS_TRANS and top[1] == 0:
            status, reason = "wrapped", ""
        elif (prev is not None and prev.type == "punct" and prev.value in ("===", "!==", "==", "!=")) or \
                (nxt is not None and nxt.type == "punct" and nxt.value in ("===", "!==", "==", "!=")):
            status, reason = "ignored", "compare"
            # مقارنة بما كتبه المستخدم (عبارة تأكيد) ⇒ typed-confirm
            near = sig[max(0, idx - 4):idx + 5]
            if any(x.type == "ident" and x.value in ("typed", "confirm", "value", "word", "phrase", "confirmIn")
                   for x in near):
                reason = "typed-confirm"
        elif prev is not None and prev.type == "ident" and prev.value == "case":
            status, reason = "ignored", "compare"
        elif nxt is not None and nxt.type == "punct" and nxt.value == ":" and \
                prev is not None and prev.type == "punct" and prev.value in ("{", ","):
            status, reason = "ignored", "key"
        elif top and top[2] == "sub":
            status, reason = "ignored", "key"
        elif top and top[2] == "call" and (
                top[0].split(".")[-1] in _JS_LOGIC_CALLEES or
                (top[0].split(".")[-1] in _JS_LOGIC_FIRST_ARG_ONLY and top[1] == 0)):
            status, reason = "ignored", "logic-arg"
        if "\x01" in text:
            flags.append("jinja-inside")
        if t.type == "tpl" and any(k == "expr" for k, _a, _b in t.parts):
            flags.append("interp")
        if "<" in text and re.search(r"<[a-zA-Z/!]", text):
            flags.append("html")
        if prev is not None and prev.type == "punct" and prev.value == "+" or \
                nxt is not None and nxt.type == "punct" and nxt.value == "+":
            flags.append("concat")
        out.append([t, status, reason, flags, idx])
    # مصفوفات تُستعمل للمنطق: ['أ','ب'].includes(x)
    logic_arrays = []
    for (a, b) in array_ranges:
        if b + 2 < len(sig) and sig[b + 1].type == "punct" and sig[b + 1].value in (".", "?.") \
                and sig[b + 2].type == "ident" and sig[b + 2].value in _JS_LOGIC_CALLEES | {"indexOf", "includes"}:
            logic_arrays.append((a, b))
    if logic_arrays:
        for rec in out:
            if rec[1] == "leak" and any(a < rec[4] < b for a, b in logic_arrays):
                rec[1], rec[2] = "ignored", "compare"
    return [(r[0], r[1], r[2], r[3]) for r in out]


def js_string_text(t: JsTok, src: str) -> str:
    """النصّ المرئيّ لسلسلة JS (بلا علامات الاقتباس؛ أجزاء ${} تصير {…})."""
    if t.type == "str":
        return src[t.start + 1:t.end - 1] if t.end - t.start >= 2 else ""
    if t.type == "tpl":
        out = []
        for kind, a, b in t.parts:
            out.append(src[a:b] if kind == "text" else "{…}")
        return "".join(out)
    return t.value


# ═══════════════════════ مُرمِّز تعابير Jinja ═══════════════════════

_JINJA_TOKEN = re.compile(
    r"""(?P<str>'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")"""
    r"|(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<op>==|!=|<=|>=|//|\*\*|[-+*/%~<>=()\[\]{}.,:|!?])"
    r"|(?P<ws>\s+)"
    r"|(?P<other>.)", re.S)

_JINJA_COMPARE = {"==", "!=", "<", ">", "<=", ">="}
_JINJA_COMPARE_WORDS = {"in", "is", "equalto", "sameas", "eq", "ne", "lt", "gt", "le", "ge"}
_JINJA_LOGIC_CALLEES = {
    "replace", "split", "startswith", "endswith", "selectattr", "rejectattr", "get",
    "pop", "setdefault", "strip", "lstrip", "rstrip", "count", "index", "find", "attr",
    "map", "sort", "groupby", "sum", "dictsort", "unique", "min", "max", "rfind",
    "url_for", "static", "format_datetime", "strftime", "request", "has_perm",
    "can", "has_permission", "setting", "get_setting", "partition",
}


@dataclass
class JTok:
    type: str
    start: int
    end: int
    value: str


def jinja_tokens(src: str, start: int, end: int) -> list[JTok]:
    """رموز تعبير Jinja. السلاسل المتجاورة ("a" "b") تُدمَج رمزًا واحدًا
    (كما يفعل Jinja نفسه) — قيمتها نصّ حرفيّ جاهز لـ literal_eval."""
    toks: list[JTok] = []
    for m in _JINJA_TOKEN.finditer(src, start, end):
        kind = m.lastgroup
        if kind == "ws":
            continue
        if kind == "str" and toks and toks[-1].type == "str":
            prev = toks[-1]
            toks[-1] = JTok("str", prev.start, m.end(), prev.value + " " + m.group())
            continue
        toks.append(JTok(kind, m.start(), m.end(), m.group()))
    return toks


def jinja_str_value(tok: JTok) -> str:
    raw = tok.value[1:-1]
    try:
        return ast.literal_eval(tok.value)
    except Exception:  # noqa: BLE001
        return raw


def classify_jinja_expr(src: str, start: int, end: int, is_block: bool):
    """يُصنّف السلاسل العربيّة داخل {{ … }} أو {% … %}."""
    toks = jinja_tokens(src, start, end)
    out = []
    stack: list[list] = []   # [callee, argidx, kind, kwarg_pending]
    for i, t in enumerate(toks):
        if t.type == "op" and t.value in "([{":
            p = toks[i - 1] if i else None
            if t.value == "(":
                callee = p.value if p is not None and p.type == "name" else ""
                stack.append([callee, 0, "call"])
            elif t.value == "[":
                is_sub = p is not None and (p.type in ("name", "str") or p.value in (")", "]"))
                stack.append(["", 0, "sub" if is_sub else "list"])
            else:
                stack.append(["", 0, "dict"])
            continue
        if t.type == "op" and t.value in ")]}":
            if stack:
                stack.pop()
            continue
        if t.type == "op" and t.value == "," and stack:
            stack[-1][1] += 1
            continue
        if t.type != "str":
            continue
        text = jinja_str_value(t)
        if not isinstance(text, str) or not has_arabic(text):
            continue
        prev = toks[i - 1] if i else None
        prev2 = toks[i - 2] if i > 1 else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        top = stack[-1] if stack else None
        flags = []
        is_kw = prev is not None and prev.value == "=" and prev2 is not None and prev2.type == "name"
        if is_kw:
            flags.append("kw:" + prev2.value)
        if not has_letter(text):
            st, rs = "ignored", "no-letter"
        elif top and top[2] == "call" and top[0] in JINJA_TRANS and not is_kw:
            st, rs = "wrapped", ""
        elif (prev is not None and (prev.value in _JINJA_COMPARE or
                                    (prev.type == "name" and prev.value in _JINJA_COMPARE_WORDS))) or \
                (nxt is not None and (nxt.value in _JINJA_COMPARE or
                                      (nxt.type == "name" and nxt.value in ("in", "not", "is")))):
            st, rs = "ignored", "compare"
        elif top and top[2] == "dict" and nxt is not None and nxt.value == ":":
            st, rs = "ignored", "key"
        elif top and top[2] == "sub":
            st, rs = "ignored", "key"
        elif top and top[2] == "call" and top[0] in _JINJA_LOGIC_CALLEES and \
                not (top[0] in ("replace", "get", "pop", "setdefault") and top[1] >= 1):
            st, rs = "ignored", "logic-arg"
        else:
            st, rs = "leak", ""
        if prev is not None and prev.value in ("~", "+") or nxt is not None and nxt.value in ("~", "+"):
            flags.append("concat")
        if re.search(r"<[a-zA-Z/!]", text):
            flags.append("html")
        if top and top[2] == "call":
            flags.append("call:" + top[0])
        out.append((t, text, st, rs, flags))
    return out


# ═══════════════════════ ماسح القوالب ═══════════════════════

_JINJA_OPEN = re.compile(r"\{\{|\{%|\{#")


def jinja_regions(src: str):
    """يقسم القالب إلى مناطق: ('data'|'expr'|'block'|'comment', start, end, inner_start, inner_end).

    يحترم السلاسل داخل التعابير، و{% raw %}."""
    i = 0
    n = len(src)
    regions = []
    while i < n:
        m = _JINJA_OPEN.search(src, i)
        if not m:
            regions.append(("data", i, n, i, n))
            break
        if m.start() > i:
            regions.append(("data", i, m.start(), i, m.start()))
        kind = {"{{": "expr", "{%": "block", "{#": "comment"}[m.group()]
        close = {"expr": "}}", "block": "%}", "comment": "#}"}[kind]
        j = m.end()
        if kind == "comment":
            k = src.find("#}", j)
            k = n if k == -1 else k + 2
            regions.append(("comment", m.start(), k, j, max(j, k - 2)))
            i = k
            continue
        # امشِ مع احترام السلاسل
        q = None
        while j < n:
            ch = src[j]
            if q:
                if ch == "\\":
                    j += 2
                    continue
                if ch == q:
                    q = None
            elif ch in "'\"":
                q = ch
            elif src.startswith(close, j):
                break
            j += 1
        k = min(n, j + 2)
        inner_s = m.end()
        inner_e = j
        # علامات التحكّم بالمسافات {{- … -}} / {%+ … +%} ليست جزءًا من التعبير
        if inner_s < inner_e and src[inner_s] in "-+":
            inner_s += 1
        if inner_e > inner_s and src[inner_e - 1] in "-+":
            inner_e -= 1
        regions.append((kind, m.start(), k, inner_s, inner_e))
        i = k
        if kind == "block":
            body = src[inner_s:inner_e].strip(" -+\t\r\n")
            if body.split(" ")[0] == "raw":
                e = re.compile(r"\{%-?\s*endraw\s*-?%\}").search(src, i)
                stop = e.start() if e else n
                regions.append(("data", i, stop, i, stop))
                if e:
                    regions.append(("block", e.start(), e.end(), e.start() + 2, e.end() - 2))
                    i = e.end()
                else:
                    i = n
    return regions


_DISPLAY_ATTRS = {
    "placeholder", "title", "alt", "aria-label", "aria-description", "aria-placeholder",
    "aria-roledescription", "aria-valuetext", "label", "summary", "content", "abbr",
    "data-original-title", "data-bs-original-title", "tooltip",
    "data-column-label", "data-cc-inert", "data-on", "data-off", "data-urow-confirm",
    "data-uds-export-title",
}
_DISPLAY_DATA_PREFIX = (
    "data-confirm", "data-title", "data-tooltip", "data-tip", "data-hint", "data-label",
    "data-placeholder", "data-msg", "data-message", "data-text", "data-empty", "data-help",
    "data-desc", "data-success", "data-error", "data-loading", "data-copied", "data-copy-label",
    "data-ok", "data-cancel", "data-busy", "data-done", "data-prompt", "data-warn", "data-note",
    "data-caption", "data-heading", "data-subtitle", "data-sub", "data-body", "data-on-label",
    "data-off-label", "data-yes", "data-no", "data-name", "data-toast", "data-alert",
    "data-hr-", "data-pending", "data-saving", "data-saved", "data-label-",
)
_LOGIC_ATTRS = {
    "name", "id", "class", "href", "src", "action", "method", "type", "for", "pattern",
    "rel", "target", "role", "lang", "dir", "form", "list", "accept", "autocomplete",
    "inputmode", "enctype", "xmlns", "viewbox", "d", "points", "fill", "stroke",
    "data-value", "data-filter", "data-key", "data-status", "data-type", "data-kind",
    "data-tab", "data-id", "data-target", "data-bs-target", "data-toggle", "data-bs-toggle",
    "data-sort", "data-sort-value", "data-group", "data-state", "data-mode", "data-code",
    "data-match", "data-search", "data-default", "data-field", "data-col", "data-row",
}


def _html_scan(masked: str, start: int, end: int, emit, region_cb):
    """يمسح HTML (بعد إخفاء Jinja بـ\\x01): نصوص، سمات، script/style.

    emit(kind_ctx, text_start, text_end, extra) — region_cb('script'|'style', s, e, attrs)."""
    i = start
    text_start = start
    lower = masked  # نستخدم .lower() موضعيًّا
    while i < end:
        lt = masked.find("<", i, end)
        if lt == -1:
            emit("html-text", text_start, end, {})
            return
        nxt = masked[lt + 1:lt + 2]
        if not (nxt.isalpha() or nxt in "/!"):
            i = lt + 1
            continue
        emit("html-text", text_start, lt, {})
        if masked.startswith("<!--", lt):
            k = masked.find("-->", lt + 4, end)
            k = end if k == -1 else k + 3
            i = text_start = k
            continue
        # وسم: اقرأ الاسم والسمات
        j = lt + 1
        closing = masked[j] == "/"
        if closing:
            j += 1
        name_m = re.compile(r"[A-Za-z][A-Za-z0-9:-]*").match(masked, j)
        if not name_m:
            i = lt + 1
            continue
        tag = name_m.group().lower()
        j = name_m.end()
        attrs = []
        while j < end:
            while j < end and masked[j] in " \t\r\n/\x01":
                j += 1
            if j >= end or masked[j] == ">":
                break
            am = re.compile(r"[^\s=>/\x01]+").match(masked, j)
            if not am:
                j += 1
                continue
            aname = am.group().lower()
            j = am.end()
            while j < end and masked[j] in " \t\r\n":
                j += 1
            vs = ve = -1
            if j < end and masked[j] == "=":
                j += 1
                while j < end and masked[j] in " \t\r\n":
                    j += 1
                if j < end and masked[j] in "'\"":
                    qch = masked[j]
                    k = masked.find(qch, j + 1, end)
                    k = end if k == -1 else k
                    vs, ve = j + 1, k
                    j = k + 1
                else:
                    k = j
                    while k < end and masked[k] not in " \t\r\n>":
                        k += 1
                    vs, ve = j, k
                    j = k
            attrs.append((aname, vs, ve))
        gt = j
        i = text_start = min(end, gt + 1)
        if closing:
            continue
        attr_map = {a: (s, e) for a, s, e in attrs}
        for aname, vs, ve in attrs:
            if vs >= 0:
                emit("attr", vs, ve, {"attr": aname, "tag": tag, "attrs": attr_map})
        if tag in ("script", "style"):
            close_re = re.compile(r"</" + tag + r"\s*>", re.I)
            cm = close_re.search(masked, i, end)
            body_end = cm.start() if cm else end
            stype = ""
            if "type" in attr_map and attr_map["type"][0] >= 0:
                stype = masked[attr_map["type"][0]:attr_map["type"][1]].lower()
            region_cb(tag, i, body_end, {"type": stype})
            i = text_start = cm.end() if cm else end


def scan_template(path: str, src: str) -> list[Finding]:
    rel = _rel(path)
    findings: list[Finding] = []
    regions = jinja_regions(src)
    # قناع: كل منطقة Jinja ⇒ \x01 (مع إبقاء \n لأرقام الأسطر)
    chars = list(src)
    in_trans = False
    trans_ranges = []
    trans_start = None
    for kind, s, e, is_, ie in regions:
        if kind in ("expr", "block", "comment"):
            for p in range(s, e):
                if chars[p] != "\n":
                    chars[p] = "\x01"
        if kind == "block":
            body = src[is_:ie].strip(" -+\t\r\n")
            word = body.split(None, 1)[0] if body else ""
            if word == "trans":
                in_trans = True
                trans_start = e
            elif word == "endtrans" and in_trans:
                in_trans = False
                trans_ranges.append((trans_start, s))
    masked = "".join(chars)
    # كتل trans: مُغلَّفة — نُحصيها ونُخفيها
    for a, b in trans_ranges:
        seg = src[a:b]
        if has_letter(seg):
            findings.append(Finding(rel, _line_of(src, a), "html", "trans",
                                    _norm_ws(re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", seg)), "wrapped",
                                    start=a, end=b))
        masked = masked[:a] + "".join("\n" if ch == "\n" else " " for ch in masked[a:b]) + masked[b:]
    # تعليقات Jinja (للإحصاء فقط)
    # ── تعابير Jinja ──
    for kind, s, e, is_, ie in regions:
        if kind not in ("expr", "block"):
            continue
        if any(a <= s < b for a, b in trans_ranges):
            continue
        for t, text, st, rs, flags in classify_jinja_expr(src, is_, ie, kind == "block"):
            findings.append(Finding(rel, _line_of(src, t.start), "html", "jinja-str", text, st, rs,
                                    start=t.start, end=t.end, flags=flags + [f"in:{kind}"]))

    # ── HTML/JS/CSS ──
    def emit(ctx, a, b, extra):
        seg = masked[a:b]
        if not has_arabic(seg):
            return
        if ctx == "html-text":
            # نصّ بين وسمين؛ أجزاء \x01 = تعابير Jinja
            text = seg
            stripped = text.strip()
            if not stripped:
                return
            # تعليقات HTML تُعالَج في _html_scan؛ هنا نصّ مرئيّ
            st = "leak" if has_letter(stripped) else "ignored"
            lead = len(text) - len(text.lstrip())
            trail = len(text) - len(text.rstrip())
            findings.append(Finding(rel, _line_of(src, a + lead), "html", "html-text",
                                    _norm_ws(src[a + lead:b - trail]), st,
                                    "" if st == "leak" else "no-letter",
                                    start=a + lead, end=b - trail,
                                    flags=(["jinja-inside"] if "\x01" in stripped else [])))
            return
        if ctx == "attr":
            aname = extra["attr"]
            tag = extra["tag"]
            text = src[a:b]
            if aname.startswith("on"):
                _scan_js_into(findings, rel, src, masked, a, b, attr_ctx=aname)
                return
            if aname == "style":
                if "content" in seg:
                    findings.append(Finding(rel, _line_of(src, a), "html", "css", _norm_ws(text),
                                            "leak", "", start=a, end=b))
                return
            st, rs = "leak", ""
            if not has_letter(seg):
                st, rs = "ignored", "no-letter"
            elif aname == "value":
                itype = ""
                if "type" in extra["attrs"] and extra["attrs"]["type"][0] >= 0:
                    ts, te = extra["attrs"]["type"]
                    itype = masked[ts:te].lower()
                if tag == "input" and itype in ("submit", "button", "reset"):
                    st, rs = "leak", ""
                elif tag == "button":
                    st, rs = "ignored", "logic-attr"
                else:
                    st, rs = "ignored", "logic-attr"
            elif aname in _LOGIC_ATTRS:
                st, rs = "ignored", "logic-attr"
            flags = ["jinja-inside"] if "\x01" in seg else []
            if aname in _DISPLAY_ATTRS or aname.startswith(_DISPLAY_DATA_PREFIX):
                flags.append("display-attr")
            findings.append(Finding(rel, _line_of(src, a), "html", f"attr:{aname}", _norm_ws(text),
                                    st, rs, start=a, end=b, flags=flags))

    def region_cb(tag, a, b, extra):
        if tag == "style":
            seg = masked[a:b]
            if has_arabic(seg):
                # تعليقات CSS ⇒ comment؛ content:"…" ⇒ تسريب
                for m in re.finditer(r"/\*.*?\*/", seg, re.S):
                    pass
                body = re.sub(r"/\*.*?\*/", lambda m: " " * len(m.group()), seg, flags=re.S)
                for m in re.finditer(r"""content\s*:\s*(['"])(.*?)\1""", body):
                    if has_letter(m.group(2)):
                        findings.append(Finding(rel, _line_of(src, a + m.start(2)), "html", "css",
                                                m.group(2), "leak", "", start=a + m.start(2),
                                                end=a + m.end(2)))
            return
        stype = extra.get("type", "")
        if stype in ("text/template", "text/html", "text/x-template", "text/ng-template"):
            _html_scan(masked, a, b, emit, region_cb)
            return
        _scan_js_into(findings, rel, src, masked, a, b)

    for kind, s, e, is_, ie in regions:
        pass
    _html_scan(masked, 0, len(masked), emit, region_cb)
    return findings


def _scan_js_into(findings, rel, src, masked, a, b, attr_ctx: str = ""):
    toks = js_tokenize(masked, a, b)
    for t, st, rs, flags in classify_js_tokens(toks, masked):
        text = js_string_text(t, src)
        fl = list(flags)
        if attr_ctx:
            fl.append("in-attr:" + attr_ctx)
        findings.append(Finding(rel, _line_of(src, t.start), "html",
                                "js-tpl" if t.type == "tpl" else "js-str",
                                text, st, rs, start=t.start, end=t.end, flags=fl))


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# ═══════════════════════ ماسح JavaScript الثابت ═══════════════════════

def scan_js(path: str, src: str) -> list[Finding]:
    rel = _rel(path)
    out = []
    toks = js_tokenize(src)
    for t, st, rs, flags in classify_js_tokens(toks, src):
        out.append(Finding(rel, _line_of(src, t.start), "js", "js-tpl" if t.type == "tpl" else "js-str",
                           js_string_text(t, src), st, rs, start=t.start, end=t.end, flags=flags))
    return out


# ═══════════════════════ ماسح بايثون ═══════════════════════

_PY_LOG_ATTRS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log", "fatal"}
_PY_LOG_BASES = re.compile(r"(^|_)(log|logger|logging|LOG|LOGGER|_log|_logger|log_)$|logger$|^log$|_LOG$", re.I)
_PY_LOGIC_METHODS = {
    "replace", "split", "rsplit", "startswith", "endswith", "strip", "lstrip", "rstrip",
    "find", "rfind", "index", "rindex", "count", "partition", "rpartition", "translate",
    "maketrans", "get", "pop", "setdefault", "removeprefix", "removesuffix", "encode",
    "decode", "fromkeys", "has_key", "getattr", "hasattr", "setattr", "isinstance",
    "get_setting", "set_setting", "getenv", "environ",
}
_PY_REGEX_FUNCS = {"compile", "match", "search", "sub", "subn", "findall", "finditer", "fullmatch", "split"}
_PY_SQL_FUNCS = {"execute", "executemany", "executescript", "text", "query", "fetch", "_q", "_exec",
                 "_execute", "exec_sql", "scalar", "fetchone", "fetchall", "_fetch", "_one", "_all", "q"}
_SQL_RE = re.compile(r"^\s*(SELECT|UPDATE|INSERT|DELETE|CREATE|ALTER|DROP|WITH|REPLACE|PRAGMA)\b", re.I)


def _callee(fn) -> tuple[str, str]:
    """(الاسم الأخير، الجذر) لاستدعاء."""
    if isinstance(fn, ast.Name):
        return fn.id, fn.id
    if isinstance(fn, ast.Attribute):
        root = fn.value
        while isinstance(root, ast.Attribute):
            root = root.value
        base = fn.value.attr if isinstance(fn.value, ast.Attribute) else (
            fn.value.id if isinstance(fn.value, ast.Name) else "")
        return fn.attr, base or (root.id if isinstance(root, ast.Name) else "")
    return "", ""


def _is_log_call(call: ast.Call) -> bool:
    name, base = _callee(call.func)
    if name in ("print",) and isinstance(call.func, ast.Name):
        return True
    if name == "warn" and base == "warnings":
        return True
    if isinstance(call.func, ast.Attribute) and name in _PY_LOG_ATTRS:
        if base and (_PY_LOG_BASES.search(base) or base in ("logging", "logger", "app")):
            return True
        # current_app.logger.info / self.log.info / self._logger.x
        if isinstance(call.func.value, ast.Attribute) and "log" in call.func.value.attr.lower():
            return True
        if isinstance(call.func.value, ast.Name) and "log" in call.func.value.id.lower():
            return True
    return False


def py_fstring_text(node: ast.JoinedStr) -> str:
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value))
        else:
            parts.append("{…}")
    return "".join(parts)


def scan_python(path: str, src: str) -> list[Finding]:
    rel = _rel(path)
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")   # تحذيرات هروب في الكود الممسوح ليست شأننا
            tree = ast.parse(src)
    except SyntaxError:
        return []
    parent: dict = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            parent[ch] = node
    # docstrings
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and \
                    isinstance(node.body[0].value, (ast.Constant, ast.JoinedStr)):
                docs.add(node.body[0].value)
    # تحويل إزاحة البايتات (col_offset) إلى محارف
    line_starts = [0]
    for m in re.finditer("\n", src):
        line_starts.append(m.end())
    lines = src.split("\n")

    def char_off(lineno, col_bytes):
        line = lines[lineno - 1]
        col = len(line.encode("utf-8")[:col_bytes].decode("utf-8", "ignore"))
        return line_starts[lineno - 1] + col

    out: list[Finding] = []
    fstr_parts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                fstr_parts.add(v)
                if isinstance(v, ast.FormattedValue) and v.format_spec is not None:
                    for vv in ast.walk(v.format_spec):
                        fstr_parts.add(vv)

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node in fstr_parts:
                continue
            text = node.value
            ctx = "py-str"
        elif isinstance(node, ast.JoinedStr):
            if node in fstr_parts:
                continue
            text = py_fstring_text(node)
            ctx = "py-fstr"
        else:
            continue
        if not has_arabic(text):
            continue
        st, rs, flags, scope = _classify_py(node, parent, docs, text)
        s = char_off(node.lineno, node.col_offset)
        e = char_off(node.end_lineno, node.end_col_offset)
        out.append(Finding(rel, node.lineno, "py", ctx, text, st, rs, start=s, end=e,
                           scope=scope, flags=flags))
    return out


def _classify_py(node, parent, docs, text):
    flags: list[str] = []
    if node in docs:
        return "ignored", "docstring", flags, ""
    # النطاق: runtime إن كان داخل دالة/لامدا (وليس قيمة افتراضيّة/مزخرِفًا)
    scope = "import"
    child = node
    p = parent.get(node)
    while p is not None:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            in_defaults = isinstance(p, ast.Lambda) and child is p.args or \
                (not isinstance(p, ast.Lambda) and (child in p.decorator_list or child is p.args
                                                      or child is p.returns))
            if not in_defaults:
                scope = "runtime"
                break
        child = p
        p = parent.get(p)
    if not has_letter(text):
        return "ignored", "no-letter", flags, scope
    # امشِ صعودًا للبحث عن سياق مُحدِّد
    child = node
    p = parent.get(node)
    depth = 0
    while p is not None and depth < 12:
        depth += 1
        if isinstance(p, ast.Call):
            name, base = _callee(p.func)
            is_arg = child in p.args or any(child is kw.value for kw in p.keywords)
            if is_arg:
                if name in PY_TRANS and (child in p.args):
                    # داخل تعبير f-string: مُستخرِج Babel لا يراه ⇒ لن يدخل الكتالوج
                    anc = parent.get(p)
                    while anc is not None:
                        if isinstance(anc, ast.FormattedValue):
                            flags.append("in-fstring")
                            return "leak", "", flags, scope
                        anc = parent.get(anc)
                    return "wrapped", "", flags, scope
                if _is_log_call(p):
                    return "ignored", "log", flags, scope
                if base == "re" and name in _PY_REGEX_FUNCS:
                    return "ignored", "regex", flags, scope
                if name in _PY_SQL_FUNCS and child in p.args and p.args and child is p.args[0]:
                    return "ignored", "sql", flags, scope
                if name in ("startswith", "endswith", "find", "rfind", "index", "count",
                            "removeprefix", "removesuffix") and isinstance(p.func, ast.Attribute):
                    flags.append("cmp:sub")
                if name in _PY_LOGIC_METHODS and isinstance(p.func, ast.Attribute):
                    if not (name == "replace" and child in p.args and p.args.index(child) >= 1) and \
                            not (name in ("get", "pop", "setdefault") and child in p.args
                                 and p.args.index(child) >= 1):
                        return "ignored", "logic-arg", flags, scope
                if name in ("getattr", "hasattr", "setattr", "isinstance") and child in p.args[:2]:
                    return "ignored", "logic-arg", flags, scope
                for kw in p.keywords:
                    if child is kw.value:
                        flags.append(f"kw:{kw.arg}")
                flags.append(f"call:{name}")
            break_here = True
            if break_here:
                break
        if isinstance(p, ast.Compare):
            # 'x' in msg  ⇒ فحص احتواء (cmp:sub) ؛ غيره مساواة (cmp:eq)
            if child is p.left and any(isinstance(o, (ast.In, ast.NotIn)) for o in p.ops):
                flags.append("cmp:sub")
            else:
                flags.append("cmp:eq")
            return "ignored", "compare", flags, scope
        if isinstance(p, ast.Dict):
            if child in p.keys:
                return "ignored", "key", flags, scope
            k = p.keys[p.values.index(child)] if child in p.values else None
            if isinstance(k, ast.Constant):
                flags.append(f"dkey:{k.value}")
            break
        if isinstance(p, ast.Subscript) and child is p.slice:
            return "ignored", "key", flags, scope
        if isinstance(p, (ast.Tuple, ast.List, ast.Set)) and child in p.elts and isinstance(child, ast.Constant):
            # قائمة مرادفات: كل عناصرها سلاسل، ومنها ≥2 معرّف لاتينيّ (("name","الاسم","title"…)).
            # (لا تشمل ("not_found", "رسالة", 404) — رسالة خطأ للعرض.)
            if all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in p.elts) and \
                    sum(1 for e in p.elts if re.fullmatch(r"[a-z][a-z0-9_ ]*", e.value)) >= 2:
                return "ignored", "parse-vocab", flags, scope
        if isinstance(p, ast.keyword) and p.arg and PARSE_VOCAB_NAME.search(p.arg.upper()):
            return "ignored", "parse-vocab", flags, scope
        if isinstance(p, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            tgt = p.targets[0] if isinstance(p, ast.Assign) else p.target
            if isinstance(tgt, ast.Name) and re.search(r"CONFIRM_(WORD|PHRASE|TEXT)$", tgt.id):
                return "ignored", "typed-confirm", flags, scope
            if isinstance(tgt, ast.Name) and PARSE_VOCAB_NAME.search(tgt.id):
                return "ignored", "parse-vocab", flags, scope
            if isinstance(tgt, ast.Name):
                flags.append(f"assign:{tgt.id}")
                if re.search(r"(_RE|_PATTERN|_REGEX|_SQL|_QUERY)$", tgt.id):
                    return "ignored", "regex" if "RE" in tgt.id else "sql", flags, scope
            elif isinstance(tgt, ast.Attribute):
                flags.append(f"assign:{tgt.attr}")
            break
        if isinstance(p, (ast.Return,)):
            flags.append("return")
            break
        if isinstance(p, ast.match_case if hasattr(ast, "match_case") else ()):
            return "ignored", "compare", flags, scope
        if isinstance(p, ast.MatchValue):
            return "ignored", "compare", flags, scope
        if isinstance(p, (ast.Expr, ast.stmt)):
            break
        child = p
        p = parent.get(p)
    if _SQL_RE.search(text):
        return "ignored", "sql", flags, scope
    if re.search(r"<[a-zA-Z/!][^>]*>", text):
        flags.append("html")
    return "leak", "", flags, scope


# ═══════════════════════ قائمة السماح ═══════════════════════

@dataclass
class AllowRule:
    pattern: str      # glob مسار
    text: str | None  # None = الملف كله؛ وإلا نصّ مطابق (بعد تطبيع المسافات) أو بادئة بـ ^
    reason: str


def load_allowlist(path: str = ALLOWLIST_PATH) -> list[AllowRule]:
    rules = []
    if not os.path.isfile(path):
        return rules
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                parts = [p.strip() for p in re.split(r"\s+\|\s+", line)]
            if len(parts) < 3:
                continue
            pat, txt, reason = parts[0].strip(), parts[1].strip(), "\t".join(parts[2:]).strip()
            rules.append(AllowRule(pat, None if txt == "*" else txt, reason))
    return rules


def _allow_match(f: Finding, rules: list[AllowRule]) -> AllowRule | None:
    for r in rules:
        if not fnmatch.fnmatch(f.file, r.pattern):
            continue
        if r.text is None:
            return r
        norm = _norm_ws(f.text)
        if r.text.startswith("^"):
            if norm.startswith(r.text[1:]):
                return r
        elif norm == _norm_ws(r.text):
            return r
    return None


# ═══════════════════════ التجميع ═══════════════════════

def _rel(path: str) -> str:
    return os.path.relpath(os.path.abspath(path), ROOT).replace("\\", "/")


def iter_sources(root: str = ROOT):
    """يُعيد [(kind, abs_path)] لكل ملفّ داخل النطاق."""
    out = []
    for base, kind, ext in (("app/templates", "html", ".html"),
                            ("app/radius/templates", "html", ".html"),
                            ("app", "py", ".py"),
                            ("app/static/js", "js", ".js")):
        d = os.path.join(root, base)
        for dp, dns, fns in os.walk(d):
            dns[:] = [x for x in dns if x not in ("__pycache__", "node_modules", "vendor")]
            if kind == "js" and os.path.abspath(dp) != os.path.abspath(d):
                pass
            for fn in sorted(fns):
                if not fn.endswith(ext):
                    continue
                if kind == "js" and fn.endswith(".min.js"):
                    continue
                out.append((kind, os.path.join(dp, fn)))
    out.sort(key=lambda kp: kp[1])
    return out


def skipped_reason(rel: str) -> str | None:
    for pat, why in SKIP_FILES.items():
        if fnmatch.fnmatch(rel, pat):
            return why
    if "/tests/" in rel or os.path.basename(rel).startswith("test_"):
        return RULES["test-file"]
    return None


def scan_file(kind: str, path: str) -> list[Finding]:
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        src = fh.read()
    if not has_arabic(src):
        return []
    if kind == "html":
        return scan_template(path, src)
    if kind == "py":
        return scan_python(path, src)
    return scan_js(path, src)


def inventory(root: str = ROOT, files: list[str] | None = None, allow: bool = True):
    rules = load_allowlist() if allow else []
    findings: list[Finding] = []
    skipped: dict[str, str] = {}
    srcs = iter_sources(root)
    if files:
        wanted = {_rel(f) for f in files}
        srcs = [(k, p) for k, p in srcs if _rel(p) in wanted]
    for kind, path in srcs:
        rel = _rel(path)
        why = skipped_reason(rel)
        if why:
            skipped[rel] = why
            continue
        for f in scan_file(kind, path):
            if f.status == "leak":
                r = _allow_match(f, rules)
                if r is not None:
                    f.status, f.reason = "allowlisted", r.reason
            findings.append(f)
    return findings, skipped


def summarize(findings: list[Finding]) -> dict:
    by_status: dict[str, int] = {}
    by_kind: dict[str, dict[str, int]] = {}
    ignored_by_rule: dict[str, int] = {}
    for f in findings:
        by_status[f.status] = by_status.get(f.status, 0) + 1
        by_kind.setdefault(f.kind, {}).setdefault(f.status, 0)
        by_kind[f.kind][f.status] += 1
        if f.status == "ignored":
            ignored_by_rule[f.reason] = ignored_by_rule.get(f.reason, 0) + 1
    uniq_leak = len({_norm_ws(f.text) for f in findings if f.status == "leak"})
    return {"by_status": by_status, "by_kind": by_kind, "ignored_by_rule": ignored_by_rule,
            "unique_leak_texts": uniq_leak, "total": len(findings)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="جرد النصوص العربيّة المرئيّة: مُغلَّف/مُسرَّب.")
    ap.add_argument("--leaks", action="store_true", help="اطبع كل تسريب file:line.")
    ap.add_argument("--file", action="append", help="افحص ملفًّا (يتكرّر).")
    ap.add_argument("--json", metavar="OUT", help="اكتب الجرد الكامل JSON.")
    ap.add_argument("--rules", action="store_true", help="اطبع قواعد الاستثناء.")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--status", default=None, help="مع --file: فلترة بالحالة.")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    if args.rules:
        for k, v in RULES.items():
            print(f"{k:<14} {v}")
        print("\nملفّات مستثناة كليًّا:")
        for k, v in SKIP_FILES.items():
            print(f"  {k}  —  {v}")
        return 0
    findings, skipped = inventory(files=args.file)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"summary": summarize(findings), "skipped": skipped,
                       "findings": [asdict(f) for f in findings]}, fh, ensure_ascii=False, indent=1)
    if args.file:
        for f in findings:
            if args.status and f.status != args.status:
                continue
            print(f"{f.file}:{f.line} [{f.status}{':' + f.reason if f.reason else ''}] "
                  f"{f.ctx} {f.scope} {','.join(f.flags)} | {f.text[:100]}")
        return 0
    if args.leaks:
        for f in findings:
            if f.status == "leak":
                print(f"{f.file}:{f.line}\t{f.ctx}\t{f.text[:120]}")
        return 0
    s = summarize(findings)
    print(json.dumps({k: v for k, v in s.items()}, ensure_ascii=False, indent=1))
    per: dict[str, int] = {}
    for f in findings:
        if f.status == "leak":
            per[f.file] = per.get(f.file, 0) + 1
    print(f"\nملفات بها تسريب: {len(per)}")
    for fn, c in sorted(per.items(), key=lambda kv: -kv[1])[:args.top]:
        print(f"{c:>6}  {fn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
