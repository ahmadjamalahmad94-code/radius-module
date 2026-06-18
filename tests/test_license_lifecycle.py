"""feat/license-service-gate — اختبارات دورة حياة الترخيص.

تغطّي السلوك المختلف بين:
  • انقطاع تزامن عابر (fail-open، آخر معلوم ضمن السماحية)
  • ترخيص منتهٍ صراحةً (fail-closed، إقفال شامل)
  • نسخة لم تُفعَّل بعد (fail-closed، شاشة activate)
  • انقطاع طويل تجاوز السماحية (fail-closed، يُعامَل كانتهاء)

السوبر-أدمن لا يتجاوز في حالات الإقفال. صفحات الإصلاح/التشخيص تبقى
مكشوفة دائمًا (login/logout/locale + license + admin_bridge +
lockout/grants pages) كي لا يُحجَب المسؤول عن إصلاح الترخيص.

شغّل هذا الملف وحده (عزل لكل ملف اختبار).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "lifecycle.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    # هذا الملف يفحص الحارس نفسه — نُلغي تجاوز الاختبار العام كي يَنفّذ
    # الحارس قراراته الحقيقية على كل سيناريو.
    monkeypatch.delenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        yield app


def _iso(dt: datetime) -> str:
    return dt.isoformat() + "Z"


def _seed_license_snapshot(*, tenant_id: int = 1, status: str = "active",
                            expires_at=None, grace_until=None,
                            fetched_at: datetime | None = None,
                            stale_after_seconds: int = 86400) -> int:
    """يكتب لقطة license مباشرة في DB — يحاكي ما يصلنا من المزوّد."""
    from app.radius.db.connection import db
    payload = {"status": status}
    if expires_at is not None:
        payload["expires_at"] = expires_at if isinstance(expires_at, str) else _iso(expires_at)
    if grace_until is not None:
        payload["grace_until"] = grace_until if isinstance(grace_until, str) else _iso(grace_until)
    fetched_iso = _iso(fetched_at or datetime.utcnow())
    # normalized_status يجب أن يكون active لتلتقطه latest_success.
    normalized = "active" if status in ("active", "valid", "ok", "healthy", "grace") else status
    cur = db().execute(
        """INSERT INTO license_admin_bridge_snapshots
           (tenant_id, snapshot_type, normalized_status, source_url,
            payload_json, error_json, fetched_at, stale_after_seconds, created_at)
           VALUES (?, 'license', ?, 'test://lic',
                   ?, '{}', ?, ?, ?)""",
        (int(tenant_id), normalized,
         json.dumps(payload, ensure_ascii=False),
         fetched_iso, int(stale_after_seconds), fetched_iso))
    return int(cur.lastrowid or 0)


def _client(app, *, super_admin: bool = True):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=1, admin_user="admin", admin_name="admin",
                 is_super_admin=super_admin, tenant_id=1, permissions=["*"])
    return c


# ════════════════════════════════════════════════════════════════════════
# (1) منطق خالص — evaluate
# ════════════════════════════════════════════════════════════════════════
class TestLifecycleEvaluation:

    def test_never_activated_when_no_snapshot(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        d = evaluate(1)
        assert d.state == LifecycleState.NEVER_ACTIVATED
        assert d.blocks_panel

    def test_active_when_fresh_active_status(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_license_snapshot(status="active")
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE
        assert not d.blocks_panel

    def test_expired_when_status_says_expired(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_license_snapshot(status="expired")
        d = evaluate(1)
        assert d.state == LifecycleState.EXPIRED
        assert d.blocks_panel
        assert d.reason == "status_expired"

    def test_expired_when_status_revoked(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        _seed_license_snapshot(status="revoked")
        d = evaluate(1)
        assert d.state == LifecycleState.EXPIRED
        assert d.blocks_panel

    def test_expired_when_expires_at_past_and_no_grace(self, app_ctx):
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        past = datetime.utcnow() - timedelta(days=2)
        _seed_license_snapshot(status="active", expires_at=past)
        d = evaluate(1)
        assert d.state == LifecycleState.EXPIRED
        assert d.reason == "expires_at_passed"

    def test_active_when_expires_at_past_but_grace_still_running(self, app_ctx):
        """grace_until من المزوّد يمدّ السماحية رسميًّا — يبقى نشطًا."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        past = datetime.utcnow() - timedelta(days=2)
        future = datetime.utcnow() + timedelta(days=5)
        _seed_license_snapshot(status="active",
                                 expires_at=past, grace_until=future)
        d = evaluate(1)
        assert d.state == LifecycleState.ACTIVE

    def test_sync_outage_within_grace_keeps_working(self, app_ctx):
        """آخر لقطة ناجحة قبل أسبوع، stale_after=1d → ضمن السماحية المحلّية
        (7 أيام افتراضيًّا) → fail-open."""
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        old = datetime.utcnow() - timedelta(days=3)
        _seed_license_snapshot(status="active", fetched_at=old,
                                 stale_after_seconds=86400)
        d = evaluate(1)
        assert d.state == LifecycleState.SYNC_OUTAGE_IN_GRACE
        assert not d.blocks_panel
        assert d.stale_days >= 2.9
        assert d.grace_remaining_days > 0

    def test_sync_outage_beyond_grace_locks(self, app_ctx, monkeypatch):
        """تجاوز السماحية المحلّية → يُعامَل كانتهاء."""
        monkeypatch.setenv("HOBERADIUS_LICENSE_SYNC_GRACE_DAYS", "2")
        from app.radius.services.license_lifecycle import (
            evaluate, LifecycleState)
        old = datetime.utcnow() - timedelta(days=5)
        _seed_license_snapshot(status="active", fetched_at=old,
                                 stale_after_seconds=86400)
        d = evaluate(1)
        assert d.state == LifecycleState.SYNC_OUTAGE_BEYOND_GRACE
        assert d.blocks_panel
        assert d.reason == "sync_grace_exhausted"

    def test_grace_days_env_clamped(self):
        """قيمة env خارج النطاق تُصحَّح (clamp إلى [0.5, 30])."""
        from app.radius.services.license_lifecycle import _sync_grace_days
        os.environ["HOBERADIUS_LICENSE_SYNC_GRACE_DAYS"] = "100"
        try:
            assert _sync_grace_days() == 30.0
        finally:
            del os.environ["HOBERADIUS_LICENSE_SYNC_GRACE_DAYS"]
        os.environ["HOBERADIUS_LICENSE_SYNC_GRACE_DAYS"] = "0"
        try:
            assert _sync_grace_days() == 0.5
        finally:
            del os.environ["HOBERADIUS_LICENSE_SYNC_GRACE_DAYS"]


