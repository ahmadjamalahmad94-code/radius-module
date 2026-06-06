#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""دمج shards الترجمة → tools/i18n_translations/{en,fr,tr,es}.json.

كل shard ملف JSON: { "<عربي>": {"en":..,"fr":..,"tr":..,"es":..}, ... }.
ينتجها وكلاء الترجمة المتوازون. هذا السكربت يدمجها في خرائط لغة موحّدة:
  • en.json: يُبقي الترجمات المنسّقة الموجودة (curated)، يملأ الناقص من الshards.
  • fr/tr/es.json: من الshards (تُحدَّث/تُضاف).
ثمّ شغّل tools/i18n_build_catalogs.py لبناء .po/.mo.

الاستخدام: python tools/i18n_merge_shards.py  [--report]
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TDIR = ROOT / "tools" / "i18n_translations"
SHARDS = TDIR / "shards"
LOCS = ("en", "fr", "tr", "es")


def _load(p: Path) -> dict:
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def main() -> int:
    maps = {loc: _load(TDIR / f"{loc}.json") for loc in LOCS}
    curated_en = set(k for k, v in maps["en"].items() if isinstance(v, str) and v.strip())

    # نلتقط كل ملفات العمل المحتملة: shards/*.json + أي _slice/_tmp في الجذر.
    shard_files = sorted(SHARDS.glob("*.json"))
    shard_files += sorted(TDIR.glob("_slice*.json")) + sorted(TDIR.glob("_tmp*.json"))
    seen, added = 0, {loc: 0 for loc in LOCS}

    def _vals_of(entry):
        """يطبّع قيمة الإدخال إلى dict لغوي. يقبل dict أو list [en,fr,tr,es]."""
        if isinstance(entry, dict):
            return entry
        if isinstance(entry, list) and len(entry) >= 4:
            return {LOCS[i]: entry[i] for i in range(4)}
        return None

    for sf in shard_files:
        try:
            data = _load(sf)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ ملف تالف يُتخطّى: {sf.name} ({exc})")
            continue
        # نقبل صيغة dict {msgid: vals} فقط (نتجاهل القوائم العليا الملتبسة).
        if not isinstance(data, dict):
            print(f"  ⚠ صيغة غير متوقّعة (ليست dict) يُتخطّى: {sf.name}")
            continue
        for mid, raw in data.items():
            vals = _vals_of(raw)
            if vals is None:
                continue
            seen += 1
            for loc in LOCS:
                v = vals.get(loc)
                if not (isinstance(v, str) and v.strip()):
                    continue
                # en: لا تَدُس المنسّق الموجود.
                if loc == "en" and mid in curated_en:
                    continue
                if mid not in maps[loc] or not str(maps[loc].get(mid, "")).strip():
                    added[loc] += 1
                maps[loc][mid] = v

    for loc in LOCS:
        out = TDIR / f"{loc}.json"
        with out.open("w", encoding="utf-8") as fh:
            json.dump(maps[loc], fh, ensure_ascii=False, indent=2, sort_keys=True)

    print(f"shards مدموجة: {len(shard_files)} | إدخالات مقروءة: {seen}")
    for loc in LOCS:
        print(f"  {loc}: إجمالي {len(maps[loc])} (مضاف الآن +{added[loc]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
