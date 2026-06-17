"""mt-programming — v1 JSON API (feat/api-first-endpoints).

Mirrors the MikroTik network-programming wizard
(`routes/mt_programming.py`, `/admin/radius/mt/<id>/program*`): generate a
plan from a hotspot/pppoe spec against the router's live state, apply it
(with safety check + confirm + per-command report + audit), and unprogram
(remove every hoberadius:<kind> object). Reuses the `mt_programming` service
+ `mikrotik_admin_client` + safety/preview/audit services — no duplicated
logic.

Admin-authed via require_api_token (the web equivalent is gated by
PERM_PROGRAM for plan/apply and PERM_ROLLBACK for unprogram). Tenant-scoped.
Destructive endpoints require `confirm: true` in the body.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from flask import Blueprint, g, request

from ...radius.db.connection import db
from ...radius.integration.mikrotik.client import MikrotikClient
from ...radius.services import mikrotik_admin_client as mac
from ...radius.services import mt_programming as prog
from ...radius.services.audit import get_audit_service
from ...radius.services.nas_connection import resolve_connection_address
from ..auth import require_api_token
from ..responses import fail, ok


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def _actor() -> str:
    return str(getattr(g, "admin_id", None) or getattr(g, "admin_name", "") or "api")


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik/<int:nas_id>/program", "mt_program_get",
                    require_api_token(program_get), methods=["GET"])
    bp.add_url_rule("/mikrotik/<int:nas_id>/program/plan", "mt_program_plan",
                    require_api_token(program_plan), methods=["POST"])
    bp.add_url_rule("/mikrotik/<int:nas_id>/program/apply", "mt_program_apply",
                    require_api_token(program_apply), methods=["POST"])
    bp.add_url_rule("/mikrotik/<int:nas_id>/program/unprogram",
                    "mt_program_unprogram",
                    require_api_token(program_unprogram), methods=["POST"])


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, api_port, api_user, api_password, "
        "       api_use_tls, enabled, connection_mode, vpn_peer_address "
        "FROM nas_devices WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (int(nas_id), _tid()),
    ).fetchone()
    return dict(row) if row else None


def _nas_for_mac(nas: dict) -> dict:
    return {
        "id": nas["id"], "name": nas["name"],
        "host": resolve_connection_address(nas),
        "port": int(nas.get("api_port") or 8728),
        "username": nas.get("api_user") or "admin",
        "password": nas.get("api_password") or "",
        "use_tls": bool(nas.get("api_use_tls")),
        "verify_tls": True, "timeout_sec": 10,
    }


def _fetch_router_state(nas: dict) -> tuple[list, list, list]:
    nc = _nas_for_mac(nas)
    i, a, r = mac.interface_list(nc), mac.ip_addresses(nc), mac.ip_routes(nc)
    return (list(i.data) if i.ok else [], list(a.data) if a.ok else [],
            list(r.data) if r.ok else [])


def _connect_client(nas: dict) -> MikrotikClient:
    return MikrotikClient(
        host=resolve_connection_address(nas),
        port=int(nas.get("api_port") or 8728),
        username=nas.get("api_user") or "admin",
        password=nas.get("api_password") or "",
        use_tls=bool(nas.get("api_use_tls")), verify_tls=True, timeout=15.0)


# نفس مفاتيح _read_form في الويب + الافتراضات.
_FORM_DEFAULTS = {
    "kind": "hotspot", "interface": "", "cidr": "", "hotspot_name": "",
    "dns_servers": "8.8.8.8,1.1.1.1", "pool_start": "", "pool_end": "",
    "gateway": "", "lease_time": "1h", "rate_limit": "", "profile_name": "",
    "service_name": "", "local_address": "",
}


def _read_form(body: dict) -> dict:
    out = {}
    for k, default in _FORM_DEFAULTS.items():
        v = body.get(k, default)
        out[k] = str(v).strip() if isinstance(v, str) else (v if v is not None else default)
    out["kind"] = (out.get("kind") or "hotspot").strip().lower()
    return out


def _plan_from_form(nas: dict, form: dict):
    """نفس منطق _plan_from_form في الويب → (plan|None, error)."""
    kind = form.get("kind") or "hotspot"
    try:
        ifaces, addrs, routes = _fetch_router_state(nas)
        if kind == "pppoe":
            spec = prog.PppoeProgrammingSpec(
                interface=form["interface"], cidr=form["cidr"],
                profile_name=form["profile_name"], service_name=form["service_name"],
                pool_start=form["pool_start"], pool_end=form["pool_end"],
                local_address=form["local_address"], dns_servers=form["dns_servers"])
            return prog.plan_pppoe(nas, spec, existing_interfaces=ifaces,
                                   existing_addresses=addrs, existing_routes=routes), ""
        spec = prog.HotspotProgrammingSpec(
            interface=form["interface"], cidr=form["cidr"],
            hotspot_name=form["hotspot_name"], dns_servers=form["dns_servers"],
            pool_start=form["pool_start"], pool_end=form["pool_end"],
            gateway=form["gateway"], lease_time=form["lease_time"],
            rate_limit=form["rate_limit"])
        return prog.plan_hotspot(nas, spec, existing_interfaces=ifaces,
                                 existing_addresses=addrs, existing_routes=routes), ""
    except ValueError as e:
        return None, str(e)


def _plan_dict(plan) -> dict:
    return {
        "kind": plan.kind, "script": plan.script,
        "summary": list(plan.summary), "warnings": list(plan.warnings),
        "risks": list(plan.risks),
        "commands": [{"path": c.path, "attrs": dict(c.attrs)} for c in plan.commands],
    }


def _result_dict(res) -> dict:
    """ApplyResult/UnprogramResult → JSON (مع steps)."""
    out: dict[str, Any] = {"ok": bool(res.ok), "error": res.error or "",
                           "summary": res.summary()}
    if hasattr(res, "result_status"):
        out["result_status"] = res.result_status()
    if hasattr(res, "skipped_paths"):
        out["skipped_paths"] = list(res.skipped_paths)
    out["steps"] = [dataclasses.asdict(s) for s in getattr(res, "steps", [])]
    return out


# ───────────────────────── النقاط ─────────────────────────

def program_get(nas_id: int):
    """GET /mikrotik/<id>/program?kind= — مخطّط النموذج + حالة الراوتر الحيّة
    (الواجهات/العناوين) لبناء النموذج. حالة الراوتر أفضل-جهد (فارغة إن تعذّر)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    kind = (request.args.get("kind") or "hotspot").strip().lower()
    if kind not in ("hotspot", "pppoe"):
        kind = "hotspot"
    ifaces, addrs, routes = _fetch_router_state(nas)
    return ok({
        "nas": {"id": nas["id"], "name": nas["name"], "address": nas.get("address")},
        "kind": kind,
        "form_fields": _FORM_DEFAULTS,
        "router_state": {"interfaces": ifaces, "addresses": addrs, "routes": routes},
    })


