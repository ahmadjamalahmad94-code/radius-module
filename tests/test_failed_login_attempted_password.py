"""
تشخيص المالك: «كلمة المرور المُحاوَلة (خاطئة)» على المحاولات الفاشلة.

يتحقّق من:
  • المسارات التي يُمكن فيها الالتقاط مقابل التي لا يُمكن:
      - شبكة PAP فاشلة  → النصّ الصريح يُلتقط من radpostauth.pass  → pw_status='shown'.
      - شبكة CHAP فاشلة → radpostauth.pass فارغ (هوتسبوت ميكروتك)     → pw_status='chap'.
      - ويب (بوابة/لوحة) فاشلة → النصّ يُخزَّن في audit_log.payload   → pw_status='shown'.
      - أي محاولة ناجحة → لا تُخزَّن أبدًا، ولا تُعرض                  → pw_status='none'.
      - بعد انقضاء مدّة الاحتفاظ → القيمة لا تُعرض                     → pw_status='expired'.
  • بوّابة العرض: المدير الرئيسي فقط (session.is_super_admin) يرى القيمة؛
    غير-السوبر لا يراها إطلاقًا في الصفحة المرندرة.
"""
from __future__ import annotations

import os

import pytest

from app.radius.db.connection import reset_for_tests


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "attempted_pw.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations

        run_pending_migrations()
    return flask_app


def _seed(app):
    """يزرع محاولات فاشلة/ناجحة عبر القناتين (شبكة + ويب)."""
    from app.radius.db.connection import db, transaction
    from app.radius.db.helpers import now_iso
    from app.radius.services.login_events import record_login_event

    with app.app_context():
        with transaction() as conn:
            ins = ("INSERT INTO radpostauth(tenant_id,username,pass,reply,authdate,class,nas)"
                   " VALUES(?,?,?,?,?,?,?)")
            # شبكة PAP فاشلة → النصّ الصريح محفوظ
            conn.execute(ins, (1, "subPAP", "WrongPap123", "Access-Reject",
                               now_iso(), "password_wrong", "10.0.0.1"))
            # شبكة CHAP فاشلة → pass فارغ (هوتسبوت ميكروتك لا يرسل نصًّا)
            conn.execute(ins, (1, "subCHAP", "", "Access-Reject",
                               now_iso(), "password_wrong", "10.0.0.2"))
            # شبكة فاشلة قديمة (> مدّة الاحتفاظ) → expired
            conn.execute(ins, (1, "subOld", "OldLeakPw", "Access-Reject",
                               "2000-01-01T00:00:00Z", "password_wrong", "10.0.0.3"))
            # شبكة ناجحة → pass مُقنّع '***'، لا يُعرض شيء
            conn.execute(ins, (1, "subOK", "***", "Access-Accept",
                               now_iso(), "", "10.0.0.4"))
        # ويب فاشلة → يُخزَّن في payload عبر المسار الحقيقي
        record_login_event(actor_type="subscriber", username="subWeb", success=False,
                           reason="bad_password", tenant_id=1,
                           attempted_password="WebWrong!")
        # ويب ناجحة → لا يُخزَّن نصّ حتى لو مُرِّر
        record_login_event(actor_type="subscriber", username="subWebOK", success=True,
                           tenant_id=1, attempted_password="ShouldNeverStore")


def test_service_pw_status_per_path(app):
    """الخدمة تصنّف كل مسار بدقّة، ولا تُسرّب كلمة مرور ناجحة."""
    _seed(app)
    from app.radius.services.login_events import fetch_login_events

    with app.app_context():
        rows = fetch_login_events(1, actor="subscriber")["rows"]
    by = {r["username"]: r for r in rows}

    assert by["subPAP"]["pw_status"] == "shown"
    assert by["subPAP"]["attempted_password"] == "WrongPap123"

    assert by["subCHAP"]["pw_status"] == "chap"
    assert by["subCHAP"]["attempted_password"] == ""

    assert by["subOld"]["pw_status"] == "expired"
    assert by["subOld"]["attempted_password"] == ""

    assert by["subWeb"]["pw_status"] == "shown"
    assert by["subWeb"]["attempted_password"] == "WebWrong!"

    # ناجحة: لا تخزين ولا عرض — حتى لو مُرِّرت كلمة المرور
    assert by["subOK"]["pw_status"] == "none"
    assert by["subOK"]["attempted_password"] == ""
    assert by["subWebOK"]["pw_status"] == "none"
    assert by["subWebOK"]["attempted_password"] == ""


