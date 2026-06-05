"""Setup Wizard API for native clients.

The web wizard owns the operational state machine. This module exposes the
same safe planning surface to mobile/desktop clients: health, readiness, run
state, phase planner catalogue, per-phase script preview, and diagnostics.
Actual router apply operations stay behind their explicit guarded routes.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g, request

from ...radius.db.connection import db
from ...radius.services.setup_wizard_added_services_phase_planner import (
    AddedServicesPhasePlanner,
)
from ...radius.services.setup_wizard_broadband_phase_planner import (
    BroadbandPhasePlanner,
)
from ...radius.services.setup_wizard_hotspot_phase_planner import (
    HotspotPhasePlanner,
)
from ...radius.services.setup_wizard_internet_phase_planner import (
    InternetPhasePlanner,
)
from ...radius.services.setup_wizard_server_wg_readiness import (
    ServerWireGuardReadinessService,
)
from ...radius.services.setup_wizard_v3 import (
    V3Error,
    V3NotFound,
    WizardV3Service,
)
from ...radius.services.setup_wizard_vpn_radius_phase_planner import (
    VpnRadiusPhasePlanner,
)
from ..auth import require_api_token
from ..responses import fail, ok

_PHASE_PLANNERS = {
    "internet": InternetPhasePlanner,
    "vpn_radius": VpnRadiusPhasePlanner,
    "hotspot": HotspotPhasePlanner,
    "broadband": BroadbandPhasePlanner,
    "added_services": AddedServicesPhasePlanner,
}


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
    bp.add_url_rule(
        "/setup-wizard/phase-planners",
        "setup_wizard_phase_planners",
        require_api_token(setup_wizard_phase_planners),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/phase-plan/<phase>",
        "setup_wizard_phase_plan",
        require_api_token(setup_wizard_phase_plan),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/diagnostics-catalogue",
        "setup_wizard_diagnostics_catalogue",
        require_api_token(setup_wizard_diagnostics_catalogue),
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


def _body() -> dict[str, Any]:
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return request.form.to_dict()


def _phase_catalogue() -> list[dict[str, Any]]:
    return [
        {
            "phase": "internet",
            "title_ar": "وصلة الإنترنت",
            "description_ar": "تجهيز المنفذ الخارج سواء كان تلقائيًا أو ثابتًا أو عبر PPPoE.",
            "required_inputs": ["source_type"],
        },
        {
            "phase": "vpn_radius",
            "title_ar": "الربط الآمن وخدمة الريدياس",
            "description_ar": "تجهيز النفق الآمن وربط الراوتر بخادم الريدياس.",
            "required_inputs": [
                "router_vpn_ip",
                "vps_vpn_ip",
                "vps_public_endpoint",
                "radius_secret",
                "server_public_key",
            ],
        },
        {
            "phase": "hotspot",
            "title_ar": "بوابة الدخول",
            "description_ar": "تجهيز بوابة الدخول مع توزيع العناوين والمصادقة عبر الريدياس.",
            "required_inputs": [
                "selected_interfaces",
                "subnet_base",
                "radius_secret",
                "router_vpn_ip",
            ],
        },
        {
            "phase": "broadband",
            "title_ar": "اشتراكات PPPoE",
            "description_ar": "تجهيز اشتراكات PPPoE مع مدى عناوين وتوجيه مقيّد.",
            "required_inputs": [
                "selected_interfaces",
                "local_address",
                "remote_pool_cidr",
            ],
        },
        {
            "phase": "added_services",
            "title_ar": "خدمات إضافية",
            "description_ar": "مواقع مفتوحة، حجب مواقع، أو تغيير عنوان الخروج العام.",
            "required_inputs": ["service_key"],
        },
    ]


def _diagnostics_for_codes(codes: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
    from ...radius.services import setup_wizard_diagnostics as diagnostics

    result: list[dict[str, Any]] = []
    for code in codes:
        try:
            item = diagnostics.get(str(code))
        except KeyError:
            result.append(
                {
                    "code": str(code),
                    "ar_explanation": "خطأ تشخيصي غير معروف.",
                    "severity": "error",
                }
            )
            continue
        result.append(
            {
                "code": item.code,
                "phase": item.phase,
                "ar_explanation": item.ar_explanation,
                "cause": item.cause,
                "fix": item.fix,
                "severity": item.severity,
                "inspect_command": item.inspect_command,
            }
        )
    return result


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
                "can_plan_phases": True,
                "reason_ar": "التطبيق يستطيع قراءة الحالة، بدء تشغيل جديد، وتوليد خطط المراحل والسكربتات. تطبيق الأوامر على الراوتر يبقى من مسارات التطبيق المحمية لاحقًا.",
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


def setup_wizard_phase_planners():
    return ok({"phases": _phase_catalogue()})


def setup_wizard_phase_plan(run_id: int, phase: str):
    phase_key = str(phase or "").strip().lower()
    planner_cls = _PHASE_PLANNERS.get(phase_key)
    if planner_cls is None:
        return fail(
            "unknown_phase",
            "مرحلة المعالج غير معروفة.",
            status=400,
            details={"phase": phase_key, "allowed": sorted(_PHASE_PLANNERS)},
        )
    try:
        run = _svc().get_state(tenant_id=_tid(), run_id=run_id)
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3Error as exc:
        return fail("setup_wizard_state_failed", str(exc), status=409)

    body = _body()
    raw_inputs = body.get("inputs")
    inputs = dict(raw_inputs) if isinstance(raw_inputs, dict) else dict(body)
    if isinstance(raw_inputs, dict):
        for key, value in body.items():
            if key != "inputs":
                inputs.setdefault(key, value)
    try:
        plan = planner_cls().plan(run_id=int(run.id), inputs=inputs)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "planner_failed",
            f"تعذر توليد خطة المرحلة: {exc}",
            status=500,
        )

    plan_payload = plan.to_dict()
    return ok(
        {
            "phase": phase_key,
            "run_id": int(run.id),
            "plan": plan_payload,
            "diagnostics": _diagnostics_for_codes(
                tuple(plan_payload.get("blocking_errors") or ())
            ),
        }
    )


def setup_wizard_diagnostics_catalogue():
    from ...radius.services import setup_wizard_diagnostics as diagnostics

    catalogue = []
    for code in diagnostics.all_codes():
        item = diagnostics.get(code)
        catalogue.append(
            {
                "code": item.code,
                "phase": item.phase,
                "ar_explanation": item.ar_explanation,
                "cause": item.cause,
                "fix": item.fix,
                "severity": item.severity,
                "inspect_command": item.inspect_command,
            }
        )
    return ok({"catalogue": catalogue})
