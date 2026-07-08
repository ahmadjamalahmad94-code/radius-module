"""Smoke test for the 1.1.0 release demo of the self-update flow.

Confirms the panel footer renders the REAL running version (via the
``running_version`` Jinja global) instead of the old hardcoded «0.1.0», and
that ``app_version`` reports the bumped release.
"""
from app.radius.core import app_version


def test_app_version_is_bumped():
    assert app_version.APP_VERSION == "1.1.0"


def test_running_version_reports_bumped(monkeypatch):
    # Clear the operator/CI override so we assert the baked release version.
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    assert app_version.running_version() == "1.1.0"


def test_footer_renders_running_version(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    from app import create_app

    app = create_app()
    rv = app.jinja_env.globals["running_version"]
    assert rv() == "1.1.0"
    # The footer template string renders the live version, not 0.1.0.
    rendered = app.jinja_env.from_string(
        "{{ _('الإصدار %(v)s', v=running_version()) }}"
    ).render()
    assert "1.1.0" in rendered
    assert "0.1.0" not in rendered
