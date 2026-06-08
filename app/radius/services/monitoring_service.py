"""خدمة المراقبة والتنبيهات — تجمع كل مقاييس الصحة والسعة لصفحة المتابعة.

سياسة السعة (مُطبَّقة في _capacity_health_pct):
    < 70%  → ok       (أخضر — السماح بتخصيصات جديدة)
    70-85% → warning  (أصفر — تحذير، التخصيصات مقيَّدة)
    > 85%  → critical (أحمر — حجب التخصيصات الجديدة)

متانة (hardening):
    - كل قسم من أقسام get_dashboard_stats مُحاط بـ try/except مستقل — خطأ
      في أحد الأقسام (جدول غير موجود، DB غير جاهزة، wireguard غير مثبّت)
      لا يُسقط الصفحة كاملةً.
    - _check_wg_interface تتعامل مع غياب wg (بيئة تطوير) بشكل صريح.
    - status_ar: تحويل الحالات الإنجليزية إلى عربية لعرضها في الواجهة.
"""
from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone
from typing import Any

_LOG = logging.getLogger(__name__)

# عتبات السعة (ثوابت حرجة — لا تُغيَّر دون مراجعة معمارية)
_WARN_PCT  = 70.0
_BLOCK_PCT = 85.0

# عتبة قِدَم المزامنة بالدقائق
_SYNC_WARN_MIN  = 10
_SYNC_CRIT_MIN  = 20

# ترجمة حالات الصحة إلى العربية
_STATUS_AR: dict[str, str] = {
    "ok":          "سليم",
    "warning":     "تحذير",
    "critical":    "خطر",
    "unknown":     "غير معروف",
    "unconfigured": "غير مهيأ",
    "disconnected": "غير متصل",
    "not_synced":   "لم تتم المزامنة بعد",
}


def status_ar(status: str) -> str:
    """يُعيد الترجمة العربية لحالة الصحة — يعود للحالة الأصلية إن لم تُعرَّف."""
    return _STATUS_AR.get(str(status).lower(), status)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _db():
    from ..db.connection import db
    return db()


# ─── حساب حالة السعة ─────────────────────────────────────────────

def _capacity_health_pct(pct: float) -> str:
    if pct >= _BLOCK_PCT:
        return "critical"
    if pct >= _WARN_PCT:
        return "warning"
    return "ok"


def _capacity_health(used: int, max_allowed: int) -> str:
    """يُحدِّد health_status بناءً على العدد المستخدم والحد الأقصى."""
    if max_allowed <= 0:
        return "ok"  # بدون حد → لا يوجد خطر سعة
    return _capacity_health_pct(used / max_allowed * 100.0)


# ─── فحوصات الصحة ────────────────────────────────────────────────

def _check_license_sync(snapshot: dict | None) -> dict:
    if not snapshot:
        return {"name": "license_sync", "title": "مزامنة الترخيص",
                "status": "not_synced", "status_ar": status_ar("not_synced"),
                "detail": "لم تتم المزامنة بعد"}
    synced_str = snapshot.get("synced_at") or ""
    try:
        # نقبل ISO مع Z أو +00:00
        synced_at = datetime.fromisoformat(synced_str.replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - synced_at).total_seconds() / 60.0
        if age_min <= _SYNC_WARN_MIN:
            return {"name": "license_sync", "title": "مزامنة الترخيص",
                    "status": "ok", "status_ar": status_ar("ok"),
                    "detail": f"منذ {age_min:.0f} دقيقة"}
        if age_min <= _SYNC_CRIT_MIN:
            return {"name": "license_sync", "title": "مزامنة الترخيص",
                    "status": "warning", "status_ar": status_ar("warning"),
                    "detail": f"منذ {age_min:.0f} دقيقة — ابطأ من المعتاد"}
        return {"name": "license_sync", "title": "مزامنة الترخيص",
                "status": "critical", "status_ar": status_ar("critical"),
                "detail": f"منذ {age_min:.0f} دقيقة — تحقق من systemd timer"}
    except (ValueError, TypeError):
        return {"name": "license_sync", "title": "مزامنة الترخيص",
                "status": "unknown", "status_ar": status_ar("unknown"),
                "detail": "تاريخ المزامنة غير صالح"}


def _check_license_status(snapshot: dict | None) -> dict:
    if not snapshot:
        return {"name": "license_status", "title": "حالة الترخيص",
                "status": "unknown", "status_ar": status_ar("unknown"),
                "detail": "لا توجد بيانات ترخيص"}
    ls = snapshot.get("license_status", "unknown")
    st = "ok" if ls == "active" else "critical"
    return {
        "name": "license_status",
        "title": "حالة الترخيص",
        "status": st,
        "status_ar": status_ar(st),
        "detail": ls,
    }


