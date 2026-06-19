# -*- coding: utf-8 -*-
"""إضافات متقدّمة إضافية: CSS مخصّص (تنقية)، خطوط، وصول، A/B،
تحليلات، فيديو سبلاش، SVG، محتوى مجدول. شغّل الملف وحده."""
from __future__ import annotations

EXTRA = ("custom_css", "font_picker", "accessibility_mode", "ab_testing",
         "analytics", "video_splash", "animated_svg", "scheduled_content")


def _pre(cfg):
    from app.radius.services import hotspot_addons as ad
    return ad.render_prelogin_fragments(ad.normalize_config(cfg), {"accent": "#2563EB"})


def test_extras_registered():
    from app.radius.services import hotspot_addons as ad
    for k in EXTRA:
        assert k in ad.ADDONS, f"إضافة غير مسجَّلة: {k}"
    assert len(ad.ADDONS) >= 47


def test_custom_css_strips_html():
    html = _pre({"custom_css": {"enabled": True,
                 "config": {"css": "body{color:red}</style><script>alert(1)</script>"}}})
    assert "<script" not in html
    assert html.count("</style>") == 1   # لا خروج من وسم style
    assert "color:red" in html


def test_font_picker_injects_family():
    html = _pre({"font_picker": {"enabled": True, "config": {"family": "rounded"}}})
    assert "font-family:'Tajawal'" in html


def test_accessibility_toggle():
    html = _pre({"accessibility_mode": {"enabled": True, "config": {}}})
    assert "hr-a11y-btn" in html and "hr-a11y-on" in html


def test_ab_testing_buckets_button():
    html = _pre({"ab_testing": {"enabled": True,
                 "config": {"text_a": "أ", "text_b": "ب"}}})
    assert "hr-ab" in html and "أ" in html and "ب" in html


def test_analytics_local_and_beacon_domain():
    from app.radius.services import hotspot_addons as ad
    cfg = {"analytics": {"enabled": True,
           "config": {"endpoint": "https://an.example.com/c", "vertical": "cafe"}}}
    html = _pre(cfg)
    assert "hr-an-imp" in html and "sendBeacon" in html
    assert "an.example.com" in ad.collect_walled_garden_domains(ad.normalize_config(cfg))


def test_analytics_offline_without_endpoint():
    html = _pre({"analytics": {"enabled": True, "config": {}}})
    assert "hr-an-imp" in html       # يحصي محليًّا دائمًا
    assert "sendBeacon" not in html  # لا beacon بلا نقطة نهاية


def test_video_splash_relative_offline():
    html = _pre({"video_splash": {"enabled": True,
                 "config": {"video_file": "splash.mp4"}}})
    assert "<video" in html and "splash.mp4" in html and "autoplay" in html


def test_animated_svg_shapes():
    for shape in ("waves", "wifi", "blob"):
        html = _pre({"animated_svg": {"enabled": True, "config": {"shape": shape}}})
        assert "<svg" in html and "@keyframes" in html


def test_scheduled_content_window():
    html = _pre({"scheduled_content": {"enabled": True,
                 "config": {"message": "ساعة سعيدة", "start_hour": "16",
                            "end_hour": "18"}}})
    assert "hr-sched" in html and "ساعة سعيدة" in html
    assert "getHours" in html


def test_extras_keep_mikrotik_valid():
    from app.radius.services.hotspot_templates import deploy_login

    class _F:
        def __init__(self): self.calls = []
        def connect(self): pass
        def close(self): pass
        def run(self, p, attrs=None):
            self.calls.append((p, dict(attrs or {})))
            return []
    cfg = {k: {"enabled": True, "config": {
        "css": "body{}", "shape": "wifi", "message": "x",
        "video_file": "v.mp4", "endpoint": "https://an.x/c"}} for k in EXTRA}
    f = _F()
    res = deploy_login(f, "mikrotik", {}, addons=cfg)
    assert res.ok is True
    html = next(a["contents"] for p, a in f.calls
                if p in ("/file/add", "/file/set"))
    for tok in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)"):
        assert tok in html
