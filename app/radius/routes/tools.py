"""
Tools — أدوات تشغيلية:
- set_speeds: تعديل سرعات الخطط بشكل جماعي.
- maintenance: تنظيف DB (radacct قديم، sync_queue منتهية).
- general_adjustments: عمليات جماعية على المشتركين (extend/disable/enable).
- test_auth: محاكاة Access-Request لاختبار policy engine.
- radius_log: عرض حيّ لـ radpostauth (آخر القرارات).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, session, url_for

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db, transaction
from ..db.helpers import now_iso
from ..db.repos import plans_repo, subscribers_repo


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def register_tools_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/tools/set_speeds", "tool_set_speeds",
                    tool_set_speeds, methods=["GET", "POST"])
    bp.add_url_rule("/tools/maintenance", "tool_maintenance",
                    tool_maintenance, methods=["GET", "POST"])
    bp.add_url_rule("/tools/general_adjustments", "tool_general_adj",
                    tool_general_adj, methods=["GET", "POST"])
    bp.add_url_rule("/tools/test_auth", "tool_test_auth",
                    tool_test_auth, methods=["GET", "POST"])
    bp.add_url_rule("/tools/radius_log", "tool_radius_log",
                    tool_radius_log, methods=["GET"])
    bp.add_url_rule("/tools/radius_log.json", "tool_radius_log_json",
                    tool_radius_log_json, methods=["GET"])


# ─────────────── 1. set_speeds ───────────────

def tool_set_speeds():
    if request.method == "POST":
        from dataclasses import replace
        plan_ids = request.form.getlist("plan_ids")
        # Same validation as /api/v1/tools/set-speeds (finite, positive,
        # bounded — a huge multiplier used to overflow SQLite → HTML 500).
        from ..core.errors import RadiusValidationError
        from ..services.bulk_speeds import new_speed, parse_bulk_speed_params
        try:
            mult_down, mult_up, set_down, set_up = parse_bulk_speed_params(
                request.form.get("mult_down"), request.form.get("mult_up"),
                request.form.get("set_down"), request.form.get("set_up"))
        except RadiusValidationError as e:
            flash(e.message, "error")
            return redirect(url_for("radius.tool_set_speeds"))
        if not plan_ids:
            flash("اختر خطة واحدة على الأقل", "error")
            return redirect(url_for("radius.tool_set_speeds"))
        pending = []
        for pid in plan_ids:
            try: pid = int(pid)
            except ValueError: continue
            p = plans_repo.get_plan(_tid(), pid)
            if not p: continue
            try:
                new_down = new_speed(p.speed_down_kbps, mult_down, set_down,
                                     plan_name=p.name, direction="التنزيل")
                new_up = new_speed(p.speed_up_kbps, mult_up, set_up,
                                   plan_name=p.name, direction="الرفع")
            except RadiusValidationError as e:
                flash(e.message, "error")
                return redirect(url_for("radius.tool_set_speeds"))
            pending.append((p, new_down, new_up))
        changed = 0
        for p, new_down, new_up in pending:
            plans_repo.upsert_plan(replace(p, speed_down_kbps=new_down, speed_up_kbps=new_up))
            changed += 1
        # سجّل في audit
        from ..db.repos import audit_repo
        # result_status marks this as a successful (DB-level) speed change so it
        # reads «نجاح» in the unified MikroTik-actions feed. NOTE: this is a
        # plan-table change applied to all offers — there is no per-router CoA
        # push here, so there is no per-router success/fail to record (the live
        # per-session push is the separate online CoA path, captured there).
        audit_repo.record(tenant_id=_tid(), actor=_actor(), action="bulk_set_speeds",
                           target_type="plan", target_id=",".join(plan_ids),
                           result_status="success",
                           payload={"mult_down": mult_down, "mult_up": mult_up,
                                    "set_down": set_down, "set_up": set_up,
                                    "changed": changed})
        flash(f"تم تعديل سرعات {changed} باقة.", "success")
        return redirect(url_for("radius.tool_set_speeds"))
    plans = plans_repo.list_plans(_tid(), limit=500)
    return render_template("radius/tool_set_speeds.html", plans=plans)


# ─────────────── 2. maintenance ───────────────

def _send_maintenance_notice() -> None:
    """Enqueue a gated, fail-safe WhatsApp maintenance notice to subscribers.

    Explicit «maintenance notice» action only. Recipients are the usernames the
    operator listed (CSV/newline) or, if blank, every real subscriber. Each
    enqueue is gated on ``whatsapp.send.maintenance`` and wrapped so a bridge
    failure can never break the maintenance page. A single stable ``run_id``
    bucket de-dupes a double-submit within the same minute.
    """
    from ..services.whatsapp_notify import notify_whatsapp

    tid = _tid()
    # Coarse per-minute run bucket → resubmitting within the minute is deduped
    # by the idempotency key; a later run is a fresh batch.
    run_id = str(int(datetime.utcnow().timestamp() // 60))
    raw = request.form.get("usernames") or ""
    wanted = {u.strip() for u in raw.replace(",", "\n").split("\n") if u.strip()}

    try:
        rows = subscribers_repo.list_subscribers(tid, user_type="subscriber", limit=5000)
    except Exception:  # noqa: BLE001 — recipient lookup must not break the page
        rows = []

    sent = 0
    for sub in rows:
        username = str(getattr(sub, "username", "") or "")
        if wanted and username not in wanted:
            continue
        sid = int(getattr(sub, "id", 0) or 0)
        if sid <= 0:
            continue
        phone = str(getattr(sub, "mobile", "") or "").strip()
        if notify_whatsapp(
            tid,
            "maintenance_notice",
            gate="maintenance",
            recipient_phone=phone,
            template_key="maintenance_notice",
            subscriber_id=sid,
            idempotency_key=f"maint:{tid}:{run_id}:{sid}",
        ):
            sent += 1
    flash(f"تم إرسال إشعار الصيانة عبر واتساب إلى {sent} مشترك (حسب التفعيل).", "success")


def tool_maintenance():
    if request.method == "POST":
        action = request.form.get("action")
        # Explicit maintenance-notice broadcast — handled before the DB-purge
        # transaction since it touches no tables, only the gated WhatsApp bridge.
        if action == "notice":
            _send_maintenance_notice()
            return redirect(url_for("radius.tool_maintenance"))
        days = int(request.form.get("days") or 90)
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat() + "Z"
        with transaction() as conn:
            if action == "purge_radacct":
                cur = conn.execute("DELETE FROM radacct WHERE tenant_id = ? AND acctstoptime IS NOT NULL AND acctstoptime < ?",
                                    (_tid(), cutoff))
                flash(f"تم حذف {cur.rowcount} صف accounting أقدم من {days} يوم.", "success")
            elif action == "purge_sync_done":
                cur = conn.execute("DELETE FROM sync_queue WHERE tenant_id = ? AND status='done' AND completed_at < ?",
                                    (_tid(), cutoff))
                flash(f"تم حذف {cur.rowcount} job منتهي.", "success")
            elif action == "purge_audit":
                cur = conn.execute("DELETE FROM audit_log WHERE tenant_id = ? AND created_at < ?",
                                    (_tid(), cutoff))
                flash(f"تم حذف {cur.rowcount} سجل تدقيق أقدم من {days} يوم.", "success")
            elif action == "purge_failed_webhooks":
                cur = conn.execute("DELETE FROM webhook_deliveries WHERE tenant_id = ? AND status='failed'",
                                    (_tid(),))
                flash(f"تم حذف {cur.rowcount} delivery فاشلة.", "success")
            elif action == "vacuum":
                # vacuum خارج transaction
                pass
            else:
                flash("إجراء غير معروف", "error")
        if action == "vacuum":
            db().execute("VACUUM")
            flash("VACUUM اكتمل.", "success")
        return redirect(url_for("radius.tool_maintenance"))

    # إحصاءات
    stats = {}
    for tbl in ("radacct", "audit_log", "sync_queue", "webhook_deliveries"):
        try:
            stats[tbl] = db().execute(f"SELECT COUNT(*) AS c FROM {tbl} WHERE tenant_id = ?",
                                        (_tid(),)).fetchone()["c"]
        except Exception:
            stats[tbl] = 0
    # DB size
    import os
    from ..db.connection import db_path
    try: db_size_mb = os.path.getsize(db_path()) / 1048576
    except OSError: db_size_mb = 0.0
    return render_template("radius/tool_maintenance.html",
                            stats=stats, db_size_mb=db_size_mb)


# ─────────────── 3. general adjustments (bulk) ───────────────

def tool_general_adj():
    """«عمليات جماعية» — the shared general_adjustments service (same rules as
    POST /api/v1/tools/general-adjustments): the request is validated once
    (Arabic flash), «معاينة» (dry_run=1) resolves every username and shows
    what would happen without writing, and failures carry an Arabic reason."""
    if request.method == "POST":
        from ..core.errors import RadiusError
        from ..services import general_adjustments as ga
        usernames = ga.parse_usernames(request.form.get("usernames") or "")
        try:
            params = ga.validate_request(
                request.form.get("action"), usernames,
                minutes=request.form.get("minutes"),
                new_password=request.form.get("new_password"))
        except RadiusError as e:
            flash(e.message, "error")
            return render_template("radius/tool_general_adj.html", form=request.form), 400
        if (request.form.get("dry_run") or "").strip().lower() in {"1", "true", "on", "yes"}:
            preview = ga.plan(_tid(), usernames, params)
            return render_template("radius/tool_general_adj.html",
                                   form=request.form, preview=preview)
        result = ga.run(usernames, params, actor=_actor())
        success, fail = result["success"], result["failed"]
        flash(f"تم على {success} مستخدم · فشل {fail}.", "success" if success else "warning")
        failures = [i for i in result["items"] if not i["ok"]]
        if failures:
            shown = "، ".join(f"{i['username']}: {i['error']}" for i in failures[:10])
            more = "…" if len(failures) > 10 else ""
            flash(f"الأسباب: {shown}{more}", "warning")
        return redirect(url_for("radius.tool_general_adj"))
    return render_template("radius/tool_general_adj.html")


# ─────────────── 4. test_auth — محاكاة Access-Request ───────────────

def tool_test_auth():
    """يستدعي policy_engine.authorize مباشرة (بدون شبكة) ويعرض القرار."""
    result = None
    form_data = {
        "username": "", "password": "",
        "calling_station_id": "", "called_station_id": "",
        "nas_ip": "", "nas_port_type": "Ethernet",
    }
    if request.method == "POST":
        from ..services.policy_engine import AuthRequest, authorize
        for k in form_data:
            form_data[k] = (request.form.get(k) or "").strip()
        try:
            req = AuthRequest(
                username=form_data["username"],
                password=form_data["password"],
                tenant_id=_tid(),
                calling_station_id=form_data["calling_station_id"],
                called_station_id=form_data["called_station_id"],
                nas_ip=form_data["nas_ip"],
                nas_port_type=form_data["nas_port_type"],
                simulate=True,  # zero-w1 L1: لا يحجز مقعدَ جهاز
            )
            decision = authorize(req)
            result = {
                "ok": decision.ok,
                "reason": decision.reason,
                "message": decision.message,
                "reply_attrs": dict(decision.reply_attrs or {}),
            }
        except Exception as e:  # noqa: BLE001
            result = {"ok": False, "reason": "engine_error", "message": str(e),
                       "reply_attrs": {}}
    return render_template("radius/tool_test_auth.html",
                            result=result, form=form_data)


# ─────────────── 5. radius_log — عرض radpostauth الحيّ ───────────────

def tool_radius_log():
    return render_template("radius/tool_radius_log.html")


def tool_radius_log_json():
    """يعيد آخر N صف من radpostauth لـ AJAX polling."""
    try: limit = min(int(request.args.get("limit") or 50), 500)
    except ValueError: limit = 50
    rows = db().execute("""
        SELECT id, authdate, username, reply, nas, class
        FROM radpostauth
        WHERE tenant_id = ?
        ORDER BY id DESC LIMIT ?
    """, (_tid(), limit)).fetchall()
    items = [{
        "id": r["id"],
        "authdate": r["authdate"],
        "username": r["username"],
        "reply": r["reply"],
        "nas": r["nas"],
        "reason": r["class"] or "",
        "ok": "Accept" in (r["reply"] or ""),
    } for r in rows]
    return jsonify({"items": items, "count": len(items)})
