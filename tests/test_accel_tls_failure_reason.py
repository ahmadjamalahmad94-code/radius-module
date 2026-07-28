"""MT75 — «فشل مصافحة TLS» يجب أن يقول السبب الحقيقيّ.

حادثة إنتاج (169.58.71.165، 2026-07-28): الفاحص أظهر «TCP متصل لكن مصافحة
TLS فشلت … UNEXPECTED_EOF_WHILE_READING»، والشهادة سليمةٌ تمامًا. الحقيقة
في سجلّ accel: ``sstp: IP is out of client-ip-range, droping connection`` —
accel كان يُسقط الاتّصال **قبل** التشفير لأنّ `[client-ip-range]` يَفحص
**عنوان المصدر** للاتّصال الوارد، وكان محصورًا في بركة الإدارة. أي أنّ
**كل راوترٍ يأتي من IP عامّ كان يُرفض قبل أن يبدأ** — والرسالة تُرسل
المشغّل يفتّش في الشهادة.
"""
from __future__ import annotations

import pytest

from app.radius.services import accel_config as ac


def _log(tmp_path, text):
    p = tmp_path / "accel-ppp.log"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_ip_range_rejection_is_named_not_blamed_on_tls(tmp_path, monkeypatch):
    """🔴 جوهر الحادثة: يُسمّى السبب ويُذكر الإجراء."""
    path = _log(tmp_path, "warn: sstp: IP is out of client-ip-range, droping connection...\n")
    monkeypatch.setattr(ac, "_ACCEL_LOG_PATHS", (path,))
    msg = ac._tls_failure_reason("1.2.3.4", 443, "EOF")
    assert "client-ip-range" in msg
    assert "0.0.0.0/0" in msg, "لا يذكر الإجراء العلاجيّ"
    assert "قبل" in msg, "لا يوضّح أنّ الرفض سابقٌ للتشفير"


def test_certificate_problems_still_reported(tmp_path, monkeypatch):
    path = _log(tmp_path, "error: sstp: ssl_ctx: cannot load certificate\n")
    monkeypatch.setattr(ac, "_ACCEL_LOG_PATHS", (path,))
    assert "شهادة" in ac._tls_failure_reason("h", 443, "EOF")


def test_unknown_cause_falls_back_to_raw_detail(tmp_path, monkeypatch):
    """بلا نمطٍ معروف: لا نَخترع سببًا — نُعيد التفصيل الخامّ."""
    path = _log(tmp_path, "info: sstp: started\n")
    monkeypatch.setattr(ac, "_ACCEL_LOG_PATHS", (path,))
    msg = ac._tls_failure_reason("h", 443, "SOME_RAW_DETAIL")
    assert "SOME_RAW_DETAIL" in msg
    assert "السبب الحقيقيّ" not in msg


def test_missing_log_never_raises(monkeypatch):
    """الفاحص لا يَنهار لتعذّر قراءة السجلّ (صلاحيات/مسار مختلف)."""
    monkeypatch.setattr(ac, "_ACCEL_LOG_PATHS", ("/nope/does-not-exist.log",))
    msg = ac._tls_failure_reason("h", 443, "detail")
    assert "detail" in msg


def test_generated_config_allows_public_routers():
    """المولّد لا يعود يَحصر النطاق في بركة الإدارة (سبب الحادثة)."""
    src = (__import__("pathlib").Path(ac.__file__).parents[3]
           / "deploy" / "accel-ppp" / "accel_conf_gen.py").read_text(encoding="utf-8")
    # rsplit: الاسم يَرد في docstring الوحدة أيضًا — نريد قالب الإعداد.
    block = src.rsplit("[client-ip-range]", 1)[1].split("[ip-pool]", 1)[0]
    assert "0.0.0.0/0" in block
    # القيمة الفعليّة (لا التعليق) ليست بركة الإدارة
    values = [l.strip() for l in block.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert values and values[0] == "0.0.0.0/0", values
