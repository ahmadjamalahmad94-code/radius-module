"""«منع استنساخ MAC» — مسارات الإدارة (feat/anti-mac-clone).

صفحة واحدة تتيح:
  • تشغيل/إيقاف الميزة (toggle عام، الافتراضي OFF).
  • اختيار النمط: monitor (سجّل فقط) | enforce (ارفض + كَل CoA).
  • النطاق: all | plans (قائمة عرض/باقة) | groups (قائمة مجموعات).
  • حدّ الثقة الأدنى لاتخاذ القرار: low | medium | high.
  • تفعيل حارس الجلسات المتزامنة (impossible-travel) + CoA + التنبيه.
  • قائمة الارتباطات النشطة (mac_clone_bindings) — حذف/تعليق/استبدال.
  • سجلّ الأحداث (mac_clone_events) — للمراجعة.

الإنفاذ الحقيقي في policy_engine → services.anti_mac_clone. هذه الصفحة
لا تكتب في DB سوى عبر الخدمة (set_settings / repo) — للتدقيق الموحَّد.
"""
from __future__ import annotations

from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import audit_repo, mac_clone_repo, plans_repo, subscriber_groups_repo
from ..services import anti_mac_clone as svc


def register_anti_mac_clone_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/anti-mac-clone", "anti_mac_clone_page",
                    page, methods=["GET"])
    bp.add_url_rule("/anti-mac-clone/settings", "anti_mac_clone_save_settings",
                    save_settings, methods=["POST"])
    bp.add_url_rule("/anti-mac-clone/binding/<int:binding_id>/<string:action>",
                    "anti_mac_clone_binding_action",
                    binding_action, methods=["POST"])


# ════════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════════
def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> tuple[str, int]:
    actor = session.get("admin_name") or session.get("admin_user") or "anonymous"
    try:
        admin_id = int(session.get("admin_id") or 0)
    except (TypeError, ValueError):
        admin_id = 0
    return actor, admin_id


# ════════════════════════════════════════════════════════════════════════
# الصفحة الرئيسة
# ════════════════════════════════════════════════════════════════════════
def page():
    tid = _tid()
    settings = svc.get_settings(tid)
    try:
        events_limit = max(10, min(2000, int(settings.get(svc.SK_RAW_LIMIT) or 200)))
    except ValueError:
        events_limit = 200

    bindings = mac_clone_repo.list_bindings(tid, limit=500)
    events = mac_clone_repo.list_events(tid, limit=events_limit)
    counts = mac_clone_repo.count_events_by_type(tid)

    # قوائم الـscope (للنموذج).
    try:
        plans = plans_repo.list_plans(tid, limit=500)
    except Exception:  # noqa: BLE001
        plans = []
    try:
        groups = subscriber_groups_repo.list_groups(tid)
    except Exception:  # noqa: BLE001
        groups = []

    return render_template(
        "radius/anti_mac_clone.html",
        settings=settings,
        sk={
            "enabled":           svc.SK_ENABLED,
            "mode":              svc.SK_MODE,
            "scope":             svc.SK_SCOPE,
            "scope_plan_ids":    svc.SK_SCOPE_PLAN_IDS,
            "scope_group_names": svc.SK_SCOPE_GROUP_NAMES,
            "confidence_min":    svc.SK_CONFIDENCE_MIN,
            "concurrent_guard":  svc.SK_CONCURRENT_GUARD,
            "alert_enabled":     svc.SK_ALERT_ENABLED,
            "coa_disconnect":    svc.SK_COA_DISCONNECT,
            "raw_limit":         svc.SK_RAW_LIMIT,
        },
        bindings=bindings,
        events=events,
        counts={
            "bindings":          mac_clone_repo.count_bindings(tid),
            "active_bindings":   mac_clone_repo.count_bindings(tid, status="active"),
            "clone_detected":    int(counts.get("clone_detected") or 0),
            "verify_ok":         int(counts.get("verify_ok") or 0),
            "bind":              int(counts.get("bind") or 0),
            "concurrent_kick":   int(counts.get("concurrent_kick") or 0),
            "stepup":            int(counts.get("stepup_required") or 0),
        },
        plans=plans,
        groups=groups,
        feature_enabled=svc.is_enabled(tid),
    )


