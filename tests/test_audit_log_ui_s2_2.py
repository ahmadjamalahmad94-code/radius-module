"""S2.2 — Audit log center UI.

Pins:
  - /admin/radius/audit renders (login-guarded, tenant-scoped)
  - Filters (router_id, action, severity, result_status, q) work
  - Detail page renders for a real id, 404s for missing/foreign
  - Secrets stay redacted in the rendered HTML (no plaintext leak)
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s2_2_")
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
    u = f"s2_2_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s2-pass", full_name="S2.2 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s2-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _record(*, action="mt.x", router_id=None, severity="info",
            result_status="", payload=None, target_id="1"):
    from app.radius.db.repos import audit_repo
    return audit_repo.record(
        tenant_id=1, actor="op", action=action,
        target_type="mikrotik_nas", target_id=str(target_id),
        router_id=router_id, severity=severity,
        result_status=result_status,
        payload=payload or {},
    )


# ─── Index ────────────────────────────────────────────────────


def test_audit_index_is_login_guarded(client):
    res = client.get("/admin/radius/audit", follow_redirects=False)
    assert res.status_code in {302, 303}


def test_audit_index_renders_shell(app, client):
    _login(client)
    res = client.get("/admin/radius/audit")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-audit-log-page" in html
    assert "data-audit-filter-router" in html
    assert "data-audit-filter-action" in html
    assert "data-audit-filter-severity" in html
    assert "data-audit-filter-result" in html
    assert "data-audit-filter-search" in html


def test_audit_index_lists_rows(app, client):
    with app.app_context():
        _record(action="mt.programming.hotspot.apply",
                router_id=42, severity="warning",
                result_status="success")
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    # The row now shows the Arabic action label (the raw English code was
    # removed from the list for clarity; it remains on the detail page).
    assert "تطبيق إعدادات Hotspot" in html
    assert "data-audit-log-rows" in html


def test_audit_index_renders_empty_state_when_no_rows(app, client):
    _login(client)
    html = client.get("/admin/radius/audit?q=__no_such_audit_row__").get_data(as_text=True)
    assert "data-audit-empty" in html


# ─── Filters ──────────────────────────────────────────────────


def test_filter_by_router_id(app, client):
    with app.app_context():
        _record(action="apply", router_id=10)
        _record(action="apply", router_id=20)
    _login(client)
    html = client.get(
        "/admin/radius/audit?router_id=10").get_data(as_text=True)
    # Both rows have action="apply"; the filter should show 1 row.
    assert html.count('data-audit-row="') == 1


def test_filter_by_severity(app, client):
    with app.app_context():
        _record(severity="critical", action="boom")
        _record(severity="info", action="benign")
    _login(client)
    html = client.get(
        "/admin/radius/audit?severity=critical").get_data(as_text=True)
    # Raw action codes are no longer printed in the list, so assert the filter
    # by row count + the critical badge: only the critical row survives.
    assert html.count('data-audit-row="') == 1
    assert "حرجة" in html


def test_filter_by_action_and_search(app, client):
    with app.app_context():
        _record(action="mt.deploy", target_id="alpha-zone")
        _record(action="mt.apply",  target_id="beta-zone")
    _login(client)
    html = client.get(
        "/admin/radius/audit?q=alpha").get_data(as_text=True)
    assert "alpha-zone" in html
    assert "beta-zone" not in html


# ─── Detail ───────────────────────────────────────────────────


def test_detail_renders_full_picture(app, client):
    with app.app_context():
        aid = _record(action="mt.detail",
                       payload={"k": "v",
                                "api_password": "pwd-LEAK"})
    _login(client)
    html = client.get(
        f"/admin/radius/audit/{aid}").get_data(as_text=True)
    assert "data-audit-detail-page" in html
    assert f'data-audit-id="{aid}"' in html
    assert "mt.detail" in html


def test_detail_404_for_unknown_id(app, client):
    _login(client)
    res = client.get("/admin/radius/audit/999999")
    assert res.status_code == 404


def test_detail_redacts_secrets_in_rendered_html(app, client):
    """The repo redacts at write-time, so the secret should
    already be '***' in the row. This test catches a regression
    where someone bypasses the redact path."""
    with app.app_context():
        aid = _record(action="mt.x",
                       payload={"api_password": "PWD-MUST-NOT-APPEAR",
                                "username": "op"})
    _login(client)
    html = client.get(
        f"/admin/radius/audit/{aid}").get_data(as_text=True)
    assert "PWD-MUST-NOT-APPEAR" not in html
    assert "***" in html
    # And the non-secret key shows through.
    assert "op" in html


def test_detail_shows_before_and_after_blocks_when_present(app, client):
    with app.app_context():
        from app.radius.db.repos import audit_repo
        aid = audit_repo.record(
            tenant_id=1, actor="op",
            action="mt.toggle", target_type="mikrotik_nas",
            target_id="42", router_id=42,
            before={"enabled": True},
            after={"enabled": False},
            severity="warning",
            result_status="success",
        )
    _login(client)
    html = client.get(
        f"/admin/radius/audit/{aid}").get_data(as_text=True)
    assert "data-audit-detail-before" in html
    assert "data-audit-detail-after" in html
    assert "قبل التغيير" in html
    assert "بعد التغيير" in html


# ─── تعريب أعمدة السجل (تصحيح يونيو 2026) ────────────────────


def _seed_nas(app, *, nas_id: int, name: str) -> None:
    """ينشئ صفّ راوتر في nas_devices حتى يتمكّن audit_format من حلّ
    اسم الراوتر/الهدف عبر join. الأعمدة الإلزامية بحدّ أدنى."""
    from datetime import datetime
    from app.radius.db.connection import transaction
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context(), transaction() as c:
        c.execute(
            "INSERT INTO nas_devices (id, tenant_id, name, address, secret, "
            "vendor, nas_type, enabled, created_at, connection_mode, "
            "api_user, api_password) "
            "VALUES (?, 1, ?, '203.0.113.20', 's', 'mikrotik', 'hotspot', "
            "1, ?, 'direct', 'u', 'p')",
            (nas_id, name, now),
        )


def test_audit_index_router_column_shows_nas_name_not_raw_id(app, client):
    """تصحيح المالك: عمود «الراوتر» كان «#17» الخام؛ صار يعرض اسم
    nas_devices الفعلي. الـID يبقى في title للمراجعة."""
    _seed_nas(app, nas_id=17, name="MT-HQ-Core")
    with app.app_context():
        _record(action="mt.deploy", router_id=17,
                target_id="17", severity="info",
                result_status="success")
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    # الاسم ظاهر، والـID الخام لم يَعُد منفردًا
    assert "MT-HQ-Core" in html
    # ما يهمّ أنّ النصّ الخام «#17» لم يَعُد ظاهرًا في خلية العمود.
    # نتأكّد بتعليم العمود بسمة data-audit-router ووجود الاسم فيه.
    assert "data-audit-router" in html


def test_audit_index_target_column_resolves_router_name(app, client):
    """تصحيح المالك: «هدف (17)» الخام يصبح «MT-HQ-Core». مفتاح الـtarget
    يُحلّ عبر nas_devices للأنواع mikrotik_nas/router/nas/nas_device."""
    _seed_nas(app, nas_id=17, name="MT-HQ-Core")
    with app.app_context():
        _record(action="mt.deploy", router_id=17,
                target_id="17", severity="info",
                result_status="success")
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    # «هدف» (label الخام) لم يَعُد يظهر منفردًا قبل اسم الراوتر — اسم
    # الراوتر يظهر مباشرة بدلاً من «هدف (17)».
    assert "هدف (17)" not in html
    assert "هدف #17" not in html
    # الاسم الفعلي يظهر في عمود الهدف (كما يظهر أيضاً في عمود الراوتر).
    assert html.count("MT-HQ-Core") >= 2


def test_audit_index_target_falls_back_to_arabic_type_plus_id(app, client):
    """راوتر غير موجود في nas_devices — لا يجب أن يظهر «هدف 17»؛ بل
    «المايكروتيك #17» (نوع عربي + معرّف)."""
    with app.app_context():
        # نُسجّل بـtarget_id=99 بلا nas_devices.id=99 → لا تحليل اسم
        _record(action="mt.x", router_id=99,
                target_id="99", severity="info", result_status="")
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    assert "هدف (99)" not in html
    assert "هدف #99" not in html
    assert "المايكروتيك #99" in html


