"""PARITY (round 6, team a) — subscriber form fields: app (API) vs web.

Every test here is a field that was shown on one side and silently lost,
ignored or refused on the other:

* the web edit form wiped the app-only personal fields (nationality, address,
  state, zip, payment_reference) and reset user_type «trial» → «subscriber»;
* the web edit form could not CLEAR an advanced MikroTik/RADIUS field (an
  emptied input was skipped and the old value merged back);
* the API dropped «قسم كلمة المرور» (login_without_password) and «عند بلوغ حدّ
  الأجهزة» (device_limit_mode) — both enforced by the policy engine and
  editable on the web only.

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from html.parser import HTMLParser
from uuid import uuid4

import pytest

TOKEN = "parity-a-subs-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "paritya.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "parity-a-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_pa")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-pa")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    application = create_app()
    with application.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield application


@pytest.fixture
def client(app):
    return app.test_client()


def _sub(username=None, **kw):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    username = username or "u_" + uuid4().hex[:8]
    subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=1, username=username, password="secret1",
        status="enabled", expire_at=datetime.utcnow() + timedelta(days=10),
        full_name="Test User", **kw))
    return subscribers_repo.get_subscriber(1, username)


def _get(username):
    from app.radius.db.repos import subscribers_repo
    return subscribers_repo.get_subscriber(1, username)


def _web_login(client):
    res = client.post("/admin/radius/login",
                      data={"username": "owner_pa", "password": "owner-pass-pa"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")


class _FormScraper(HTMLParser):
    """Collects what a browser would post for ``<form id="uf-form">``."""

    def __init__(self):
        super().__init__()
        self.inside = False
        self.fields: list[tuple[str, str]] = []
        self._select = None
        self._select_first = None
        self._select_chosen = None
        self._textarea = None
        self._text = ""

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("id") == "uf-form":
            self.inside = True
            return
        if not self.inside:
            return
        name = a.get("name")
        if tag == "input" and name and "disabled" not in a:
            typ = (a.get("type") or "text").lower()
            if typ in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((name, a.get("value", "on")))
            elif typ not in ("submit", "button", "file"):
                self.fields.append((name, a.get("value", "")))
        elif tag == "select" and name:
            self._select, self._select_first, self._select_chosen = name, None, None
        elif tag == "option" and self._select:
            val = a.get("value", "")
            if self._select_first is None:
                self._select_first = val
            if "selected" in a:
                self._select_chosen = val
        elif tag == "textarea" and name:
            self._textarea, self._text = name, ""

    def handle_data(self, data):
        if self._textarea:
            self._text += data

    def handle_endtag(self, tag):
        if tag == "form" and self.inside:
            self.inside = False
        elif tag == "select" and self._select:
            chosen = (self._select_chosen if self._select_chosen is not None
                      else self._select_first)
            self.fields.append((self._select, chosen or ""))
            self._select = None
        elif tag == "textarea" and self._textarea:
            self.fields.append((self._textarea, self._text))
            self._textarea = None


def _edit_form(client, username) -> list[tuple[str, str]]:
    html = client.get(f"/admin/radius/users/{username}/edit").get_data(as_text=True)
    p = _FormScraper()
    p.feed(html)
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    fields = [(k, v) for k, v in p.fields if k != "_csrf_token"]
    fields.append(("_csrf_token", csrf))
    return fields


def _set(fields, name, value):
    out = [(k, v) for k, v in fields if k != name]
    out.append((name, value))
    return out


def _post(client, username, fields):
    from werkzeug.datastructures import MultiDict
    res = client.post(f"/admin/radius/users/{username}", data=MultiDict(fields))
    assert res.status_code in (302, 303), (res.status_code,
                                           res.get_data(as_text=True)[:400])
    return res


# ═══════════ web edit keeps what only the app edits ═══════════

def test_web_edit_keeps_app_only_personal_fields(client, app):
    s = _sub(nationality="فلسطيني", address="شارع الجلاء 12", state="غزة",
             zip="00970", payment_reference="REF-77")
    _web_login(client)
    form = _set(_edit_form(client, s.username), "remark", "web note")
    _post(client, s.username, form)
    after = _get(s.username)
    assert after.remark == "web note"
    assert after.nationality == "فلسطيني"
    assert after.address == "شارع الجلاء 12"
    assert after.state == "غزة"
    assert after.zip == "00970"
    assert after.payment_reference == "REF-77"


def test_web_edit_keeps_trial_user_type(client, app):
    s = _sub(user_type="trial")
    _web_login(client)
    _post(client, s.username, _set(_edit_form(client, s.username), "remark", "x"))
    assert _get(s.username).user_type == "trial"


def test_web_edit_can_clear_advanced_network_field(client, app):
    meta = {"mikrotik": {"mikrotik_address_list": "vip"},
            "radius": {"framed_pool": "pool-a"}}
    s = _sub(metadata=json.dumps(meta))
    _web_login(client)
    form = _edit_form(client, s.username)
    assert ("mikrotik_address_list", "vip") in form
    form = _set(form, "mikrotik_address_list", "")
    _post(client, s.username, form)
    after = json.loads(_get(s.username).metadata or "{}")
    assert not (after.get("mikrotik") or {}).get("mikrotik_address_list")
    # an untouched field keeps its value
    assert (after.get("radius") or {}).get("framed_pool") == "pool-a"


def test_web_edit_untouched_keeps_app_metadata_groups(client, app):
    meta = {"general": {"notes": "from app"}, "mikrotik": {"profile": "p1"}}
    s = _sub(metadata=json.dumps(meta))
    _web_login(client)
    _post(client, s.username, _set(_edit_form(client, s.username), "remark", "y"))
    after = json.loads(_get(s.username).metadata or "{}")
    assert after["general"]["notes"] == "from app"
    assert after["mikrotik"]["profile"] == "p1"


# ═══════════ API accepts the web-only wired fields ═══════════

def test_api_patch_login_without_password_and_device_limit_mode(client, app):
    s = _sub()
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"login_without_password": True,
                             "device_limit_mode": "replace"})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert data["login_without_password"] is True
    assert data["device_limit_mode"] == "replace"
    after = _get(s.username)
    assert after.login_without_password is True
    assert after.device_limit_mode == "replace"
    assert after.password == "secret1"          # the stored password is kept
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"login_without_password": False, "device_limit_mode": ""})
    assert res.status_code == 200
    after = _get(s.username)
    assert after.login_without_password is False
    assert after.device_limit_mode == ""


def test_api_rejects_unknown_device_limit_mode(client, app):
    s = _sub()
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"device_limit_mode": "kick-everyone"})
    assert res.status_code == 422


def test_api_create_without_password_when_login_without_password(client, app):
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "nopw_" + uuid4().hex[:5], "login_without_password": True,
        "expire_at": None})
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["data"]["login_without_password"] is True
    # still required otherwise
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": "pw_" + uuid4().hex[:5], "expire_at": None})
    assert res.status_code == 422


def test_api_connection_schedule_round_trip(client, app):
    s = _sub()
    sched = {"windows": [{"days": ["sat", "sun"], "from": "08:00", "to": "16:00"}]}
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"connection_schedule": json.dumps(sched)})
    assert res.status_code == 200, res.get_json()
    data = res.get_json()["data"]
    assert json.loads(data["connection_schedule"]) == sched
    assert data["working_days"] == "sat,sun"
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"connection_schedule": ""})
    assert res.status_code == 200
    after = _get(s.username)
    assert after.connection_schedule == ""
    assert after.working_days == ""
