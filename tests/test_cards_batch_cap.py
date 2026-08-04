"""سقف عدد البطاقات في الدفعة — كان 2000 مثبّتًا، صار إعدادًا افتراضه بلا حدّ."""
from __future__ import annotations

import os

import pytest


def db():
    from app.radius.db.connection import db as live_db

    return live_db()


@pytest.fixture
def app(monkeypatch, tmp_path):
    db_file = os.path.join(tmp_path, "batch_cap.db")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", db_file)
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    from app.radius.db.connection import reset_for_tests

    reset_for_tests(db_file)
    from app import create_app

    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import tenants_repo

        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
    return flask_app


def _plan() -> int:
    cur = db().execute(
        """
        INSERT INTO access_plans(tenant_id, name, duration_minutes, validity_days,
                                 price, currency, created_at, updated_at)
        VALUES(1,'Cap Plan',1440,30,1.0,'ILS',datetime('now'),datetime('now'))
        """
    )
    return int(cur.lastrowid)


def test_default_is_unlimited(app):
    with app.app_context():
        from app.radius.services.cards import max_cards_per_batch
        assert max_cards_per_batch(1) == 0


def test_setting_caps_and_zero_means_unlimited(app):
    with app.app_context():
        from app.radius.db.repos import tenants_repo
        from app.radius.services.cards import max_cards_per_batch

        tenants_repo.set_setting(1, "cards.max_per_batch", "500")
        assert max_cards_per_batch(1) == 500
        tenants_repo.set_setting(1, "cards.max_per_batch", "0")
        assert max_cards_per_batch(1) == 0
        # إعداد تالف = بلا حدّ (لا يَحبس المالك)
        tenants_repo.set_setting(1, "cards.max_per_batch", "abc")
        assert max_cards_per_batch(1) == 0


def test_generate_above_old_2000_limit_succeeds(app):
    """الحدّ القديم كان يرفض 2500 — الآن يمرّ ويُنشئ العدد كاملًا."""
    with app.app_context():
        plan_id = _plan()
        from app.radius.services.cards import get_cards_service

        batch, cards = get_cards_service().generate_batch(
            actor="test", plan_id=plan_id, count=2500, username_length=10, password_length=6,
        )
        assert len(cards) == 2500
        n = db().execute("SELECT COUNT(*) FROM cards WHERE batch_id=?",
                         (batch.id,)).fetchone()[0]
        assert n == 2500


def test_explicit_cap_is_enforced(app):
    with app.app_context():
        plan_id = _plan()
        from app.radius.core.errors import RadiusValidationError
        from app.radius.db.repos import tenants_repo
        from app.radius.services.cards import get_cards_service

        tenants_repo.set_setting(1, "cards.max_per_batch", "100")
        with pytest.raises(RadiusValidationError):
            get_cards_service().generate_batch(actor="test", plan_id=plan_id, count=101)
        # ضمن الحدّ يمرّ
        _b, cards = get_cards_service().generate_batch(actor="test", plan_id=plan_id, count=100)
        assert len(cards) == 100


def test_zero_or_negative_still_rejected(app):
    with app.app_context():
        plan_id = _plan()
        from app.radius.core.errors import RadiusValidationError
        from app.radius.services.cards import get_cards_service

        for bad in (0, -5):
            with pytest.raises(RadiusValidationError):
                get_cards_service().generate_batch(actor="test", plan_id=plan_id, count=bad)
