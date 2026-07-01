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

    def test_upload_progress_bar_real_xhr(self, client):
        # شريط تقدّم الرفع موجود ومدفوع بأحداث الرفع الحقيقيّة (لا مؤقّت وهميّ).
        u = _make_admin()
        _login(client, u)
        html = client.get("/admin/radius/migrate").data.decode("utf-8")
        # عناصر الشريط.
        assert "mig-progress" in html and "mig-progress-fill" in html
        assert "mig-progress-cancel" in html            # خيار الإلغاء
        assert "is-analyzing" in html                   # طور «جارٍ التحليل…»
        # مدفوع ببايتات فعليّة عبر XMLHttpRequest.upload.onprogress.
        assert "XMLHttpRequest" in html
        assert "xhr.upload.onprogress" in html
        assert "e.loaded" in html and "e.total" in html
        # ليس مؤقّتًا وهميًّا لتحريك الشريط.
        assert "setInterval" not in html

    def test_commit_progress_and_status_wired(self, client):
        # شريط تقدّم التنفيذ + استطلاع الحالة الخلفيّة موجودان.
        u = _make_admin()
        _login(client, u)
        html = client.get("/admin/radius/migrate").data.decode("utf-8")
        assert "mig-commit-progress" in html and "mig-commit-fill" in html
        assert "commit_status" in html                  # نقطة الاستطلاع
        assert "pollCommit" in html                     # حلقة الاستطلاع
        assert "COMMIT_BUSY" in html                    # منع الإرسال المزدوج

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

    def test_index_both_slash_variants(self, client):
        # /migrate و /migrate/ كلاهما يخدم الصفحة (لا 404 على الشرطة الأخيرة).
        u = _make_admin()
        _login(client, u)
        # بلا شرطة → 200 مباشرة.
        assert client.get("/admin/radius/migrate").status_code == 200
        # بشرطة → 200 (بعد إعادة توجيه Flask للشكل القانونيّ)، ليس 404.
        res = client.get("/admin/radius/migrate/", follow_redirects=True)
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

def _analyze(client, tok, blob=None, name="src.db"):
    data = {"file": (io.BytesIO(blob or _sqlite_upload()), name)}
    return client.post("/admin/radius/migrate/analyze", data=data,
                       content_type="multipart/form-data",
                       headers={"X-CSRFToken": tok}).get_json()


def _commit_and_wait(client, token, tok, dry_run=False, tries=60):
    # التنفيذ خلفيّ: POST يبدأ، ثمّ نستطلع الحالة حتى الانتهاء ونُعيد التقرير.
    import time
    r = client.post("/admin/radius/migrate/commit",
                    json={"token": token, "dry_run": dry_run},
                    headers={"X-CSRFToken": tok})
    assert r.status_code == 200, r.get_json()
    assert r.get_json().get("running") is True
    for _ in range(tries):
        st = client.get("/admin/radius/migrate/commit_status?token=" + token).get_json()
        if st["status"] != "running":
            return st
        time.sleep(0.15)
    raise AssertionError("commit did not finish in time")


class TestFlow:
    def test_analyze_plan_commit(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        j = _analyze(client, tok)
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

        # تنفيذ تجريبيّ (خلفيّ) — لا كتابة.
        from app.radius.db.repos import subscribers_repo
        st = _commit_and_wait(client, token, tok, dry_run=True)
        assert st["status"] == "dry_done"
        assert st["report"]["dry_run"] is True
        assert subscribers_repo.count_subscribers(1) == 0

        # تنفيذ فعليّ (خلفيّ) + تقدّم.
        st = _commit_and_wait(client, token, tok, dry_run=False)
        assert st["status"] == "committed"
        rep = st["report"]
        assert rep["totals"]["created"] >= 2
        assert subscribers_repo.count_subscribers(1) == 2
        ali = subscribers_repo.get_subscriber(1, "ali")
        assert ali is not None and ali.plan_id is not None  # العلاقة محلولة

    def test_commit_idempotent_via_routes(self, client):
        u = _make_admin()
        _login(client, u)
        tok = _csrf(client)
        token = _analyze(client, tok)["token"]
        _commit_and_wait(client, token, tok, dry_run=False)
        st = _commit_and_wait(client, token, tok, dry_run=False)
        from app.radius.db.repos import subscribers_repo
        assert subscribers_repo.count_subscribers(1) == 2   # لا تكرار
        assert st["report"]["totals"]["merged"] >= 2

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
