"""علّتا صفحة «خدمات المنافذ» (loop_detect) — تركيب + مصالحة الحالة.

BUG 1 — زرّ «تركيب» (data-pss-port-action=apply) كان لا يُعطي أثرًا مرئيًّا:
        يُحدّث خليّة صغيرة فقط («تم — حدّث الصفحة») بلا توست وبلا تحديث
        صفّ/بانر، فيبدو «لا يفعل شيئًا». الإصلاح: توست دائمًا + تحديث الصفّ
        في مكانه + مصالحة البانر (نتحقّق من تركيب الـmarkup/المعالج).

BUG 2 — البانر العلوي كان يَدّعي «مفعّلة على كل المنافذ» (أخضر) بينما
        القراءة الحقيقيّة تُظهر منافذ غير مركّبة. الإصلاح: رأس صادق
        «مفعّلة على X/Y» + رقائق كهرمانيّة للمنافذ غير المركّبة + بانر تناقض.

شغّل الملف وحده (عزل لكل ملف)."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_pssrec_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
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
    """يُسجّل الدخول بالمالك الرئيسيّ — المبدأ الوحيد غير المقيَّد.

    مسارات «خدمات المنافذ» محروسة بـ``requires_perm(PERM_PROGRAM)``. بعد قرار
    «owner-only bypass» لم يَعُد علم ``is_super_admin`` وحده يَمنح ``mikrotik.*``
    (المالك = ``primary_admin_id()`` = أصغر معرّف)، و``create_app`` يَبذر مدير
    الإقلاع فيَحجز ذلك المعرّف — فإنشاء «سوبر» ثانٍ هنا يُنتج حسابًا غير مالك
    فيُردّ بـ403.
    """
    from app.radius.db.repos import admins_repo
    pid = admins_repo.primary_admin_id()
    if pid is None:
        pid = admins_repo.create_admin(
            username=f"pss_{uuid4().hex[:10]}", password="p", full_name="T",
            is_super_admin=True).id
    owner = admins_repo.get_admin(int(pid))
    admins_repo.update_admin(int(pid), password="p")
    res = client.post("/admin/radius/login",
                      data={"username": owner.username, "password": "p"},
                      follow_redirects=False)
    assert res.status_code in {302, 303}


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                "INSERT INTO nas_devices(id,tenant_id,name,address,secret,"
                "vendor,nas_type,enabled,created_at,connection_mode,api_user,"
                "api_password) VALUES(?,1,'pss-rtr','203.0.113.20','sek',"
                "'mikrotik','hotspot',1,?,'direct','hr-test','pw')",
                (nas_id, now))


def _set_state(app, ports, *, nas_id=1, slug="loop_detect"):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.set_setting(1, f"pss.{nas_id}.{slug}.ports", ",".join(ports))
        tenants_repo.set_setting(1, f"pss.{nas_id}.{slug}.enabled", "1")


def _set_readings(app, mapping, *, nas_id=1):
    """mapping: {port: status} — 'no-rule'⇒غير مركّبة، 'searching'⇒مركّبة."""
    with app.app_context():
        from app.radius.db.repos import router_loop_probes_repo
        for port, status in mapping.items():
            router_loop_probes_repo.upsert_reading(
                tenant_id=1, router_id=nas_id, interface=port, status=status)


def _page(client, slug="loop_detect", nas_id=1) -> str:
    res = client.get(f"/admin/radius/mt/{nas_id}/port-services?slug={slug}")
    assert res.status_code == 200
    return res.get_data(as_text=True)


def _markup(html: str) -> str:
    """يجرّد كتل <script> — فلا تَتلوّث تأكيدات الـmarkup بالنصوص الحرفيّة
    داخل JS (مثل سلاسل reconcileBanner أو محدّدات data-pss-*)."""
    import re
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.DOTALL)


# ════════════ BUG 2 — البانر لا يَدّعي «جاهز على الكل» كذبًا ════════════
def test_banner_partial_when_saved_but_not_installed(app, client, monkeypatch):
    """saved=enabled على 8 منافذ، لكن 4 منها غير مركّبة فعليًّا (no-rule):
    رأس البانر «partial» يَعرض «مفعّلة على 4/8»، لا «enabled» أخضر كاذب."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    saved = [f"ether{i}" for i in range(2, 10)]            # 8 منافذ
    _set_state(app, saved)
    _set_readings(app, {p: ("no-rule" if i < 4 else "searching")
                        for i, p in enumerate(saved)})      # 4 غير مركّبة
    _login(client)
    markup = _markup(_page(client))
    # رأس البانر = partial (لا enabled الأخضر الكاذب)
    assert 'data-pss-state-banner="partial"' in markup
    assert 'data-pss-state-banner="enabled"' not in markup
    # العنوان الصادق «مفعّلة على 4/8»
    assert "مفعّلة على" in markup and "4/8" in markup
    assert "منفذ محفوظ بانتظار التركيب" in markup


