"""Hotspot login-page publish must NOT depend on FTP.

Covers the two root bugs:
  1. "file already exists" — the API path read /file/print rows at the wrong
     level (top-level instead of attrs), so it ALWAYS fell to /file/add and
     trapped on an existing file. Now it reads attrs → /file/set, and on a
     stray "already exists" trap it removes-then-adds (reliable overwrite).
  2. FTP refused on a hardened router (onboarding disables /ip service ftp).
     The router now PULLS each file from the panel over the tunnel via
     /tool fetch (no FTP), and /tool fetch overwrites the destination.
"""
from __future__ import annotations

import pytest

from app.radius.integration.mikrotik.errors import MikrotikTrap
from app.radius.services import hotspot_file_transfer as hft
from app.radius.services import hotspot_publish_store as hps
from app.radius.services import hotspot_templates as ht


# ── fake RouterOS client (client.run shape: {"reply","attrs":{...}}) ──
class FakeClient:
    def __init__(self, *, existing=None, fetch_status="finished",
                 present_after_fetch=True, add_trap_once=False):
        self.calls = []
        self.files = dict(existing or {})        # name -> .id
        self.fetch_status = fetch_status
        self.present_after_fetch = present_after_fetch
        self.add_trap_once = add_trap_once
        self._last_fetch_dst = None

    def run(self, path, attrs=None):
        attrs = attrs or {}
        self.calls.append((path, dict(attrs)))
        if path == "/file/print":
            where = attrs.get("where", "")
            name = where.split("name=", 1)[1] if "name=" in where else ""
            rows = []
            if name in self.files:
                rows.append({"reply": "!re",
                             "attrs": {"name": name, ".id": self.files[name]}})
            elif (self.present_after_fetch and name == self._last_fetch_dst):
                rows.append({"reply": "!re",
                             "attrs": {"name": name, ".id": "*FETCHED"}})
            return rows
        if path == "/file/set":
            return [{"reply": "!done", "attrs": {}}]
        if path == "/file/add":
            name = attrs.get("name")
            if self.add_trap_once and name not in self.files:
                self.add_trap_once = False
                raise MikrotikTrap("failure: file already exists")
            self.files[name] = "*NEW"
            return [{"reply": "!done", "attrs": {}}]
        if path == "/file/remove":
            fid = attrs.get(".id")
            for n, i in list(self.files.items()):
                if i == fid:
                    del self.files[n]
            return [{"reply": "!done", "attrs": {}}]
        if path == "/tool/fetch":
            self._last_fetch_dst = attrs.get("dst-path")
            return [{"reply": "!re", "attrs": {"status": "connecting"}},
                    {"reply": "!re", "attrs": {"status": self.fetch_status}},
                    {"reply": "!done", "attrs": {}}]
        return [{"reply": "!done", "attrs": {}}]

    def _names_called(self, path):
        return [a for p, a in self.calls if p == path]


# ── 1) overwrite: existing file → /file/set, not /file/add ───────────
def test_put_file_uses_set_when_file_exists():
    c = FakeClient(existing={"hotspot/login.html": "*7"})
    res = ht._put_file_once(c, "hotspot/login.html", "<html>new</html>")
    assert res.ok
    assert c._names_called("/file/set"), "must overwrite via /file/set"
    assert not c._names_called("/file/add"), "must NOT /file/add an existing file"


def test_put_file_recovers_from_already_exists_trap():
    """print misses but the file is really there → /file/add traps
    'already exists' → remove-then-add (reliable overwrite)."""
    c = FakeClient(existing={})
    # Patch print to reveal the file only after the first add attempt traps,
    # and let the SECOND /file/add (post-remove) go through orig_run cleanly.
    orig_run = c.run
    state = {"trapped": False}

    def run(path, attrs=None):
        if path == "/file/print" and state["trapped"]:
            return [{"reply": "!re",
                     "attrs": {"name": "hotspot/login.html", ".id": "*9"}}]
        if path == "/file/add" and not state["trapped"]:
            state["trapped"] = True
            raise MikrotikTrap("failure: file already exists")
        return orig_run(path, attrs)

    c.run = run
    res = ht._put_file_once(c, "hotspot/login.html", "x")
    assert res.ok
    assert c._names_called("/file/remove"), "must remove the stale file"


