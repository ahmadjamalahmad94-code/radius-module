"""
routes للحالة التشغيلية: /admin/radius/_status + sync queue inspector.

/admin/radius/_status   ← HTML + JSON (Accept: application/json) — يعرض workers, queues, MT health
/admin/radius/sync      ← قائمة sync_queue + إعادة محاولة / إلغاء

ملاحظة: سجل العمليات (/admin/radius/audit) يخدمه audit_log.py — لا تُسجَّل
نسخة ثانية هنا حتى لا يتصادم مساران على نفس العنوان.
"""
from __future__ import annotations

import json
import time

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import mikrotik_repo, sync_queue_repo, webhooks_repo
from ..services import system_probe


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_status_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/_status", "system_status", system_status, methods=["GET"])
    bp.add_url_rule("/sync", "sync_list", sync_list, methods=["GET"])
    bp.add_url_rule("/sync/<int:job_id>/retry", "sync_retry", sync_retry, methods=["POST"])
    bp.add_url_rule("/sync/<int:job_id>/cancel", "sync_cancel", sync_cancel, methods=["POST"])
    bp.add_url_rule("/_reconcile_now", "reconcile_now",
                    reconcile_now, methods=["POST", "GET"])
    bp.add_url_rule("/diagnostics", "diagnostics",
                    diagnostics, methods=["GET"])
    bp.add_url_rule("/mt-push-setup", "mt_push_setup",
                    mt_push_setup, methods=["GET"])


_PAYLOAD_KEY_LABELS = {
    "router_id": "الراوتر",
    "job_id": "المهمة",
    "status": "الحالة",
    "result": "النتيجة",
    "error": "الخطأ",
    "message": "الرسالة",
    "count": "العدد",
    "changed": "العناصر المتغيرة",
    "username": "اسم الدخول",
    "plan_id": "الباقة",
    "amount": "المبلغ",
    "currency": "العملة",
    "reference": "المرجع",
    "reference_code": "رمز المرجع",
    "interval_sec": "فترة التشغيل",
    "threshold_sec": "حد الإغلاق",
    "last_processed": "آخر عناصر تمت معالجتها",
    "last_reaped": "آخر جلسات مغلقة",
    "last_scanned": "آخر عناصر مفحوصة",
    "last_reclaimed": "آخر عناصر مسترجعة",
    "last_closed": "آخر جلسات مغلقة",
    "last_routers_ok": "راوترات متصلة",
    "last_routers_skipped": "راوترات متجاوزة",
    "ok": "النتيجة",
    "license_active": "الترخيص مفعل",
    "capacity_snapshot_id": "لقطة الحدود",
    "identity_ok": "مزامنة الهوية",
    "identity_synced_count": "حسابات متزامنة",
    "last_sessions_seen": "آخر جلسات مقروءة",
}


def _payload_summary(raw: object) -> str:
    try:
        payload = json.loads(raw or "{}") if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict) or not payload:
        return "لا توجد تفاصيل إضافية."

    parts: list[str] = []
    for key, value in list(payload.items())[:4]:
        label = _PAYLOAD_KEY_LABELS.get(str(key), str(key).replace("_", " "))
        if isinstance(value, bool):
            rendered = "نعم" if value else "لا"
        elif value is None or value == "":
            rendered = "—"
        elif isinstance(value, (dict, list, tuple)):
            rendered = "مجموعة بيانات"
        else:
            rendered = str(value)
        parts.append(f"{label}: {rendered}")
    if len(payload) > 4:
        parts.append(f"{len(payload) - 4} حقل إضافي")
    return "، ".join(parts)


def _worker_info_summary(raw: object) -> str:
    summary = _payload_summary(raw)
    if summary == "لا توجد تفاصيل إضافية.":
        return ""
    return summary


def _workers_for_html(workers: list[dict]) -> list[dict]:
    decorated: list[dict] = []
    for worker in workers:
        item = dict(worker)
        item["info_summary"] = _worker_info_summary(item.get("info"))
        decorated.append(item)
    return decorated


