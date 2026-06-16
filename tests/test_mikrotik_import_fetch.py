"""feat/mikrotik-user-import — الزيادة 2: طبقة الجلب (REST + API الثنائي).

يغطّي: تطبيع النوع/النقل، REST مفضّل، السقوط إلى API عند فشل REST، وضعَيْ
rest/api القسريّين، تطبيع السجلّ (_id/_disabled)، وعدم تسريب كلمة المرور.
شغّل الملف وحده.
"""
from __future__ import annotations

import pytest

from app.radius.services import mt_import_fetch as F


# عيّنة NAS — قاموس بسيط (الجالب يقبل Mapping، لا حاجة لقاعدة بيانات).
def _nas(**over):
    base = {
        "id": 7, "name": "MT-Main", "address": "10.0.0.1",
        "api_user": "admin", "api_password": "s3cr3t", "api_use_tls": 0,
        "api_type": "auto",
    }
    base.update(over)
    return base


# ════════════════════════════════════════════════════════════════════════
# تطبيع النوع والنقل
# ════════════════════════════════════════════════════════════════════════
class TestNormalization:

    @pytest.mark.parametrize("raw,exp", [
        ("hotspot", "hotspot"), ("HS", "hotspot"),
        ("broadband", "broadband"), ("ppp", "broadband"),
        ("pppoe", "broadband"), ("secret", "broadband"),
    ])
    def test_import_type_synonyms(self, raw, exp):
        assert F._norm_import_type(raw) == exp

    def test_bad_import_type_raises(self):
        with pytest.raises(ValueError):
            F._norm_import_type("wireless")

    def test_resolve_transport_from_nas(self):
        assert F._resolve_transport(_nas(api_type="rest"), "") == "rest"
        assert F._resolve_transport(_nas(api_type="api"), "") == "api"
        assert F._resolve_transport(_nas(api_type="bogus"), "") == "auto"

    def test_resolve_transport_override_wins(self):
        assert F._resolve_transport(_nas(api_type="api"), "rest") == "rest"


# ════════════════════════════════════════════════════════════════════════
# تطبيع السجلّ
# ════════════════════════════════════════════════════════════════════════
class TestRecordNormalize:

    def test_dot_id_becomes_underscore_id(self):
        r = F._normalize_record({".id": "*5", "name": "u1"})
        assert r["_id"] == "*5" and r["name"] == "u1"

    def test_disabled_flag_parsed(self):
        assert F._normalize_record({"disabled": "true"})["_disabled"] is True
        assert F._normalize_record({"disabled": "false"})["_disabled"] is False
        assert F._normalize_record({"name": "x"})["_disabled"] is False

    def test_non_dict_safe(self):
        assert F._normalize_record("nope") == {}


# ════════════════════════════════════════════════════════════════════════
# REST مفضّل
# ════════════════════════════════════════════════════════════════════════
class TestRestPreferred:

    def test_rest_success_hotspot(self, monkeypatch):
        calls = {}

        def fake_get(url, *, user, password, verify, timeout):
            calls["url"] = url
            calls["user"] = user
            return [{".id": "*1", "name": "guest1", "password": "p1",
                     "profile": "default"}]
        monkeypatch.setattr(F, "_http_get_json", fake_get)

        res = F.fetch_users(_nas(), "hotspot")
        assert res.ok and res.transport == "rest"
        assert res.count == 1 and res.records[0]["name"] == "guest1"
        assert calls["url"].endswith("/rest/ip/hotspot/user")
        assert res.attempted == ["rest"]

    def test_rest_success_broadband_path(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: seen.setdefault("url", url) or [])
        res = F.fetch_users(_nas(), "broadband")
        assert res.ok and seen["url"].endswith("/rest/ppp/secret")

    def test_rest_uses_https_when_tls(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: seen.setdefault("url", url) or [])
        F.fetch_users(_nas(api_use_tls=1), "hotspot")
        assert seen["url"].startswith("https://10.0.0.1:443/")

    def test_rest_uses_http_default_80(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: seen.setdefault("url", url) or [])
        F.fetch_users(_nas(api_use_tls=0), "hotspot")
        assert seen["url"].startswith("http://10.0.0.1:80/")


# ════════════════════════════════════════════════════════════════════════
# السقوط إلى API + الوضعان القسريّان
# ════════════════════════════════════════════════════════════════════════
class TestFallbackAndForced:

    def _fake_api(self, monkeypatch, *, ok=True, data=None, error=""):
        from app.radius.services import mikrotik_admin_client as mac

        def fake_dial(*, nas, operation, work):
            return mac.MtResult(ok=ok, data=data or [], error=error)
        monkeypatch.setattr(mac, "_safe_dial", fake_dial)

    def test_auto_falls_back_to_api(self, monkeypatch):
        def boom(url, **kw):
            raise ConnectionError("refused")
        monkeypatch.setattr(F, "_http_get_json", boom)
        self._fake_api(monkeypatch, ok=True,
                       data=[{".id": "*2", "name": "ppp1"}])
        res = F.fetch_users(_nas(), "broadband")
        assert res.ok and res.transport == "api"
        assert res.attempted == ["rest", "api"]
        assert res.records[0]["name"] == "ppp1"

    def test_auto_both_fail_keeps_error(self, monkeypatch):
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: (_ for _ in ()).throw(OSError("x")))
        self._fake_api(monkeypatch, ok=False, error="تعذر الاتصال")
        res = F.fetch_users(_nas(), "hotspot")
        assert not res.ok and res.error
        assert res.attempted == ["rest", "api"]

    def test_forced_api_skips_rest(self, monkeypatch):
        rest_called = {"n": 0}
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: rest_called.update(n=rest_called["n"] + 1) or [])
        self._fake_api(monkeypatch, ok=True, data=[{"name": "only-api"}])
        res = F.fetch_users(_nas(api_type="api"), "hotspot")
        assert res.ok and res.transport == "api" and rest_called["n"] == 0

    def test_forced_rest_does_not_fallback(self, monkeypatch):
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: (_ for _ in ()).throw(OSError("dead")))
        # حتى لو كان API متاحًا، rest القسري لا يسقط.
        res = F.fetch_users(_nas(api_type="rest"), "hotspot")
        assert not res.ok and res.attempted == ["rest"]

    def test_no_address_clean_error(self, monkeypatch):
        res = F.fetch_users(_nas(address="", api_type="rest"), "hotspot")
        assert not res.ok and "عنوان" in res.error


# ════════════════════════════════════════════════════════════════════════
# أمان — لا تسرّب كلمة المرور
# ════════════════════════════════════════════════════════════════════════
class TestSecurity:

    def test_password_not_in_logs(self, monkeypatch, caplog):
        import logging
        monkeypatch.setattr(F, "_http_get_json",
                            lambda url, **kw: (_ for _ in ()).throw(OSError("boom")))
        with caplog.at_level(logging.INFO):
            F.fetch_users(_nas(api_password="TOP-SECRET", api_type="rest"), "hotspot")
        assert "TOP-SECRET" not in caplog.text

    def test_bad_type_returns_envelope_not_raise(self):
        res = F.fetch_users(_nas(), "garbage")
        assert not res.ok and res.error and res.records == []
