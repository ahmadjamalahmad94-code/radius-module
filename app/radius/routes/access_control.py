"""«التحكم بالدخول» — مسارات الإدارة (feat/access-control-blocking).

صفحة مُدارة بطبقتين متمايزتين في القسم نفسه:
  • «تعليق الوصول» (suspension): نطاقي (مشترك/مجموعة/عرض/حزمة/شامل) + أنماط
    المدّة — يحكم متى/هل يُسمح بالدخول (رسالة مهذّبة للمستخدم).
  • «حظر» (block): IP/MAC يدوي + تلقائي (fail2ban) + منع MAC العشوائي.
الإنفاذ في policy_engine عبر services/access_control.
"""
from __future__ import annotations

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.repos import (
    access_blocks_repo, allow_mode_repo, audit_repo, cards_repo,
    plans_repo, tenants_repo,
)
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

# نطاقات «تعليق الوصول» (الطبقة A) — تُمرَّر لنموذج التعليق.
SUSPENSION_SCOPE_LABELS = [
    ("subscriber", "مشترك محدّد"),
    ("group", "مجموعة مشتركين"),
    ("plan", "عرض/باقة"),
    ("card_batch", "حزمة بطاقات"),
    ("all_subscribers", "كل المشتركين"),
    ("all_hotspot", "كل الهوتسبوت"),
    ("all_cards", "كل البطاقات"),
    ("all_pppoe", "كل PPPoE"),
]
# نطاقات «الحظر» الأمني (الطبقة B) — تُمرَّر لنموذج الحظر.
BLOCK_SCOPE_LABELS = [
    ("ip", "عنوان IP"),
    ("mac", "عنوان MAC"),
]
# خريطة موحّدة لعرض التسمية في الجداول.
SCOPE_LABEL_MAP = dict(SUSPENSION_SCOPE_LABELS + BLOCK_SCOPE_LABELS)
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
    # «نمط السماح» — السياسات + الأجهزة
    bp.add_url_rule("/access-control/allow-mode/policy",
                    "access_control_allow_mode_upsert",
                    allow_mode_upsert, methods=["POST"])
    bp.add_url_rule("/access-control/allow-mode/policy/<int:policy_id>/delete",
                    "access_control_allow_mode_delete_policy",
                    allow_mode_delete_policy, methods=["POST"])
    bp.add_url_rule("/access-control/allow-mode/policy/<int:policy_id>/toggle",
                    "access_control_allow_mode_toggle_policy",
                    allow_mode_toggle_policy, methods=["POST"])
    bp.add_url_rule("/access-control/allow-mode/device",
                    "access_control_allow_mode_add_device",
                    allow_mode_add_device, methods=["POST"])
    bp.add_url_rule("/access-control/allow-mode/device/<int:device_id>/delete",
                    "access_control_allow_mode_delete_device",
                    allow_mode_delete_device, methods=["POST"])


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
    suspensions = access_blocks_repo.list_blocks(tid, layer=ac.LAYER_SUSPENSION)
    blocks = access_blocks_repo.list_blocks(tid, layer=ac.LAYER_BLOCK)
    settings = {k: tenants_repo.get_setting(tid, k, d) for k, d in _SECURITY_KEYS.items()}

    # «نمط السماح»: السياسات + قائمة Plans/CardBatches + الأجهزة لكل سياسة.
    try:
        am_policies = allow_mode_repo.list_policies(tid)
    except Exception:  # noqa: BLE001
        am_policies = []
    try:
        plans_list = plans_repo.list_plans(tid, limit=500)
    except Exception:  # noqa: BLE001
        plans_list = []
    try:
        batches_list = cards_repo.list_batches(tid, limit=500)
    except Exception:  # noqa: BLE001
        batches_list = []
    plan_by_id = {int(getattr(p, "id", 0)): p for p in plans_list if getattr(p, "id", None)}
    batch_by_id = {int(getattr(b, "id", 0)): b for b in batches_list if getattr(b, "id", None)}

    am_view = []
    for pol in am_policies:
        try:
            devs = allow_mode_repo.list_devices(int(pol["id"]))
        except Exception:  # noqa: BLE001
            devs = []
        if pol.get("scope_type") == "plan":
            obj = plan_by_id.get(int(pol.get("scope_id") or 0))
            scope_name = getattr(obj, "name", None) or f"plan #{pol.get('scope_id')}"
        else:
            obj = batch_by_id.get(int(pol.get("scope_id") or 0))
            scope_name = getattr(obj, "name", None) or f"batch #{pol.get('scope_id')}"
        am_view.append({**pol, "devices": devs, "scope_name": scope_name})

    return render_template(
        "radius/access_control.html",
        suspensions=suspensions,
        blocks=blocks,
        settings=settings,
        suspension_scope_labels=SUSPENSION_SCOPE_LABELS,
        block_scope_labels=BLOCK_SCOPE_LABELS,
        duration_labels=DURATION_LABELS,
        scope_label_map=SCOPE_LABEL_MAP,
        duration_label_map=dict(DURATION_LABELS),
        ac_keys={
            "enabled": ac.SK_AUTOBLOCK_ENABLED,
            "threshold": ac.SK_AUTOBLOCK_THRESHOLD,
            "window": ac.SK_AUTOBLOCK_WINDOW_SEC,
            "duration": ac.SK_AUTOBLOCK_DURATION_MIN,
            "target": ac.SK_AUTOBLOCK_TARGET,
        },
        # Allow-mode
        am_policies=am_view,
        am_plans=plans_list,
        am_batches=batches_list,
        am_mode_labels=[
            ("open",   "Open — بلا ربط أجهزة (حدّ الجلسات من العرض)"),
            ("tofu",   "TOFU — أوّل دخول ناجح يربط الجهاز، سقف N أجهزة"),
            ("manual", "Manual — قائمة سماح يدوية (افتراضي رفض)"),
        ],
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
    block_type = request.form.get("block_type") or ""
    is_block = ac.layer_of(block_type) == ac.LAYER_BLOCK
    noun = "الحظر" if is_block else "التعليق"
    try:
        block_id = ac.create_block_from_input(
            tenant_id=tid,
            block_type=block_type,
            target=request.form.get("target") or "",
            reason=request.form.get("reason") or "",
            duration_mode=request.form.get("duration_mode") or "permanent",
            window_start=request.form.get("window_start") or "",
            window_end=request.form.get("window_end") or "",
            expires_at=request.form.get("expires_at") or "",
            created_by=admin_id,
        )
        audit_repo.record(tenant_id=tid, actor=actor, action="access_control_add",
                          target_type="access_control", target_id=str(block_id),
                          payload={"block_type": block_type, "layer": ac.layer_of(block_type)})
        flash(f"تم إضافة {noun}.", "success")
    except ac.AccessControlError as exc:
        flash(str(exc), "error")
    return redirect(url_for("radius.access_control_page"))


def access_control_clear_block(block_id: int):
    tid = _tid()
    actor, admin_id = _actor()
    existing = access_blocks_repo.get_block(tid, int(block_id))
    noun = "الحظر"
    if existing and ac.layer_of(existing.get("block_type")) == ac.LAYER_SUSPENSION:
        noun = "التعليق"
    if access_blocks_repo.clear_block(tid, int(block_id), by=admin_id):
        audit_repo.record(tenant_id=tid, actor=actor, action="access_control_clear",
                          target_type="access_control", target_id=str(block_id),
                          payload={"cleared": True})
        flash(f"تم رفع {noun}.", "success")
    else:
        flash("السجلّ غير موجود أو مرفوع سابقًا.", "info")
    return redirect(url_for("radius.access_control_page"))


# ════════════════════════════════════════════════════════════════════════
# «نمط السماح» (Allow-mode) — Handlers
# ════════════════════════════════════════════════════════════════════════
def _redirect_am():
    return redirect(url_for("radius.access_control_page", _anchor="allow-mode"))


def allow_mode_upsert():
    """ينشئ/يحدّث سياسة نمط سماح (UNIQUE على tenant+scope_type+scope_id)."""
    tid = _tid()
    actor, admin_id = _actor()
    scope_type = (request.form.get("scope_type") or "").strip()
    try:
        scope_id = int(request.form.get("scope_id") or 0)
    except (TypeError, ValueError):
        scope_id = 0
    mode = (request.form.get("mode") or "open").strip()
    try:
        max_devices = int(request.form.get("max_devices") or 0)
    except (TypeError, ValueError):
        max_devices = 0
    active = (request.form.get("active") or "1") in ("1", "on", "true")
    note = (request.form.get("note") or "").strip()[:200]

    if scope_type not in allow_mode_repo.VALID_SCOPES:
        flash("نطاق السياسة غير صالح.", "error")
        return _redirect_am()
    if scope_id <= 0:
        flash("اختر العرض/الحزمة المستهدفة.", "error")
        return _redirect_am()
    if mode not in allow_mode_repo.VALID_MODES:
        flash("نمط السماح غير صالح.", "error")
        return _redirect_am()
    if mode == "tofu" and max_devices <= 0:
        flash("نمط TOFU يحتاج عددًا صالحًا (1 على الأقل) للأجهزة المسموحة.", "error")
        return _redirect_am()

    try:
        pol = allow_mode_repo.upsert_policy(
            tenant_id=tid, scope_type=scope_type, scope_id=scope_id,
            mode=mode, max_devices=max_devices, active=active,
            note=note, by=admin_id)
        audit_repo.record(tenant_id=tid, actor=actor,
                          action="allow_mode_upsert",
                          target_type="allow_mode_policy",
                          target_id=str(pol["id"]),
                          payload={"scope_type": scope_type,
                                    "scope_id": scope_id,
                                    "mode": mode,
                                    "max_devices": max_devices,
                                    "active": active})
        flash("تم حفظ سياسة نمط السماح.", "success")
    except ValueError as exc:
        flash(str(exc), "error")
    return _redirect_am()


def allow_mode_delete_policy(policy_id: int):
    tid = _tid()
    actor, admin_id = _actor()
    if allow_mode_repo.delete_policy(tid, int(policy_id)):
        audit_repo.record(tenant_id=tid, actor=actor,
                          action="allow_mode_delete_policy",
                          target_type="allow_mode_policy",
                          target_id=str(policy_id),
                          payload={"deleted": True})
        flash("حُذفت السياسة وكل أجهزتها.", "success")
    else:
        flash("السياسة غير موجودة.", "info")
    return _redirect_am()


def allow_mode_toggle_policy(policy_id: int):
    tid = _tid()
    actor, admin_id = _actor()
    pol = allow_mode_repo.get_policy_by_id(tid, int(policy_id))
    if not pol:
        flash("السياسة غير موجودة.", "info")
        return _redirect_am()
    new_active = not bool(pol.get("active"))
    if allow_mode_repo.set_policy_active(tid, int(policy_id), new_active):
        audit_repo.record(tenant_id=tid, actor=actor,
                          action="allow_mode_toggle_policy",
                          target_type="allow_mode_policy",
                          target_id=str(policy_id),
                          payload={"active": new_active})
        flash("تم تفعيل السياسة." if new_active else "تم تعليق السياسة.",
              "success")
    return _redirect_am()


def allow_mode_add_device():
    """إضافة جهاز يدويًّا لسياسة. username='' = مشترك بين كل المستخدمين."""
    tid = _tid()
    actor, admin_id = _actor()
    try:
        policy_id = int(request.form.get("policy_id") or 0)
    except (TypeError, ValueError):
        policy_id = 0
    pol = allow_mode_repo.get_policy_by_id(tid, policy_id)
    if not pol:
        flash("السياسة غير موجودة.", "error")
        return _redirect_am()
    username = (request.form.get("username") or "").strip()
    mac = (request.form.get("mac") or "").strip()
    label = (request.form.get("label") or "").strip()[:120]
    norm = allow_mode_repo.normalize_mac(mac)
    import re as _re
    if not _re.match(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$", norm):
        flash("صيغة MAC غير صالحة (مثال: AA:BB:CC:DD:EE:FF).", "error")
        return _redirect_am()
    dev = allow_mode_repo.add_device(
        policy_id=policy_id, username=username, mac=norm,
        source="manual", label=label, by=admin_id)
    if dev:
        audit_repo.record(tenant_id=tid, actor=actor,
                          action="allow_mode_add_device",
                          target_type="allow_mode_device",
                          target_id=str(dev["id"]),
                          payload={"policy_id": policy_id,
                                    "username": username,
                                    "mac": norm})
        flash("تمت إضافة الجهاز.", "success")
    else:
        flash("تعذّر إضافة الجهاز.", "error")
    return _redirect_am()


def allow_mode_delete_device(device_id: int):
    tid = _tid()
    actor, admin_id = _actor()
    # نتحقّق أنّ الجهاز يخصّ سياسة لنفس المستأجر قبل الحذف.
    from app.radius.db.connection import db as _db
    row = _db().execute(
        "SELECT d.id FROM allow_mode_devices d "
        "JOIN allow_mode_policies p ON p.id = d.policy_id "
        "WHERE d.id = ? AND p.tenant_id = ?",
        (int(device_id), tid),
    ).fetchone()
    if not row:
        flash("الجهاز غير موجود.", "info")
        return _redirect_am()
    if allow_mode_repo.delete_device(int(device_id)):
        audit_repo.record(tenant_id=tid, actor=actor,
                          action="allow_mode_delete_device",
                          target_type="allow_mode_device",
                          target_id=str(device_id),
                          payload={"deleted": True})
        flash("حُذف الجهاز.", "success")
    return _redirect_am()
