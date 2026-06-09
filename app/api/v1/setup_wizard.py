"""Setup Wizard API for native clients.

The web wizard owns the operational state machine. This module exposes the
same safe planning surface to mobile/desktop clients: health, readiness, run
state, phase planner catalogue, per-phase script preview, and diagnostics.
Actual router apply operations stay behind their explicit guarded routes.
"""
from __future__ import annotations

import os
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
    V3InvalidState,
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
        "/setup-wizard/runs/<int:run_id>/router-info",
        "setup_wizard_router_info",
        require_api_token(setup_wizard_router_info),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/generate-script",
        "setup_wizard_generate_script",
        require_api_token(setup_wizard_generate_script),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/submit-key",
        "setup_wizard_submit_key",
        require_api_token(setup_wizard_submit_key),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/apply-server-peer",
        "setup_wizard_apply_server_peer",
        require_api_token(setup_wizard_apply_server_peer),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/mark-handshake",
        "setup_wizard_mark_handshake",
        require_api_token(setup_wizard_mark_handshake),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/setup-wizard/runs/<int:run_id>/register",
        "setup_wizard_register_router",
        require_api_token(setup_wizard_register_router),
        methods=["POST"],
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
    bp.add_url_rule(
        "/setup-wizard/router-services/catalogue",
        "setup_wizard_router_services_catalogue",
        require_api_token(setup_wizard_router_services_catalogue),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/setup-wizard/routers/<int:router_id>/services/status",
        "setup_wizard_router_services_status",
        require_api_token(setup_wizard_router_services_status),
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


_ROUTER_SERVICE_TITLE_OVERRIDES = {
    "hotspot": "بوابة الدخول",
    "broadband": "اشتراكات PPPoE",
    "block-sites": "حجب المواقع",
    "open-sites": "المواقع المفتوحة",
    "public-ip": "تغيير عنوان الخروج",
    "remote-access": "الدخول الفني الآمن",
}

_ROUTER_SERVICE_STATUS_AR = {
    "active": "مفعّلة",
    "inactive": "غير مفعّلة",
    "unknown": "غير معروف",
}


def _router_service_cards() -> list[dict[str, Any]]:
    from ...radius.routes.setup_wizard_v3 import ROUTER_SERVICE_CARDS

    cards: list[dict[str, Any]] = []
    for item in ROUTER_SERVICE_CARDS:
        key = str(item.get("key") or "")
        cards.append(
            {
                "key": key,
                "title_ar": _ROUTER_SERVICE_TITLE_OVERRIDES.get(
                    key, str(item.get("title_ar") or key)
                ),
                "subtitle_ar": str(item.get("subtitle_ar") or ""),
                "icon": str(item.get("icon") or ""),
                "color": str(item.get("color") or ""),
                "phases_count": int(item.get("phases_count") or 0),
            }
        )
    return cards


def _service_status(value: Any) -> str:
    if value is True:
        return "active"
    if value is False:
        return "inactive"
    return "unknown"


def _router_services_status_items(raw: Any) -> list[dict[str, Any]]:
    values = raw if isinstance(raw, dict) else {}
    cards = _router_service_cards()
    ordered = [card["key"] for card in cards]
    for key in values:
        text_key = str(key)
        if text_key not in ordered:
            ordered.append(text_key)

    items: list[dict[str, Any]] = []
    title_by_key = {card["key"]: card["title_ar"] for card in cards}
    for key in ordered:
        value = values.get(key)
        status = _service_status(value)
        items.append(
            {
                "key": key,
                "title_ar": title_by_key.get(key, key),
                "enabled": value if value in (True, False) else None,
                "status": status,
                "status_ar": _ROUTER_SERVICE_STATUS_AR[status],
            }
        )
    return items


def _json_response_payload(response: Any) -> tuple[dict[str, Any], int]:
    status = 200
    payload_source = response
    if isinstance(response, tuple):
        payload_source = response[0]
        if len(response) > 1:
            try:
                status = int(response[1])
            except (TypeError, ValueError):
                status = 200
    if hasattr(payload_source, "status_code"):
        status = int(getattr(payload_source, "status_code", status) or status)
    if hasattr(payload_source, "get_json"):
        data = payload_source.get_json(silent=True) or {}
    else:
        data = payload_source if isinstance(payload_source, dict) else {}
    return (data if isinstance(data, dict) else {}, status)


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


def _visible_v3_error(exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    if "router_name is required" in lowered:
        return "اسم الراوتر مطلوب."
    if "router_name must be 64 chars or fewer" in lowered:
        return "اسم الراوتر يجب أن يكون 64 حرفًا أو أقل."
    if "router_type must be" in lowered:
        return "نوع الراوتر يجب أن يكون بوابة دخول أو اشتراكات أو مختلط."
    if "cannot" in lowered and "from state" in lowered:
        return "حالة تشغيل المعالج لا تسمح بهذه الخطوة الآن."
    return text if any("\u0600" <= ch <= "\u06ff" for ch in text) else "تعذر تنفيذ خطوة معالج الإعداد."


def _fail_v3(exc: Exception, *, status: int = 409, code: str = "setup_wizard_step_failed"):
    return fail(code, _visible_v3_error(exc), status=status)


def _int_body(name: str, default: int) -> int:
    raw = _body().get(name)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def _public_script_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "run": result.get("run") or {},
        "script": result.get("script") or "",
        "short_code": result.get("short_code") or "",
        "sha256": result.get("sha256") or "",
        "expires_at": result.get("expires_at") or "",
        "server_radius_provisioning": result.get("server_radius_provisioning") or {},
        "script_contains_sensitive_values": True,
        "warning_ar": "هذا السكربت يحتوي أسرار تشغيلية داخلية. انسخه للراوتر فقط ولا ترسله لأي جهة غير موثوقة.",
    }


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
                "can_apply_server_peer": True,
                "can_plan_phases": True,
                "can_run_lifecycle": True,
                "reason_ar": "التطبيق يستطيع قراءة الحالة، بدء تشغيل جديد، توليد خطط المراحل، وإكمال خطوات تشغيل الخادم والتسجيل. إرسال أوامر مباشرة للراوتر يبقى محميًا بخطوات معاينة واضحة.",
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


def setup_wizard_router_info(run_id: int):
    body = _body()
    try:
        run = _svc().submit_router_info(
            tenant_id=_tid(),
            run_id=run_id,
            router_name=str(body.get("router_name") or ""),
            router_type=str(body.get("router_type") or "hotspot"),
        )
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3InvalidState as exc:
        return _fail_v3(exc, code="invalid_state")
    except V3Error as exc:
        return _fail_v3(exc)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_router_info_failed",
            f"تعذر حفظ بيانات الراوتر: {exc}",
            status=500,
        )
    return ok({"run": run.to_dict()})


