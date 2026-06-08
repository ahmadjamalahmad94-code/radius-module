"""تطبيق انتهاء الصلاحية والكوتا تلقائيًا — يُشغَّل كل 15 دقيقة.

الأوضاع:
  flask enforce-expiry --tenant-id 1              ← dry-run (الافتراضي — بدون كتابة)
  flask enforce-expiry --tenant-id 1 --apply      ← تطبيق فعلي

ما يفعله:
  1. تعطيل VPN accounts عند انتهاء صلاحية التخصيص (service_allocation_mirror.expires_at)
  2. تعطيل WireGuard peers عند تجاوز كوتا النقل (transfer_limit_bytes)
  3. تعطيل خدمة WireGuard كاملةً عند تجاوز كوتا الخدمة
  4. إعادة تفعيل الخدمات المُعلَّقة بسبب كوتا عند بدء شهر جديد

قواعد الأمان:
  - dry_run=True (الافتراضي): يقرأ DB فقط — لا يكتب أي شيء، لا يُستدعى wg، لا يُرسَل
    أي طلب خارجي. آمن تمامًا للتحقق من الحالة دون عواقب.
  - dry_run=False (--apply): يُطبّق التغييرات فعليًا.
  - عزل الأخطاء: فشل معالجة كيان واحد يُسجَّل ويُتابع الباقي — لا يوقف الجري كله.
  - idempotent: الكيانات المنتهية بالفعل لا تُعالَج مرة أخرى (الاستعلام يُرشِّح حالة
    'active' فقط).
  - كل عملية مُدقَّقة في service_audit_log (عند التطبيق الفعلي).
  - كل كتابات DB ضمن transaction مستقل لكل كيان.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

_LOG = logging.getLogger(__name__)


def _db():
    from ..db.connection import db
    return db()


def _tx():
    from ..db.connection import transaction
    return transaction()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _wg_remove(public_key: str, interface: str) -> None:
    try:
        subprocess.run(
            ["wg", "set", interface, "peer", public_key, "remove"],
            capture_output=True, timeout=10, check=False,
        )
    except FileNotFoundError:
        _LOG.debug("wg not found — skipping live removal (dev mode)")
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("wg remove failed for peer %.12s: %s", public_key, exc)


def _audit_conn(conn, tenant_id: int, entity_type: str, entity_id: int | None,
                action: str, description: str) -> None:
    conn.execute(
        """INSERT INTO service_audit_log
           (tenant_id, entity_type, entity_id, action, actor, description, created_at)
           VALUES (?,?,?,?,?,?,?)""",
        (tenant_id, entity_type, entity_id, action, "system", description, _now()),
    )


# ─── 1. انتهاء صلاحية التخصيصات ─────────────────────────────────

def _expire_allocations(tenant_id: int, results: dict[str, int], dry_run: bool) -> None:
    """يُعطّل التخصيصات المنتهية وحسابات VPN المرتبطة بها.

    Idempotent: only selects status='active' — already-expired rows are ignored.
    Per-record isolation: one bad row does not stop the rest.
    """
    now_iso = _now()
    expired = _db().execute(
        """SELECT id FROM service_allocation_mirror
           WHERE tenant_id=? AND status='active'
             AND expires_at IS NOT NULL AND expires_at != ''
             AND expires_at < ?""",
        (tenant_id, now_iso),
    ).fetchall()

    for row in expired:
        alloc_id = row["id"]
        try:
            # حساب عدد الحسابات المتأثرة (للـ dry-run والـ apply معًا)
            acc_row = _db().execute(
                "SELECT COUNT(*) AS c FROM vpn_account "
                "WHERE allocation_mirror_id=? AND status IN ('active','suspended')",
                (alloc_id,),
            ).fetchone()
            acc_count = int(acc_row["c"]) if acc_row else 0

            if dry_run:
                _LOG.info(
                    "[DRY-RUN] would expire allocation id=%d "
                    "→ disable %d vpn account(s)",
                    alloc_id, acc_count,
                )
                results["allocations_expired"] += 1
                results["vpn_accounts_expired"] += acc_count
                continue

            with _tx() as conn:
                conn.execute(
                    "UPDATE service_allocation_mirror "
                    "SET status='expired', updated_at=? WHERE id=?",
                    (now_iso, alloc_id),
                )
                _audit_conn(conn, tenant_id, "allocation_mirror", alloc_id,
                            "expire", "انتهت صلاحية التخصيص تلقائيًا")

                conn.execute(
                    """UPDATE vpn_account SET status='expired', updated_at=?
                       WHERE allocation_mirror_id=? AND status IN ('active','suspended')""",
                    (now_iso, alloc_id),
                )
                # SELECT changes() يعمل على نفس الاتصال thread-local — يُعيد عدد
                # الصفوف التي عدَّلها آخر UPDATE في هذا الاتصال.
                actual = _db().execute("SELECT changes()").fetchone()[0]
                if actual:
                    _audit_conn(conn, tenant_id, "allocation_mirror", alloc_id,
                                "cascade_expire",
                                f"تعطيل {actual} حساب VPN بسبب انتهاء التخصيص")

            results["allocations_expired"] += 1
            results["vpn_accounts_expired"] += actual
            _LOG.info(
                "enforcer: expired allocation id=%d, disabled %d vpn accounts",
                alloc_id, actual,
            )

        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "enforcer: error processing allocation id=%d: %s",
                alloc_id, exc, exc_info=True,
            )
            results["errors"] = results.get("errors", 0) + 1


# ─── 2. كوتا WireGuard Peers ──────────────────────────────────────

def _enforce_wg_peer_quotas(tenant_id: int, results: dict[str, int], dry_run: bool) -> None:
    """يُعطّل peers تجاوزت transfer_limit_bytes ويُزيلها live.

    Idempotent: only selects status='active' — quota_exceeded peers are ignored.
    """
    over_quota = _db().execute(
        """SELECT p.id, p.public_key, p.service_id, s.interface_name
           FROM wireguard_peer p
           JOIN wireguard_data_service s ON s.id = p.service_id
           WHERE p.tenant_id=?
             AND p.status = 'active'
             AND p.transfer_limit_bytes IS NOT NULL
             AND p.transfer_limit_bytes > 0
             AND p.quota_bytes_used >= p.transfer_limit_bytes""",
        (tenant_id,),
    ).fetchall()

    for row in over_quota:
        peer_id = row["id"]
        pub_key = row["public_key"]
        iface   = row["interface_name"]
        try:
            if dry_run:
                _LOG.info(
                    "[DRY-RUN] would set peer id=%d to quota_exceeded (iface=%s)",
                    peer_id, iface,
                )
                results["wg_peers_quota_exceeded"] += 1
                continue

            now_iso = _now()
            with _tx() as conn:
                conn.execute(
                    "UPDATE wireguard_peer "
                    "SET status='quota_exceeded', updated_at=? WHERE id=?",
                    (now_iso, peer_id),
                )
                _audit_conn(conn, tenant_id, "wireguard_peer", peer_id,
                            "quota_exceeded",
                            "تجاوز الـ peer حد النقل — تعطيل تلقائي")

            _wg_remove(pub_key, iface)

            # حذف ملف الـ peer من peers.d
            try:
                from .wg_data_manager import _peer_file
                pf = _peer_file(peer_id)
                if pf.exists():
                    pf.unlink()
            except Exception:  # noqa: BLE001
                pass

            results["wg_peers_quota_exceeded"] += 1
            _LOG.info(
                "enforcer: peer id=%d quota exceeded — removed from %s",
                peer_id, iface,
            )

        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "enforcer: error processing wg peer id=%d: %s",
                peer_id, exc, exc_info=True,
            )
            results["errors"] = results.get("errors", 0) + 1


# ─── 3. كوتا خدمة WireGuard ──────────────────────────────────────

def _enforce_wg_service_quota(tenant_id: int, results: dict[str, int], dry_run: bool) -> None:
    """يُعطّل خدمة WireGuard البيانات كاملةً عند تجاوز كوتا الخدمة.

    Idempotent: only selects status='active'.
    """
    svc_row = _db().execute(
        """SELECT id, interface_name, transfer_limit_bytes, quota_bytes_used
           FROM wireguard_data_service
           WHERE tenant_id=?
             AND status = 'active'
             AND transfer_limit_bytes IS NOT NULL
             AND transfer_limit_bytes > 0
             AND quota_bytes_used >= transfer_limit_bytes
           LIMIT 1""",
        (tenant_id,),
    ).fetchone()

    if not svc_row:
        return

    svc_id = svc_row["id"]
    try:
        if dry_run:
            _LOG.warning(
                "[DRY-RUN] would set wg-data service id=%d to quota_exceeded "
                "(%d MB / %d MB)",
                svc_id,
                (svc_row["quota_bytes_used"] or 0) // 1_048_576,
                (svc_row["transfer_limit_bytes"] or 0) // 1_048_576,
            )
            results["wg_service_quota_exceeded"] += 1
            return

        now_iso = _now()
        with _tx() as conn:
            conn.execute(
                "UPDATE wireguard_data_service "
                "SET status='quota_exceeded', updated_at=? WHERE id=?",
                (now_iso, svc_id),
            )
            _audit_conn(
                conn, tenant_id, "wireguard_data_service", svc_id,
                "quota_exceeded",
                f"الخدمة تجاوزت الكوتا "
                f"({(svc_row['quota_bytes_used'] or 0) // 1_048_576} MB / "
                f"{(svc_row['transfer_limit_bytes'] or 0) // 1_048_576} MB)",
            )

        results["wg_service_quota_exceeded"] += 1
        _LOG.warning("enforcer: wg-data service id=%d quota exceeded", svc_id)

    except Exception as exc:  # noqa: BLE001
        _LOG.error(
            "enforcer: error processing wg service id=%d: %s",
            svc_id, exc, exc_info=True,
        )
        results["errors"] = results.get("errors", 0) + 1


# ─── 4. إعادة تفعيل كوتا الشهر الجديد ───────────────────────────

def _reset_new_period_quotas(tenant_id: int, results: dict[str, int], dry_run: bool) -> None:
    """يُعيد تفعيل الـ peers والخدمات التي كانت محجوبة بسبب الكوتا
    عند بدء شهر جديد (quota_period != الشهر الحالي).

    Per-record isolation applies to each peer individually.
    """
    current = _period()
    now_iso = _now()

    # Peers: quota_exceeded + quota_period قديم → active
    old_peers = _db().execute(
        """SELECT p.id, p.public_key, p.peer_address, s.interface_name
           FROM wireguard_peer p
           JOIN wireguard_data_service s ON s.id = p.service_id
           WHERE p.tenant_id=?
             AND p.status = 'quota_exceeded'
             AND (p.quota_period IS NULL OR p.quota_period != ?)""",
        (tenant_id, current),
    ).fetchall()

    for row in old_peers:
        peer_id = row["id"]
        pub     = row["public_key"]
        addr    = row["peer_address"]
        iface   = row["interface_name"]
        try:
            if dry_run:
                _LOG.info(
                    "[DRY-RUN] would reactivate peer id=%d (new quota period %s)",
                    peer_id, current,
                )
                results["wg_peers_reactivated"] = results.get("wg_peers_reactivated", 0) + 1
                continue

            with _tx() as conn:
                conn.execute(
                    """UPDATE wireguard_peer
                       SET status='active', quota_bytes_used=0, quota_period=?, updated_at=?
                       WHERE id=?""",
                    (current, now_iso, peer_id),
                )
                _audit_conn(conn, tenant_id, "wireguard_peer", peer_id,
                            "activate", "إعادة تفعيل تلقائية — بداية فترة كوتا جديدة")

            # إعادة إضافة الـ peer live
            try:
                subprocess.run(
                    ["wg", "set", iface, "peer", pub, "allowed-ips", f"{addr}/32"],
                    capture_output=True, timeout=10, check=False,
                )
            except FileNotFoundError:
                pass
            try:
                from .wg_data_manager import _write_peer_file
                _write_peer_file(peer_id, pub, addr)
            except Exception:  # noqa: BLE001
                pass

            results["wg_peers_reactivated"] = results.get("wg_peers_reactivated", 0) + 1

        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "enforcer: error reactivating wg peer id=%d: %s",
                peer_id, exc, exc_info=True,
            )
            results["errors"] = results.get("errors", 0) + 1

    # خدمة WireGuard: quota_exceeded + quota_period قديم → active
    old_svc = _db().execute(
        """SELECT id FROM wireguard_data_service
           WHERE tenant_id=?
             AND status = 'quota_exceeded'
             AND (quota_period IS NULL OR quota_period != ?)""",
        (tenant_id, current),
    ).fetchone()

    if old_svc:
        svc_id = old_svc["id"]
        try:
            if dry_run:
                _LOG.info(
                    "[DRY-RUN] would reactivate wg-data service id=%d (new quota period %s)",
                    svc_id, current,
                )
                results["wg_service_reactivated"] = results.get("wg_service_reactivated", 0) + 1
                return

            with _tx() as conn:
                conn.execute(
                    """UPDATE wireguard_data_service
                       SET status='active', quota_bytes_used=0, quota_period=?, updated_at=?
                       WHERE id=?""",
                    (current, now_iso, svc_id),
                )
                _audit_conn(conn, tenant_id, "wireguard_data_service", svc_id,
                            "activate", "إعادة تفعيل تلقائية — بداية فترة كوتا جديدة")
            results["wg_service_reactivated"] = results.get("wg_service_reactivated", 0) + 1

        except Exception as exc:  # noqa: BLE001
            _LOG.error(
                "enforcer: error reactivating wg service id=%d: %s",
                svc_id, exc, exc_info=True,
            )
            results["errors"] = results.get("errors", 0) + 1


# ─── الدالة الرئيسية ──────────────────────────────────────────────

def run(tenant_id: int = 1, dry_run: bool = True) -> dict[str, Any]:
    """نقطة الدخول الرئيسية — يُشغَّل كل 15 دقيقة.

    المعاملات:
        tenant_id: معرّف المستأجر (default: 1)
        dry_run:   True (default) — اقرأ فقط، لا تكتب شيئًا.
                   False         — طبِّق التغييرات فعليًا (--apply في CLI).

    يُعيد ملخص ما عُولج (أو ما كان سيُعالَج في وضع dry-run):
    {
        allocations_expired,
        vpn_accounts_expired,
        wg_peers_quota_exceeded,
        wg_service_quota_exceeded,
        wg_peers_reactivated,     # إذا وجد
        wg_service_reactivated,   # إذا وجد
        errors,                   # إذا وجد — عدد الأخطاء المعزولة
        dry_run: bool,
    }

    ملاحظات الأمان:
    - في وضع dry-run: لا يكتب في DB، لا يستدعي wg، لا يُرسل للشبكة.
    - في وضع apply: كل كيان مُعزول — فشل أحدهم لا يوقف البقية.
    - الدالة نفسها لا تُطلق استثناءات للخارج — تلتقطها وتُعيدها في "error".
    """
    results: dict[str, Any] = {
        "allocations_expired": 0,
        "vpn_accounts_expired": 0,
        "wg_peers_quota_exceeded": 0,
        "wg_service_quota_exceeded": 0,
        "dry_run": dry_run,
    }

    mode = "DRY-RUN" if dry_run else "APPLY"

    try:
        _reset_new_period_quotas(tenant_id, results, dry_run)
        _expire_allocations(tenant_id, results, dry_run)
        _enforce_wg_peer_quotas(tenant_id, results, dry_run)
        _enforce_wg_service_quota(tenant_id, results, dry_run)
    except Exception as exc:  # noqa: BLE001
        _LOG.error("expiry_enforcer: unhandled error: %s", exc, exc_info=True)
        results["error"] = str(exc)

    _LOG.info("expiry_enforcer [%s]: done %s", mode, results)
    return results


__all__ = ["run"]
