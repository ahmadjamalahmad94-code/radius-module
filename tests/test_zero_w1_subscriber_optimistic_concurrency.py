"""Zero-w1 #2 (round 6 M3) — a stale full-object save no longer overwrites
another admin's change.

Round 6 (r6ops): admin A GETs /accounts/u, admin B tops up +25 and extends +1
day, A PATCHes the object he read (+ a remark) => balance back to 0 and the old
expiry — 33/33. Now every account carries ``version``; PATCH with a stale
``version`` (or If-Match) => 409 ``stale_version`` + Arabic message, nothing
written. Without ``version`` old clients keep working (only sent keys apply).
The web edit form carries ``_row_version`` the same way.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

PW = "Pass-12345"
STALE_AR = "عُدِّل هذا المشترك من مدير آخر بعد فتحك له — أعد التحميل."


@pytest.fixture(scope="module")
def app():
    tmp = tempfile.mkdtemp(prefix="hr_zw1_occ_")
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED",
            "HOBERADIUS_LICENSE_GATE_TEST_BYPASS")
    old = {k: os.environ.get(k) for k in keys}
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "t.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    os.environ["HOBERADIUS_LICENSE_GATE_TEST_BYPASS"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    a.config["TESTING"] = True
    with a.app_context():
        from app.radius.db.repos import admins_repo, tenants_repo
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password=PW,
                                 full_name="المالك", is_super_admin=True)
    yield a
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _owner_id():
    from app.radius.db.repos import admins_repo
    return int(admins_repo.primary_admin_id())


def _hdr(app):
    from app.radius.db.repos import api_tokens_repo
    with app.app_context():
        _r, plain = api_tokens_repo.create_token(
            tenant_id=1, name="login:t" + uuid4().hex[:6], scopes=["admin:full"],
            created_by=_owner_id())
    return {"Authorization": "Bearer " + plain}


def _sub(app):
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    with app.app_context():
        s = subscribers_repo.upsert_subscriber(Subscriber(
            id=None, tenant_id=1, username="occ" + uuid4().hex[:8],
            password="p1234567", status="enabled",
            expire_at=datetime.utcnow().replace(microsecond=0) + timedelta(days=10)))
    return s.username


def _other_admin_changes(app, username):
    """Admin B: +25 balance and +1 day, committed after A loaded the row."""
    from app.radius.db.connection import transaction
    with app.app_context():
        with transaction() as c:
            c.execute("UPDATE subscribers SET balance = balance + 25,"
                      " expire_at = datetime(expire_at, '+1 day') WHERE username=?",
                      (username,))


def _row(app, username):
    from app.radius.db.connection import db
    with app.app_context():
        return dict(db().execute("SELECT balance, expire_at, remark FROM subscribers"
                                 " WHERE username=?", (username,)).fetchone())


def test_stale_full_object_patch_is_409_and_keeps_the_other_change(app):
    c, hdr = app.test_client(), _hdr(app)
    u = _sub(app)
    loaded = c.get(f"/api/v1/accounts/{u}", headers=hdr).get_json()["data"]
    assert loaded.get("version")
    _other_admin_changes(app, u)
    after_b = _row(app, u)
    body = {k: loaded[k] for k in ("balance", "expire_at", "full_name", "status")}
    body.update(remark="A note", version=loaded["version"])
    r = c.patch(f"/api/v1/accounts/{u}", headers=hdr, json=body)
    assert r.status_code == 409, r.get_json()
    err = r.get_json()["error"]
    assert err["code"] == "stale_version" and err["message"] == STALE_AR
    assert err["details"]["current_version"] != loaded["version"]
    assert _row(app, u) == after_b            # +25 / +1 day kept, no remark
    # reload => the fresh version saves
    fresh = c.get(f"/api/v1/accounts/{u}", headers=hdr).get_json()["data"]
    ok = c.patch(f"/api/v1/accounts/{u}", headers=hdr,
                 json={"remark": "A note", "version": fresh["version"]})
    assert ok.status_code == 200, ok.get_json()
    row = _row(app, u)
    assert row["remark"] == "A note" and float(row["balance"]) == 25.0
    assert ok.get_json()["data"]["version"] != fresh["version"]


def test_if_match_header_is_accepted(app):
    c, hdr = app.test_client(), _hdr(app)
    u = _sub(app)
    v = c.get(f"/api/v1/accounts/{u}", headers=hdr).get_json()["data"]["version"]
    _other_admin_changes(app, u)
    r = c.patch(f"/api/v1/accounts/{u}", headers={**hdr, "If-Match": f'"{v}"'},
                json={"remark": "x"})
    assert r.status_code == 409
    v2 = c.get(f"/api/v1/accounts/{u}", headers=hdr).get_json()["data"]["version"]
    r = c.patch(f"/api/v1/accounts/{u}", headers={**hdr, "If-Match": v2},
                json={"remark": "x"})
    assert r.status_code == 200


def test_without_version_old_clients_still_save_only_sent_fields(app):
    c, hdr = app.test_client(), _hdr(app)
    u = _sub(app)
    c.get(f"/api/v1/accounts/{u}", headers=hdr)
    _other_admin_changes(app, u)
    r = c.patch(f"/api/v1/accounts/{u}", headers=hdr, json={"remark": "diff only"})
    assert r.status_code == 200
    row = _row(app, u)
    assert row["remark"] == "diff only" and float(row["balance"]) == 25.0


def test_login_bookkeeping_does_not_make_the_version_stale(app):
    from app.radius.services.users import get_users_service, subscriber_version
    u = _sub(app)
    with app.app_context():
        v = subscriber_version(get_users_service().get(u))
        from app.radius.db.connection import transaction
        with transaction() as cx:
            cx.execute("UPDATE subscribers SET last_seen_at=datetime('now'),"
                       " last_login_at=datetime('now'), online_count=1 WHERE username=?",
                       (u,))
        assert subscriber_version(get_users_service().get(u)) == v


def test_service_rechecks_under_the_write_lock(app):
    from dataclasses import replace
    from app.radius.core.errors import RadiusStaleEdit
    from app.radius.services.users import get_users_service, subscriber_version
    u = _sub(app)
    with app.app_context():
        svc = get_users_service()
        base = svc.get(u)
        v = subscriber_version(base)
        _other_admin_changes(app, u)
        with pytest.raises(RadiusStaleEdit):
            svc.update(actor="t", sub=replace(base, remark="late"), base=base,
                       expected_version=v)
    assert _row(app, u)["remark"] in ("", None)


def _login(client, admin_id):
    from app.radius.auth.session_helpers import _resolve_is_super
    from app.radius.db.repos import admins_repo
    a = admins_repo.get_admin(int(admin_id))
    with client.session_transaction() as s:
        s["admin_id"] = a.id
        s["admin_user"] = a.username
        s["admin_name"] = a.username
        s["is_super_admin"] = bool(_resolve_is_super(a))
        s["tenant_id"] = 1
        s["admin_sv"] = admins_repo.session_epoch(a.id) or 0
        s["admin_av"] = admins_repo.authz_epoch(a.id)
        s["permissions"] = list(admins_repo.admin_permissions(a))
        s["_csrf_token"] = "tok"
    client.environ_base["HTTP_X_CSRFTOKEN"] = "tok"


def _web_open(app, u):
    c = app.test_client()
    with app.app_context():
        _login(c, _owner_id())
    html = c.get(f"/admin/radius/users/{u}/edit").get_data(as_text=True)
    m = re.search(r'name="_form_orig" value="([^"]*)"', html)
    assert m and m.group(1)
    import html as _h
    return c, _h.unescape(m.group(1))


def _flashes(c):
    with c.session_transaction() as s:
        return [msg for _cat, msg in (s.get("_flashes") or [])]


def test_web_same_field_changed_by_both_is_refused(app):
    """The web merges what only one side changed (F03-N1, kept); the SAME
    field changed by the operator AND by another admin meanwhile was
    last-writer-wins — now refused with the Arabic «أعد التحميل»."""
    u = _sub(app)
    c, orig = _web_open(app, u)
    from app.radius.db.connection import transaction
    with app.app_context():
        with transaction() as cx:
            cx.execute("UPDATE subscribers SET remark='B note', balance=balance+25"
                       " WHERE username=?", (u,))
    before = _row(app, u)
    r = c.post(f"/admin/radius/users/{u}",
               data={"_form_orig": orig, "remark": "A note", "username": u,
                     "csrf_token": "tok"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/users/{u}/edit")
    assert _row(app, u) == before                 # B's remark + balance kept
    assert any(STALE_AR in m for m in _flashes(c))


def test_web_expiry_set_on_a_stale_page_after_a_renewal_is_refused(app):
    u = _sub(app)
    c, orig = _web_open(app, u)
    _other_admin_changes(app, u)                  # renewal +1 day meanwhile
    before = _row(app, u)
    r = c.post(f"/admin/radius/users/{u}",
               data={"_form_orig": orig, "username": u, "csrf_token": "tok",
                     "expire_orig": "2000-01-01 00:00", "expire_year": "2031",
                     "expire_month": "1", "expire_day": "1",
                     "expire_time": "12:00"})
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/users/{u}/edit")
    assert _row(app, u) == before
