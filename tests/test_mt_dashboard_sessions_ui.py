"""Router dashboard «المتصلون الآن» panel — UX guards.

Two owner-reported defects:
  1. The «المدة» (uptime) cell mixes digits + Arabic unit letters (س/د); with
     only dir="ltr" (isolate) the bidi W2 rule reorders it. It must force LTR
     with unicode-bidi:isolate-override — in BOTH the server template and the JS
     row-builder (which repaints on every 10s poll).
  2. All sessions ARE rendered (count == len(sessions) invariant), but the list
     had no scroll container so the card clipped to ~2 rows. The list must be a
     vertical scroll container.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_duration_cell_forces_ltr_override_in_template():
    html = (ROOT / "app/templates/radius/mt_dashboard.html").read_text(encoding="utf-8")
    # the المدة cell in the server-rendered row
    assert 'unicode-bidi:isolate-override' in html
    # anchored to the uptime cell specifically (dir=ltr + override together)
    assert 'dir="ltr" style="unicode-bidi:isolate-override"' in html


def test_duration_cell_forces_ltr_override_in_js():
    js = (ROOT / "app/static/js/mt_dashboard.js").read_text(encoding="utf-8")
    # the JS repaints rows every poll — the uptime td must carry the override too
    assert 'dir="ltr" style="unicode-bidi:isolate-override"' in js


def test_sessions_list_is_scrollable():
    html = (ROOT / "app/templates/radius/mt_dashboard.html").read_text(encoding="utf-8")
    css = html.replace(" ", "").replace("\n", "")
    # .mt-users-list becomes a bounded vertical-scroll container
    assert ".mt-users-list{" in css
    assert "overflow-y:auto" in css
    assert "max-height:" in css
    # sticky header so column labels stay visible while scrolling
    assert "position:sticky" in css
