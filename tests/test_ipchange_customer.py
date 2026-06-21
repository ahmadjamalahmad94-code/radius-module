# -*- coding: utf-8 -*-
"""خدمة «تغيير الـIP» المدفوعة — جانب العميل (المرحلة 1).

يغطّي: تصيير الصفحة، إنشاء الطلب حاملًا السرعة+شهريّ+غير محدودة عبر مسار
الطلبات الموحّد، تحقّق المواصفات، بوابة منح المزوّد (غير مُفعَّلة → «طلب
تفعيل»، مُفعَّلة → قسم الحالة/التزويد، موقوفة → غير متاحة)، ورابط الشريط
الجانبي. شغّل الملف وحده."""
from __future__ import annotations

import os

import pytest

PATH = "/admin/radius/ip-change"
REQ_URL = "/admin/radius/service-requests"


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "ipc.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()
    return flask_app


def _auth(client):
    with client.session_transaction() as s:
        s.update(admin_id=1, admin_user="ipc_admin", admin_name="IPC Admin",
                 is_super_admin=True, tenant_id=1, _csrf_token="ipc-csrf")


def _grant_payload(monkeypatch, payload):
    """يُثبّت لقطة عقد المزوّد (يتجاوز قراءة قاعدة البيانات)."""
    from app.radius.services import provider_grant
    monkeypatch.setattr(provider_grant, "get_payload", lambda tenant_id: payload)


# ── (1) تصيير الصفحة ──
def test_page_renders_activation_form_when_not_granted(app):
    c = app.test_client()
    _auth(c)
    r = c.get(PATH)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "تغيير الـIP" in html
    assert 'class="ipc-activate"' in html          # نموذج التفعيل
    assert 'name="mbps"' in html                # حقل السرعة
    assert 'data-testid="ipc-price"' in html     # سعر الميغا
    assert 'data-testid="ipc-total"' in html     # الإجمالي المحسوب
    assert 'data-testid="ipc-provision"' not in html  # لا قسم تزويد بعد


# ── (2) إنشاء الطلب يحمل السرعة + شهريّ + غير محدودة ──
def test_request_created_with_speed_monthly_unlimited(app):
    c = app.test_client()
    _auth(c)
    res = c.post(REQ_URL, json={
        "service_type": "ip_change", "action": "activate",
        "spec": {"requested_speed_mbps": 100, "billing_cycle": "monthly",
                 "data_limit": "unlimited"},
    }, headers={"X-CSRFToken": "ipc-csrf"})
    assert res.status_code == 200, res.get_data(as_text=True)
    data = res.get_json()
    assert data["ok"] is True
    assert data["service_label"] == "تغيير الـIP"

    with app.app_context():
        from app.radius.services import ip_change_service as ipc
        reqs = ipc.list_requests(1)
        assert len(reqs) == 1
        spec = reqs[0]["spec"]
        assert spec["requested_speed_mbps"] == 100
        assert spec["billing_cycle"] == "monthly"
        assert spec["data_limit"] == "unlimited"
        assert reqs[0]["status"] == "pending"
        assert reqs[0]["service_type"] == "ip_change"


# ── (3) تحقّق المواصفات: السرعة مطلوبة ──
def test_spec_requires_speed(app):
    c = app.test_client()
    _auth(c)
    res = c.post(REQ_URL, json={
        "service_type": "ip_change", "action": "activate",
        "spec": {"billing_cycle": "monthly", "data_limit": "unlimited"},
    }, headers={"X-CSRFToken": "ipc-csrf"})
    assert res.status_code == 400
    assert res.get_json()["ok"] is False


def test_spec_kind_registered():
    from app.radius.services.service_specs import (
        kind_for_service, service_label, SERVICE_TYPE_MAP)
    kind = kind_for_service("ip_change")
    assert kind is not None
    keys = [f.key for f in kind.fields]
    assert "requested_speed_mbps" in keys
    assert "billing_cycle" in keys and "data_limit" in keys
    assert service_label("ip_change") == "تغيير الـIP"
    assert SERVICE_TYPE_MAP.get("ipchange") == "ip_change"


# ── (4) بوابة منح المزوّد ──
def test_gating_granted_shows_status_section(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": True, "status": "active"}}})
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    # مُفعَّلة بلا تزويد بعد → قسم بيانات الاتصال + placeholder الانتظار،
    # لا نموذج تفعيل، ولا قسم سكربت (يظهر فقط بعد وصول التزويد).
    assert 'data-testid="ipc-provision"' in html
    assert 'data-testid="ipc-awaiting"' in html
    assert 'class="ipc-activate"' not in html
    assert 'data-testid="ipc-oneclick"' not in html


def test_gating_disabled_shows_unavailable(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": False, "status": "disabled"}}})
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert "موقوفة" in html
    assert 'class="ipc-activate"' not in html


def test_gating_requires_upgrade_shows_activation(app, monkeypatch):
    # «مدفوعة-غير-مفعّلة» → تبقى الصفحة تَعرض «طلب تفعيل».
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": True, "status": "locked_upgrade"}}})
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert 'class="ipc-activate"' in html
    assert 'data-testid="ipc-provision"' not in html


# ── (5) السعر القابل للضبط (إعداد محلّي) ──
def test_price_per_mbps_from_local_setting(app):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.services import ip_change_service as ipc
        tenants_repo.set_setting(1, ipc.PRICE_SETTING_KEY, "2.5")
        assert ipc.price_per_mbps(1) == 2.5
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert "2.50" in html  # يظهر في شريط السعر


def test_price_from_provider_contract(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"price_per_mbps": 3}}})
    with app.app_context():
        from app.radius.services import ip_change_service as ipc
        assert ipc.price_per_mbps(1) == 3.0