# ════════════════════════════════════════════════════════════════════════
# (2) حارس _perm_guard — السوبر-أدمن لا يتجاوز
# ════════════════════════════════════════════════════════════════════════
class TestPermGuardLockout:

    def test_super_admin_locked_when_never_activated(self, app_ctx):
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/users", follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/admin/radius/_license/activate" in rv.headers.get("Location", "")

    def test_super_admin_locked_when_license_expired(self, app_ctx):
        _seed_license_snapshot(status="expired")
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/cards", follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/admin/radius/_license/expired" in rv.headers.get("Location", "")

    def test_super_admin_locked_when_expires_at_past(self, app_ctx):
        past = datetime.utcnow() - timedelta(days=1)
        _seed_license_snapshot(status="active", expires_at=past)
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/reports/login_states",
                          follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/admin/radius/_license/expired" in rv.headers.get("Location", "")

    def test_super_admin_locked_beyond_sync_grace(self, app_ctx, monkeypatch):
        monkeypatch.setenv("HOBERADIUS_LICENSE_SYNC_GRACE_DAYS", "1")
        old = datetime.utcnow() - timedelta(days=10)
        _seed_license_snapshot(status="active", fetched_at=old,
                                 stale_after_seconds=86400)
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/", follow_redirects=False)
        assert rv.status_code in (302, 303)
        assert "/admin/radius/_license/expired" in rv.headers.get("Location", "")

    def test_super_admin_writes_get_403_when_locked(self, app_ctx):
        _seed_license_snapshot(status="expired")
        client = _client(app_ctx, super_admin=True)
        client.get("/admin/radius/_license/expired")  # CSRF seed
        with client.session_transaction() as s:
            token = s.get("_csrf_token") or ""
        rv = client.post("/admin/radius/cards/generate",
                          data={"plan_id": "1", "count": "5",
                                "_csrf_token": token})
        assert rv.status_code == 403

    def test_active_license_does_not_lock(self, app_ctx):
        _seed_license_snapshot(status="active")
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/", follow_redirects=False)
        # active = لا حارس دورة حياة → 200 طبيعي
        assert rv.status_code == 200

    def test_sync_outage_in_grace_does_not_lock(self, app_ctx):
        # لقطة قديمة ضمن السماحية → fail-open (الإبقاء على آخر معلوم)
        old = datetime.utcnow() - timedelta(days=3)
        _seed_license_snapshot(status="active", fetched_at=old,
                                 stale_after_seconds=86400)
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/users", follow_redirects=False)
        assert rv.status_code in (200, 302)
        # لا redirect لصفحة قفل
        loc = rv.headers.get("Location") or ""
        assert "/admin/radius/_license/" not in loc


