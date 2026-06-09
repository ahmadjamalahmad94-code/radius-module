"""اختبارات وحدات لإطار «خدمات السكربت المبنيّة على المنافذ».

تغطّي المنطق الحسّاس للدمج لاحقًا:
  • تعويض العناصر النائبة {{PORTS}}/{{IFACES}} في سكربتي التفعيل والإزالة.
  • بناء خطة (build_plan) بوضعَي التفعيل/الإزالة + التحقّق من المنافذ.
  • بناء أوامر الدفع (build_push_commands) وإعادة استخدام
    mt_programming.apply_commands لتنفيذها بالترتيب (add → run → remove).
  • حفظ/قراءة حالة الخدمة (مفعّلة على مداخل X,Y / غير مفعّلة).
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest

from app.radius.services import port_script_services as pss


# ─── تعويض العناصر النائبة ───────────────────────────────────────


def test_render_script_substitutes_ports_and_ifaces():
    svc = pss.get_service("bt_wifi_block")
    script = pss.render_script(svc, ["ether2", "ether3"])
    # {{PORTS}} → قائمة مفصولة بفواصل
    assert "ether2,ether3" in script
    # {{IFACES}} → سطر mangle حقيقي (TTL=1) لكل واجهة موسوم HR-AntiShare
    assert script.count("out-interface=ether2") == 1
    assert script.count("out-interface=ether3") == 1
    assert 'comment="HR-AntiShare ether2"' in script
    assert 'comment="HR-AntiShare ether3"' in script
    assert "new-ttl=set:1" in script
    # لم تبقَ أي عناصر نائبة
    assert "{{PORTS}}" not in script
    assert "{{IFACES}}" not in script
    assert "{{IFACE}}" not in script


def test_loop_detect_script_adds_dhcp_client_per_port():
    """سكربت كشف اللوب المفعّل يضيف عميل DHCP موسومًا لكل منفذ، وسكربت
    الإزالة يحذف الموسوم لذلك المنفذ."""
    svc = pss.get_service("loop_detect")
    enable = pss.render_script(svc, ["ether4", "ether5"])
    assert enable.count("/ip dhcp-client add interface=ether4") == 1
    assert enable.count("/ip dhcp-client add interface=ether5") == 1
    assert "add-default-route=no" in enable
    assert 'comment="HR-LoopDetect ether4"' in enable
    remove = pss.render_script(svc, ["ether4"], remove=True)
    assert "/ip dhcp-client remove" in remove
    assert 'comment="HR-LoopDetect ether4"' in remove


def test_both_services_are_activated_not_placeholder():
    """الخدمتان مُفعّلتان (is_placeholder=False) — يفعّل زر التطبيق
    ويزيل شارة «بانتظار السكربت»."""
    assert pss.get_service("bt_wifi_block").is_placeholder is False
    assert pss.get_service("loop_detect").is_placeholder is False


def test_render_script_remove_uses_remove_template():
    svc = pss.get_service("loop_detect")
    apply_script = pss.render_script(svc, ["ether5"], remove=False)
    remove_script = pss.render_script(svc, ["ether5"], remove=True)
    assert "تفعيل" in apply_script
    assert "إزالة" in remove_script
    # كلاهما يعوّض الواجهة بلا عناصر نائبة متبقّية
    for s in (apply_script, remove_script):
        assert "ether5" in s
        assert "{{IFACE" not in s


def test_render_iface_block_one_line_per_port():
    svc = pss.get_service("bt_wifi_block")
    block = pss.render_iface_block(svc, ["a", "b", "c"])
    assert len(block.splitlines()) == 3


# ─── بناء الخطة + التحقّق من المنافذ ─────────────────────────────


def test_build_plan_apply_summary_no_placeholder_warning():
    plan = pss.build_plan("bt_wifi_block", ["ether2"])
    assert plan.slug == "bt_wifi_block"
    assert plan.selected_ports == ["ether2"]
    # الخدمة مُفعّلة الآن → لا تحذير «قالب مبدئي» والدفع مسموح
    assert plan.is_placeholder is False
    assert not any("قالب مبدئي" in w for w in plan.warnings)
    assert any("التفعيل" in s for s in plan.summary)


def test_build_plan_remove_mode_marks_action():
    plan = pss.build_plan("bt_wifi_block", ["ether2"], remove=True)
    assert any("الإزالة" in s for s in plan.summary)
    assert "إزالة" in plan.script


def test_build_plan_dedups_and_validates_ports():
    plan = pss.build_plan("loop_detect", ["ether2", "ether2", "  ether3  "])
    assert plan.selected_ports == ["ether2", "ether3"]


def test_build_plan_rejects_empty_ports():
    with pytest.raises(ValueError):
        pss.build_plan("loop_detect", ["   ", ""])


def test_build_plan_rejects_bad_interface_name():
    with pytest.raises(ValueError):
        pss.build_plan("loop_detect", ["ether 2; bad"])


def test_build_plan_unknown_slug():
    with pytest.raises(ValueError):
        pss.build_plan("does_not_exist", ["ether2"])


# ─── تتبّع حالة اللوب: parse_loop_status + read_loop_status ──────


def test_parse_loop_status_bound_is_loop_searching_is_not():
    rows = [
        # ether2: استلم عنوانًا (bound) → لوب
        {"interface": "ether2", "status": "bound",
         "address": "192.168.88.50/24", "gateway": "192.168.88.1",
         "dhcp-server": "192.168.88.1", "comment": "HR-LoopDetect ether2"},
        # ether3: يبحث (searching) → لا لوب
        {"interface": "ether3", "status": "searching...",
         "address": "", "gateway": "", "dhcp-server": "",
         "comment": "HR-LoopDetect ether3"},
        # إدخال غير موسوم → يُتجاهَل تمامًا
        {"interface": "ether9", "status": "bound",
         "address": "10.0.0.2/24", "comment": "client آخر"},
    ]
    probes = pss.parse_loop_status(rows)
    assert [p.iface for p in probes] == ["ether2", "ether3"]
    loop, clear = probes[0], probes[1]
    assert loop.is_loop is True
    assert "لوب مكتشف على ether2" in loop.message
    assert "192.168.88.50/24" in loop.message
    assert "192.168.88.1" in loop.message
    assert clear.is_loop is False
    assert "لا لوب" in clear.message


def test_parse_loop_status_filters_by_only_ports():
    rows = [
        {"interface": "ether2", "status": "bound", "address": "1.2.3.4/24",
         "comment": "HR-LoopDetect ether2"},
        {"interface": "ether3", "status": "searching",
         "comment": "HR-LoopDetect ether3"},
    ]
    probes = pss.parse_loop_status(rows, only_ports=["ether3"])
    assert [p.iface for p in probes] == ["ether3"]


def test_parse_loop_status_yields_probe_per_selected_port():
    """ISSUE A (يونيو 2026): «فحص اللوب» يجب أن يُعيد بطاقة لكل منفذ
    اختاره المشغّل — حتى لو لم توجد قاعدة HR-LoopDetect مركّبة عليه.

    قبل الإصلاح: النسخة السابقة كانت تكرّر على الصفوف الموسومة فقط
    فيختار المشغّل 9 منافذ فيرى 4 (لأن apply الأخير شُغِّل بـ4 فقط)
    والباقي يسقط بصمت. الآن: نتيجة بطول 9 — 4 probes حقيقية و5 «no-rule».
    """
    selected = [f"ether{i}" for i in range(2, 9)] + ["sfp1", "sfp2"]
    rows = [
        {"interface": "ether2", "status": "bound",
         "address": "192.168.88.7/24", "comment": "HR-LoopDetect ether2"},
        {"interface": "ether3", "status": "searching...",
         "comment": "HR-LoopDetect ether3"},
        {"interface": "ether4", "status": "searching...",
         "comment": "HR-LoopDetect ether4"},
        {"interface": "ether5", "status": "bound",
         "address": "10.10.10.4/24", "comment": "HR-LoopDetect ether5"},
    ]
    probes = pss.parse_loop_status(rows, only_ports=selected)
    # بطاقة لكل منفذ من selected بنفس الترتيب
    assert [p.iface for p in probes] == selected
    assert len(probes) == 9
    # الموجودة: ether2/ether5 لوب، ether3/ether4 لا لوب، الباقي «no-rule»
    by_iface = {p.iface: p for p in probes}
    assert by_iface["ether2"].is_loop is True
    assert by_iface["ether5"].is_loop is True
    assert by_iface["ether3"].is_loop is False
    assert by_iface["ether3"].status != "no-rule"
    assert by_iface["ether4"].is_loop is False
    # المفقودة من /ip dhcp-client تأتي بشكل صريح «no-rule»
    for missing in ("ether6", "ether7", "ether8", "sfp1", "sfp2"):
        assert by_iface[missing].status == "no-rule"
        assert by_iface[missing].is_loop is False
        assert "لم تُركَّب" in by_iface[missing].message


def test_parse_loop_status_dedups_only_ports():
    rows = [{"interface": "ether2", "status": "bound",
             "address": "1.2.3.4/24", "comment": "HR-LoopDetect ether2"}]
    probes = pss.parse_loop_status(
        rows, only_ports=["ether2", "ether2", "  ", "ether3"])
    assert [p.iface for p in probes] == ["ether2", "ether3"]
    assert probes[1].status == "no-rule"


# ─── ISSUE B: مرشّح LAN-only المُشترَك (يستبعد WAN + الأنفاق) ─────


def test_is_lan_port_excludes_default_wan_ether1():
    """بلا wan_iface معرّفًا نستخدم احتراز ether1 — لا تركيب dhcp-client
    على WAN يكسر التوجيه."""
    assert pss.is_lan_port({"name": "ether1", "type": "ether"}) is False
    assert pss.is_lan_port({"name": "ether2", "type": "ether"}) is True


def test_is_lan_port_respects_explicit_wan_iface():
    """مع wan_iface صريح (مأخوذ من setup_wizard_runs) نُستبعِده هو، لا
    ether1 — أيّ منفذ من الإيثرنت يمكن أن يكون الـWAN على راوتر معيّن."""
    assert pss.is_lan_port({"name": "ether1", "type": "ether"},
                           wan_iface="ether3") is True
    assert pss.is_lan_port({"name": "ether3", "type": "ether"},
                           wan_iface="ether3") is False


def test_is_lan_port_excludes_vpn_tunnel_types():
    """الفحص البنيوي القاسي عبر type — يلتقط الأنفاق حتى لو سُمّيت
    عشوائيًا. يغطي pppoe/pptp/l2tp/sstp/ovpn/ipsec/wireguard/gre/eoip."""
    tunnel_rows = [
        {"name": "hr-pppoe-ether1",  "type": "pppoe-out"},
        {"name": "pptp-out1",        "type": "pptp-out"},
        {"name": "hr-wg",            "type": "wireguard"},
        {"name": "ovpn-corp",        "type": "ovpn-out"},
        {"name": "ipsec-mgmt",       "type": "ipsec"},
        {"name": "l2tp-customer",    "type": "l2tp-out"},
        {"name": "sstp-mgmt",        "type": "sstp-out"},
        {"name": "gre-1",            "type": "gre-tunnel"},
        {"name": "eoip-1",           "type": "eoip-tunnel"},
        {"name": "lo",               "type": "loopback"},
        {"name": "hobe-vpn",         "type": "wireguard"},
    ]
    for r in tunnel_rows:
        assert pss.is_lan_port(r) is False, f"{r} should be excluded"


def test_is_lan_port_excludes_tunnel_names_even_with_blank_type():
    """احتراز ثانٍ: إن أعاد الراوتر type فارغًا/مشوّهًا، يلتقطها الاسم
    (lo، hr-wg، hobe-vpn، وبادئات pppoe-/pptp-/l2tp-/…)."""
    name_only = [
        {"name": "lo", "type": ""},
        {"name": "hr-wg", "type": ""},
        {"name": "hobe-vpn", "type": ""},
        {"name": "hr-pppoe-ether1", "type": ""},
        {"name": "pppoe-out2", "type": ""},
        {"name": "pptp-out9", "type": ""},
        {"name": "wireguard-test", "type": ""},
    ]
    for r in name_only:
        assert pss.is_lan_port(r) is False, f"{r} should be excluded"


def test_is_lan_port_keeps_typical_lan_ports():
    """ether2..ether8، bridge، vlan، wlan، sfp جميعها LAN ports صالحة."""
    rows = [
        {"name": "ether2",     "type": "ether"},
        {"name": "ether8",     "type": "ether"},
        {"name": "sfp1",       "type": "ether"},
        {"name": "bridge-lan", "type": "bridge"},
        {"name": "vlan-100",   "type": "vlan"},
        {"name": "wlan1",      "type": "wlan"},
    ]
    for r in rows:
        assert pss.is_lan_port(r) is True, f"{r} should be allowed"


def test_filter_lan_ports_keeps_order_and_drops_excluded():
    """مثال واقعي مطابق لوصف العطل: ether1 (WAN افتراضي)، hr-pppoe-ether1،
    hr-wg، pptp-out1، lo، hobe-vpn ⇒ تُسقَط. ether2..ether8، sfp1 ⇒ تبقى."""
    interfaces = [
        {"name": "ether1",          "type": "ether"},
        {"name": "ether2",          "type": "ether"},
        {"name": "ether3",          "type": "ether"},
        {"name": "ether4",          "type": "ether"},
        {"name": "ether5",          "type": "ether"},
        {"name": "ether6",          "type": "ether"},
        {"name": "ether7",          "type": "ether"},
        {"name": "ether8",          "type": "ether"},
        {"name": "sfp1",            "type": "ether"},
        {"name": "hr-pppoe-ether1", "type": "pppoe-out"},
        {"name": "pptp-out1",       "type": "pptp-out"},
        {"name": "hr-wg",           "type": "wireguard"},
        {"name": "lo",              "type": "loopback"},
        {"name": "hobe-vpn",        "type": "wireguard"},
    ]
    out = pss.filter_lan_ports(interfaces)
    names = [r["name"] for r in out]
    assert names == ["ether2", "ether3", "ether4", "ether5", "ether6",
                     "ether7", "ether8", "sfp1"]
    # الحقول الأخرى محفوظة لكل صفّ ناجح
    assert out[0]["type"] == "ether"


class _StubRes:
    """نتيجة API ستب على شكل MtResult (ok/data/error) بلا راوتر."""
    def __init__(self, *, ok, data=None, error=""):
        self.ok = ok
        self.data = data or []
        self.error = error


def test_read_loop_status_via_stub_client_bound_then_searching():
    """يثبت إعادة استخدام عميل API المُمرَّر: bound→لوب، searching→لا."""
    captured = {}

    def stub_dhcp_client_list(nas_call):
        captured["nas"] = nas_call
        return _StubRes(ok=True, data=[
            {"interface": "ether2", "status": "bound",
             "address": "172.16.0.9/24", "dhcp-server": "172.16.0.1",
             "comment": "HR-LoopDetect ether2"},
            {"interface": "ether3", "status": "searching...",
             "comment": "HR-LoopDetect ether3"},
        ])

    probes, error = pss.read_loop_status(
        {"id": 7}, stub_dhcp_client_list)
    assert error == ""
    assert captured["nas"] == {"id": 7}  # مُرِّر صفّ الراوتر كما هو
    assert probes[0].is_loop is True
    assert probes[1].is_loop is False


def test_read_loop_status_surfaces_api_error():
    def failing(nas_call):
        return _StubRes(ok=False, error="تعذر الاتصال")
    probes, error = pss.read_loop_status({"id": 1}, failing)
    assert probes == []
    assert "تعذر الاتصال" in error


# ─── بناء أوامر الدفع + إعادة استخدام منفّذ mt_programming ────────


def test_build_push_commands_sequence_add_run_remove():
    cmds = pss.build_push_commands(
        "the script body", name="hr-pss-x", comment="hoberadius:pss:x")
    paths = [c.path for c in cmds]
    assert paths == [
        "/system/script/add",
        "/system/script/run",
        "/system/script/remove",
    ]
    # add يحمل المصدر والاسم والتعليق
    add = cmds[0]
    assert add.attrs["name"] == "hr-pss-x"
    assert add.attrs["source"] == "the script body"
    assert add.attrs["comment"] == "hoberadius:pss:x"
    # run بالاسم
    assert cmds[1].attrs["number"] == "hr-pss-x"


def test_build_push_commands_cleanup_false_omits_remove():
    cmds = pss.build_push_commands(
        "s", name="n", comment="c", cleanup=False)
    assert [c.path for c in cmds] == [
        "/system/script/add", "/system/script/run"]


class _FakeClient:
    """عميل وهمي يسجّل كل استدعاء run — يثبت أن منفّذ mt_programming
    يدفع الأوامر بالترتيب الصحيح عبر client.run."""
    def __init__(self):
        self.calls = []

    def run(self, path, attrs=None, queries=None):
        self.calls.append((path, dict(attrs or {})))
        return []


def test_apply_commands_executes_push_via_mt_programming():
    from app.radius.services import mt_programming as mtp
    plan = pss.build_plan("bt_wifi_block", ["ether2"])
    cmds = pss.build_push_commands(
        plan.script, name="hr-pss-bt", comment="hoberadius:pss:bt_wifi_block")
    fake = _FakeClient()
    result = mtp.apply_commands(fake, cmds)
    assert result.ok is True
    # نُفِّذت الأوامر الثلاثة بالترتيب على نفس منفّذ البرمجة الموجود
    assert [p for p, _ in fake.calls] == [
        "/system/script/add",
        "/system/script/run",
        "/system/script/remove",
    ]
    # السكربت المُولّد وصل فعليًا في مصدر السكربت
    assert "ether2" in fake.calls[0][1]["source"]


# ─── حالة الخدمة: حفظ/قراءة (مفعّلة على مداخل X,Y / غير مفعّلة) ───


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pss_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_service_state_round_trip(app):
    """حفظ الحالة ثم قراءتها يعيد enabled + قائمة المنافذ كما هي."""
    with app.app_context():
        from app.radius.routes import port_script_services as route

        # ابتداءً: غير مفعّلة، بلا منافذ
        st0 = route._get_state(7, "bt_wifi_block")
        assert st0["enabled"] is False
        assert st0["ports"] == []

        # تفعيل على منفذين
        route._set_state(7, "bt_wifi_block", enabled=True,
                         ports=["ether2", "ether3"])
        st1 = route._get_state(7, "bt_wifi_block")
        assert st1["enabled"] is True
        assert st1["ports"] == ["ether2", "ether3"]

        # حالة راوتر/خدمة أخرى مستقلّة تمامًا
        assert route._get_state(7, "loop_detect")["enabled"] is False
        assert route._get_state(9, "bt_wifi_block")["enabled"] is False

        # تعطيل يمسح المنافذ
        route._set_state(7, "bt_wifi_block", enabled=False, ports=[])
        st2 = route._get_state(7, "bt_wifi_block")
        assert st2["enabled"] is False
        assert st2["ports"] == []


def test_port_rows_extracts_interface_state(app):
    """يستخرج اسم/running/disabled لكل صفّ مُكتشف.

    تحديث (يونيو 2026 — ISSUE B): _port_rows صارت تطبّق مرشّح LAN-only،
    فـether1 يُستبعَد افتراضًا (احتراز WAN). لإثبات منطق الاستخراج نفسه
    نمرّر wan_iface="" مع صفوف LAN آمنة (ether2/ether5)."""
    with app.app_context():
        from app.radius.routes import port_script_services as route
        rows = route._port_rows([
            {"name": "ether2", "running": True, "type": "ether"},
            {"name": "ether5", "running": False, "disabled": "true",
             "type": "ether"},
            {"name": "", "running": True},  # يُتجاهَل (بلا اسم)
        ])
        assert [r["name"] for r in rows] == ["ether2", "ether5"]
        assert rows[0]["running"] is True
        assert rows[1]["running"] is False
        assert rows[1]["disabled"] is True


def test_port_rows_drops_wan_default_and_tunnels(app):
    """_port_rows تستبعد ether1 (WAN افتراضي) والأنفاق المعروفة قبل
    عرض المربّعات على المشغّل — لا يستطيع اختيار WAN/VPN لتركيب
    dhcp-client أو TTL=1 عليه أصلًا."""
    with app.app_context():
        from app.radius.routes import port_script_services as route
        rows = route._port_rows([
            {"name": "ether1",          "type": "ether", "running": True},
            {"name": "ether2",          "type": "ether", "running": True},
            {"name": "hr-pppoe-ether1", "type": "pppoe-out", "running": True},
            {"name": "pptp-out1",       "type": "pptp-out", "running": True},
            {"name": "hr-wg",           "type": "wireguard", "running": True},
            {"name": "lo",              "type": "loopback", "running": True},
            {"name": "hobe-vpn",        "type": "wireguard", "running": True},
            {"name": "sfp1",            "type": "ether", "running": False},
        ])
        names = [r["name"] for r in rows]
        assert names == ["ether2", "sfp1"]


def test_port_rows_honours_explicit_wan_iface(app):
    """إن مُرِّر wan_iface صراحةً يُحجَب هو فقط — لا تطبيق ether1
    الافتراضي. (يحاكي راوترًا الـWAN فيه ether3 لا ether1.)"""
    with app.app_context():
        from app.radius.routes import port_script_services as route
        rows = route._port_rows([
            {"name": "ether1", "type": "ether", "running": True},
            {"name": "ether2", "type": "ether", "running": True},
            {"name": "ether3", "type": "ether", "running": True},
        ], wan_iface="ether3")
        names = [r["name"] for r in rows]
        assert "ether1" in names
        assert "ether2" in names
        assert "ether3" not in names
