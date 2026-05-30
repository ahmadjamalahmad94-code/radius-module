from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "admin_bridge_sidebar.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "admin-bridge-sidebar-token")
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", "false")
    monkeypatch.setenv("HOBERADIUS_ADMIN_SHARED_SECRET", "super-secret-test-value")
    monkeypatch.setenv("HOBERADIUS_LICENSE_KEY", "license-secret-test-value")
    reset_for_tests(db_file)
    from app import create_app

    return create_app()


def _auth_session(client):
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "bridge_sidebar"
        sess["admin_name"] = "Bridge Sidebar"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "bridge-sidebar-csrf"


def _sidebar(html: str) -> str:
    start = html.index('<aside class="hb-side"')
    end = html.index("</aside>", start)
    return html[start:end]


def test_admin_bridge_sidebar_contains_safe_html_page_only(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/admin-bridge").get_data(as_text=True)

    sidebar = _sidebar(html)
    assert "جسر الإدارة" in sidebar
    assert "/admin/radius/admin-bridge" in sidebar
    assert "/admin/radius/license-file" in sidebar
    assert "/api/v1/system/admin-bridge" not in sidebar
    for forbidden in ("server-peer/apply", "server-peer/rollback", "restore-apply", "/api/v1/", "upload-latest"):
        assert forbidden not in sidebar


def test_admin_bridge_index_returns_html_with_dry_run_safety_labels(app):
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/admin-bridge")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert "وضع جاف" in html
    assert "غير مفعل إنتاجيًا" in html
    assert "يحتاج تأكيد عقود الإدارة" in html
    assert "super-secret-test-value" not in html
    assert "license-secret-test-value" not in html


def test_admin_bridge_index_does_not_expose_live_action_links(app):
    with app.test_client() as client:
        _auth_session(client)
        html = client.get("/admin/radius/admin-bridge").get_data(as_text=True)

    forbidden = (
        "server-peer/apply",
        "server-peer/rollback",
        "/apply/",
        "/rollback/",
        "restore-apply",
        "upload-latest",
        "wg set",
        "systemctl restart",
    )
    for token in forbidden:
        assert token not in html


def test_license_file_page_masks_bridge_secrets(app):
    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/license-file")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/admin/radius/license-file/sync" in html
    assert "super-secret-test-value" not in html
    assert "license-secret-test-value" not in html


def test_license_file_translates_service_contract_keys(app):
    from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore, SNAPSHOT_CAPACITY

    with app.app_context():
        LicenseAdminSnapshotStore().save(
            tenant_id=1,
            snapshot_type=SNAPSHOT_CAPACITY,
            normalized_status="active",
            source_url="mock://runtime-contract",
            payload={
                "contract": {
                    "services": {
                        "cards": {"enabled": True, "status": "active"},
                        "cards_recharge": {"enabled": False, "status": "disabled"},
                        "customer_portal": {"enabled": True, "status": "active"},
                        "integration_bridge": {"enabled": True, "status": "active"},
                        "ip_change_vpn": {
                            "enabled": True,
                            "status": "active",
                            "download_mbps": 50,
                            "upload_mbps": 50,
                            "max_vpn_users": 100,
                        },
                    },
                    "limits": {
                        "subscribers": {"max_total": 250},
                        "nas": {"max_total": 3},
                    },
                }
            },
        )

    with app.test_client() as client:
        _auth_session(client)
        response = client.get("/admin/radius/license-file")
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "الكروت" in html
    assert "شحن الكروت" in html
    assert "بوابة العميل" in html
    assert "جسر الربط مع لوحة التراخيص" in html
    assert "خدمة تغيير عنوان الإنترنت / الشبكة الخاصة" in html
    assert "سرعة التحميل: 50 ميجابت/ثانية" in html
    assert "عدد المشتركين" in html
    assert "أجهزة الشبكة" in html
    for raw_key in ("cards_recharge", "customer_portal", "integration_bridge", "ip_change_vpn"):
        assert raw_key not in html


def test_license_file_can_save_customer_portal_bridge_values(app, monkeypatch):
    monkeypatch.delenv("HOBERADIUS_ADMIN_BRIDGE_ENABLED", raising=False)
    monkeypatch.delenv("HOBERADIUS_ADMIN_BASE_URL", raising=False)
    monkeypatch.delenv("HOBERADIUS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("INSTANCE_LICENSE_KEY", raising=False)
    monkeypatch.delenv("HOBERADIUS_ADMIN_SHARED_SECRET", raising=False)

    with app.test_client() as client:
        _auth_session(client)
        response = client.post("/admin/radius/license-file/config", data={
            "_csrf_token": "bridge-sidebar-csrf",
            "base_url": "https://hoberadius.com/",
            "license_key": "HBR-2026-AAAA-BBBB-CCCC",
            "shared_secret": "shared-secret-from-customer-page",
            "enabled": "1",
            "runtime_contract_sync": "1",
            "identity_sync_enabled": "1",
            "identity_sync_on_login": "1",
        }, follow_redirects=True)
        html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "HBR-2026-AAAA-BBBB-CCCC" not in html
    assert "shared-secret-from-customer-page" not in html
    assert "ربط الترخيص من صفحة العميل" in html

    from app.radius.db.repos import tenants_repo
    from app.radius.services.admin_panel_client import AdminBridgeConfig

    assert tenants_repo.get_setting(1, "license_admin_bridge.base_url") == "https://hoberadius.com"
    assert tenants_repo.get_setting(1, "license_admin_bridge.license_key") == "HBR-2026-AAAA-BBBB-CCCC"
    assert tenants_repo.get_setting(1, "license_admin_bridge.shared_secret") == "shared-secret-from-customer-page"
    config = AdminBridgeConfig.from_env()
    assert config.enabled is True
    assert config.base_url == "https://hoberadius.com"
    assert config.license_key == "HBR-2026-AAAA-BBBB-CCCC"
