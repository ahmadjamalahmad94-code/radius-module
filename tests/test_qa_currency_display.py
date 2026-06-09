"""QA: price/currency display must follow the panel's configured currency.

Bug: some defaults hardcoded JOD ("دينار أردني" / "د.أ") instead of reading
billing.currency. This locks in that format_money (the `money` filter) and the
empty/legacy record-currency fallback both respect the configured currency.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


@pytest.fixture(autouse=True)
def _restore_env():
    keys = ("HOBERADIUS_DB_PATH", "HOBERADIUS_NO_WORKER", "HOBERADIUS_NO_SEED")
    saved = {k: os.environ.get(k) for k in keys}
    yield
    for k, v in saved.items():
        os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def _fresh_app():
    tmp = tempfile.mkdtemp(prefix="hr_cur_")
    os.environ["HOBERADIUS_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["HOBERADIUS_NO_WORKER"] = "1"
    os.environ["HOBERADIUS_NO_SEED"] = "1"
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


def _set_currency(code):
    from app.radius.core.tenant import DEFAULT_TENANT_ID
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(DEFAULT_TENANT_ID, "billing.currency", code)


def test_format_money_follows_configured_currency():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.system_config import format_money
        _set_currency("ILS")
        assert "₪" in format_money(100)
        assert "د.أ" not in format_money(100)        # not JOD
        _set_currency("JOD")
        assert "د.أ" in format_money(100)
        assert "₪" not in format_money(100)


def test_empty_or_missing_record_currency_uses_panel_not_jod():
    app = _fresh_app()
    with app.app_context():
        from app.radius.core.system_config import format_money
        _set_currency("ILS")
        # empty-string and None currency must fall back to the panel currency,
        # never to a hardcoded JOD.
        assert "₪" in format_money(50, "")
        assert "₪" in format_money(50, None)
        assert "د.أ" not in format_money(50, "")
