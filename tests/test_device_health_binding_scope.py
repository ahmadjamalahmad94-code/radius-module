"""MT109 — تجاوز الهوت سبوت يقتصر على جهاز التوزيع، لا شبكته.

كان «تطبيق» صفحة تتبّع الأجهزة يكتب على الراوتر:

    /ip/hotspot/ip-binding/add address=192.168.20.0/24 type=bypassed

والهوت سبوت يعمل على المدخل نفسه الذي تحمل عليه تلك الشبكة عنوان الإدارة.
فأيّ زبونٍ يضبط لنفسه عنوانًا ثابتًا داخل النطاق يتجاوز البوابة كلَّها:
إنترنت بلا تسجيل دخول ولا محاسبة — والكلمة تنتشر بين الزبائن بسرعة.

الغرض لا يحتاج ذلك: نريد أن تصل اللوحة إلى **الجهاز المُراقَب**، وهو
عنوانٌ واحد. هذا الاختبار يحرس الفرق، لأنّ الخطأ صامت — لا يُبلّغ عنه
أحدٌ إلّا حين يتوقّف الناس عن الشراء.
"""

import pytest

from app.radius.services.device_health import _apply_one


class _MT:
    def __init__(self):
        self.calls = []

    def add_ip_binding(self, nas, *, address, binding_type, device_id, live):
        self.calls.append({"address": address, "type": binding_type})
        return type("R", (), {"ok": True, "data": {}, "error": ""})()

    def add_ip_address(self, *a, **k):
        return type("R", (), {"ok": True, "data": {}, "error": ""})()

    def add_netwatch(self, *a, **k):
        return type("R", (), {"ok": True, "data": {}, "error": ""})()


NET = {"ip_address": "192.168.20.2",
       "gateway_address": "192.168.20.1/24",
       "network_cidr": "192.168.20.0/24"}
DEVICE = {"id": 1, "interface_name": "ether2", "router_id": 1,
          "netwatch_interval_sec": 60, "netwatch_timeout_sec": 3}


def test_binding_is_the_device_address_not_the_subnet():
    """جوهر العطب: /24 يفتح الشبكة كلّها لمن يضع عنوانًا ثابتًا."""
    mt = _MT()
    _apply_one(mt, {}, DEVICE, NET, {"kind": "ip_binding"})
    assert mt.calls == [{"address": "192.168.20.2", "type": "bypassed"}]


def test_binding_never_carries_a_cidr_suffix():
    """أيّ `/N` يعني نطاقًا — والنطاق هو الثغرة."""
    mt = _MT()
    _apply_one(mt, {}, DEVICE, NET, {"kind": "ip_binding"})
    assert "/" not in mt.calls[0]["address"]


def test_planner_plans_the_device_address_too():
    """الخطّة المعروضة والأمر المُنفَّذ يجب أن يتطابقا — وإلّا وافق المشغّل
    على شيء ونُفِّذ غيره."""
    from app.radius.services.device_health_planner import build_plan
    plan = build_plan(interface_name="ether2", ip_address="192.168.20.2")
    assert plan["ok"] and plan["valid"], plan.get("error")
    binding = [i for i in plan["items"] if i["kind"] == "ip_binding"][0]
    assert binding["address"] == "192.168.20.2"
    assert "192.168.20.2" in binding["command"]
    assert "192.168.20.0/24" not in binding["command"]


def test_planner_source_no_longer_binds_the_network():
    """حارسٌ نصّيّ: لا يعود `network_cidr` عنوانًا لأمر ip-binding."""
    import inspect
    from app.radius.services import device_health_planner as p
    src = inspect.getsource(p)
    i = src.find("/ip/hotspot/ip-binding/add")
    assert i > 0, "أمر الربط اختفى من المخطِّط"
    line = src[i:i + 160]
    assert "network_cidr" not in line, f"المخطِّط ما زال يربط الشبكة: {line!r}"
