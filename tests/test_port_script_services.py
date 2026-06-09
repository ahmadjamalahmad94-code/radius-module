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
    with app.app_context():
        from app.radius.routes import port_script_services as route
        rows = route._port_rows([
            {"name": "ether1", "running": True, "type": "ether"},
            {"name": "ether2", "running": False, "disabled": "true"},
            {"name": "", "running": True},  # يُتجاهَل (بلا اسم)
        ])
        assert [r["name"] for r in rows] == ["ether1", "ether2"]
        assert rows[0]["running"] is True
        assert rows[1]["running"] is False
        assert rows[1]["disabled"] is True
