# -*- coding: utf-8 -*-
"""إضافة «المنيو الحيّ»: تجلب منيو الكافي من نظامه إلى صفحة الدخول، ونطاقُه يدخل walled-garden."""
from __future__ import annotations


def _cfg(**over):
    c = {"api_url": "http://188.40.63.44:8097/menu/api", "title": "المنيو"}
    c.update(over)
    return {"live_menu": {"enabled": True, "config": c}}


def test_registered_prelogin_and_not_server_side():
    from app.radius.services import hotspot_addons as ad
    spec = ad.ADDONS["live_menu"]
    assert spec.surface == ad.SURFACE_PRELOGIN and spec.server_side is False


def test_fragment_fetches_api_and_links_full_menu():
    from app.radius.services import hotspot_addons as ad
    html = ad.render_prelogin_fragments(ad.normalize_config(_cfg()), {"accent": "#6B5AED"})
    assert "hr-live-menu" in html and "fetch(" in html
    assert "http://188.40.63.44:8097/menu/api" in html
    assert 'href="http://188.40.63.44:8097/menu/"' in html      # مشتقٌّ من رابط الـAPI
    assert "المنيو" in html


def test_menu_url_override_and_escaping():
    from app.radius.services import hotspot_addons as ad
    html = ad.render_prelogin_fragments(ad.normalize_config(_cfg(
        menu_url="http://cafe.example/menu", title="<b>قائمتنا</b>", limit="abc")), {})
    assert 'href="http://cafe.example/menu"' in html
    assert "<b>قائمتنا</b>" not in html and "&lt;b&gt;" in html
    assert "lim=12" in html                                      # حدٌّ غير رقميّ → الافتراضيّ


def test_bad_url_renders_nothing():
    from app.radius.services import hotspot_addons as ad
    html = ad.render_prelogin_fragments(ad.normalize_config(_cfg(api_url="javascript:alert(1)")), {})
    assert "hr-live-menu" not in html


def test_api_host_goes_to_walled_garden():
    from app.radius.services import hotspot_addons as ad
    hosts = ad.collect_walled_garden_domains(ad.normalize_config(_cfg()))
    assert "188.40.63.44" in hosts


def test_mgmt_pull_base_can_be_overridden_for_non_wg_tunnels(monkeypatch):
    from app.radius.services import hotspot_templates as ht
    monkeypatch.setenv("HOBERADIUS_WG_SERVER_IP", "10.10.0.1")
    monkeypatch.setenv("HOBERADIUS_MGMT_PULL_BASE", "10.50.0.1")
    assert ht.resolve_mgmt_pull_base() == "http://10.50.0.1"
    monkeypatch.delenv("HOBERADIUS_MGMT_PULL_BASE")
    assert ht.resolve_mgmt_pull_base() == "http://10.10.0.1"


class _Client:
    def __init__(self, servers, profiles):
        self._s, self._p = servers, profiles

    def run(self, path, attrs=None):
        return {"/ip/hotspot/print": self._s, "/ip/hotspot/profile/print": self._p}.get(path, [])


def test_hotspot_dir_follows_the_active_server_profile():
    from app.radius.services import hotspot_templates as ht
    c = _Client([{"name": "hs1", "profile": "hsprof1", "disabled": "false"}],
                [{"name": "default", "html-directory": "hotspot"},
                 {"name": "hsprof1", "html-directory": "flash/hotspot"}])
    assert ht.resolve_hotspot_dir(c) == "flash/hotspot"


def test_hotspot_dir_override_and_defaults():
    from app.radius.services import hotspot_templates as ht
    c = _Client([{"name": "hs1", "profile": "p", "disabled": "false"}],
                [{"name": "p", "html-directory": "hotspot", "html-directory-override": "flash/site"}])
    assert ht.resolve_hotspot_dir(c) == "flash/site"
    assert ht.resolve_hotspot_dir(_Client([], [])) == "hotspot"

    class Boom:
        def run(self, *a, **k):
            raise RuntimeError("api down")
    assert ht.resolve_hotspot_dir(Boom()) == "hotspot"


def test_hotspot_dir_reads_raw_api_reply_shape():
    from app.radius.services import hotspot_templates as ht
    c = _Client([{"reply": "!re", "attrs": {"name": "hotspot1", "profile": "hsprof1", "disabled": "false"}}],
                [{"reply": "!re", "attrs": {"name": "default", "html-directory": "flash/hotspot"}},
                 {"reply": "!re", "attrs": {"name": "hsprof1", "html-directory": "flash/hotspot", "html-directory-override": ""}}])
    assert ht.resolve_hotspot_dir(c) == "flash/hotspot"


def test_ordering_ui_uses_router_mac_and_can_be_disabled():
    from app.radius.services import hotspot_addons as ad
    html = ad.render_prelogin_fragments(ad.normalize_config(_cfg()), {})
    assert '"$(mac)"' in html and "/menu/api/order" in html and "/menu/api/whoami" in html
    assert "hr-lm-cart" in html and "data-add" in html
    off = ad.render_prelogin_fragments(ad.normalize_config(_cfg(ordering="0")), {})
    assert "orderOn=false" in off
