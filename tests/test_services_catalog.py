"""اختبارات كتالوج الخدمات الموحّد + تعميم نافذة المواصفات.

تُغطّي:
  1) module `service_specs.catalog()` — القائمة المدفوعة بالبيانات،
     كل خدمة تَربط نوع مواصفات صالح، أعلام «مدفوعة»، إدراج السابقة
     (ip_change) والأنواع التي كانت بلا واجهة (tunnel / site_policy).
  2) صفحة /services-catalog — تُصيّر بطاقة لكل خدمة، وكل زرّ
     تفعيل/ترقية يَفتح النافذة الموحّدة (data-svc-spec-modal-open)؛
     لا زرّ «تفعيل» أعمى داخل الشبكة؛ النافذة مُضمَّنة مرّة واحدة.
  3) التدفّق الكامل عبر النافذة الموحّدة لخدمة كانت بلا مدخل
     (block-sites / remote-access) — المواصفات تَصل الطلب وتُحفَظ.
  4) خدمة «تغيير الـIP» (السابقة) ما تزال تعمل عبر نفس المسار.

تُشغَّل وحدها (عزل لكل ملف) — راجع memory test-isolation-per-file.
"""
from __future__ import annotations

import json as _json
import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_svc_catalog_")
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
    u = f"cat_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="cat-pass",
        full_name="Catalog Tester", is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "cat-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


# ═══════════════════════════════════════════════════════════════
# (1) module catalog()
# ═══════════════════════════════════════════════════════════════


def test_catalog_returns_data_driven_entries():
    """كل عنصر يَحمل المفاتيح المطلوبة للعرض، ونوع مواصفات صالح."""
    from app.radius.services import service_specs as ss
    cat = ss.catalog()
    assert isinstance(cat, list) and cat, "catalog must be non-empty"
    kind_keys = {k.key for k in ss.list_kinds()}
    seen = set()
    for svc in cat:
        for key in ("service_type", "label", "icon", "paid",
                    "kind_key", "kind_title", "summary", "field_count"):
            assert key in svc, f"missing {key} in {svc}"
        # كل خدمة تَربط نوع مواصفات مُسجَّل فعلًا (لا أيتام).
        assert svc["kind_key"] in kind_keys, svc["service_type"]
        # نوع المواصفات يَحمل حقولًا (عدد إيجابيّ).
        assert svc["field_count"] >= 1
        # لا تكرار في service_type المعروض (إزالة الأسماء البديلة).
        assert svc["service_type"] not in seen
        seen.add(svc["service_type"])


def test_catalog_includes_precedent_and_previously_orphan_types():
    """يَشمل السابقة (ip_change) + أنواعًا لم يكن لها مدخل واجهة سابقًا
    (tunnel عبر remote-access، site_policy عبر block-sites)."""
    from app.radius.services import service_specs as ss
    by_type = {s["service_type"]: s for s in ss.catalog()}
    assert "ip_change" in by_type
    assert by_type["ip_change"]["kind_key"] == "ip_change"
    # remote-access (tunnel) و block-sites (site_policy) صارا ظاهرَين.
    assert by_type.get("remote-access", {}).get("kind_key") == "tunnel"
    assert by_type.get("block-sites", {}).get("kind_key") == "site_policy"


def test_catalog_marks_paid_services():
    """الخدمات المدفوعة (تغيير الـIP/سقف عمومي/الوصول البعيد) مُعلَّمة."""
    from app.radius.services import service_specs as ss
    by_type = {s["service_type"]: s for s in ss.catalog()}
    assert by_type["ip_change"]["paid"] is True
    assert by_type["public-ip"]["paid"] is True
    # خدمات السكربت على المنافذ ليست مدفوعة (طلب تشغيليّ لا فوترة).
    assert by_type["bt_wifi_block"]["paid"] is False


# ═══════════════════════════════════════════════════════════════
# (2) page /services-catalog
# ═══════════════════════════════════════════════════════════════


def test_catalog_page_renders_a_card_per_service(app, client):
    _login(client)
    res = client.get("/admin/radius/services-catalog")
    assert res.status_code == 200, res.get_data(as_text=True)
    html = res.get_data(as_text=True)
    from app.radius.services import service_specs as ss
    services = ss.catalog()
    # بطاقة لكل خدمة.
    assert html.count("data-svccat-card") == len(services)
    # كل خدمة لها مُشغّل نافذة بنوعها (لا اسم مفقود).
    for svc in services:
        assert f'data-svc-type="{svc["service_type"]}"' in html, svc


