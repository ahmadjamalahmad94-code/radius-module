"""Card-batch import: can_import_batches gate + JSON errors + dry-run analysis.

  * import (analyze + commit) is gated by the toggleable can_import_batches
    permission — owner/super always; a sub-manager only if granted;
  * the analyze endpoint returns a JSON error (never a raw HTML 403 page);
  * «تحليل الملف» is a pure dry-run that categorises rows (valid / in-file
    duplicate / in-system duplicate / invalid) and imports nothing;
  * committing imports only the valid rows; 0 valid → no batch is created;
  * the manual «السعر الإجمالي» is gone — total is computed = valid × price.

Auth/fixture pattern mirrors test_cardgen_offer_accounting_scoping.py.
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
    db_file = os.path.join(tmp_path, "import_perm_dryrun.db")
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
        admins_repo.create_admin(
            username="owner_root", password="x12345678", full_name="Owner",
            is_super_admin=True,
        )
    flask_app.config["_HOBERADIUS_TEST_DB_FILE"] = db_file
    return flask_app


# ── helpers ────────────────────────────────────────────────────────────────
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

    adm = admins_repo.create_admin(
        username=username, password="x12345678", full_name=f"Mgr {username}",
        is_super_admin=False,
    )
    return int(adm.id)


def _grant(manager_id: int, **perms) -> None:
    from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService

    ManagerDistributorOpsService(tenant_id=1).set_policy(
        entity_type="manager", entity_id=manager_id, permissions=perms,
    )


def _login(client, *, admin_id: int, is_super: bool):
    with client.session_transaction() as sess:
        sess["admin_id"] = admin_id
        sess["admin_user"] = f"admin{admin_id}"
        sess["admin_name"] = f"Admin {admin_id}"
        sess["is_super_admin"] = is_super
        sess["tenant_id"] = 1
        sess["_csrf_token"] = "off-csrf"
        sess["permissions"] = ["cards.view", "cards.generate"]


def _preview(client, csv_bytes: bytes):
    return client.post(
        "/admin/radius/cards/batches/import/preview",
        data={"file": (io.BytesIO(csv_bytes), "cards.csv"), "_csrf_token": "off-csrf"},
        content_type="multipart/form-data",
        headers={"X-CSRFToken": "off-csrf"},
    )


# ═══ 1. permission presence + label ═════════════════════════════════════════
def test_can_import_batches_default_off(app):
    from app.radius.services.manager_distributor_ops import DEFAULT_PERMISSIONS

    assert DEFAULT_PERMISSIONS.get("can_import_batches") is False


def test_can_import_batches_label(app):
    from app.radius.services.permission_labels import permission_label

    assert permission_label("can_import_batches") == "استيراد الحِزم"


# ═══ 2. gate: analyze returns JSON 403 for a manager without the perm ═══════
def test_manager_without_permission_preview_json_403(app):
    with app.app_context():
        mgr = _sub_admin("imp_no")
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = _preview(client, b"username,password\na,1\n")
    assert res.status_code == 403
    # JSON, not a raw HTML page.
    assert res.is_json
    assert "صلاحية" in res.get_json().get("error", "")
    assert "<!doctype" not in res.get_data(as_text=True).lower()


def test_manager_with_permission_preview_ok(app):
    with app.app_context():
        mgr = _sub_admin("imp_yes")
        _grant(mgr, can_import_batches=True)
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        res = _preview(client, b"username,password\na,1\nb,2\n")
    assert res.status_code == 200
    assert res.get_json()["ok"] is True


def test_owner_preview_ok(app):
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _preview(client, b"username,password\na,1\n")
    assert res.status_code == 200


# ═══ 3. dry-run analysis categorises rows (and imports nothing) ═════════════
def test_analyze_categorises_duplicates_and_invalid(app):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        svc = get_cards_service()
        # seed an existing card so "in_system" duplicate can be detected.
        svc.import_batch(actor="t", plan_id=plan, cards=[{"username": "exists1", "password": "p"}],
                         source_type="external")
        report = svc.analyze_import([
            {"username": "new1", "password": "a"},
            {"username": "new2", "password": "b"},
            {"username": "new1", "password": "c"},   # in-file duplicate
            {"username": "exists1", "password": "d"},  # in-system duplicate
            {"username": "", "password": "e"},          # empty / invalid
        ])
        assert report["total"] == 5
        assert report["valid_count"] == 2                     # new1, new2
        assert report["duplicate_in_file"]["count"] == 1
        assert report["duplicate_in_system"]["count"] == 1
        assert "exists1" in report["duplicate_in_system"]["samples"]
        assert report["invalid"] and report["invalid"][0]["count"] == 1
        # the analysis wrote nothing extra: still only the 1 seeded card.
        n = db().execute("SELECT COUNT(*) c FROM cards").fetchone()["c"]
        assert n == 1


def test_preview_endpoint_returns_categorised_report(app):
    with app.app_context():
        _plan_id()
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        res = _preview(client, b"username,password\nx,1\ny,2\nx,3\n")
    data = res.get_json()
    rep = data["report"]
    assert rep["parsed_total"] == 3
    assert rep["valid_count"] == 2
    assert rep["duplicate_in_file"]["count"] == 1
    # csv_text carries ONLY the valid rows for the later commit.
    assert "x" in data["csv_text"] and "y" in data["csv_text"]


# ═══ 4. commit imports only valid; 0 valid → no batch; total computed ═══════
def test_commit_imports_only_valid_and_computes_total(app):
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        svc = get_cards_service()
        result = svc.import_batch(
            actor="t", plan_id=plan, source_type="external", price_per_card=2.0,
            cards=[
                {"username": "v1", "password": "a"},
                {"username": "v2", "password": "b"},
                {"username": "v1", "password": "c"},   # in-file dup → skipped
                {"username": "", "password": "d"},       # invalid → skipped
            ],
        )
        assert result["inserted_count"] == 2
        batch = result["batch"]
        assert int(batch.count) == 2                 # NOT 4 — no fabricated count
        assert abs(float(batch.total_price) - 4.0) < 0.001   # 2 valid × 2.00


def test_commit_zero_valid_creates_no_batch(app):
    from app.radius.core.errors import RadiusValidationError
    from app.radius.services.cards import get_cards_service

    with app.app_context():
        plan = _plan_id()
        svc = get_cards_service()
        # seed so the only rows are all in-system duplicates.
        svc.import_batch(actor="t", plan_id=plan, source_type="external",
                         cards=[{"username": "dup", "password": "p"}])
        batches_before = db().execute("SELECT COUNT(*) c FROM card_batches").fetchone()["c"]
        with pytest.raises(RadiusValidationError):
            svc.import_batch(actor="t", plan_id=plan, source_type="external",
                             cards=[{"username": "dup", "password": "x"},
                                    {"username": "", "password": "y"}])
        batches_after = db().execute("SELECT COUNT(*) c FROM card_batches").fetchone()["c"]
        assert batches_after == batches_before        # no empty/garbage batch


# ═══ 5. manual total field removed; manager generate summary enriched ═══════
def test_import_page_has_no_manual_total_input(app):
    with app.test_client() as client:
        _login(client, admin_id=1, is_super=True)
        html = client.get("/admin/radius/cards/batches/import").get_data(as_text=True)
    # the manual total input is gone; the computed reference breakdown is shown.
    assert 'name="total_price"' not in html
    assert "إجمالي الجملة" in html and "هامش الربح" in html


def test_manager_generate_summary_shows_all_offer_attrs(app):
    from app.radius.services.card_offers import CardOffersService

    with app.app_context():
        plan = _plan_id()
        mgr = _sub_admin("gen_mgr")
        CardOffersService(tenant_id=1).create_offer(
            name="عرض", duration_minutes=480, wholesale="2.00", selling="5.00",
            plan_id=plan, visible_admin_ids=[mgr],
        )
    with app.test_client() as client:
        _login(client, admin_id=mgr, is_super=False)
        html = client.get("/admin/radius/cards/generate").get_data(as_text=True)
    # the locked summary now exposes wholesale, quota, and margin (not just 3).
    assert "سعر الجملة / بطاقة" in html
    assert "الكوتا" in html
    assert "هامش الربح / بطاقة" in html