def mt_push_setup():
    """Generates a MikroTik scheduler script the operator pastes into
    Winbox/terminal so the router pushes DHCP leases to us via HTTPS.

    Used when the standard PULL mode (VPS → MT API on 8728) is blocked
    by firewall/NAT — outbound HTTPS from the router almost always
    works, so this path is the universal fallback.
    """
    from ..db.repos import api_tokens_repo

    # Best-effort: pick the first non-revoked API token for this tenant
    # to suggest. Operator can override on the page if they have many.
    tid = _tid()
    tokens = [t for t in api_tokens_repo.list_tokens(tid) if not t.get("revoked")]
    suggested_token_name = tokens[0]["name"] if tokens else ""

    # The VPS public URL — best-effort detection from request headers.
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    forwarded_host  = request.headers.get("X-Forwarded-Host", "")
    proto = forwarded_proto or ("https" if request.is_secure else "http")
    host  = forwarded_host  or request.host
    base_url = f"{proto}://{host}"

    return render_template(
        "radius/mt_push_setup.html",
        base_url=base_url,
        tokens=tokens,
        suggested_token_name=suggested_token_name,
    )


def diagnostics():
    """Per-router health-check page. Runs TCP probe + API login test
    against every configured router and renders verdicts + fix hints +
    copyable MT commands.

    O5 — the repair-script block in the template branches on each
    router's connection_mode: 'vpn' rows get a WG-subnet rule
    (so locking the API to the public VPS IP doesn't break the
    tunnel-side reach), 'direct' rows get the public-IP rule.
    Both subnet + public IP are passed to the template so it can
    pick the right one without re-reading env.
    """
    import os
    from ..services import mt_diagnostics
    report = mt_diagnostics.diagnose_tenant(_tid())
    vps_ip = request.headers.get("X-Real-IP") or \
             request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or \
             request.remote_addr or "YOUR_VPS_IP"
    # WG subnet for the VPN-mode repair branch. Falls back to the
    # documented default if the env var isn't set yet.
    wg_subnet = (
        os.environ.get("HOBERADIUS_WG_SUBNET") or "10.10.0.0/24"
    ).strip()
    return render_template(
        "radius/mt_diagnostics.html",
        report=report,
        vps_ip=vps_ip,
        wg_subnet=wg_subnet,
    )


def reconcile_now():
    """On-demand: run the MT-reconciler once across this tenant.

    Useful after a router reboot when the operator wants to flush
    ghost sessions immediately instead of waiting for the next 30s
    background tick. Returns JSON so it can be hit from the UI or
    curl/cron. Safe to spam — the reconciler is idempotent.
    """
    from app.workers import mt_reconciler
    try:
        stats = mt_reconciler.reconcile_once()
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True, "stats": stats})


# ─────────────── status ───────────────