def program_plan(nas_id: int):
    """POST /mikrotik/<id>/program/plan — توليد الخطّة من المواصفة + معاينة
    التغيير + تحذير النسخ الاحتياطي (يطابق mt_program_plan)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    form = _read_form(request.get_json(silent=True) or {})
    plan, error = _plan_from_form(nas, form)
    if plan is None:
        return fail("validation_error", error or "تعذّر توليد الخطّة.", status=422)

    change_preview = None
    backup_warning_ar = ""
    try:
        ifaces, addrs, _r = _fetch_router_state(nas)
        from ...radius.services import mt_change_preview as cp
        from ...radius.services.mt_router_overview import build_overview
        ov = build_overview(tenant_id=_tid(), nas_id=int(nas_id))
        cpv = cp.preview_plan(plan, snapshot_status=(ov.snapshot_status if ov else "unknown"),
                              existing_interfaces=ifaces, existing_addresses=addrs)
        change_preview = cpv.to_dict() if hasattr(cpv, "to_dict") else None
        if ov and ov.backup_status == "missing":
            backup_warning_ar = ("لا توجد نسخة احتياطية لهذا الراوتر. إن فشل "
                                 "التطبيق لن تستطيع الاستعادة. يُنصح بأخذ نسخة قبل المتابعة.")
        elif ov and ov.backup_status == "stale":
            backup_warning_ar = "آخر نسخة احتياطية قديمة — يُستحسن تحديثها قبل أي تعديل."
    except Exception:  # noqa: BLE001 — المعاينة ثانوية، لا تكسر الخطّة
        pass

    return ok({"plan": _plan_dict(plan), "change_preview": change_preview,
               "backup_warning_ar": backup_warning_ar})


def program_apply(nas_id: int):
    """POST /mikrotik/<id>/program/apply — تطبيق الخطّة (confirm مطلوب). يعيد
    تحقّق المواصفة + فحص السلامة + بوّابة المخاطر ثم ينفّذ ويدوّن (يطابق الويب)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    form = _read_form(body)
    confirmed = bool(body.get("confirm"))
    plan, error = _plan_from_form(nas, form)
    if plan is None:
        return fail("validation_error", error or "تعذّر توليد الخطّة.", status=422)

    # فحص السلامة (advisory + blocking) — نفس مسار الويب، محصّن.
    safety = None
    try:
        from ...radius.services import mt_safety_check as sc
        from ...radius.services.mt_permissions import _current_admin
        safety = sc.evaluate(tenant_id=_tid(), nas_id=int(nas_id),
                             admin=_current_admin(),
                             operation=f"mt.programming.{plan.kind}.apply",
                             override_admin=bool(body.get("override_admin")))
        if not safety.allowed:
            return fail("safety_blocked",
                        "؛ ".join(safety.blocking_reasons) or "محظور بفحص السلامة.",
                        status=409, details={"safety": safety.to_dict(),
                                             "plan": _plan_dict(plan)})
    except Exception:  # noqa: BLE001
        safety = None

    if not confirmed:
        return fail("confirm_required", "يجب تأكيد العملية قبل التطبيق (confirm).",
                    status=400, details={"plan": _plan_dict(plan)})
    if plan.risks:
        return fail("has_risks",
                    "لا يمكن التطبيق وعندنا مخاطر غير معالجة — صحّح المدخلات.",
                    status=422, details={"plan": _plan_dict(plan)})

    apply_result = None
    client = _connect_client(nas)
    try:
        client.connect()
        apply_result = prog.apply_commands(client, plan.commands)
    except Exception as e:  # noqa: BLE001
        error = "تعذّر الاتصال بالراوتر: " + str(e)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    # تدوين — نفس منطق الويب (شدّة/حالة حسب نجاح/جزئي/فشل).
    if not apply_result:
        _res, _sev = "failed", "critical"
    elif apply_result.ok:
        _res, _sev = "success", "info"
    else:
        _res = apply_result.result_status()
        _sev = "warning" if _res == "partial" else "critical"
    try:
        get_audit_service().record(
            actor=_actor(), action=f"mt.programming.{plan.kind}.apply",
            target_type="mikrotik_nas", target_id=str(nas_id), severity=_sev,
            result_status=_res, router_id=int(nas_id),
            error_message=(apply_result.error if apply_result else error),
            payload={"kind": plan.kind, "interface": form["interface"],
                     "cidr": form["cidr"], "via": "api",
                     "ok": bool(apply_result and apply_result.ok),
                     "summary": (apply_result.summary() if apply_result else None),
                     "safety": (safety.to_dict() if safety else None)})
    except Exception:  # noqa: BLE001
        pass

    if not apply_result:
        return fail("router_error", error or "فشل التطبيق.", status=502,
                    details={"plan": _plan_dict(plan),
                             "safety": safety.to_dict() if safety else None})
    return ok({"plan": _plan_dict(plan), "apply_result": _result_dict(apply_result),
               "safety": safety.to_dict() if safety else None})


