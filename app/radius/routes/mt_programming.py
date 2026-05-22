"""Q1 — Network programming wizard (read-only generator).

Routes:
  GET  /admin/radius/mt/<id>/program        — show the form.
  POST /admin/radius/mt/<id>/program/plan   — render the plan.

Apply is intentionally *not* a route in Q1 — the apply path
ships in Q2 behind a confirmation modal + audit log. The form
view here therefore renders the plan inside the same page with
the apply button disabled and a hint pointing to Q2.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, abort, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..services import mt_programming
from ..services import mikrotik_admin_client as mac
from ..services.audit import get_audit_service
from ..integration.mikrotik.client import MikrotikClient


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _load_nas(nas_id: int) -> dict | None:
    row = db().execute(
        "SELECT id, name, address, api_port, api_user, api_password, "
        "       api_use_tls, enabled, connection_mode, "
        "       vpn_peer_address "
        "FROM nas_devices "
        "WHERE id=? AND tenant_id=? "
        "  AND (deleted_at IS NULL OR deleted_at='')",
        (nas_id, _tid()),
    ).fetchone()
    return dict(row) if row else None


def _nas_for_mac(nas: dict) -> dict:
    """Translate a nas_devices row into the dict shape the admin
    client expects (mt_diagnostics uses the same pattern)."""
    return {
        "id":          nas["id"],
        "name":        nas["name"],
        "host":        nas["address"],
        "port":        int(nas.get("api_port") or 8728),
        "username":    nas.get("api_user") or "admin",
        "password":    nas.get("api_password") or "",
        "use_tls":     bool(nas.get("api_use_tls")),
        "verify_tls":  True,
        "timeout_sec": 10,
    }


def register_mt_programming_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/mt/<int:nas_id>/program",
        "mt_program_form",
        mt_program_form,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/program/plan",
        "mt_program_plan",
        mt_program_plan,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/program/apply",
        "mt_program_apply",
        mt_program_apply,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/mt/<int:nas_id>/program/unprogram",
        "mt_program_unprogram",
        mt_program_unprogram,
        methods=["POST"],
    )


def _fetch_router_state(nas: dict) -> tuple[list[dict], list[dict]]:
    """Pull the router's current interfaces + IP addresses so the
    planner can surface conflicts. Failure is non-fatal — the plan
    still generates, the conflict block just won't show specifics.
    """
    nas_call = _nas_for_mac(nas)
    iface_res = mac.interface_list(nas_call)
    addr_res  = mac.ip_addresses(nas_call)
    return (
        list(iface_res.data) if iface_res.ok else [],
        list(addr_res.data)  if addr_res.ok else [],
    )


def mt_program_form(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    kind = (request.args.get("kind") or "hotspot").strip().lower()
    if kind not in {"hotspot", "pppoe"}:
        kind = "hotspot"
    return render_template(
        "radius/mt_programming.html",
        nas=nas,
        plan=None,
        form={"kind": kind},
        kind=kind,
    )


def _read_form() -> dict:
    """All possible programming form fields. Each route picks the
    subset it needs; unused ones stay around so the template can
    re-render them after a validation error."""
    return {
        "kind":         (request.form.get("kind") or "hotspot").strip(),
        "interface":    (request.form.get("interface")    or "").strip(),
        "cidr":         (request.form.get("cidr")         or "").strip(),
        "hotspot_name": (request.form.get("hotspot_name") or "").strip(),
        "dns_servers":  (request.form.get("dns_servers")
                         or "8.8.8.8,1.1.1.1").strip(),
        "pool_start":   (request.form.get("pool_start")   or "").strip(),
        "pool_end":     (request.form.get("pool_end")     or "").strip(),
        "gateway":      (request.form.get("gateway")      or "").strip(),
        "lease_time":   (request.form.get("lease_time")   or "1h").strip(),
        "rate_limit":   (request.form.get("rate_limit")   or "").strip(),
        "profile_name": (request.form.get("profile_name") or "").strip(),
        "service_name": (request.form.get("service_name") or "").strip(),
        "local_address":(request.form.get("local_address") or "").strip(),
    }


def _plan_from_form(nas: dict, form: dict) -> tuple[Any, str]:
    """Dispatch on form['kind'] and return (plan_or_None, error_str)."""
    kind = form.get("kind") or "hotspot"
    error = ""
    plan = None
    try:
        ifaces, addrs = _fetch_router_state(nas)
        if kind == "pppoe":
            spec = mt_programming.PppoeProgrammingSpec(
                interface=form["interface"], cidr=form["cidr"],
                profile_name=form["profile_name"],
                service_name=form["service_name"],
                pool_start=form["pool_start"],
                pool_end=form["pool_end"],
                local_address=form["local_address"],
                dns_servers=form["dns_servers"],
            )
            plan = mt_programming.plan_pppoe(
                nas, spec,
                existing_interfaces=ifaces,
                existing_addresses=addrs,
            )
        else:
            spec = mt_programming.HotspotProgrammingSpec(
                interface=form["interface"], cidr=form["cidr"],
                hotspot_name=form["hotspot_name"],
                dns_servers=form["dns_servers"],
                pool_start=form["pool_start"],
                pool_end=form["pool_end"],
                gateway=form["gateway"],
                lease_time=form["lease_time"],
                rate_limit=form["rate_limit"],
            )
            plan = mt_programming.plan_hotspot(
                nas, spec,
                existing_interfaces=ifaces,
                existing_addresses=addrs,
            )
    except ValueError as e:
        error = str(e)
    return plan, error


def mt_program_plan(nas_id: int):
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    form = _read_form()
    plan, error = _plan_from_form(nas, form)
    return render_template(
        "radius/mt_programming.html",
        nas=nas,
        plan=plan,
        form=form,
        kind=form["kind"],
        error=error,
    )


# ─── Q2 — Apply ────────────────────────────────────────────────


def _connect_client(nas: dict):
    """Open a live MikrotikClient against a nas_devices row. The
    Q1 read path lets the admin-client wrapper handle pools +
    caching; for apply we want a raw session so we can stream
    one command at a time and stop on the first failure."""
    return MikrotikClient(
        host=nas["address"],
        port=int(nas.get("api_port") or 8728),
        username=nas.get("api_user") or "admin",
        password=nas.get("api_password") or "",
        use_tls=bool(nas.get("api_use_tls")),
        verify_tls=True,
        timeout=15.0,
    )


def mt_program_apply(nas_id: int):
    """Q2 — Apply a previously-generated plan.

    The form posts the same spec fields as the plan view *plus* a
    `confirm=1` checkbox. The flow is:

      1. Re-load + re-validate the spec (never trust the client).
      2. Re-run `plan_hotspot` against current router state.
      3. Refuse if `confirm != "1"` (CSRF middleware already
         covers the cross-site case; this guards against an
         operator mis-clicking the disabled button via devtools).
      4. Refuse if `plan.risks` is non-empty — risks are stop-the-
         line by design; an operator must rework the spec.
      5. Open the wire client, run each command, audit the
         outcome.

    The response always re-renders the same template, with an
    `apply_result` block added so the operator sees per-step
    success/skip/fail right after clicking apply.
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    form = _read_form()
    confirmed = request.form.get("confirm") == "1"
    plan, error = _plan_from_form(nas, form)
    apply_result = None

    if plan is not None:
        if not confirmed:
            error = "يجب تأكيد العملية قبل التطبيق."
        elif plan.risks:
            error = ("لا يمكن التطبيق وعندنا مخاطر غير معالجة — "
                     "صحّح المدخلات ثم أعد الفحص.")
        else:
            client = _connect_client(nas)
            try:
                client.connect()
                apply_result = mt_programming.apply_commands(
                    client, plan.commands)
            except Exception as e:  # noqa: BLE001
                error = "تعذّر الاتصال بالراوتر: " + str(e)
            finally:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

            actor = str(getattr(g, "admin_id", None) or "ui")
            # S2.3 — enrich audit with severity / router_id /
            # result_status so the S2.2 UI can filter.
            ok_flag = bool(apply_result and apply_result.ok)
            if not apply_result:
                _result = "failed"
                _sev = "critical"
            elif apply_result.ok:
                _result = "success"
                _sev = "info"
            else:
                _result = "failed"
                _sev = "warning"
            get_audit_service().record(
                actor=actor,
                action=f"mt.programming.{plan.kind}.apply",
                target_type="mikrotik_nas",
                target_id=str(nas_id),
                severity=_sev,
                result_status=_result,
                router_id=int(nas_id),
                error_message=(apply_result.error
                               if apply_result else error),
                payload={
                    "kind": plan.kind,
                    "interface": form["interface"],
                    "cidr": form["cidr"],
                    "name": (form.get("hotspot_name")
                             or form.get("profile_name") or ""),
                    "ok": ok_flag,
                    "summary": (apply_result.summary()
                                if apply_result else None),
                    "error": (apply_result.error if apply_result else error),
                },
            )

    return render_template(
        "radius/mt_programming.html",
        nas=nas, plan=plan, form=form,
        kind=form["kind"],
        error=error,
        apply_result=apply_result,
    )


