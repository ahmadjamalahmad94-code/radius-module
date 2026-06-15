"""«الحظر والتحكم بالدخول» — مسارات الإدارة (feat/access-control-blocking).

صفحة مُدارة مستقلّة (ليست ضمن قائمة الإعدادات المسطّحة): إعدادات الأمان
(منع MAC العشوائي + الحظر التلقائي)، نموذج إضافة حظر بنطاقاته الثمانية
وأنماط مدّته الثلاثة، وجدول قائمة الحظر مع زر الرفع. الإنفاذ في
policy_engine عبر services/access_control.
"""
from __future__ import annotations

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import access_blocks_repo, audit_repo, tenants_repo
from ..services import access_control as ac

# مفاتيح إعدادات الأمان المعروضة/المحفوظة في هذه الصفحة (toggle/قيمة).
_SECURITY_KEYS = {
    "security.block_random_mac_subscribers": "0",
    "security.block_random_mac_cards": "0",
    ac.SK_AUTOBLOCK_ENABLED: "0",
    ac.SK_AUTOBLOCK_THRESHOLD: "5",
    ac.SK_AUTOBLOCK_WINDOW_SEC: "300",
    ac.SK_AUTOBLOCK_DURATION_MIN: "60",
    ac.SK_AUTOBLOCK_TARGET: "mac",
}

# النطاقات وأنماط المدّة — تُمرَّر للقالب لبناء القوائم المنسدلة (تسميات عربية).
SCOPE_LABELS = [
    ("subscriber", "مشترك محدّد"),
    ("group", "مجموعة مشتركين"),
    ("plan", "عرض/باقة"),
    ("card_batch", "حزمة بطاقات"),
    ("all_subscribers", "كل المشتركين"),
    ("all_hotspot", "كل الهوتسبوت"),
    ("all_cards", "كل البطاقات"),
    ("all_pppoe", "كل PPPoE"),
    ("ip", "عنوان IP"),
    ("mac", "عنوان MAC"),
]
DURATION_LABELS = [
    ("permanent", "دائم حتى الرفع اليدوي"),
    ("daily_window", "نافذة يومية متكرّرة"),
    ("until", "حتى تاريخ/وقت محدّد"),
]


def register_access_control_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/access-control", "access_control_page",
                    access_control_page, methods=["GET"])
    bp.add_url_rule("/access-control/settings", "access_control_save_settings",
                    access_control_save_settings, methods=["POST"])
    bp.add_url_rule("/access-control/block", "access_control_add_block",
                    access_control_add_block, methods=["POST"])
    bp.add_url_rule("/access-control/block/<int:block_id>/clear",
                    "access_control_clear_block",
                    access_control_clear_block, methods=["POST"])


def _tid() -> int:
    return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))


def _actor() -> tuple[str, int]:
    actor = session.get("admin_name") or session.get("admin_user") or "anonymous"
    return actor, int(session.get("admin_id") or 0)


def access_control_page():
    tid = _tid()
    # كنس كسول للحظور المنتهية قبل العرض كي تطابق القائمة الواقع.
    try:
        access_blocks_repo.deactivate_expired(tid)
    except Exception:  # noqa: BLE001
        pass
    blocks = access_blocks_repo.list_blocks(tid)
    settings = {k: tenants_repo.get_setting(tid, k, d) for k, d in _SECURITY_KEYS.items()}
    return render_template(
        "radius/access_control.html",
        blocks=blocks,
        settings=settings,
        scope_labels=SCOPE_LABELS,
        duration_labels=DURATION_LABELS,
        scope_label_map=dict(SCOPE_LABELS),
        duration_label_map=dict(DURATION_LABELS),
        ac_keys={
            "enabled": ac.SK_AUTOBLOCK_ENABLED,
            "threshold": ac.SK_AUTOBLOCK_THRESHOLD,
            "window": ac.SK_AUTOBLOCK_WINDOW_SEC,
            "duration": ac.SK_AUTOBLOCK_DURATION_MIN,
            "target": ac.SK_AUTOBLOCK_TARGET,
        },
    )


def access_control_save_settings():
    tid = _tid()
    actor, admin_id = _actor()
    changed = []
    for key, default in _SECURITY_KEYS.items():
        if key == ac.SK_AUTOBLOCK_ENABLED or key.startswith("security.block_random_mac"):
            # toggles: غير المرسَل = مُطفأ ('0')
            val = "1" if request.form.get(key) in ("1", "on", "true") else "0"
        elif key == ac.SK_AUTOBLOCK_TARGET:
            val = (request.form.get(key) or default).strip()
            if val not in ("ip", "mac", "both"):
                val = default
        else:
            raw = (request.form.get(key) or default).strip()
            val = raw if raw.isdigit() else default
        old = tenants_repo.get_setting(tid, key, default)
        if val != old:
            tenants_repo.set_setting(tid, key, val, by=admin_id)
            changed.append(key)
    if changed:
        audit_repo.record(tenant_id=tid, actor=actor, action="access_control_settings",
                          target_type="settings", target_id=",".join(changed),
                          payload={"changed": changed})
        flash(f"تم حفظ {len(changed)} إعدادًا.", "success")
    else:
        flash("لا تغييرات.", "info")
    return redirect(url_for("radius.access_control_page"))


def access_control_add_block():
    tid = _tid()
    actor, admin_id = _actor()
    try:
        block_id = ac.create_block_from_input(
            tenant_id=tid,
            block_type=request.form.get("block_type") or "",
            target=request.form.get("target") or "",
            reason=request.form.get("reason") or "",
            duration_mode=request.form.get("duration_mode") or "permanent",
            window_start=request.form.get("window_start") or "",
            window_end=request.form.get("window_end") or "",
            expires_at=request.form.get("expires_at") or "",
            created_by=admin_id,
        )
        audit_repo.record(tenant_id=tid, actor=actor, action="access_block_add",
                          target_type="access_block", target_id=str(block_id),
                          payload={"block_type": request.form.get("block_type")})
        flash("تم إضافة الحظر.", "success")
    except ac.AccessControlError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.access_control_page"))


def access_control_clear_block(block_id: int):
    tid = _tid()
    actor, admin_id = _actor()
    if access_blocks_repo.clear_block(tid, int(block_id), by=admin_id):
        audit_repo.record(tenant_id=tid, actor=actor, action="access_block_clear",
                          target_type="access_block", target_id=str(block_id),
                          payload={"cleared": True})
        flash("تم رفع الحظر.", "success")
    else:
        flash("الحظر غير موجود أو مرفوع سابقًا.", "info")
    return redirect(url_for("radius.access_control_page"))
