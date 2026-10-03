#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""القائمة الشاملة للترجمة — ملف واحد يُترجَم ثم يُستورَد.

سير العمل (مرّة واحدة، ميكانيكيّ):
    python tools/i18n_master.py sync      # يستخرج كل النصوص المُغلَّفة ⇒ translations/messages.pot
    python tools/i18n_master.py export    # ⇒ translations/MASTER.csv  (UTF-8 BOM — يفتح بالإكسل)
    … املأ الخانات الفارغة (en / fr / tr / es) …
    python tools/i18n_master.py import    # MASTER.csv ⇒ ملفّات .po ⇒ تصريف .mo
    python tools/i18n_master.py stats     # كم خانة فارغة لكل لغة

أعمدة MASTER.csv:
    msgid     النصّ العربيّ (المفتاح — لا تعدّله)
    context   سياق pgettext إن وُجد (مثل weekday) — لا تعدّله
    en fr tr es  الترجمات
    status    ok / missing: en,fr…  (للقراءة فقط)
    where     أوّل موضع استعمال file:line (للقراءة فقط)

قواعد الاستيراد (آليّة):
    • العناصر النائبة يجب أن تطابق المصدر حرفيًّا: %(name)s ، %s ، {name} ، {…}.
    • «%» المفردة تُهرَّب «%%» تلقائيًّا حين يكون النصّ نصّ تنسيق أو مستعملًا في قالب
      Jinja (gettext في Jinja يطبّق ``%`` دائمًا — «%» مفردة = خطأ 500).
    • خانة فارغة لا تمسح ترجمة موجودة. ترجمة مطابقة للعربيّ تُتجاهَل.
    • الكتابة «جراحيّة» في .po (تعديل سطور msgstr أو إلحاق مُدخل) — لا إعادة تنسيق للملف.

بلا اعتماد خارج Babel (المثبّت مع Flask-Babel).
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import re
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRANS = os.path.join(ROOT, "translations")
POT = os.path.join(TRANS, "messages.pot")
JS_MSGIDS = os.path.join(TRANS, "js_msgids.json")
MASTER = os.path.join(TRANS, "MASTER.csv")
BABEL_CFG = os.path.join(ROOT, "babel.cfg")
LOCALES = ("en", "fr", "tr", "es")

#: مفاتيح الاستخراج: افتراضيّات Babel + أغلفة المشروع.
EXTRA_KEYWORDS = {"_l": None, "_tr": None, "N_": None, "hrT": None, "lazy_gettext": None}

#: نفس تعبير الاختبار tests/test_i18n_complete_locales.py (تطابق النوائب).
TOKEN_RE = re.compile(r"%\([^)]+\)[sd]|%[sd]|\{[^}]*\}")
_LONE_PCT = re.compile(r"(?<!%)%(?![%(sdifr])")


def _po_path(locale: str) -> str:
    return os.path.join(TRANS, locale, "LC_MESSAGES", "messages.po")


def _mo_path(locale: str) -> str:
    return os.path.join(TRANS, locale, "LC_MESSAGES", "messages.mo")


def _mid(m) -> str:
    return m.id if isinstance(m.id, str) else m.id[0]


def _key(m):
    return (m.context or "", _mid(m))


# ═══════════════════════ الاستخراج ═══════════════════════

def _dir_filter(dirpath) -> bool:
    """امشِ داخل app/ فقط، بما فيها مجلّدات «_» (app/templates/_partials و_components) —
    مرشِّح Babel الافتراضيّ يتخطّاها فكانت نصوصها المُغلَّفة خارج الكتالوج كليًّا."""
    rel = os.path.relpath(os.path.abspath(dirpath), ROOT)
    base = os.path.basename(rel)
    if base.startswith(".") or base in ("__pycache__", "node_modules"):
        return False
    return rel == "." or rel == "app" or rel.startswith("app" + os.sep)


