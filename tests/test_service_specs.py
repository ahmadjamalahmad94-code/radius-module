"""اختبارات نظام مواصفات الخدمات الموحّد.

تُغطّي ثلاث طبقات:
  1) module `service_specs` — المخطّط ودالّة validate_spec.
  2) endpoint /service-requests/schema/<type> + /service-requests
     POST مع تحقّق + persistence + audit.
  3) templates — مفاتيح data-* تُضخّ في الصفحات (mt_dashboard،
     port_script_services، plans_list)، وincluded partial يُضمَّن.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_svc_specs_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
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
    u = f"ssm_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="ssm-pass",
        full_name="SSM Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "ssm-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed_router(app, *, nas_id: int) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """
                INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode)
                VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot', 1,
                        ?, 'direct')
                """,
                (nas_id, f"ssm-rtr-{nas_id}", f"203.0.113.{nas_id}", now),
            )


# ═══════════════════════════════════════════════════════════════
# (1) module `service_specs` — وحدة المخطّط
# ═══════════════════════════════════════════════════════════════


def test_specs_module_registers_all_kinds():
    from app.radius.services import service_specs as ss
    keys = {k.key for k in ss.list_kinds()}
    # الأنواع الخمسة المُعلَنة: bandwidth_plan / tunnel / port_script /
    # quota / site_policy.
    assert keys == {"bandwidth_plan", "tunnel", "port_script",
                    "quota", "site_policy"}


def test_specs_service_type_map_covers_known_services():
    """كل خدمة معروفة في الواجهة لها نوع مواصفات مرتبط."""
    from app.radius.services.service_specs import SERVICE_TYPE_MAP
    must_have = {
        "bt_wifi_block", "loop_detect",
        "hotspot", "broadband",
        "public-ip", "remote-access",
        "block-sites", "open-sites",
    }
    missing = must_have - set(SERVICE_TYPE_MAP)
    assert not missing, f"missing service_type entries: {missing}"


def test_validate_spec_accepts_clean_payload():
    """نوع quota (public-ip) — quota_mb مطلوب، validity_days اختياري
    افتراضي 30، notes اختياري. التحقّق يُرجع spec نظيف بلا أخطاء."""
    from app.radius.services.service_specs import validate_spec
    clean, errs = validate_spec(
        "public-ip", {"quota_mb": 2048, "validity_days": 60,
                     "notes": "أحتاج كوتا للزبون X"})
    assert not errs
    assert clean["quota_mb"] == 2048
    assert clean["validity_days"] == 60
    assert clean["notes"] == "أحتاج كوتا للزبون X"


def test_validate_spec_required_missing_returns_arabic_error():
    """quota_mb مطلوب — تركه فارغًا يُخرج رسالة عربيّة بالاسم."""
    from app.radius.services.service_specs import validate_spec
    clean, errs = validate_spec("public-ip", {"validity_days": 30})
    assert errs and "الكمّيّة المطلوبة" in errs[0]
    assert "quota_mb" not in clean


def test_validate_spec_number_out_of_range():
    """download_mbps لها min=1 max=10000 — قيمة سالبة تُرفَض."""
    from app.radius.services.service_specs import validate_spec
    _, errs = validate_spec("hotspot", {
        "download_mbps": -5, "upload_mbps": 5, "validity_days": 30,
    })
    assert errs
    assert any("سرعة التنزيل" in e for e in errs)


def test_validate_spec_select_rejects_unknown_option():
    """site_policy.scope قائمة مغلقة — قيمة خارجها تُرفَض."""
    from app.radius.services.service_specs import validate_spec
    _, errs = validate_spec("block-sites", {
        "sites": "example.com", "scope": "invalid_value",
    })
    assert any("scope" in e or "نطاق التطبيق" in e for e in errs)


def test_validate_spec_unknown_service_type():
    from app.radius.services.service_specs import validate_spec
    _, errs = validate_spec("not_a_real_service", {"x": 1})
    assert errs and "غير معروف" in errs[0]


def test_validate_spec_strips_unknown_keys():
    """مفاتيح غير معرّفة في المخطّط لا تَنتقل لـclean (حماية أساسيّة)."""
    from app.radius.services.service_specs import validate_spec
    clean, _ = validate_spec("public-ip", {
        "quota_mb": 1024, "evil_key": "<script>",
    })
    assert "evil_key" not in clean


def test_validate_spec_checkbox_coercion():
    """JS قد يُرسل قيمًا مختلفة لـcheckbox — كلّها تُحوَّل لـbool."""
    from app.radius.services.service_specs import validate_spec
    for raw in (True, "true", "on", "1", 1, "yes"):
        clean, _ = validate_spec("tunnel", {
            "ports": "443", "protocol": "tcp", "static_ip": raw,
        })
        assert clean["static_ip"] is True, raw
    for raw in (False, "false", "0", 0, "", None, "no"):
        clean, _ = validate_spec("tunnel", {
            "ports": "443", "protocol": "tcp", "static_ip": raw,
        })
        assert clean["static_ip"] is False, raw


# ═══════════════════════════════════════════════════════════════
# (2) endpoints — schema + persistence + audit
# ═══════════════════════════════════════════════════════════════


def test_schema_endpoint_returns_kind_and_fields(app, client):
    _login(client)
    res = client.get("/admin/radius/service-requests/schema/public-ip")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["service_label"] == "تغيير IP الخروج"
    assert data["kind"]["key"] == "quota"
    fields = data["kind"]["fields"]
    keys = [f["key"] for f in fields]
    assert "quota_mb" in keys and "notes" in keys


def test_schema_endpoint_404_for_unknown_service(app, client):
    _login(client)
    res = client.get("/admin/radius/service-requests/schema/nope")
    assert res.status_code == 404


def test_schema_endpoint_for_bandwidth_plan(app, client):
    """نوع bandwidth_plan — أربع حقول رئيسيّة + ملاحظات."""
    _login(client)
    res = client.get("/admin/radius/service-requests/schema/hotspot")
    assert res.status_code == 200
    data = res.get_json()
    keys = [f["key"] for f in data["kind"]["fields"]]
    assert {"download_mbps", "upload_mbps", "quota_gb",
            "validity_days", "notes"} <= set(keys)


def test_create_request_persists_spec_and_writes_audit(app, client):
    """ACTIVATE quota service — يُحفَظ كاملاً في tenant_settings ويُسجَّل
    حدث service_request.create."""
    _seed_router(app, nas_id=21)
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={
            "service_type": "public-ip", "action": "activate",
            "scope": "nas:21",
            "spec": {"quota_mb": 5120, "validity_days": 60,
                     "notes": "تجريبي"},
        },
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["ok"] is True
    assert body["service_label"] == "تغيير IP الخروج"
    assert body["action"] == "activate"

    with app.app_context():
        from app.radius.db.connection import db
        from app.radius.db.repos import audit_repo
        import json as _json
        rows = db().execute(
            "SELECT key, value FROM tenant_settings "
            "WHERE tenant_id=1 AND key LIKE ?",
            ("service_requests.public-ip.nas:21.%",),
        ).fetchall()
        assert rows, "spec request not persisted"
        payload = _json.loads(dict(rows[0])["value"])
        assert payload["service_type"] == "public-ip"
        assert payload["action"] == "activate"
        assert payload["scope"] == "nas:21"
        assert payload["spec"]["quota_mb"] == 5120
        assert payload["spec"]["validity_days"] == 60
        assert payload["status"] == "pending"
        events = audit_repo.recent(1, limit=20)
        assert "service_request.create" in [e["action"] for e in events]


def test_create_request_upgrade_action(app, client):
    """UPGRADE bandwidth_plan — تَمرّ نفس النقطة بنوع وعملية مختلفَين."""
    _seed_router(app, nas_id=22)
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={
            "service_type": "hotspot", "action": "upgrade",
            "scope": "nas:22",
            "spec": {
                "download_mbps": 50, "upload_mbps": 10,
                "quota_gb": 200, "validity_days": 30,
                "notes": "ترقية للزبون",
            },
        },
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 200
    body = res.get_json()
    assert body["action"] == "upgrade"
    assert body["service_label"] == "خطّة هوت سبوت"

    with app.app_context():
        from app.radius.db.connection import db
        import json as _json
        rows = db().execute(
            "SELECT value FROM tenant_settings "
            "WHERE tenant_id=1 AND key LIKE ?",
            ("service_requests.hotspot.nas:22.%",),
        ).fetchall()
        assert rows
        spec = _json.loads(dict(rows[0])["value"])["spec"]
        assert spec["download_mbps"] == 50
        assert spec["upload_mbps"] == 10
        assert spec["quota_gb"] == 200


def test_create_request_rejects_bad_action(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={"service_type": "public-ip", "action": "DROP",
              "spec": {"quota_mb": 100}},
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 400


def test_create_request_rejects_bad_spec(app, client):
    """quota_mb=0 خارج النطاق — يَرفض الخادم بلا تخزين."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={"service_type": "public-ip", "action": "activate",
              "spec": {"quota_mb": 0}},
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 400
    data = res.get_json()
    assert data["ok"] is False
    assert "errors" in data or "error" in data


