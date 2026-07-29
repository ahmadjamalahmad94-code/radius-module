"""MT110 — الحارس يمنع تجاوز الهوت سبوت لأكثر من مضيفٍ واحد.

إصلاح المُنادي (MT109) عالج الحادثة؛ هذا يعالج **الصنف**: أيّ مسارٍ جديد
يكتب ربط تجاوز يمرّ بالحارس، فلا تُفتح شبكةٌ كاملة بلا تسجيل دخول مهما
كان مصدر النداء. الاختبار يحرس المنفذَين معًا — الدالّة الموحّدة، ونقطتَي
الكتابة الفعليّتَين — لأنّ حارسًا لا يُنادى ليس حارسًا.
"""

import pytest

from app.radius.services.hotspot_bypass_guard import (
    HotspotBypassScopeError, ensure_single_host, is_single_host,
)


# ── ما يجب أن يمرّ ────────────────────────────────────────────────────
@pytest.mark.parametrize("addr", [
    "192.168.3.15",
    "192.168.3.15/32",
    "10.50.0.2",
    "2001:db8::1/128",
])
def test_single_hosts_pass(addr):
    assert ensure_single_host(addr) == addr
    assert is_single_host(addr)


# ── ما يجب أن يُرفض ───────────────────────────────────────────────────
@pytest.mark.parametrize("addr", [
    "192.168.3.0/24",      # الحادثة الأصليّة
    "192.168.0.0/16",      # الأوسع الذي رآه المالك على راوتره
    "10.0.0.0/8",
    "192.168.3.15/31",     # مضيفان — ولو بدا ضيّقًا
    "10.0.0.1-10.0.0.9",   # مدًى
    "",
    "   ",
    "ليس عنوانًا",
])
def test_ranges_and_junk_are_rejected(addr):
    with pytest.raises(HotspotBypassScopeError):
        ensure_single_host(addr)
    assert not is_single_host(addr)


def test_rejection_message_names_the_fix():
    """رسالةٌ تقول ماذا يفعل المشغّل، لا «قيمة غير صالحة» فحسب."""
    with pytest.raises(HotspotBypassScopeError) as e:
        ensure_single_host("192.168.20.0/24")
    msg = str(e.value)
    assert "تسجيل دخول" in msg          # تشرح الخطر
    assert "192.168.20.0" in msg        # تقترح البديل الملموس


# ── الحارس مُنادًى فعلًا من نقطتَي الكتابة ─────────────────────────────
def test_add_ip_binding_refuses_a_subnet(monkeypatch):
    from app.radius.services import device_health_mikrotik as dm

    monkeypatch.setattr(dm, "_live_apply_allowed", lambda live, tenant_id: (True, ""))
    called = {"ran": False}
    monkeypatch.setattr(dm.mac, "_run_mutation",
                        lambda *a, **k: called.__setitem__("ran", True))

    res = dm.add_ip_binding({}, address="192.168.20.0/24",
                            binding_type="bypassed", live=True)
    assert res.ok is False
    assert called["ran"] is False, "وصل الأمر إلى الراوتر رغم الرفض"
    assert "تسجيل دخول" in res.error


def test_add_ip_binding_allows_one_host(monkeypatch):
    from app.radius.services import device_health_mikrotik as dm

    monkeypatch.setattr(dm, "_live_apply_allowed", lambda live, tenant_id: (True, ""))
    seen = {}

    def _mut(nas, *, operation, work, invalidate=()):
        seen["operation"] = operation
        return type("R", (), {"ok": True, "data": {}, "error": ""})()

    monkeypatch.setattr(dm.mac, "_run_mutation", _mut)
    monkeypatch.setattr(dm, "_with_created_id", lambda r: r)

    res = dm.add_ip_binding({}, address="192.168.20.2",
                            binding_type="bypassed", live=True)
    assert res.ok is True
    assert seen["operation"] == "hotspot/ip-binding/add"


def test_second_writer_is_guarded_too():
    """حارسٌ في مسارٍ واحد يترك الآخر مفتوحًا — نصٌّ يحرس النداء."""
    import inspect
    from app.radius.services import network_device_bypass_planner as p
    src = inspect.getsource(p)
    i = src.find("/ip/hotspot/ip-binding/add")
    assert i > 0
    before = src[max(0, i - 500):i]
    assert "ensure_single_host" in before, "الكتابة المباشرة بلا حارس"
