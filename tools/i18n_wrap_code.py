#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""مُغلِّف آليّ محافظ لتسريبات i18n — بايثون / قوالب / JS الثابت.

يعتمد على تصنيف ``tools/i18n_inventory.py`` (نفس القواعد التي يفرضها الحارس)
ويُغلّف فقط ما صُنِّف «تسريبًا» وفق قواعد آمنة؛ ما لا يطمئنّ إليه يُترك
للمراجعة اليدويّة (يبقى تسريبًا يُظهره الجرد والحارس).

بايثون:
    • داخل دالّة وفي «مصرف رسالة» (flash/fail/استثناء/خطأ/مفتاح عرض/نصّ
      مركّب f-string أو + أو % أو .format) ⇒ ``_tr('…')`` (ترجمة فوريّة).
      f-string ⇒ ``_tr('… %(name)s …', name=expr)``.
    • غير ذلك (ثوابت الوحدة، تسميات، قوائم) ⇒ ``N_('…')`` (وسم؛ يبقى str
      مطابقًا ويُترجَم عند الإخراج فقط).
قوالب Jinja:
    • نصّ HTML ⇒ ``{{ _('…') }}`` (مع ``%(x)s`` لتعابير ``{{ x }}`` البسيطة).
    • سمات العرض (placeholder/title/aria-label/data-confirm…) ⇒ ``{{ _('…') }}``.
    • سلسلة في تعبير Jinja ⇒ ``_('…')``.
    • سلسلة JS داخل <script> ⇒ ``{{ _('…')|tojson }}`` (في سمة on* يُضاف
      ``|forceescape``).
JS الثابت:
    • ``'…'`` ⇒ ``hrT('…')``؛ قالب حرفيّ ``…${x}…`` ⇒ ``hrT('…{x}…', {x: x})``.

الحمايات: لا يُلمس نصّ تقارنه JS (=== / case / includes…)، ولا وسيط منطق، ولا
HTML داخل سلسلة Jinja (مزلق «الأقواس المتداخلة/دمج Markup»)، و``%`` الحرفيّة
تُهرَّب ``%%`` حيث يُطبَّق تنسيق gettext دائمًا (Jinja).

الاستعمال:
    python tools/i18n_wrap_code.py py   [--apply] [files…]
    python tools/i18n_wrap_code.py html [--apply] [files…]
    python tools/i18n_wrap_code.py js   [--apply] [files…]