# ── 2) /tool fetch transport (no FTP) ────────────────────────────────
def test_router_fetch_upload_happy_path():
    c = FakeClient()
    sent = hft.router_fetch_upload(
        c, "hotspot/login.html", b"<html>hi</html>",
        base_url="http://10.0.0.1", stash_fn=lambda d, ct: "TOK")
    assert sent == len(b"<html>hi</html>")
    fetches = c._names_called("/tool/fetch")
    assert fetches and fetches[0]["dst-path"] == "hotspot/login.html"
    assert fetches[0]["url"] == "http://10.0.0.1/admin/radius/hotspot/pull/TOK"
    assert fetches[0]["mode"] == "http"


def test_router_fetch_overwrites_existing():
    """Pre-existing file → best-effort remove before fetch (overwrite)."""
    c = FakeClient(existing={"hotspot/login.html": "*5"})
    hft.router_fetch_upload(
        c, "hotspot/login.html", b"data",
        base_url="http://10.0.0.1", stash_fn=lambda d, ct: "TOK")
    assert c._names_called("/file/remove"), "should remove the old file first"


def test_router_fetch_failed_status_raises():
    c = FakeClient(fetch_status="failed")
    with pytest.raises(hft.FetchUploadError):
        hft.router_fetch_upload(
            c, "hotspot/login.html", b"x",
            base_url="http://10.0.0.1", stash_fn=lambda d, ct: "TOK")


def test_router_fetch_trap_raises_clear_reason():
    class Trapping(FakeClient):
        def run(self, path, attrs=None):
            if path == "/tool/fetch":
                raise MikrotikTrap("failure: closing connection")
            return super().run(path, attrs)
    with pytest.raises(hft.FetchUploadError) as exc:
        hft.router_fetch_upload(
            Trapping(), "hotspot/login.html", b"x",
            base_url="http://10.0.0.1", stash_fn=lambda d, ct: "TOK")
    assert "/tool fetch" in str(exc.value)


def test_router_fetch_no_base_url_raises():
    with pytest.raises(hft.FetchUploadError):
        hft.router_fetch_upload(
            FakeClient(), "hotspot/login.html", b"x",
            base_url="", stash_fn=lambda d, ct: "TOK")


# ── 3) transient blob store ──────────────────────────────────────────
def test_publish_store_stash_take_once():
    hps.clear()
    tok = hps.stash(b"hello", content_type="text/html")
    got = hps.take(tok)
    assert got == (b"hello", "text/html")
    assert hps.take(tok) is None, "token is one-time"


def test_publish_store_unknown_token():
    hps.clear()
    assert hps.take("nope") is None


# ── 4) smart routing prefers fetch, falls back, no FTP needed ────────
def test_smart_big_file_uses_fetch_when_available():
    big = "x" * (hft.API_SAFE_BYTES + 10)
    c = FakeClient()
    res = ht._put_file_smart(
        c, "hotspot/login.html", big,
        fetch={"base_url": "http://10.0.0.1", "stash_fn": lambda d, ct: "T"})
    assert res.ok and res.via == "fetch"
    assert c._names_called("/tool/fetch")


def test_smart_small_file_uses_api_then_succeeds():
    c = FakeClient(existing={"hotspot/x.html": "*1"})
    res = ht._put_file_smart(c, "hotspot/x.html", "small")
    assert res.ok
    assert c._names_called("/file/set")
    assert not c._names_called("/tool/fetch")


def test_smart_big_file_no_fetch_no_ftp_reports_clear_error():
    big = "x" * (hft.API_SAFE_BYTES + 10)

    class APIDead(FakeClient):
        def run(self, path, attrs=None):
            if path == "/file/add":
                raise MikrotikTrap("failure: file too big for API")
            return super().run(path, attrs)
    res = ht._put_file_smart(APIDead(), "hotspot/login.html", big)
    assert not res.ok
    assert "عنوان خادم الراديوس" in res.error  # hint to enable the fetch path


# ── 5) public pull route: no admin session, one-time ─────────────────
def test_pull_route_is_public_and_one_time(monkeypatch, tmp_path):
    import os
    db_file = os.path.join(tmp_path, "pub.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("FLASK_SECRET", "test-secret")
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        run_pending_migrations()

    hps.clear()
    tok = hps.stash(b"<html>login</html>",
                    content_type="text/html; charset=utf-8")
    c = app.test_client()
    # No admin session at all — the router has no cookie. Must be 200.
    r = c.get(f"/admin/radius/hotspot/pull/{tok}")
    assert r.status_code == 200, r.get_data(as_text=True)
    assert r.get_data() == b"<html>login</html>"
    assert "text/html" in r.headers.get("Content-Type", "")
    # One-time: the token is consumed on first fetch.
    r2 = c.get(f"/admin/radius/hotspot/pull/{tok}")
    assert r2.status_code == 404