def test_catalog_page_every_action_opens_spec_modal_no_bare_button(app, client):
    """العقد الأساس: لا «تفعيل» أعمى — كل أزرار الفعل في الكتالوج
    تَحمل data-svc-spec-modal-open. عددها = خدمتان (تفعيل+ترقية) لكلّ
    خدمة، ويُساوي عدد مُشغّلات النافذة بالضبط."""
    _login(client)
    res = client.get("/admin/radius/services-catalog")
    html = res.get_data(as_text=True)
    from app.radius.services import service_specs as ss
    n = len(ss.catalog())
    # تفعيل + ترقية لكل خدمة (هذه السمات في الـmarkup فقط، لا في الـJS).
    assert html.count('data-svc-action="activate"') == n
    assert html.count('data-svc-action="upgrade"') == n
    # كل زرّ فعل يَحمل مُشغّل النافذة. (العدّ الخام ≥ 2n لأن مُتحكّم
    # النافذة يُشير للسِمة كنصّ مُحدِّد داخل <script> — لذا ≥ لا =.)
    assert html.count("data-svc-spec-modal-open") >= 2 * n
    # لا زرّ فعل بلا مُشغّل: صفّ أزرار واحد لكل بطاقة، وكلّه مُشغّلات.
    # (نُطابق سِمة العنصر `class="svccat-actions"` لا مُحدِّد CSS.)
    assert html.count('class="svccat-actions"') == n


def test_catalog_page_includes_unified_modal_once(app, client):
    _login(client)
    res = client.get("/admin/radius/services-catalog")
    html = res.get_data(as_text=True)
    # النافذة الموحّدة مُضمَّنة مرّة واحدة — نَعدّ هيكل الحوار الفريد
    # (id="ssm-title" يَرد في الـmarkup فقط، لا كنصّ مُحدِّد في الـJS).
    assert html.count('id="ssm-title"') == 1
    # ومُتحكّم النافذة محمَّل.
    assert "svcSpecModal" in html


# ═══════════════════════════════════════════════════════════════
# (3) per-type schema renders the right fields (data-driven)
# ═══════════════════════════════════════════════════════════════


def test_schema_renders_tunnel_fields_for_remote_access(app, client):
    """remote-access ⇒ نوع tunnel: المنافذ + البروتوكول + IP ثابت."""
    _login(client)
    res = client.get("/admin/radius/service-requests/schema/remote-access")
    assert res.status_code == 200
    keys = [f["key"] for f in res.get_json()["kind"]["fields"]]
    assert {"ports", "protocol", "static_ip"} <= set(keys)


def test_schema_renders_site_policy_fields_for_block_sites(app, client):
    """block-sites ⇒ نوع site_policy: المواقع + النطاق."""
    _login(client)
    res = client.get("/admin/radius/service-requests/schema/block-sites")
    assert res.status_code == 200
    keys = [f["key"] for f in res.get_json()["kind"]["fields"]]
    assert {"sites", "scope"} <= set(keys)


# ═══════════════════════════════════════════════════════════════
# (4) full submit through the unified modal carries specs
# ═══════════════════════════════════════════════════════════════


def test_activate_previously_orphan_service_carries_spec(app, client):
    """تفعيل block-sites (site_policy) عبر النافذة الموحّدة — المواصفات
    تَصل /service-requests وتُحفَظ كاملةً (تعميم تدفّق تغيير الـIP)."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={
            "service_type": "block-sites", "action": "activate",
            "scope": "",
            "spec": {
                "sites": "youtube.com\nfacebook.com",
                "scope": "hotspot",
                "match_subdomains": True,
                "notes": "حجب للزبون التجريبي",
            },
        },
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    assert res.get_json()["ok"] is True
    with app.app_context():
        from app.radius.db.connection import db
        rows = db().execute(
            "SELECT value FROM tenant_settings "
            "WHERE tenant_id=1 AND key LIKE ?",
            ("service_requests.block-sites.%",),
        ).fetchall()
        assert rows, "block-sites spec request not persisted"
        spec = _json.loads(dict(rows[0])["value"])["spec"]
        assert "youtube.com" in spec["sites"]
        assert spec["scope"] == "hotspot"
        assert spec["match_subdomains"] is True


def test_ip_change_precedent_still_works_through_unified_modal(app, client):
    """الخدمة السابقة (تغيير الـIP) تَمرّ بنفس المسار الموحّد وتُحفَظ
    سرعتها المطلوبة — لا انحدار في الـprecedent."""
    _login(client)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/service-requests",
        json={
            "service_type": "ip_change", "action": "activate",
            "scope": "",
            "spec": {
                "requested_speed_mbps": 200,
                "billing_cycle": "monthly",
                "data_limit": "unlimited",
            },
        },
        headers={"X-CSRFToken": token},
    )
    assert res.status_code == 200, res.get_data(as_text=True)
    body = res.get_json()
    assert body["ok"] is True
    assert body["service_label"] == "تغيير الـIP"
    with app.app_context():
        from app.radius.db.connection import db
        rows = db().execute(
            "SELECT value FROM tenant_settings "
            "WHERE tenant_id=1 AND key LIKE ?",
            ("service_requests.ip_change.%",),
        ).fetchall()
        assert rows
        spec = _json.loads(dict(rows[0])["value"])["spec"]
        assert spec["requested_speed_mbps"] == 200
