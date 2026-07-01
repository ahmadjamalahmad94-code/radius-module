"""اختبارات مسارات معالج الترحيل (للمالك فقط) — e2e عبر عميل Flask.

تغطّي: حظر غير المالك (403)، التدفّق الكامل تحليل→خطّة→تنفيذ، dry-run لا
يكتب، وإخفاء/إظهار بند الشريط الجانبي.

شغّل هذا الملف وحده.
"""
from __future__ import annotations

import io
import os
import sqlite3
import tempfile
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "routes.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        tenants_repo.ensure_default_tenant()
        yield app
    reset_for_tests(None)


@pytest.fixture
def client(app):
    return app.test_client()


def _make_admin(is_super=True):
    from app.radius.db.repos import admins_repo
    u = f"u_{uuid4().hex[:8]}"
    admins_repo.create_admin(username=u, password="pw123456",
                             full_name="T", is_super_admin=is_super)
    return u


def _login(client, username):
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": "pw123456"},
                      follow_redirects=False)
    assert res.status_code in (302, 303), res.status_code


def _csrf(client):
    # زيارة الصفحة تولّد _csrf_token وتحفظه في الجلسة (csrf_token() داخل القالب).
    client.get("/admin/radius/migrate")
    with client.session_transaction() as s:
        return s.get("_csrf_token", "")


def _sqlite_upload() -> bytes:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        c = sqlite3.connect(path)
        c.executescript("""
            CREATE TABLE plans (id INTEGER, name TEXT, price REAL);
            INSERT INTO plans VALUES (1,'Gold',10);
            CREATE TABLE subscribers (id INTEGER, username TEXT, password TEXT, plan TEXT);
            INSERT INTO subscribers VALUES (1,'ali','p1','Gold'),(2,'sara','p2','Gold');
        """)
        c.commit()
        c.close()
        with open(path, "rb") as fh:
            return fh.read()
    finally:
        os.unlink(path)


# ── حماية المالك ──────────────────────────────────────────────────────

