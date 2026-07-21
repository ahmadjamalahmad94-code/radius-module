#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""مدقّق نحوي لقوالب Jinja بعد موجات تغليف الترجمة (i18n).

يحمّل كل قالب في ``app/templates/**/*.html`` ويحاول تحليله (parse) ببيئة
Jinja مطابقة لإعداد Flask (امتدادات i18n/do/loopcontrols + ضبط المسافات)،
كاشفًا أي كسر نحوي أحدثه التغليف (وسم trans ناقص، أقواس غير متوازنة، …).

التحليل وقت-التصميم فقط: لا يقيّم الفلاتر/الدوال (money/url_for…) فهي
وقت-تشغيل، فغيابها لا يسبّب أخطاء كاذبة. يطبع كل ملف فشل تحليله ورمز خطئه.

التشغيل:
    python tools/i18n_jinja_check.py            # يفحص الكل، يرجع 1 إن وُجد كسر
    python tools/i18n_jinja_check.py <path...>  # يفحص ملفات محدّدة
"""
from __future__ import annotations

import os
import sys

from jinja2 import Environment
from jinja2.exceptions import TemplateSyntaxError

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_ROOT = os.path.join(PROJECT_ROOT, "app", "templates")


def _make_env() -> Environment:
    # نطابق إعداد Flask: trim_blocks/lstrip_blocks + امتداد i18n لوسم trans.
    return Environment(
        extensions=[
            "jinja2.ext.i18n",
            "jinja2.ext.do",
            "jinja2.ext.loopcontrols",
        ],
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=True,
    )


def check_file(env: Environment, path: str) -> str | None:
    """يُعيد رسالة الخطأ إن فشل التحليل، وإلا None."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as exc:  # noqa: BLE001
        return f"تعذّر القراءة: {exc}"
    try:
        env.parse(src)
        return None
    except TemplateSyntaxError as exc:
        return f"سطر {exc.lineno}: {exc.message}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def collect(paths: list[str]) -> list[str]:
    if paths:
        return paths
    out: list[str] = []
    for dirpath, _dirs, names in os.walk(TEMPLATES_ROOT):
        for name in names:
            if name.endswith(".html"):
                out.append(os.path.join(dirpath, name))
    out.sort()
    return out


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    env = _make_env()
    files = collect([os.path.abspath(p) for p in argv])
    failures: list[tuple[str, str]] = []
    for path in files:
        err = check_file(env, path)
        if err:
            rel = os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")
            failures.append((rel, err))
    if failures:
        print(f"✗ {len(failures)} قالبًا فشل تحليله نحويًا:")
        for rel, err in failures:
            print(f"   • {rel} — {err}")
        return 1
    print(f"✓ كل القوالب ({len(files)}) سليمة نحويًا.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
