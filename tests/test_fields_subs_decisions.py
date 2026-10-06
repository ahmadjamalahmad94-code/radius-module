"""Owner decisions 2026-10-06 — subscriber form dead fields (team «subs»).

REMOVE (no reader anywhere): Pool, VLAN, ملف اتصال الجهاز, the web-only NAS
IP / service name / port, called-station id, MikroTik profile / rate_limit /
ip_pool / comment, WinBox group, notifications + subscription metadata,
«تعطيل تلقائي بعد أول استخدام», session / idle timeout, and the separate
PPPoE name/password (a PPPoE subscriber logs in with its own login).
  → gone from the web form; the web save keeps the stored value; the API
    ignores the keys silently (old app builds) and never wipes stored data.

WIRE:
  • «الجلسات المتزامنة» (override_concurrent) is visible on the web form and
    enforced at authorize.
  • The fixed address → Framed-IP-Address in the Access-Accept (through
    /api/v1/internal/auth); IPv4 only, unique per tenant. Follow-up
    2026-10-06: «IP PPPoE» (pppoe_ip) was MERGED into «IP ثابت» (static_ip)
    — one field; the API maps the old key (test_fields_followup.py).

Run this file alone (per-file isolation).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from html.parser import HTMLParser
from uuid import uuid4

import pytest

TOKEN = "fields-subs-token"
AUTH = {"Authorization": "Bearer " + TOKEN}


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "fields_subs.db")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_INTERNAL_SECRET", raising=False)
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", TOKEN)
    monkeypatch.setenv("FLASK_SECRET", "fields-subs-secret")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_USER", "owner_fs")
    monkeypatch.setenv("HOBERADIUS_BOOTSTRAP_ADMIN_PASS", "owner-pass-fs")
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
                      data={"username": "owner_fs", "password": "owner-pass-fs"})
    assert res.status_code in {302, 303}, res.status_code
    client.get("/admin/radius/users")


class _FormScraper(HTMLParser):
    """What a browser would post for ``<form id="uf-form">`` (+ input types)."""

    def __init__(self):
        super().__init__()
        self.inside = False
        self.fields: list[tuple[str, str]] = []
        self.types: dict[str, str] = {}
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
            self.types.setdefault(name, typ)
            if typ in ("checkbox", "radio"):
                if "checked" in a:
                    self.fields.append((name, a.get("value", "on")))
            elif typ not in ("submit", "button", "file"):
                self.fields.append((name, a.get("value", "")))
        elif tag == "select" and name:
            self.types.setdefault(name, "select")
            self._select, self._select_first, self._select_chosen = name, None, None
        elif tag == "option" and self._select:
            val = a.get("value", "")
            if self._select_first is None:
                self._select_first = val
            if "selected" in a:
                self._select_chosen = val
        elif tag == "textarea" and name:
            self.types.setdefault(name, "textarea")
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


def _scrape(client, url):
    p = _FormScraper()
    p.feed(client.get(url).get_data(as_text=True))
    with client.session_transaction() as sess:
        csrf = sess.get("_csrf_token", "")
    fields = [(k, v) for k, v in p.fields if k != "_csrf_token"]
    fields.append(("_csrf_token", csrf))
    return fields, p.types


def _set(fields, name, value):
    out = [(k, v) for k, v in fields if k != name]
    out.append((name, value))
    return out


def _post(client, url, fields):
    from werkzeug.datastructures import MultiDict
    return client.post(url, data=MultiDict(fields))


def _internal_auth(client, username, password="secret1"):
    res = client.post("/api/v1/internal/auth", json={
        "User-Name": username, "User-Password": password,
        "Calling-Station-Id": "AA:BB:CC:00:00:01",
        "NAS-IP-Address": "10.0.0.1", "NAS-Port-Type": "Virtual"})
    assert res.status_code == 200, res.get_data(as_text=True)[:300]
    return res.get_json()


_RETIRED_INPUTS = (
    "pool", "vlan_id", "device_connection_file", "pppoe_username",
    "pppoe_password", "nas_ip_address", "nas_port_id", "service_name",
    "mikrotik_winbox_group",
)


# ═══════════ REMOVE — web form ═══════════

def test_web_form_has_no_retired_inputs(client, app):
    s = _sub()
    _web_login(client)
    for url in ("/admin/radius/users/new", f"/admin/radius/users/{s.username}/edit"):
        _fields, types = _scrape(client, url)
        present = sorted(n for n in _RETIRED_INPUTS if n in types)
        assert present == [], (url, present)
        # notes/remark and the wired fields stay
        assert "static_ip" in types and "remark" in types
        assert "pppoe_ip" not in types      # merged into «IP ثابت» (follow-up)


def test_web_edit_keeps_stored_retired_values(client, app):
    meta = {"mikrotik": {"mikrotik_winbox_group": "full", "profile": "p1"},
            "radius": {"nas_ip_address": "10.0.0.9", "nas_port_id": "ether3",
                       "service_name": "svc1", "session_timeout": 600},
            "general": {"notes": "keep me", "tags": ["vip"]}}
    s = _sub(pool="pool-x", vlan_id=7, device_connection_file="f.rsc",
             pppoe_username="old-ppp", pppoe_password="old-ppp-pw",
             metadata=json.dumps(meta))
    _web_login(client)
    fields, _types = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    # a forged POST of a retired column is ignored too
    fields = _set(_set(fields, "remark", "web save"), "vlan_id", "99")
    res = _post(client, f"/admin/radius/users/{s.username}", fields)
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    after = _get(s.username)
    assert after.remark == "web save"
    assert (after.pool, after.vlan_id, after.device_connection_file) == ("pool-x", 7, "f.rsc")
    assert (after.pppoe_username, after.pppoe_password) == ("old-ppp", "old-ppp-pw")
    m = json.loads(after.metadata or "{}")
    assert m["mikrotik"]["mikrotik_winbox_group"] == "full"
    assert m["radius"]["nas_ip_address"] == "10.0.0.9"
    assert m["radius"]["nas_port_id"] == "ether3"
    assert m["radius"]["service_name"] == "svc1"
    assert m["general"] == {"notes": "keep me", "tags": ["vip"]}


# ═══════════ REMOVE — API (old app builds still send the keys) ═══════════

def test_api_patch_ignores_retired_columns_silently(client, app):
    s = _sub(pool="pool-x", vlan_id=7, device_connection_file="f.rsc",
             pppoe_username="old-ppp", pppoe_password="old-ppp-pw")
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={
        "pool": "new-pool", "vlan_id": 12, "device_connection_file": "x",
        "pppoe_username": "new-ppp", "pppoe_password": "new-ppp-pw",
        "remark": "from app"})
    assert res.status_code == 200, res.get_json()
    after = _get(s.username)
    assert after.remark == "from app"
    assert (after.pool, after.vlan_id, after.device_connection_file) == ("pool-x", 7, "f.rsc")
    assert (after.pppoe_username, after.pppoe_password) == ("old-ppp", "old-ppp-pw")


def test_api_create_ignores_retired_columns(client, app):
    name = "c_" + uuid4().hex[:6]
    res = client.post("/api/v1/accounts", headers=AUTH, json={
        "username": name, "password": "secret1", "expire_at": None,
        "pool": "p", "vlan_id": 5, "device_connection_file": "f",
        "pppoe_username": "x", "pppoe_password": "y"})
    assert res.status_code == 201, res.get_json()
    after = _get(name)
    assert (after.pool or "", after.vlan_id or 0, after.device_connection_file or "") == ("", 0, "")
    assert (after.pppoe_username or "", after.pppoe_password or "") == ("", "")


def test_api_metadata_retired_keys_pinned_notes_tags_kept(client, app):
    stored = {"radius": {"session_timeout": 600, "framed_pool": "fp"},
              "notifications": {"on_login": True, "email": "a@b.co"}}
    s = _sub(metadata=json.dumps(stored))
    old_build = {
        "mikrotik": {"profile": "P", "rate_limit": "5M/5M", "ip_pool": "ip",
                     "comment": "c"},
        "radius": {"session_timeout": 30, "idle_timeout": 60,
                   "called_station_id": "ap1", "framed_pool": "fp2"},
        "advanced": {"disable_on_first_use": True},
        "notifications": {"on_login": False, "email": "z@z.co", "mobile": "0599"},
        "subscription": {"type": "rolling", "days": 30},
        "general": {"notes": "n1", "tags": ["a", "b"]},
    }
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"metadata": old_build})
    assert res.status_code == 200, res.get_json()
    m = json.loads(_get(s.username).metadata or "{}")
    # retired: stored values unchanged, new ones never written
    assert m["radius"]["session_timeout"] == 600
    assert "idle_timeout" not in m["radius"]
    assert "called_station_id" not in m["radius"]
    assert m["notifications"] == {"on_login": True, "email": "a@b.co"}
    assert m.get("mikrotik", {}) == {}
    assert m.get("advanced", {}) == {}
    assert m.get("subscription", {}) == {}
    # kept / wired keys are written as before
    assert m["radius"]["framed_pool"] == "fp2"
    assert m["general"] == {"notes": "n1", "tags": ["a", "b"]}


# ═══════════ WIRE — «الجلسات المتزامنة» on the web ═══════════

def test_web_form_shows_and_saves_override_concurrent(client, app):
    s = _sub(device_count=5)
    _web_login(client)
    fields, types = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    assert types.get("override_concurrent") == "number"      # visible, not hidden
    html = client.get(f"/admin/radius/users/{s.username}/edit").get_data(as_text=True)
    assert "الجلسات المتزامنة" in html
    res = _post(client, f"/admin/radius/users/{s.username}",
                _set(fields, "override_concurrent", "1"))
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    assert _get(s.username).override_concurrent == 1
    _fields_new, types_new = _scrape(client, "/admin/radius/users/new")
    assert types_new.get("override_concurrent") == "number"

    # effective at authorize: one live session already open (another device)
    from app.radius.db.connection import db
    now = datetime.utcnow().isoformat() + "Z"
    db().execute(
        "INSERT INTO radacct (tenant_id, username, acctsessionid, callingstationid, "
        " nasipaddress, acctstarttime, acctupdatetime, acctstoptime) "
        "VALUES (1,?,?,?,?,?,?,NULL)",
        (s.username, "sess-oc-1", "AA:BB:CC:00:00:99", "10.0.0.1", now, now))
    from app.radius.services.policy_engine import AuthRequest, authorize
    d = authorize(AuthRequest(username=s.username, password="secret1", tenant_id=1,
                              calling_station_id="AA:BB:CC:00:00:01"))
    assert not d.ok and d.reason == "concurrent_limit"
    # back to 0 → device_count (5) applies again
    fields, _t = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    _post(client, f"/admin/radius/users/{s.username}", _set(fields, "override_concurrent", "0"))
    assert _get(s.username).override_concurrent == 0
    d = authorize(AuthRequest(username=s.username, password="secret1", tenant_id=1,
                              calling_station_id="AA:BB:CC:00:00:01"))
    assert d.ok, d.reason


# ═══════════ WIRE — the ONE fixed address → Framed-IP-Address ═══════════
# Follow-up 2026-10-06: «IP PPPoE» merged into «IP ثابت» (static_ip).

def test_internal_auth_reply_carries_static_framed_ip(client, app):
    with_ip = _sub(static_ip="10.9.0.7")
    without = _sub()
    out = _internal_auth(client, with_ip.username)
    assert out["control:Auth-Type"] == "Accept", out
    assert out.get("reply:Framed-IP-Address") == "10.9.0.7"
    out = _internal_auth(client, without.username)
    assert out["control:Auth-Type"] == "Accept", out
    assert "reply:Framed-IP-Address" not in out


def test_static_ip_set_from_web_and_api_reaches_the_reply(client, app):
    s = _sub()
    _web_login(client)
    fields, _types = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    res = _post(client, f"/admin/radius/users/{s.username}", _set(fields, "static_ip", "10.9.1.1"))
    assert res.status_code in (302, 303), res.get_data(as_text=True)[:400]
    assert _internal_auth(client, s.username).get("reply:Framed-IP-Address") == "10.9.1.1"
    # an old app build's pppoe_ip alone lands in «IP ثابت»
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH,
                       json={"pppoe_ip": "10.9.1.2"})
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["data"]["static_ip"] == "10.9.1.2"
    assert _internal_auth(client, s.username).get("reply:Framed-IP-Address") == "10.9.1.2"
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"static_ip": ""})
    assert res.status_code == 200
    assert "reply:Framed-IP-Address" not in _internal_auth(client, s.username)


def test_static_ip_conflicts_refused_in_arabic(client, app):
    other = _sub(static_ip="10.20.0.5")
    s = _sub()

    def patch(body):
        return client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json=body)

    # not an IPv4 address (Framed-IP-Address is IPv4)
    for bad in ("999.1.1.1", "2001:db8::1", "abc"):
        res = patch({"static_ip": bad})
        assert res.status_code == 422, (bad, res.get_json())
        assert "IP" in res.get_json()["error"]["message"]
    # used by another subscriber
    res = patch({"static_ip": "10.20.0.5"})
    assert res.status_code == 422, res.get_json()
    assert "لمشتركٍ آخر" in res.get_json()["error"]["message"]
    res = patch({"static_ip": "10.20.0.9"})
    assert res.status_code == 200, res.get_json()
    # web: refused with the Arabic message, nothing saved
    _web_login(client)
    fields, _t = _scrape(client, f"/admin/radius/users/{s.username}/edit")
    res = _post(client, f"/admin/radius/users/{s.username}", _set(fields, "static_ip", "10.20.0.5"))
    assert res.status_code == 422
    html = res.get_data(as_text=True)
    with client.session_transaction() as sess:
        flashed = " ".join(m for _c, m in sess.get("_flashes", []))
    assert "لمشتركٍ آخر" in html + flashed
    assert _get(s.username).static_ip == "10.20.0.9"
    assert other.username


def test_untouched_legacy_duplicate_stays_editable(client, app):
    """Only a CHANGED address is checked — an old row sharing an address with
    another one still saves its other fields."""
    _sub(static_ip="10.30.0.1")
    s = _sub(static_ip="10.30.0.1")
    res = client.patch(f"/api/v1/accounts/{s.username}", headers=AUTH, json={"remark": "ok"})
    assert res.status_code == 200, res.get_json()