class TestUploadFormats:
    """ركّز على قبول الرفع لكلّ الصيَغ المدعومة — خاصّةً .sql.gz (باغ المالك:
    نموذج الرفع كان يرفض gzip). الخادم يفحص المحتوى (magic 1f8b) لا الامتداد."""

    def _upload(self, client, tok, data_bytes, filename):
        return client.post(
            "/admin/radius/migrate/analyze",
            data={"file": (io.BytesIO(data_bytes), filename)},
            content_type="multipart/form-data",
            headers={"X-CSRFToken": tok})

    def test_sql_gz_accepted(self, client):
        import gzip
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        sql = (b"CREATE TABLE `subscribers` (`username` varchar(30),`pass` varchar(30));\n"
               b"INSERT INTO `subscribers` VALUES ('ali','p1'),('sara','p2');\n")
        res = self._upload(client, tok, gzip.compress(sql), "adv_dump.sql.gz")
        assert res.status_code == 200, res.get_json()
        j = res.get_json()
        assert j["ok"] is True
        assert j["analysis"]["fmt"] == "sql_dump"      # فُكّ الضغط وحُلِّل
        assert any(m["section"] == "subscribers" for m in j["analysis"]["matches"])

    def test_gzip_accepted_without_extension(self, client):
        # اسم بلا امتداد .gz لكنّ المحتوى gzip → يُقبَل بفحص المحتوى.
        import gzip
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        sql = (b"CREATE TABLE `t` (`username` varchar(30));\n"
               b"INSERT INTO `t` VALUES ('u1'),('u2');\n")
        res = self._upload(client, tok, gzip.compress(sql), "dump_nogz_ext")
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["analysis"]["fmt"] == "sql_dump"

    def test_plain_sql_accepted(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        sql = (b"CREATE TABLE `profiles` (`name` varchar(30),`price` int);\n"
               b"INSERT INTO `profiles` VALUES ('Gold',10);\n")
        res = self._upload(client, tok, sql, "d.sql")
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["analysis"]["fmt"] == "sql_dump"

    def test_csv_still_accepted(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        csv = b"username,password,plan\nali,1,Gold\nsara,2,Silver\n"
        res = self._upload(client, tok, csv, "users.csv")
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["analysis"]["fmt"] == "csv"

    def test_xlsx_still_accepted(self, client):
        from openpyxl import Workbook
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        wb = Workbook()
        ws = wb.active
        ws.append(["username", "password"])
        ws.append(["ali", 1234])
        buf = io.BytesIO()
        wb.save(buf)
        res = self._upload(client, tok, buf.getvalue(), "book.xlsx")
        assert res.status_code == 200, res.get_json()
        assert res.get_json()["analysis"]["fmt"] == "xlsx"

    def test_accept_attr_includes_gzip(self, client):
        # الواجهة: سمة accept على مُدخَل الملف تشمل gzip والامتداد المزدوج.
        u = _make_admin()
        _login(client, u)
        html = client.get("/admin/radius/migrate").data.decode("utf-8")
        assert ".sql.gz" in html and ".gz" in html
        assert "application/gzip" in html

    def test_frontend_has_robust_error_handling(self, client):
        # الواجهة تحوي مُعالِج استجابة آمنًا (لا json() عمياء) + رسائل الحالة
        # + تلميح .gz — كي لا يظهر «Unexpected token '<'» مطلقًا.
        u = _make_admin()
        _login(client, u)
        html = client.get("/admin/radius/migrate").data.decode("utf-8")
        assert "readJson" in html and "httpMessage" in html
        assert "413" in html                       # رسالة «الملفّ كبير جدًا»
        assert "mig-gz-hint" in html               # تلميح تفضيل .gz

    def test_empty_upload_returns_json_not_html(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        res = self._upload(client, tok, b"", "empty.sql")
        assert res.status_code == 400
        assert res.content_type.startswith("application/json")
        assert res.get_json()["ok"] is False

    def test_oversize_returns_json_413_not_html(self, client, app):
        # اضبط الحدّ منخفضًا ثمّ ارفع جسمًا يتجاوزه → 413 JSON نظيف (لا HTML).
        app.config["MAX_CONTENT_LENGTH"] = 2000
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        res = self._upload(client, tok, b"x" * 8000, "big.sql")
        assert res.status_code == 413
        assert res.content_type.startswith("application/json")
        j = res.get_json()
        assert j["ok"] is False and j.get("status") == "too_large"

    def test_max_content_length_is_500mb(self, app):
        assert app.config["MAX_CONTENT_LENGTH"] == 500 * 1024 * 1024

    def test_styled_uploader_present(self, client):
        u = _make_admin()
        _login(client, u)
        html = client.get("/admin/radius/migrate").data.decode("utf-8")
        # مُحمّل مُنسَّق (dropzone) لا حقل خام: label + input مخفيّ + عرض الاسم.
        assert "mig-drop" in html and "mig-file-native" in html
        assert "mig-fname" in html
        assert 'type="file"' in html                  # الحقل ما زال موجودًا (مخفيّ)


class TestOwnerOnly:
    def test_index_owner_ok(self, client):
        # أوّل أدمن = أصغر معرّف = المالك الرئيسي (fallback).
        u = _make_admin()
        _login(client, u)
        res = client.get("/admin/radius/migrate")
        assert res.status_code == 200
        assert "ترحيل".encode() in res.data or b"migrate" in res.data

    def test_non_owner_forbidden(self, client):
        owner = _make_admin()          # المالك (أصغر معرّف)
        other = _make_admin()          # مدير لاحق — ليس المالك
        _login(client, other)
        res = client.get("/admin/radius/migrate")
        assert res.status_code == 403

    def test_analyze_requires_login(self, client):
        res = client.post("/admin/radius/migrate/analyze")
        # غير مُسجَّل → إعادة توجيه لتسجيل الدخول.
        assert res.status_code in (301, 302, 303)


# ── التدفّق الكامل ────────────────────────────────────────────────────

class TestFlow:
    def test_analyze_plan_commit(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        data = {"file": (io.BytesIO(_sqlite_upload()), "src.db")}
        res = client.post("/admin/radius/migrate/analyze", data=data,
                          content_type="multipart/form-data",
                          headers={"X-CSRFToken": tok})
        assert res.status_code == 200, res.get_json()
        j = res.get_json()
        assert j["ok"] is True
        token = j["token"]
        sections = {m["section"] for m in j["analysis"]["matches"]}
        assert "subscribers" in sections and "plans" in sections

        # خطّة (للقراءة).
        res = client.post("/admin/radius/migrate/plan",
                          json={"token": token},
                          headers={"X-CSRFToken": tok})
        assert res.status_code == 200
        plan = res.get_json()["plan"]
        subs = next(s for s in plan["sections"] if s["section"] == "subscribers")
        assert subs["counts"]["new"] == 2

        # تنفيذ تجريبيّ — لا كتابة.
        from app.radius.db.repos import subscribers_repo
        res = client.post("/admin/radius/migrate/commit",
                          json={"token": token, "dry_run": True},
                          headers={"X-CSRFToken": tok})
        assert res.status_code == 200
        assert res.get_json()["report"]["dry_run"] is True
        assert subscribers_repo.count_subscribers(1) == 0

        # تنفيذ فعليّ.
        res = client.post("/admin/radius/migrate/commit",
                          json={"token": token, "dry_run": False},
                          headers={"X-CSRFToken": tok})
        assert res.status_code == 200
        rep = res.get_json()["report"]
        assert rep["totals"]["created"] >= 2
        assert subscribers_repo.count_subscribers(1) == 2
        ali = subscribers_repo.get_subscriber(1, "ali")
        assert ali is not None and ali.plan_id is not None  # العلاقة محلولة

    def test_commit_idempotent_via_routes(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        data = {"file": (io.BytesIO(_sqlite_upload()), "src.db")}
        j = client.post("/admin/radius/migrate/analyze", data=data,
                        content_type="multipart/form-data",
                        headers={"X-CSRFToken": tok}).get_json()
        token = j["token"]
        body = {"token": token, "dry_run": False}
        client.post("/admin/radius/migrate/commit", json=body,
                    headers={"X-CSRFToken": tok})
        r2 = client.post("/admin/radius/migrate/commit", json=body,
                         headers={"X-CSRFToken": tok}).get_json()
        from app.radius.db.repos import subscribers_repo
        assert subscribers_repo.count_subscribers(1) == 2   # لا تكرار
        assert r2["report"]["totals"]["merged"] >= 2

    def test_jobs_history(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        data = {"file": (io.BytesIO(_sqlite_upload()), "hist.db")}
        client.post("/admin/radius/migrate/analyze", data=data,
                    content_type="multipart/form-data",
                    headers={"X-CSRFToken": tok})
        res = client.get("/admin/radius/migrate/jobs")
        assert res.status_code == 200
        jobs = res.get_json()["jobs"]
        assert any(x["filename"] == "hist.db" for x in jobs)