def _gather_status(tenant_id: int) -> dict:
    from app.workers.heartbeat import snapshot
    workers = snapshot()
    sync_stats = sync_queue_repo.stats(tenant_id)
    mt_configs = mikrotik_repo.list_configs(tenant_id)
    enabled_mt = [c for c in mt_configs if c["enabled"]]
    vps = system_probe.get_vps_status()

    # webhook deliveries summary
    cur = db().execute("""
        SELECT status, COUNT(*) AS c FROM webhook_deliveries WHERE tenant_id = ? GROUP BY status
    """, (tenant_id,))
    wh = {"queued": 0, "retrying": 0, "delivered": 0, "failed": 0}
    for r in cur.fetchall():
        wh[r["status"]] = r["c"]

    # rowcounts (سريعة)
    counts = {}
    for t in ("subscribers", "access_plans", "cards", "card_batches",
              "vouchers", "invoices", "tickets", "radacct"):
        try:
            counts[t] = db().execute(f"SELECT COUNT(*) AS c FROM {t} WHERE tenant_id = ?",
                                       (tenant_id,)).fetchone()["c"]
        except Exception:
            counts[t] = -1

    return {
        "tenant_id": tenant_id,
        "workers": workers,
        "sync_queue": sync_stats,
        "webhook_deliveries": wh,
        "mt_routers": {
            "total": len(mt_configs),
            "enabled": len(enabled_mt),
            "items": [{"id": c["id"], "name": c["name"], "host": c["host"],
                       "last_status": (c["last_status"] or "")[:80],
                       "last_seen_at": c["last_seen_at"], "enabled": bool(c["enabled"])}
                      for c in mt_configs],
        },
        "counts": counts,
        "vps": vps,
        "system": {
            "hostname": vps.get("hostname"),
            "platform": vps.get("platform"),
            "process_uptime": vps.get("process_uptime"),
            "system_uptime": vps.get("system_uptime"),
            "cpu_pct": vps.get("cpu_pct"),
            "ram_pct": (vps.get("memory") or {}).get("percent"),
            "disk_pct": (vps.get("disk") or {}).get("percent"),
            "load": vps.get("load"),
            "network": vps.get("network"),
        },
        "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def system_status():
    data = _gather_status(_tid())
    # JSON إذا طُلب
    if "application/json" in (request.headers.get("Accept") or "") \
       or request.args.get("format") == "json":
        return jsonify(data)
    html_data = dict(data)
    html_data["workers"] = _workers_for_html(list(data.get("workers") or []))
    return render_template("radius/_status.html", s=html_data)


# ─────────────── sync queue ───────────────

def sync_list():
    status = request.args.get("status") or None
    jobs = sync_queue_repo.list_jobs(_tid(), status=status, limit=300)

    # ── سياق الراوترات لعمود «الراوتر المستهدف» ──
    # تصميم الطابور بثّي (broadcast): المهمة تُنفَّذ على كل راوترات المستأجر
    # المفعّلة دفعة واحدة (router_sync.execute_job)، وعمود router_id مجرد
    # تلميح توجيه نادر الاستخدام، وlast_router_id يسجّل آخر راوتر فشل عليه
    # التنفيذ. نمرّر خريطة {id: اسم} من جدول nas_devices (المصدر القانوني
    # لأسماء الراوترات في الواجهة) لعرض الاسم بدل الرقم الخام عند توفّره،
    # وعدد الراوترات المفعّلة لعرض «كل الراوترات المفعّلة (N)».
    # قراءة دفاعية: أي فشل هنا لا يمنع عرض الطابور نفسه.
    router_names: dict = {}
    routers_enabled = 0
    try:
        from ..db.repos import nas_repo
        for nas in nas_repo.list_nas(_tid(), limit=500):
            router_names[nas.id] = nas.name or nas.address or f"#{nas.id}"
            if nas.enabled:
                routers_enabled += 1
    except Exception:  # noqa: BLE001 — العمود يتدهور بأمان إلى الأرقام الخام
        pass

    return render_template("radius/sync_list.html", jobs=jobs, status=status,
                            stats=sync_queue_repo.stats(_tid()),
                            router_names=router_names,
                            routers_enabled=routers_enabled)


def sync_retry(job_id: int):
    """يعيد job إلى queued + يصفّر next_attempt_at."""
    from ..db.connection import transaction
    from ..db.helpers import now_iso
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE sync_queue SET status='queued', next_attempt_at=?, last_error='' "
            "WHERE tenant_id = ? AND id = ?",
            (now_iso(), _tid(), job_id)
        )
        if cur.rowcount == 0:
            abort(404)
    flash("أُعيدت المحاولة فورًا.", "success")
    return redirect(request.referrer or url_for("radius.sync_list"))


def sync_cancel(job_id: int):
    from ..db.connection import transaction
    from ..db.helpers import now_iso
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE sync_queue SET status='failed', last_error='canceled by admin' "
            "WHERE tenant_id = ? AND id = ? AND status IN ('queued','retrying')",
            (_tid(), job_id)
        )
        if cur.rowcount == 0:
            abort(404)
    flash("تم إلغاء المهمة.", "warning")
    return redirect(request.referrer or url_for("radius.sync_list"))
