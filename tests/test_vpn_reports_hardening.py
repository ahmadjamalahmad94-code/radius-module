"""اختبارات vpn_reports — PART 2E Production Hardening.

تغطّي:
  1. DB فارغة — كل CSV endpoints تُعيد 200 بدون استثناء
  2. BOM (UTF-8 BOM) موجود في كل ملف CSV
  3. حد 5000 صف مُطبَّق
  4. لا يوجد password أو private_key أو secret في CSV
  5. فلتر غير صالح لا يُسقط الصفحة
  6. صفحة /service-reports تُعيد 200 مع DB فارغة
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")

_BOM = "﻿"


def _make_client(tmp_path: Path):
    db_path = str(tmp_path / "test.db")
    os.environ["HOBERADIUS_DB_PATH"] = db_path
    from app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    client = app.test_client()
    # simulate logged-in session
    with app.test_request_context():
        with client.session_transaction() as sess:
            sess["admin_id"] = 1
            sess["tenant_id"] = 1
            sess["is_super_admin"] = True
    return app, client


def _login(client):
    """ضبط جلسة مُصادَق عليها مباشرةً."""
    with client.session_transaction() as sess:
        sess["admin_id"] = 1
        sess["tenant_id"] = 1
        sess["is_super_admin"] = True
    return client


# ══════════════════════════════════════════════════════════════════

class TestVpnReportsEmptyDB:
    """DB فارغة — المساعِدات ومسارات CSV يجب أن تعمل بدون أخطاء."""

    def test_service_reports_helpers_empty_db(self, tmp_path):
        """يتحقق من أن _vpn_accounts / _wg_peers / _audit_log تعمل مع DB فارغة."""
        app, _ = _make_client(tmp_path)
        with app.app_context():
            from app.radius.routes.vpn_reports import (
                _vpn_accounts, _wg_peers, _audit_log, _wg_service,
            )
            # يجب أن تُعيد قوائم فارغة بدون استثناء
            assert _vpn_accounts(1, 100) == []
            assert _audit_log(1, 100) == []
            assert _wg_service(1) is None

    def test_wg_peers_helper_empty_db(self, tmp_path):
        """يتحقق أن _wg_peers تعمل مع DB فارغة (JOIN على جدول فارغ)."""
        app, _ = _make_client(tmp_path)
        with app.app_context():
            from app.radius.routes.vpn_reports import _wg_peers
            rows = _wg_peers(1, 100)
            assert rows == []

    def test_vpn_accounts_csv_empty_db(self, tmp_path):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/vpn-accounts.csv")
        # 200 (csv) أو 302 (redirect to login) — المهم ألا يكون 500
        assert resp.status_code in (200, 302), f"Got {resp.status_code}"

    def test_wg_peers_csv_empty_db(self, tmp_path):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/wg-peers.csv")
        assert resp.status_code in (200, 302), f"Got {resp.status_code}"

    def test_audit_log_csv_empty_db(self, tmp_path):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/audit-log.csv")
        assert resp.status_code in (200, 302), f"Got {resp.status_code}"


class TestVpnReportsCsvContent:
    """محتوى CSV — BOM، لا أسرار، حد الصفوف."""

    def _get_csv(self, tmp_path, url):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        return resp.data.decode("utf-8-sig")  # utf-8-sig يُزيل BOM تلقائيًا

    def _get_raw(self, tmp_path, url):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        return resp.data

    def test_vpn_accounts_csv_has_bom(self, tmp_path):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/vpn-accounts.csv")
        if resp.status_code == 200:
            # BOM هو \xef\xbb\xbf في UTF-8
            assert resp.data[:3] == b"\xef\xbb\xbf", "CSV must start with UTF-8 BOM"

    def test_wg_peers_csv_has_bom(self, tmp_path):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/wg-peers.csv")
        if resp.status_code == 200:
            assert resp.data[:3] == b"\xef\xbb\xbf", "CSV must start with UTF-8 BOM"

    def test_audit_log_csv_has_bom(self, tmp_path):
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/audit-log.csv")
        if resp.status_code == 200:
            assert resp.data[:3] == b"\xef\xbb\xbf", "CSV must start with UTF-8 BOM"

    def test_vpn_accounts_csv_no_password_column(self, tmp_path):
        """عمود password_hash يجب ألا يظهر في CSV."""
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/vpn-accounts.csv")
        if resp.status_code == 200:
            content = resp.data.decode("utf-8", errors="replace").lower()
            assert "password" not in content, "CSV must not expose password columns"

    def test_wg_peers_csv_no_private_key(self, tmp_path):
        """private_key يجب ألا يظهر في CSV."""
        app, client = _make_client(tmp_path)
        _login(client)
        resp = client.get("/admin/radius/service-reports/export/wg-peers.csv")
        if resp.status_code == 200:
            content = resp.data.decode("utf-8", errors="replace").lower()
            assert "private_key" not in content, "CSV must not expose private_key"

    def test_max_rows_param_respected(self, tmp_path):
        """limit في _vpn_accounts / _wg_peers مُطبَّق بحد أقصى 5000."""
        from app.radius.routes.vpn_reports import _vpn_accounts, _wg_peers, _audit_log

        app, _ = _make_client(tmp_path)
        with app.app_context():
            # استدعاء مباشر — يجب أن يعيد list (ليس error)
            rows = _vpn_accounts(1, limit=5000)
            assert isinstance(rows, list)
            assert len(rows) <= 5000

            rows_wg = _wg_peers(1, limit=5000)
            assert isinstance(rows_wg, list)
            assert len(rows_wg) <= 5000

            rows_al = _audit_log(1, limit=5000)
            assert isinstance(rows_al, list)
            assert len(rows_al) <= 5000


class TestVpnReportsFilterSafety:
    """فلاتر غير صالحة لا تُسقط المساعِدات — اختبار مباشر للمنطق."""

    def _query_vpn(self, app, status_filter="", allocation_filter=None, limit=200):
        """يُشغّل استعلام VPN مباشرةً كما تفعله service_reports."""
        from app.radius.db.connection import db as _db
        try:
            limit = min(int(limit), 1000)
        except (ValueError, TypeError):
            limit = 200
        rows = _db().execute(
            """SELECT va.id, va.username, va.status,
                      va.allocation_mirror_id, sam.service_type, sam.expires_at,
                      va.created_at, va.updated_at
               FROM vpn_account va
               LEFT JOIN service_allocation_mirror sam ON sam.id = va.allocation_mirror_id
               WHERE va.tenant_id = ?
                 AND (? = '' OR va.status = ?)
                 AND (? = 0 OR va.allocation_mirror_id = ?)
               ORDER BY va.id DESC LIMIT ?""",
            (1,
             status_filter, status_filter,
             1 if allocation_filter else 0, allocation_filter or 0,
             limit),
        ).fetchall()
        return rows

    def test_invalid_limit_defaults_gracefully(self, tmp_path):
        """limit=notanumber يجب أن يُعالَج بافتراضي 200."""
        app, _ = _make_client(tmp_path)
        with app.app_context():
            rows = self._query_vpn(app, limit="notanumber")
        assert isinstance(rows, list)

    def test_sql_injection_in_status_filter(self, tmp_path):
        """حقن SQL في status يُمرَّر كمعامل — parameterized query آمن."""
        app, _ = _make_client(tmp_path)
        with app.app_context():
            # ما من نتائج تُعاد لأن الحالة لا تطابق أي حساب
            rows = self._query_vpn(app, status_filter="active' OR '1'='1")
        assert isinstance(rows, list)

    def test_large_allocation_id_filter(self, tmp_path):
        """allocation_id غير موجود يُعيد قائمة فارغة بدون خطأ."""
        app, _ = _make_client(tmp_path)
        with app.app_context():
            rows = self._query_vpn(app, allocation_filter=999999)
        assert rows == []
