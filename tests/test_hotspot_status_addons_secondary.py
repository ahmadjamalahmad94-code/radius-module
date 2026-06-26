# -*- coding: utf-8 -*-
"""Post-login «الحالة» (status) bug fix:

The owner opened the status screen and saw ONLY a «نقاط الولاء» (loyalty) addon
card under a «تم الاتصال» header — the real session status (uptime / bytes /
IP+MAC / logout) didn't show. Root cause: post-login addon widgets were rendered
on a SEPARATE page (redirect.html) that never advanced to the status page.

Fix: the status page (build_status) now always shows the session details +
logout, with addons injected as a SECONDARY block BELOW; and the connected page
(build_redirect_page) auto-redirects to status.html."""
from __future__ import annotations

from app.radius.services import hotspot_addons as ad
from app.radius.services import hotspot_companion_pages as hcp
from app.radius.services import hotspot_surfaces as sf

VALUES = {"TENANT_NAME": "شبكة الأمل", "ACCENT_COLOR": "#2563EB"}


def _loyalty_cfg():
    return ad.normalize_config({"loyalty": {
        "enabled": True,
        "message": "انضم لبرنامج الولاء واكسب نقاطًا مع كل زيارة!",
        "join_url": "https://example.com/join"}})


def test_status_shows_session_details_and_logout_with_loyalty_secondary():
    pages = hcp.build_all_companions(VALUES, addons_cfg=_loyalty_cfg())
    status = pages["status.html"]
    # the REAL status content is always present
    assert "$(uptime)" in status                       # session uptime
    assert "$(ip)" in status and "$(mac)" in status     # IP + MAC
    assert 'action="$(link-logout)"' in status and "تسجيل الخروج" in status
    # loyalty addon IS present...
    assert "نقاط الولاء" in status
    # ...as a SECONDARY block, AFTER the logout button (never instead of it)
    assert status.index("تسجيل الخروج") < status.index("نقاط الولاء")
    assert 'class="hr-addons"' in status and "عروض وإضافات" in status


def test_status_without_addons_is_unchanged():
    """No addons configured → status page is exactly the session panel, no
    secondary block (no regression for the common case)."""
    status = hcp.build_all_companions(VALUES)["status.html"]
    assert "تسجيل الخروج" in status and "$(uptime)" in status
    assert "نقاط الولاء" not in status
    assert 'class="hr-addons"' not in status


def test_status_never_breaks_on_bad_addons_cfg():
    """A malformed addons config must never blow up the status page."""
    status = hcp.build_status(
        __import__("app.radius.services.hotspot_templates",
                   fromlist=["validate_vars"]).validate_vars(VALUES),
        addons_cfg="not-a-dict")
    assert "تسجيل الخروج" in status   # still a valid status page


def test_connected_page_auto_redirects_to_status():
    """build_redirect_page (the «تم الاتصال» / loyalty page) must auto-forward to
    the real status page — what the owner expected («تلقائي يحولني عالستيتاس»)."""
    red = sf.build_redirect_page(VALUES, _loyalty_cfg())
    assert 'http-equiv="refresh"' in red and "url=status.html" in red   # meta forward
    assert "location.href='status.html'" in red                          # JS fallback
    assert 'href="status.html"' in red                                   # manual link
    # the connected confirmation is still there
    assert "تم الاتصال بالإنترنت" in red
