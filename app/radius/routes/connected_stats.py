"""connected_stats route — «إحصائيات المتصلين».

صفحة واحدة بثلاثة أنماط (جلسات فريدة / كل الجلسات الناجحة / كل المحاولات
الفاشلة) تُعيد تحديد المؤشّرات + الدونات + مخطّط 24 ساعة، مع فترة تاريخ.
البيانات حقيقيّة من radacct + radpostauth عبر services.connected_stats.
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..services import connected_stats as cs


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_connected_stats_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/connected-stats", "connected_stats",
                    connected_stats_page, methods=["GET"])
    bp.add_url_rule("/connected-stats.json", "connected_stats_json",
                    connected_stats_json, methods=["GET"])


def _read() -> dict:
    return cs.stats(
        _tid(),
        mode=request.args.get("mode"),
        date_from=request.args.get("date_from"),
        date_to=request.args.get("date_to"),
    )


def connected_stats_page():
    data = _read()
    return render_template(
        "radius/connected_stats.html",
        s=data,
        modes=[{"key": k, "label": cs.MODE_LABELS[k]} for k in cs.MODES],
        hourly_max=max(data["hourly"]) if data["hourly"] else 0,
        donut_total=sum(d["count"] for d in data["donut"]) if data["donut"] else 0,
    )


def connected_stats_json():
    """نفس البيانات JSON (للوحات/المنعشات اللاحقة)."""
    return jsonify({"ok": True, **_read()})
