#!/usr/bin/env python3
"""مجمّع كتالوجات الترجمة — يبني translations/{en,ar}/.../messages.po.

المنطق (حتمي وقابل للتكرار):
  1. يستخرج كل الـ msgids الحقيقية من القوالب المغلّفة (pybabel extract) →
     مصدر الحقيقة. أي نص داخل _() / {% trans %} يظهر هنا.
  2. يقرأ شظايا الترجمة tools/i18n_fragments/*.po → قاموس {عربي: إنجليزي}.
  3. يملأ كتالوج en من القاموس، ويبلّغ عن أي msgid في القوالب بلا ترجمة
     (= نص عربي سيظهر بالإنجليزية = عيب يجب إصلاحه).
  4. كتالوج ar: msgstr فارغة عمدًا → gettext يُرجع الـ msgid (العربية).
  5. يصرّف الكتالوجين (.mo).

الاستخدام:  python tools/i18n_assemble.py
لا يكسر شيئًا: العربية تبقى المصدر؛ النص غير المترجم يسقط للعربية تلقائيًا.
"""
from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po, write_po
from babel.messages.mofile import write_mo

ROOT = Path(__file__).resolve().parent.parent
FRAGMENTS_DIR = ROOT / "tools" / "i18n_fragments"
TRANSLATIONS = ROOT / "translations"
POT = TRANSLATIONS / "messages.pot"
LOCALES = ("en", "ar")


def _extract_pot() -> None:
    """يستخرج كل النصوص المغلّفة من app/ إلى messages.pot."""
    cmd = [
        sys.executable, "-m", "babel.messages.frontend", "extract",
        "-F", str(ROOT / "babel.cfg"),
        "-k", "_l",
        "-o", str(POT),
        "--sort-output", "--no-location",
        # أنماط babel.cfg (app/**.py …) نسبية لدليل المسح، فنمسح من الجذر.
        ".",
    ]
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def _load_fragments() -> dict[str, str]:
    """يقرأ كل شظايا الترجمة → {msgid عربي: msgstr إنجليزي}.

    الشظايا بلا ترويسة، فنضيف ترويسة دنيا ليقرأها read_po. عند تعارض ترجمتين
    لنفس النص يفوز الأول ويُطبع تحذير."""
    trans: dict[str, str] = {}
    header = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=utf-8\\n"\n\n'
    for frag in sorted(FRAGMENTS_DIR.glob("*.po")):
        raw = frag.read_text(encoding="utf-8")
        cat = read_po(io.BytesIO((header + raw).encode("utf-8")))
        for msg in cat:
            if not msg.id:
                continue
            en = msg.string or ""
            if msg.id in trans and trans[msg.id] != en and en:
                print(f"  ⚠ تعارض ترجمة لـ «{msg.id[:40]}» — أُبقي الأول")
                continue
            if msg.id not in trans:
                trans[msg.id] = en
    return trans


def _build_catalog(locale: str, pot: Catalog, trans: dict[str, str]) -> tuple[Catalog, list[str]]:
    """يبني كتالوج لغة من الـ pot. en يُملأ من القاموس؛ ar يُترك فارغًا."""
    cat = Catalog(locale=locale)
    missing: list[str] = []
    for msg in pot:
        if not msg.id:
            continue
        if locale == "ar":
            cat.add(msg.id, "")  # هوية: السقوط للـ msgid العربي
        else:
            en = trans.get(msg.id, "")
            if not en:
                missing.append(msg.id)
            cat.add(msg.id, en)
    return cat, missing


def main() -> int:
    print("① استخراج الـ msgids من القوالب المغلّفة…")
    _extract_pot()
    pot = read_po(POT.open("rb"))
    n_msgids = sum(1 for m in pot if m.id)
    print(f"   {n_msgids} نصًّا مغلّفًا.")

    print("② قراءة شظايا الترجمة…")
    trans = _load_fragments()
    print(f"   {len(trans)} ترجمة في القاموس.")

    print("③ بناء وتصريف الكتالوجات…")
    total_missing: list[str] = []
    for locale in LOCALES:
        cat, missing = _build_catalog(locale, pot, trans)
        po_path = TRANSLATIONS / locale / "LC_MESSAGES" / "messages.po"
        mo_path = TRANSLATIONS / locale / "LC_MESSAGES" / "messages.mo"
        po_path.parent.mkdir(parents=True, exist_ok=True)
        with po_path.open("wb") as f:
            write_po(f, cat, sort_output=True)
        with mo_path.open("wb") as f:
            write_mo(f, cat)
        print(f"   {locale}: {sum(1 for m in cat if m.id)} مدخلة → .po + .mo")
        if locale == "en":
            total_missing = missing

    if total_missing:
        print(f"\n⚠ {len(total_missing)} نصًّا مغلّفًا بلا ترجمة إنجليزية "
              "(سيظهر بالعربية عند الإنجليزية):")
        for mid in total_missing[:40]:
            print(f"   • {mid[:70]}")
        if len(total_missing) > 40:
            print(f"   … و{len(total_missing) - 40} غيرها")
        return 1
    print("\n✓ كل نص مغلّف له ترجمة إنجليزية — صفر تسريب عربي.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
