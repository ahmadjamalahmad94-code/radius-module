from __future__ import annotations

import os
import sys
import tempfile


def test_demo_seed_tops_up_operational_tables(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_seed_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]

    from app import create_app
    from app.radius.seed import seed_demo_data

    app = create_app()
    with app.app_context():
        summary = seed_demo_data(force=True)

    assert summary["subscribers"] >= 25
    assert summary["plans"] >= 8
    assert summary["nas"] >= 4
    assert summary["card_batches"] >= 3
    assert summary["cards"] >= 25
    assert summary["sessions"] >= 25
    assert summary["payments"] >= 15
    assert summary["loans"] >= 6

    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def test_demo_seed_does_not_auto_run_in_production_without_opt_in(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_seed_prod_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_ENV", "production")
    # SEC H5 — a production boot now requires a non-default FLASK_SECRET.
    monkeypatch.setenv("FLASK_SECRET", "test-production-secret-32-bytes-min-xx")
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]

    from app import create_app
    from app.radius.db.connection import db

    app = create_app()
    with app.app_context():
        row = db().execute(
            "SELECT COUNT(*) AS c FROM access_plans WHERE tenant_id = 1"
        ).fetchone()

    assert int(row["c"] or 0) == 0

    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