def setup_wizard_generate_script(run_id: int):
    body = _body()
    endpoint = (
        body.get("vps_public_endpoint")
        or os.environ.get("HOBERADIUS_PUBLIC_HOST")
        or os.environ.get("HOBERADIUS_WG_SERVER_ENDPOINT", "").split(":")[0]
        or request.host.split(":")[0]
    )
    pubkey = body.get("vps_wg_pubkey") or os.environ.get("HOBERADIUS_WG_SERVER_PUBKEY") or ""
    if not str(endpoint).strip():
        return fail(
            "missing_endpoint",
            "عنوان الخادم العام غير مضبوط.",
            status=400,
        )
    if not str(pubkey).strip():
        return fail(
            "missing_server_pubkey",
            "مفتاح WireGuard العام للخادم غير مضبوط.",
            status=400,
        )
    try:
        result = _svc().generate_unified_script(
            tenant_id=_tid(),
            run_id=run_id,
            vps_public_endpoint=str(endpoint).strip(),
            vps_wg_pubkey=str(pubkey).strip(),
            wg_listen_port=_int_body("wg_listen_port", 13231),
            vps_endpoint_port=_int_body("vps_endpoint_port", 51820),
        )
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3InvalidState as exc:
        return _fail_v3(exc, code="invalid_state")
    except V3Error as exc:
        return _fail_v3(exc)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_generate_script_failed",
            f"تعذر توليد سكربت الربط: {exc}",
            status=500,
        )
    return ok(_public_script_result(result))