def extract_catalog():
    """يستخرج كل msgid من الكود (قوالب + بايثون + JS) — نفس pybabel extract."""
    from babel.messages.catalog import Catalog
    from babel.messages.extract import DEFAULT_KEYWORDS, extract_from_dir
    from babel.messages.frontend import parse_mapping_cfg

    keywords = dict(DEFAULT_KEYWORDS)
    keywords.update(EXTRA_KEYWORDS)
    with open(BABEL_CFG, encoding="utf-8") as fh:
        method_map, options_map = parse_mapping_cfg(fh)
    cat = Catalog(project="HobeRadius", charset="utf-8", fuzzy=False)
    for filename, lineno, message, comments, context in extract_from_dir(
            ROOT, method_map, options_map, keywords=keywords, comment_tags=(),
            strip_comment_tags=True, directory_filter=_dir_filter):
        cat.add(message, None, [(filename.replace("\\", "/"), lineno)], auto_comments=comments,
                context=context)
    for msgid, rel, lineno in js_messages():
        cat.add(msgid, None, [(rel, lineno)])
    return cat


def js_messages():
    """[(msgid, file, line)] لكل hrT('…') في app/static/js — عبر مُرمِّز الجرد."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import i18n_inventory as inv
    from i18n_wrap_code import js_unescape
    out = []
    for kind, path in inv.iter_sources(ROOT):
        if kind != "js":
            continue
        for f in inv.scan_file(kind, path):
            if f.status == "wrapped" and f.ctx == "js-str":
                text = js_unescape(f.text)
                if text:
                    out.append((text, f.file, f.line))
    return out


def cmd_sync(_args) -> int:
    from babel.messages.pofile import write_po
    cat = extract_catalog()
    import datetime as _dt
    # ترويسة ثابتة (لا تاريخ متغيّر) كي لا يتغيّر الملف بلا سبب
    cat.creation_date = _dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc)
    buf = io.BytesIO()
    write_po(buf, cat, width=76, sort_output=True, omit_header=False)
    data = buf.getvalue()
    with open(POT, "wb") as fh:
        fh.write(data)
    import json
    js_ids = sorted({m for m, _f, _l in js_messages()})
    with open(JS_MSGIDS, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(js_ids, fh, ensure_ascii=False, indent=0)
        fh.write("\n")
    print(f"js_msgids.json: {len(js_ids)} msgid (hrT في JS الثابت)")
    n = len([m for m in cat if m.id])
    print(f"messages.pot: {n} msgid")
    return 0


# ═══════════════════════ قراءة الكتالوجات ═══════════════════════

def read_catalog(path):
    from babel.messages.pofile import read_po
    with open(path, "rb") as fh:
        return read_po(fh)


def translations_map(locale: str) -> dict:
    out = {}
    if not os.path.isfile(_po_path(locale)):
        return out
    for m in read_catalog(_po_path(locale)):
        if not m.id:
            continue
        s = m.string
        if isinstance(s, (list, tuple)):
            s = s[0] if s else ""
        if s and not m.fuzzy:
            out[_key(m)] = s
    return out


def pot_entries():
    """[(key, msgid, context, plural, locations)] من messages.pot مرتّبة بالموضع."""
    cat = read_catalog(POT)
    rows = []
    for m in cat:
        if not m.id:
            continue
        locs = [f"{f}:{ln}" for f, ln in m.locations]
        plural = m.id[1] if not isinstance(m.id, str) else ""
        rows.append((_key(m), _mid(m), m.context or "", plural, locs))
    rows.sort(key=lambda r: (r[4][0].rsplit(":", 1)[0] if r[4] else "~",
                             int(r[4][0].rsplit(":", 1)[1]) if r[4] else 0, r[1]))
    return rows


# ═══════════════════════ التصدير ═══════════════════════

HEADER = ["msgid", "context", "en", "fr", "tr", "es", "status", "where"]


def cmd_export(args) -> int:
    out = args.out or MASTER
    rows = pot_entries()
    tmaps = {lc: translations_map(lc) for lc in LOCALES}
    empty = {lc: 0 for lc in LOCALES}
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL)
        w.writerow(HEADER)
        for key, msgid, ctx, plural, locs in rows:
            cells = []
            missing = []
            for lc in LOCALES:
                t = tmaps[lc].get(key, "")
                if not t:
                    missing.append(lc)
                    empty[lc] += 1
                cells.append(t)
            status = "ok" if not missing else "missing: " + ",".join(missing)
            w.writerow([msgid, ctx, *cells, status, locs[0] if locs else ""])
    print(f"MASTER.csv: {len(rows)} rows → {os.path.relpath(out, ROOT)}")
    for lc in LOCALES:
        print(f"  {lc}: {empty[lc]} empty")
    return 0


# ═══════════════════════ التحقّق ═══════════════════════

def is_format_msgid(msgid: str) -> bool:
    return bool(re.search(r"%\([^)]+\)[sdif]|%[sdif]|%%", msgid))


def fix_translation(msgid: str, text: str, in_template: bool) -> tuple[str | None, str]:
    """يُعيد (الترجمة المُصلَحة أو None, سبب الرفض)."""
    t = text.replace("\r\n", "\n")
    if not t.strip():
        return None, "empty"
    if is_format_msgid(msgid) or in_template:
        t = _LONE_PCT.sub("%%", t)
    if sorted(TOKEN_RE.findall(msgid)) != sorted(TOKEN_RE.findall(t)):
        return None, (f"placeholders differ: source {sorted(set(TOKEN_RE.findall(msgid)))} "
                      f"≠ translation {sorted(set(TOKEN_RE.findall(t)))}")
    if is_format_msgid(msgid) or in_template:
        names = re.findall(r"%\(([^)]+)\)[sd]", msgid)
        try:
            if names:
                t % {n: "x" for n in names}
            elif not re.search(r"%[sd]", msgid):
                t % {}
        except (ValueError, KeyError, TypeError) as exc:
            return None, f"bad %-format: {exc}"
    return t, ""


# ═══════════════════════ كتابة .po جراحيّة ═══════════════════════

def _po_unescape(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(n, "\\" + n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _po_escape(s: str) -> str:
    return (s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
            .replace("\r", "\\r").replace("\n", "\\n"))


def _po_field(name: str, value: str) -> list[str]:
    if "\n" in value.rstrip("\n") or len(value) > 70:
        parts = re.split(r"(?<=\n)", value)
        lines = [f'{name} ""']
        for p in parts:
            if p:
                lines.append(f'"{_po_escape(p)}"')
        return lines
    return [f'{name} "{_po_escape(value)}"']


class PoFile:
    """تعديل ملف .po «جراحيًّا»: كل سطر لم نلمسه يبقى كما هو بايتًا ببايت.

    المُدخل = سطور متتالية غير فارغة. التعديل يستبدل سطور مُدخله فقط؛ المُدخل الجديد
    يُلحَق بنهاية الملف."""

    def __init__(self, path: str):
        self.path = path
        with open(path, encoding="utf-8", newline="") as fh:
            raw = fh.read()
        self.nl = "\r\n" if "\r\n" in raw else "\n"
        self.lines = raw.replace("\r\n", "\n").split("\n")
        self.spans: list[tuple[int, int]] = []     # [start, end) في self.lines
        i = 0
        n = len(self.lines)
        while i < n:
            if self.lines[i].strip() == "":
                i += 1
                continue
            j = i
            while j < n and self.lines[j].strip() != "":
                j += 1
            self.spans.append((i, j))
            i = j
        self.index = {}
        for bi, (a, b) in enumerate(self.spans):
            k = self._block_key(self.lines[a:b])
            if k is not None:
                self.index[k] = bi
        self.replaced: dict[int, list[str]] = {}
        self.appended: list[list[str]] = []
        self.app_index: dict = {}
        self.changed = False

    @staticmethod
    def _field(b: list[str], name: str) -> str | None:
        val = None
        cur = None
        for line in b:
            if line.startswith("#"):
                cur = None
                continue
            m = re.match(r'^(msgctxt|msgid_plural|msgid|msgstr(?:\[\d+\])?)\s+"(.*)"\s*$', line)
            if m:
                cur = m.group(1)
                if cur == name:
                    val = _po_unescape(m.group(2))
                continue
            m = re.match(r'^"(.*)"\s*$', line)
            if m and cur == name:
                val = (val or "") + _po_unescape(m.group(1))
        return val

    def _block_key(self, b):
        if all(line.startswith("#") for line in b):
            return None
        mid = self._field(b, "msgid")
        if mid is None or mid == "":
            return None
        return (self._field(b, "msgctxt") or "", mid)

    def _block(self, key):
        if key in self.app_index:
            return self.appended[self.app_index[key]]
        bi = self.index.get(key)
        if bi is None:
            return None
        if bi in self.replaced:
            return self.replaced[bi]
        a, b = self.spans[bi]
        return self.lines[a:b]

    def get(self, key) -> str | None:
        b = self._block(key)
        return None if b is None else self._field(b, "msgstr")

    @staticmethod
    def _with_msgstr(b: list[str], value: str) -> list[str]:
        out = []
        skipping = False
        for line in b:
            if line.startswith("#,") and "fuzzy" in line:
                flags = [f.strip() for f in line[2:].split(",") if f.strip() and f.strip() != "fuzzy"]
                if flags:
                    out.append("#, " + ", ".join(flags))
                continue
            if line.startswith("msgstr"):
                skipping = True
                continue
            if skipping and line.startswith('"'):
                continue
            skipping = False
            out.append(line)
        out.extend(_po_field("msgstr", value))
        return out

    def set(self, key, value: str, location: str = "", python_format: bool = False):
        if key in self.app_index:
            ai = self.app_index[key]
            self.appended[ai] = self._with_msgstr(self.appended[ai], value)
        elif key in self.index:
            bi = self.index[key]
            self.replaced[bi] = self._with_msgstr(self._block(key), value)
        else:
            ctx, mid = key
            b = []
            if location:
                b.append(f"#: {location}")
            if python_format:
                b.append("#, python-format")
            if ctx:
                b.extend(_po_field("msgctxt", ctx))
            b.extend(_po_field("msgid", mid))
            b.extend(_po_field("msgstr", value))
            self.appended.append(b)
            self.app_index[key] = len(self.appended) - 1
        self.changed = True

    def save(self):
        out = list(self.lines)
        # استبدال من الآخر للأوّل كي لا تتزحزح المواضع
        for bi in sorted(self.replaced, reverse=True):
            a, b = self.spans[bi]
            out[a:b] = self.replaced[bi]
        if self.appended:
            while out and out[-1] == "":
                out.pop()
            for b in self.appended:
                out.append("")
                out.extend(b)
            out.append("")
        text = "\n".join(out)
        if self.nl == "\r\n":
            text = text.replace("\n", "\r\n")
        with open(self.path, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)


# ═══════════════════════ الاستيراد ═══════════════════════

def cmd_import(args) -> int:
    path = args.csv or MASTER
    pot = {r[0]: r for r in pot_entries()}
    norm_index = {}
    for key in pot:
        nk = (key[0], re.sub(r"\s+", " ", key[1]).strip())
        norm_index.setdefault(nk, []).append(key)
    pos = {lc: PoFile(_po_path(lc)) for lc in LOCALES}
    stats = {lc: {"added": 0, "updated": 0, "same": 0} for lc in LOCALES}
    errors = []
    unknown = 0
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        missing_cols = [c for c in ("msgid", *LOCALES) if c not in (rd.fieldnames or [])]
        if missing_cols:
            print(f"✗ أعمدة ناقصة في {path}: {missing_cols}", file=sys.stderr)
            return 2
        for lineno, row in enumerate(rd, start=2):
            msgid = (row.get("msgid") or "").replace("\r\n", "\n")
            ctx = (row.get("context") or "").strip()
            key = (ctx, msgid)
            if key not in pot:
                cands = norm_index.get((ctx, re.sub(r"\s+", " ", msgid).strip()), [])
                if len(cands) == 1:
                    key = cands[0]
                else:
                    unknown += 1
                    continue
            entry = pot[key]
            locs = entry[4]
            in_template = any(l.rsplit(":", 1)[0].endswith(".html") for l in locs)
            for lc in LOCALES:
                cell = row.get(lc) or ""
                if not cell.strip():
                    continue
                if cell.strip() == key[1].strip():
                    continue   # منسوخ من العربيّ = لا ترجمة
                fixed, why = fix_translation(key[1], cell, in_template)
                if fixed is None:
                    errors.append(f"MASTER.csv:{lineno} [{lc}] {key[1][:50]!r}: {why}")
                    continue
                cur = pos[lc].get(key)
                if cur == fixed:
                    stats[lc]["same"] += 1
                    continue
                pos[lc].set(key, fixed, locs[0] if locs else "", is_format_msgid(key[1]))
                stats[lc]["updated" if cur is not None else "added"] += 1
    for lc, po in pos.items():
        if po.changed:
            po.save()
    for lc in LOCALES:
        s = stats[lc]
        print(f"  {lc}: +{s['added']} new, {s['updated']} updated, {s['same']} unchanged")
    if unknown:
        print(f"  ⚠ {unknown} صفًّا msgid غير موجود في messages.pot (شغّل sync أولًا؟) — تُجوهلت")
    if errors:
        print(f"  ✗ {len(errors)} ترجمة مرفوضة (لم تُكتب):")
        for e in errors[:200]:
            print("    " + e)
    if not args.no_compile:
        cmd_compile(args)
    return 1 if errors else 0


def cmd_compile(_args) -> int:
    from babel.messages.mofile import write_mo
    for lc in ("ar",) + LOCALES:
        po = _po_path(lc)
        if not os.path.isfile(po):
            continue
        cat = read_catalog(po)
        with open(_mo_path(lc), "wb") as fh:
            write_mo(fh, cat, use_fuzzy=False)
    print("  .mo compiled:", ", ".join(("ar",) + LOCALES))
    return 0


def cmd_stats(_args) -> int:
    rows = pot_entries()
    tmaps = {lc: translations_map(lc) for lc in LOCALES}
    print(f"messages.pot: {len(rows)} msgid")
    for lc in LOCALES:
        miss = sum(1 for r in rows if r[0] not in tmaps[lc])
        print(f"  {lc}: {len(rows) - miss} translated, {miss} empty")
    return 0


def cmd_check(_args) -> int:
    """يفحص كل ترجمة موجودة: النوائب و«%» — بلا تعديل."""
    rows = {r[0]: r for r in pot_entries()}
    bad = 0
    for lc in LOCALES:
        for key, t in translations_map(lc).items():
            if key not in rows:
                continue
            in_t = any(l.rsplit(":", 1)[0].endswith(".html") for l in rows[key][4])
            fixed, why = fix_translation(key[1], t, in_t)
            if fixed is None or fixed != t:
                bad += 1
                print(f"[{lc}] {key[1][:60]!r}: {why or 'needs %% escaping'}")
    print(f"{bad} problem(s)")
    return 1 if bad else 0


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    ap = argparse.ArgumentParser(description="القائمة الشاملة للترجمة (MASTER.csv).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync", help="استخراج ⇒ translations/messages.pot")
    p = sub.add_parser("export", help="⇒ translations/MASTER.csv")
    p.add_argument("out", nargs="?")
    p = sub.add_parser("import", help="MASTER.csv ⇒ .po ⇒ .mo")
    p.add_argument("csv", nargs="?")
    p.add_argument("--no-compile", action="store_true")
    sub.add_parser("compile", help=".po ⇒ .mo")
    sub.add_parser("stats", help="عدد الخانات الفارغة لكل لغة")
    sub.add_parser("check", help="فحص النوائب و«%» في الترجمات الموجودة")
    args = ap.parse_args(argv)
    return {"sync": cmd_sync, "export": cmd_export, "import": cmd_import,
            "compile": cmd_compile, "stats": cmd_stats, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
