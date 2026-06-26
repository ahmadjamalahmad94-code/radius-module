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
import re
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
    # اختيار الخدمة → بانر الحالة «غير مفعّلة» (لأنّه بلا مداخل بعد)
    # + زر معاينة التفعيل ظاهر. يونيو 2026: enabled صار دائمًا True
    # للخدمات دائمة الإتاحة، فالبانر صار يَنعكس من ports|length فقط:
    # «بلا مداخل بعد» = «غير مفعّلة».
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


def test_page_title_reflects_active_service(app, client, monkeypatch):
    """تصحيح المالك (يونيو 2026): العنوان عند فتح صفحة خدمة بعينها يجب
    أن يحمل اسم الخدمة الفعلي (مثلاً «منع بث البلوتوث والواي فاي»)، لا
    العنوان الجامع «خدمات المنافذ». نتحقّق من ظهور اسم الخدمة في <title>
    وفي الهيدر القابل للقراءة على الصفحة. الزيارة العامّة (بلا slug)
    تبقى بالعنوان الجامع كما هو."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _login(client)

    # 1) خدمة محدّدة: bt_wifi_block — العنوان يعكس اسم الخدمة الحقيقي
    # في كلٍّ من <title> والـhub-hero-title الظاهر للمشغّل.
    body = client.get(
        "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
    ).get_data(as_text=True)
    assert "<title>منع بث البلوتوث والواي فاي" in body
    assert ('<h1 class="hub-hero-title">'
            'منع بث البلوتوث والواي فاي — pss-rtr</h1>') in body
    # العنوان الجامع لا يظهر منفردًا في الـ<title> أو الـhero
    assert "<title>خدمات المنافذ" not in body
    assert '"hub-hero-title">خدمات المنافذ' not in body

    # 2) خدمة loop_detect — نفس الشيء بـ«تتبّع اللوب»
    body2 = client.get(
        "/admin/radius/mt/1/port-services?slug=loop_detect"
    ).get_data(as_text=True)
    assert "<title>تتبّع اللوب" in body2
    assert ('<h1 class="hub-hero-title">'
            'تتبّع اللوب — pss-rtr</h1>') in body2

    # 3) الزيارة العامّة (بلا slug) — العنوان الجامع لا يزال صحيحًا
    body3 = client.get(
        "/admin/radius/mt/1/port-services"
    ).get_data(as_text=True)
    assert "<title>خدمات المنافذ" in body3
    assert ('<h1 class="hub-hero-title">'
            'خدمات المنافذ — pss-rtr</h1>') in body3


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
    # بعد remove: ports=[] → البانر «غير مفعّلة» (لا مَداخل، رغم أنّ
    # enabled في الخادم دائمًا True لخدمات نظافة الشبكة).
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


def test_config_form_and_loop_check_agree_on_bound_port(app, client, monkeypatch):
    """العطل الميدانيّ: منفذ متّصل (له dhcp-client HR-LoopDetect حالته bound)
    يظهر «مركّب» في «فحص اللوب» لكن «غير مركّب» في صفحة الإعداد (GET) — لأن
    الإعداد كان يقرأ الحالة المخزّنة (poller) لا الحيّة. الإصلاح: الإعداد يقرأ
    نفس المصدر الحيّ، فتتطابق الصفحتان ولا يظهر زر «تركيب» لمنفذ مُركّب."""
    _seed(app)
    # ether6 محفوظ كمطلوب تفعيله (سيناريو المالك: محفوظ + متّصل).
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "pss.1.loop_detect.ports", "ether6")
        tenants_repo.set_setting(1, "pss.1.loop_detect.enabled", "1")

    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover",
                        lambda nas: [{"name": "ether6", "type": "ether"}])

    class _Res:
        ok = True
        error = ""

        def __init__(self, data):
            self.data = data

    # ether6: dhcp-client HR-LoopDetect في حالة bound = مركّب + متّصل (online).
    def bound(nas):
        return _Res([
            {"interface": "ether6", "status": "bound",
             "address": "192.168.88.9/24", "gateway": "192.168.88.1",
             "dhcp-server": "192.168.88.1", "comment": "HR-LoopDetect ether6"},
        ])
    monkeypatch.setattr(route.mac, "dhcp_client_list", bound)

    # خليّة الحالة المرسومة تستعمل مسافة بين السمتين؛ بينما مُحدِّد الـJS
    # يستعمل أقواسًا «]data-pss-installed]» — فالـregex بالمسافة يَفصلهما.
    def _rendered_cell(html, value):
        return re.search(
            r'data-pss-rule-cell\s+data-pss-installed="%s"' % value, html)

    _login(client)
    # (1) صفحة الإعداد (GET) — تُظهر ether6 «مركّبة» من القراءة الحيّة، ولا
    #     «غير مركّبة»، ولا زرّ «تركيب» المرسوم لمنفذ مُركّب.
    form = client.get(
        "/admin/radius/mt/1/port-services?slug=loop_detect").get_data(as_text=True)
    assert _rendered_cell(form, "true")
    assert not _rendered_cell(form, "false")   # لا منفذ «غير مركّب» على الإعداد
    # زرّ «تركيب» المرسوم خادميًّا يحمل اسم المنفذ حرفيًّا (الـJS يبنيه بـ+port+).
    assert 'data-pss-port-action="apply" data-pss-port="ether6"' not in form

    # (2) فحص اللوب — نفس النتيجة بالضبط (المصدر الحيّ ذاته).
    check = client.get(
        "/admin/radius/mt/1/port-services/loop_detect/loop-check").get_data(as_text=True)
    assert _rendered_cell(check, "true")
    assert not _rendered_cell(check, "false")
    # الصفحتان متطابقتان الآن: كلتاهما «مركّبة» لـether6 (لا تناقض).


def test_loop_check_get_returns_200_not_405(app, client, monkeypatch):
    """تحديث الصفحة أو فتح رابط «فحص اللوب» مباشرةً = GET. الفحص قراءة فقط
    (/ip dhcp-client الموسوم، بلا تعديل) فيجب أن يعمل ويُرجع 200 — لا 405.
    بلا نموذج تُشتقّ المنافذ من الحالة المحفوظة."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])

    class _Res:
        ok = True
        error = ""

        def __init__(self, data):
            self.data = data

    monkeypatch.setattr(route.mac, "dhcp_client_list", lambda nas: _Res([
        {"interface": "ether2", "status": "bound",
         "address": "192.168.88.7/24", "gateway": "192.168.88.1",
         "dhcp-server": "192.168.88.1", "comment": "HR-LoopDetect ether2"},
        {"interface": "ether3", "status": "searching...",
         "comment": "HR-LoopDetect ether3"},
    ]))

    _login(client)
    # GET صِرف (تحديث/فتح مباشر) — بلا نموذج ولا CSRF.
    res = client.get(
        "/admin/radius/mt/1/port-services/loop_detect/loop-check")
    assert res.status_code == 200          # ليس 405 Method Not Allowed
    body = res.get_data(as_text=True)
    assert "لوب مكتشف على ether2" in body   # نفس نتائج الفحص تُعرض على GET
    assert "لا لوب على ether3" in body


