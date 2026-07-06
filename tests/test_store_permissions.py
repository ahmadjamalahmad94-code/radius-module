"""المتجر الإلكترونيّ — مجموعة صلاحيّات مستقلّة store.* (2026-07).

يُثبت:
  * الكتالوج يحوي المفاتيح السبعة الجديدة (تظهر في صفحة الأدوار تلقائيًّا).
  * الحارس المركزيّ يربط كلّ مسار كتابة بمفتاحه الدقيق، والعرض بـstore.view.
  * إصلاح التسريب: مدير عنده cards.view **فقط** يُمنع (403) من صفحة المتجر،
    ومن عنده store.view يمرّ.
  * الحذف الناعم/الاستعادة على مستوى الخدمة (status=archived ↔ active).
  * ترحيل 156 يمنح store.* لمن كان يملك cards.recharge/store.review.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]

NEW_KEYS = [
    "store.view", "store.package_add", "store.user_add", "store.user_edit",
    "store.user_recharge", "store.user_purchase", "store.user_delete",
]


# ─────────────────────────── source-level (no app) ───────────────────────────

def test_new_store_perms_in_catalog():
    from app.radius.core.constants import ALL_PERMISSIONS
    for key in NEW_KEYS:
        assert key in ALL_PERMISSIONS, f"{key} مفقود من ALL_PERMISSIONS"
    # القديم يبقى
    assert "store.review" in ALL_PERMISSIONS


def test_perm_guarded_write_mapping():
    from app.radius.routes.blueprint import _PERM_GUARDED
    expected = {
        "card_marketplace_package_create": "store.package_add",
        "card_marketplace_package_mode": "store.package_add",
        "card_marketplace_inventory_upload": "store.package_add",
        "card_marketplace_default_mode": "store.package_add",
        "card_users_create": "store.user_add",
        "card_user_password": "store.user_edit",
        "card_user_recharge": "store.user_recharge",
        "card_user_purchase": "store.user_purchase",
        "card_user_delete": "store.user_delete",
        "card_user_restore": "store.user_delete",
        # كان بلا حارس قبل اليوم:
        "store_support_chat_status": "store.review",
    }
    for ep, perm in expected.items():
        assert _PERM_GUARDED.get(ep) == perm, f"{ep} يجب أن يُربط بـ{perm}"
    # لم تعُد أيّ نقطة متجر مربوطة بـcards.recharge المستعار:
    for ep in expected:
        assert _PERM_GUARDED.get(ep) != "cards.recharge"


def test_nav_perm_view_moved_to_store_view():
    """إصلاح التسريب: صفحات عرض المتجر تُحرَس بـstore.view لا cards.view."""
    from app.radius.auth.ui_permissions import _NAV_PERM
    for ep in ("card_marketplace", "card_users_list",
               "card_user_360", "card_marketplace_package_file"):
        assert _NAV_PERM.get(ep) == "store.view", f"{ep} يجب أن يُحرَس بـstore.view"


def test_perm_labels_present():
    html = (ROOT / "app/templates/radius/_perm_labels.html").read_text(encoding="utf-8")
    for key in NEW_KEYS:
        assert f"'{key}'" in html, f"لا تسمية لـ{key} في _perm_labels.html"


def test_backfill_migration_present():
    mig = ROOT / "app/radius/db/migrations/156_store_permissions_backfill.sql"
    assert mig.exists(), "ترحيل 156 مفقود"
    sql = mig.read_text(encoding="utf-8")
    # store.view يُشتق من cards.recharge أو store.review
    assert "store.view" in sql and "store.review" in sql and "cards.recharge" in sql
    # idempotent + لا يمسّ super_admin
    assert "json_insert" in sql and "super_admin" in sql
    for key in NEW_KEYS:
        assert key in sql


# ─────────────────────────── integration (app + DB) ───────────────────────────

@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_storeperm_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    # تجاوز حارس دورة حياة الترخيص (NEVER_ACTIVATED يحجب اللوحة في NO_SEED)
    # كي يكون حارس الصلاحيّات هو ما يقرّر 403/200 هنا لا حارس الترخيص.
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


def _login_with_perms(client, perms: tuple[str, ...]):
    """مدير غير-سوبر مربوط بدور بصلاحيّات محدّدة، مع مالك رئيسيّ زائف أوّلًا
    كي لا يُحسب المُختبَر مالكًا رئيسيًّا (فيتجاوز RBAC)."""
    from app.radius.db.repos import admins_repo
    if admins_repo.primary_admin_id() is None:
        admins_repo.create_admin(username=f"owner_{uuid4().hex[:8]}",
                                 password="owner-pass", full_name="Owner",
                                 is_super_admin=True)
    role = admins_repo.create_role(name=f"r_{uuid4().hex[:6]}",
                                   display_name="Scoped", permissions=tuple(perms))
    u = f"m_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="p", full_name="T",
                             is_super_admin=False, role_id=role.id)
    res = client.post("/admin/radius/login", data={"username": u, "password": "p"})
    assert res.status_code in {302, 303}


def test_cards_view_only_is_blocked_from_marketplace(app, client):
    """التسريب المُصلَح: عرض البطاقات وحده لا يفتح المتجر."""
    _login_with_perms(client, ("cards.view",))
    res = client.get("/admin/radius/card-marketplace", follow_redirects=False)
    assert res.status_code == 403
    res2 = client.get("/admin/radius/card-users", follow_redirects=False)
    assert res2.status_code == 403


def test_store_view_reaches_marketplace(app, client):
    _login_with_perms(client, ("store.view",))
    res = client.get("/admin/radius/card-marketplace", follow_redirects=False)
    assert res.status_code == 200, "store.view يجب أن يفتح المتجر"
    res2 = client.get("/admin/radius/card-users", follow_redirects=False)
    assert res2.status_code == 200


# ملاحظة: مسارات الكتابة (إنشاء/حذف مستخدم متجر) تمرّ عبر طبقتين مستقلّتين
# للمدير غير-السوبر: (1) صلاحيّة الدور _PERM_GUARDED store.*، و(2) بوّابة
# منح المدير الدقيقة manager_grants (storeuser.*، افتراضها OFF). كلتاهما
# يجب أن تسمح. لذا اختبار الكتابة عبر HTTP يتشابك مع الطبقة الثانية —
# نُثبت طبقة الدور مصدريًّا (test_perm_guarded_write_mapping) واتساقها مع
# طبقة المنح أدناه، ونُبقي اختبارات HTTP على مسار العرض (النظيف) فقط.

def test_manager_grants_store_delete_action_consistency():
    """الحذف/الاستعادة مضافان لسجلّ أفعال منح المدير (اتساق مع storeuser.*)."""
    from app.radius.services.manager_grants import (
        ACTION_REGISTRY, endpoint_action)
    assert "storeuser.delete" in ACTION_REGISTRY
    assert endpoint_action("card_user_delete") == "storeuser.delete"
    assert endpoint_action("card_user_restore") == "storeuser.delete"


# ─────────────────────────── service: soft delete/restore ───────────────────────────

def test_soft_delete_and_restore(app):
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService, CardMarketplaceError)
    with app.app_context():
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        svc = CardUsersMarketplaceService(tenant_id=1)
        u = svc.create_card_user(display_name="Del Me", mobile="0595555555")
        uid = int(u["id"])
        assert svc.get_card_user(uid)["status"] == "active"
        # حذف ناعم → archived (البيانات باقية)
        svc.set_card_user_status(card_user_id=uid, status="archived", actor="tester")
        assert svc.get_card_user(uid)["status"] == "archived"
        # استعادة → active
        svc.set_card_user_status(card_user_id=uid, status="active", actor="tester")
        assert svc.get_card_user(uid)["status"] == "active"
        # حالة غير صالحة تُرفض
        with pytest.raises(CardMarketplaceError):
            svc.set_card_user_status(card_user_id=uid, status="bogus", actor="tester")


def test_restore_blocked_when_mobile_taken(app):
    from app.radius.db.repos import admins_repo, tenants_repo
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService, CardMarketplaceError)
    with app.app_context():
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        svc = CardUsersMarketplaceService(tenant_id=1)
        a = svc.create_card_user(display_name="First A", mobile="0596666666")
        aid = int(a["id"])
        # احذف A فيتحرّر رقمه، ثم سجّل B بنفس الرقم
        svc.set_card_user_status(card_user_id=aid, status="archived", actor="t")
        svc.create_card_user(display_name="Second B", mobile="0596666666")
        # استعادة A تفشل — الرقم يستخدمه حساب نشط الآن
        with pytest.raises(CardMarketplaceError):
            svc.set_card_user_status(card_user_id=aid, status="active", actor="t")
