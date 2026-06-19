# -*- coding: utf-8 -*-
"""P3 — الربح + أنماط الدخول + التفاعل، وفحص شامل لكل الإضافات معًا
(صحّة المايكروتيك + صفحة ما بعد الدخول). شغّل الملف وحده."""
from __future__ import annotations

import pytest

MONEY = ("sponsor_banner", "watch_ad", "data_collection", "coupons",
         "loyalty", "tier_upsell")
LOGIN = ("one_tap", "social_login", "sms_otp", "voucher_scratch",
         "multilang", "tos_consent", "returning_user")
ENGAGE = ("spin_to_win", "referral", "feedback_review",
          "post_connect_redirect", "daily_checkin", "trial_timer")


def test_all_p3_addons_registered():
    from app.radius.services import hotspot_addons as ad
    for k in MONEY + LOGIN + ENGAGE:
        assert k in ad.ADDONS, f"إضافة P3 غير مسجَّلة: {k}"


def test_total_addon_count():
    from app.radius.services import hotspot_addons as ad
    assert len(ad.ADDONS) >= 39


def _pre(cfg):
    from app.radius.services import hotspot_addons as ad
    return ad.render_prelogin_fragments(ad.normalize_config(cfg), {"accent": "#2563EB"})


def _post(cfg):
    from app.radius.services import hotspot_addons as ad
    return ad.render_postlogin_widgets(ad.normalize_config(cfg), {"accent": "#2563EB"})


def test_watch_ad_gates_submit():
    html = _pre({"watch_ad": {"enabled": True, "config": {"video_file": "ad.mp4"}}})
    assert "hr-ad-v" in html
    assert "disabled=true" in html  # يبوّب زر الدخول


def test_data_collection_consent_gate_and_escape():
    html = _pre({"data_collection": {"enabled": True,
                "config": {"consent_text": "<x>موافق"}}})
    assert "hr-dc-ok" in html
    assert "<x>موافق" not in html and "&lt;x&gt;" in html


def test_one_tap_requires_user():
    # بلا اسم مستخدم → لا زر (لا يُرسل نموذجًا فارغًا)
    assert _pre({"one_tap": {"enabled": True, "config": {}}}) == ""
    html = _pre({"one_tap": {"enabled": True, "config": {"free_user": "guest"}}})
    assert "hr-onetap" in html and "guest" in html


def test_social_login_domains():
    from app.radius.services import hotspot_addons as ad
    cfg = {"social_login": {"enabled": True,
           "config": {"google": "https://auth.me/g"}}}
    doms = ad.collect_walled_garden_domains(ad.normalize_config(cfg))
    for d in ("google.com", "facebook.com", "appleid.apple.com"):
        assert d in doms


def test_voucher_scratch_canvas():
    html = _pre({"voucher_scratch": {"enabled": True, "config": {}}})
    assert "hr-scratch-c" in html and "getContext" in html


def test_tos_gates_submit():
    html = _pre({"tos_consent": {"enabled": True, "config": {"text": "موافق"}}})
    assert "hr-tos-ok" in html and "disabled=true" in html


def test_spin_to_win_needs_prizes():
    assert _pre({"spin_to_win": {"enabled": True, "config": {}}}) == ""
    html = _pre({"spin_to_win": {"enabled": True,
                "config": {"prizes": "جائزة١\nجائزة٢"}}})
    assert "hr-wheel" in html and "جائزة١" in html


def test_feedback_review_funnel():
    html = _post({"feedback_review": {"enabled": True,
                 "config": {"review_url": "https://g.page/r/x",
                            "feedback_url": "https://forms/x"}}})
    assert "hr-stars" in html and "g.page/r/x" in html


def test_coupon_copy_widget():
    html = _post({"coupons": {"enabled": True, "config": {"code": "SAVE20"}}})
    assert "SAVE20" in html and "clipboard" in html


def test_trial_timer_post():
    html = _post({"trial_timer": {"enabled": True, "config": {"minutes": "15"}}})
    assert "hr-trial" in html


# ════════════════════════════════════════════════════════════════
# الفحص الشامل — كل الإضافات مفعّلة معًا
# ════════════════════════════════════════════════════════════════
def _all_enabled_cfg():
    """تفعيل كل إضافة بإعداد صالح مناسب لكل الحقول الشائعة."""
    from app.radius.services import hotspot_addons as ad
    sample = {
        "body": "سطر", "items": "خبر١\nخبر٢", "title": "ع",
        "url": "https://m.example.com/x", "form_url": "https://forms/x",
        "stream_url": "https://s.example.com/live", "images": "https://cdn.x/1.jpg",
        "image_url": "https://cdn.x/bg.jpg", "click_url": "https://ad.x/c",
        "text": "إشعار", "consent_text": "موافق", "season": "ramadan",
        "free_user": "guest", "google": "https://auth/g",
        "request_url": "https://api/otp", "prizes": "ج١\nج٢",
        "review_url": "https://g.page/r/x", "code": "C1",
        "share_url": "https://s/x", "join_url": "https://j/x",
        "store_url": "https://store/x", "city": "جدة",
        "lat": "21.4", "lon": "39.8",
    }
    cfg = {}
    for key, spec in ad.ADDONS.items():
        c = {f.key: sample.get(f.key, f.default) for f in spec.fields}
        cfg[key] = {"enabled": True, "config": c}
    return cfg


class _FakeRouter:
    def __init__(self):
        self.calls = []

    def connect(self): pass
    def close(self): pass

    def run(self, path, attrs=None):
        self.calls.append((path, dict(attrs or {})))
        return []


def _uploaded(fake):
    for p, a in fake.calls:
        if p in ("/file/add", "/file/set") and "contents" in a:
            return a["contents"]
    return ""


def test_all_addons_deploy_keeps_mikrotik_valid(monkeypatch):
    # لا نضرب الشبكة من إضافة الطقس أثناء الفحص.
    from app.radius.services import hotspot_addons_content as c
    monkeypatch.setattr(c, "fetch_weather", lambda *a, **k: {"temp": 25, "code": 0})
    from app.radius.services.hotspot_templates import deploy_login
    cfg = _all_enabled_cfg()
    fake = _FakeRouter()
    res = deploy_login(fake, "mikrotik", {}, addons=cfg)
    assert res.ok is True
    html = _uploaded(fake)
    for tok in ("$(link-login-only)", "$(chap-id)", "$(chap-challenge)"):
        assert tok in html, f"placeholder مفقود مع كل الإضافات: {tok}"


def test_all_addons_redirect_page_builds(monkeypatch):
    from app.radius.services import hotspot_surfaces as sf
    cfg = _all_enabled_cfg()
    vals = {"TENANT_NAME": "كل المنشآت", "ACCENT_COLOR": "#2563EB",
            "BG_COLOR": "#F8FAFC"}
    html = sf.build_redirect_page(vals, cfg)
    assert html.lstrip().startswith("<!DOCTYPE html>")
    # ودجت ما بعد الدخول حاضرة (راديو/كوبون/تقييم...)
    assert "hr-widget" in html


def test_all_addons_collect_domains(monkeypatch):
    from app.radius.services import hotspot_addons as ad
    doms = ad.collect_walled_garden_domains(ad.normalize_config(_all_enabled_cfg()))
    # نطاقات من social_links + social_login + موارد روابط
    assert "facebook.com" in doms
    assert "google.com" in doms
