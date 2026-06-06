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
    # {{IFACES}} → سطر لكل واجهة من قالب السطر
    assert script.count("على الواجهة ether2") == 1
    assert script.count("على الواجهة ether3") == 1
    # لم تبقَ أي عناصر نائبة
    assert "{{PORTS}}" not in script
    assert "{{IFACES}}" not in script
    assert "{{IFACE}}" not in script


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


def test_build_plan_apply_summary_and_placeholder_warning():
    plan = pss.build_plan("bt_wifi_block", ["ether2"])
    assert plan.slug == "bt_wifi_block"
    assert plan.selected_ports == ["ether2"]
    assert plan.is_placeholder is True
    # تحذير القالب المبدئي موجود (الدفع معطّل)
    assert any("قالب مبدئي" in w for w in plan.warnings)
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