def test_loop_check_returns_probe_for_every_selected_port(app, client, monkeypatch):
    """ISSUE A — تكامل: يختار المشغّل 9 منافذ لكن /ip dhcp-client يحتوي
    قواعد HR-LoopDetect لـ4 منها فقط (apply الأخير شُغِّل بـ4). قبل
    الإصلاح: النتيجة 4. بعد الإصلاح: 9 بطاقات بنفس ترتيب الاختيار،
    والـ5 الباقية تظهر بشكل صريح كـ«قاعدة غير مُركّبة»."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])

    class _Res:
        ok = True
        error = ""
        def __init__(self, data):
            self.data = data

    def fake_dhcp(nas):
        return _Res([
            {"interface": "ether2", "status": "bound",
             "address": "10.0.0.4/24", "comment": "HR-LoopDetect ether2"},
            {"interface": "ether3", "status": "searching...",
             "comment": "HR-LoopDetect ether3"},
            {"interface": "ether4", "status": "searching...",
             "comment": "HR-LoopDetect ether4"},
            {"interface": "ether5", "status": "bound",
             "address": "10.0.0.5/24", "comment": "HR-LoopDetect ether5"},
        ])
    monkeypatch.setattr(route.mac, "dhcp_client_list", fake_dhcp)

    _login(client)
    token = _csrf(client)
    selected = ["ether2", "ether3", "ether4", "ether5",
                "ether6", "ether7", "ether8", "sfp1", "sfp2"]
    # نموذج يقبل ports متعدّدة عبر CSV — _ports_from_form يقسّم بفواصل.
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/loop-check",
        data={"_csrf_token": token, "ports": ",".join(selected)},
    )
    body = res.get_data(as_text=True)
    assert res.status_code == 200
    # كل المنافذ التسعة لها بطاقة في DOM (data-pss-loop-iface فريد لكل واحد)
    for port in selected:
        assert f'data-pss-loop-iface="{port}"' in body, \
            f"missing probe for {port}"
    # ether2/ether5: لوب ⇒ data-pss-loop-probe="loop"
    assert body.count('data-pss-loop-probe="loop"') == 2
    # ether3/ether4: لا لوب ⇒ data-pss-loop-probe="clear"
    assert body.count('data-pss-loop-probe="clear"') == 2
    # ether6..ether8 + sfp1/sfp2 (5 منافذ): «no-rule» ⇒ تنبيه بانتظار apply
    assert body.count('data-pss-loop-probe="no-rule"') == 5
    assert "لم تُركَّب قاعدة كشف اللوب على ether6" in body


def test_apply_rejects_wan_and_tunnel_ports(app, client, monkeypatch):
    """ISSUE B — تكامل: المشغّل يصوغ POST بـ ether1 (WAN) ضمن المنافذ.
    حارس _validate_lan_ports يردّ رسالة عربية واضحة ولا يدفع السكربت."""
    _seed(app)
    _install_real_script(monkeypatch, "loop_detect")

    fake = _FakeClient()
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_connect_client", lambda nas: fake)
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": "ether1", "running": True, "type": "ether"},
        {"name": "ether2", "running": True, "type": "ether"},
        {"name": "hr-wg",  "running": True, "type": "wireguard"},
    ])
    _login(client)
    token = _csrf(client)
    # 3 منافذ: ether1 (WAN افتراضي) + hr-wg (نفق) + ether2 (LAN صالح)
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/apply",
        data={"_csrf_token": token, "confirm": "1",
              "ports": "ether1,hr-wg,ether2"},
    )
    body = res.get_data(as_text=True)
    assert res.status_code == 200
    # رسالة الخطأ ظاهرة وتُسمّي الواجهات الممنوعة
    assert "WAN/نفق" in body or "WAN" in body
    assert "ether1" in body and "hr-wg" in body
    # لم يُدفع أيّ سكربت إلى الراوتر
    assert fake.calls == []


# ─── الإدارة الاحترافية لكشف اللوب (يونيو 2026) ───────────────────
#   • apply-port: تطبيق/إزالة منفذًا منفذًا (JSON) + حالة تراكمية صادقة.
#   • سجل الفحوصات (router_loop_checks) من الفحص اليدوي.
#   • إعدادات الفحص الدوري + احترام الـpoller لها.
#   • جدول حالة المنافذ + بانر التناقض بين المحفوظ والواقع.


def test_apply_port_updates_state_incrementally(app, client, monkeypatch):
    """apply-port يدفع سكربت المنفذ الواحد ويُحدّث الحالة تراكميًا —
    منفذان ناجحان يظهران معًا، وإزالة أحدهما تُبقي الآخر."""
    _seed(app)
    _install_real_script(monkeypatch, "loop_detect")
    fake = _FakeClient()
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_connect_client", lambda nas: fake)
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": "ether2", "running": True, "type": "ether"},
        {"name": "ether3", "running": True, "type": "ether"},
    ])
    _login(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/apply-port",
        data={"_csrf_token": token, "port": "ether2", "mode": "apply"},
    )
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True and data["port"] == "ether2"
    assert [p for p, _ in fake.calls] == [
        "/system/script/add", "/system/script/run", "/system/script/remove",
    ]
    assert "ether2" in fake.calls[0][1]["source"]

    res2 = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/apply-port",
        data={"_csrf_token": token, "port": "ether3", "mode": "apply"},
    )
    assert res2.get_json()["ok"] is True
    # الحالة تراكمت: المنفذان معًا
    from app.radius.routes.port_script_services import _get_state
    with app.app_context(), app.test_request_context():
        st = _get_state(1, "loop_detect")
    assert st["enabled"] is True
    assert st["ports"] == ["ether2", "ether3"]

    # إزالة منفذ واحد تُبقي الآخر
    res3 = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/apply-port",
        data={"_csrf_token": token, "port": "ether2", "mode": "remove"},
    )
    assert res3.get_json()["ok"] is True
    with app.app_context(), app.test_request_context():
        st2 = _get_state(1, "loop_detect")
    assert st2["enabled"] is True
    assert st2["ports"] == ["ether3"]


def test_apply_port_failure_keeps_state_honest(app, client, monkeypatch):
    """فشل التركيب على منفذ لا يُسجّله «مفعّلًا» — مصدر التناقض القديم:
    بانر أخضر يدّعي 8 منافذ بينما القاعدة مركّبة على 5 فقط."""
    _seed(app)
    _install_real_script(monkeypatch, "loop_detect")
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": "ether2", "running": True, "type": "ether"},
    ])
    monkeypatch.setattr(
        route, "_push_to_router",
        lambda nas, plan, comment: (None, "تعذّر الاتصال بالراوتر"))
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/apply-port",
        data={"_csrf_token": token, "port": "ether2", "mode": "apply"},
    )
    data = res.get_json()
    assert data["ok"] is False
    assert "تعذّر الاتصال" in data["error"]
    from app.radius.routes.port_script_services import _get_state
    with app.app_context(), app.test_request_context():
        st = _get_state(1, "loop_detect")
    # يونيو 2026: loop_detect دائمة الإتاحة → enabled=True دائمًا، لكن
    # ports يَجب أن تَبقى فارغة بعد فشل التركيب (الـsource of truth
    # لما يَنطبق على الراوتر فعلًا).
    assert st["enabled"] is True
    assert st["ports"] == []


def test_loop_check_writes_history_and_page_shows_it(app, client, monkeypatch):
    """كل فحص يدوي يُدوَّن في سجل router_loop_checks بملخّصه (منافذ/لوب/
    قواعد مفقودة) وتفاصيل كل منفذ، ويظهر في قسم «سجل الفحوصات»."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])

    class _Res:
        ok = True
        error = ""
        def __init__(self, data):
            self.data = data

    monkeypatch.setattr(route.mac, "dhcp_client_list", lambda nas: _Res([
        {"interface": "ether2", "status": "bound",
         "address": "10.0.0.4/24", "comment": "HR-LoopDetect ether2"},
        {"interface": "ether3", "status": "searching...",
         "comment": "HR-LoopDetect ether3"},
    ]))
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/loop-check",
        data={"_csrf_token": token, "ports": "ether2,ether3,ether4"},
    )
    assert res.status_code == 200

    with app.app_context():
        from app.radius.db.repos import router_loop_checks_repo
        checks = router_loop_checks_repo.list_for_router(1, 1)
    assert len(checks) == 1
    c = checks[0]
    assert c["source"] == "manual" and c["ok"] == 1
    assert c["ports_total"] == 3
    assert c["loops_found"] == 1          # ether2 bound
    assert c["rules_missing"] == 1        # ether4 بلا قاعدة
    assert {d["iface"] for d in c["details"]} == {"ether2", "ether3", "ether4"}

    # السجل ظاهر في الصفحة (نفس استجابة الفحص تعرضه)
    body = res.get_data(as_text=True)
    assert "سجل الفحوصات" in body
    assert 'data-pss-loop-check=' in body
    # وجدول الحالة يعرض القاعدة المفقودة + بانر التناقض غائب (لا حالة محفوظة)
    assert "حالة منافذ كشف اللوب" in body
    assert 'data-pss-loop-row="ether4"' in body