def test_create_request_rejects_unknown_service(app, client):
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={"service_type": "not_a_real_service", "action": "activate",
              "spec": {}},
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 400


def test_list_endpoint_returns_persisted_items(app, client):
    """GET قائمة الطلبات للوحة المالك."""
    _seed_router(app, nas_id=23)
    _login(client)
    token = _csrf(client)
    client.post(
        "/admin/radius/service-requests",
        json={"service_type": "broadband", "action": "upgrade",
              "scope": "nas:23",
              "spec": {"download_mbps": 100, "upload_mbps": 20,
                       "validity_days": 30}},
        headers={"X-CSRFToken": token},
    )
    res = client.get("/admin/radius/service-requests")
    assert res.status_code == 200
    body = res.get_json()
    assert body["ok"] is True
    items = body["items"]
    assert items, "list endpoint returned no items"
    assert items[0]["service_type"] == "broadband"
    assert items[0]["action"] == "upgrade"


# ═══════════════════════════════════════════════════════════════
# (3) templates — modal wired into the actual pages
# ═══════════════════════════════════════════════════════════════


def test_dashboard_includes_spec_modal_partial(app, client):
    _seed_router(app, nas_id=31)
    _login(client)
    res = client.get("/admin/radius/mt/31/dashboard")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # partial markup is present (single shared modal).
    assert "data-ssm-modal" in html
    assert "data-ssm-spec" in html
    # public-ip card opens the unified modal in activate mode.
    assert 'data-svc-type="public-ip"' in html
    assert 'data-svc-action="activate"' in html
    # bt_wifi_block / loop_detect cards each show an upgrade pill.
    assert 'data-svc-type="bt_wifi_block"' in html
    assert 'data-svc-type="loop_detect"' in html
    # hotspot + broadband upgrade pills present.
    assert 'data-svc-type="hotspot"' in html
    assert 'data-svc-type="broadband"' in html


def test_port_script_services_page_has_upgrade_button(app, client):
    _seed_router(app, nas_id=32)
    _login(client)
    res = client.get(
        "/admin/radius/mt/32/port-services?slug=bt_wifi_block")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-ssm-modal" in html
    assert 'data-svc-type="bt_wifi_block"' in html
    # zero saved state ⇒ default action is activate.
    assert 'data-svc-action="activate"' in html


def test_plans_list_includes_upgrade_button_per_plan(app, client):
    """plans_list.html — كل خطّة تحمل زرّ ترقية بـscope=plan:<id>."""
    _login(client)
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            c.execute(
                "INSERT INTO access_plans (tenant_id, name, plan_type, "
                "  service_type, created_at) "
                "VALUES (1, 'PLN-A', 'time', 'Hotspot', ?)",
                (datetime.utcnow().isoformat() + "Z",),
            )
    res = client.get("/admin/radius/plans")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-ssm-modal" in html
    assert 'data-svc-type="bandwidth_plan"' in html
    assert 'data-svc-action="upgrade"' in html
    assert 'data-svc-scope="plan:' in html
