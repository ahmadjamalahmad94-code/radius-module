# -*- coding: utf-8 -*-
"""إضافات معرض الإلهام الجديدة (25): التسجيل، التوليد، الأمان، وحارس
عدم تسريب placeholders عند تفعيلها كلها مع الجلود. شغّل الملف وحده."""
from __future__ import annotations

import re

import pytest

NEW = ("tab_bar_nav", "dealers_directory", "cobrand_dual_logo",
       "datetime_greeting", "rating_badge", "quota_alert", "staff_login_link",
       "password_eye", "remember_me", "network_status_strip", "recent_cards",
       "device_readout", "expiry_alert", "support_card", "buy_card_cta",
       "throughput_bars", "refresh_session", "online_chip", "manual_redirect",
       "package_ribbons", "logout_confirm", "ornamental_divider",
       "countdown_tile", "mac_dashboard", "venue_footer")

_SAMPLE = {
    "dealers": "المركز|0590000000|الحمراء\nفرع|0591111111|الزهور",
    "payments": "مدى, أبل باي", "logo2_url": "https://cdn.example.com/l.png",
    "text": "تنبيه", "phone": "0590000000", "url": "https://pay.example.com/c",
    "title": "باقة 50", "address": "شارع 1", "tagline": "أهلًا بكم",
    "stars": "4", "minutes": "30", "label": "اشترِ", "art_url": "https://cdn.example.com/a.png",
}


def _norm(keys):
    from app.radius.services import hotspot_addons as ad
    return ad.normalize_config({k: {"enabled": True, "config": _SAMPLE} for k in keys})


def test_all_new_addons_registered():
    from app.radius.services import hotspot_addons as ad
    assert len(NEW) == 25
    for k in NEW:
        assert k in ad.ADDONS, f"غير مسجّلة: {k}"


def test_all_new_addons_default_off():
    """كلها مطفأة افتراضيًّا — يحفظ ضمان «بلا إضافات = ناتج مطابق».
    (إظهار/إخفاء كلمة المرور تحسين موصى به لكنه مطفأ افتراضيًّا.)"""
    from app.radius.services import hotspot_addons as ad
    for k in NEW:
        assert ad.ADDONS[k].default_on is False, f"{k} يجب أن تكون مطفأة افتراضيًّا"


@pytest.mark.parametrize("key", NEW)
def test_addon_renders_some_surface(key):
    from app.radius.services import hotspot_addons as ad
    spec = ad.ADDONS[key]
    cfg = _norm([key])
    out = ""
    if spec.runs_prelogin():
        out += ad.render_prelogin_fragments(cfg, {"accent": "#2563EB"})
    if spec.runs_postlogin():
        out += ad.render_postlogin_widgets(cfg, {"accent": "#2563EB"})
    assert out.strip(), f"{key}: لم تُنتج أي HTML بإعداد صالح"
    assert "{{" not in out, f"{key}: تسريب متغيّر خام"


def test_dealers_escapes_and_tel():
    from app.radius.services import hotspot_addons as ad
    cfg = ad.normalize_config({"dealers_directory": {"enabled": True,
          "config": {"dealers": "<b>x</b>|0590|حي", "payments": "مدى"}}})
    html = ad.render_prelogin_fragments(cfg, {})
    assert "<b>x</b>" not in html and "&lt;b&gt;" in html
    assert "tel:0590" in html
    assert "نقطة رئيسية" in html  # أول صف = النقطة الرئيسية


def test_no_native_alert_or_confirm():
    """toast فقط — لا alert/confirm أصلي في أي إضافة جديدة."""
    from app.radius.services import hotspot_addons as ad
    blob = ""
    for k in NEW:
        cfg = _norm([k])
        blob += ad.render_prelogin_fragments(cfg, {"accent": "#2563EB"})
        blob += ad.render_postlogin_widgets(cfg, {"accent": "#2563EB"})
    assert "alert(" not in blob
    assert "confirm(" not in blob


def test_post_widgets_have_no_raw_dollar():
    """شاشة ما بعد الدخول (مستضافة) يجب ألا تسرّب أي $(...)."""
    from app.radius.services import hotspot_addons as ad
    post_keys = [k for k in NEW if ad.ADDONS[k].runs_postlogin()]
    html = ad.render_postlogin_widgets(_norm(post_keys), {"accent": "#2563EB"})
    assert "$(" not in html


def test_cobrand_and_buycard_collect_domains():
    from app.radius.services import hotspot_addons as ad
    doms = ad.collect_walled_garden_domains(_norm(
        ["cobrand_dual_logo", "buy_card_cta"]))
    assert "cdn.example.com" in doms
    assert "pay.example.com" in doms


# ── حارس شامل: كل الإضافات (72) + جلد → login.html سليم المايكروتيك ──
_ALLOWED = None


def _allowed():
    from app.radius.services import hotspot_templates as ht
    return set(ht.ROUTEROS_REQUIRED) | {
        "$(link-orig)", "$(link-orig-esc)", "$(username)", "$(mac-esc)",
        "$(if error)", "$(endif)"}


class _Fake:
    def __init__(self): self.calls = []
    def connect(self): pass
    def close(self): pass
    def run(self, p, attrs=None):
        self.calls.append((p, dict(attrs or {})))
        return []


def test_all_addons_with_skin_keep_login_valid(monkeypatch):
    from app.radius.services import hotspot_addons_content as c
    monkeypatch.setattr(c, "fetch_weather", lambda *a, **k: {"temp": 25, "code": 0})
    from app.radius.services import hotspot_addons as ad
    from app.radius.services.hotspot_templates import deploy_login
    cfg = {k: {"enabled": True, "config": _SAMPLE} for k in ad.ADDONS}
    f = _Fake()
    res = deploy_login(f, "frost_glass_blue", {}, addons=cfg)
    assert res.ok is True
    html = next(a["contents"] for p, a in f.calls
                if p in ("/file/add", "/file/set"))
    for tok in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)"):
        assert tok in html
    unknown = set(re.findall(r"\$\([^)]*\)", html)) - _allowed()
    assert not unknown, f"placeholders خام مسرّبة في login.html: {unknown}"
    assert "{{" not in html