def test_uninstalled_chips_render_distinct_amber(app, client, monkeypatch):
    """رقائق المنافذ غير المركّبة تَحمل صنف التحذير الكهرماني وتلميحًا —
    لا تَظهر خضراء «جاهزة»."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    saved = ["ether2", "ether3", "ether4"]
    _set_state(app, saved)
    _set_readings(app, {"ether2": "no-rule", "ether3": "no-rule",
                        "ether4": "searching"})
    _login(client)
    markup = _markup(_page(client))
    # رقاقتان غير مركّبتين (ether2/ether3) تَحملان صنف التحذير (عدّ سمة
    # الصنف المُصيَّرة تحديدًا، لا تعريف CSS).
    assert markup.count('class="pss-iface-chip pss-iface-chip--warn"') == 2
    assert "غير مركّبة فعليًا على الراوتر" in markup            # تلميح الرقاقة
    # بانر التناقض يُسمّي المنفذين غير المركّبين
    assert "data-pss-loop-mismatch" in markup
    assert "ether2" in markup and "ether3" in markup


def test_banner_enabled_green_when_all_installed(app, client, monkeypatch):
    """كل المنافذ المحفوظة مركّبة فعلًا ⇒ البانر أخضر «enabled»، لا تناقض."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    saved = ["ether2", "ether3"]
    _set_state(app, saved)
    _set_readings(app, {"ether2": "searching", "ether3": "searching"})
    _login(client)
    markup = _markup(_page(client))
    assert 'data-pss-state-banner="enabled"' in markup
    assert 'data-pss-state-banner="partial"' not in markup
    assert 'class="pss-iface-chip pss-iface-chip--warn"' not in markup
    assert "data-pss-loop-mismatch" not in markup


# ════════════ BUG 1 — «تركيب» يُعطي توستًا ويُحدّث الصفّ ════════════
def test_install_button_and_handler_wired(app, client, monkeypatch):
    """زرّ «تركيب» موجود للمنفذ غير المركّب، والجدول موصول بنقطة apply-port،
    وخليّة القاعدة تحمل data-pss-installed لتحديثها في مكانها."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _set_state(app, ["ether2"])
    _set_readings(app, {"ether2": "no-rule"})
    _login(client)
    html = _page(client)
    assert 'data-pss-port-action="apply" data-pss-port="ether2"' in html
    assert "/port-services/loop_detect/apply-port" in html
    assert 'data-pss-rule-cell' in html and 'data-pss-installed="false"' in html


def test_table_handler_shows_toast_never_silent(app, client, monkeypatch):
    """المعالج يَستدعي توست النجاح («تم التركيب على …») وتوست الفشل، ويُحدّث
    الصفّ (setRowInstalled) ويُصالح البانر (reconcileBanner) — لا no-op صامت."""
    _seed(app)
    from app.radius.routes import port_script_services as route
    monkeypatch.setattr(route, "_discover", lambda nas: [])
    _set_state(app, ["ether2"])
    _set_readings(app, {"ether2": "no-rule"})
    import json
    _login(client)
    html = _page(client)
    # نصوص JS تُمرَّر عبر |tojson فتُهرَّب العربية إلى \uXXXX — نطابق الصورة
    # المُهرَّبة (لا الحرفيّة) لإثبات وجود توست النجاح والفشل في المعالج.
    assert json.dumps("تم التركيب على ", ensure_ascii=True)[1:-1] in html
    assert json.dumps("تعذّر تنفيذ العملية على الراوتر.", ensure_ascii=True)[1:-1] in html
    # المعالج يَستدعي توست + يُحدّث الصفّ + يُصالح البانر (لا no-op صامت)
    assert "uiToast(" in html
    assert "setRowInstalled(table, port, true)" in html
    assert "reconcileBanner(table)" in html


def test_apply_port_backend_installs_single_port(app, client, monkeypatch):
    """تكامل الخلفية: نداء apply-port يَبني الخطّة ويَدفع للراوتر ويُعيد ok،
    ويُضيف المنفذ للحالة المحفوظة (إثبات أن «تركيب» يُركّب فعلًا)."""
    import dataclasses
    _seed(app)
    from app.radius.routes import port_script_services as route
    from app.radius.services import port_script_services as fresh
    # ثبّت سكربت حقيقي (غير placeholder) لـloop_detect
    base = fresh.get_service("loop_detect")
    monkeypatch.setitem(fresh.REGISTRY, "loop_detect", dataclasses.replace(
        base, script_template="# {{PORTS}}\n{{IFACES}}\n",
        iface_line_template="/ip dhcp-client add interface={{IFACE}}",
        remove_template="# {{PORTS}}\n{{IFACES}}\n",
        remove_iface_line_template="/ip dhcp-client remove {{IFACE}}",
        is_placeholder=False))
    monkeypatch.setattr(route, "_discover", lambda nas: [
        {"name": "ether2", "running": True, "type": "ether"}])

    class _Fake:
        def __init__(self): self.calls = []
        def connect(self): pass
        def close(self): pass
        def run(self, path, attrs=None): self.calls.append(path); return []
    monkeypatch.setattr(route, "_connect_client", lambda nas: _Fake())
    _login(client)
    tok = None
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as s:
        tok = s["_csrf_token"]
    res = client.post(
        "/admin/radius/mt/1/port-services/loop_detect/apply-port",
        data={"_csrf_token": tok, "port": "ether2", "mode": "apply"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True and data["port"] == "ether2"
    from app.radius.routes.port_script_services import _get_state
    with app.app_context(), app.test_request_context():
        assert "ether2" in _get_state(1, "loop_detect")["ports"]