def program_unprogram(nas_id: int):
    """POST /mikrotik/<id>/program/unprogram — إزالة كل كائنات hoberadius:<kind>.
    مدمّر؛ يتطلّب confirm + kind صالح (يطابق mt_program_unprogram)."""
    nas = _load_nas(nas_id)
    if not nas:
        return fail("not_found", "الراوتر غير موجود", status=404)
    body = request.get_json(silent=True) or {}
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in ("hotspot", "pppoe"):
        return fail("validation_error", "نوع البرمجة غير معروف.", status=422)
    if not bool(body.get("confirm")):
        return fail("confirm_required", "يجب تأكيد عملية الإزالة (confirm).", status=400)

    result = None
    error = ""
    client = _connect_client(nas)
    try:
        client.connect()
        result = prog.unprogram(client, kind)
    except Exception as e:  # noqa: BLE001
        error = "تعذّر الاتصال بالراوتر: " + str(e)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    if not result:
        _res, _sev = "failed", "critical"
    elif result.ok:
        _res, _sev = "success", "warning"  # إزالة دائمًا warning على الأقل
    else:
        _res, _sev = "failed", "critical"
    try:
        get_audit_service().record(
            actor=_actor(), action=f"mt.programming.{kind}.unprogram",
            target_type="mikrotik_nas", target_id=str(nas_id), severity=_sev,
            result_status=_res, router_id=int(nas_id),
            error_message=(result.error if result else error),
            payload={"kind": kind, "via": "api",
                     "ok": bool(result and result.ok),
                     "summary": (result.summary() if result else None)})
    except Exception:  # noqa: BLE001
        pass

    if not result:
        return fail("router_error", error or "فشلت الإزالة.", status=502)
    return ok({"unprogram_result": _result_dict(result)})
