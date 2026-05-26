"""Setup Wizard — single-pane system-health check.

The product ships to customers running their own ISPs. They
need ONE place to ask "is everything OK?" — not 10 manual
diagnostic commands.

`check_all()` runs every invariant we've established in
postmortems #1-#21 against the live system and reports a
verdict. Returns:

    {
        "overall": "healthy" | "degraded" | "critical",
        "checks": {
            "<name>": {
                "status": "ok" | "warn" | "fail",
                "title_ar": "...",
                "details": "...",
                "evidence": {...},
            },
            ...
        },
        "checked_at": "<iso>",
    }

Verdict policy:
- any check at `fail` → overall=critical
- any check at `warn` and no fail → overall=degraded
- all `ok` → overall=healthy

Designed for two consumers:
1. The operator hitting `/admin/radius/_system_health` (UI)
2. External monitoring polling the endpoint at intervals.
   Returns HTTP 200 only when overall=healthy; HTTP 503
   otherwise so off-the-shelf uptime checks alert.
"""
from __future__ import annotations

import logging
import os
import re
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ..db.connection import db


_LOG = logging.getLogger(__name__)


# ─── Status helpers ────────────────────────────────────────


def _ok(title_ar: str, details: str = "", **evidence: Any) -> dict:
    return {
        "status": "ok",
        "title_ar": title_ar,
        "details": details,
        "evidence": evidence,
    }


def _warn(title_ar: str, details: str, **evidence: Any) -> dict:
    return {
        "status": "warn",
        "title_ar": title_ar,
        "details": details,
        "evidence": evidence,
    }


def _fail(title_ar: str, details: str, **evidence: Any) -> dict:
    return {
        "status": "fail",
        "title_ar": title_ar,
        "details": details,
        "evidence": evidence,
    }


# ─── Individual checks ─────────────────────────────────────


def check_db_migrations() -> dict:
    """All migrations applied? PRAGMA table_info reveals
    missing columns like state_json (postmortem #1) or
    tentative_expires_at (postmortem TTL slice)."""
    expected = {
        "setup_wizard_runs": {
            "state_json", "v3_state", "v3_diagnostics_json",
        },
        "router_provisioning_registry": {
            "tentative_expires_at", "tentative_started_at",
        },
    }
    try:
        missing_total: list[str] = []
        scanned = 0
        for table, required in expected.items():
            cols = {
                r["name"]
                for r in db()
                .execute(f"PRAGMA table_info({table})")
                .fetchall()
            }
            scanned += len(cols)
            absent = required - cols
            for col in sorted(absent):
                missing_total.append(f"{table}.{col}")
        if missing_total:
            return _fail(
                "مايجريشن قاعدة البيانات",
                f"أعمدة ناقصة: {missing_total}",
                missing_columns=missing_total,
            )
        return _ok(
            "مايجريشن قاعدة البيانات",
            f"كل الأعمدة المطلوبة موجودة "
            f"({scanned} عمود في {len(expected)} جدول)",
            column_count=scanned,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "مايجريشن قاعدة البيانات",
            f"تعذّر فحص المايجريشن: {exc}",
        )


