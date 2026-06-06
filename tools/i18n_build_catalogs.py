#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""باني كتالوجات الترجمة متعدّدة اللغات — en/fr/tr/es فوق المصدر العربي.

النموذج (حتمي وقابل للتكرار):
  1. يستخرج كل الـ msgids المغلّفة من ``app/`` إلى ``translations/messages.pot``
     (مصدر الحقيقة لما يجب ترجمته).
  2. لكل لغة هدف (en, fr, tr, es): يقرأ خريطة الترجمة من
     ``tools/i18n_translations/<locale>.json`` ({msgid عربي: الترجمة})،
     ويبني ``translations/<locale>/LC_MESSAGES/messages.po`` + ``.mo``.
     أي msgid بلا ترجمة → msgstr فارغة → gettext يسقط للعربية (آمن، لا يكسر).
  3. العربية (ar): msgstr فارغة عمدًا → gettext يُرجع الـ msgid نفسه (العربية).
  4. يبلّغ نسبة التغطية لكل لغة (عدد المترجَم / إجمالي الـ msgids).

ملفات JSON هي مصدر الحقيقة لترجمات الوكلاء — لا تُحرَّر .po يدويًا.
لإعادة بذر en من .po منسّق سابق: ``python tools/i18n_build_catalogs.py --seed-en``.

الاستخدام:
  python tools/i18n_build_catalogs.py            # يبني كل اللغات
  python tools/i18n_build_catalogs.py --seed-en  # يبذر en.json من en/.po الحالي ثم يبني
شغّل دائمًا بـ PYTHONUTF8=1 على ويندوز.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po, write_po
from babel.messages.mofile import write_mo

ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS = ROOT / "translations"
POT = TRANSLATIONS / "messages.pot"
TRANS_DIR = ROOT / "tools" / "i18n_translations"

#: اللغات الهدف (لها كتالوج مترجَم). العربية تُبنى فارغة منفصلةً (سقوط للمصدر).
TARGET_LOCALES = ("en", "fr", "tr", "es")
ALL_LOCALES = ("ar",) + TARGET_LOCALES


def _extract_pot() -> None:
    """يستخرج كل النصوص المغلّفة من الجذر إلى messages.pot."""
    cmd = [
        sys.executable, "-m", "babel.messages.frontend", "extract",
        "-F", str(ROOT / "babel.cfg"),
        "-k", "_l",
        "-o", str(POT),
        "--sort-output", "--no-location",
        ".",
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def _load_pot_ids() -> list[str]:
    with POT.open("r", encoding="utf-8") as fh:
        cat = read_po(fh)
    return [m.id for m in cat if m.id]


def _load_json(locale: str) -> dict[str, str]:
    path = TRANS_DIR / f"{locale}.json"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    # نقبل فقط القيم النصّية غير الفارغة.
    return {k: v for k, v in data.items() if isinstance(v, str) and v.strip()}


def _seed_en_from_po() -> None:
    """يبذر tools/i18n_translations/en.json من en/.po المنسّق الموجود (مرّة)."""
    po_path = TRANSLATIONS / "en" / "LC_MESSAGES" / "messages.po"
    if not po_path.exists():
        print("  (لا يوجد en/.po للبذر)")
        return
    with po_path.open("r", encoding="utf-8") as fh:
        cat = read_po(fh)
    existing = _load_json("en")
    added = 0
    for m in cat:
        if m.id and m.string and m.id not in existing:
            existing[m.id] = m.string
            added += 1
    TRANS_DIR.mkdir(parents=True, exist_ok=True)
    out = TRANS_DIR / "en.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(existing, fh, ensure_ascii=False, indent=2, sort_keys=True)
    print(f"  بُذِر en.json: +{added} (الإجمالي {len(existing)})")


_NAMED_PH = re.compile(r"%\((\w+)\)[sd]")


def _safe_msgstr(mid: str, s: str) -> str:
    """يُعقّم الترجمة كي لا تكسر رندر gettext (الذي يطبّق ``rv % vars`` دائمًا).

    • msgid بلا '%': الاستدعاء بلا kwargs → أيّ '%' في الترجمة يجب أن يُهرَّب
      (``%`` → ``%%``) كي يُعرَض حرفيًا.
    • msgid فيه عناصر نائبة: نتحقّق أن الترجمة تُرندَر بمتغيّرات وهمية مطابقة؛
      إن فشلت (وكيل أفسد العنصر النائب) نُسقِطها للعربية (msgstr فارغة).
    """
    if not s:
        return s
    if "%" not in mid:
        return s.replace("%", "%%")
    # msgid فيه '%' — اختبر الرندر بمتغيّرات وهمية من أسماء عناصر msgid.
    names = set(_NAMED_PH.findall(mid))
    dummy = {n: 0 for n in names}
    try:
        s % dummy
        return s
    except Exception:  # noqa: BLE001
        return ""  # ترجمة غير آمنة → سقوط للعربية.


def _build_locale(locale: str, ids: list[str]) -> tuple[int, int]:
    """يبني .po + .mo للغة. يُرجع (مترجَم, إجمالي)."""
    cat = Catalog(locale=locale)
    cat.fuzzy = False  # وإلا علّم pybabel compile الكتالوج fuzzy وتخطّاه.
    trans = {} if locale == "ar" else _load_json(locale)
    translated = 0
    for mid in ids:
        s = _safe_msgstr(mid, trans.get(mid, ""))
        if s:
            translated += 1
        cat.add(mid, string=s)
    out_dir = TRANSLATIONS / locale / "LC_MESSAGES"
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "messages.po").open("wb") as fh:
        write_po(fh, cat, sort_output=True, width=76)
    with (out_dir / "messages.mo").open("wb") as fh:
        write_mo(fh, cat)
    return translated, len(ids)


def main() -> int:
    if "--seed-en" in sys.argv:
        print("» بذر en.json من en/.po …")
        _seed_en_from_po()

    print("» استخراج messages.pot من الجذر …")
    _extract_pot()
    ids = _load_pot_ids()
    print(f"  إجمالي الـ msgids: {len(ids)}")

    print("» بناء الكتالوجات …")
    for loc in ALL_LOCALES:
        t, total = _build_locale(loc, ids)
        if loc == "ar":
            print(f"  ar : (مصدر — فارغ متعمّد، سقوط للعربية)")
        else:
            pct = (t / total * 100) if total else 0.0
            print(f"  {loc} : {t}/{total}  ({pct:.1f}%)")
    print("✓ تمّ بناء الكتالوجات (.po + .mo).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