# ════════════════════════════════════════════════════════════════════════
# حفظ الإعدادات
# ════════════════════════════════════════════════════════════════════════
def save_settings():
    tid = _tid()
    actor, admin_id = _actor()

    # نبني القاموس من النموذج (تواكب set_settings تطبيع كل قيمة).
    values = {
        # toggles ثنائية: المرسَل = 1، غير المرسَل = 0.
        svc.SK_ENABLED:           "1" if request.form.get(svc.SK_ENABLED) in ("1", "on", "true") else "0",
        svc.SK_CONCURRENT_GUARD:  "1" if request.form.get(svc.SK_CONCURRENT_GUARD) in ("1", "on", "true") else "0",
        svc.SK_ALERT_ENABLED:     "1" if request.form.get(svc.SK_ALERT_ENABLED) in ("1", "on", "true") else "0",
        svc.SK_COA_DISCONNECT:    "1" if request.form.get(svc.SK_COA_DISCONNECT) in ("1", "on", "true") else "0",
        svc.SK_MODE:              (request.form.get(svc.SK_MODE) or "").strip(),
        svc.SK_SCOPE:             (request.form.get(svc.SK_SCOPE) or "").strip(),
        svc.SK_CONFIDENCE_MIN:    (request.form.get(svc.SK_CONFIDENCE_MIN) or "").strip(),
        svc.SK_RAW_LIMIT:         (request.form.get(svc.SK_RAW_LIMIT) or "").strip(),
    }
    # multi-select قد تكون قائمة قيم.
    plan_ids = ",".join(request.form.getlist(svc.SK_SCOPE_PLAN_IDS))
    group_names = ",".join(request.form.getlist(svc.SK_SCOPE_GROUP_NAMES))
    values[svc.SK_SCOPE_PLAN_IDS] = plan_ids
    values[svc.SK_SCOPE_GROUP_NAMES] = group_names

    changed = svc.set_settings(tid, values, by=admin_id)
    if changed:
        audit_repo.record(tenant_id=tid, actor=actor,
                          action="anti_mac_clone_settings",
                          target_type="settings",
                          target_id=",".join(changed),
                          payload={"changed": changed})
        flash(f"تم حفظ {len(changed)} إعدادًا لميزة «منع استنساخ MAC».", "success")
    else:
        flash("لا تغييرات.", "info")
    return redirect(url_for("radius.anti_mac_clone_page"))


# ════════════════════════════════════════════════════════════════════════
# عمليات على الـbindings
# ════════════════════════════════════════════════════════════════════════
def binding_action(binding_id: int, action: str):
    tid = _tid()
    actor, admin_id = _actor()
    action = (action or "").lower()
    success = False

    if action == "delete":
        success = mac_clone_repo.delete_binding(tid, int(binding_id))
        verb = "حذف"
    elif action in ("suspend", "active", "superseded"):
        success = mac_clone_repo.set_binding_status(
            tid, int(binding_id),
            "suspended" if action == "suspend" else action)
        verb = {"suspend": "تعليق", "active": "تفعيل",
                "superseded": "وسم كمستبدَل"}[action]
    else:
        flash("إجراء غير معروف.", "error")
        return redirect(url_for("radius.anti_mac_clone_page"))

    if success:
        audit_repo.record(tenant_id=tid, actor=actor,
                          action=f"anti_mac_clone_binding_{action}",
                          target_type="mac_clone_binding",
                          target_id=str(binding_id),
                          payload={"action": action})
        flash(f"تم {verb} الارتباط.", "success")
    else:
        flash("الارتباط غير موجود أو سبق إجراؤه.", "info")
    return redirect(url_for("radius.anti_mac_clone_page"))


__all__ = ["register_anti_mac_clone_routes"]
