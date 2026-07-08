"""Smoke test for the 1.2.0 release demo (install-flow test).

Confirms the baked version is bumped and the «تحديث النظام» page renders the
new installed-build marker with the live running version.
"""
from app.radius.core import app_version


def test_app_version_is_1_2_0(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    assert app_version.APP_VERSION == "1.2.0"
    assert app_version.running_version() == "1.2.0"


def test_1_1_0_sees_update_available_to_1_2_0():
    # A 1.1.0 customer must see 1.2.0 advertised as newer.
    assert app_version.is_newer("1.2.0", "1.1.0") is True


def test_installed_marker_renders_running_version(monkeypatch):
    monkeypatch.delenv("HOBERADIUS_VERSION", raising=False)
    from app import create_app

    app = create_app()
    rendered = app.jinja_env.from_string(
        "{{ _('محدّث — إصدار %(v)s', v=running_version()) }}"
    ).render()
    assert "1.2.0" in rendered
    assert "محدّث" in rendered
