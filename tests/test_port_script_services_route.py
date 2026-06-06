"""اختبارات مسار «خدمات السكربت المبنيّة على المنافذ» (تكامل خفيف).

تُثبت أنّ كل شيء جاهز فعليًا بحيث يبقى للمستخدم *فقط* لصق السكربتات:
  • الصفحة تُعرض، البطاقات والخدمات تظهر، والقالب المبدئي يُعرض كشارة
    «بانتظار السكربت» وزر الدفع معطّل.
  • حالما يُلصق سكربت حقيقي (is_placeholder=False)، يعمل الدفع للراوتر
    عبر نفس منفّذ mt_programming (add → run → remove)، وتُحفظ الحالة
    «مفعّلة على المنافذ»، ثم تُعطّلها الإزالة.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest

from app.radius.services import port_script_services as pss


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pssr_")
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


@pytest.fixture
def client(app):
    return app.test_client()


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"pss_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="pss-pass", full_name="PSS Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "pss-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'pss-rtr', '203.0.113.20', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


class _FakeClient:
    """عميل راوتر وهمي — يسجّل كل أمر بلا اتصال حقيقي."""
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def connect(self):
        pass

    def close(self):
        pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        return []


def _install_real_script(monkeypatch, slug="bt_wifi_block"):
    """يحاكي «لصق المستخدم للسكربتات» — يستبدل القالب المبدئي بخدمة
    بسكربتي تفعيل/إزالة حقيقيين و is_placeholder=False.

    ملاحظة: نستورد الوحدة *طازجة* لأنّ fixture التطبيق يعيد تحميل حزمة
    app؛ فالمرجع المستورد أعلى الملف يصبح قديمًا بينما يستعمل المسار
    النسخة المعاد تحميلها."""
    from app.radius.services import port_script_services as fresh_pss
    base = fresh_pss.get_service(slug)
    # نقطة الحقن هي {{IFACES}} داخل القالب، و{{IFACE}} داخل سطر المنفذ.
    real = dataclasses.replace(
        base,
        script_template="# enable on {{PORTS}}\n{{IFACES}}\n",
        iface_line_template="/interface ethernet set [find name={{IFACE}}] disabled=no",
        remove_template="# disable on {{PORTS}}\n{{IFACES}}\n",
        remove_iface_line_template="/interface ethernet set [find name={{IFACE}}] disabled=yes",
        is_placeholder=False,
    )
    monkeypatch.setitem(fresh_pss.REGISTRY, slug, real)
    return real


# ─── العرض الأساسي + الخدمتان المفعّلتان + حارس القالب المبدئي ────


def test_form_renders_activated_services_and_state(app, client, monkeypatch):
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _login(client)
    # الصفحة العامة: بطاقات الخدمتين
    res = client.get("/admin/radius/mt/1/port-services")
    body = res.get_data(as_text=True)
    assert res.status_code == 200
    assert "منع بث البلوتوث والواي فاي" in body
    assert "تتبّع اللوب" in body
    # الخدمتان مفعّلتان (is_placeholder=False) → لا شارة «بانتظار السكربت»
    assert "بانتظار السكربت" not in body
    # اختيار الخدمة → بانر الحالة «غير مفعّلة» + زر معاينة التفعيل ظاهر
    res2 = client.get("/admin/radius/mt/1/port-services?slug=bt_wifi_block")
    body2 = res2.get_data(as_text=True)
    assert "غير مفعّلة" in body2
    assert "معاينة سكربت التفعيل" in body2


def test_loop_detect_form_shows_loop_check_button(app, client, monkeypatch):
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _login(client)
    body = client.get(
        "/admin/radius/mt/1/port-services?slug=loop_detect"
    ).get_data(as_text=True)
    # زر «فحص اللوب» يظهر لخدمة كشف اللوب فقط
    assert "فحص اللوب" in body


def test_apply_blocked_while_placeholder(app, client, monkeypatch):
    """حارس القالب المبدئي ما زال يمنع الدفع — نُثبّت خدمة مبدئية مؤقتة
    لإثباته (الخدمتان الحقيقيتان مفعّلتان الآن)."""
    import dataclasses
    _seed(app)
    from app.radius.routes import port_script_services as route
    from app.radius.services import port_script_services as fresh
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    base = fresh.get_service("bt_wifi_block")
    monkeypatch.setitem(fresh.REGISTRY, "bt_wifi_block",
                        dataclasses.replace(base, is_placeholder=True))
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/bt_wifi_block/apply",
        data={"_csrf_token": token, "ports": "ether2", "confirm": "1"},
    )
    body = res.get_data(as_text=True)
    # القالب مبدئي → لا دفع، رسالة واضحة
    assert "قالب مبدئي" in body


# ─── الدفع الحقيقي بعد «لصق السكربت» + حفظ الحالة + الإزالة ───────


def test_apply_pushes_script_and_saves_state(app, client, monkeypatch):
    _seed(app)
    _install_real_script(monkeypatch, "bt_wifi_block")

    fake = _FakeClient()
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_connect_client", lambda nas: fake)
    # نتجنّب انتظار اكتشاف منافذ راوتر غير موجود (مهلة الشبكة) — نُرجِع
    # واجهات وهمية بحالتها (يُغذّي عرض المنافذ أيضًا).
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": "ether2", "running": True, "type": "ether"},
        {"name": "ether3", "running": False, "type": "ether"},
    ])

    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/bt_wifi_block/apply",
        data={"_csrf_token": token, "ports": "ether2", "confirm": "1"},
    )
    body = res.get_data(as_text=True)
    assert res.status_code == 200

    # دُفعت أوامر السكربت عبر نفس منفّذ mt_programming بالترتيب:
    assert [p for p, _ in fake.calls] == [
        "/system/script/add",
        "/system/script/run",
        "/system/script/remove",
    ]
    # السكربت الفعلي المُولّد (بعد تعويض الواجهة) وصل في مصدر السكربت:
    assert "ether2" in fake.calls[0][1]["source"]

    # حُفظت الحالة: مفعّلة على ether2 (تظهر بعد إعادة فتح الصفحة)
    page = client.get(
        "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
    ).get_data(as_text=True)
    assert "مفعّلة" in page
    assert "ether2" in page

    # الإزالة تدفع سكربت الإزالة وتُعطّل الحالة
    fake.calls.clear()
    rem = client.post(
        "/admin/radius/mt/1/port-services/bt_wifi_block/remove",
        data={"_csrf_token": token, "confirm": "1"},  # المنافذ من الحالة المحفوظة
    )
    assert rem.status_code == 200
    assert [p for p, _ in fake.calls] == [
        "/system/script/add",
        "/system/script/run",
        "/system/script/remove",
    ]
    # سكربت الإزالة عوّض ether2 المحفوظة
    assert "ether2" in fake.calls[0][1]["source"]

    page2 = client.get(
        "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
    ).get_data(as_text=True)
    assert "غير مفعّلة" in page2


# ─── فحص اللوب الحيّ عبر /ip dhcp-client (عميل API ستب) ──────────


def test_loop_check_reports_live_status(app, client, monkeypatch):
    """زر «فحص اللوب» يقرأ /ip dhcp-client الموسوم HR-LoopDetect عبر
    mac.dhcp_client_list ويعرض: ether2 bound = لوب مكتشف، ether3
    searching = لا لوب."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])

    class _Res:
        ok = True
        error = ""

        def __init__(self, data):
            self.data = data

    captured = {}

    def fake_dhcp(nas):
        # نتأكّد أن المسار مرّر صفّ الراوتر الخام (فيه api_user) لا مخطّط
        # _nas_for_mac — لأن mac يبني router_cfg من أعمدة api_*.
        captured["nas"] = nas
        return _Res([
            {"interface": "ether2", "status": "bound",
             "address": "192.168.88.7/24", "gateway": "192.168.88.1",
             "dhcp-server": "192.168.88.1",
             "comment": "HR-LoopDetect ether2"},
            {"interface": "ether3", "status": "searching...",
             "comment": "HR-LoopDetect ether3"},
        ])

    monkeypatch.setattr(route.mac, "dhcp_client_list", fake_dhcp)

    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/loop-check",
        data={"_csrf_token": token},  # بلا ports → يعرض كل الموسومين
    )
    body = res.get_data(as_text=True)
    assert res.status_code == 200
    # لوب على ether2 (رجع IP من DHCP server)
    assert "لوب مكتشف على ether2" in body
    assert "192.168.88.7/24" in body
    # لا لوب على ether3
    assert "لا لوب على ether3" in body
    # مُرِّر صفّ الراوتر الخام (يحمل بيانات اعتماد api_user)
    assert captured["nas"].get("api_user") == "hr-test"
