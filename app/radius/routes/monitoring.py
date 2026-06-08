"""لوحة المتابعة والصحة — عرض مقاييس الخدمات وتنبيهات السعة.

مُسجَّل على blueprint الرئيسي (radius) ضمن _register_all.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, session


def register_monitoring_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/monitoring", "monitoring_dashboard", monitoring_dashboard, methods=["GET"])
    bp.add_url_rule("/monitoring/api/stats", "monitoring_api_stats", monitoring_api_stats, methods=["GET"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def monitoring_dashboard():
    from ..services.monitoring_service import get_dashboard_stats

    stats = get_dashboard_stats(_tid())
    return render_template("radius/monitoring.html", stats=stats)


def monitoring_api_stats():
    """JSON endpoint للتحديث التلقائي (polling كل 60 ثانية)."""
    from ..services.monitoring_service import get_dashboard_stats

    stats = get_dashboard_stats(_tid())
    # نُعيد نسخة مُبسَّطة مناسبة للـ JSON (لا كائنات SQLite Row)
    return jsonify({
        "overall_health": stats["overall_health"],
        "totals": stats["totals"],
        "health_checks": stats["health_checks"],
        "generated_at": stats["generated_at"],
        "vpn_allocations": [
            {
                "id": a["id"],
                "service_type": a["service_type"],
                "chr_node_name": a.get("chr_node_name", ""),
                "status": a["status"],
                "active_accounts": a["active_accounts"],
                "max_accounts": a.get("max_accounts", 0),
                "capacity_pct": a["capacity_pct"],
                "health_status": a["health_status"],
            }
            for a in stats["vpn_allocations"]
        ],
        "wg_service": {
            "interface_name": stats["wg_service"]["interface_name"],
            "active_peers": stats["wg_service"]["active_peers"],
            "max_peers": stats["wg_service"].get("max_peers", 0),
            "capacity_pct": stats["wg_service"]["capacity_pct"],
            "health_status": stats["wg_service"]["health_status"],
            "quota_bytes_used": stats["wg_service"].get("quota_bytes_used", 0),
        } if stats.get("wg_service") else None,
    })
