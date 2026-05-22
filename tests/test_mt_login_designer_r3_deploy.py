"""R3 — Deploy hotspot login.html to the router."""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_r3_")
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
    u = f"r3_{uuid4().hex[:10]}"
    admins_repo.create_admin(
        username=u, password="r3-pass", full_name="R3 Tester",
        is_super_admin=True,
    )
    res = client.post(
        "/admin/radius/login",
        data={"username": u, "password": "r3-pass"},
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
                   VALUES (?, 1, 'r3-rtr', '203.0.113.19', 'sek',
                           'mikrotik', 'hotspot', 1, ?, 'direct',
                           'hr-test', 'pw')""",
                (nas_id, now),
            )


class _FakeRouter:
    """Records every call. `file_print_rows` defines what /file/print
    returns; everything else is a no-op."""

    def __init__(self, *, file_print_rows: list[dict] | None = None,
                 raise_on: dict[str, str] | None = None):
        self.file_print_rows = file_print_rows or []
        self.raise_on = raise_on or {}
        self.calls: list[tuple[str, dict]] = []

    def connect(self): pass
    def close(self):   pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        if path in self.raise_on:
            raise RuntimeError(self.raise_on[path])
        if path == "/file/print":
            return list(self.file_print_rows)
        return []


# ─── Service-layer ────────────────────────────────────────────


def test_deploy_creates_new_file_when_absent():
    from app.radius.services.hotspot_templates import (
        deploy_login, DEFAULT_LOGIN_PATH,
    )
    fake = _FakeRouter(file_print_rows=[])
    res = deploy_login(fake, "classic", {})
    assert res.ok is True
    assert res.path == DEFAULT_LOGIN_PATH
    assert res.bytes > 0
    # /file/print was the first call, /file/add was the second.
    paths = [c[0] for c in fake.calls]
    assert paths == ["/file/print", "/file/add"]
    add_attrs = fake.calls[1][1]
    assert add_attrs["name"] == DEFAULT_LOGIN_PATH
    assert "<!DOCTYPE html>" in add_attrs["contents"]


def test_deploy_overwrites_existing_file_via_file_set():
    from app.radius.services.hotspot_templates import (
        deploy_login, DEFAULT_LOGIN_PATH,
    )
    fake = _FakeRouter(file_print_rows=[
        {".id": "*55", "name": DEFAULT_LOGIN_PATH},
    ])
    res = deploy_login(fake, "card", {})
    assert res.ok is True
    paths = [c[0] for c in fake.calls]
    assert paths == ["/file/print", "/file/set"]
    set_attrs = fake.calls[1][1]
    assert set_attrs[".id"] == "*55"
    assert "<!DOCTYPE html>" in set_attrs["contents"]


def test_deploy_propagates_print_failure():
    from app.radius.services.hotspot_templates import deploy_login
    fake = _FakeRouter(raise_on={"/file/print": "perm denied"})
    res = deploy_login(fake, "classic", {})
    assert res.ok is False
    assert "/file/print" in res.error


def test_deploy_propagates_add_failure():
    from app.radius.services.hotspot_templates import deploy_login
    fake = _FakeRouter(file_print_rows=[],
                       raise_on={"/file/add": "disk full"})
    res = deploy_login(fake, "classic", {})
    assert res.ok is False
    assert "disk full" in res.error


def test_deploy_rejects_template_missing_routeros_placeholders():
    """Defense-in-depth: even if a future template is added that
    drops a required RouterOS placeholder, deploy_login must
    refuse rather than upload a broken page."""
    from app.radius.services import hotspot_templates as ht
    from app.radius.services.hotspot_templates import deploy_login
    # Add a malformed template to the in-memory catalogue. We
    # monkeypatch TEMPLATES_BY_SLUG only for this test.
    bad = ht.LoginTemplate(
        slug="broken-test", name_ar="x", description_ar="",
        html="<html>no placeholders here</html>",
    )
    ht.TEMPLATES_BY_SLUG[bad.slug] = bad
    try:
        fake = _FakeRouter()
        res = deploy_login(fake, "broken-test", {})
        assert res.ok is False
        assert "placeholders" in res.error
        # And the wire client was NOT touched.
        assert fake.calls == []
    finally:
        del ht.TEMPLATES_BY_SLUG[bad.slug]


# ─── Route ─────────────────────────────────────────────────────


def _post_deploy(client, *, nas_id, confirm=True):
    token = _csrf(client)
    data = {"_csrf_token": token}
    if confirm:
        data["confirm"] = "1"
    return client.post(
        f"/admin/radius/mt/{nas_id}/login-designer/deploy",
        data=data,
    )


def test_deploy_route_refuses_without_confirm(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    fake = _FakeRouter()
    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas_id: fake)
    res = _post_deploy(client, nas_id=1, confirm=False)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "يجب تأكيد عملية النشر" in html
    assert fake.calls == [], "no router calls when refused"


def test_deploy_route_uses_saved_design(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    # Pre-save a design so we know which slug the deploy uses.
    with app.app_context():
        from app.radius.db.repos import hotspot_designs_repo as r
        r.save_design(1, 1, template_slug="dark",
                      variables={"TENANT_NAME": "Dark Co"})
    fake = _FakeRouter(file_print_rows=[])
    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas_id: fake)
    res = _post_deploy(client, nas_id=1, confirm=True)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-designer-deploy-result" in html
    # The uploaded HTML used the saved variables.
    add_call = next(c for c in fake.calls if c[0] == "/file/add")
    assert "Dark Co" in add_call[1]["contents"]


def test_deploy_route_surfaces_failure(app, client, monkeypatch):
    _seed(app, nas_id=1)
    _login(client)
    # Default design (classic + defaults) — connect fails on print.
    fake = _FakeRouter(raise_on={"/file/print": "no perm"})
    from app.radius.routes import mt_login_designer as routes_pkg
    monkeypatch.setattr(routes_pkg, "_connect_client",
                        lambda nas_id: fake)
    res = _post_deploy(client, nas_id=1, confirm=True)
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "data-mt-designer-deploy-result" in html
    # The error text reaches the page.
    assert "no perm" in html