def test_successful_web_login_never_stores_password(app):
    """تأكيد تخزيني صريح: payload المحاولة الناجحة لا يحوي attempted_password."""
    from app.radius.db.connection import db
    from app.radius.services.login_events import record_login_event

    with app.app_context():
        record_login_event(actor_type="admin", username="root", success=True,
                           tenant_id=1, attempted_password="correcthorse")
        row = db().execute(
            "SELECT payload_json FROM audit_log WHERE actor='root' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert "attempted_password" not in (row["payload_json"] or "")
    assert "correcthorse" not in (row["payload_json"] or "")


# بعد تقسيم صفحة «حالات الدخول» إلى خمسة أقسام مقيّدة المصدر: محاولات الشبكة
# (PAP/CHAP) تظهر في «حالات دخول المشتركين» (network)، ومحاولات الويب في
# «حالات بوابة المشتركين» (portal). كلٌّ برابطه المباشر.
_NET_URL = "/admin/radius/reports/login_states/subscribers"
_PORTAL_URL = "/admin/radius/reports/login_states/sub_portal"


def _get(app, url, *, is_super: bool):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["admin_user"] = "tester"
            sess["is_super_admin"] = is_super
            sess["tenant_id"] = 1
            # non-super يحتاج صلاحية فتح صفحة التقارير؛ نُثبت أن العرض محجوب
            # بسبب بوّابة السوبر في القالب لا بسبب حارس المسار.
            sess["permissions"] = [] if is_super else ["reports.view"]
            sess["_csrf_token"] = "t"
        res = client.get(url)
        assert res.status_code == 200, url
        return res.get_data(as_text=True)


def test_super_admin_sees_attempted_passwords_and_chap_notice(app):
    _seed(app)
    net = _get(app, _NET_URL, is_super=True)          # PAP/CHAP/expired (شبكة)
    portal = _get(app, _PORTAL_URL, is_super=True)     # WebWrong! (بوابة الويب)
    # القيم الصريحة تظهر للسوبر — الشبكة PAP + الويب.
    assert 'data-testid="attempted-pw"' in net
    assert "WrongPap123" in net
    assert "WebWrong!" in portal
    # CHAP غير قابل للاسترجاع — رسالة صريحة لا قيمة وهمية (شبكة).
    assert 'data-testid="attempted-pw-chap"' in net
    assert "النصّ غير متاح — CHAP" in net
    # انقضاء الاحتفاظ يُخفي القيمة القديمة (شبكة).
    assert "انتهت مدّة الاحتفاظ" in net
    assert "OldLeakPw" not in net
    # تنويه السياسة حاضر على كِلا الصفحتين للسوبر.
    assert 'data-testid="attempted-pw-note"' in net
    assert 'data-testid="attempted-pw-note"' in portal


def test_non_super_admin_never_sees_attempted_passwords(app):
    _seed(app)
    net = _get(app, _NET_URL, is_super=False)
    portal = _get(app, _PORTAL_URL, is_super=False)
    # الصفوف تظهر لكن بلا أي كلمة مرور مُحاوَلة ولا تنويه.
    assert "subPAP" in net  # الصفّ نفسه ظاهر
    assert "WrongPap123" not in net
    assert "WebWrong!" not in portal
    assert 'data-testid="attempted-pw"' not in net
    assert "النصّ غير متاح — CHAP" not in net
    assert 'data-testid="attempted-pw-note"' not in net
    assert 'data-testid="attempted-pw-note"' not in portal
