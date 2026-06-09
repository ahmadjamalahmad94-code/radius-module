"""S2.2 — Audit log center.

Routes:
  GET /admin/radius/audit            list + filters
  GET /admin/radius/audit/<id>       detail (one entry)

The repo already redacts secrets at write time (S2.1), so the
templates can render `payload_json` / `before_json` / `after_json`
directly without further masking — the "***" lands at the
boundary, not in the view layer.
"""
from __future__ import annotations

import json

from flask import Blueprint, abort, g, render_template, request

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db
from ..db.repos import audit_repo
from ..services import audit_format as af
from ..services.mt_permissions import (
    PERM_AUDIT_VIEW, requires_perm,
)

_SEVERITY_LABELS = {
    "info": "معلومة",
    "warning": "تحذير",
    "critical": "حرجة",
}

_RESULT_LABELS = {
    "success": "نجحت",
    "failed": "فشلت",
    "partial": "جزئية",
    "cancelled": "ملغاة",
}

# High-value exact labels — read better than the auto-composer below.
_ACTION_LABELS = {
    "mt.programming.hotspot.apply": "تطبيق إعدادات Hotspot",
    "mt.programming.ppp.apply": "تطبيق إعدادات PPPoE",
    "mt.programming.interface.apply": "تعديل واجهة الراوتر",
    "mt.backup.create": "إنشاء نسخة احتياطية",
    "mt.deploy": "نشر إعدادات على الراوتر",
    "mt.apply": "تطبيق إعداد على الراوتر",
    "mt.toggle": "تبديل حالة الراوتر",
    "change_plan": "تغيير عرض المشترك",
    "subscriber.cash_balance_add": "إضافة رصيد نقدي",
    "subscriber.debt_settled_from_payment": "تسوية دين من دفعة",
    "subscriber.payment": "تسجيل دفعة نقدية",
    "subscriber.loan": "منح سلفة",
    "subscriber.quota_reset": "استعادة الكوتة اليومية",
    "subscriber.extend_time": "إضافة وقت للمشترك",
}

# Auto-composer vocabulary — turns an unmapped action code like
# "subscriber.cash_balance_add" into Arabic ("إضافة رصيد") instead of
# leaking the raw English tail. Verb comes from the last segment's tokens,
# noun from the nearest known token (last segment wins over the prefix).
_VERB_LABELS = {
    "create": "إنشاء", "add": "إضافة", "new": "إنشاء", "update": "تعديل",
    "edit": "تعديل", "set": "ضبط", "delete": "حذف", "remove": "حذف",
    "disable": "تعطيل", "enable": "تفعيل", "apply": "تطبيق", "deploy": "نشر",
    "toggle": "تبديل", "settle": "تسوية", "settled": "تسوية", "void": "إلغاء",
    "reset": "تصفير", "extend": "تمديد", "renew": "تجديد", "change": "تغيير",
    "login": "تسجيل دخول", "logout": "تسجيل خروج", "send": "إرسال",
    "import": "استيراد", "export": "تصدير", "freeze": "تجميد", "unfreeze": "فكّ التجميد",
    "writeoff": "مسامحة", "refund": "استرجاع", "archive": "أرشفة",
    "restore": "استعادة", "assign": "إسناد", "grant": "منح", "revoke": "سحب",
    "rename": "إعادة تسمية", "move": "نقل", "sync": "مزامنة", "run": "تشغيل",
}
_NOUN_LABELS = {
    "balance": "رصيد", "debt": "دين", "loan": "سلفة", "payment": "دفعة",
    "subscriber": "مشترك", "user": "مشترك", "card": "بطاقة", "cards": "بطاقات",
    "plan": "عرض", "quota": "كوتة", "time": "وقت", "speed": "سرعة",
    "mt": "راوتر", "router": "راوتر", "nas": "راوتر", "device": "جهاز",
    "backup": "نسخة احتياطية", "ticket": "تذكرة", "admin": "مدير",
    "distributor": "موزّع", "role": "دور", "session": "جلسة", "password": "كلمة المرور",
    "ledger": "قيد مالي", "interface": "واجهة", "hotspot": "Hotspot", "ppp": "PPPoE",
}

# Target type → Arabic, so the row reads «مشترك (user1034)» not «user#5».
_TARGET_TYPE_LABELS = {
    "user": "مشترك", "subscriber": "مشترك", "card": "بطاقة", "plan": "عرض",
    "loan": "سلفة", "payment": "دفعة", "router": "راوتر", "nas": "راوتر",
    "device": "جهاز", "admin": "مدير", "distributor": "موزّع", "role": "دور",
    "ticket": "تذكرة", "backup": "نسخة احتياطية", "ledger": "قيد مالي",
}


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def register_audit_log_routes(bp: Blueprint) -> None:
    bp.add_url_rule(
        "/audit", "audit_log_index",
        requires_perm(PERM_AUDIT_VIEW)(audit_log_index),
        methods=["GET"],
    )
    bp.add_url_rule(
        "/audit/<int:audit_id>", "audit_log_detail",
        requires_perm(PERM_AUDIT_VIEW)(audit_log_detail),
        methods=["GET"],
    )