# ════════════════════════════════════════════════════════════════════════
# (3) صفحات الإصلاح تبقى مكشوفة دائمًا
# ════════════════════════════════════════════════════════════════════════
class TestRecoveryPagesAlwaysReachable:

    def _check_reachable(self, app, path):
        client = _client(app, super_admin=True)
        rv = client.get(path, follow_redirects=False)
        # 200 أو redirect لتسجيل دخول/locale (ليس لقفل)، لكن ليس 403 ولا
        # redirect لـ_license/.
        loc = rv.headers.get("Location") or ""
        assert "/admin/radius/_license/" not in loc, \
            f"{path} unexpectedly redirected to lockout: {loc}"
        assert rv.status_code != 403, f"{path} returned 403"
        return rv

    def test_activate_page_reachable_when_never_activated(self, app_ctx):
        # لا snapshot — حالة never_activated
        self._check_reachable(app_ctx, "/admin/radius/_license/activate")

    def test_expired_page_reachable_when_expired(self, app_ctx):
        _seed_license_snapshot(status="expired")
        self._check_reachable(app_ctx, "/admin/radius/_license/expired")

    def test_license_file_reachable_when_expired(self, app_ctx):
        _seed_license_snapshot(status="expired")
        self._check_reachable(app_ctx, "/admin/radius/license/file")

    def test_admin_bridge_reachable_when_never_activated(self, app_ctx):
        self._check_reachable(app_ctx, "/admin/radius/admin-bridge")

    def test_set_locale_reachable_when_expired(self, app_ctx):
        _seed_license_snapshot(status="expired")
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/set-locale?locale=en&next=/admin/radius/")
        # set-locale يعيد توجيهًا (302) لكن ليس لصفحة قفل
        loc = rv.headers.get("Location") or ""
        assert "/admin/radius/_license/" not in loc

    def test_logout_reachable_when_expired(self, app_ctx):
        _seed_license_snapshot(status="expired")
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/logout")
        loc = rv.headers.get("Location") or ""
        assert "/admin/radius/_license/" not in loc

    def test_grants_status_reachable_when_expired(self, app_ctx):
        """تشخيصية — يجب أن تبقى مرئية حتى مع انتهاء الترخيص كي يرى
        المسؤول ما الذي وصل من المزوّد."""
        _seed_license_snapshot(status="expired")
        self._check_reachable(app_ctx, "/admin/radius/_provider/grants")


# ════════════════════════════════════════════════════════════════════════
# (4) صفحات القفل تَرندَر بمحتوى صحيح
# ════════════════════════════════════════════════════════════════════════
class TestLockoutPagesRender:

    def test_activate_page_renders(self, app_ctx):
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_license/activate")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "فعّل الترخيص" in body
        assert "ترخيص النظام" in body  # CTA

    def test_expired_page_renders_with_decision_details(self, app_ctx):
        past = datetime.utcnow() - timedelta(days=2)
        _seed_license_snapshot(status="expired", expires_at=past)
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_license/expired")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "الترخيص منتهي" in body
        assert "expired" in body  # last_status
        assert "status_expired" in body  # reason

    def test_expired_page_distinguishes_sync_outage_beyond_grace(
            self, app_ctx, monkeypatch):
        monkeypatch.setenv("HOBERADIUS_LICENSE_SYNC_GRACE_DAYS", "1")
        old = datetime.utcnow() - timedelta(days=5)
        _seed_license_snapshot(status="active", fetched_at=old,
                                 stale_after_seconds=86400)
        client = _client(app_ctx, super_admin=True)
        rv = client.get("/admin/radius/_license/expired")
        assert rv.status_code == 200
        body = rv.get_data(as_text=True)
        assert "انقطاع تزامن طويل" in body
