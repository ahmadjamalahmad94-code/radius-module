"""S6.2 — Smart alerts center.

Routes:
  GET  /admin/radius/alerts                list (filterable)
  GET  /admin/radius/alerts/<id>           detail
  POST /admin/radius/alerts/settings       save toggles + per-router thresholds
  GET  /admin/radius/alerts/agent-setup    router metrics-push agent script

Opening the list runs a cheap DB-only refresh: an offline heartbeat sweep
(smart_alerts.sweep_offline) plus the problems→alerts bridge, so the page
always reflects current state without a background worker.
"""
from __future__ import annotations

from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, url_for,
)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import alerts_repo
from ..services.mt_permissions import (
    PERM_DIAGNOSTICS, requires_perm,
)


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_mt_alerts_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/alerts", "mt_alerts_index",
        requires_perm(PERM_DIAGNOSTICS)(mt_alerts_index),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/alerts/settings", "mt_alerts_settings_save",
        requires_perm(PERM_DIAGNOSTICS)(mt_alerts_settings_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/alerts/agent-setup", "mt_metrics_setup",
        requires_perm(PERM_DIAGNOSTICS)(mt_metrics_setup),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/alerts/loop-setup", "mt_loop_setup",
        requires_perm(PERM_DIAGNOSTICS)(mt_loop_setup),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/alerts/resource-thresholds", "resource_alerts_settings",
        requires_perm(PERM_DIAGNOSTICS)(resource_alerts_settings),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/alerts/resource-thresholds", "resource_alerts_save",
        requires_perm(PERM_DIAGNOSTICS)(resource_alerts_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/alerts/periodic-notifications", "monitoring_periodic_save",
        requires_perm(PERM_DIAGNOSTICS)(monitoring_periodic_save),
        methods=["POST"],
    )
    bp.add_url_rule(
        "/alerts/<int:alert_id>", "mt_alerts_detail",
        requires_perm(PERM_DIAGNOSTICS)(mt_alerts_detail),
        methods=["GET"],
    )


def _routers_with_thresholds(tid: int) -> list[dict]:
    """Every router + its effective alert thresholds (per-router over global)."""
    from ..db.repos import nas_repo, router_alert_settings_repo
    from ..services import smart_alerts

    glob = smart_alerts.global_settings(tid)
    per_router = router_alert_settings_repo.list_for_tenant(tid)
    try:
        devices = nas_repo.list_nas(tid, limit=1000)
    except Exception:  # noqa: BLE001
        devices = []
    out: list[dict] = []
    for d in devices:
        rid = int(getattr(d, "id"))
        eff = smart_alerts.effective_for_router(rid, glob, per_router)
        out.append({
            "id": rid,
            "name": getattr(d, "name", None) or f"#{rid}",
            "enabled": eff["enabled"],
            "offline_after_min": eff["offline_after_min"],
            "normal_speed_mbps": eff["normal_speed_mbps"],
            "normal_usage_gb": eff["normal_usage_gb"],
            "usage_window": eff["usage_window"],
        })
    return out


def mt_alerts_index():
    from ..services import mt_alerts_generator, smart_alerts

    tid = _tid()
    # Cheap, DB-only refresh so the page reflects reality on every open:
    #  1) heartbeat sweep → offline alerts from routers that stopped pushing
    #  2) bridge existing problems (snapshot/backup/audit) into the alerts table
    try:
        smart_alerts.sweep_offline(tid)
    except Exception:  # noqa: BLE001
        pass
    try:
        smart_alerts.evaluate_all(tid)   # high-traffic / high-usage breaches
    except Exception:  # noqa: BLE001
        pass
    try:
        mt_alerts_generator.refresh_alerts_from_problems(tid)
    except Exception:  # noqa: BLE001
        pass

    status = (request.args.get("status") or "open").strip().lower()
    if status not in {"open", "resolved"}:
        status = "open"
    severity = (request.args.get("severity") or "").strip() or None
    raw_router = (request.args.get("router_id") or "").strip()
    try:
        router_id = int(raw_router) if raw_router else None
    except (TypeError, ValueError):
        router_id = None

    if status == "open":
        rows = alerts_repo.list_open(tid, router_id=router_id, severity=severity)
    else:
        rows = alerts_repo.list_resolved(tid, router_id=router_id)

    # خريطة {معرّف الراوتر → اسمه} ليعرض عمود «الراوتر» اسمًا حقيقيًّا
    # بدل «#رقم» خام في الجدول. صفوف list_nas كائنات (وصول بالسمة).
    from ..db.repos import nas_repo
    try:
        _devs = nas_repo.list_nas(tid, limit=1000)
    except Exception:  # noqa: BLE001
        _devs = []
    router_names = {
        int(getattr(d, "id")): (getattr(d, "name", None) or "")
        for d in _devs
    }

    return render_template(
        "radius/mt_alerts_index.html",
        rows=rows,
        filters={"status": status, "severity": severity, "router_id": router_id},
        severities=["info", "warning", "critical"],
        settings=smart_alerts.global_settings(tid),
        routers=_routers_with_thresholds(tid),
        router_names=router_names,
    )


def mt_alerts_settings_save():
    """Persist global toggles + per-router thresholds from the settings modal."""
    from ..db.repos import router_alert_settings_repo
    from ..services import smart_alerts

    tid = _tid()
    form = request.form

    def _checkbox(name: str) -> bool:
        return form.get(name) in {"1", "on", "true", "yes"}

    def _opt_int(name: str):
        raw = (form.get(name) or "").strip()
        if not raw:
            return None
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            return None

    smart_alerts.save_global_settings(tid, {
        "enabled": _checkbox("enabled"),
        "telegram": _checkbox("telegram"),
        "offline": _checkbox("offline"),
        "high_traffic": _checkbox("high_traffic"),
        "high_usage": _checkbox("high_usage"),
        "offline_after_min": _opt_int("offline_after_min") or 6,
        "default_speed_mbps": _opt_int("default_speed_mbps") or 100,
        "default_usage_gb": _opt_int("default_usage_gb") or 200,
        "usage_window": (form.get("usage_window") or "day").strip(),
    })

    # Per-router rows: present only for routers the operator actually edited.
    for key in form.keys():
        if not key.startswith("r_") or not key.endswith("_present"):
            continue
        try:
            rid = int(key[2:-len("_present")])
        except (TypeError, ValueError):
            continue
        router_alert_settings_repo.upsert(
            tenant_id=tid, router_id=rid,
            enabled=_checkbox(f"r_{rid}_enabled"),
            offline_after_min=_opt_int(f"r_{rid}_offline"),
            normal_speed_mbps=_opt_int(f"r_{rid}_speed"),
            normal_usage_gb=_opt_int(f"r_{rid}_usage"),
            usage_window=(form.get(f"r_{rid}_window") or "").strip() or None,
        )

    flash("تم حفظ إعدادات التنبيهات الذكية.", "success")
    return redirect(url_for("radius.mt_alerts_index"))


def mt_metrics_setup():
    """Router metrics-push agent: generates a /system scheduler script the
    operator pastes once into the router (clone of «دفع DHCP», for metrics)."""
    from ..db.repos import api_tokens_repo

    tid = _tid()
    tokens = [t for t in api_tokens_repo.list_tokens(tid) if not t.get("revoked")]
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    forwarded_host = request.headers.get("X-Forwarded-Host", "")
    proto = forwarded_proto or ("https" if request.is_secure else "http")
    host = forwarded_host or request.host
    return render_template(
        "radius/mt_metrics_setup.html",
        base_url=f"{proto}://{host}",
        tokens=tokens,
        suggested_token_name=(tokens[0]["name"] if tokens else ""),
        routers=_routers_with_thresholds(tid),
    )


def mt_loop_setup():
    """Loop-tracking «تتبّع اللوب» — صفحة حالة + إرشاد.

    التركيب صار عبر «خدمات المنافذ» (loop_detect: عميل DHCP موسوم
    HR-LoopDetect لكل منفذ مختار)، والكشف صار باستطلاع هادئ من جهة اللوحة
    كل ٥ دقائق (worker: loop_probe_poller) يقرأ /ip dhcp-client عبر النفق
    الإداري — لا scheduler/fetch على الراوتر. هذه الصفحة تعرض آخر حالة لكل
    منفذ + روابط التركيب/الإزالة + أوامر تنظيف بقايا الآلية القديمة."""
    from ..db.repos import router_loop_probes_repo

    tid = _tid()

    # Group probe readings by router for the status panel.
    by_router: dict[int, list[dict]] = {}
    for p in router_loop_probes_repo.list_for_tenant(tid):
        by_router.setdefault(int(p["router_id"]), []).append(p)

    routers = _routers_with_thresholds(tid)
    for r in routers:
        r["probes"] = by_router.get(r["id"], [])

    return render_template(
        "radius/mt_loop_setup.html",
        routers=routers,
    )


# تسميات عربية مفهومة لقواعد التنبيه الآلية (auto.<type>)
_RULE_LABELS_AR = {
    "auto.router.disabled":  "الراوتر معطَّل",
    "auto.snapshot.stale":   "اللقطة التشغيلية قديمة",
    "auto.snapshot.failed":  "فشل أخذ اللقطة التشغيلية",
    "auto.backup.missing":   "لا توجد نسخة احتياطية",
    "auto.backup.stale":     "النسخة الاحتياطية قديمة",
    "auto.alert.critical":   "إنذار حرج من الراوتر",
    "auto.alert.warning":    "تحذير من الراوتر",
    "auto.audit.failure":    "فشل عملية حديثة على الراوتر",
    "auto.audit.partial":    "تطبيق جزئي لعملية على الراوتر",
}


def mt_alerts_detail(alert_id: int):
    row = alerts_repo.get_by_id(_tid(), int(alert_id))
    if not row:
        abort(404)
    # اسم الراوتر بدل "#id" حتى يعرف المشغّل أي جهاز مقصود
    router_name = ""
    if row.get("router_id"):
        try:
            from ..db.repos import nas_repo
            device = nas_repo.get_nas(_tid(), int(row["router_id"]))
            if device:
                router_name = device.name or device.shortname or ""
        except Exception:  # noqa: BLE001 — الاسم تحسين عرض، لا يكسر الصفحة
            router_name = ""
    rule_label = _RULE_LABELS_AR.get((row.get("rule") or "").strip(), "")
    return render_template(
        "radius/mt_alerts_detail.html",
        alert=row,
        router_name=router_name,
        rule_label=rule_label,
    )


# ── عتبات تنبيهات موارد الراوتر (CPU/حرارة/ذاكرة/قرص/حركة) ──
# واجهة ضبط من اللوحة فقط (قاعدة المالك: لا تيرمنال، كل شيء من الواجهة).

def resource_alerts_settings():
    from ..services import router_resource_monitor
    from ..services import monitoring_digest
    return render_template(
        "radius/resource_alerts.html",
        thresholds=router_resource_monitor.get_thresholds(_tid()),
        periodic=monitoring_digest.get_periodic_config(_tid()),
    )


def resource_alerts_save():
    from ..services import router_resource_monitor
    f = request.form
    values = {
        "enabled": f.get("enabled") in ("1", "on", "true", "yes"),
        "cpu_pct": f.get("cpu_pct"),
        "temp_c": f.get("temp_c"),
        "ram_pct": f.get("ram_pct"),
        "disk_free_pct": f.get("disk_free_pct"),
        "traffic_mbps": f.get("traffic_mbps"),
    }
    router_resource_monitor.set_thresholds(_tid(), values)
    flash("حُفظت حدود تنبيهات الموارد.", "success")
    return redirect(url_for("radius.resource_alerts_settings"))


def monitoring_periodic_save():
    """يحفظ إعدادات الإشعارات الدوريّة (تذكير المفصول + تقرير الأسطول)."""
    from ..services import monitoring_digest
    f = request.form
    monitoring_digest.set_periodic_config(_tid(), {
        "reminder_enabled": f.get("reminder_enabled") in ("1", "on", "true", "yes"),
        "reminder_minutes": f.get("reminder_minutes"),
        "digest_enabled": f.get("digest_enabled") in ("1", "on", "true", "yes"),
        "digest_minutes": f.get("digest_minutes"),
    })
    flash("حُفظت إعدادات الإشعارات الدوريّة.", "success")
    return redirect(url_for("radius.resource_alerts_settings"))
