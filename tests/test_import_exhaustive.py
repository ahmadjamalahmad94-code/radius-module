"""EXHAUSTIVE coverage — card-batch import gate, JSON errors, dry-run analysis.

Proves the REAL server-side behaviour:
  * can_import_batches gates ALL three surfaces (analyze, commit, page) — owner
    always, manager only if granted, otherwise 403;
  * the analyze endpoint returns JSON (never a raw HTML 403 page);
  * «تحليل الملف» is a pure dry-run: it writes NOTHING and returns a categorised
    report (valid / in-file dup / in-system dup / invalid) with counts + samples;
  * the commit imports only valid rows, computes total = valid × price, and
    creates NO batch when nothing is valid.
"""
from __future__ import annotations

import io
import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


def _reset_for_tests(db_file: str) -> None:
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "import_exhaustive.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    _reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
        admins_repo.create_admin(username="owner_root", password="x12345678",
                                 full_name="Owner", is_super_admin=True)
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


def _plan_id() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(
            tenant_id, name, duration_minutes, validity_days, price, currency,
            speed_down_kbps, speed_up_kbps, quota_total_mb, created_at, updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """,
        (1, "باقة", 8 * 60, 1, 5.0, "JOD", 4096, 2048, 1024),
    )
    return int(cur.lastrowid)


def _sub_admin(username: str) -> int:
    from app.radius.db.repos import admins_repo

    adm = admins_repo.create_admin(username=username, password="x12345678",
                                   full_name=f"M {username}", is_super_admin=False)
    return int(adm.id)


def _grant(manager_id: int, **perms) -> None:
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService

    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=manager_id, permissions=perms)


def _seed_card(plan_id: int, username: str) -> None:
    from app.radius.services.cards import get_cards_service

    get_cards_service().import_batch(actor="seed", plan_id=plan_id, source_type="external",
                                     cards=[{"username": username, "password": "p"}])


def _login(client, *, admin_id: int, is_super: bool, perms=("cards.view",)):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = list(perms)


def _preview(client, csv_bytes: bytes):
    return client.post(
        "/admin/radius/cards/batches/import/preview",
        data={"file": (io.BytesIO(csv_bytes), "cards.csv"), "_csrf_token": "off-csrf"},
        content_type="multipart/form-data", headers={"X-CSRFToken": "off-csrf"})


def _commit(client, *, plan_id, csv_text, source_type="external", price="2.00"):
    return client.post("/admin/radius/cards/batches/import", data={
        "_csrf_token": "off-csrf", "plan_id": str(plan_id), "source_type": source_type,
        "csv_text": csv_text, "price_per_card": price, "package_name": "p"},
        follow_redirects=False)


def _counts():
    return (db().execute("SELECT COUNT(*) c FROM cards").fetchone()["c"],
            db().execute("SELECT COUNT(*) c FROM card_batches").fetchone()["c"])


# ═══ permission presence + toggle wiring ════════════════════════════════════
def test_permission_default_off(app):
    from app.radius.services.manager_distributor_ops import DEFAULT_PERMISSIONS

    assert DEFAULT_PERMISSIONS.get("can_import_batches") is False


def test_permission_label(app):
    from app.radius.services.permission_labels import permission_label

    assert permission_label("can_import_batches") == "استيراد الحِزم"


def test_owner_toggles_permission_via_policy_route(app):
    with app.app_context():
        mgr = _sub_admin("tog")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        res = c.post(f"/admin/radius/business-operators/manager/{mgr}/policy",
                     data={"_csrf_token": "off-csrf", "can_import_batches": "1"})
    assert res.status_code in (302, 303)
    with app.app_context():
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        assert ManagerDistributorOpsService(tenant_id=1).has_permission(
            entity_type="manager", entity_id=mgr, permission="can_import_batches") is True


# ═══ gate on ALL three surfaces ═════════════════════════════════════════════
def test_analyze_manager_without_perm_json_403(app):
    with app.app_context():
        mgr = _sub_admin("a_no")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        res = _preview(c, b"username\nx\n")
    assert res.status_code == 403
    assert res.is_json                                  # JSON, not HTML
    assert "<!doctype" not in res.get_data(as_text=True).lower()
    assert "صلاحية" in res.get_json().get("error", "")


def test_analyze_manager_with_perm_ok(app):
    with app.app_context():
        mgr = _sub_admin("a_yes"); _grant(mgr, can_import_batches=True)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        assert _preview(c, b"username\nx\n").status_code == 200


def test_analyze_owner_ok(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert _preview(c, b"username\nx\n").status_code == 200


def test_commit_manager_without_perm_403(app):
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("c_no")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        res = _commit(c, plan_id=plan, csv_text="username\nz\n")
    assert res.status_code == 403
    with app.app_context():
        assert _counts() == (0, 0)                      # nothing imported


def test_commit_manager_with_perm_ok(app):
    with app.app_context():
        plan = _plan_id(); mgr = _sub_admin("c_yes"); _grant(mgr, can_import_batches=True)
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        res = _commit(c, plan_id=plan, csv_text="username,password\nz,1\n")
    assert res.status_code in (302, 303)
    with app.app_context():
        assert db().execute("SELECT COUNT(*) c FROM cards").fetchone()["c"] == 1


def test_page_get_manager_without_perm_403(app):
    with app.app_context():
        mgr = _sub_admin("p_no")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        assert c.get("/admin/radius/cards/batches/import").status_code == 403


def test_page_get_owner_200(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        assert c.get("/admin/radius/cards/batches/import").status_code == 200


def test_import_button_hidden_for_manager_without_perm(app):
    with app.app_context():
        mgr = _sub_admin("btn_no")
    with app.test_client() as c:
        _login(c, admin_id=mgr, is_super=False)
        html = c.get("/admin/radius/cards/batches?status=all").get_data(as_text=True)
    assert "/cards/batches/import" not in html
    with app.app_context():
        mgr2 = _sub_admin("btn_yes"); _grant(mgr2, can_import_batches=True)
    with app.test_client() as c:
        _login(c, admin_id=mgr2, is_super=False)
        html = c.get("/admin/radius/cards/batches?status=all").get_data(as_text=True)
    assert "/cards/batches/import" in html


# ═══ dry-run writes NOTHING ═════════════════════════════════════════════════
def test_analyze_writes_nothing(app):
    with app.app_context():
        _plan_id()
        before = _counts()
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        _preview(c, b"username,password\na,1\nb,2\na,3\n,4\n")
    with app.app_context():
        assert _counts() == before                      # no rows written at all


# ═══ categorised report — every reason group with counts + samples ══════════
def test_analyze_report_categories_and_samples(app):
    with app.app_context():
        plan = _plan_id()
        _seed_card(plan, "sys1")                         # exists in the system
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        # the blank-username row is dropped by the parser → rows_skipped_by_parser.
        res = _preview(c, b"username,password\nnew1,a\nnew2,b\nnew1,c\nsys1,d\n,e\n")
    rep = res.get_json()["report"]
    assert rep["parsed_total"] == 4                      # 4 parsed (blank pre-filtered)
    assert rep["valid_count"] == 2                       # new1, new2
    assert rep["duplicate_in_file"]["count"] == 1
    assert "new1" in rep["duplicate_in_file"]["samples"]
    assert rep["duplicate_in_system"]["count"] == 1
    assert "sys1" in rep["duplicate_in_system"]["samples"]
    assert rep["rows_skipped_by_parser"] >= 1           # the blank row surfaced


def test_analyze_csv_text_is_valid_only(app):
    with app.app_context():
        plan = _plan_id(); _seed_card(plan, "old1")
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        data = _preview(c, b"username,password\nfresh1,a\nold1,b\nfresh1,c\n").get_json()
    # only the single fresh/valid row survives into csv_text.
    assert "fresh1" in data["csv_text"]
    assert data["csv_text"].count("old1") == 0
    assert data["count"] == 1


def test_analyze_surfaces_parser_skipped_rows(app):
    # a wholly blank line is skipped by the parser; report must surface it.
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        report = get_cards_service().analyze_import(
            [{"username": "u1", "password": "p"}, {"username": "", "password": ""}])
        assert report["valid_count"] == 1
        assert report["invalid"][0]["count"] == 1
        assert "فارغ" in report["invalid"][0]["label"]


# ═══ commit: only valid; 0 valid → no batch; total computed ═════════════════
def test_commit_imports_only_valid(app):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        r = get_cards_service().import_batch(
            actor="t", plan_id=plan, source_type="external", price_per_card=2.0,
            cards=[{"username": "v1", "password": "a"}, {"username": "v2", "password": "b"},
                   {"username": "v1", "password": "c"}, {"username": "", "password": "d"}])
        assert r["inserted_count"] == 2
        assert int(r["batch"].count) == 2               # not 4
        assert abs(float(r["batch"].total_price) - 4.0) < 0.001  # 2 × 2.00


def test_commit_zero_valid_no_batch(app):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id(); _seed_card(plan, "dup")
        before = db().execute("SELECT COUNT(*) c FROM card_batches").fetchone()["c"]
        with pytest.raises(RadiusValidationError):
            get_cards_service().import_batch(
                actor="t", plan_id=plan, source_type="external",
                cards=[{"username": "dup", "password": "x"}, {"username": "", "password": "y"}])
        after = db().execute("SELECT COUNT(*) c FROM card_batches").fetchone()["c"]
        assert after == before


def test_import_page_has_no_manual_total(app):
    with app.test_client() as c:
        _login(c, admin_id=1, is_super=True)
        html = c.get("/admin/radius/cards/batches/import").get_data(as_text=True)
    assert 'name="total_price"' not in html
    assert "إجمالي الجملة" in html and "هامش الربح" in html
