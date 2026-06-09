"""S8 — Backup center contract."""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_s8_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_BACKUP_DIR",
                       os.path.join(tmp, "backups"))
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
    u = f"s8_{uuid4().hex[:8]}"
    admins_repo.create_admin(
        username=u, password="s8-pass", full_name="S8",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "s8-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess.get("_csrf_token") or ""


def _seed_nas(app, *, nas_id=1, enabled=True):
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, ?, ?, 'sek', 'mikrotik', 'hotspot',
                           ?, ?, 'direct', 'hr', 'p')""",
                (nas_id, f"s8-rtr-{nas_id}",
                 f"203.0.113.{nas_id}",
                 1 if enabled else 0, now),
            )


# ─── Migration + repo ─────────────────────────────────────────


def test_backups_table_exists(app):
    with app.app_context():
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='router_backups'"
        ).fetchone()
        assert row is not None


def test_repo_record_and_read_back(app):
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        bid = r.record(
            tenant_id=1, router_id=42,
            backup_type="binary", filename="x.backup",
            size_bytes=1024, checksum="deadbeef",
            sensitive=True, status="success",
            created_by=1,
        )
        row = r.get_by_id(1, bid)
        assert row is not None
        assert row["sensitive"] == 1
        assert row["status"] == "success"
        assert row["size_bytes"] == 1024


def test_repo_list_for_router(app):
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        r.record(tenant_id=1, router_id=10, backup_type="binary",
                 filename="a")
        r.record(tenant_id=1, router_id=10, backup_type="binary",
                 filename="b")
        r.record(tenant_id=1, router_id=20, backup_type="binary",
                 filename="c")
        rows = r.list_for_router(1, 10)
        assert len(rows) == 2
        # Newest first.
        assert rows[0]["filename"] == "b"


# ─── Routes ───────────────────────────────────────────────────


def test_list_route_login_guarded(client):
    res = client.get("/admin/radius/mt/1/backups",
                     follow_redirects=False)
    assert res.status_code in {302, 303}


def test_list_route_renders(app, client):
    _seed_nas(app)
    _login(client)
    res = client.get("/admin/radius/mt/1/backups")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-backups-page" in html
    assert "data-mt-backups-save-card" in html
    assert "data-mt-backups-empty" in html
    # Restore-disabled banner is rendered.
    assert "data-mt-backups-restore-disabled" in html


def test_save_route_writes_record_and_audit(app, client, monkeypatch):
    _seed_nas(app, nas_id=2)
    _login(client)
    # Stub the wire client + backup_save.
    from app.radius.routes import mt_backups as routes_pkg
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult

    class _FakeClient:
        def __init__(self, **kw): pass
        def connect(self): pass
        def close(self): pass
    monkeypatch.setattr(routes_pkg, "MikrotikClient", _FakeClient)
    # نلتقط الوسيط name= للتأكد أن المسار يستدعي الخدمة بالتوقيع الصحيح
    # (name بلا امتداد) بدل filename الخاطئ.
    seen = {}

    def _fake_save(nas, *, name):
        seen["name"] = name
        return MtResult(ok=True, data=[])
    monkeypatch.setattr(mac, "backup_save", _fake_save)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/2/backups/save",
                      data={"_csrf_token": token},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    # بلا اسم من المستخدم → اسم تلقائي بلا امتداد .backup
    assert seen["name"].startswith("nas-2-")
    assert not seen["name"].endswith(".backup")
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        from app.radius.db.repos import audit_repo
        rows = r.list_for_router(1, 2)
        assert len(rows) == 1
        assert rows[0]["status"] == "success"
        # Audit row recorded.
        ar = audit_repo.recent(1, action="mt.backup.save")
        assert len(ar) >= 1
        assert ar[0]["router_id"] == 2


def test_save_records_failure_when_wire_fails(app, client, monkeypatch):
    _seed_nas(app, nas_id=3)
    _login(client)
    from app.radius.routes import mt_backups as routes_pkg
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult

    class _FakeClient:
        def __init__(self, **kw): pass
        def connect(self):
            raise RuntimeError("router unreachable")
        def close(self): pass
    monkeypatch.setattr(routes_pkg, "MikrotikClient", _FakeClient)
    monkeypatch.setattr(
        mac, "backup_save",
        lambda nas, *, name: MtResult(ok=True, data=[]),
    )
    token = _csrf(client)
    res = client.post("/admin/radius/mt/3/backups/save",
                      data={"_csrf_token": token},
                      follow_redirects=True)
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        rows = r.list_for_router(1, 3)
        assert rows
        assert rows[0]["status"] == "failed"
        # النص الخام (الاستثناء) يُحفظ في سجل التدقيق/السجل المحلي…
        assert "unreachable" in rows[0]["error_message"]
    # …لكن المستخدم يرى رسالة عربية ودودة لا نص الاستثناء الخام.
    html = res.get_data(as_text=True)
    assert "تعذّر الاتصال بالراوتر" in html
    # نص الاستثناء الخام لا يُسرَّب للمستخدم.
    assert "router unreachable" not in html


def test_save_uses_user_supplied_name(app, client, monkeypatch):
    """اسم اختياري من المستخدم → يُمرَّر إلى الخدمة بـname= بعد التنظيف
    (بلا امتداد .backup) ويُخزَّن في السجل مع الامتداد."""
    _seed_nas(app, nas_id=7)
    _login(client)
    from app.radius.routes import mt_backups as routes_pkg
    from app.radius.services import mikrotik_admin_client as mac
    from app.radius.services.mikrotik_admin_client import MtResult

    class _FakeClient:
        def __init__(self, **kw): pass
        def connect(self): pass
        def close(self): pass
    monkeypatch.setattr(routes_pkg, "MikrotikClient", _FakeClient)
    seen = {}

    def _fake_save(nas, *, name):
        seen["name"] = name
        return MtResult(ok=True, data=[])
    monkeypatch.setattr(mac, "backup_save", _fake_save)
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/7/backups/save",
        data={"_csrf_token": token, "backup_name": "pre-upgrade"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}
    # الاسم وصل للراوتر كما هو بلا امتداد.
    assert seen["name"] == "pre-upgrade"
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        rows = r.list_for_router(1, 7)
        assert rows
        # السجل المحلي يحمل الامتداد .backup.
        assert rows[0]["filename"] == "pre-upgrade.backup"


def test_save_rejects_bad_name_with_arabic_message(app, client, monkeypatch):
    """اسم بحروف عربية/رموز يرفضه _sanitize_backup_name → رسالة عربية
    ودودة بدل خطأ بايثون خام، ولا نسخة ناجحة."""
    _seed_nas(app, nas_id=8)
    _login(client)
    from app.radius.routes import mt_backups as routes_pkg

    class _FakeClient:
        def __init__(self, **kw): pass
        def connect(self): pass
        def close(self): pass
    monkeypatch.setattr(routes_pkg, "MikrotikClient", _FakeClient)
    # نترك backup_save الحقيقية تعمل: تُنظّف الاسم وترجع خطأ عربي.
    token = _csrf(client)
    res = client.post(
        "/admin/radius/mt/8/backups/save",
        data={"_csrf_token": token, "backup_name": "نسخة..خطيرة"},
        follow_redirects=True,
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "فشل حفظ النسخة الاحتياطية" in html
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        rows = r.list_for_router(1, 8)
        assert rows
        assert rows[0]["status"] == "failed"


def test_list_renders_optional_name_field(app, client):
    """خانة اسم النسخة الاختيارية ظاهرة في بطاقة الحفظ."""
    _seed_nas(app, nas_id=9)
    _login(client)
    res = client.get("/admin/radius/mt/9/backups")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert 'name="backup_name"' in html
    assert "data-mt-backups-save-name" in html


def test_save_refuses_disabled_router(app, client):
    _seed_nas(app, nas_id=4, enabled=False)
    _login(client)
    token = _csrf(client)
    res = client.post("/admin/radius/mt/4/backups/save",
                      data={"_csrf_token": token},
                      follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.db.repos import router_backups_repo as r
        rows = r.list_for_router(1, 4)
    # No record created — early return.
    assert rows == []


def test_restore_plan_inspects_file_without_applying(app, client):
    _seed_nas(app, nas_id=5)
    _login(client)
    token = _csrf(client)
    fake_file = (io.BytesIO(b"\x00\x01\x02RouterOSBackup..."),
                 "test.backup")
    res = client.post(
        "/admin/radius/mt/5/backups/restore-plan",
        data={"_csrf_token": token, "backup_file": fake_file},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-backups-restore-plan" in html
    assert 'data-mt-backups-restore-row="filename"' in html
    assert 'data-mt-backups-restore-row="appears_binary"' in html
    assert "{&#34;filename&#34;" not in html
    assert "data-mt-backups-restore-disabled" in html