# ── (6) رابط الشريط الجانبي (لا يتيم) ──
def test_sidebar_link_present(app):
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert PATH in html                 # رابط الصفحة في الشريط الجانبي
    assert "خدمات الاتصال" in html       # عنوان المجموعة الجديدة


# ══════════ المرحلة 4: عرض بيانات التزويد من جسر السحب ══════════
def _save_provisioning(app, **over):
    """يحقن لقطة تزويد (كما يُسلّمها المزوّد عبر الجسر)."""
    svc = {"status": "provisioned", "server_host": "vpn-7.hoberadius.net",
           "server_ip": "203.0.113.50", "sstp_username": "ipc_5521",
           "sstp_password": "Pw#Secret9", "speed_mbps": 100,
           "expires_at": "2026-07-21"}
    svc.update(over)
    with app.app_context():
        from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore
        from app.radius.services import ip_change_service as ipc
        LicenseAdminSnapshotStore().save(
            tenant_id=1, snapshot_type=ipc.SNAPSHOT_PROVISIONING,
            normalized_status="active", source_url="bridge",
            payload={"services": {"ip_change": svc}})


def test_provision_read_from_bridge(app):
    _save_provisioning(app)
    with app.app_context():
        from app.radius.services import ip_change_service as ipc
        p = ipc.provision(1)
        assert p and p["status"] == "provisioned"
        assert p["server_ip"] == "203.0.113.50"
        assert p["sstp_username"] == "ipc_5521"
        assert p["sstp_password"] == "Pw#Secret9"   # نجا من تعقيم الجسر
        assert p["speed_mbps"] == 100


def test_provision_encrypted_password(app):
    with app.app_context():
        from app.radius.services.vpn_account_service import _encrypt
        enc = _encrypt("Secret#Plain")
    _save_provisioning(app, sstp_password="", sstp_password_enc=enc)
    with app.app_context():
        from app.radius.services import ip_change_service as ipc
        p = ipc.provision(1)
        assert p and p["sstp_password"] == "Secret#Plain"


def test_page_shows_creds_when_granted_and_provisioned(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": True, "status": "active"}}})
    _save_provisioning(app)
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert 'data-testid="ipc-provision"' in html
    assert "203.0.113.50" in html and "ipc_5521" in html and "Pw#Secret9" in html
    assert 'data-testid="ipc-awaiting"' not in html
    # قسم سكربت «تغيير الـIP» متاح
    assert 'data-testid="ipc-oneclick"' in html and 'data-ipc-script' in html


# ══════════ المرحلة 3: نقطة توليد السكربت ══════════
def test_script_endpoint_requires_provision(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": True, "status": "active"}}})
    c = app.test_client()
    _auth(c)
    res = c.get("/admin/radius/ip-change/script")
    assert res.status_code == 409
    assert res.get_json()["ok"] is False


def test_script_endpoint_returns_apply_rollback(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": True, "status": "active"}}})
    _save_provisioning(app)
    c = app.test_client()
    _auth(c)
    data = c.get("/admin/radius/ip-change/script?exclude_video=0").get_json()
    assert data["ok"] is True
    assert "/interface sstp-client add" in data["apply_script"]
    assert 'gateway="hobe-ipchange-sstp"' in data["apply_script"]   # إعادة توجيه
    assert "sstp-client remove" in data["rollback_script"]          # تراجع
    # بلا استبعاد فيديو
    assert 'address-list add list="hobe-ipchange-video"' not in data["apply_script"]


def test_script_endpoint_video_toggle(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": True, "status": "active"}}})
    _save_provisioning(app)
    c = app.test_client()
    _auth(c)
    data = c.get("/admin/radius/ip-change/script?exclude_video=1").get_json()
    assert data["ok"] is True
    assert 'address-list add list="hobe-ipchange-video"' in data["apply_script"]
    assert "googlevideo.com" in data["apply_script"]
    assert "tiktokcdn.com" in data["apply_script"]


# ══════════ المرحلة 5: انتهاء الصلاحية + التراجع ══════════
def test_expired_shows_banner_not_disabled_empty(app, monkeypatch):
    _grant_payload(monkeypatch, {"services": {"ip_change": {"enabled": False, "status": "expired"}}})
    _save_provisioning(app)   # التزويد ما زال محفوظًا → التراجع متاح
    c = app.test_client()
    _auth(c)
    html = c.get(PATH).get_data(as_text=True)
    assert 'data-testid="ipc-expired"' in html          # لافتة الانتهاء
    assert "موقوفة" not in html                          # ليس مسار «موقوفة» العامّ
    assert 'data-ipc-script' in html                     # سكربت التراجع متاح
    with app.app_context():
        from app.radius.services import ip_change_service as ipc
        assert ipc.expiry_state(1)["expired"] is True
