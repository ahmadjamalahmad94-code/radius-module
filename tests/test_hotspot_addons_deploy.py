# -*- coding: utf-8 -*-
"""P2 — ربط وقت النشر بإطار الإضافات: deploy_login يحقن أجزاء «قبل
الدخول» في login.html المرفوع، ويُبقي placeholders المايكروتيك سليمة،
وبلا إضافات = ناتج مطابق. شغّل الملف وحده."""
from __future__ import annotations

import pytest


class _FakeRouter:
    """يسجّل كل نداء؛ /file/print يعيد صفوفًا معرّفة، الباقي no-op."""
    def __init__(self, *, file_print_rows=None, raise_on=None):
        self.file_print_rows = file_print_rows or []
        self.raise_on = raise_on or {}
        self.calls = []

    def connect(self): pass
    def close(self): pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        if path in self.raise_on:
            raise RuntimeError(self.raise_on[path])
        if path == "/file/print":
            return list(self.file_print_rows)
        return []


def _uploaded(fake):
    """نص الملف المرفوع (contents من /file/add أو /file/set)."""
    for path, attrs in fake.calls:
        if path in ("/file/add", "/file/set") and "contents" in attrs:
            return attrs["contents"]
    return ""


_REQUIRED_MT = ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)")


def test_deploy_login_injects_prelogin_addon():
    from app.radius.services.hotspot_templates import deploy_login
    fake = _FakeRouter(file_print_rows=[])
    cfg = {"live_clock": {"enabled": True, "config": {"format": "24h"}}}
    res = deploy_login(fake, "mikrotik", {}, addons=cfg)
    assert res.ok is True
    html = _uploaded(fake)
    assert "hr-clock" in html, "جزء الساعة لم يُحقن في login.html المرفوع"
    assert "hr-addon:live_clock" in html


def test_deploy_login_keeps_mikrotik_placeholders_with_addons():
    from app.radius.services.hotspot_templates import deploy_login
    fake = _FakeRouter(file_print_rows=[])
    cfg = {"announcements": {"enabled": True,
                            "config": {"title": "x", "body": "a\nb"}}}
    deploy_login(fake, "mikrotik", {}, addons=cfg)
    html = _uploaded(fake)
    for tok in _REQUIRED_MT:
        assert tok in html, f"placeholder مفقود بعد الحقن: {tok}"


def test_deploy_login_no_addons_identical():
    from app.radius.services.hotspot_templates import deploy_login
    a = _FakeRouter(file_print_rows=[])
    b = _FakeRouter(file_print_rows=[])
    deploy_login(a, "classic", {})
    deploy_login(b, "classic", {}, addons={})
    assert _uploaded(a) == _uploaded(b)
    # لا أثر لأي إضافة
    assert "hr-addon:" not in _uploaded(b)


def test_deploy_login_disabled_addon_not_injected():
    from app.radius.services.hotspot_templates import deploy_login
    fake = _FakeRouter(file_print_rows=[])
    cfg = {"live_clock": {"enabled": False}}
    deploy_login(fake, "classic", {}, addons=cfg)
    assert "hr-clock" not in _uploaded(fake)


def test_deploy_login_post_only_addon_not_in_login():
    """إضافة post فقط (روابط تواصل) لا تظهر في login.html."""
    from app.radius.services.hotspot_templates import deploy_login
    fake = _FakeRouter(file_print_rows=[])
    cfg = {"social_links": {"enabled": True,
                            "config": {"whatsapp": "https://wa.me/1"}}}
    deploy_login(fake, "classic", {}, addons=cfg)
    assert "hr-soc" not in _uploaded(fake)
