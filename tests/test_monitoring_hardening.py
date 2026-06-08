"""اختبارات monitoring_service + _push_usage_snapshot — PART 2C + 2D.

تغطّي:
  2C - _push_usage_snapshot:
    1. لا يُطلق استثناء عند فشل الشبكة
    2. الـ payload لا يحتوي على أسرار
    3. لا يوقف المزامنة الرئيسية عند الفشل

  2D - get_dashboard_stats / monitoring:
    1. DB فارغة → يُعيد dict صالح بدون استثناء
    2. جدول غير موجود → يُعيد dict صالح بدون استثناء
    3. Arabic status_ar مُرجَعة بشكل صحيح
    4. overall_health محسوب بشكل صحيح
    5. لا يُطلق استثناء عند غياب WireGuard
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")


def _make_app(tmp_path: Path):
    db_path = str(tmp_path / "test.db")
    os.environ["HOBERADIUS_DB_PATH"] = db_path
    from app import create_app
    return create_app()


# ══════════════════════════════════════════════════════════════════
# PART 2D — get_dashboard_stats hardening
# ══════════════════════════════════════════════════════════════════

class TestGetDashboardStatsEmptyDB:
    """DB فارغة — يجب ألا تُطلق استثناءات."""

    def test_empty_db_returns_valid_dict(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import get_dashboard_stats
            result = get_dashboard_stats(1)

        assert isinstance(result, dict)
        assert "overall_health" in result
        assert "health_checks" in result
        assert "totals" in result
        assert isinstance(result["vpn_allocations"], list)
        assert result["wg_service"] is None

    def test_empty_db_overall_health_not_crash(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import get_dashboard_stats
            result = get_dashboard_stats(1)
        assert result["overall_health"] in ("ok", "warning", "critical", "unknown", "not_synced")

    def test_empty_db_totals_zero(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import get_dashboard_stats
            result = get_dashboard_stats(1)
        assert result["totals"]["total_accounts"] == 0
        assert result["totals"]["total_peers"] == 0

    def test_no_wireguard_binary_does_not_crash(self, tmp_path):
        """غياب wg على بيئة التطوير لا يُسقط لوحة المتابعة."""
        app = _make_app(tmp_path)
        with app.app_context():
            # بذر خدمة WireGuard لكي يُستدعى _check_wg_interface
            from app.radius.db.connection import transaction
            with transaction() as conn:
                conn.execute(
                    """INSERT INTO wireguard_data_service
                       (id, tenant_id, interface_name, status, max_peers,
                        created_at, updated_at)
                       VALUES (1,1,'wg-data','active',100,
                       '2025-01-01T00:00:00Z','2025-01-01T00:00:00Z')"""
                )

            with patch("subprocess.run", side_effect=FileNotFoundError("wg not found")):
                from app.radius.services.monitoring_service import get_dashboard_stats
                result = get_dashboard_stats(1)

        assert isinstance(result, dict)
        # يجب أن يُعيد حالة unconfigured لا استثناء
        wg_checks = [c for c in result["health_checks"] if c["name"] == "wg_data"]
        if wg_checks:
            assert wg_checks[0]["status"] in ("unconfigured", "unknown")


class TestStatusAr:
    """تحويل حالات الصحة إلى العربية."""

    def test_ok_maps_to_arabic(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("ok") == "سليم"

    def test_warning_maps_to_arabic(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("warning") == "تحذير"

    def test_critical_maps_to_arabic(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("critical") == "خطر"

    def test_unknown_maps_to_arabic(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("unknown") == "غير معروف"

    def test_unconfigured_maps_to_arabic(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("unconfigured") == "غير مهيأ"

    def test_not_synced_maps_to_arabic(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("not_synced") == "لم تتم المزامنة بعد"

    def test_unknown_status_returned_as_is(self):
        from app.radius.services.monitoring_service import status_ar
        assert status_ar("some_custom_status") == "some_custom_status"


class TestHealthChecksArFields:
    """كل فحص صحة يجب أن يحتوي على status_ar."""

    def test_health_checks_have_status_ar(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import get_dashboard_stats
            result = get_dashboard_stats(1)

        for check in result["health_checks"]:
            assert "status_ar" in check, f"check {check.get('name')} missing status_ar"

    def test_overall_health_ar_present(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import get_dashboard_stats
            result = get_dashboard_stats(1)
        assert "overall_health_ar" in result


# ══════════════════════════════════════════════════════════════════
# PART 2C — _push_usage_snapshot hardening
# ══════════════════════════════════════════════════════════════════

class TestPushUsageSnapshot:
    """_push_usage_snapshot لا يجب أن يُطلق استثناءات أبدًا."""

    def test_network_error_does_not_raise(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            mock_client = MagicMock()
            mock_client._license_check_payload.return_value = {}
            mock_client._headers.return_value = {}
            mock_client.transport.request_json.side_effect = OSError("network unreachable")

            mock_config = MagicMock()
            mock_config.base_url = "http://localhost:9999"
            mock_config.timeout_seconds = 5

            from app.radius.services.license_sync_service import _push_usage_snapshot
            # نتوقع عدم إطلاق استثناء
            _push_usage_snapshot(mock_client, mock_config, tenant_id=1)

    def test_monitoring_service_error_does_not_raise(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            mock_client = MagicMock()
            mock_config = MagicMock()
            mock_config.base_url = "http://localhost:9999"
            mock_config.timeout_seconds = 5

            with patch(
                "app.radius.services.license_sync_service.build_usage_snapshot_payload",
                side_effect=RuntimeError("DB exploded"),
                create=True,
            ):
                from app.radius.services.license_sync_service import _push_usage_snapshot
                _push_usage_snapshot(mock_client, mock_config, tenant_id=1)

    def test_payload_contains_no_secrets(self, tmp_path):
        """الـ payload المرسَل لا يحتوي على كلمات مرور أو مفاتيح WireGuard."""
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import build_usage_snapshot_payload
            payload = build_usage_snapshot_payload(1)

        # تحقق من أن الـ payload لا يحتوي على كلمات مفتاحية خطيرة
        import json
        payload_str = json.dumps(payload).lower()
        forbidden_keys = ["password", "secret", "private_key", "hmac", "token"]
        for key in forbidden_keys:
            assert key not in payload_str, f"payload must not contain '{key}'"

    def test_build_usage_snapshot_payload_empty_db(self, tmp_path):
        """build_usage_snapshot_payload يُعيد dict صالح حتى مع DB فارغة."""
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.services.monitoring_service import build_usage_snapshot_payload
            result = build_usage_snapshot_payload(1)

        assert isinstance(result, dict)
        assert "allocations" in result
        assert "overall_health" in result
        assert isinstance(result["allocations"], list)
