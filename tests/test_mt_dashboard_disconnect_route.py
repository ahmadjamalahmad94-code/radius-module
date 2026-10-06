"""F-01 — web MikroTik dashboard «قطع» (session disconnect) buttons.

Reproduction: the dashboard JS used to POST to
``/api/v1/mikrotik/<id>/ppp/disconnect`` and ``.../hotspot/disconnect`` with a
JSON body. Neither URL is registered, so every click got a 404 — and because
``api()`` never rejects and the caller ignored ``res.ok``, the row was removed
optimistically as if the kick had worked (it re-appeared on the next poll).

The real endpoints (also used by the Flutter app, mikrotik_repository.dart) are
``POST /api/v1/mikrotik/<nas>/{ppp,hotspot}/active/<session_id>/disconnect``.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest
from werkzeug.exceptions import MethodNotAllowed, NotFound

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app/static/js/mt_dashboard.js"


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_f01_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    a = create_app()
    a.testing = True
    yield a
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _match(app, path, method="POST"):
    return app.url_map.bind("localhost").match(path, method=method)


@pytest.mark.parametrize("kind", ["ppp", "hotspot"])
def test_old_dashboard_urls_are_not_routes(app, kind):
    """The URLs the old JS posted to do not exist (repro of F-01)."""
    with pytest.raises((NotFound, MethodNotAllowed)):
        _match(app, f"/api/v1/mikrotik/7/{kind}/disconnect")
    resp = app.test_client().post(
        f"/api/v1/mikrotik/7/{kind}/disconnect", json={"id": "*1A"})
    assert resp.status_code in (404, 405)


@pytest.mark.parametrize("kind", ["ppp", "hotspot"])
def test_real_disconnect_endpoint_matches_session_id(app, kind):
    endpoint, args = _match(
        app, f"/api/v1/mikrotik/7/{kind}/active/*1A/disconnect")
    assert endpoint.endswith(f"v1.mt_{kind}_disconnect")
    assert args == {"nas_id": 7, "session_id": "*1A"}


def _handler_src() -> str:
    js = JS.read_text(encoding="utf-8")
    i = js.index('closest("[data-mt-disconnect]")')
    return js[i:i + 3000]


def test_dashboard_js_posts_to_the_real_endpoint():
    src = _handler_src()
    assert '"/ppp/active/"' in src and '"/hotspot/active/"' in src
    assert 'encodeURIComponent(id) + "/disconnect"' in src
    # the dead URLs must not come back
    assert '"/ppp/disconnect"' not in src
    assert '"/hotspot/disconnect"' not in src


def test_dashboard_js_checks_the_response_before_removing_row():
    """api() never rejects — the handler must inspect res.ok / body.ok /
    data.ok and throw, otherwise a failure still looks like success."""
    src = _handler_src()
    check = src.index("res.ok")
    remove = src.index("tr.remove()")
    assert check < remove
    assert re.search(r"inner\.ok\s*===\s*false", src)
    assert "throw new Error" in src
