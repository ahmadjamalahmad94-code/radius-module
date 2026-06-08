"""اختبارات expiry_enforcer — PART 2A Production Hardening.

تغطّي:
  1. وضع dry-run: لا يكتب في DB، لا يغيّر حالة الكيانات
  2. وضع apply: يُعطّل التخصيصات المنتهية + VPN accounts
  3. وضع apply: يُعطّل WireGuard peers عند تجاوز الكوتا
  4. وضع apply: إعادة تفعيل عند بدء شهر جديد
  5. idempotency: تشغيل مضاعف لا يُعيد المعالجة
  6. عزل الأخطاء: خطأ في كيان لا يوقف البقية
  7. نطاق tenant_id: لا تُلمَس تخصيصات مستأجر آخر
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Bootstrap project in path ──────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")


# ── Helpers to build an in-memory app + DB ─────────────────────

def _make_app(tmp_path: Path):
    """يُنشئ تطبيقًا بـ SQLite مؤقت في ذاكرة الاختبار."""
    db_path = str(tmp_path / "test.db")
    os.environ["HOBERADIUS_DB_PATH"] = db_path

    from app import create_app
    app = create_app()
    return app


def _seed_allocation(conn, tenant_id: int, alloc_id: int, expires_at: str, status: str = "active"):
    conn.execute(
        """INSERT OR REPLACE INTO service_allocation_mirror
           (id, tenant_id, remote_allocation_id, service_type, status,
            chr_node_name, chr_node_public_ip,
            speed_limit_mbps, max_accounts, max_peers,
            transfer_limit_bytes, expires_at,
            synced_at, payload_hash, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (alloc_id, tenant_id, alloc_id, "pptp_vpn", status,
         "chr1", "1.2.3.4",
         100, 10, 0,
         None, expires_at,
         "2025-01-01T00:00:00Z", "abc", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )


def _seed_vpn_account(conn, tenant_id: int, acc_id: int, alloc_id: int, status: str = "active"):
    conn.execute(
        """INSERT OR REPLACE INTO vpn_account
           (id, tenant_id, username, password_hash, status,
            allocation_mirror_id, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (acc_id, tenant_id, f"user{acc_id}", "hash", status,
         alloc_id, "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )


def _seed_wg_service(conn, tenant_id: int, svc_id: int, interface: str = "wg-data",
                     transfer_limit: int = 0, quota_used: int = 0, status: str = "active"):
    conn.execute(
        """INSERT OR REPLACE INTO wireguard_data_service
           (id, tenant_id, interface_name, status,
            transfer_limit_bytes, quota_bytes_used, max_peers,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (svc_id, tenant_id, interface, status,
         transfer_limit or None, quota_used, 100,
         "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )


def _seed_wg_peer(conn, tenant_id: int, peer_id: int, service_id: int,
                  transfer_limit: int, quota_used: int,
                  status: str = "active", quota_period: str | None = None):
    conn.execute(
        """INSERT OR REPLACE INTO wireguard_peer
           (id, tenant_id, service_id, display_name, public_key,
            peer_address, status,
            transfer_limit_bytes, quota_bytes_used, quota_period,
            speed_limit_mbps, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (peer_id, tenant_id, service_id, f"peer{peer_id}", f"pubkey{peer_id}==",
         f"10.0.0.{peer_id}", status,
         transfer_limit, quota_used, quota_period,
         10, "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
    )


# ═══════════════════════════════════════════════════════════════════

class TestExpiryEnforcerDryRun:
    """وضع dry-run: لا يجب أن يتغيّر أي شيء في DB."""

    def test_dry_run_does_not_expire_allocation(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import db, transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")
                _seed_vpn_account(conn, 1, 1, 1)

            from app.radius.services.expiry_enforcer import run
            result = run(tenant_id=1, dry_run=True)

        assert result["allocations_expired"] == 1   # contou
        assert result["vpn_accounts_expired"] == 1
        assert result["dry_run"] is True

        # verificar que DB não mudou
        with app.app_context():
            from app.radius.db.connection import db
            alloc = db().execute(
                "SELECT status FROM service_allocation_mirror WHERE id=1"
            ).fetchone()
            acc = db().execute(
                "SELECT status FROM vpn_account WHERE id=1"
            ).fetchone()
            assert alloc["status"] == "active", "dry-run must NOT expire allocation"
            assert acc["status"] == "active", "dry-run must NOT expire vpn_account"

    def test_dry_run_does_not_exceed_wg_peer_quota(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_wg_service(conn, 1, 1)
                _seed_wg_peer(conn, 1, 1, 1,
                               transfer_limit=1_000_000, quota_used=2_000_000)

            from app.radius.services.expiry_enforcer import run
            result = run(tenant_id=1, dry_run=True)

        assert result["wg_peers_quota_exceeded"] == 1
        assert result["dry_run"] is True

        with app.app_context():
            from app.radius.db.connection import db
            peer = db().execute(
                "SELECT status FROM wireguard_peer WHERE id=1"
            ).fetchone()
            assert peer["status"] == "active", "dry-run must NOT change peer status"

    def test_dry_run_no_audit_log_entries(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import db, transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")

            before = db().execute(
                "SELECT COUNT(*) AS c FROM service_audit_log"
            ).fetchone()["c"]

            from app.radius.services.expiry_enforcer import run
            run(tenant_id=1, dry_run=True)

            after = db().execute(
                "SELECT COUNT(*) AS c FROM service_audit_log"
            ).fetchone()["c"]
            assert after == before, "dry-run must NOT write audit log entries"


class TestExpiryEnforcerApply:
    """وضع apply: يجب أن تُعطَّل الكيانات المنتهية."""

    def test_apply_expires_allocation_and_accounts(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import db, transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")
                _seed_vpn_account(conn, 1, 1, 1, status="active")
                _seed_vpn_account(conn, 1, 2, 1, status="suspended")

            from app.radius.services.expiry_enforcer import run
            result = run(tenant_id=1, dry_run=False)

        assert result["allocations_expired"] == 1
        assert result["vpn_accounts_expired"] == 2
        assert result.get("errors", 0) == 0

        with app.app_context():
            from app.radius.db.connection import db
            alloc = db().execute(
                "SELECT status FROM service_allocation_mirror WHERE id=1"
            ).fetchone()
            accs = db().execute(
                "SELECT status FROM vpn_account WHERE allocation_mirror_id=1"
            ).fetchall()
            assert alloc["status"] == "expired"
            assert all(r["status"] == "expired" for r in accs)

    def test_apply_creates_audit_log(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import db, transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")

            from app.radius.services.expiry_enforcer import run
            run(tenant_id=1, dry_run=False)

            count = db().execute(
                "SELECT COUNT(*) AS c FROM service_audit_log "
                "WHERE entity_type='allocation_mirror' AND action='expire'"
            ).fetchone()["c"]
            assert count >= 1

    def test_apply_wg_peer_quota_exceeded(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_wg_service(conn, 1, 1)
                _seed_wg_peer(conn, 1, 1, 1,
                               transfer_limit=1_000_000, quota_used=1_000_000)

            with patch(
                "app.radius.services.expiry_enforcer._wg_remove",
                return_value=None,
            ):
                from app.radius.services.expiry_enforcer import run
                result = run(tenant_id=1, dry_run=False)

        assert result["wg_peers_quota_exceeded"] == 1

        with app.app_context():
            from app.radius.db.connection import db
            peer = db().execute(
                "SELECT status FROM wireguard_peer WHERE id=1"
            ).fetchone()
            assert peer["status"] == "quota_exceeded"

    def test_non_expired_allocation_untouched(self, tmp_path):
        """تخصيص لم ينتهِ بعد يجب أن يبقى active."""
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2099-01-01T00:00:00Z")
                _seed_vpn_account(conn, 1, 1, 1)

            from app.radius.services.expiry_enforcer import run
            result = run(tenant_id=1, dry_run=False)

        assert result["allocations_expired"] == 0

        with app.app_context():
            from app.radius.db.connection import db
            alloc = db().execute(
                "SELECT status FROM service_allocation_mirror WHERE id=1"
            ).fetchone()
            assert alloc["status"] == "active"


class TestExpiryEnforcerIdempotency:
    """تشغيل مضاعف لا يُعيد المعالجة."""

    def test_double_apply_is_idempotent(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")
                _seed_vpn_account(conn, 1, 1, 1)

            from app.radius.services.expiry_enforcer import run
            r1 = run(tenant_id=1, dry_run=False)
            r2 = run(tenant_id=1, dry_run=False)

        assert r1["allocations_expired"] == 1
        assert r2["allocations_expired"] == 0, "second run must not re-expire"


class TestExpiryEnforcerIsolation:
    """خطأ في كيان لا يوقف البقية."""

    def test_db_error_on_one_alloc_continues_rest(self, tmp_path):
        """إذا فشل تخصيص واحد، يجب أن يُعالَج الباقي."""
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")
                _seed_allocation(conn, 1, 2, "2020-01-01T00:00:00Z")

            call_count = {"n": 0}
            original_tx = None

            import app.radius.services.expiry_enforcer as _ee

            orig_tx = _ee._tx

            def _failing_tx_first_call():
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("simulated DB error on first alloc")
                return orig_tx()

            with patch.object(_ee, "_tx", side_effect=_failing_tx_first_call):
                from app.radius.services.expiry_enforcer import run
                result = run(tenant_id=1, dry_run=False)

        # واحد نجح، واحد فشل
        assert result.get("errors", 0) >= 1
        assert result["allocations_expired"] >= 1


class TestExpiryEnforcerQuotaReset:
    """إعادة تفعيل الكوتا عند بدء شهر جديد."""

    def test_reactivates_peer_with_old_quota_period(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_wg_service(conn, 1, 1)
                _seed_wg_peer(conn, 1, 1, 1,
                               transfer_limit=1_000_000, quota_used=2_000_000,
                               status="quota_exceeded",
                               quota_period="2020-01")  # شهر قديم

            with (
                patch("subprocess.run", return_value=MagicMock(returncode=0)),
                patch(
                    "app.radius.services.expiry_enforcer._write_peer_file",
                    return_value=None, create=True,
                ),
            ):
                from app.radius.services.expiry_enforcer import run
                result = run(tenant_id=1, dry_run=False)

        assert result.get("wg_peers_reactivated", 0) == 1

        with app.app_context():
            from app.radius.db.connection import db
            peer = db().execute(
                "SELECT status, quota_bytes_used FROM wireguard_peer WHERE id=1"
            ).fetchone()
            assert peer["status"] == "active"
            assert peer["quota_bytes_used"] == 0


class TestExpiryEnforcerTenantScope:
    """tenant_id scoping: لا تُلمَس كيانات مستأجر آخر."""

    def test_other_tenant_allocation_untouched(self, tmp_path):
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import transaction
            with transaction() as conn:
                _seed_allocation(conn, 1, 1, "2020-01-01T00:00:00Z")  # tenant 1
                _seed_allocation(conn, 2, 2, "2020-01-01T00:00:00Z")  # tenant 2

            from app.radius.services.expiry_enforcer import run
            run(tenant_id=1, dry_run=False)

        with app.app_context():
            from app.radius.db.connection import db
            t2 = db().execute(
                "SELECT status FROM service_allocation_mirror WHERE id=2"
            ).fetchone()
            assert t2["status"] == "active", "other tenant alloc must be untouched"
