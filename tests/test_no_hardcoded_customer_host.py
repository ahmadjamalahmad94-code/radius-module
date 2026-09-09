# -*- coding: utf-8 -*-
"""حارسٌ: لا عنوانَ زبونٍ بعينِه مخبوءًا في النسخة المُباعة.

القصّةُ التي وُلد منها هذا الملفّ: عنوانُ خادمِ زبونٍ واحد (187.77.70.18)
كان قيمةً افتراضيّةً في مزوّد المعالج وفي حقلَي القالبَين وفي ملفَّي
جافاسكربت. فكلُّ نسخةٍ تُباع بلا ضبطٍ صريح كانت راوتراتُها تدُقُّ خادمَ
ذلك الزبون — تسريبُ وجهةٍ صامتٌ لا يظهر في أيّ سجلّ عندنا.

القاعدةُ الآن: العنوانُ يأتي من إعدادٍ صريح، ثمّ من عنوان اللوحة العامّ،
وإلّا فراغٌ يملؤه المشغّل. وفراغٌ يُوقف المشغّلَ خيرٌ من عنوانٍ يعمل
ويصيب الخادمَ الخطأ.

يُسمح بذكرِ العنوان في **التعليقات** وحدَها (توثيقُ الحادثة نفسِها).

Run this file alone (per-file isolation)."""
from __future__ import annotations

import os
import re

#: عناوينُ زبائنَ حقيقيّةٍ ظهرت يومًا في الشيفرة. تُضاف إليها كلُّ حالة.
FORBIDDEN = ("187.77.70.18",)

#: بادئاتُ التعليق في اللغات التي نمسحها.
_COMMENT = re.compile(r"^\s*(#|//|\*|/\*|\{#|<!--|:#:|#:)")

_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "app")
_EXT = (".py", ".js", ".html", ".jinja", ".css", ".json", ".yml", ".yaml")


def _offending_lines():
    hits = []
    for base, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
        for name in files:
            if not name.endswith(_EXT):
                continue
            path = os.path.join(base, name)
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.read().splitlines()
            except OSError:  # pragma: no cover - قراءةٌ متعذّرة
                continue
            for no, line in enumerate(lines, 1):
                if not any(ip in line for ip in FORBIDDEN):
                    continue
                if _COMMENT.match(line):
                    continue          # توثيقُ الحادثة مسموح
                rel = os.path.relpath(path, os.path.dirname(_ROOT))
                hits.append("%s:%d: %s" % (rel, no, line.strip()[:100]))
    return hits


def test_no_customer_ip_in_shipped_code():
    hits = _offending_lines()
    assert not hits, (
        "عنوانُ زبونٍ حقيقيٍّ في شيفرةٍ تُباع — اجعلْه إعدادًا:\n"
        + "\n".join(hits))


def test_wizard_endpoint_default_is_empty_without_config(monkeypatch):
    """بلا ضبطٍ: فراغٌ — لا عنوانَ مُخمَّن."""
    from app.radius.services import setup_wizard_router_provisioning as p
    monkeypatch.delenv(p.SERVER_ENDPOINT_ENV, raising=False)
    monkeypatch.delenv(p.PANEL_PUBLIC_IP_ENV, raising=False)
    host, port = p._endpoint_defaults()
    assert host == ""
    assert port == p.DEFAULT_ENDPOINT_PORT


def test_wizard_endpoint_falls_back_to_panel_public_ip(monkeypatch):
    """عنوانُ اللوحة العامُّ هو الاحتياطُ الصحيح — accel يعمل على المضيف نفسِه."""
    from app.radius.services import setup_wizard_router_provisioning as p
    monkeypatch.delenv(p.SERVER_ENDPOINT_ENV, raising=False)
    monkeypatch.setenv(p.PANEL_PUBLIC_IP_ENV, "198.51.100.7")
    host, _ = p._endpoint_defaults()
    assert host == "198.51.100.7"


def test_explicit_endpoint_wins(monkeypatch):
    from app.radius.services import setup_wizard_router_provisioning as p
    monkeypatch.setenv(p.PANEL_PUBLIC_IP_ENV, "198.51.100.7")
    monkeypatch.setenv(p.SERVER_ENDPOINT_ENV, "203.0.113.10:51821")
    host, port = p._endpoint_defaults()
    assert host == "203.0.113.10"
    assert port == 51821