def mt_program_unprogram(nas_id: int):
    """Q4 — Remove every hoberadius:<kind> object from the router.

    Destructive: this issues `/remove` against every matching row.
    Confirmation is required. We re-validate `kind` against the
    fixed allowlist before dispatching so a manipulated POST can't
    sneak in a third arm.
    """
    nas = _load_nas(nas_id)
    if not nas:
        abort(404)
    kind = (request.form.get("kind") or "").strip().lower()
    confirmed = request.form.get("confirm") == "1"
    error: str = ""
    unprogram_result = None

    if kind not in {"hotspot", "pppoe"}:
        error = "نوع البرمجة غير معروف."
    elif not confirmed:
        error = "يجب تأكيد عملية الإزالة قبل تنفيذها."
    else:
        client = _connect_client(nas)
        try:
            client.connect()
            unprogram_result = mt_programming.unprogram(client, kind)
        except Exception as e:  # noqa: BLE001
            error = "تعذّر الاتصال بالراوتر: " + str(e)
        finally:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        actor = str(getattr(g, "admin_id", None) or "ui")
        # S2.3 — unprogram is always destructive, so even a
        # successful run is `warning` severity (operator must
        # see it in the filter).
        if not unprogram_result:
            _result = "failed"
            _sev = "critical"
        elif unprogram_result.ok:
            _result = "success"
            _sev = "warning"
        else:
            _result = "failed"
            _sev = "critical"
        get_audit_service().record(
            actor=actor,
            action=f"mt.programming.{kind}.unprogram",
            target_type="mikrotik_nas",
            target_id=str(nas_id),
            severity=_sev,
            result_status=_result,
            router_id=int(nas_id),
            error_message=(unprogram_result.error
                           if unprogram_result else error),
            payload={
                "kind": kind,
                "ok": bool(unprogram_result and unprogram_result.ok),
                "summary": (unprogram_result.summary()
                            if unprogram_result else None),
                "error": (unprogram_result.error
                          if unprogram_result else error),
            },
        )

    return render_template(
        "radius/mt_programming.html",
        nas=nas, plan=None, form={"kind": kind or "hotspot"},
        kind=kind or "hotspot",
        error=error,
        unprogram_result=unprogram_result,
    )
