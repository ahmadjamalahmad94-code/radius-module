"""Zero-w1 #4 — «تثبيت MAC» on a CARD from the online page (web + API) writes
an audit row (``card.lock_mac``), like the subscriber path (``user.update``)
and the cards checker. Before: ``cards.locked_mac`` was written directly and
nothing was recorded.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

import pytest

AUTH = {"Authorization": "Bearer dev-token-please-change"}
MAC = "AA:BB:CC:DD:EE:0F"


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_zw1_lockmac_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_LICENSE_GATE_TEST_BYPASS", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_TOKENS", raising=False)
    monkeypatch.delenv("HOBERADIUS_API_RATE_LIMIT_PER_MINUTE", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_card_session(app, username, sid) -> int:
    now = datetime.utcnow().isoformat() + "Z"
    with app.app_context():
        from app.radius.db.connection import transaction
        with transaction() as c:
            plan_id = c.execute(
                "INSERT INTO access_plans(tenant_id, name, service_type, created_at) "
                "VALUES (1,?,?,?)", ("P " + username, "Hotspot", now)).lastrowid
            batch_id = c.execute(
                "INSERT INTO card_batches(tenant_id, batch_code, plan_id, count, created_at) "
                "VALUES (1,?,?,1,?)", ("B-" + username, plan_id, now)).lastrowid
            card_id = c.execute(
                "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, created_at) "
                "VALUES (1,?,?,?,?,?)", (batch_id, username, "pw", plan_id, now)).lastrowid
            c.execute(
                "INSERT INTO radacct (tenant_id, acctsessionid, acctuniqueid, username,"
                " nasipaddress, framedipaddress, callingstationid, acctstarttime,"
                " acctupdatetime) VALUES (1,?,?,?,?,?,?,?,?)",
                (sid, "u-" + sid, username, "10.10.0.2", "10.20.30.9", MAC, now, now))
    return int(card_id)


def _audit_rows(app, card_id):
    with app.app_context():
        from app.radius.db.connection import db
        return [dict(r) for r in db().execute(
            "SELECT action, target_type, target_id, payload_json FROM audit_log "
            "WHERE action='card.lock_mac' AND target_type='card' AND target_id=?",
            (str(card_id),)).fetchall()]


def _locked(app, card_id):
    with app.app_context():
        from app.radius.db.connection import db
        return db().execute("SELECT locked_mac FROM cards WHERE id=?",
                            (card_id,)).fetchone()["locked_mac"]


def test_api_online_lock_mac_on_card_is_audited(app):
    cid = _seed_card_session(app, "zcard-api", "s-zapi")
    res = app.test_client().post("/api/v1/sessions/lock-mac", headers=AUTH,
                                 json={"username": "zcard-api", "session_id": "s-zapi"})
    assert res.status_code == 200, res.get_json()
    assert _locked(app, cid) == MAC
    rows = _audit_rows(app, cid)
    assert len(rows) == 1 and MAC in (rows[0]["payload_json"] or "")


def _logged_in(app):
    client = app.test_client()
    with app.app_context():
        from app.radius.db.repos import admins_repo
        u = f"zw1_{uuid4().hex[:10]}"
        admins_repo.create_admin(
            username=u, password="zw1-pass", full_name="ZW1",
            is_super_admin=True,
            role_id=getattr(admins_repo.get_role_by_name("super_admin"), "id", None))
    r = client.post("/admin/radius/login", data={"username": u, "password": "zw1-pass"})
    assert r.status_code in {302, 303}
    return client


def test_web_online_lock_mac_on_card_is_audited(app):
    cid = _seed_card_session(app, "zcard-web", "s-zweb")
    client = _logged_in(app)
    client.get("/admin/radius/online?type=card")
    with client.session_transaction() as s:
        token = s.get("_csrf_token")
    resp = client.post("/admin/radius/online/lock-mac", data={
        "_csrf_token": token, "next": "/admin/radius/online?type=card",
        "username": "zcard-web", "session_id": "s-zweb"})
    assert resp.status_code == 302
    assert _locked(app, cid) == MAC
    rows = _audit_rows(app, cid)
    assert len(rows) == 1 and MAC in (rows[0]["payload_json"] or "")