def test_loop_table_mismatch_banner_when_saved_state_disagrees(app, client, monkeypatch):
    """الحالة المحفوظة تقول «مفعّلة على ether2,ether3» بينما الفحص الحي
    يجد قاعدة على ether2 فقط ⇒ بانر تناقض صريح + صفّ «غير مركّبة»."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])

    class _Res:
        ok = True
        error = ""
        def __init__(self, data):
            self.data = data

    monkeypatch.setattr(route.mac, "dhcp_client_list", lambda nas: _Res([
        {"interface": "ether2", "status": "searching...",
         "comment": "HR-LoopDetect ether2"},
    ]))
    _login(client)
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, "pss.1.loop_detect.enabled", "1")
        tenants_repo.set_setting(1, "pss.1.loop_detect.ports", "ether2,ether3")
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/loop-check",
        data={"_csrf_token": token},  # المنافذ من الحالة المحفوظة
    )
    body = res.get_data(as_text=True)
    assert res.status_code == 200
    # بانر التناقض يسمّي المنفذ الناقص
    assert "data-pss-loop-mismatch" in body
    assert "ether3" in body
    # الجدول: ether2 مركّبة، ether3 غير مركّبة مع زر تركيب
    assert 'data-pss-loop-row="ether2"' in body
    assert 'data-pss-loop-row="ether3"' in body
    assert "غير مركّبة" in body
    assert 'data-pss-port-action="apply" data-pss-port="ether3"' in body


def test_loop_poll_settings_saved_and_respected_by_poller(app, client, monkeypatch):
    """حفظ إعدادات الفحص الدوري من الصفحة + احترام الـpoller لها:
    معطَّل ⇒ لا فحص، فترة لم تنقضِ ⇒ لا فحص، انقضت/لا سجل ⇒ فحص."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/loop-settings",
        data={"_csrf_token": token, "poll_minutes": "30"},  # بلا poll_enabled = إيقاف
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    page = client.get(
        "/admin/radius/mt/1/port-services?slug=loop_detect"
    ).get_data(as_text=True)
    assert "الفحص الدوري التلقائي" in page
    assert 'value="30"' in page

    from app.workers.loop_probe_poller import _poll_due
    with app.app_context():
        # معطَّل ⇒ ليس مستحقًا
        assert _poll_due(1, 1, {"enabled": False, "minutes": 30}) is False
        # مفعَّل بلا سجل سابق ⇒ مستحق الآن
        assert _poll_due(1, 1, {"enabled": True, "minutes": 30}) is True
        # فحص دوري للتو ⇒ غير مستحق قبل انقضاء الفترة
        from app.radius.db.repos import router_loop_checks_repo
        router_loop_checks_repo.insert_check(
            tenant_id=1, router_id=1, source="poller", ok=True, details=[])
        assert _poll_due(1, 1, {"enabled": True, "minutes": 30}) is False


