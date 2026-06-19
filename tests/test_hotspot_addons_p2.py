# -*- coding: utf-8 -*-
"""P2 — إضافات المحتوى + الثيمات: التسجيل، التوليد، الأمان، وصحّة
المايكروتيك بعد الحقن. شغّل الملف وحده."""
from __future__ import annotations

import pytest

CONTENT = ("internet_radio", "news_ticker", "prayer_times", "weather",
           "image_carousel", "qr_menu", "survey")
THEMES = ("theme_glass", "theme_gradient", "theme_dark", "theme_animations",
          "theme_fullscreen_bg", "theme_minimal", "theme_branded",
          "theme_seasonal")


def test_all_p2_addons_registered():
    from app.radius.services import hotspot_addons as ad
    for k in CONTENT + THEMES:
        assert k in ad.ADDONS, f"إضافة P2 غير مسجَّلة: {k}"


def test_themes_inject_style_prelogin():
    from app.radius.services import hotspot_addons as ad
    for k in THEMES:
        cfg = {k: {"enabled": True, "config": {
            "image_url": "https://cdn.x/bg.jpg", "season": "ramadan"}}}
        html = ad.render_prelogin_fragments(ad.normalize_config(cfg),
                                            {"accent": "#2563EB"})
        assert "<style>" in html, f"{k}: لا كتلة style"


def test_prayer_times_offline_calc_and_hijri():
    from app.radius.services import hotspot_addons as ad
    cfg = {"prayer_times": {"enabled": True,
                            "config": {"lat": "21.42", "lon": "39.83",
                                       "tz": "3", "method": "umm_alqura"}}}
    html = ad.render_prelogin_fragments(ad.normalize_config(cfg), {})
    # حساب في المتصفّح (لا fetch) + هجري + كل الصلوات
    assert "hr-pray-grid" in html
    assert "hr-hijri" in html
    assert "fetch(" not in html and "XMLHttpRequest" not in html
    for p in ("الفجر", "الظهر", "العصر", "المغرب", "العشاء"):
        assert p in html


def test_qr_menu_bakes_svg():
    from app.radius.services import hotspot_addons as ad
    cfg = {"qr_menu": {"enabled": True,
                       "config": {"url": "https://menu.example.com"}}}
    html = ad.render_prelogin_fragments(ad.normalize_config(cfg), {})
    assert "<svg" in html and "</svg>" in html


def test_news_ticker_escapes_and_bakes():
    from app.radius.services import hotspot_addons as ad
    cfg = {"news_ticker": {"enabled": True,
                           "config": {"items": "<b>خبر</b>\nثانٍ"}}}
    html = ad.render_prelogin_fragments(ad.normalize_config(cfg), {})
    assert "hr-ticker" in html
    assert "<b>خبر</b>" not in html and "&lt;b&gt;" in html
    assert "ثانٍ" in html


def test_weather_failsafe_no_coords():
    from app.radius.services import hotspot_addons as ad
    cfg = {"weather": {"enabled": True, "config": {"city": "جدة"}}}
    # بلا إحداثيات → لا بطاقة (لا نُظهر فارغًا مضلِّلًا)
    assert ad.render_prelogin_fragments(ad.normalize_config(cfg), {}) == ""


def test_weather_renders_with_data(monkeypatch):
    from app.radius.services import hotspot_addons_content as c
    from app.radius.services import hotspot_addons as ad
    monkeypatch.setattr(c, "fetch_weather",
                        lambda lat, lon, **k: {"temp": 28.4, "code": 0})
    cfg = {"weather": {"enabled": True,
                       "config": {"city": "جدة", "lat": "21.4", "lon": "39.8"}}}
    html = ad.render_prelogin_fragments(ad.normalize_config(cfg), {})
    assert "جدة" in html and "28" in html


def test_carousel_and_fsbg_collect_domains():
    from app.radius.services import hotspot_addons as ad
    cfg = {"image_carousel": {"enabled": True,
                              "config": {"images": "https://cdn.aa.com/1.jpg"}},
           "theme_fullscreen_bg": {"enabled": True,
                                   "config": {"image_url": "https://img.bb.com/x.jpg"}}}
    doms = ad.collect_walled_garden_domains(ad.normalize_config(cfg))
    assert "cdn.aa.com" in doms
    assert "img.bb.com" in doms


def test_radio_post_widget_and_domain():
    from app.radius.services import hotspot_addons as ad
    cfg = {"internet_radio": {"enabled": True,
                              "config": {"stream_url": "https://stream.cc.fm/live"}}}
    norm = ad.normalize_config(cfg)
    w = ad.render_postlogin_widgets(norm, {})
    assert "<audio" in w
    assert "stream.cc.fm" in ad.collect_walled_garden_domains(norm)


# ── صحّة المايكروتيك بعد حقن كل إضافات P2 ──
class _FakeRouter:
    def __init__(self):
        self.calls = []

    def connect(self): pass
    def close(self): pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        return [] if path == "/file/print" else []


def _uploaded(fake):
    for p, a in fake.calls:
        if p in ("/file/add", "/file/set") and "contents" in a:
            return a["contents"]
    return ""


def test_deploy_with_all_pre_addons_keeps_placeholders():
    from app.radius.services.hotspot_templates import deploy_login
    pre = ("live_clock", "announcements", "news_ticker", "prayer_times",
           "qr_menu", "image_carousel", "countdown_access", "emergency_notice",
           *THEMES)
    cfg = {k: {"enabled": True, "config": {
        "body": "x", "items": "a\nb", "url": "https://m.x/q",
        "images": "https://cdn.x/1.jpg", "image_url": "https://cdn.x/bg.jpg",
        "text": "طوارئ", "season": "eid"}} for k in pre}
    fake = _FakeRouter()
    res = deploy_login(fake, "mikrotik", {}, addons=cfg)
    assert res.ok is True
    html = _uploaded(fake)
    for tok in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)"):
        assert tok in html, f"placeholder مفقود: {tok}"
    assert "hr-pray-grid" in html  # محتوى مخبوز حاضر
    assert "<style>" in html       # ثيم محقون
