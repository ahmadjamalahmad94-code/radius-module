# -*- coding: utf-8 -*-
"""حارس i18n: لا نصّ عربيّ مرئيّ خارج القائمة الشاملة، ولا قائمة قديمة.

يفشل إذا:
  (أ) نصّ عربيّ يظهر للمستخدم مكتوب خامًا (غير مُغلَّف) في قالب/بايثون/JS —
      إلا ما بُرِّر صراحةً في tools/i18n_allowlist.txt؛
  (ب) نصّ مُغلَّف في الكود غير موجود في translations/messages.pot (الكتالوج قديم)؛
  (ج) translations/MASTER.csv لا يطابق messages.pot (القائمة الشاملة قديمة).

فحص ساكن بالكامل (بلا تشغيل التطبيق ولا رندر). شغّل هذا الملف وحده.
الإصلاح دائمًا: غلّف النصّ ثم
    python tools/i18n_master.py sync && python tools/i18n_master.py export
"""
from __future__ import annotations

import csv
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import i18n_inventory as inv  # noqa: E402
import i18n_master as master  # noqa: E402

HOW_TO_WRAP = (
    "كيف تُصلح (How to fix):\n"
    "  • قالب HTML:            {{ _('النص') }}   — داخل تعبير Jinja: _('النص')\n"
    "  • JS داخل قالب:          {{ _('النص')|tojson }}\n"
    "  • بايثون وقت الطلب:       _tr('النص')  /  _tr('تم حذف %(n)s', n=n)   (from app.i18n_text import _tr)\n"
    "  • بايثون ثابت وحدة:       N_('النص')   (from app.i18n_text import N_)\n"
    "  • JS ثابت (static/js):    hrT('النص')  /  hrT('تم {n}', {n: n})\n"
    "  ثم: python tools/i18n_master.py sync && python tools/i18n_master.py export\n"
    "  [in-fstring] استدعاء _tr/N_ داخل {…} في f-string لا يراه المستخرِج: أخرجه إلى متغيّر.\n"
    "  ليس نصّ واجهة؟ أضِف سطرًا مُبرَّرًا إلى tools/i18n_allowlist.txt (مسار | نص | سبب)."
)


@pytest.fixture(scope="module")
def inventory():
    findings, _skipped = inv.inventory(allow=True)
    return findings


def test_no_unwrapped_user_visible_arabic(inventory):
    leaks = [f for f in inventory if f.status == "leak"]
    if leaks:
        lines = [f"  {f.file}:{f.line}  [{f.ctx}{' in-fstring' if 'in-fstring' in f.flags else ''}]  "
                 f"«{inv._norm_ws(f.text)[:90]}»" for f in leaks[:80]]
        more = f"\n  … و{len(leaks) - 80} غيرها" if len(leaks) > 80 else ""
        pytest.fail(
            f"{len(leaks)} نصًّا عربيًّا مرئيًّا غير مُغلَّف (unwrapped user-visible Arabic):\n"
            + "\n".join(lines) + more + "\n\n" + HOW_TO_WRAP,
            pytrace=False)


def test_allowlist_entries_are_justified_and_still_used(inventory):
    rules = inv.load_allowlist()
    assert rules, "tools/i18n_allowlist.txt مفقود أو فارغ"
    unjustified = [r for r in rules if len((r.reason or "").strip()) < 10]
    assert not unjustified, f"أسطر قائمة السماح بلا سبب واضح: {[(r.pattern, r.text) for r in unjustified]}"
    used = set()
    for f in inventory:
        if f.status == "allowlisted":
            r = inv._allow_match(f, rules)
            if r is not None:
                used.add((r.pattern, r.text))
    stale = [(r.pattern, r.text) for r in rules if (r.pattern, r.text) not in used]
    assert not stale, ("أسطر في tools/i18n_allowlist.txt لم تعد تطابق أيّ نصّ — احذفها: "
                       f"{stale}")


@pytest.fixture(scope="module")
def pot_keys():
    assert os.path.isfile(master.POT), "translations/messages.pot مفقود — شغّل: python tools/i18n_master.py sync"
    return {r[0]: r for r in master.pot_entries()}


def test_every_wrapped_msgid_is_in_catalog(pot_keys):
    cat = master.extract_catalog()
    missing = []
    for m in cat:
        if not m.id:
            continue
        key = master._key(m)
        if key not in pot_keys:
            loc = f"{m.locations[0][0]}:{m.locations[0][1]}" if m.locations else "?"
            missing.append(f"  {loc}  «{inv._norm_ws(master._mid(m))[:90]}»")
    if missing:
        pytest.fail(
            f"{len(missing)} نصًّا مُغلَّفًا غير موجود في translations/messages.pot (الكتالوج قديم):\n"
            + "\n".join(missing[:80])
            + "\n\nالإصلاح: python tools/i18n_master.py sync && python tools/i18n_master.py export",
            pytrace=False)


def test_inventory_wrapped_literals_reach_catalog(inventory, pot_keys):
    """شبكة أمان مستقلّة عن مُستخرِج Babel: كل نصّ حرفيّ يراه الجرد مُغلَّفًا
    (بايثون/قوالب/JS) موجود في messages.pot."""
    from i18n_wrap_code import js_unescape
    known = {k[1] for k in pot_keys} | {k[0] for k in pot_keys} | {r[3] for r in pot_keys.values()}
    missing = []
    for f in inventory:
        if f.status != "wrapped":
            continue
        if f.ctx in ("py-str", "jinja-str"):
            text = f.text
        elif f.kind == "js" and f.ctx == "js-str":
            text = js_unescape(f.text)
        else:
            continue
        if text and text not in known:
            missing.append(f"  {f.file}:{f.line}  «{inv._norm_ws(text)[:90]}»")
    assert not missing, ("نصوص مُغلَّفة غير موجودة في messages.pot:\n" + "\n".join(missing[:60])
                         + "\n\nالإصلاح: python tools/i18n_master.py sync && python tools/i18n_master.py export")


def test_js_msgids_list_is_current():
    """translations/js_msgids.json (تقرؤه اللوحة لبناء window.HR_I18N) مطابق لنصوص hrT الحاليّة."""
    import json
    assert os.path.isfile(master.JS_MSGIDS), "شغّل: python tools/i18n_master.py sync"
    with open(master.JS_MSGIDS, encoding="utf-8") as fh:
        listed = set(json.load(fh))
    current = {m for m, _f, _l in master.js_messages()}
    assert listed == current, (f"translations/js_msgids.json قديم ({len(current - listed)} ناقص، "
                               f"{len(listed - current)} زائد). الإصلاح: python tools/i18n_master.py sync")


def test_master_csv_matches_catalog(pot_keys):
    assert os.path.isfile(master.MASTER), \
        "translations/MASTER.csv مفقود — شغّل: python tools/i18n_master.py export"
    with open(master.MASTER, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert rows and list(rows[0].keys()) == master.HEADER, f"أعمدة MASTER.csv يجب أن تكون {master.HEADER}"
    keys = {((r.get("context") or ""), r["msgid"].replace("\r\n", "\n")) for r in rows}
    missing = [k for k in pot_keys if k not in keys]
    extra = [k for k in keys if k not in pot_keys]
    assert not missing and not extra, (
        f"translations/MASTER.csv قديم: {len(missing)} نصًّا ناقصًا، {len(extra)} زائدًا "
        f"(مثال: {(missing or extra)[:3]}).\nالإصلاح: python tools/i18n_master.py export")
