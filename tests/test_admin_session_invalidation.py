"""إبطال جلسات المدراء — حادثة 2026-08-02 (مدير محذوف تصفّح بجلسة قديمة).

الجلسة كوكي موقَّع عند المتصفّح، فلا يُنهيها حذف الحساب ولا تغيير كلمة
المرور تلقائيًّا. هذه الاختبارات تُثبّت الإنفاذ الخادميّ:
- حساب محذوف/معطَّل ⇒ الجلسة ميتة فورًا.
- تغيير كلمة المرور ⇒ كل الجلسات المفتوحة تموت (كل الأجهزة).
- مزامنة لوحة التراخيص بنفس البصمة ⇒ لا تطرد أحدًا.
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "sess_inval.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()

    @flask_app.before_request
    def _bind_test_db():
        os.environ["HOBERADIUS_DB_PATH"] = db_file
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(db_file)

    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


def _admin(username: str = "mgr", password: str = "pw-123456789") -> int:
    from app.radius.db.repos import admins_repo

    a = admins_repo.create_admin(username=username, password=password,
                                 full_name="مدير اختبار")
    return int(a.id)


def _session_for(client, admin_id: int) -> None:
    """جلسة مطابقة لما يكتبه الدخول الحقيقي (بما فيها ختم الجلسة)."""
    from app.radius.db.repos import admins_repo

    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = "mgr"
        sess["admin_name"] = "مدير اختبار"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["permissions"] = ["cards.view"]
        sess["admin_sv"] = admins_repo.session_epoch(admin_id) or 0
        sess["_csrf_token"] = "t"


def _is_kicked(resp) -> bool:
    return resp.status_code in (302, 303) and "login" in resp.headers.get("Location", "")


def test_live_session_works(app):
    with app.app_context():
        aid = _admin()
    client = app.test_client()
    _session_for(client, aid)
    assert not _is_kicked(client.get("/admin/radius/cards/checker"))


def test_deleted_admin_session_is_dead(app):
    """جوهر الحادثة: الحساب حُذف والجلسة القديمة ما زالت في المتصفّح."""
    with app.app_context():
        aid = _admin()
    client = app.test_client()
    _session_for(client, aid)
    with app.app_context():
        db().execute("UPDATE admins SET deleted_at=datetime('now') WHERE id=?", (aid,))
    assert _is_kicked(client.get("/admin/radius/cards/checker"))


def test_disabled_admin_session_is_dead(app):
    with app.app_context():
        aid = _admin()
    client = app.test_client()
    _session_for(client, aid)
    with app.app_context():
        db().execute("UPDATE admins SET enabled=0 WHERE id=?", (aid,))
    assert _is_kicked(client.get("/admin/radius/cards/checker"))


def test_password_change_kills_all_sessions(app):
    """جهازان مفتوحان على الحساب؛ تغيير كلمة المرور يطردهما معًا."""
    with app.app_context():
        aid = _admin()
    dev1, dev2 = app.test_client(), app.test_client()
    _session_for(dev1, aid)
    _session_for(dev2, aid)
    assert not _is_kicked(dev1.get("/admin/radius/cards/checker"))
    assert not _is_kicked(dev2.get("/admin/radius/cards/checker"))

    with app.app_context():
        from app.radius.db.repos import admins_repo
        admins_repo.update_admin(aid, password="brand-new-pw-9")

    assert _is_kicked(dev1.get("/admin/radius/cards/checker"))
    assert _is_kicked(dev2.get("/admin/radius/cards/checker"))


def test_epoch_bumps_only_on_password_change(app):
    """تعديل حقل آخر (الاسم) لا يطرد أحدًا."""
    with app.app_context():
        aid = _admin()
    client = app.test_client()
    _session_for(client, aid)
    with app.app_context():
        from app.radius.db.repos import admins_repo
        before = admins_repo.session_epoch(aid)
        admins_repo.update_admin(aid, full_name="اسم جديد")
        assert admins_repo.session_epoch(aid) == before
    assert not _is_kicked(client.get("/admin/radius/cards/checker"))


def test_account_password_route_logs_out_everywhere(app):
    """المسار الحقيقي /account/password: يغيّر ثم يطرد لصفحة الدخول."""
    with app.app_context():
        aid = _admin(password="old-password-1")
    client = app.test_client()
    _session_for(client, aid)
    resp = client.post("/admin/radius/account/password", data={
        "current_password": "old-password-1",
        "new_password": "new-password-2",
        "confirm_password": "new-password-2",
        "_csrf_token": "t",
    }, follow_redirects=False)
    assert _is_kicked(resp), resp.headers.get("Location")
    # الجلسة نفسها ماتت أيضًا
    assert _is_kicked(client.get("/admin/radius/cards/checker"))
    with app.app_context():
        from app.radius.db.repos import admins_repo
        a = admins_repo.get_admin(aid)
        assert admins_repo.verify_password("new-password-2", a.password_hash)


def test_session_without_stamp_survives_when_epoch_zero(app):
    """جلسات قائمة قبل الترقية (بلا ختم) لا تُطرد بلا سبب."""
    with app.app_context():
        aid = _admin()
    client = app.test_client()
    _session_for(client, aid)
    with client.session_transaction() as sess:
        sess.pop("admin_sv", None)          # كما كانت الجلسات قبل الترقية
    assert not _is_kicked(client.get("/admin/radius/cards/checker"))
