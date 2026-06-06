"""شريط تقدّم النشر — نقطة البثّ NDJSON (deploy/stream).

تتحقّق أن النشر يبثّ حالة كل مرحلة، وأن الفشل يحمل رسالة السبب
الحقيقية على الخطوة الفاشلة، وأن طلبًا بلا تأكيد لا يلمس الراوتر."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_prog_")
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


def _login(client) -> None:
    from app.radius.db.repos import admins_repo
    u = f"prog_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="prog-pass", full_name="Progress Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "prog-pass"},
        follow_redirects=False,
    )
    assert res.status_code in {302, 303}


def _csrf(client) -> str:
    client.get("/admin/radius/mt/operations")
    with client.session_transaction() as sess:
        return sess["_csrf_token"]


def _seed(app, *, nas_id: int = 1) -> None:
    with app.app_context():
        from app.radius.db.connection import transaction
        now = datetime.utcnow().isoformat() + "Z"
        with transaction() as c:
            c.execute(
                """INSERT INTO nas_devices
                    (id, tenant_id, name, address, secret, vendor,
                     nas_type, enabled, created_at, connection_mode,
                     api_user, api_password)
                   VALUES (?, 1, 'prog-rtr', '203.0.113.20', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


class _FakeRouter:
    def __init__(self, *, file_print_rows=None, raise_on=None):
        self.file_print_rows = file_print_rows or []
        self.raise_on = raise_on or {}
        self.calls = []

    def connect(self): pass
    def close(self):   pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        if path in self.raise_on:
            raise RuntimeError(self.raise_on[path])
        if path == "/file/print":
            return list(self.file_print_rows)
        return []


def _events(client, *, nas_id, confirm=True):
    """يرسل طلب البثّ ويُرجع قائمة أحداث NDJSON المُحلَّلة."""
    token = _csrf(client)
    data = {"_csrf_token": token}
    if confirm:
        data["confirm"] = "1"
    res = client.post(
        f"/admin/radius/mt/{nas_id}/login-designer/deploy/stream",
        data=data,
    )
    assert res.status_code == 200
    assert res.mimetype == "application/x-ndjson"
    out = []
    for line in res.get_data(as_text=True).splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def test_designer_page_renders_progress_ui_and_binds_submit(app, client):
    """شريط التقدّم لا بدّ أن يُصيَّر في صفحة المصمّم ويكون مربوطًا بزر
    «نشر على الراوتر»: حاوية التقدّم + رابط البثّ + سكربت يعترض الإرسال،
    وكتلة <script> مستقلّة فلا يمنعها خطأ سكربت آخر."""
    _seed(app, nas_id=1)
    _login(client)
    res = client.get("/admin/radius/mt/1/login-designer")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    # عناصر شريط التقدّم حاضرة.
    assert "data-mt-deploy-progress" in html
    assert "data-mt-deploy-steps" in html
    assert 'data-mt-designer-deploy-form' in html
    # رابط البثّ يشير لنقطة deploy/stream الصحيحة.
    assert "/login-designer/deploy/stream" in html
    assert "data-mt-deploy-stream-url" in html
    # السكربت يعترض الإرسال ويبثّ.
    assert 'addEventListener("submit"' in html
    assert "streamDeploy" in html
    # كتلة شريط التقدّم معزولة في <script> مستقلّ: نتأكد أن streamDeploy
    # تأتي بعد </script> تُغلق الكتلة السابقة (فلا يمنعها خطأ سكربت آخر).
    head = html[:html.index("function streamDeploy")]
    assert "</script>" in head, "deploy script not isolated in its own block"


def test_stream_emits_plan_and_done_on_success(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    fake = _FakeRouter(file_print_rows=[])
    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client", lambda nas_id: fake)

    evs = _events(client, nas_id=1, confirm=True)
    types = [e["type"] for e in evs]
    assert "plan" in types
    assert types[-1] == "done"

    plan = next(e for e in evs if e["type"] == "plan")
    keys = [s["key"] for s in plan["steps"]]
    # الخطوات الأساسية حاضرة بهذا الترتيب (قد تتخلّلها خطوة «assets»
    # عند توفّر اعتماد FTP — الـnas في الاختبار له api_user).
    core = ["prepare", "connect", "login", "errors", "companions"]
    positions = [keys.index(k) for k in core]
    assert positions == sorted(positions), f"core steps out of order: {keys}"

    # خطوة login انتهت بنجاح، والخلاصة ok.
    login_oks = [e for e in evs
                 if e["type"] == "step" and e["key"] == "login"
                 and e["status"] == "ok"]
    assert login_oks, "login step never reported ok"
    done = evs[-1]
    assert done["ok"] is True
    # فعلًا رُفع login.html عبر API (دفع مباشر، لا fetch).
    assert any(c[0] == "/file/add" for c in fake.calls)


def test_stream_surfaces_real_error_on_failed_step(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    # /file/print يرمي → deploy_login يفشل برسالة تحمل السبب الحقيقي.
    fake = _FakeRouter(raise_on={"/file/print": "no perm"})
    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client", lambda nas_id: fake)

    evs = _events(client, nas_id=1, confirm=True)
    failed = [e for e in evs
              if e["type"] == "step" and e["status"] == "failed"]
    assert failed, "no failed step surfaced"
    # رسالة السبب الحقيقية تصل ضمن تفاصيل الخطوة الفاشلة.
    assert any("no perm" in (e.get("detail") or "") for e in failed)
    assert evs[-1]["type"] == "done"
    assert evs[-1]["ok"] is False


def test_stream_refuses_without_confirm_without_touching_router(
        app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    fake = _FakeRouter()
    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client", lambda nas_id: fake)

    evs = _events(client, nas_id=1, confirm=False)
    done = evs[-1]
    assert done["type"] == "done"
    assert done["ok"] is False
    assert "تأكيد" in done["error"]
    assert fake.calls == [], "router must not be touched when unconfirmed"
