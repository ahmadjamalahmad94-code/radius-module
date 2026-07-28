"""MT76 — مُشغِّل إعادة التحميل يُداوي نفسه، وإلّا مات كل راوترٍ جديد بصمت.

حادثة إنتاج (169.58.71.165، 2026-07-28): المُثبِّت أنشأ `.reload-trigger`
بملكيّة root وصلاحية 0600، واللوحة تعمل بمستخدمٍ آخر ⇒ `touch`/`utime`
يفشلان ويُبتلع الفشل. النتيجة: FreeRADIUS يبقى بقائمة عملاء قديمة، فيُسقط
حزم الراوتر الجديد **بصمت** (`unknown client`) — الراوتر يرى مهلات لا
رفضًا، فيبدو العطب شبكيًّا. ساعةٌ من التشخيص.

العقد: إن تعذّر اللمس، **يُعاد إنشاء الملفّ** (المجلّد قابلٌ للكتابة
بالضرورة — كُتب فيه ملفّ العميل للتوّ)، وإن تعذّر ذلك أيضًا يُسجَّل خطأٌ
صريح لا تحذيرٌ مبتلَع.
"""
from __future__ import annotations

import os
import time

import pytest

from app.radius.services import setup_wizard_v3_radius_server_provisioning as prov


def test_plain_touch_bumps_mtime(tmp_path):
    trig = tmp_path / ".reload-trigger"
    trig.write_text("")
    os.utime(trig, (1_000_000, 1_000_000))
    prov._touch_reload_trigger(tmp_path)
    assert trig.stat().st_mtime > 1_000_000


def test_unwritable_trigger_is_recreated(tmp_path, monkeypatch):
    """🔴 جوهر الحادثة: اللمس يفشل ⇒ يُعاد الإنشاء بدل الاستسلام."""
    trig = tmp_path / ".reload-trigger"
    trig.write_text("")
    os.utime(trig, (1_000_000, 1_000_000))

    real_touch = type(trig).touch

    def _deny(self, *a, **k):
        if self.name == ".reload-trigger":
            raise PermissionError(13, "Permission denied")
        return real_touch(self, *a, **k)

    monkeypatch.setattr(type(trig), "touch", _deny)
    prov._touch_reload_trigger(tmp_path)
    assert trig.exists(), "الملفّ اختفى — المراقب لن يجد شيئًا"
    assert trig.stat().st_mtime > 1_000_000, "لم يُجدَّد الطابع ⇒ لا إعادة تحميل"


def test_missing_trigger_is_created(tmp_path):
    """مجلّدٌ بلا مُشغِّل (تثبيتٌ قديم) ⇒ يُنشأ لا يُتجاهَل."""
    prov._touch_reload_trigger(tmp_path)
    assert (tmp_path / ".reload-trigger").exists()


def test_total_failure_is_logged_as_error_not_swallowed(tmp_path, monkeypatch, caplog):
    """فشلٌ تامّ يُسجَّل ERROR — كان تحذيرًا يَضيع بين السطور."""
    import logging
    trig = tmp_path / ".reload-trigger"
    trig.write_text("")

    def _deny_touch(self, *a, **k):
        raise PermissionError(13, "denied")

    def _deny_write(self, *a, **k):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(type(trig), "touch", _deny_touch)
    monkeypatch.setattr(type(trig), "write_text", _deny_write)
    with caplog.at_level(logging.ERROR):
        prov._touch_reload_trigger(tmp_path)
    assert any(r.levelno >= logging.ERROR for r in caplog.records), \
        "الفشل التامّ ما زال مبتلَعًا"


def test_never_raises_into_the_caller(tmp_path, monkeypatch):
    """الكتابة نجحت سلفًا — لا يجوز أن يُسقط المُشغِّلُ حفظَ الجهاز."""
    def _boom(self, *a, **k):
        raise OSError("disk on fire")

    monkeypatch.setattr(type(tmp_path), "__truediv__", type(tmp_path).__truediv__)
    monkeypatch.setattr(os, "utime", _boom)
    prov._touch_reload_trigger(tmp_path)      # لا استثناء