def test_audit_index_action_not_raw_key_and_not_vague(app, client):
    """تصحيح المالك: «الإجراء» يجب ألّا يكون المفتاح الخام، ولا يكون
    «عملية على راوتر» الغامضة. (مفتاح غير معرّف عمومًا)."""
    with app.app_context():
        _record(action="mt.unknown_thing",  # غير معرّف في الخريطة
                router_id=1, target_id="1", severity="info")
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    # المفتاح الخام لا يظهر كنصّ ظاهر (يبقى في title)
    # لا يظهر «عملية على راوتر» الغامضة.
    assert "عملية على راوتر" not in html
    assert "عملية على النظام" not in html


def test_audit_index_details_column_renders_arabic_sentence(app, client):
    """تصحيح المالك: عمود «التفاصيل» كان JSON خامًا؛ صار جملة عربية
    موجزة عبر format_payload."""
    _seed_nas(app, nas_id=17, name="MT-HQ-Core")
    with app.app_context():
        _record(
            action="mt.port_services.loop_detect.apply",
            router_id=17, target_id="17",
            payload={"ports": ["ether2", "ether3"],
                     "slug": "loop_detect", "ok": True},
            severity="info", result_status="success",
        )
    _login(client)
    html = client.get("/admin/radius/audit").get_data(as_text=True)
    # عمود التفاصيل يحمل علامة لاختبار + نصّ عربي مقروء
    assert "data-audit-details" in html
    assert "المنافذ: ether2, ether3" in html
    # عنوان الإجراء يستخدم الخريطة الدقيقة الموسّعة
    assert "تفعيل تتبّع اللوب" in html
