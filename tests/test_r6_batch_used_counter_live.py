"""R6 (r6-ops, client20): عدّادُ «مستخدم» للحزمة في الـAPI كان يقرأ العمود
``card_batches.used`` الذي **لا يتحدّث** حين تُستعمل البطاقة في RADIUS (أوّلُ
دخولٍ يضبط ``cards.used=1`` فقط). فشاشةُ الحزمة في التطبيق (``batch.used``)
تُظهر «مستخدم 0» وشريطَ تقدّمٍ فارغًا لحزمةٍ استُعملت كلُّها: 10/20 حزمةً على
client20. ملخّصُ الحزمة (/summary) كان صحيحًا — صار GET والقائمة مثله.
شغّل هذا الملف وحده.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def ctx(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "r6used.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.setenv("HOBERADIUS_API_TOKENS", "r6-used-token")
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
        yield flask_app, flask_app.test_client()


def db():
    from app.radius.db.connection import db as live
    return live()


H = {"Authorization": "Bearer r6-used-token"}


def _seed(n=5, used=3, deleted_used=1):
    cur = db().execute(
        "INSERT INTO access_plans(tenant_id,name,duration_minutes,validity_days,price,currency,"
        " created_at,updated_at) VALUES(1,'ساعة',60,1,1,'ILS',datetime('now'),datetime('now'))")
    plan_id = int(cur.lastrowid)
    cur = db().execute(
        "INSERT INTO card_batches(tenant_id,batch_code,package_name,plan_id,count,generated,used,"
        " time_value,time_unit,count_from_first_connect,count_by_seconds,status,created_at)"
        " VALUES(1,'B-U','r6used',?,?,?,0,1,'hours',1,0,'active',datetime('now'))", (plan_id, n, n))
    bid = int(cur.lastrowid)
    for i in range(n):
        is_used = 1 if i < used else 0
        first = datetime.utcnow().isoformat() + "Z" if is_used else None
        deleted = (datetime.utcnow().isoformat() + "Z") if i < deleted_used else None
        db().execute(
            "INSERT INTO cards(tenant_id,batch_id,username,password,plan_id,used,first_used_at,"
            " deleted_at,created_at) VALUES(1,?,?,'1',?,?,?,?,datetime('now'))",
            (bid, "u%d_%d" % (bid, i), plan_id, is_used, first, deleted))
    db().commit()
    return bid


def test_batch_get_reports_live_used_count(ctx):
    _app, client = ctx
    bid = _seed(n=5, used=3, deleted_used=1)   # 3 used, 1 of them archived ⇒ 2 live used
    r = client.get(f"/api/v1/cards/batches/{bid}", headers=H)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["data"]["used"] == 2


def test_batch_list_reports_live_used_count(ctx):
    _app, client = ctx
    bid = _seed(n=4, used=4, deleted_used=0)
    r = client.get("/api/v1/cards/batches", headers=H, query_string={"limit": 50})
    assert r.status_code == 200, r.get_json()
    item = next(i for i in r.get_json()["data"]["items"] if i["id"] == bid)
    assert item["used"] == 4
    assert item["used"] == item["used_count"]