def _int_arg(name: str) -> int | None:
    raw = (request.args.get(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _str_arg(name: str) -> str | None:
    raw = (request.args.get(name) or "").strip()
    return raw or None


def _tone_for_severity(value: str | None) -> str:
    if value == "critical":
        return "danger"
    if value == "warning":
        return "warning"
    return "info"


def _tone_for_result(value: str | None) -> str:
    if value == "success":
        return "success"
    if value in {"failed", "cancelled"}:
        return "danger"
    if value == "partial":
        return "warning"
    return "muted"


def _action_label(action: str | None) -> str:
    """يستدعي خدمة `audit_format.action_label` كي تبقى الخريطة الموسّعة
    والمُركِّب الذكي بمصدر واحد قابل للاختبار. لا منطق هنا — مجرّد جسر
    للحفاظ على الاسم القديم لأيّ مكان يستخدمه."""
    return af.action_label(action)


def _target_label(target_type: str | None, target_id,
                  names: dict | None = None) -> str:
    """يستدعي خدمة `audit_format.target_label_for` التي تُرجع الاسم
    الفعلي للهدف إن وُجد في خريطة `names` المحلولة دفعةً واحدة، وإلّا
    «النوع العربي #المعرّف» (لا «هدف (17)» الخام)."""
    return af.target_label_for(target_type, target_id, names=names)


def _decorate_row(row: dict, *,
                  target_names: dict | None = None,
                  router_names: dict | None = None) -> dict:
    try:
        payload = json.loads(row.get("payload_json") or "{}")
    except (TypeError, ValueError):
        payload = {}
    severity = row.get("severity") or "info"
    result_status = row.get("result_status") or ""
    rid_raw = row.get("router_id")
    try:
        rid_int = int(rid_raw) if rid_raw not in (None, "") else None
    except (TypeError, ValueError):
        rid_int = None
    router_name = (router_names or {}).get(rid_int) if rid_int is not None else None
    # عنوان «الراوتر» الظاهر: الاسم إن وُجد، وإلّا «المايكروتيك #ID»
    # — لا «#17» الخام. الـtitle (في القالب) يحمل المعرّف الأصلي للمراجعة.
    if router_name:
        router_label = router_name
    elif rid_int is not None:
        router_label = f"المايكروتيك #{rid_int}"
    else:
        router_label = ""
    return {
        **row,
        "payload": payload,
        # نُبقي preview_keys لمستهلكي الـAPI القدامى إن وُجدوا.
        "preview_keys": [k for k in payload.keys() if k != "ok"][:4],
        "action_label": _action_label(row.get("action")),
        "severity_label": _SEVERITY_LABELS.get(severity, severity),
        "severity_tone": _tone_for_severity(severity),
        "result_label": _RESULT_LABELS.get(
            result_status, result_status or "غير محددة"),
        "result_tone": _tone_for_result(result_status),
        "target_label": _target_label(
            row.get("target_type"), row.get("target_id"),
            names=target_names),
        # عمود «الراوتر» الجديد بالاسم.
        "router_label": router_label,
        # عمود «التفاصيل» الجديد بجملة عربية موجزة.
        "details_label": af.format_payload(
            row.get("action"), payload,
            target_type=row.get("target_type")),
    }


def audit_log_index():
    filters = {
        "router_id": _int_arg("router_id"),
        "action": _str_arg("action"),
        "severity": _str_arg("severity"),
        "result_status": _str_arg("result_status"),
        "search": _str_arg("q"),
    }
    rows = audit_repo.recent(_tid(), limit=200, **filters)
    # حلّ أسماء الراوترات/الأهداف في استعلامين مجمّعَين على الأكثر،
    # ثم زيِّن كل صفّ. كان السلوك السابق يمرّ على r["router_id"] خامًا
    # فيُظهر «#17» للمشغّل و«هدف (17)» للأهداف الـ mikrotik_nas.
    conn = db()
    router_names = af.resolve_router_names(
        rows, tenant_id=_tid(), db_conn=conn)
    target_names = af.resolve_target_names(
        rows, tenant_id=_tid(), db_conn=conn)
    decorated = [_decorate_row(r, router_names=router_names,
                               target_names=target_names) for r in rows]
    summary = {
        "total": len(decorated),
        "critical": sum(
            1 for r in decorated if r.get("severity") == "critical"),
        "warnings": sum(1 for r in decorated if r.get("severity") == "warning"),
        "failed": sum(
            1 for r in decorated if r.get("result_status") == "failed"),
        "success": sum(
            1 for r in decorated if r.get("result_status") == "success"),
        "routers": len({
            r.get("router_id") for r in decorated if r.get("router_id")
        }),
        "active_filters": sum(1 for v in filters.values() if v not in (None, "")),
    }
    return render_template(
        "radius/audit_log_index.html",
        rows=decorated,
        filters=filters,
        summary=summary,
        # Surface the values the UI dropdowns need.
        severities=["info", "warning", "critical"],
        result_statuses=["success", "failed", "partial", "cancelled"],
        severity_labels=_SEVERITY_LABELS,
        result_labels=_RESULT_LABELS,
    )


def audit_log_detail(audit_id: int):
    row = audit_repo.get_by_id(_tid(), int(audit_id))
    if not row:
        abort(404)
    # Parse the three JSON columns for display.
    for col in ("payload_json", "before_json", "after_json"):
        try:
            row[col.replace("_json", "")] = \
                json.loads(row.get(col) or "{}")
        except (TypeError, ValueError):
            row[col.replace("_json", "")] = {}
    # حلّ اسم الراوتر/الهدف لصفّ واحد (نفس الـAPI، صفّ منفرد في list).
    conn = db()
    router_names = af.resolve_router_names(
        [row], tenant_id=_tid(), db_conn=conn)
    target_names = af.resolve_target_names(
        [row], tenant_id=_tid(), db_conn=conn)
    row = _decorate_row(row, router_names=router_names,
                       target_names=target_names)
    return render_template("radius/audit_log_detail.html",
                           entry=row)
