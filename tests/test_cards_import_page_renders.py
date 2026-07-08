"""Regression: GET /admin/radius/cards/batches/import must return 200.

Guards the card-batches *import* page against a live 500 that surfaced right
after the plans/offer + store-purchase merges. The import GET builds its
context from ``get_plans_service().list()`` and renders
``radius/cards_import.html`` (which imports the shared ``_partials/hub.html``
macros). Two failure classes are covered:

 1. A panel-wide boot failure. ``create_app`` registers the Jinja global
    ``fmt_base_time_ar`` by importing ``app.radius.core.duration_fmt`` at boot
    time. That import used to be UNGUARDED — a missing/broken formatter module
    (the file is easy to leave uncommitted) aborted ``create_app`` entirely, so
    EVERY admin page (including this one) 500'd, not just the pages that use the
    formatter. The import is now defensive with a self-contained fallback; this
    test asserts the app boots and the page renders even when the formatter
    module cannot be imported.

 2. The ordinary render path: an authorized (super) admin GETs the page and
    gets 200 with a populated plans dropdown.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime

import pytest


def _fresh_app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_import_page_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    # NO_SEED + the conftest-default LICENSE_GATE_TEST_BYPASS lets the licensed
    # gate pass so we actually exercise the render (not a gate 302).
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    return create_app()


@pytest.fixture
def app(monkeypatch):
    yield _fresh_app(monkeypatch)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def _seed_plan(app) -> None:
    """One access_plans row so the import dropdown renders a real option."""
    with app.app_context():
        from app.radius.db.connection import db
        db().execute(
            "INSERT INTO access_plans (tenant_id, name, price, enabled, created_at)"
            " VALUES (1, 'Plan A', 5.0, 1, ?)",
            (datetime.utcnow().isoformat() + "Z",),
        )


def _authorized_client(app):
    """A test client whose session is an authorized super admin on tenant 1."""
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["is_super_admin"] = True
        sess["tenant_id"] = 1
        sess["permissions"] = []
        sess["admin_user"] = "owner"
        sess["admin_name"] = "owner"
    return client


URL = "/admin/radius/cards/batches/import"


def test_import_page_get_returns_200(app):
    _seed_plan(app)
    resp = _authorized_client(app).get(URL)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:800]
    body = resp.get_data(as_text=True)
    # The plans dropdown rendered the seeded option (proves the plans-service
    # + template path executed end-to-end, not just an empty error page).
    assert "Plan A" in body


def test_import_page_survives_missing_duration_formatter(monkeypatch):
    """The panel must still boot + render this page when the base-time formatter
    module cannot be imported (defensive import in create_app)."""
    # Force the formatter import to fail during create_app.
    import builtins

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "app.radius.core.duration_fmt" or name.endswith("core.duration_fmt"):
            raise ModuleNotFoundError("simulated missing duration_fmt")
        return real_import(name, *args, **kwargs)

    tmp = tempfile.mkdtemp(prefix="hr_import_page_deg_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    monkeypatch.setattr(builtins, "__import__", _boom)
    from app import create_app
    app = create_app()  # must NOT raise despite the failed formatter import
    monkeypatch.setattr(builtins, "__import__", real_import)
    try:
        resp = _authorized_client(app).get(URL)
        assert resp.status_code == 200, resp.get_data(as_text=True)[:800]
    finally:
        for k in list(sys.modules):
            if k.startswith("app."):
                del sys.modules[k]


# The card-batches import page is the only page that uses cards_import_engine
# (smart CSV/Excel/PDF parser). The optional parser libraries (openpyxl for
# Excel, pdfplumber/pypdf for PDF) may be absent from a stripped deployment.
# They are imported LAZILY and guarded, so a missing lib must degrade the smart
# import to a friendly message — never 500 the page or the preview endpoint.
_PARSER_LIBS = ("openpyxl", "pdfplumber", "pypdf", "PyPDF2", "fitz")


def _block_parser_libs(monkeypatch):
    """Make every optional parser lib unimportable for the duration of a test."""
    for name in _PARSER_LIBS:
        monkeypatch.setitem(sys.modules, name, None)  # `import name` -> ImportError


def test_import_page_get_renders_without_parser_libs(app, monkeypatch):
    _seed_plan(app)
    _block_parser_libs(monkeypatch)
    resp = _authorized_client(app).get(URL)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:800]


@pytest.mark.parametrize(
    "filename, blob",
    [("cards.xlsx", b"PK\x03\x04not-a-real-xlsx"), ("cards.pdf", b"%PDF-1.4 not-real")],
)
def test_import_preview_degrades_without_parser_libs(app, monkeypatch, filename, blob):
    """Uploading an Excel/PDF when the parser lib is missing returns a clean
    JSON response (with a warning / friendly error), NOT a 500."""
    import io as _io

    _block_parser_libs(monkeypatch)
    client = _authorized_client(app)
    # Establish the CSRF token the smart-import POST carries.
    client.get(URL)
    with client.session_transaction() as sess:
        token = sess.get("_csrf_token", "")
    data = {"file": (_io.BytesIO(blob), filename)}
    if token:
        data["_csrf_token"] = token
    resp = client.post(
        "/admin/radius/cards/batches/import/preview",
        data=data,
        content_type="multipart/form-data",
    )
    assert resp.status_code != 500, resp.get_data(as_text=True)[:800]
    # Response is valid JSON the UI can render (either ok:true+warnings, or a
    # friendly ok:false error) — the smart-import feature degraded gracefully.
    assert resp.get_json() is not None