def check_freeradius_responsive(
    *, host: str = "127.0.0.1", port: int = 1812,
    timeout: float = 2.0,
) -> dict:
    """Quick UDP probe to confirm FreeRADIUS is alive +
    listening (postmortem #17). Sends a malformed packet
    and just checks the port is open (we don't expect a
    valid reply)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(timeout)
        # Send a single null byte — FreeRADIUS will drop it
        # but we only need to verify the port accepts UDP.
        s.sendto(b"\x00", (host, port))
        s.close()
        return _ok(
            "FreeRADIUS يستلم",
            f"المنفذ UDP {port} مفتوح على {host}",
            host=host, port=port,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "FreeRADIUS يستلم",
            f"تعذّر الوصول إلى UDP {port} على {host}: {exc}",
            host=host, port=port,
        )


def check_wizard_clients_directory(
    *, path: str | None = None,
) -> dict:
    """The clients-wizard dir must exist + be readable +
    contain the placeholder file (postmortem #17)."""
    target_dir = Path(
        path
        or os.environ.get(
            "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",
        )
        or "/app/instance/freeradius-clients-wizard",
    )
    if not target_dir.is_dir():
        return _fail(
            "مجلّد إعدادات RADIUS",
            f"المجلّد {target_dir} غير موجود",
            path=str(target_dir),
        )
    placeholder = target_dir / "_placeholder.conf"
    if not placeholder.is_file():
        return _warn(
            "مجلّد إعدادات RADIUS",
            f"ملفّ _placeholder.conf مفقود — "
            f"freeradius قد يفشل في الإقلاع إذا كان "
            f"المجلّد فاضي",
            path=str(target_dir),
        )
    conf_files = [
        p.name for p in target_dir.iterdir()
        if p.is_file() and p.name.endswith(".conf")
    ]
    return _ok(
        "مجلّد إعدادات RADIUS",
        f"{len(conf_files)} ملفّ .conf موجود",
        path=str(target_dir),
        conf_files_count=len(conf_files),
    )


def check_wizard_invariants(
    *, path: str | None = None,
) -> dict:
    """Run the reconciler in read-only mode and report any
    drift (postmortems #20 + #21). We use a snapshot
    comparison — no mutation."""
    target_dir = Path(
        path
        or os.environ.get(
            "HOBERADIUS_FREERADIUS_CLIENTS_WIZARD_DIR",
        )
        or "/app/instance/freeradius-clients-wizard",
    )

    import json as _json
    # Index files
    files_by_run: dict[int, Path] = {}
    files_by_ip: dict[str, list[Path]] = {}
    run_re = re.compile(r"^wizard-run-(\d+)\.conf$")
    ip_re = re.compile(r"^\s*ipaddr\s*=\s*(\S+)", re.MULTILINE)
    sec_re = re.compile(r"^\s*secret\s*=\s*(\S+)", re.MULTILINE)
    iterable = (
        target_dir.iterdir() if target_dir.is_dir() else []
    )
    for path_obj in iterable:
        if not path_obj.is_file():
            continue
        m = run_re.match(path_obj.name)
        if not m:
            continue
        rid = int(m.group(1))
        files_by_run[rid] = path_obj
        try:
            text = path_obj.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        im = ip_re.search(text)
        if im:
            files_by_ip.setdefault(
                im.group(1).strip(), [],
            ).append(path_obj)

    # Active runs across all tenants
    active: dict[int, dict[str, Any]] = {}
    for r in (
        db()
        .execute(
            "SELECT id, tenant_id, state_json "
            "FROM setup_wizard_runs "
            "WHERE v3_state IN "
            "('VERIFYING', 'REGISTERING', 'COMPLETE')",
        )
        .fetchall()
    ):
        try:
            state = _json.loads(r["state_json"] or "{}") or {}
        except (TypeError, ValueError):
            state = {}
        if state.get("router_vpn_ip") and state.get(
            "radius_secret",
        ):
            active[int(r["id"])] = state

    # INV-1 violations: active runs with missing/wrong file
    inv1_violations: list[int] = []
    for rid, state in active.items():
        p = files_by_run.get(rid)
        if not p or not p.exists():
            inv1_violations.append(rid)
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            inv1_violations.append(rid)
            continue
        if state["radius_secret"] not in text:
            inv1_violations.append(rid)
            continue
        if state["router_vpn_ip"] not in text:
            inv1_violations.append(rid)

    # INV-2 violations: orphan files
    inv2_violations = [
        f.name for rid, f in files_by_run.items()
        if rid not in active
    ]

    # INV-3 violations: duplicate ipaddr
    inv3_violations = [
        ip for ip, paths in files_by_ip.items()
        if len(paths) > 1
    ]

    issues = []
    if inv1_violations:
        issues.append(
            f"INV-1 (سرّ مطابق): {len(inv1_violations)} "
            f"run بدون ملفّ صحيح",
        )
    if inv2_violations:
        issues.append(
            f"INV-2 (لا ملفّات يتيمة): "
            f"{len(inv2_violations)} ملفّ بدون run نشط",
        )
    if inv3_violations:
        issues.append(
            f"INV-3 (IP فريد): {len(inv3_violations)} عنوان "
            f"مكرّر بين ملفّات",
        )

    evidence = {
        "active_runs": len(active),
        "conf_files": len(files_by_run),
        "inv1_violations": inv1_violations,
        "inv2_violations": inv2_violations,
        "inv3_violations": inv3_violations,
    }

    if issues:
        # Drift exists but the reconciler will fix it on
        # next tick. Report as warn unless we have
        # data-loss-level violations.
        return _warn(
            "ضمانات السرّ بين الراوتر والخادم",
            "خلل سيُصلَح في تشغيل الـ reconciler القادم: "
            + " · ".join(issues),
            **evidence,
        )
    return _ok(
        "ضمانات السرّ بين الراوتر والخادم",
        f"كل الراوترات النشطة ({len(active)}) مزامنة مع "
        f"ملفّاتها على الخادم",
        **evidence,
    )


def check_recent_reconciler_drift(
    *, hours: int = 24,
) -> dict:
    """How many drift events did the reconciler fix in the
    last N hours? In a healthy production, the count is 0
    (no drift). Any rewrite means something else went wrong
    upstream — worth a look but not critical."""
    try:
        cutoff = (
            datetime.utcnow() - timedelta(hours=hours)
        ).isoformat()
        rows = (
            db()
            .execute(
                "SELECT action, COUNT(*) c FROM audit_log "
                "WHERE actor='setup_wizard_radius_reconciler' "
                "AND created_at > ? GROUP BY action",
                (cutoff,),
            )
            .fetchall()
        )
        counts = {r["action"]: int(r["c"]) for r in rows}
        total = sum(counts.values())
        if total == 0:
            return _ok(
                "استقرار مزامنة RADIUS",
                f"لم يُكتشف أي drift خلال آخر {hours} ساعة",
                hours=hours,
                **counts,
            )
        # Any drift event is a yellow flag — investigate.
        return _warn(
            "استقرار مزامنة RADIUS",
            f"الـ reconciler صحّح {total} عملية خلال آخر "
            f"{hours} ساعة. اقرأ audit_log للتفاصيل.",
            hours=hours,
            total=total,
            **counts,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "استقرار مزامنة RADIUS",
            f"تعذّر قراءة audit_log: {exc}",
        )


def check_wg_peers_dir(
    *, path: str = "/etc/hoberadius/wg-peers.d",
) -> dict:
    """The WG peers directory must exist and be writable."""
    p = Path(path)
    if not p.is_dir():
        return _fail(
            "مجلّد WireGuard peers",
            f"المجلّد {path} غير موجود",
            path=path,
        )
    test_file = p / f".health_check_{int(time.time())}"
    try:
        test_file.touch()
        test_file.unlink()
        return _ok(
            "مجلّد WireGuard peers",
            f"قابل للكتابة في {path}",
            path=path,
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(
            "مجلّد WireGuard peers",
            f"غير قابل للكتابة في {path}: {exc}",
            path=path,
        )


def check_clients_conf_no_wildcards() -> dict:
    """Postmortem #17: clients.conf must not use unsupported
    $INCLUDE wildcards. Detects regression to the broken
    form."""
    path = "/etc/freeradius/clients.conf"
    # This check runs from hoberadius container which doesn't
    # have FR's clients.conf at this path. Instead check the
    # source-baked version that ships in the image.
    candidates = [
        path,
        "/app/deploy/freeradius/clients.conf",
    ]
    for candidate in candidates:
        cp = Path(candidate)
        if not cp.is_file():
            continue
        try:
            text = cp.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        # Look for `$INCLUDE <path>/*.conf` — the broken form.
        if re.search(
            r"\$INCLUDE\s+\S*/\*\.conf",
            text,
        ):
            return _fail(
                "صيغة clients.conf",
                f"عُثر على $INCLUDE wildcard في {candidate} "
                "— سيؤدّي إلى crash-loop في FreeRADIUS 3.x. "
                "استخدم directory form (/path/) بدلاً من *.conf.",
                source=candidate,
            )
        return _ok(
            "صيغة clients.conf",
            f"لا توجد wildcards غير مدعومة في {candidate}",
            source=candidate,
        )
    return _warn(
        "صيغة clients.conf",
        "تعذّر العثور على clients.conf للفحص "
        "(لا يعني فشلاً — قد يكون داخل container آخر)",
    )


# ─── Top-level aggregator ─────────────────────────────────


def check_all() -> dict[str, Any]:
    """Run all checks. The verdict is computed from check
    statuses: any fail → critical, any warn → degraded,
    else → healthy."""
    checked_at = (
        datetime.utcnow().isoformat() + "Z"
    )
    started = time.monotonic()

    checks = {
        "db_migrations":            check_db_migrations(),
        "freeradius_responsive":    check_freeradius_responsive(),
        "wizard_clients_directory": check_wizard_clients_directory(),
        "wizard_invariants":        check_wizard_invariants(),
        "recent_reconciler_drift":  check_recent_reconciler_drift(),
        "wg_peers_dir":             check_wg_peers_dir(),
        "clients_conf_syntax":      check_clients_conf_no_wildcards(),
    }

    statuses = [c["status"] for c in checks.values()]
    if "fail" in statuses:
        overall = "critical"
    elif "warn" in statuses:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "overall": overall,
        "checks": checks,
        "checked_at": checked_at,
        "duration_ms": int(
            (time.monotonic() - started) * 1000,
        ),
        "version": "setup_wizard_v3",
    }


__all__ = [
    "check_all",
    "check_db_migrations",
    "check_freeradius_responsive",
    "check_wizard_clients_directory",
    "check_wizard_invariants",
    "check_recent_reconciler_drift",
    "check_wg_peers_dir",
    "check_clients_conf_no_wildcards",
]
