"""Setup Wizard API for native clients.

The web wizard already owns the operational state machine.  This module
exposes a small, token-protected JSON surface for mobile/desktop clients:
read health, read WireGuard readiness, list recent runs, create a run, and
poll a run.  It does not execute router scripts or apply gateway changes.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g

from ...radius.db.connection import db
from ...radius.services.setup_wizard_server_wg_readiness import (
    ServerWireGuardReadinessService,
)
from ...radius.services.setup_wizard_v3 import (
    V3Error,
    V3NotFound,
    WizardV3Service,
)
from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/setup-wizard/overview",
        "setup_wizard_overview",
        require_api_token(setup_wizard_overview),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/health",
        "setup_wizard_health",
        require_api_token(setup_wizard_health),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/server-readiness",
        "setup_wizard_server_readiness",
        require_api_token(setup_wizard_server_readiness),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs",
        "setup_wizard_runs_create",
        require_api_token(setup_wizard_runs_create),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/state",
        "setup_wizard_runs_state",
        require_api_token(setup_wizard_runs_state),
        methods=["GET"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    token_id = getattr(g, "api_token_id", None)
    return f"api-token:{token_id or 'env'}"


def _svc() -> WizardV3Service:
    return WizardV3Service()


def _health_report() -> dict[str, Any]:
    from ...radius.services.setup_wizard_system_health import check_all

    return check_all()


def _server_readiness() -> dict[str, Any]:
    return ServerWireGuardReadinessService().evaluate()


def _recent_runs(*, tenant_id: int, limit: int = 8) -> list[dict[str, Any]]:
    rows = (
        db()
        .execute(
            """
            SELECT id
            FROM setup_wizard_runs
            WHERE tenant_id=? AND v3_state IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(tenant_id), int(limit)),
        )
        .fetchall()
    )
    runs: list[dict[str, Any]] = []
    service = _svc()
    for row in rows:
        try:
            runs.append(
                service.get_state(
                    tenant_id=tenant_id,
                    run_id=int(row["id"]),
                ).to_dict()
            )
        except V3NotFound:
            continue
    return runs


def _run_summary(runs: list[dict[str, Any]]) -> dict[str, Any]:
    by_state: dict[str, int] = {}
    active = 0
    for run in runs:
        state = str(run.get("state") or "")
        by_state[state] = by_state.get(state, 0) + 1
        if not run.get("is_terminal"):
            active += 1
    return {
        "recent_count": len(runs),
        "active_count": active,
        "by_state": by_state,
    }


def setup_wizard_overview():
    try:
        tenant_id = _tid()
        health = _health_report()
        readiness = _server_readiness()
        runs = _recent_runs(tenant_id=tenant_id)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_overview_failed",
            f"تعذر قراءة حالة معالج الإعداد: {exc}",
            status=500,
        )
    return ok(
        {
            "health": health,
            "server_readiness": readiness,
            "recent_runs": runs,
            "runs_summary": _run_summary(runs),
            "safe_operations": {
                "can_create_run": True,
                "can_apply_router_changes": False,
                "can_apply_server_peer": False,
                "reason_ar": "هذا الـ API مخصص للقراءة وبدء التشغيل فقط. تطبيق إعدادات الراوتر يبقى من شاشة الويب المحمية.",
            },
        }
    )


def setup_wizard_health():
    try:
        return ok({"health": _health_report()})
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_health_failed",
            f"تعذر فحص صحة معالج الإعداد: {exc}",
            status=500,
        )


def setup_wizard_server_readiness():
    try:
        return ok({"server_readiness": _server_readiness()})
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_readiness_failed",
            f"تعذر فحص جاهزية الخادم: {exc}",
            status=500,
        )


def setup_wizard_runs_create():
    try:
        run = _svc().start_new_run(tenant_id=_tid(), actor=_actor())
    except V3Error as exc:
        return fail("setup_wizard_run_failed", str(exc), status=409)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_run_failed",
            f"تعذر إنشاء تشغيل جديد للمعالج: {exc}",
            status=500,
        )
    return ok({"run": run.to_dict()}, status=201)


def setup_wizard_runs_state(run_id: int):
    try:
        run = _svc().get_state(tenant_id=_tid(), run_id=run_id)
    except V3NotFound:
        return fail(
            "not_found",
            "تشغيل معالج الإعداد غير موجود.",
            status=404,
        )
    except V3Error as exc:
        return fail("setup_wizard_state_failed", str(exc), status=409)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_state_failed",
            f"تعذر قراءة حالة تشغيل المعالج: {exc}",
            status=500,
        )
    return ok({"run": run.to_dict()})
