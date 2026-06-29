# -*- coding: utf-8 -*-
"""اختبارات رفع اعتماد Firebase من اللوحة (بلا أيّ خطوة خادم/بيئة).

تُغطّي:
  • التحقّق: ملفّ حساب خدمة صالح يُقبَل، وغير الصالح يُرفَض برسالة.
  • التخزين: يُكتب في instance/ بجوار القاعدة + نسخة قاعدة بيانات + يُلتقطه
    fcm_push عبر credentials_path (الترتيب: ملفّ → قاعدة → بيئة).
  • الحالة المُقنَّعة: project_id يَظهر، client_email مُقنَّع، والمحتوى السرّ
    (المفتاح الخاصّ) لا يُعرَض أبدًا.
  • مسار الرفع: المالك يَرفع صالحًا → 200 + الحالة configured + يُلتقط
    (إرسال موهوم)؛ غير الصالح يُرفَض؛ غير المالك يُمنَع.
  • مكتبة firebase-admin غير مثبّتة → رسالة صريحة في البطاقة.

تُموّه طبقة الإرسال — لا اتّصال بـ FCM الحقيقي. عزل لكل ملفّ.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

# ملفّ حساب خدمة Firebase «صالح» للاختبار — بنية حقيقيّة، مفتاح وهميّ
# (PEM-marker فقط؛ لا يُستخدم في إرسال حقيقيّ — الإرسال موهوم).
_VALID_SA = {
    "type": "service_account",
    "project_id": "hoberadius",
    "private_key_id": "abc123",
    "private_key": "-----BEGIN PRIVATE KEY-----\nFAKEKEYDATA==\n-----END PRIVATE KEY-----\n",
    "client_email": "firebase-adminsdk-xyz99@hoberadius.iam.gserviceaccount.com",
    "client_id": "1234567890",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def _valid_bytes() -> bytes:
    return json.dumps(_VALID_SA).encode("utf-8")


@pytest.fixture()
def app():
    tmp = tempfile.mkdtemp(prefix="hr_fcm_cred_")
    os.environ.update(
        HOBERADIUS_DB_PATH=os.path.join(tmp, "instance", "test.db"),
        HOBERADIUS_NO_WORKER="1", HOBERADIUS_NO_SEED="1",
        HOBERADIUS_LICENSE_GATE_TEST_BYPASS="1", FLASK_SECRET="k")
    os.environ.pop("FIREBASE_CREDENTIALS_PATH", None)
    os.environ.pop("GOOGLE_APPLICATION_CREDENTIALS", None)
    os.environ.pop("HOBERADIUS_FCM_DISABLED", None)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(os.environ["HOBERADIUS_DB_PATH"])
    from app import create_app
    created = create_app()
    with created.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admin = admins_repo.create_admin(
            username="op", password="op123456", full_name="مالك")
        created.config["_admin_id"] = int(getattr(admin, "id", 1) or 1)
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


@pytest.fixture()
def owner_client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=app.config["_admin_id"], admin_user="op",
                 admin_name="مالك", is_super_admin=True, tenant_id=1,
                 _csrf_token="t")
    return c


@pytest.fixture()
def viewer_client(app):
    c = app.test_client()
    with c.session_transaction() as s:
        s.update(admin_id=app.config["_admin_id"], admin_user="v",
                 admin_name="مشاهد", is_super_admin=False, tenant_id=1,
                 _csrf_token="t")
    return c


# ═══════════════════════ (1) التحقّق ═══════════════════════

def test_validate_accepts_real_service_account(app):
    from app.services import fcm_credentials
    with app.app_context():
        ok, data, err = fcm_credentials.validate_service_account(_valid_bytes())
        assert ok is True and err == ""
        assert data["project_id"] == "hoberadius"


def test_validate_rejects_non_json(app):
    from app.services import fcm_credentials
    with app.app_context():
        ok, data, err = fcm_credentials.validate_service_account(b"not json {{{")
        assert ok is False and data is None and "JSON" in err


def test_validate_rejects_wrong_type(app):
    from app.services import fcm_credentials
    with app.app_context():
        bad = json.dumps({"type": "authorized_user", "project_id": "x"}).encode()
        ok, _data, err = fcm_credentials.validate_service_account(bad)
        assert ok is False and "service_account" in err


def test_validate_rejects_missing_fields(app):
    from app.services import fcm_credentials
    with app.app_context():
        bad = json.dumps({"type": "service_account", "project_id": "hoberadius"}).encode()
        ok, _data, err = fcm_credentials.validate_service_account(bad)
        assert ok is False and ("private_key" in err or "client_email" in err)


# ═══════════════════════ (2) التخزين + الالتقاط ═══════════════════════

def test_store_writes_file_next_to_db_and_db_backup(app):
    from app.services import fcm_credentials
    with app.app_context():
        info = fcm_credentials.store_uploaded(_valid_bytes(), by=1)
        assert info["project_id"] == "hoberadius"
        # الملفّ كُتب بجوار قاعدة البيانات الحيّة في instance/.
        path = fcm_credentials.stored_file_path()
        assert path.is_file()
        from app.radius.db.connection import db_path
        assert str(path.parent) == str(__import__("pathlib").Path(db_path()).resolve().parent)
        # نسخة الاسترداد في القاعدة موجودة ومُشفّرة (system_settings عبر env_settings):
        # القيمة المفكوكة تَستردّ الـ JSON، والقيمة المخزّنة الخام مشفّرة (لا نصّ صريح).
        from app.radius.core import env_settings
        assert "hoberadius" in env_settings.env("HOBERADIUS_FCM_CREDENTIAL_JSON", "")
        import sqlite3
        con = sqlite3.connect(db_path())
        row = con.execute("SELECT value, is_secret FROM system_settings "
                          "WHERE key='HOBERADIUS_FCM_CREDENTIAL_JSON'").fetchone()
        con.close()
        assert row and row[1] == 1                       # مُعلَّم سرًّا
        assert "PRIVATE KEY" not in (row[0] or "")        # مُشفَّر في العمود — لا نصّ صريح


def test_fcm_push_picks_up_uploaded_credential(app):
    """بعد الرفع من اللوحة، credentials_path يُرجع الملفّ المرفوع بلا أيّ بيئة."""
    from app.services import fcm_credentials, fcm_push
    with app.app_context():
        assert fcm_push.credentials_path() == ""   # لا اعتماد بعد
        fcm_credentials.store_uploaded(_valid_bytes(), by=1)
        fcm_push.reset_for_test()
        path = fcm_push.credentials_path()
        assert path and os.path.isfile(path)
        assert path == str(fcm_credentials.stored_file_path())


def test_resolve_regenerates_file_from_db_backup(app):
    """لو فُقد ملفّ instance/ تُعاد كتابته من نسخة القاعدة (استرداد)."""
    from app.services import fcm_credentials
    with app.app_context():
        fcm_credentials.store_uploaded(_valid_bytes(), by=1)
        fcm_credentials.stored_file_path().unlink()   # حاكِ فقد المجلّد
        assert not fcm_credentials.stored_file_path().is_file()
        path = fcm_credentials.resolve_credential_path()
        assert path and os.path.isfile(path)          # أُعيد توليده من القاعدة


# ═══════════════════════ (3) الحالة المُقنَّعة (لا تسريب) ═══════════════════════

def test_status_masks_email_and_never_leaks_private_key(app):
    from app.services import fcm_credentials
    with app.app_context():
        fcm_credentials.store_uploaded(_valid_bytes(), by=1)
        st = fcm_credentials.status()
        assert st["configured"] is True
        assert st["project_id"] == "hoberadius"
        # البريد مُقنَّع (يُبقي النطاق) والمفتاح الخاصّ لا يَظهر في أيّ حقل.
        blob = json.dumps(st, ensure_ascii=False)
        assert "PRIVATE KEY" not in blob
        assert "FAKEKEYDATA" not in blob
        assert "hoberadius.iam.gserviceaccount.com" in st["client_email"]


# ═══════════════════════ (4) مسار الرفع (الواجهة) ═══════════════════════

def test_owner_upload_valid_enables_and_status_configured(app, owner_client, monkeypatch):
    import io
    res = owner_client.post(
        "/admin/radius/notifications/push-credential",
        data={"_csrf_token": "t",
              "credential": (io.BytesIO(_valid_bytes()), "firebase-admin-sdk.json")},
        content_type="multipart/form-data", follow_redirects=True)
    assert res.status_code == 200
    assert "تم رفع اعتماد Firebase" in res.get_data(as_text=True)
    with app.app_context():
        from app.radius.services import notifications as notif_svc
        st = notif_svc.push_status(1)
        assert st["has_cred"] is True
        assert st["project_id"] == "hoberadius"


def test_owner_upload_then_test_push_dispatches(app, owner_client, monkeypatch):
    """بعد الرفع، «أرسل إشعار تجريبي» يَستدعي المُرسِل (إرسال موهوم)."""
    import io
    from app.services import fcm_push
    from app.radius.db.repos import device_push_tokens_repo as repo

    calls = {"n": 0}
    monkeypatch.setattr(fcm_push, "send_to_tokens",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
                        or {"ok": True, "sent": 1, "failed": 0, "invalid_tokens": []})
    monkeypatch.setattr(fcm_push, "is_enabled", lambda: True)
    with app.app_context():
        repo.register(1, "tok-1", admin_id=1, platform="android")
    owner_client.post(
        "/admin/radius/notifications/push-credential",
        data={"_csrf_token": "t",
              "credential": (io.BytesIO(_valid_bytes()), "sa.json")},
        content_type="multipart/form-data", follow_redirects=True)
    res = owner_client.post("/admin/radius/notifications/test-push",
                            data={"_csrf_token": "t"}, follow_redirects=True)
    assert res.status_code == 200
    assert calls["n"] == 1


def test_owner_upload_invalid_rejected(app, owner_client):
    import io
    res = owner_client.post(
        "/admin/radius/notifications/push-credential",
        data={"_csrf_token": "t",
              "credential": (io.BytesIO(b"garbage not json"), "bad.json")},
        content_type="multipart/form-data", follow_redirects=True)
    assert res.status_code == 200
    assert "غير صالح" in res.get_data(as_text=True)
    with app.app_context():
        from app.services import fcm_credentials
        assert fcm_credentials.status()["configured"] is False


def test_non_owner_cannot_upload(app, viewer_client):
    import io
    res = viewer_client.post(
        "/admin/radius/notifications/push-credential",
        data={"_csrf_token": "t",
              "credential": (io.BytesIO(_valid_bytes()), "sa.json")},
        content_type="multipart/form-data", follow_redirects=True)
    assert res.status_code == 200
    assert "مقصور على المالك" in res.get_data(as_text=True)
    with app.app_context():
        from app.services import fcm_credentials
        assert fcm_credentials.status()["configured"] is False


def test_center_does_not_render_credential_contents(app, owner_client):
    """صفحة المركز تَعرض الحالة المُقنَّعة لكن لا تُسرّب محتوى الملفّ أبدًا."""
    import io
    owner_client.post(
        "/admin/radius/notifications/push-credential",
        data={"_csrf_token": "t",
              "credential": (io.BytesIO(_valid_bytes()), "sa.json")},
        content_type="multipart/form-data", follow_redirects=True)
    res = owner_client.get("/admin/radius/notifications")
    h = res.get_data(as_text=True)
    assert "اعتماد Firebase مرفوع" in h          # حالة مُقنَّعة ظاهرة
    assert "hoberadius" in h                      # project_id العامّ
    assert "PRIVATE KEY" not in h                 # السرّ لا يُعرَض
    assert "FAKEKEYDATA" not in h


def test_library_missing_message_when_cred_present(app, owner_client, monkeypatch):
    """اعتماد مرفوع لكن firebase-admin غير مثبّت → رسالة صريحة في البطاقة."""
    import io
    from app.services import fcm_push
    monkeypatch.setattr(fcm_push, "library_available", lambda: False)
    owner_client.post(
        "/admin/radius/notifications/push-credential",
        data={"_csrf_token": "t",
              "credential": (io.BytesIO(_valid_bytes()), "sa.json")},
        content_type="multipart/form-data", follow_redirects=True)
    res = owner_client.get("/admin/radius/notifications")
    assert "مكتبة Firebase غير مثبّتة على الخادم" in res.get_data(as_text=True)


def test_upload_field_only_for_owner(app, owner_client, viewer_client):
    assert 'data-testid="fcm-cred-upload"' in owner_client.get(
        "/admin/radius/notifications").get_data(as_text=True)
    assert 'data-testid="fcm-cred-upload"' not in viewer_client.get(
        "/admin/radius/notifications").get_data(as_text=True)
