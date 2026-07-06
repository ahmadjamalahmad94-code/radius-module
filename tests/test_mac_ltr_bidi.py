"""SEC/UX — MAC addresses stay LTR-isolated in the RTL panel.

A MAC like D8:93:D4:E2:03:5B next to Arabic text (e.g. «· متصل» or a device
label) gets its segments scrambled by the bidi algorithm because the colons
are neutral and the page direction is RTL. The fix isolates every MAC display
as an LTR run.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mono_class_is_ltr_isolated():
    css = (ROOT / "app/static/css/admin_layout.css").read_text(encoding="utf-8")
    # the shared technical-value class (MAC/IP/hex/timestamp) forces LTR.
    line = next(l for l in css.splitlines() if l.strip().startswith(".mono{"))
    assert "direction:ltr" in line
    assert "unicode-bidi:isolate" in line


def test_mac_history_chip_isolates_mac():
    html = (ROOT / "app/templates/radius/users_form.html").read_text(encoding="utf-8")
    # the login-history chip wraps the MAC in an LTR bdi so «· متصل» can't
    # flip its segments.
    assert '<bdi dir="ltr">{{ item.mac }}</bdi>' in html


def test_mac_pill_js_isolates_mac():
    html = (ROOT / "app/templates/radius/users_form.html").read_text(encoding="utf-8")
    # the JS-built pill sets the MAC span to dir=ltr (isolated from the
    # optional Arabic device label).
    assert "text.dir = 'ltr'" in html