def test_poller_logs_checks_in_history(app, client):
    """دورة الـpoller تسجّل فحصًا في السجل (source=poller) بنتائجه."""
    _seed(app)
    with app.app_context():
        from app.workers.loop_probe_poller import record_router_probes
        record_router_probes(1, 1, [
            {"interface": "ether2", "status": "bound",
             "address": "10.0.0.9/24", "comment": "HR-LoopDetect ether2"},
        ], only_ports=["ether2", "ether3"], log_source="poller")
        from app.radius.db.repos import router_loop_checks_repo
        checks = router_loop_checks_repo.list_for_router(1, 1)
    assert len(checks) == 1
    assert checks[0]["source"] == "poller"
    assert checks[0]["ports_total"] == 2
    assert checks[0]["loops_found"] == 1
    assert checks[0]["rules_missing"] == 1


def test_picker_hides_wan_and_tunnel_interfaces(app, client, monkeypatch):
    """ISSUE B — تكامل عرض: شبكة المربّعات تعرض ether2..ether8 + sfp1 فقط،
    ولا تعرض ether1 (WAN افتراضي) ولا hr-wg/hr-pppoe-ether1/lo/hobe-vpn."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": "ether1",          "type": "ether",      "running": True},
        {"name": "ether2",          "type": "ether",      "running": True},
        {"name": "ether3",          "type": "ether",      "running": True},
        {"name": "ether8",          "type": "ether",      "running": True},
        {"name": "sfp1",            "type": "ether",      "running": True},
        {"name": "hr-pppoe-ether1", "type": "pppoe-out",  "running": True},
        {"name": "hr-wg",           "type": "wireguard",  "running": True},
        {"name": "lo",              "type": "loopback",   "running": True},
        {"name": "hobe-vpn",        "type": "wireguard",  "running": True},
    ])
    _login(client)
    body = client.get(
        "/admin/radius/mt/1/port-services?slug=loop_detect"
    ).get_data(as_text=True)
    # نُدقّق على value="<iface>" الذي يُصدِره القالب لكل مربّع.
    assert 'value="ether2"' in body
    assert 'value="ether8"' in body
    assert 'value="sfp1"' in body
    # الواجهات الممنوعة لا تظهر في النموذج (لا مربّع باسمها)
    for blocked in ("ether1", "hr-pppoe-ether1", "hr-wg", "lo", "hobe-vpn"):
        assert f'value="{blocked}"' not in body, \
            f"{blocked} should not appear in the picker"


# ─── حذف منفذ واحد من رقائق «مفعّلة حاليًا» (per-interface delete) ─────
def _set_ports(app, slug, ports, nas_id=1):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, f"pss.{nas_id}.{slug}.ports", ",".join(ports))


def test_iface_chips_render_delete_button_per_port(app, client, monkeypatch):
    """كل منفذ مُضاف للخدمة يظهر كرقاقة مع زرّ حذف خاص به."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _set_ports(app, "bt_wifi_block", ["ether2", "ether3", "ether4"])
    _login(client)
    html = client.get(
        "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
    ).get_data(as_text=True)
    # رقاقة + زرّ حذف لكل منفذ
    assert "data-pss-iface-list" in html
    for p in ("ether2", "ether3", "ether4"):
        assert f'data-pss-iface-chip="{p}"' in html
        assert f'data-pss-port="{p}"' in html
    # عدد أزرار الحذف = عدد المنافذ (صنف الزرّ يظهر في الترميم فقط)
    assert html.count('class="pss-iface-del"') == 3
    # يستهدف نقطة apply-port (نفس آليّة الإزالة لكل منفذ)
    assert "/port-services/bt_wifi_block/apply-port" in html


