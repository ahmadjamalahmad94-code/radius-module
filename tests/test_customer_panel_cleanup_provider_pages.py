"""chore/customer-panel-cleanup-1 — إخراج صفحات «المزوّد فقط» والطابور الخامل
من لوحة العميل.

يثبّت هذا الملف ثلاثة عقود:

  (A) حراسة المسار:
      • «المستأجرون» و«التحصيل» صارتا قدرتَين default-off (مفتاحا «tenants»
        و«finance_collection»): بلا منح المزوّد تُحجَبان عن **الجميع** —
        السوبر لا يَتجاوز — GET→إعادة توجيه للوحة، الكتابة→403.
      • «مختبر الدفع» يبقى super-only كلاسيكيًّا: العاديّ 403، السوبر يَصِله.

  (B) الشريط الجانبي: «التحصيل» و«المستأجرون» مخفيّان حتى عن السوبر ما لم
      يَمنحهما المزوّد (default-off). «مختبر الدفع» و«طابور المزامنة» مُزالان
      كليًّا (لا للسوبر أيضًا).

  (C) تنظيف الطابور: sync_queue_repo.mark_stale_resolved يحوّل الصفوف
      العالقة (failed/retrying) إلى done، وهو idempotent (الاستدعاء
      الثاني يُعيد 0)، ولا يلمس queued/syncing. والـmigration 133 يطابقه.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


# ───────────────────────── تهيئة تطبيق معزول ─────────────────────────
@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_cleanup1_")
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


def _make_admin(*, is_super_admin: bool, viewer: bool = False):
    from app.radius.db.repos import admins_repo
    role_id = None
    if viewer:
        r = admins_repo.get_role_by_name("viewer")
        role_id = r.id if r else None
    return admins_repo.create_admin(
        username=f"cl1_{uuid4().hex[:8]}",
        password="cl1-pass",
        full_name="Cleanup1 Tester",
        role_id=role_id,
        is_super_admin=is_super_admin,
    )


def _login(client, username: str):
    res = client.post(
        "/admin/radius/login",
        data={"username": username, "password": "cl1-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}, res.status_code
    return res


# مسارات «المزوّد فقط» المُطفأة افتراضيًّا (default-off): tenants + التحصيل.
# صارت قدرات لا يَمنحها إلا المزوّد؛ بلا منح تُحجَب عن **الجميع** (السوبر لا
# يَتجاوز): GET → إعادة توجيه للوحة، الكتابة → 403. (تحوّلت من super-only
# لأنّ المالك سوبر فكان لا يزال يَراها — راجع gate «sections».)
_DEFAULT_OFF_GET = {
    "tenants_list":   "/admin/radius/tenants",
    "tenants_new":    "/admin/radius/tenants/new",
    "tenants_edit":   "/admin/radius/tenants/1/edit",
    "collection_hub": "/admin/radius/finance/collection",
}

# مسار super-only كلاسيكيّ (لم يتغيّر): العاديّ 403، السوبر يَصِله.
_SUPER_ONLY_GET = {
    "payments_lab":   "/admin/radius/payments-lab",
}


# ───────────────────── (A) حراسة المسار ─────────────────────
@pytest.mark.parametrize("name,url", sorted(_SUPER_ONLY_GET.items()))
def test_normal_admin_403_on_super_only_pages(app, client, name, url):
    """مسؤول عاديّ (غير سوبر) — حتى بدور واسع — يُمنَع 403 على صفحة super-only."""
    _make_admin(is_super_admin=True)                       # المالك يَحجز id #1
    limited = _make_admin(is_super_admin=False, viewer=True)
    _login(client, limited.username)
    res = client.get(url, follow_redirects=False)
    assert res.status_code == 403, f"{name} ({url}) → {res.status_code} (متوقّع 403)"
    assert "Location" not in res.headers          # 403 صريح لا إعادة توجيه دخول


@pytest.mark.parametrize("name,url", sorted(_DEFAULT_OFF_GET.items()))
def test_default_off_pages_redirect_even_for_super(app, client, name, url):
    """قدرة default-off بلا منح: GET يُعاد توجيهه للوحة — **حتى للسوبر**
    (لا 200 ولا صفحة blocked). لا تَجاوز من السوبر."""
    owner = _make_admin(is_super_admin=True)               # سوبر/مالك
    _login(client, owner.username)
    res = client.get(url, follow_redirects=False)
    assert res.status_code in (302, 303), f"{name} ({url}) → {res.status_code}"
    loc = res.headers.get("Location", "")
    assert url not in loc                  # ليست الصفحة نفسها
    assert "/_provider/blocked" not in loc  # وليست صفحة الحجب
    assert "/login" not in loc              # وليست بوّابة الدخول


def test_default_off_write_403_even_for_super(app, client):
    """الكتابة على نقطة default-off بلا منح → 403 صريح حتى للسوبر."""
    owner = _make_admin(is_super_admin=True)
    _login(client, owner.username)
    client.get("/admin/radius/")                           # يولّد _csrf_token
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token") or ""
    res = client.post("/admin/radius/tenants",
                      data={"_csrf_token": token, "name": "X"},
                      follow_redirects=False)
    assert res.status_code == 403


def test_super_admin_reaches_classic_super_only_pages(app, client):
    """السوبر يَصِل صفحات super-only الكلاسيكيّة (payments_lab، غير محجوبة)."""
    owner = _make_admin(is_super_admin=True)
    _login(client, owner.username)
    # مختبر الدفع التجريبي مسجّل ومحروس super-only — السوبر يَصِله (غير محجوب).
    assert client.get("/admin/radius/payments-lab").status_code != 403


# ملاحظة: نقطة payments_lab_webhook (POST) محروسة أيضًا super_admin فقط في
# _PERM_GUARDED (دفاع بالعمق)، لكن اختبارها عبر الكدسة الكاملة يَمرّ بحارس
# CSRF (يُعيد التوجيه قبل حارس الصلاحية) فلا يُعزَل عقد الصلاحية بوضوح؛
# تغطية صفحة المختبر (GET) أعلاه تُثبت الآليّة ذاتها.


# ───────────────────── (B) غياب الروابط من الشريط الجانبي ─────────────────────
def _render_sidebar(app, *, session_overrides: dict):
    from flask import render_template, session
    from app.radius.core.tenant import DEFAULT_TENANT_ID
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(DEFAULT_TENANT_ID, "security.unauthorized_ui", "freeze")
    with app.test_request_context("/admin/radius/dashboard"):
        for k, v in session_overrides.items():
            session[k] = v
        return render_template("admin/_sidebar.html")


# روابط الصفحات المُزالة — كما تُرسَم في href.
_LINK_TENANTS    = "/admin/radius/tenants"
_LINK_COLLECTION = "/admin/radius/finance/collection"
_LINK_PAY_LAB    = "/admin/radius/payments-lab"
_LINK_SYNC       = "/admin/radius/sync"


def test_normal_admin_sidebar_omits_provider_links(app):
    """مسؤول عاديّ بصلاحيات واسعة — لكن لا روابط الصفحات المُزالة، ولا
    صفّ مجمّد لها (إخفاء كليّ لا تجميد)."""
    html = _render_sidebar(app, session_overrides={
        "admin_id": 2,
        "is_super_admin": False,
        "tenant_id": 1,
        # صلاحيات تَفتح أقسام «المال» و«الإدارة» كي نُثبت أنّ الغياب بسبب
        # حراسة super لا لأنّ القسم كلّه مخفيّ.
        "permissions": ["reports.finance", "admins.view", "nas.view",
                        "users.view", "settings.view"],
    })
    assert _LINK_COLLECTION not in html, "رابط التحصيل يجب أن يَغيب عن العاديّ"
    assert _LINK_PAY_LAB not in html, "رابط مختبر الدفع يجب أن يَغيب عن العاديّ"
    assert _LINK_SYNC not in html, "رابط طابور المزامنة يجب أن يَغيب عن العاديّ"
    # لا رابط مستأجرين (href ينتهي بـ/tenants تحديدًا).
    assert 'href="/admin/radius/tenants"' not in html, "رابط المستأجرين يجب أن يَغيب"
    # سلامة: القسمان المجاوران ما زالا يَظهران ببنود مسموحة (لا تكسير تنقّل).
    assert "/admin/radius/accounting" in html or "المركز المالي" in html
    assert "المال والتحصيل" in html and "الإدارة" in html


def test_super_admin_sidebar_hides_default_off_provider_links(app):
    """حتى السوبر لا يَرى «التحصيل» و«المستأجرون» ما لم يَمنحهما المزوّد
    (قدرتان default-off؛ بلا منح في هذه اللقطة فهما مخفيّان للجميع)، و«مختبر
    الدفع» و«طابور المزامنة» مُزالان كليًّا أصلًا."""
    html = _render_sidebar(app, session_overrides={
        "admin_id": 1,
        "is_super_admin": True,
        "tenant_id": 1,
        "permissions": [],
    })
    # default-off بلا منح → مخفيّان حتى عن السوبر (لا تَجاوز).
    assert _LINK_COLLECTION not in html, "التحصيل default-off يجب أن يَغيب بلا منح"
    assert 'href="/admin/radius/tenants"' not in html, "المستأجرون default-off يجب أن يَغيب بلا منح"
    # مُزالان كليًّا أصلًا — لا للسوبر.
    assert _LINK_PAY_LAB not in html, "مختبر الدفع مُزال للجميع"
    assert _LINK_SYNC not in html, "طابور المزامنة مُزال للجميع"
    assert "hb-side-frozen" not in html  # السوبر لا يَرى صفوفًا مجمّدة


# ───────────────────── (C) تنظيف طابور المزامنة ─────────────────────
def _seed_sync_rows(tenant_id: int = 1):
    """يَزرع صفًّا لكلّ حالة ليُختبَر الانتقائيّة."""
    from app.radius.db.connection import transaction
    from app.radius.db.helpers import now_iso
    now = now_iso()
    rows = [
        ("failed",   "boom"),
        ("retrying", "temporary"),
        ("queued",   ""),
        ("syncing",  ""),
        ("done",     ""),
    ]
    with transaction() as conn:
        for status, err in rows:
            conn.execute(
                "INSERT INTO sync_queue(tenant_id, router_id, kind, entity_id, "
                "entity_key, payload_json, status, attempts, last_error, "
                "next_attempt_at, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (tenant_id, None, "subscriber_upsert", None, "u",
                 "{}", status, 1, err, now, now))


def test_mark_stale_resolved_converts_failed_and_retrying(app):
    from app.radius.db.repos import sync_queue_repo
    with app.app_context():
        _seed_sync_rows()
        before = sync_queue_repo.stats(1)
        assert before["failed"] == 1 and before["retrying"] == 1

        n = sync_queue_repo.mark_stale_resolved(1)
        assert n == 2, "صفّان عالقان (failed+retrying) فقط"

        after = sync_queue_repo.stats(1)
        assert after["failed"] == 0 and after["retrying"] == 0
        # queued/syncing سليمان (لم يُلمسا)؛ done صار 3 (1 سابق + 2 مُحلّان).
        assert after["queued"] == 1 and after["syncing"] == 1
        assert after["done"] == 3
        # last_error مُسِح للصفوف المُحلّاة.
        bad = [r for r in sync_queue_repo.list_jobs(1) if r["last_error"]]
        assert bad == [], "last_error يجب أن يُمسَح عند الحلّ"


def test_mark_stale_resolved_is_idempotent(app):
    from app.radius.db.repos import sync_queue_repo
    with app.app_context():
        _seed_sync_rows()
        first = sync_queue_repo.mark_stale_resolved(1)
        assert first == 2
        second = sync_queue_repo.mark_stale_resolved(1)
        assert second == 0, "إعادة التشغيل لا تجد صفوفًا عالقة → 0 (idempotent)"


def test_migration_133_cleanup_sql_matches_repo(app):
    """الـmigration 133 يُطبّق نفس التنظيف (failed/retrying→done) على مستوى DB."""
    from app.radius.db.connection import db
    from app.radius.db.repos import sync_queue_repo
    from app.radius.db import migrations_runner
    with app.app_context():
        _seed_sync_rows()
        # نُحاكي إعادة تشغيل migration 133 يدويًّا (الـrunner سجّله مُطبَّقًا
        # عند الإقلاع على قاعدة نظيفة بلا صفوف؛ هنا نُعيد تنفيذ نصّه بعد الزرع).
        path = migrations_runner._MIGRATIONS_DIR / "133_sync_queue_cleanup.sql"
        db().executescript(path.read_text(encoding="utf-8"))
        after = sync_queue_repo.stats(1)
        assert after["failed"] == 0 and after["retrying"] == 0
        assert after["done"] == 3
