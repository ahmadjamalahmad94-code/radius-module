"""بوابة الموزّع «فحص كروت» — دخول + فحص قراءة-فقط.

يتحقق أن:
- الموزّع الممنوح cards.check + كلمة مرور يدخل ويفحص كرتًا.
- بلا الصلاحية أو بكلمة خاطئة أو بحالة غير active → 401/طرد.
- الإسقاط قراءة-فقط: لا operations ولا كلمات مرور في الصفحة.
- نموذج الإدارة يخزّن hash كلمة مرور البوابة (لا نصًا صريحًا).
"""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "dist_checker.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()

    @flask_app.before_request
    def _bind_test_db():
        os.environ["HOBERADIUS_DB_PATH"] = db_file
        from app.radius.db.connection import reset_for_tests

        reset_for_tests(db_file)

    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


def _seed_card(username: str = "7770001112223", *, batch_code: str = "DBATCH") -> int:
    """يبذر باقة+حزمة+كرت — يُعيد batch_id لاستخدامه في اختبارات النطاق."""
    plan = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                 price, currency, created_at, updated_at)
        VALUES(1,?,1440,30,5.0,'JOD',datetime('now'),datetime('now'))
        """,
        (f"Checker Plan {batch_code}",),
    )
    batch = db().execute(
        """
        INSERT INTO card_batches(tenant_id, batch_code, package_name, plan_id,
                                 count, generated, created_at)
        VALUES(1,?,'حزمة فحص',?,1,1,datetime('now'))
        """,
        (batch_code, plan.lastrowid),
    )
    db().execute(
        """
        INSERT INTO cards(tenant_id, batch_id, username, password, plan_id,
                          used, created_at)
        VALUES(1,?,?,'112233',?,0,datetime('now'))
        """,
        (batch.lastrowid, username, plan.lastrowid),
    )
    return int(batch.lastrowid)


def _assign_batch(distributor_id: int, batch_id: int) -> None:
    db().execute(
        """
        INSERT INTO card_batch_assignments(tenant_id, batch_id, distributor_id,
                                           assigned_by, status, assigned_at)
        VALUES(1,?,?,'test','assigned',datetime('now'))
        """,
        (batch_id, distributor_id),
    )


def _distributor(*, name: str = "dealer1", password: str | None = "pw-1234",
                 perms: str = '["cards.read","cards.check"]',
                 status: str = "active",
                 scope: str = '{"card_batches":"all"}') -> int:
    from werkzeug.security import generate_password_hash

    cur = db().execute(
        """
        INSERT INTO distributors(tenant_id, name, display_name, status,
                                 permissions_json, scope_json, balance,
                                 credit_limit, debt_balance, created_by,
                                 created_at, portal_password_hash)
        VALUES(1,?,?,?,?,?,0,0,0,'test',datetime('now'),?)
        """,
        (name, name, status, perms, scope,
         generate_password_hash(password) if password else None),
    )
    return int(cur.lastrowid)


def _login(client, name: str = "dealer1", password: str = "pw-1234"):
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "t"
    return client.post(
        "/portal/distributor/login",
        data={"username": name, "password": password, "_csrf_token": "t"},
        follow_redirects=False,
    )


def test_login_and_check_card_read_only(app):
    with app.app_context():
        _distributor()
        _seed_card()
    client = app.test_client()
    resp = _login(client)
    assert resp.status_code == 302 and "/portal/distributor" in resp.headers["Location"]

    page = client.get("/portal/distributor?q=7770001112223")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "7770001112223" in html
    assert "غير مستخدم بعد" in html  # status=available
    # قراءة فقط: لا كلمة مرور الكرت ولا أي أزرار عمليات في الصفحة
    assert "112233" not in html
    for op in ("disconnect", "lock_mac", "reset_usage", "disable"):
        assert op not in html


def test_not_found_card_shows_message(app):
    with app.app_context():
        _distributor()
    client = app.test_client()
    _login(client)
    page = client.get("/portal/distributor?q=nope-404")
    assert page.status_code == 200
    assert "لا يوجد كرت بهذا الرقم" in page.get_data(as_text=True)


def test_wrong_password_rejected(app):
    with app.app_context():
        _distributor()
    client = app.test_client()
    assert _login(client, password="WRONG").status_code == 401


def test_missing_permission_rejected(app):
    with app.app_context():
        _distributor(perms='["cards.read","cards.sell"]')
    client = app.test_client()
    assert _login(client).status_code == 401


def test_inactive_distributor_rejected_and_session_cut(app):
    with app.app_context():
        dist_id = _distributor()
    client = app.test_client()
    assert _login(client).status_code == 302
    # تعطيل الموزّع بعد الدخول → أول طلب تالٍ يطرده لصفحة الدخول
    with app.app_context():
        db().execute("UPDATE distributors SET status='suspended' WHERE id=?", (dist_id,))
    resp = client.get("/portal/distributor", follow_redirects=False)
    assert resp.status_code == 302 and "login" in resp.headers["Location"]


def test_anonymous_redirected_to_login(app):
    client = app.test_client()
    resp = client.get("/portal/distributor", follow_redirects=False)
    assert resp.status_code == 302 and "login" in resp.headers["Location"]


def test_no_write_routes_under_distributor_portal(app):
    """القراءة-فقط بنيويًا: لا يوجد أي مسار POST تحت /portal/distributor
    سوى login/logout — أي POST آخر = 404/405."""
    with app.app_context():
        _distributor()
    client = app.test_client()
    _login(client)
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "t"
    resp = client.post("/portal/distributor", data={"op": "disconnect", "_csrf_token": "t"})
    assert resp.status_code == 405
    resp = client.post("/portal/distributor/operate", data={"_csrf_token": "t"})
    assert resp.status_code == 404


def test_scope_assigned_blocks_unassigned_batch(app):
    """نطاق «حزم معيّنة»: كرت من حزمة غير مربوطة → رسالة خارج النطاق بلا تفاصيل."""
    with app.app_context():
        _distributor(scope='{"card_batches":"assigned"}')
        _seed_card("5550001112223", batch_code="OTHER")
    client = app.test_client()
    _login(client)
    page = client.get("/portal/distributor?q=5550001112223")
    html = page.get_data(as_text=True)
    assert "ليس ضمن الحزم المتاحة لك" in html
    assert "حزمة فحص" not in html  # لا تفاصيل عن الكرت/حزمته


def test_scope_assigned_allows_assigned_batch(app):
    with app.app_context():
        dist_id = _distributor(scope='{"card_batches":"assigned"}')
        batch_id = _seed_card("6660001112223", batch_code="MINE")
        _assign_batch(dist_id, batch_id)
    client = app.test_client()
    _login(client)
    page = client.get("/portal/distributor?q=6660001112223")
    html = page.get_data(as_text=True)
    assert "6660001112223" in html
    assert "ليس ضمن الحزم المتاحة لك" not in html


def test_scope_assigned_allows_batch_carried_on_distributor_id(app):
    """الربط المباشر عبر card_batches.distributor_id يُحتسب ضمن النطاق أيضًا."""
    with app.app_context():
        dist_id = _distributor(scope='{"card_batches":"assigned"}')
        batch_id = _seed_card("9990001112223", batch_code="CARRIED")
        db().execute(
            "UPDATE card_batches SET distributor_id=? WHERE id=?",
            (dist_id, batch_id),
        )
    client = app.test_client()
    _login(client)
    page = client.get("/portal/distributor?q=9990001112223")
    assert "9990001112223" in page.get_data(as_text=True)


def test_scope_all_sees_any_batch(app):
    with app.app_context():
        _distributor(scope='{"card_batches":"all"}')
        _seed_card("4440001112223", batch_code="ANY")
    client = app.test_client()
    _login(client)
    page = client.get("/portal/distributor?q=4440001112223")
    html = page.get_data(as_text=True)
    assert "4440001112223" in html and "ليس ضمن الحزم" not in html


def _seed_session(username: str, mac: str, *, online: bool = False) -> None:
    db().execute(
        """
        INSERT INTO radacct(tenant_id, acctsessionid, username, nasipaddress,
                            nasporttype, acctstarttime, acctupdatetime,
                            acctstoptime, acctsessiontime, callingstationid,
                            acctinputoctets, acctoutputoctets)
        VALUES(1,?,?,'10.0.0.1','Wireless-802.11',datetime('now','-2 hours'),
               datetime('now'),?,3600,?,1048576,2097152)
        """,
        (f"s-{mac}", username, None if online else "2026-08-03T09:00:00Z", mac),
    )


def test_devices_listed_with_masked_mac(app):
    """كشف الأجهزة: النوع/الشركة ظاهر، والماك نصفه فقط — لا يظهر كاملًا أبدًا."""
    with app.app_context():
        _distributor(scope='{"card_batches":"all"}')
        _seed_card("3330001112223", batch_code="DEVS")
        _seed_session("3330001112223", "A4:83:E7:11:22:33")
        _seed_session("3330001112223", "5C:F9:38:AA:BB:CC", online=True)
    client = app.test_client()
    _login(client)
    html = client.get("/portal/distributor?q=3330001112223").get_data(as_text=True)

    assert 'data-testid="dist-devices"' in html
    # النصف الأول (الشركة) ظاهر
    assert "A4:83:E7:••:••:••" in html
    assert "5C:F9:38:••:••:••" in html
    # الماك الكامل لا يظهر بأي صيغة
    for full in ("A4:83:E7:11:22:33", "5C:F9:38:AA:BB:CC",
                 "a4:83:e7:11:22:33", "A4-83-E7-11-22-33"):
        assert full not in html
    assert "11:22:33" not in html and "AA:BB:CC" not in html


def test_mask_mac_never_leaks_second_half(app):
    from app.radius.routes.customer_portals import _mask_mac

    assert _mask_mac("A4:83:E7:11:22:33") == "A4:83:E7:••:••:••"
    assert _mask_mac("a4-83-e7-11-22-33") == "A4:83:E7:••:••:••"
    assert _mask_mac("") == "••:••:••"
    assert _mask_mac("garbage") == "••:••:••"


def test_no_devices_section_when_never_used(app):
    with app.app_context():
        _distributor(scope='{"card_batches":"all"}')
        _seed_card("2220001112223", batch_code="NODEV")
    client = app.test_client()
    _login(client)
    html = client.get("/portal/distributor?q=2220001112223").get_data(as_text=True)
    assert 'data-testid="dist-devices"' not in html


def test_admin_form_sets_portal_password_hash(app):
    """distributors_create مع portal_password يخزّن hash لا نصًا صريحًا."""
    from werkzeug.security import check_password_hash

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["admin_user"] = "admin"
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["permissions"] = ["reports.finance"]
        sess["_csrf_token"] = "t"
    resp = client.post(
        "/admin/radius/distributors",
        data={
            "name": "dealer9", "display_name": "موزع ٩", "status": "active",
            "permissions": ["cards.read", "cards.check"],
            "scope_json": '{"card_batches":"assigned"}',
            "portal_password": "secret-77",
            "_csrf_token": "t",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.get_data(as_text=True)[:300]
    with app.app_context():
        row = db().execute(
            "SELECT portal_password_hash, permissions_json FROM distributors WHERE name='dealer9'"
        ).fetchone()
    stored = row[0]
    assert stored and stored != "secret-77"
    assert check_password_hash(stored, "secret-77")
    assert "cards.check" in row[1]
