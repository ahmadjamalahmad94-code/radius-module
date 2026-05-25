"""factory رئيسية لـ radius blueprint — يضم كل الأقسام + الـ auth + health/readiness."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

_PUBLIC_ENDPOINTS = {
    "radius.auth_login",
    "radius.auth_logout",
    "radius._radius_health",
    "radius._radius_healthz",
}


def get_radius_blueprint() -> Blueprint:
    bp = Blueprint(
        "radius", __name__,
        url_prefix="/admin/radius",
        template_folder=str(_TEMPLATES_DIR),
    )
    _register_health(bp)
    _register_auth(bp)
    _register_tenants(bp)
    _register_all(bp)
    _install_global_login_guard(bp)
    return bp


def _register_health(bp: Blueprint) -> None:

    @bp.get("/_health")
    def _radius_health():
        """liveness — لا يفحص شيئًا، فقط يردّ أنا حي. للـ docker/k8s liveness."""
        return jsonify({"module": "radius", "status": "ok"})

    @bp.get("/_healthz")
    def _radius_healthz():
        """readiness — يفحص DB + workers. status=503 إن degraded."""
        out = {"status": "ok", "checks": {}}
        try:
            from ..db.connection import db
            db().execute("SELECT 1").fetchone()
            out["checks"]["db"] = "ok"
        except Exception as e:  # noqa: BLE001
            out["checks"]["db"] = f"fail: {e}"
            out["status"] = "degraded"
        try:
            from app.workers.heartbeat import snapshot
            alive = [w for w in snapshot() if w["is_alive"]]
            out["checks"]["workers"] = {
                "alive": len(alive),
                "names": [w["name"] for w in alive],
            }
            if not alive and not _is_worker_disabled():
                out["status"] = "degraded"
        except Exception as e:  # noqa: BLE001
            out["checks"]["workers"] = f"fail: {e}"
        return jsonify(out), 200 if out["status"] == "ok" else 503


def _is_worker_disabled() -> bool:
    import os
    return bool(os.environ.get("HOBERADIUS_NO_WORKER"))


def _register_auth(bp: Blueprint) -> None:
    from .auth import register_auth_routes
    register_auth_routes(bp)


def _register_tenants(bp: Blueprint) -> None:
    from .tenants import register_tenants_routes
    register_tenants_routes(bp)


def _register_all(bp: Blueprint) -> None:
    from .dashboard import register_dashboard_routes
    from .devices import register_devices_routes
    from .sessions import register_sessions_routes
    from .plans import register_plans_routes
    from .users import register_users_routes
    from .subscriber_groups import register_subscriber_groups_routes
    from .cards import register_cards_routes
    from .admins import register_admins_routes
    from .distributors import register_distributors_routes
    from .accounting import register_accounting_routes
    from .finance_center import register_finance_center_routes
    from .card_users_marketplace import register_card_users_marketplace_routes
    from .recycle_bin import register_recycle_bin_routes
    from .backups import register_backup_routes
    from .lifecycle import register_lifecycle_routes
    from .bandwidth_schedules import register_bandwidth_schedule_routes
    from .print_templates import register_print_template_routes
    from .setup_wizard import register_setup_wizard_routes

    register_dashboard_routes(bp)
    register_devices_routes(bp)
    register_sessions_routes(bp)
    register_plans_routes(bp)
    register_users_routes(bp)
    register_subscriber_groups_routes(bp)
    register_cards_routes(bp)
    register_admins_routes(bp)
    register_distributors_routes(bp)
    register_accounting_routes(bp)
    register_finance_center_routes(bp)
    register_card_users_marketplace_routes(bp)
    register_recycle_bin_routes(bp)
    register_backup_routes(bp)
    register_lifecycle_routes(bp)
    register_bandwidth_schedule_routes(bp)
    register_print_template_routes(bp)
    register_setup_wizard_routes(bp)

    from .saas_modules import register_saas_routes
    register_saas_routes(bp)

    from .integrations import register_integration_routes
    register_integration_routes(bp)

    from .mt_dashboard import register_mt_dashboard_routes
    register_mt_dashboard_routes(bp)

    from .mt_programming import register_mt_programming_routes
    register_mt_programming_routes(bp)

    from .mt_login_designer import register_mt_login_designer_routes
    register_mt_login_designer_routes(bp)

    from .jobs import register_jobs_routes
    register_jobs_routes(bp)

    from .audit_log import register_audit_log_routes
    register_audit_log_routes(bp)

    from .mt_topology import register_mt_topology_routes
    register_mt_topology_routes(bp)

    from .mt_alerts import register_mt_alerts_routes
    register_mt_alerts_routes(bp)

    from .mt_backups import register_mt_backups_routes
    register_mt_backups_routes(bp)

    from .mt_router_overview import register_mt_router_overview_routes
    register_mt_router_overview_routes(bp)

    from .mt_problems import register_mt_problems_routes
    register_mt_problems_routes(bp)

    from .mt_audit_timeline import register_mt_audit_timeline_routes
    register_mt_audit_timeline_routes(bp)

    from .mt_recovery_plan import register_mt_recovery_plan_routes
    register_mt_recovery_plan_routes(bp)

    from .mt_setup import register_mt_setup_routes
    register_mt_setup_routes(bp)

    from .mt_permission_matrix import (
        register_mt_permission_matrix_routes,
    )
    register_mt_permission_matrix_routes(bp)

    from .mt_guided_op import register_mt_guided_op_routes
    register_mt_guided_op_routes(bp)

    from .site_exit import register_site_exit_routes
    register_site_exit_routes(bp)

    from .network_policy import register_network_policy_routes
    register_network_policy_routes(bp)

    from .tokens import register_tokens_routes
    register_tokens_routes(bp)

    from .status import register_status_routes
    register_status_routes(bp)

    from .reports import register_reports_routes
    register_reports_routes(bp)

    from .tools import register_tools_routes
    register_tools_routes(bp)

    from .settings import register_settings_routes
    register_settings_routes(bp)

    from .overviews import register_overview_routes
    register_overview_routes(bp)

    from .share_groups import register_share_groups_routes
    register_share_groups_routes(bp)


def _install_global_login_guard(bp: Blueprint) -> None:
    """يحرس كل الـ endpoints الإدارية بـ login، عدا public."""
    from ..auth.session_helpers import current_admin_id

    @bp.before_request
    def _guard():
        # السماح بكل الـ public + كل ما يخص الـ static
        ep = request.endpoint or ""
        if ep in _PUBLIC_ENDPOINTS:
            return None
        # حمى: غير مسجَّل = إعادة توجيه لـ login
        if not current_admin_id():
            from flask import redirect, url_for, flash
            flash("سجّل الدخول للمتابعة.", "warning")
            return redirect(url_for("radius.auth_login", next=request.path))
        return None


__all__ = ["get_radius_blueprint"]
