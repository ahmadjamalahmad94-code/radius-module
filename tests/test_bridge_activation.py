"""Tests for the Admin Bridge activation service (radius-module side).

Covers:
- activate_bridge(): validates inputs, calls panel API, stores credentials
- test_bridge_connection(): checks stored config and panel reachability
- BridgeActivationError raised on panel errors
- Secrets never appear in logs or return values beyond activation response
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_panel_response(payload: dict, status: int = 200) -> MagicMock:
    """Simulate a successful urllib.request.urlopen response."""
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    resp.status = status
    return resp


def _http_error(status: int, body: dict):
    import urllib.error
    err = urllib.error.HTTPError(
        url="https://panel.example.com/...",
        code=status,
        msg="err",
        hdrs=None,  # type: ignore
        fp=None,
    )
    err.read = lambda: json.dumps(body).encode("utf-8")
    return err


# ── BridgeActivationError ─────────────────────────────────────────────────────

class TestBridgeActivationError:
    def test_attributes(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        exc = BridgeActivationError("رسالة", "expired", http_status=410)
        assert str(exc) == "رسالة"
        assert exc.status == "expired"
        assert exc.http_status == 410

    def test_default_status_and_http_status(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        exc = BridgeActivationError("خطأ")
        assert exc.status == "error"
        assert exc.http_status == 400


# ── activate_bridge() ─────────────────────────────────────────────────────────

class TestActivateBridge:
    def setup_method(self):
        # Provide stubs for repos used by activate_bridge.
        self._tenants_store: dict[tuple, str] = {}
        self._audit_records: list[dict] = []

        def fake_set_setting(tid, key, value, *, by=0):
            self._tenants_store[(tid, key)] = value

        # Patch at module level so activate_bridge picks them up.
        import app.radius.services.bridge_activation as _svc
        self._patcher_tenants = patch.object(_svc, "tenants_repo")
        self._patcher_audit = patch.object(_svc, "audit_repo")
        self._patcher_fp = patch.object(_svc, "_server_fingerprint", return_value="fp-test")

        mock_tenants = self._patcher_tenants.start()
        mock_tenants.set_setting.side_effect = fake_set_setting
        mock_audit = self._patcher_audit.start()
        mock_audit.record.side_effect = lambda **kw: self._audit_records.append(kw)
        self._patcher_fp.start()

    def teardown_method(self):
        self._patcher_tenants.stop()
        self._patcher_audit.stop()
        self._patcher_fp.stop()

    def _call(self, base_url="https://panel.example.com", code="AABB1122-CCDD3344-EEFF5566"):
        from app.radius.services.bridge_activation import activate_bridge
        return activate_bridge(base_url=base_url, activation_code=code, tenant_id=1, admin_id=7)

    # -- input validation --

    def test_empty_code_raises(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        with pytest.raises(BridgeActivationError) as exc_info:
            self._call(code="")
        assert exc_info.value.http_status == 400

    def test_invalid_base_url_raises(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        with pytest.raises(BridgeActivationError):
            self._call(base_url="ftp://bad-url.com")

    def test_missing_base_url_raises(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        with pytest.raises(BridgeActivationError):
            self._call(base_url="")

    # -- successful activation --

    def test_success_stores_credentials(self):
        panel_payload = {
            "ok": True,
            "status": "activated",
            "license_key": "LK-TEST-0001",
            "shared_secret": "a" * 64,
            "base_url": "https://panel.example.com",
            "customer_name": "Acme Corp",
        }
        resp = _make_panel_response(panel_payload)
        with patch("urllib.request.urlopen", return_value=resp):
            result = self._call()

        assert result["ok"] is True
        assert result["license_key"] == "LK-TEST-0001"
        assert result["customer_name"] == "Acme Corp"
        # shared_secret NOT in result.
        assert "shared_secret" not in result

    def test_success_does_not_return_shared_secret(self):
        panel_payload = {
            "ok": True, "status": "activated",
            "license_key": "LK-NOSECRET", "shared_secret": "supersecret123",
            "base_url": "https://panel.example.com", "customer_name": "Test",
        }
        resp = _make_panel_response(panel_payload)
        with patch("urllib.request.urlopen", return_value=resp):
            result = self._call()
        assert "shared_secret" not in result
        # shared_secret should be stored in settings though.
        assert self._tenants_store.get((1, "license_admin_bridge.shared_secret")) == "supersecret123"

    def test_success_sets_enabled_flag(self):
        panel_payload = {
            "ok": True, "status": "activated",
            "license_key": "LK-ENABLED", "shared_secret": "s" * 64,
            "base_url": "https://panel.example.com", "customer_name": "X",
        }
        resp = _make_panel_response(panel_payload)
        with patch("urllib.request.urlopen", return_value=resp):
            self._call()
        assert self._tenants_store.get((1, "license_admin_bridge.enabled")) == "1"

    def test_success_stores_all_four_keys(self):
        panel_payload = {
            "ok": True, "status": "activated",
            "license_key": "LK-FOUR", "shared_secret": "s" * 64,
            "base_url": "https://panel.example.com", "customer_name": "Y",
        }
        resp = _make_panel_response(panel_payload)
        with patch("urllib.request.urlopen", return_value=resp):
            self._call()
        stored_keys = {k[1] for k in self._tenants_store}
        assert "license_admin_bridge.base_url" in stored_keys
        assert "license_admin_bridge.license_key" in stored_keys
        assert "license_admin_bridge.shared_secret" in stored_keys
        assert "license_admin_bridge.enabled" in stored_keys

    def test_audit_record_written_on_success(self):
        panel_payload = {
            "ok": True, "status": "activated",
            "license_key": "LK-AUDIT", "shared_secret": "s" * 64,
            "base_url": "https://panel.example.com", "customer_name": "AuditCo",
        }
        resp = _make_panel_response(panel_payload)
        with patch("urllib.request.urlopen", return_value=resp):
            self._call()
        assert len(self._audit_records) == 1
        rec = self._audit_records[0]
        assert rec.get("action") == "bridge_activated"
        # shared_secret must NOT appear in audit payload.
        payload_str = json.dumps(rec.get("payload", {}))
        assert "shared_secret" not in payload_str
        assert "supersecret" not in payload_str

    # -- panel error handling --

    def test_http_401_raises_invalid_token(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        with patch("urllib.request.urlopen", side_effect=_http_error(401, {"message": "bad"})):
            with pytest.raises(BridgeActivationError) as exc_info:
                self._call()
        assert exc_info.value.http_status == 401
        assert exc_info.value.status == "invalid_token"

    def test_http_409_raises_already_used(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        with patch("urllib.request.urlopen", side_effect=_http_error(409, {})):
            with pytest.raises(BridgeActivationError) as exc_info:
                self._call()
        assert exc_info.value.status == "already_used"

    def test_http_410_raises_expired(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        with patch("urllib.request.urlopen", side_effect=_http_error(410, {})):
            with pytest.raises(BridgeActivationError) as exc_info:
                self._call()
        assert exc_info.value.status == "expired"

    def test_connection_error_raises_503(self):
        import urllib.error
        from app.radius.services.bridge_activation import BridgeActivationError
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
            with pytest.raises(BridgeActivationError) as exc_info:
                self._call()
        assert exc_info.value.http_status == 503
        assert exc_info.value.status == "connection_error"

    def test_panel_ok_false_raises(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        resp = _make_panel_response({"ok": False, "status": "no_license", "message": "nope"})
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(BridgeActivationError) as exc_info:
                self._call()
        assert exc_info.value.status == "no_license"

    def test_missing_license_key_in_response_raises(self):
        from app.radius.services.bridge_activation import BridgeActivationError
        resp = _make_panel_response({"ok": True, "status": "activated", "shared_secret": "s" * 64})
        with patch("urllib.request.urlopen", return_value=resp):
            with pytest.raises(BridgeActivationError):
                self._call()

    def test_shared_secret_not_logged(self, caplog):
        """shared_secret must never appear in log output."""
        import logging
        panel_payload = {
            "ok": True, "status": "activated",
            "license_key": "LK-LOG", "shared_secret": "SECRETVALUE_DO_NOT_LOG",
            "base_url": "https://panel.example.com", "customer_name": "LogCo",
        }
        resp = _make_panel_response(panel_payload)
        with caplog.at_level(logging.DEBUG):
            with patch("urllib.request.urlopen", return_value=resp):
                self._call()
        for record in caplog.records:
            assert "SECRETVALUE_DO_NOT_LOG" not in record.getMessage()


# ── test_bridge_connection() ──────────────────────────────────────────────────

class TestTestBridgeConnection:
    def test_returns_not_configured_when_no_settings(self):
        from app.radius.services.bridge_activation import test_bridge_connection

        mock_cfg = MagicMock()
        mock_cfg.base_url = ""
        mock_cfg.license_key = ""
        mock_cfg.shared_secret = ""

        import app.radius.services.bridge_activation as _svc
        with patch.object(_svc, "AdminBridgeConfig") as MockCfg:
            MockCfg.from_env.return_value = mock_cfg
            result = test_bridge_connection(tenant_id=1)

        assert result["configured"] is False
        assert result["reachable"] is False
        assert result["status"] == "not_configured"

    def test_returns_connected_when_panel_reachable(self):
        from app.radius.services.bridge_activation import test_bridge_connection

        mock_cfg = MagicMock()
        mock_cfg.base_url = "https://panel.example.com"
        mock_cfg.license_key = "LK-TEST"
        mock_cfg.shared_secret = "s" * 64

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch("app.radius.services.bridge_activation.AdminBridgeConfig") as MockCfg, \
             patch("urllib.request.urlopen", return_value=mock_resp):
            MockCfg.from_env.return_value = mock_cfg
            result = test_bridge_connection(tenant_id=1)

        assert result["configured"] is True
        assert result["reachable"] is True
        assert result["status"] == "connected"

    def test_returns_specific_status_when_panel_down(self):
        from app.radius.services.bridge_activation import test_bridge_connection
        import urllib.error

        mock_cfg = MagicMock()
        mock_cfg.base_url = "https://panel.example.com"
        mock_cfg.license_key = "LK-DOWN"
        mock_cfg.shared_secret = "s" * 64

        with patch("app.radius.services.bridge_activation.AdminBridgeConfig") as MockCfg, \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")):
            MockCfg.from_env.return_value = mock_cfg
            result = test_bridge_connection(tenant_id=1)

        assert result["configured"] is True
        assert result["reachable"] is False
        assert result["ok"] is False
        # status is now specific (connection_error / dns_error / timeout / …),
        # no longer the generic "unreachable" string from the old implementation.
        assert result["status"] not in ("connected", "not_configured")

    # ── pre-activation test_url path ──────────────────────────────────────────

    def test_test_url_reachable(self):
        from app.radius.services.bridge_activation import test_bridge_connection

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_bridge_connection(test_url="https://hoberadius.com")

        assert result["ok"] is True
        assert result["reachable"] is True
        assert result["status"] == "reachable"
        # configured=False because we haven't activated yet
        assert result["configured"] is False

    def test_test_url_dns_error(self):
        from app.radius.services.bridge_activation import test_bridge_connection
        import urllib.error

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("getaddrinfo failed")):
            result = test_bridge_connection(test_url="https://unreachable.invalid")

        assert result["ok"] is False
        assert result["reachable"] is False
        assert result["status"] == "dns_error"
        assert "DNS" in result["message_ar"]

    def test_test_url_timeout(self):
        from app.radius.services.bridge_activation import test_bridge_connection
        import urllib.error

        with patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("timed out")):
            result = test_bridge_connection(test_url="https://slow.example.com")

        assert result["ok"] is False
        assert result["status"] == "timeout"

    def test_test_url_http_error(self):
        from app.radius.services.bridge_activation import test_bridge_connection

        with patch("urllib.request.urlopen",
                   side_effect=_http_error(503, {"message": "overloaded"})):
            result = test_bridge_connection(test_url="https://panel.example.com")

        assert result["ok"] is False
        assert result["reachable"] is True
        assert result["status"] == "http_error"
        assert "503" in result["message_ar"]

    def test_test_url_invalid_scheme_rejected(self):
        from app.radius.services.bridge_activation import test_bridge_connection

        result = test_bridge_connection(test_url="not-a-url")
        assert result["ok"] is False
        assert result["status"] == "invalid_url"

    def test_test_url_does_not_require_stored_credentials(self):
        """Providing test_url bypasses the AdminBridgeConfig lookup entirely."""
        from app.radius.services.bridge_activation import test_bridge_connection

        mock_resp = MagicMock()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200

        import app.radius.services.bridge_activation as _svc
        with patch.object(_svc, "AdminBridgeConfig") as MockCfg, \
             patch("urllib.request.urlopen", return_value=mock_resp):
            result = test_bridge_connection(test_url="https://hoberadius.com")

        # AdminBridgeConfig.from_env() must NOT have been called
        MockCfg.from_env.assert_not_called()
        assert result["ok"] is True