def setup_wizard_submit_key(run_id: int):
    body = _body()
    pasted = str(body.get("pasted_output") or body.get("public_key") or "")
    try:
        run = _svc().submit_router_public_key(
            tenant_id=_tid(),
            run_id=run_id,
            pasted_or_key=pasted,
        )
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3InvalidState as exc:
        return _fail_v3(exc, code="invalid_state")
    except V3Error as exc:
        return _fail_v3(exc)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_submit_key_failed",
            f"تعذر حفظ مفتاح الراوتر العام: {exc}",
            status=500,
        )
    return ok({"run": run.to_dict()})


def setup_wizard_apply_server_peer(run_id: int):
    try:
        run = _svc().apply_server_peer(tenant_id=_tid(), run_id=run_id)
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3InvalidState as exc:
        return _fail_v3(exc, code="invalid_state")
    except V3Error as exc:
        return _fail_v3(exc)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_apply_peer_failed",
            f"تعذر تطبيق peer على الخادم: {exc}",
            status=500,
        )
    return ok({"run": run.to_dict()})


def setup_wizard_mark_handshake(run_id: int):
    try:
        run = _svc().mark_handshake_observed(tenant_id=_tid(), run_id=run_id)
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3InvalidState as exc:
        return _fail_v3(exc, code="invalid_state")
    except V3Error as exc:
        return _fail_v3(exc)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_mark_handshake_failed",
            f"تعذر تأكيد اتصال الراوتر: {exc}",
            status=500,
        )
    return ok({"run": run.to_dict()})


def setup_wizard_register_router(run_id: int):
    body = _body()
    try:
        run = _svc().register_router_in_inventory(
            tenant_id=_tid(),
            run_id=run_id,
            api_user=str(body.get("api_user") or "admin"),
            api_password=str(body.get("api_password") or ""),
        )
    except V3NotFound:
        return fail("not_found", "تشغيل معالج الإعداد غير موجود.", status=404)
    except V3InvalidState as exc:
        return _fail_v3(exc, code="invalid_state")
    except V3Error as exc:
        return _fail_v3(exc)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "setup_wizard_register_failed",
            f"تعذر تسجيل الراوتر في النظام: {exc}",
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


def setup_wizard_router_services_catalogue():
    return ok({"services": _router_service_cards()})


def setup_wizard_router_services_status(router_id: int):
    try:
        from ...radius.routes import setup_wizard_v3 as web_wizard

        web_response = web_wizard.setup_wizard_v3_router_services_status(router_id)
        payload, status_code = _json_response_payload(web_response)
    except Exception as exc:  # noqa: BLE001
        return fail(
            "router_services_status_failed",
            f"تعذّرت قراءة حالة خدمات الراوتر: {exc}",
            status=500,
        )

    services = _router_services_status_items(payload.get("services"))
    if payload.get("ok") is True:
        return ok({"router_id": int(router_id), "services": services})

    message = str(payload.get("error") or "تعذّرت قراءة حالة خدمات الراوتر.")
    if not any("\u0600" <= ch <= "\u06ff" for ch in message):
        message = "تعذّرت قراءة حالة خدمات الراوتر."
    return fail(
        str(payload.get("code") or "router_services_status_failed"),
        message,
        status=status_code if status_code >= 400 else 502,
        details={"router_id": int(router_id), "services": services},
    )