"""
from __future__ import annotations

import argparse
import ast
import json
import keyword
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import i18n_inventory as inv  # noqa: E402

ROOT = inv.ROOT
PY_COMPARE_SUB: set = set()
NO_COMPARE_GUARD = False
PY_COMPARE_EQ: set = set()

#: ملفّات «محتوى خارجيّ» (صفحات هوتسبوت، سكربتات راوتر، رسائل للمشتركين/تيليجرام،
#: بطاقات/PDF مطبوعة): لغتها ليست لغة واجهة المدير ⇒ وسم N_ فقط (لا ترجمة فوريّة
#: تُغيّر المُولَّد)، وما تبقّى (f-string/HTML) يُبرَّر في قائمة السماح.
N_ONLY = (
    "app/radius/services/hotspot_*.py",
    "app/radius/services/router_onboarding_script.py",
    "app/radius/services/ip_change_script.py",
    "app/radius/services/notifications_engine.py",
    "app/radius/services/comms_bot.py",
    "app/radius/services/admin_alerts.py",
    "app/radius/services/monitoring_digest.py",
    "app/radius/services/card_template_gallery.py",
    "app/radius/services/card_renderer.py",
    "app/radius/services/pdf_theme.py",
)

# ═══════════════════════ أدوات مشتركة ═══════════════════════


def py_quote(s: str) -> str:
    """سلسلة بايثون حرفيّة مقروءة (العربيّة كما هي)."""
    q = "'" if s.count("'") <= s.count('"') else '"'
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == q:
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 32 or ch in "‎‏؜⁦⁧⁨⁩‪‫‬‭‮":
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return q + "".join(out) + q


def jinja_quote(s: str) -> str:
    """سلسلة Jinja حرفيّة (نفس قواعد بايثون)."""
    return py_quote(s)


def _ident_from(expr_src: str, used: set, fallback: str = "v") -> str:
    # أزل مرشِّحات Jinja/الأقواس الخارجيّة: (x|safe) ⇒ x
    expr_src = re.sub(r"\|\s*[A-Za-z_]+(\([^()]*\))?\s*\)?\s*$", "", expr_src.strip())
    if re.match(r"^\(?\s*['\"]", expr_src):
        expr_src = ""
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(\s*\))?\s*$", expr_src.strip())
    name = ""
    if m:
        name = m.group(1)
    # a.b.c ⇒ c ؛ x['key'] ⇒ key
    km = re.search(r"\[\s*['\"]([A-Za-z_][A-Za-z0-9_]*)['\"]\s*\]\s*$", expr_src.strip())
    if km:
        name = km.group(1)
    if not name or keyword.iskeyword(name) or name in ("_", "N_", "_tr") or name.startswith("__"):
        name = fallback
    name = name.lstrip("_") or fallback
    if keyword.iskeyword(name):
        name = fallback
    base = name
    i = 2
    while name in used:
        name = f"{base}{i}"
        i += 1
    used.add(name)
    return name


def js_logic_texts(findings) -> set[str]:
    """نصوص تقارنها JavaScript أو تستعملها مفاتيح/منطقًا — لا تُغلَّف أبدًا آليًّا.

    + عبارات التأكيد المكتوبة متعدّدة الكلمات التي يقارنها الخادم (بايثون)."""
    out = set()
    for f in findings:
        if f.status == "ignored" and f.reason in ("compare", "key", "logic-arg") and \
                f.ctx in ("js-str", "js-tpl"):
            out.add(inv._norm_ws(f.text))
        # عبارة تأكيد مكتوبة: تُحمى فقط إن كانت متعدّدة الكلمات (لا تُعطِّل كلمة «حذف» العامّة)
        if f.status == "ignored" and f.reason == "typed-confirm" and " " in inv._norm_ws(f.text):
            out.add(inv._norm_ws(f.text))
        # عبارة تأكيد يقارنها الخادم بمساواة (confirm != 'حذف البطاقة') ⇒ لا تُغلَّف في القوالب
        if f.kind == "py" and f.status == "ignored" and (
                f.reason == "typed-confirm" or (f.reason == "compare" and "cmp:eq" in f.flags)) \
                and " " in inv._norm_ws(f.text) and inv.has_letter(f.text):
            out.add(inv._norm_ws(f.text))
    return out


def py_compare_texts(findings) -> tuple[set, set]:
    """نصوص يفحصها بايثون: (احتواء ``'x' in msg``/startswith, مساواة ``== 'x'``).

    رسالة تحوي نصّ احتواء، أو تساوي نصّ مساواة، لا تُترجَم فوريًّا (_tr) كي يبقى
    الفحص صحيحًا بكل اللغات؛ تُوسَم N_ فقط (هويّة في بايثون)."""
    sub, eq = set(), set()
    for f in findings:
        if f.kind == "py" and f.status == "ignored" and f.reason in ("compare", "logic-arg", "key") \
                and len(inv.AR_LETTER.findall(f.text)) >= 3:
            (sub if "cmp:sub" in f.flags else eq).add(f.text.strip())
    return sub, eq


# ═══════════════════════ بايثون ═══════════════════════

SINK_CALLS = {
    "flash", "fail", "abort", "jsonify", "_err", "_fail", "err", "_error", "error_response",
    "_invalid", "_form_error", "status_notice", "_problem", "_reason", "_warn", "_ok",
    "_step", "_deploy_step", "progress", "_signal", "_add_reason", "_notify_customer",
    "_bad", "_json_error", "_api_error", "bad_request", "_denied", "_forbidden",
    "_not_found", "_conflict", "json_error", "api_error", "_msg", "_message", "_note",
    "_info", "_success", "_flash", "set_progress", "_set_progress", "notify", "_check",
    "_audit_conn", "_action", "_emit", "_hdr", "_push", "add_error", "add_warning",
    "_issue", "_finding", "_result", "_res", "make_error", "send_error",
}
SINK_SUFFIX = ("Error", "Exception", "Conflict", "NotFound", "Invalid", "Forbidden",
               "Denied", "Failure", "Warning")
DISPLAY_KEYS = re.compile(
    r"^(label|title|desc|description|message|msg|error|errors|hint|reason|note|notes|text|"
    r"summary|detail|details|help|tip|tooltip|placeholder|caption|heading|subtitle|"
    r"body|content|explanation|likely_causes|suggested_fixes|warning|warnings|info|"
    r"success|status_label|stage_label|action_label|next_action|advice|fix|cause|"
    r"empty|empty_text|button|btn|cta|confirm|prompt|question|answer|name_ar|"
    r"[a-z_]*_(ar|label|title|text|msg|message|hint|desc|description|note|reason|error))$")
DISPLAY_NAMES = DISPLAY_KEYS


def _py_parent_map(tree):
    par = {}
    for n in ast.walk(tree):
        for c in ast.iter_child_nodes(n):
            par[c] = n
    return par


def _is_sink(node, par, f) -> bool:
    flags = f.flags
    for fl in flags:
        if fl.startswith("call:"):
            name = fl[5:]
            if name in SINK_CALLS or name.endswith(SINK_SUFFIX):
                return True
        if fl.startswith("dkey:") and DISPLAY_KEYS.match(fl[5:].lower()):
            return True
        if fl.startswith("kw:") and DISPLAY_KEYS.match((fl[3:] or "").lower()):
            return True
        if fl.startswith("assign:") and DISPLAY_NAMES.match(fl[7:].lower()):
            return True
    p = par.get(node)
    if isinstance(p, ast.BinOp) and isinstance(p.op, (ast.Add, ast.Mod)):
        return True
    if isinstance(p, ast.Attribute) and p.value is node:   # 'نص {x}'.format(...)
        return True
    if isinstance(p, ast.Raise) or (isinstance(p, ast.Call) and isinstance(par.get(p), ast.Raise)):
        return True
    return False


def _fstring_call(node: ast.JoinedStr, src_seg: str) -> str | None:
    """f-string ⇒ ``_tr('… %(x)s …', x=expr)``؛ None إن تعذّر بأمان."""
    parts = []
    kwargs = []
    used: set = set()
    exprs: dict[str, str] = {}
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value).replace("%", "%%"))
            continue
        if not isinstance(v, ast.FormattedValue):
            return None
        expr = ast.unparse(v.value)
        if "\n" in expr:
            return None
        if v.conversion == ord("r"):
            expr_v = f"repr({expr})"
        elif v.conversion == ord("a"):
            expr_v = f"ascii({expr})"
        elif v.conversion == ord("s"):
            expr_v = f"str({expr})"
        else:
            expr_v = expr
        if v.format_spec is not None:
            spec = v.format_spec
            if not (isinstance(spec, ast.JoinedStr) and all(isinstance(x, ast.Constant) for x in spec.values)):
                return None
            spec_s = "".join(str(x.value) for x in spec.values)
            expr_v = f"format({expr_v}, {py_quote(spec_s)})"
        # نفس التعبير مرّتين ⇒ نفس الاسم
        key = expr_v
        if key in exprs:
            name = exprs[key]
        else:
            name = _ident_from(expr, used)
            exprs[key] = name
            kwargs.append(f"{name}={expr_v}")
        parts.append(f"%({name})s")
    msgid = "".join(parts)
    if not kwargs:
        msgid = msgid.replace("%%", "%")
        return f"_tr({py_quote(msgid)})"
    return f"_tr({py_quote(msgid)}, {', '.join(kwargs)})"


def _ensure_py_import(src: str, names: set[str]) -> str:
    if not names:
        return src
    nl = "\r\n" if "\r\n" in src else "\n"
    m = re.search(r"^from app\.i18n_text import ([^\n\r]+?)(?=\r?$)", src, re.M)
    if m:
        have = {x.strip() for x in m.group(1).split(",")}
        allnames = sorted(have | names, key=lambda x: (x != "N_", x))
        return src[:m.start()] + "from app.i18n_text import " + ", ".join(allnames) + src[m.end():]
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    insert_line = 0
    body = tree.body
    idx = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        insert_line = body[0].end_lineno
        idx = 1
    while idx < len(body) and isinstance(body[idx], ast.ImportFrom) and body[idx].module == "__future__":
        insert_line = body[idx].end_lineno
        idx += 1
    lines = src.split("\n")
    stmt = "from app.i18n_text import " + ", ".join(sorted(names, key=lambda x: (x != "N_", x)))
    if "\r\n" in src:
        stmt += "\r"
    lines.insert(insert_line, stmt)
    return "\n".join(lines)


def upgrade_python(path: str, apply: bool, stats: dict) -> int:
    """N_('…') في «مصرف رسالة» وقت الطلب ⇒ _tr('…') (حين لم يعد حارس المقارنة يمنعه)."""
    import fnmatch
    if any(fnmatch.fnmatch(inv._rel(path), pat) for pat in N_ONLY):
        return 0
    with open(path, encoding="utf-8", newline="") as fh:
        src = fh.read()
    if "N_(" not in src:
        return 0
    tree = ast.parse(src)
    par = _py_parent_map(tree)
    line_starts = [0]
    for m in re.finditer("\n", src):
        line_starts.append(m.end())
    lines = src.split("\n")

    def off(lineno, col):
        line = lines[lineno - 1]
        return line_starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))

    edits = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "N_"
                and len(n.args) == 1 and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)):
            continue
        text = n.args[0].value
        st, rs, flags, scope = inv._classify_py(n, par, set(), text)
        if scope != "runtime" or st != "leak":
            continue
        f = inv.Finding("", n.lineno, "py", "py-str", text, st, rs, scope=scope, flags=flags)
        if not _is_sink(n, par, f):
            continue
        if any(t in text for t in PY_COMPARE_SUB) or text.strip() in PY_COMPARE_EQ:
            continue
        a = off(n.func.lineno, n.func.col_offset)
        edits.append((a, a + 2, "_tr"))
    if not edits:
        return 0
    out = src
    for a, b, rep in sorted(edits, key=lambda e: -e[0]):
        out = out[:a] + rep + out[b:]
    out = _ensure_py_import(out, {"_tr"})
    ast.parse(out)
    stats["upgraded"] = stats.get("upgraded", 0) + len(edits)
    if apply:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
    return len(edits)


def wrap_python(path: str, logic: set[str], apply: bool, stats: dict) -> int:
    with open(path, encoding="utf-8", newline="") as fh:
        src = fh.read()
    findings = inv.scan_python(path, src)
    leaks = [f for f in findings if f.status == "leak"]
    if not leaks:
        return 0
    rules = inv.load_allowlist()
    leaks = [f for f in leaks if inv._allow_match(f, rules) is None]
    import fnmatch
    n_only = any(fnmatch.fnmatch(inv._rel(path), pat) for pat in N_ONLY)
    tree = ast.parse(src)
    par = _py_parent_map(tree)
    # فهرس العُقد بالإزاحة
    line_starts = [0]
    for m in re.finditer("\n", src):
        line_starts.append(m.end())
    lines = src.split("\n")

    def off(lineno, col):
        line = lines[lineno - 1]
        return line_starts[lineno - 1] + len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))

    nodes = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.Constant, ast.JoinedStr)) and hasattr(n, "lineno"):
            k = (off(n.lineno, n.col_offset), off(n.end_lineno, n.end_col_offset))
            # بايثون 3.11: أجزاء f-string تحمل موضع الـ JoinedStr نفسه ⇒ الأولويّة له
            if isinstance(nodes.get(k), ast.JoinedStr):
                continue
            nodes[k] = n
    edits = []
    used = set()
    for f in leaks:
        node = nodes.get((f.start, f.end))
        if node is None:
            stats["skip:no-node"] = stats.get("skip:no-node", 0) + 1
            continue
        seg = src[f.start:f.end]
        if "html" in f.flags and len(f.text) > 400:
            stats["skip:big-html"] = stats.get("skip:big-html", 0) + 1
            continue
        sink = f.scope == "runtime" and (_is_sink(node, par, f) or isinstance(node, ast.JoinedStr))
        if n_only:
            if isinstance(node, ast.JoinedStr):
                stats["skip:n-only-fstring"] = stats.get("skip:n-only-fstring", 0) + 1
                continue
            sink = False
        if sink and not NO_COMPARE_GUARD and (
                any(t in f.text for t in PY_COMPARE_SUB) or f.text.strip() in PY_COMPARE_EQ):
            if isinstance(node, ast.JoinedStr):
                stats["skip:compare-guard-fstring"] = stats.get("skip:compare-guard-fstring", 0) + 1
                continue
            sink = False
            stats["N_:compare-guard"] = stats.get("N_:compare-guard", 0) + 1
        if not sink and inv._norm_ws(f.text) in logic:
            stats["skip:js-logic"] = stats.get("skip:js-logic", 0) + 1
            continue
        if isinstance(node, ast.JoinedStr):
            if f.scope != "runtime":
                stats["skip:import-fstring"] = stats.get("skip:import-fstring", 0) + 1
                continue
            rep = _fstring_call(node, seg)
            if rep is None:
                stats["skip:fstring-complex"] = stats.get("skip:fstring-complex", 0) + 1
                continue
            edits.append((f.start, f.end, rep))
            used.add("_tr")
            stats["_tr:fstring"] = stats.get("_tr:fstring", 0) + 1
            continue
        # بايتات/raw ⇒ تخطَّ
        if re.match(r"^[rRbBuU]*[bB]", seg):
            continue
        fn = "_tr" if sink else "N_"
        edits.append((f.start, f.end, f"{fn}({seg})"))
        used.add(fn)
        stats[fn] = stats.get(fn, 0) + 1
    if not edits:
        return 0
    # أزل المتداخل (الخارجيّ يكسب؛ الداخليّ في الجولة التالية)
    edits.sort(key=lambda e: (e[0], -e[1]))
    kept = []
    last_end = -1
    for e in edits:
        if e[0] < last_end:
            continue
        kept.append(e)
        last_end = e[1]
    out = src
    for s, e, rep in sorted(kept, key=lambda x: -x[0]):
        out = out[:s] + rep + out[e:]
    out = _ensure_py_import(out, used)
    try:
        ast.parse(out)
    except SyntaxError as exc:
        print(f"!! syntax error after wrap, skipped: {inv._rel(path)}: {exc}", file=sys.stderr)
        stats["file-syntax-fail"] = stats.get("file-syntax-fail", 0) + 1
        return 0
    if apply:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
    return len(kept)


# ═══════════════════════ JS الثابت ═══════════════════════

JS_SHIM = ("var hrT = window.hrT || function (s, o) { var d = window.HR_I18N || {}; "
           "var t = Object.prototype.hasOwnProperty.call(d, s) ? d[s] : s; "
           "if (o) { for (var k in o) { t = String(t).split('{' + k + '}').join(o[k]); } } return t; };"
           "  // i18n — انظر I18N.md")


def js_unescape(body: str) -> str | None:
    """يفكّ هروب سلسلة JS حرفيّة إلى نصّها الحقيقيّ (None إن غير مدعوم)."""
    out = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nx = body[i + 1:i + 2]
        if nx == "n":
            out.append("\n")
        elif nx == "t":
            out.append("\t")
        elif nx == "r":
            out.append("\r")
        elif nx in ("'", '"', "\\", "`", "/", "$", "{", "}"):
            out.append(nx)
        elif nx == "u":
            m = re.match(r"u([0-9a-fA-F]{4})|u\{([0-9a-fA-F]+)\}", body[i + 1:])
            if not m:
                return None
            out.append(chr(int(m.group(1) or m.group(2), 16)))
            i += 1 + len(m.group(0))
            continue
        elif nx == "x":
            m = re.match(r"x([0-9a-fA-F]{2})", body[i + 1:])
            if not m:
                return None
            out.append(chr(int(m.group(1), 16)))
            i += 4
            continue
        elif nx == "\n":
            pass
        else:
            return None
        i += 2
    return "".join(out)


def js_quote(s: str) -> str:
    q = "'"
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == q:
            out.append("\\'")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch in "  ":
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    return q + "".join(out) + q


def _js_tpl_to_call(src: str, tok: inv.JsTok, fn: str) -> str | None:
    """قالب حرفيّ ⇒ hrT('…{a}…', {a: expr}). None إن احتوى HTML أو تعقيدًا."""
    parts = []
    objs = []
    used: set = set()
    seen: dict = {}
    for kind, a, b in tok.parts:
        if kind == "text":
            t = js_unescape(src[a:b])
            if t is None:
                return None
            if "{" in t or "}" in t:
                return None
            parts.append(t)
        else:
            expr = src[a:b].strip()
            if not expr or "`" in expr or "\n" in expr:
                return None
            if expr in seen:
                name = seen[expr]
            else:
                name = _ident_from(expr, used)
                seen[expr] = name
                objs.append(f"{name}: {expr}" if name != expr else name)
            parts.append("{" + name + "}")
    text = "".join(parts)
    if re.search(r"<[a-zA-Z/!]", text):
        return None
    if not objs:
        return f"{fn}({js_quote(text)})"
    return f"{fn}({js_quote(text)}, {{{', '.join(objs)}}})"


_HTML_SPLIT = re.compile(r"(<[^<>]*>|&[a-zA-Z]+;|&#\d+;)")


def _split_ws(t: str):
    lead = len(t) - len(t.lstrip())
    trail = len(t) - len(t.rstrip())
    return t[:lead], t[lead:len(t) - trail], t[len(t) - trail:]


def js_str_html_parts(text: str, wrap) -> str | None:
    """نصّ سلسلة JS يحوي HTML ⇒ تعبير دمج يغلّف أجزاء النصّ العربيّة فقط.

    wrap(core) يُعيد تعبير JS للنصّ المترجَم. يُعيد None إن لا شيء يُغلَّف."""
    pieces = [x for x in _HTML_SPLIT.split(text) if x != ""]
    out = []
    changed = False
    for piece in pieces:
        if _HTML_SPLIT.fullmatch(piece) or not inv.has_letter(piece):
            out.append(js_quote(piece))
            continue
        lead, core, trail = _split_ws(piece)
        if lead:
            out.append(js_quote(lead))
        out.append(wrap(core))
        changed = True
        if trail:
            out.append(js_quote(trail))
    if not changed:
        return None
    return "(" + " + ".join(out) + ")"


def js_tpl_html(src: str, tok: inv.JsTok, wrap_call) -> str | None:
    """قالب حرفيّ بـ HTML ⇒ نفس القالب مع ${wrap('نصّ {a}', {a: expr})} لكل عقدة نصّ عربيّة.

    wrap_call(msg, objs) يُعيد تعبير JS."""
    # سلسلة عناصر: ('c', حرف) أو ('e', تعبير)
    items = []
    for kind, a, b in tok.parts:
        if kind == "text":
            raw = src[a:b]
            if "{" in (js_unescape(raw) or "{") or "}" in (js_unescape(raw) or ""):
                return None
            # نحتفظ بالخام لإعادة البناء، والمفكوك للرسالة
            i = 0
            while i < len(raw):
                if raw[i] == "\\":
                    items.append(("c", raw[i:i + 2]))
                    i += 2
                    continue
                items.append(("c", raw[i]))
                i += 1
        else:
            expr = src[a:b].strip()
            if not expr or "`" in expr or "\n" in expr:
                return None
            items.append(("e", expr))
    # قسّم على الوسوم في الأحرف
    joined = "".join(x[1] if x[0] == "c" else "\x00" for x in items)
    exprs = [x[1] for x in items if x[0] == "e"]
    segs = [x for x in _HTML_SPLIT.split(joined) if x != ""]
    out = []
    ei = 0
    changed = False
    for seg in segs:
        n_e = seg.count("\x00")
        seg_exprs = exprs[ei:ei + n_e]
        ei += n_e
        if _HTML_SPLIT.fullmatch(seg) or not inv.has_letter(seg.replace("\x00", "")):
            # أعِد البناء كما هو
            k = 0
            buf = []
            for ch in seg:
                if ch == "\x00":
                    buf.append("${" + seg_exprs[k] + "}")
                    k += 1
                else:
                    buf.append(ch)
            out.append("".join(buf))
            continue
        lead, core, trail = _split_ws(seg)
        used: set = set()
        seen: dict = {}
        objs = []
        msg = []
        k = 0
        for ch in core:
            if ch == "\x00":
                expr = seg_exprs[lead.count("\x00") + k]
                k += 1
                if expr in seen:
                    name = seen[expr]
                else:
                    name = _ident_from(expr, used)
                    seen[expr] = name
                    objs.append(f"{name}: {expr}" if name != expr else name)
                msg.append("{" + name + "}")
            else:
                msg.append(ch)
        if "\x00" in lead or "\x00" in trail:
            return None
        text = js_unescape("".join(msg))
        if text is None:
            return None
        out.append(lead + "${" + wrap_call(text, objs) + "}" + trail)
        changed = True
    if not changed:
        return None
    return "`" + "".join(out) + "`"


def wrap_js(path: str, logic: set[str], apply: bool, stats: dict) -> int:
    with open(path, encoding="utf-8", newline="") as fh:
        src = fh.read()
    findings = inv.scan_js(path, src)
    leaks = [f for f in findings if f.status == "leak"]
    rules = inv.load_allowlist()
    leaks = [f for f in leaks if inv._allow_match(f, rules) is None]
    if not leaks:
        return 0
    toks = {(t.start, t.end): t for t in inv.js_tokenize(src) if t.type in ("str", "tpl")}
    edits = []
    for f in leaks:
        if inv._norm_ws(f.text) in logic:
            stats["skip:js-logic"] = stats.get("skip:js-logic", 0) + 1
            continue
        tok = toks.get((f.start, f.end))
        if tok is None:
            stats["skip:no-tok"] = stats.get("skip:no-tok", 0) + 1
            continue
        if tok.type == "str":
            text = js_unescape(src[tok.start + 1:tok.end - 1])
            if text is None:
                stats["skip:escape"] = stats.get("skip:escape", 0) + 1
                continue
            if re.search(r"<[a-zA-Z/!]|&[a-zA-Z#0-9]+;", text):
                rep = js_str_html_parts(text, lambda core: f"hrT({js_quote(core)})")
                if rep is None:
                    continue
                edits.append((tok.start, tok.end, rep))
                stats["hrT:html"] = stats.get("hrT:html", 0) + 1
                continue
            edits.append((tok.start, tok.end, f"hrT({js_quote(text)})"))
            stats["hrT"] = stats.get("hrT", 0) + 1
        else:
            rep = _js_tpl_to_call(src, tok, "hrT")
            if rep is None:
                rep = js_tpl_html(src, tok, lambda msg, objs: f"hrT({js_quote(msg)}" +
                                  (f", {{{', '.join(objs)}}})" if objs else ")"))
                if rep is not None:
                    stats["hrT:tpl-html"] = stats.get("hrT:tpl-html", 0) + 1
            if rep is None:
                stats["skip:tpl-complex"] = stats.get("skip:tpl-complex", 0) + 1
                continue
            edits.append((tok.start, tok.end, rep))
            stats["hrT:tpl"] = stats.get("hrT:tpl", 0) + 1
    if not edits:
        return 0
    edits.sort(key=lambda e: (e[0], -e[1]))
    kept, last = [], -1
    for e in edits:
        if e[0] < last:
            continue
        kept.append(e)
        last = e[1]
    out = src
    for s, e, rep in sorted(kept, key=lambda x: -x[0]):
        out = out[:s] + rep + out[e:]
    if "var hrT = window.hrT ||" not in out:
        nl = "\r\n" if "\r\n" in out else "\n"
        # بعد 'use strict' العلويّ إن وُجد، وإلا أوّل سطر
        m = re.match(r"(\s*(?:/\*.*?\*/\s*|//[^\n]*\n\s*)*)(['\"]use strict['\"];?[^\n]*\n)?", out, re.S)
        pos = m.end() if m else 0
        out = out[:pos] + JS_SHIM + nl + out[pos:]
    if apply:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
    return len(kept)


# ═══════════════════════ قوالب Jinja ═══════════════════════

_SIMPLE_EXPR = re.compile(r"^\{\{\s*(?P<e>[^{}%]+?)\s*\}\}$", re.S)


def _tpl_msg_with_vars(src: str, a: int, b: int, regions, used: set):
    """نصّ [a,b) قد يحوي {{ expr }} بسيطة ⇒ (msgid, kwargs) أو None."""
    parts = []
    kwargs = []
    seen = {}
    pos = a
    for kind, s, e, is_, ie in regions:
        if kind == "data" or e <= a or s >= b:
            continue
        if s < a or e > b:
            return None
        if kind != "expr":
            return None
        raw = src[s:e]
        if raw.startswith("{{-") or raw.endswith("-}}") or raw.startswith("{{+"):
            return None
        m = _SIMPLE_EXPR.match(raw)
        if not m:
            return None
        expr = m.group("e").strip()
        if re.search(r"\b(_|gettext|ngettext|pgettext)\s*\(", expr) or inv.has_arabic(expr):
            return None
        parts.append(src[pos:s].replace("%", "%%"))
        if expr in seen:
            name = seen[expr]
        else:
            name = _ident_from(expr, used)
            seen[expr] = name
            kwargs.append((name, expr))
        parts.append(f"%({name})s")
        pos = e
    parts.append(src[pos:b].replace("%", "%%"))
    return "".join(parts), kwargs


_CHAIN_STOP_WORDS = {"if", "else", "and", "or", "not", "in", "is", "for", "recursive", "with",
                     "without", "context", "import", "as", "set", "elif", "return"}
_CHAIN_STOP_OPS = {",", "=", ":", "==", "!=", "<", ">", "<=", ">=", "?"}


def _jinja_chain(src: str, inner_s: int, inner_e: int, tok_start: int):
    """سلسلة دمج «~» حول سلسلة عربيّة ⇒ (start, end, msgid, kwargs) أو None.

    "تجهيز «" ~ device.name ~ "» للإدارة" ⇒ _('تجهيز «%(name)s» للإدارة', name=device.name)."""
    toks = inv.jinja_tokens(src, inner_s, inner_e)
    idx = next((i for i, t in enumerate(toks) if t.start == tok_start), None)
    if idx is None:
        return None

    def is_boundary(i):
        t = toks[i]
        return (t.type == "op" and t.value in _CHAIN_STOP_OPS) or                (t.type == "name" and t.value in _CHAIN_STOP_WORDS)

    # يسارًا
    depth = 0
    a = idx
    i = idx - 1
    while i >= 0:
        t = toks[i]
        if t.type == "op" and t.value in ")]}":
            depth += 1
        elif t.type == "op" and t.value in "([{":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and is_boundary(i):
            break
        a = i
        i -= 1
    depth = 0
    b = idx
    i = idx + 1
    while i < len(toks):
        t = toks[i]
        if t.type == "op" and t.value in "([{":
            depth += 1
        elif t.type == "op" and t.value in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and is_boundary(i):
            break
        b = i
        i += 1
    # قسّم على ~ في العمق 0 (أو على + إن كانت السلسلة كلّها + بلا ~: دمج نصوص)
    ops_at0 = set()
    depth = 0
    for t in toks[a:b + 1]:
        if t.type == "op" and t.value in "([{":
            depth += 1
        elif t.type == "op" and t.value in ")]}":
            depth -= 1
        elif depth == 0 and t.type == "op" and t.value in ("~", "+", "-", "*", "/", "%", "//", "**"):
            ops_at0.add(t.value)
    if "~" in ops_at0:
        if ops_at0 - {"~"}:
            return None   # خليط ~ مع حساب — يدويّ
        sep = "~"
    elif ops_at0 == {"+"}:
        sep = "+"
    else:
        return None
    operands = []
    cur = []
    depth = 0
    for t in toks[a:b + 1]:
        if t.type == "op" and t.value in "([{":
            depth += 1
        elif t.type == "op" and t.value in ")]}":
            depth -= 1
        if depth == 0 and t.type == "op" and t.value == sep:
            operands.append(cur)
            cur = []
            continue
        cur.append(t)
    operands.append(cur)
    if len(operands) < 2 or any(not o for o in operands):
        return None
    parts = []
    kwargs = []
    used: set = set()
    seen = {}
    has_ar = False
    for o in operands:
        if len(o) == 1 and o[0].type == "str":
            txt = inv.jinja_str_value(o[0])
            if not isinstance(txt, str) or re.search(r"<[a-zA-Z/!]", txt) or "%" in txt:
                return None
            has_ar = has_ar or inv.has_letter(txt)
            parts.append(txt)
            continue
        if any(x.type == "str" and inv.has_arabic(inv.jinja_str_value(x) or "") for x in o):
            return None
        expr = src[o[0].start:o[-1].end]
        if re.search(r"\b(_|gettext|ngettext)\s*\(", expr):
            return None
        if expr in seen:
            name = seen[expr]
        else:
            name = _ident_from(expr, used)
            seen[expr] = name
            kwargs.append((name, expr))
        parts.append(f"%({name})s")
    if not has_ar:
        return None
    return toks[a].start, toks[b].end, "".join(parts), kwargs


def _call_src(msgid: str, kwargs) -> str:
    q = jinja_quote(msgid)
    if not kwargs:
        return f"_({q})"
    return f"_({q}, " + ", ".join(f"{k}={v}" for k, v in kwargs) + ")"


def wrap_template(path: str, logic: set[str], apply: bool, stats: dict) -> int:
    with open(path, encoding="utf-8", newline="") as fh:
        src = fh.read()
    findings = inv.scan_template(path, src)
    rules = inv.load_allowlist()
    leaks = [f for f in findings if f.status == "leak" and inv._allow_match(f, rules) is None]
    if not leaks:
        return 0
    regions = inv.jinja_regions(src)
    file_logic = set(logic)
    for f in findings:
        if f.status == "ignored" and f.reason in ("compare", "key", "logic-arg"):
            file_logic.add(inv._norm_ws(f.text))
    # نطاقات <pre>/<textarea> — المسافات فيها مرئيّة
    pre_ranges = [(m.start(), m.end()) for m in re.finditer(r"<(pre|textarea)\b.*?</\1\s*>", src, re.S | re.I)]
    edits = []

    def add(s, e, rep, key):
        edits.append((s, e, rep))
        stats[key] = stats.get(key, 0) + 1

    for f in leaks:
        norm = inv._norm_ws(f.text)
        if norm in file_logic:
            stats["skip:logic"] = stats.get("skip:logic", 0) + 1
            continue
        if f.ctx == "jinja-str":
            if "html" in f.flags:
                stats["skip:jinja-html"] = stats.get("skip:jinja-html", 0) + 1
                continue
            if "concat" in f.flags:
                reg = next((r for r in regions if r[3] <= f.start < r[4]), None)
                # سلسلة «~» نظيفة (لا HTML حرفيّ في أجزائها النصّيّة) ⇒ رسالة واحدة بنوائب
                ch = _jinja_chain(src, reg[3], reg[4], f.start) if reg is not None else None
                if ch is not None:
                    cs, ce, msgid, kwargs = ch
                    add(cs, ce, _call_src(msgid, kwargs), "jinja-chain")
                    continue
                # الدمج مع سلسلة HTML حرفيّة في نفس التعبير ⇒ يدويّ (مزلق دمج Markup)
                if reg is None or re.search(r"['\"][^'\"]*<[a-zA-Z/!]", src[reg[3]:reg[4]]):
                    stats["skip:concat-html"] = stats.get("skip:concat-html", 0) + 1
                    continue
            text = f.text
            if re.search(r"%(?!%)", text.replace("%%", "")):
                text = text.replace("%", "%%")
            if "%(" in f.text or "%s" in f.text:
                stats["skip:jinja-percent"] = stats.get("skip:jinja-percent", 0) + 1
                continue
            add(f.start, f.end, _call_src(text, []), "jinja-str")
            continue
        if f.ctx == "html-text":
            in_pre = any(a <= f.start < b for a, b in pre_ranges)
            used: set = set()
            res = _tpl_msg_with_vars(src, f.start, f.end, regions, used)
            if res is None:
                # قسّم على مناطق Jinja: غلّف كل جزء نصّيّ يحوي حرفًا عربيًّا
                pos = f.start
                segs = []
                for kind, s, e, is_, ie in regions:
                    if kind == "data" or e <= f.start or s >= f.end:
                        continue
                    segs.append((pos, s))
                    pos = e
                segs.append((pos, f.end))
                for a, b in segs:
                    seg = src[a:b]
                    if not inv.has_letter(seg):
                        continue
                    if "<" in seg or ">" in seg:
                        stats["skip:text-angle"] = stats.get("skip:text-angle", 0) + 1
                        continue
                    lead = len(seg) - len(seg.lstrip())
                    trail = len(seg) - len(seg.rstrip())
                    core = seg[lead:len(seg) - trail]
                    msg = core if in_pre else inv._norm_ws(core)
                    if inv._norm_ws(core) in file_logic:
                        continue
                    add(a + lead, b - trail, "{{ " + _call_src(msg.replace("%", "%%"), []) + " }}",
                        "html-text-seg")
                continue
            msgid, kwargs = res
            if "<" in msgid or ">" in msgid:
                stats["skip:text-angle"] = stats.get("skip:text-angle", 0) + 1
                continue
            if not in_pre:
                msgid = inv._norm_ws(msgid)
            add(f.start, f.end, "{{ " + _call_src(msgid, kwargs) + " }}",
                "html-text-vars" if kwargs else "html-text")
            continue
        if f.ctx.startswith("attr:"):
            if "display-attr" not in f.flags:
                stats["skip:attr-unknown"] = stats.get("skip:attr-unknown", 0) + 1
                continue
            used = set()
            res = _tpl_msg_with_vars(src, f.start, f.end, regions, used)
            if res is None:
                stats["skip:attr-complex"] = stats.get("skip:attr-complex", 0) + 1
                continue
            msgid, kwargs = res
            if "&" in msgid or '"' in msgid:
                stats["skip:attr-entity"] = stats.get("skip:attr-entity", 0) + 1
                continue
            lead = len(msgid) - len(msgid.lstrip())
            raw = src[f.start:f.end]
            l2 = len(raw) - len(raw.lstrip())
            t2 = len(raw) - len(raw.rstrip())
            add(f.start + l2, f.end - t2, "{{ " + _call_src(inv._norm_ws(msgid), kwargs) + " }}", "attr")
            continue
        if f.ctx in ("js-str", "js-tpl"):
            in_attr = any(fl.startswith("in-attr:") for fl in f.flags)
            raw = src[f.start:f.end]
            if f.ctx == "js-str":
                body = raw[1:-1]
                used = set()
                res = _tpl_msg_with_vars(src, f.start + 1, f.end - 1, regions, used)
                if res is None:
                    stats["skip:js-jinja"] = stats.get("skip:js-jinja", 0) + 1
                    continue
                msgid, kwargs = res
                if kwargs:
                    # تعابير Jinja داخل سلسلة JS ⇒ لا نفكّ هروب JS حولها
                    if "\\" in msgid:
                        stats["skip:js-escape"] = stats.get("skip:js-escape", 0) + 1
                        continue
                    text = msgid
                else:
                    t = js_unescape(body)
                    if t is None:
                        stats["skip:js-escape"] = stats.get("skip:js-escape", 0) + 1
                        continue
                    text = t.replace("%", "%%")
                if in_attr and ("&" in text):
                    stats["skip:attr-entity"] = stats.get("skip:attr-entity", 0) + 1
                    continue
                filt = "|tojson|forceescape" if in_attr else "|tojson"
                if re.search(r"<[a-zA-Z/!]|&[a-zA-Z#0-9]+;", text):
                    if kwargs:
                        stats["skip:js-html-vars"] = stats.get("skip:js-html-vars", 0) + 1
                        continue
                    rep = js_str_html_parts(
                        text.replace("%%", "%"),
                        lambda core: "{{ " + _call_src(core.replace("%", "%%"), []) + filt + " }}")
                    if rep is None:
                        continue
                    if in_attr and '"' in rep:
                        stats["skip:attr-quote"] = stats.get("skip:attr-quote", 0) + 1
                        continue
                    add(f.start, f.end, rep, "js-str-html")
                    continue
                add(f.start, f.end, "{{ " + _call_src(text, kwargs) + filt + " }}", "js-str")
                continue
            # قالب حرفيّ داخل <script>: نصّ بلا Jinja فقط
            if "jinja-inside" in f.flags:
                stats["skip:js-tpl-jinja"] = stats.get("skip:js-tpl-jinja", 0) + 1
                continue
            tok = next((t for t in inv.js_tokenize(src, f.start, f.end) if t.start == f.start and t.type == "tpl"), None)
            if tok is None:
                continue
            rep = _js_tpl_to_call(src, tok, "__HRT__")
            if rep is None:
                stats["skip:js-tpl-complex"] = stats.get("skip:js-tpl-complex", 0) + 1
                continue
            m = re.match(r"__HRT__\((?P<q>'(?:\\.|[^'\\])*')(?:, (?P<o>\{.*\}))?\)$", rep, re.S)
            text = js_unescape(m.group("q")[1:-1]).replace("%", "%%")
            js_lit = "{{ " + _call_src(text, []) + ("|tojson|forceescape" if in_attr else "|tojson") + " }}"
            if m.group("o"):
                obj = m.group("o")[1:-1]
                chain = js_lit
                for item in [x.strip() for x in obj.split(", ")]:
                    if ":" in item:
                        k, v = item.split(":", 1)
                        chain += f".split('{{{k.strip()}}}').join({v.strip()})"
                    else:
                        chain += f".split('{{{item}}}').join({item})"
                add(f.start, f.end, "(" + chain + ")", "js-tpl")
            else:
                add(f.start, f.end, js_lit, "js-tpl")
            continue
        stats["skip:" + f.ctx] = stats.get("skip:" + f.ctx, 0) + 1
    if not edits:
        return 0
    edits.sort(key=lambda e: (e[0], -e[1]))
    kept, last = [], -1
    for e in edits:
        if e[0] < last:
            continue
        kept.append(e)
        last = e[1]
    out = src
    for s, e, rep in sorted(kept, key=lambda x: -x[0]):
        out = out[:s] + rep + out[e:]
    # بوّابة: يجب أن يُحلَّل القالب
    try:
        import jinja2
        env = jinja2.Environment(extensions=["jinja2.ext.i18n", "jinja2.ext.do", "jinja2.ext.loopcontrols"])
        env.parse(out)
    except Exception as exc:  # noqa: BLE001
        print(f"!! template parse failed after wrap, skipped: {inv._rel(path)}: {exc}", file=sys.stderr)
        stats["file-parse-fail"] = stats.get("file-parse-fail", 0) + 1
        return 0
    if apply:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(out)
    return len(kept)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["py", "html", "js"])
    ap.add_argument("files", nargs="*")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--upgrade", action="store_true", help="py: N_ في مصرف رسالة ⇒ _tr")
    ap.add_argument("--no-compare-guard", action="store_true",
                    help="py: تجاهل حارس المقارنة (بعد جعل موضع المقارنة مستقلًّا عن اللغة)")
    ap.add_argument("--exclude", action="append", default=[])
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    global NO_COMPARE_GUARD
    NO_COMPARE_GUARD = bool(getattr(args, "no_compare_guard", False))
    findings, _skipped = inv.inventory(allow=True)
    logic = js_logic_texts(findings)
    _sub, _eq = py_compare_texts(findings)
    PY_COMPARE_SUB.update(_sub)
    PY_COMPARE_EQ.update(_eq)
    srcs = [p for k, p in inv.iter_sources() if k == args.kind]
    if args.files:
        want = {inv._rel(f) for f in args.files}
        srcs = [p for p in srcs if inv._rel(p) in want]
    import fnmatch
    stats: dict = {}
    total = 0
    for p in srcs:
        rel = inv._rel(p)
        if inv.skipped_reason(rel) or any(fnmatch.fnmatch(rel, x) for x in args.exclude):
            continue
        fn = {"py": wrap_python, "html": wrap_template, "js": wrap_js}[args.kind]
        if args.upgrade and args.kind == "py":
            n = upgrade_python(p, args.apply, stats)
        else:
            n = fn(p, logic, args.apply, stats)
        if n:
            total += n
    print(json.dumps({"edits": total, **stats}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