def test_port_grid_renders_delete_button_per_applied_interface(app, client, monkeypatch):
    """تصحيح المالك (يونيو 2026): المربّعات الكبيرة الكاملة العرض في
    «المنافذ / الواجهات» كانت تعرض اسم الواجهة وحالتها فقط بلا أي زرّ
    حذف ظاهر — فظنّ المالك أنها قائمة المنافذ المُطبَّقة بلا وسيلة لإزالة
    واحدة. الآن كل مربّع لواجهة *مُطبَّقة فعلًا* يحمل زرّ حذف (×) يستهدف
    apply-port، بينما الواجهات غير المُطبَّقة تبقى بلا زرّ حذف."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    # نكتشف 5 واجهات (ether2..ether6) لكن المُطبَّق منها 3 فقط.
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": f"ether{i}", "running": True, "type": "ether"}
        for i in range(2, 7)
    ])
    _set_ports(app, "bt_wifi_block", ["ether2", "ether3", "ether4"])
    _login(client)
    html = client.get(
        "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
    ).get_data(as_text=True)

    import re
    # شبكة المربّعات تحمل نقطة الإزالة (apply-port) ليستهلكها زرّ كل مربّع
    assert "data-pss-port-grid" in html
    assert "/port-services/bt_wifi_block/apply-port" in html
    # نلتقط الواجهات التي لها زرّ حذف في الشبكة (data-pss-port-del يَخصّ
    # الشبكة وحدها — رقائق البانر تستخدم data-pss-iface-del المختلف).
    del_ports = set(re.findall(
        r'data-pss-port-del\s+data-pss-port="([^"]+)"', html))
    # زرّ حذف للواجهات المُطبَّقة الثلاث فقط — لا أكثر ولا أقل
    assert del_ports == {"ether2", "ether3", "ether4"}
    # الواجهات غير المُطبَّقة (ether5/ether6) موجودة كصندوق اختيار بلا زرّ حذف
    assert 'value="ether5"' in html and 'value="ether6"' in html
    assert "ether5" not in del_ports and "ether6" not in del_ports


def test_chip_delete_removes_single_interface_from_list(app, client, monkeypatch):
    """حذف منفذ عبر زرّ الرقاقة (apply-port mode=remove) يُسقطه من قائمة
    منافذ الخدمة ويُبقي البقيّة، والصفحة تُعيد عرض الباقين فقط."""
    _seed(app)
    _install_real_script(monkeypatch, "bt_wifi_block")
    fake = _FakeClient()
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_connect_client", lambda nas: fake)
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _set_ports(app, "bt_wifi_block", ["ether2", "ether3"])
    _login(client)
    token = _csrf(client)

    res = client.post(
        "/admin/radius/mt/1/port-services/bt_wifi_block/apply-port",
        data={"_csrf_token": token, "port": "ether2", "mode": "remove"},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True and body["mode"] == "remove" and body["port"] == "ether2"

    from app.radius.routes.port_script_services import _get_state
    with app.app_context(), app.test_request_context():
        st = _get_state(1, "bt_wifi_block")
    assert st["ports"] == ["ether3"]          # ether2 dropped, ether3 kept

    # الصفحة الآن تعرض رقاقة ether3 فقط
    html = client.get(
        "/admin/radius/mt/1/port-services?slug=bt_wifi_block"
    ).get_data(as_text=True)
    assert 'data-pss-iface-chip="ether3"' in html
    assert 'data-pss-iface-chip="ether2"' not in html
