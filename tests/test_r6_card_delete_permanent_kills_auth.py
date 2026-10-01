"""R6 (r6-ops, client20): «حذف نهائيّ» لبطاقةٍ واحدةٍ كان يحذف صفَّ ``cards``
وحده ويترك **مرآتَها** في ``subscribers`` (user_type='card') — فالبطاقةُ
المحذوفةُ نهائيًّا ظلّت تُقبَل في RADIUS (60/60 على client20).

حذفُ الحزمةِ نهائيًّا (``purge_batch``) كان يحذف المرآةَ وصفوفَ rad* أصلًا؛
حذفُ البطاقةِ الواحدةِ يتبعه الآن. شغّل هذا الملف وحده.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

MAC = "AA:BB:CC:DD:EE:02"


@pytest.fixture
def app_ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "r6del.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests
    reset_for_tests(db_file)
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        yield flask_app


def db():
    from app.radius.db.connection import db as live
    return live()


def _seed(username="77001122", password="4455"):
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,"
        " price,currency,created_at,updated_at) VALUES(1,'ساعة',60,1,1,'ILS',"
        "datetime('now'),datetime('now'))")
    plan_id = int(cur.lastrowid)
    cur = db().execute(
        "INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,"
        " generated,used,time_value,time_unit,count_from_first_connect,"
        " count_by_seconds,status,created_at)"
        " VALUES(1,'B-R6','r6',?,2,2,0,1,'hours',1,0,'active',datetime('now'))",
        (plan_id,))
    batch_id = int(cur.lastrowid)
    exp = (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    ids = []
    for u in (username, username + "9"):
        cur = db().execute(
            "INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,used,"
            " expire_at,created_at) VALUES(1,?,?,?,?,0,?,datetime('now'))",
            (batch_id, u, password, plan_id, exp))
        ids.append(int(cur.lastrowid))
        db().execute(
            "INSERT INTO subscribers(tenant_id,username,password,plan_id,status,"
            " user_type,card_batch_id,expire_at,created_at)"
            " VALUES(1,?,?,?,'enabled','card',?,?,datetime('now'))",
            (u, password, plan_id, batch_id, exp))
        db().execute(
            "INSERT INTO radcheck(tenant_id,username,attribute,op,value)"
            " VALUES(1,?,'Cleartext-Password',':=',?)", (u, password))
    db().commit()
    return ids


def _auth(username, password="4455"):
    from app.radius.services.policy_engine import AuthRequest, authorize
    return authorize(AuthRequest(username=username, password=password, tenant_id=1,
                                 calling_station_id=MAC, nas_ip="10.0.0.1"))


def test_permanently_deleted_card_no_longer_authenticates(app_ctx):
    card_id, other_id = _seed()
    assert _auth("77001122").ok            # control: the live card works
    from app.radius.db.repos import cards_repo
    assert cards_repo.delete_card_permanently(1, card_id) is True
    # the card AND its RADIUS mirror are gone …
    assert db().execute("SELECT 1 FROM cards WHERE id=?", (card_id,)).fetchone() is None
    assert db().execute("SELECT 1 FROM subscribers WHERE username='77001122'"
                        " AND user_type='card'").fetchone() is None
    assert db().execute("SELECT 1 FROM radcheck WHERE username='77001122'").fetchone() is None
    # … so RADIUS rejects it
    assert not _auth("77001122").ok
    # the sibling card of the same batch is untouched
    assert _auth("770011229").ok
    assert db().execute("SELECT 1 FROM cards WHERE id=?", (other_id,)).fetchone()


def test_delete_keeps_a_regular_subscriber_with_the_same_username(app_ctx):
    """المرآةُ وحدَها (user_type='card') تُحذف — لا مشتركٌ عاديٌّ يحمل الاسمَ نفسه."""
    card_id, _ = _seed(username="55001")
    db().execute("UPDATE subscribers SET user_type='subscriber', card_batch_id=NULL"
                 " WHERE username='55001'")
    db().commit()
    from app.radius.db.repos import cards_repo
    assert cards_repo.delete_card_permanently(1, card_id) is True
    assert db().execute("SELECT 1 FROM subscribers WHERE username='55001'").fetchone()


def test_missing_card_returns_false(app_ctx):
    from app.radius.db.repos import cards_repo
    assert cards_repo.delete_card_permanently(1, 987654) is False