def _check_wg_interface(iface: str) -> dict:
    """يفحص حالة واجهة WireGuard — آمن في كل البيئات."""
    try:
        r = subprocess.run(
            ["wg", "show", iface],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if r.returncode == 0:
            return {"name": "wg_data", "title": f"واجهة {iface}",
                    "status": "ok", "status_ar": status_ar("ok"),
                    "detail": "الواجهة نشطة"}
        detail = r.stderr.strip() or "wg show أعاد خطأ"
        return {"name": "wg_data", "title": f"واجهة {iface}",
                "status": "critical", "status_ar": status_ar("critical"),
                "detail": detail}
    except FileNotFoundError:
        # بيئة تطوير — wg غير مثبّت
        return {"name": "wg_data", "title": f"واجهة {iface}",
                "status": "unconfigured", "status_ar": status_ar("unconfigured"),
                "detail": "wg غير مثبّت (بيئة تطوير)"}
    except subprocess.TimeoutExpired:
        return {"name": "wg_data", "title": f"واجهة {iface}",
                "status": "unknown", "status_ar": status_ar("unknown"),
                "detail": "wg show تجاوز الوقت المحدد"}
    except Exception as exc:  # noqa: BLE001
        return {"name": "wg_data", "title": f"واجهة {iface}",
                "status": "unknown", "status_ar": status_ar("unknown"),
                "detail": str(exc)}


def _check_wg_quota(wg: dict) -> dict | None:
    limit = wg.get("transfer_limit_bytes") or 0
    if limit <= 0:
        return None
    used = wg.get("quota_bytes_used") or 0
    pct = used / limit * 100.0
    st = _capacity_health_pct(pct)
    used_mb  = used  / 1_048_576
    limit_mb = limit / 1_048_576
    return {
        "name": "wg_quota",
        "title": "كوتا WireGuard",
        "status": st,
        "status_ar": status_ar(st),
        "detail": f"{pct:.1f}% — {used_mb:.1f} MB / {limit_mb:.0f} MB",
    }


# ─── الدالة الرئيسية ──────────────────────────────────────────────

def get_dashboard_stats(tenant_id: int) -> dict[str, Any]:
    """يجمع كل بيانات لوحة المتابعة.

    كل قسم مُحاط بـ try/except مستقل — خطأ في قسم لا يُسقط بقية الأقسام.

    المُخرج:
    {
        license:         dict | None,
        vpn_allocations: list[dict],   # مع capacity_pct + health_status + status_ar
        wg_service:      dict | None,  # مع active_peers + capacity_pct + health_status
        health_checks:   list[dict],   # {name, title, status, status_ar, detail}
        overall_health:  "ok" | "warning" | "critical" | "unknown",
        generated_at:    str,
        totals: {total_accounts, total_peers, total_quota_mb},
    }
    """
    stats: dict[str, Any] = {
        "license": None,
        "vpn_allocations": [],
        "wg_service": None,
        "health_checks": [],
        "overall_health": "unknown",
        "generated_at": _now(),
        "totals": {"total_accounts": 0, "total_peers": 0, "total_quota_mb": 0.0},
    }

    # ── 1. نسخة الترخيص ──
    snapshot = None
    try:
        from .license_sync_service import get_current_snapshot
        snapshot = get_current_snapshot(tenant_id)
        stats["license"] = snapshot
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("get_dashboard_stats: license snapshot failed: %s", exc)

    # ── 2. تخصيصات VPN (غير wireguard_data) ──
    total_accounts = 0
    try:
        alloc_rows = _db().execute(
            "SELECT * FROM service_allocation_mirror "
            "WHERE tenant_id=? AND service_type != 'wireguard_data' "
            "ORDER BY service_type, id",
            (tenant_id,),
        ).fetchall()

        for row in alloc_rows:
            try:
                a = dict(row)
                count_row = _db().execute(
                    "SELECT COUNT(*) AS c FROM vpn_account "
                    "WHERE allocation_mirror_id=? AND status='active'",
                    (a["id"],),
                ).fetchone()
                active_acc = int(count_row["c"]) if count_row else 0
                max_acc    = a.get("max_accounts") or 0
                cap_pct    = round(active_acc / max_acc * 100.0, 1) if max_acc > 0 else None
                hs         = _capacity_health(active_acc, max_acc)
                a["active_accounts"] = active_acc
                a["capacity_pct"]    = cap_pct
                a["health_status"]   = hs
                a["health_status_ar"] = status_ar(hs)
                stats["vpn_allocations"].append(a)
                total_accounts += active_acc
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("get_dashboard_stats: vpn alloc row error: %s", exc)

    except Exception as exc:  # noqa: BLE001
        _LOG.warning("get_dashboard_stats: vpn allocations section failed: %s", exc)

    # ── 3. خدمة WireGuard البيانات ──
    wg_row = None
    total_peers    = 0
    total_quota_mb = 0.0
    try:
        wg_row = _db().execute(
            "SELECT * FROM wireguard_data_service WHERE tenant_id=? ORDER BY id LIMIT 1",
            (tenant_id,),
        ).fetchone()

        if wg_row:
            wg = dict(wg_row)
            try:
                peer_count_row = _db().execute(
                    "SELECT COUNT(*) AS c FROM wireguard_peer "
                    "WHERE service_id=? AND status != 'revoked'",
                    (wg["id"],),
                ).fetchone()
                active_peers = int(peer_count_row["c"]) if peer_count_row else 0
            except Exception:  # noqa: BLE001
                active_peers = 0
            max_peers    = wg.get("max_peers") or 0
            cap_pct      = round(active_peers / max_peers * 100.0, 1) if max_peers > 0 else None
            hs           = _capacity_health(active_peers, max_peers)
            wg["active_peers"]   = active_peers
            wg["capacity_pct"]   = cap_pct
            wg["health_status"]  = hs
            wg["health_status_ar"] = status_ar(hs)
            stats["wg_service"] = wg
            total_peers    = active_peers
            total_quota_mb = (wg.get("quota_bytes_used") or 0) / 1_048_576

    except Exception as exc:  # noqa: BLE001
        _LOG.warning("get_dashboard_stats: wg service section failed: %s", exc)

    stats["totals"] = {
        "total_accounts": total_accounts,
        "total_peers": total_peers,
        "total_quota_mb": round(total_quota_mb, 2),
    }

    # ── 4. فحوصات الصحة ──
    checks: list[dict] = []
    try:
        checks.append(_check_license_sync(snapshot))
        checks.append(_check_license_status(snapshot))
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("get_dashboard_stats: license checks failed: %s", exc)

    if wg_row:
        try:
            wg_iface = dict(wg_row).get("interface_name", "wg-data")
            checks.append(_check_wg_interface(wg_iface))
            quota_check = _check_wg_quota(dict(wg_row))
            if quota_check:
                checks.append(quota_check)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("get_dashboard_stats: wg checks failed: %s", exc)

    stats["health_checks"] = checks

    # ── 5. الصحة الإجمالية = أسوأ حالة ──
    all_statuses = [c["status"] for c in checks]
    for a in stats["vpn_allocations"]:
        all_statuses.append(a["health_status"])
    if stats["wg_service"]:
        all_statuses.append(stats["wg_service"]["health_status"])

    if "critical" in all_statuses:
        stats["overall_health"] = "critical"
    elif "warning" in all_statuses:
        stats["overall_health"] = "warning"
    elif all_statuses and all(s == "ok" for s in all_statuses):
        stats["overall_health"] = "ok"
    else:
        stats["overall_health"] = "unknown"

    stats["overall_health_ar"] = status_ar(stats["overall_health"])
    return stats


# ─── بناء payload للـ usage snapshot ────────────────────────────

def build_usage_snapshot_payload(tenant_id: int) -> dict[str, Any]:
    """يبني payload الـ usage snapshot لإرساله إلى لوحة التراخيص.

    الـ payload يحتوي فقط على مقاييس مجمَّعة (عدد حسابات، بيانات نقل، health)
    — لا يحتوي على كلمات مرور أو مفاتيح أو بيانات مستخدمين شخصية.
    """
    stats = get_dashboard_stats(tenant_id)

    allocations_payload = []
    for a in stats["vpn_allocations"]:
        allocations_payload.append({
            "remote_allocation_id": a.get("remote_allocation_id"),
            "service_type": a.get("service_type", ""),
            "active_accounts": a.get("active_accounts", 0),
            "active_peers": 0,
            "used_transfer_bytes": 0,
            "current_mbps": 0,
            "health_status": a.get("health_status", "unknown"),
        })

    wg = stats.get("wg_service") or {}
    wg_alloc_id = wg.get("allocation_mirror_id")
    if wg_alloc_id:
        try:
            wg_remote_id = _db().execute(
                "SELECT remote_allocation_id FROM service_allocation_mirror WHERE id=?",
                (wg_alloc_id,),
            ).fetchone()
            allocations_payload.append({
                "remote_allocation_id": wg_remote_id["remote_allocation_id"] if wg_remote_id else None,
                "service_type": "wireguard_data",
                "active_accounts": 0,
                "active_peers": wg.get("active_peers", 0),
                "used_transfer_bytes": wg.get("quota_bytes_used") or 0,
                "current_mbps": 0,
                "health_status": wg.get("health_status", "unknown"),
            })
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("build_usage_snapshot_payload: wg alloc lookup failed: %s", exc)

    return {
        "allocations": [a for a in allocations_payload if a.get("remote_allocation_id")],
        "overall_health": stats["overall_health"],
    }


__all__ = [
    "get_dashboard_stats",
    "build_usage_snapshot_payload",
    "status_ar",
    "_capacity_health",
    "_capacity_health_pct",
    "_WARN_PCT",
    "_BLOCK_PCT",
    "_STATUS_AR",
]
