"""SEC H4/M6 — stored XSS in the marketplace package-file table.

card_marketplace_package_file.html built cell values as
``('<bdi>' ~ it.username ~ '</bdi>') | safe`` — the |safe rendered the
attacker-controllable card username/password/batch_code as RAW HTML in the
operator's page, so an imported card with username ``<img onerror=…>`` executed
in the admin origin. Fix: escape the dynamic value (``literal|safe ~ value|e``)
while keeping the ``<bdi>`` RTL wrapper.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_TEMPLATE = (Path(__file__).resolve().parents[1]
             / "app" / "templates" / "radius"
             / "card_marketplace_package_file.html")


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_mxss_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "t.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    yield create_app()
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


def test_bdi_cell_expression_escapes_payload_but_keeps_wrapper(app):
    from flask import render_template_string
    payload = "<img src=x onerror=alert(document.cookie)>"
    tpl = "{{ ('<bdi>'|safe ~ (u or '—')|e ~ '</bdi>'|safe) }}"
    with app.test_request_context("/"):
        out = render_template_string(tpl, u=payload)
    assert "<bdi>" in out and "</bdi>" in out         # RTL wrapper preserved
    assert "<img" not in out                           # payload NOT a raw tag
    assert "&lt;img" in out                            # payload escaped (inert)


def test_template_has_no_unescaped_bdi_safe_sink():
    src = _TEMPLATE.read_text(encoding="utf-8")
    # The vulnerable shape was: ~ (it.<field> or "—") ~ '</bdi>') | safe
    # i.e. a dynamic value concatenated INTO a |safe string without |e.
    import re
    bad = re.findall(r"~\s*\(it\.[a-z_]+ or [^)]*\)\s*~\s*'</bdi>'\)\s*\|\s*safe", src)
    assert not bad, f"un-escaped |safe bdi sink(s) remain: {bad}"
    # And the fixed shape (value|e inside a |safe wrapper) is present.
    assert "|e ~ '</bdi>'|safe" in src
