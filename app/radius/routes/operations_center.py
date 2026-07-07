"""Operations Center and dry-run Speed Control Center routes."""
from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.operations_speed_center import OperationsSpeedCenterService, OperationsSpeedError, SPEED_PRESETS


def register_operations_center_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/operations", "operations_center", operations_center, methods=["GET"])
    # «التحكم المجدول»: بطاقات الأوضاع + المعاينة + السياسات المحفوظة
    bp.add_url_rule("/operations/speed-control", "operations_speed_control", operations_speed_control, methods=["GET", "POST"])
    # «التحكم اليدوي»: محرّك السلايدر والحلقة — نفس عقد الحفظ/المعاينة في الخادم
    bp.add_url_rule("/operations/speed-control/manual", "operations_speed_control_manual", operations_speed_control_manual, methods=["GET", "POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _svc() -> OperationsSpeedCenterService:
    return OperationsSpeedCenterService(tenant_id=_tid())


def operations_center():
    return render_template("radius/operations_center.html", snapshot=_svc().operations_snapshot())


def _schedule_context() -> dict:
    """سياق «الجداول الزمنية للسرعة» المشترك لصفحتَي التحكم بالسرعة.

    مصدر واحد للحقيقة: النماذج تُرسِل إلى نفس مسارات
    radius.bandwidth_schedules_create/_update/_delete/_apply (مع return_to)،
    والبيانات هنا تُقرأ من نفس خدمة العمليات ومستودعاتها — لا نموذج جداول منافس.
    """
    from ..services.bandwidth_rate import live_apply_enabled as _live_enabled
    from ..services.cards import get_cards_service
    from ..services.operations import get_operations_service
    from ..services.plans import get_plans_service
    from ..services.users import get_users_service

    tid = _tid()
    plans = list(get_plans_service().list(limit=500))
    subscribers = list(get_users_service().list(user_type="subscriber", limit=500))
    batches = list(get_cards_service().list_batches(limit=500))
    schedules = get_operations_service().list_bandwidth_schedules(tenant_id=tid, limit=500)
    live_apply_enabled = _live_enabled()
    return {
        "schedules": schedules,
        "sched_plans": plans,
        "sched_subscribers": subscribers,
        "sched_batches": batches,
        "plan_names": {plan.id: plan.name for plan in plans},
        "subscriber_names": {
            sub.username: (sub.full_name or sub.username) for sub in subscribers
        },
        "batch_names": {
            batch.id: f"{batch.batch_code} - {batch.package_name or batch.service_name or 'بدون اسم'}"
            for batch in batches
        },
        "live_apply_enabled": live_apply_enabled,
    }


def _speed_control_page(template: str, redirect_endpoint: str):
    """منطق مشترك لصفحتَي التحكم بالسرعة (المجدول واليدوي).

    نفس عقد POST تمامًا (preset / multiplier / profile_ids / settings_json /
    save_policy / policy_key / title) — يختلف فقط القالب المعروض ووجهة
    إعادة التوجيه بعد الحفظ، فيبقى كل مسار على صفحته."""
    svc = _svc()
    preview = None
    if request.method == "POST":
        try:
            preset = request.form.get("preset") or "normal"
            mode, profile_ids, multiplier, overrides = _parse_control_payload(request.form, preset)
            if request.form.get("apply") == "1":
                # تطبيق فعليّ حيّ: يُخزّن المعامل (يَحكم الجديد) + CoA لكلّ المتصلين.
                result = svc.apply_speed_policy(
                    preset=preset, multiplier=multiplier, profile_ids=profile_ids,
                    overrides=overrides, mode=mode, actor=_actor(),
                    title=request.form.get("title") or "",
                    policy_key=request.form.get("policy_key") or "",
                )
                _coa = result.get("coa") or {}
                _hit = f"{int(_coa.get('applied') or 0)}/{int(_coa.get('targets') or 0)}"
                if result.get("reset"):
                    flash(f"أُعيدت السرعة للوضع الطبيعي (100%). طُبِّق على {_hit} جلسة متصلة، "
                          f"والجلسات الجديدة تعود لسرعتها الأصليّة.", "success")
                else:
                    _pct = int(round(float(result.get("multiplier") or 1.0) * 100))
                    flash(f"طُبِّقت «{result.get('label')}» ({_pct}%) حيًّا: CoA على {_hit} جلسة "
                          f"متصلة، وتُطبَّق تلقائيًّا على كلّ اتصال جديد.", "success")
                return redirect(url_for(redirect_endpoint, policy_id=result["policy"]["id"]))
            if request.form.get("save_policy") == "1":
                policy = svc.save_speed_policy(
                    policy_key=request.form.get("policy_key") or "",
                    title=request.form.get("title") or "",
                    preset=preset,
                    multiplier=multiplier,
                    profile_ids=profile_ids,
                    overrides=overrides,
                    mode=mode,
                    actor=_actor(),
                )
                flash("تم حفظ سياسة السرعة كمعاينة بدون تنفيذ. لم يتم تطبيق أي تغيير مباشر على RADIUS أو CoA.", "success")
                return redirect(url_for(redirect_endpoint, policy_id=policy["id"]))
            preview = svc.speed_preview(
                preset=preset, multiplier=multiplier, profile_ids=profile_ids, overrides=overrides
            )
        except (OperationsSpeedError, ValueError) as exc:
            flash(str(exc), "error")
    return render_template(
        template,
        presets=SPEED_PRESETS,
        preview=preview,
        policies=svc.list_policies(),
        control_profiles=svc.control_profiles(),
        active_speed=svc.active_policy(),   # لتهيئة الواجهة بالسرعة المطبَّقة فعلًا
        **_schedule_context(),
    )


def operations_speed_control():
    """التحكم المجدول: بطاقات الأوضاع + المعاينة + السياسات المحفوظة."""
    return _speed_control_page("radius/operations_speed_control.html", "radius.operations_speed_control")


def operations_speed_control_manual():
    """التحكم اليدوي: محرّك السلايدر/الحلقة — يعيد استخدام نفس منطق POST."""
    return _speed_control_page("radius/operations_speed_control_manual.html", "radius.operations_speed_control_manual")


def _parse_control_payload(form, preset: str):
    """Resolve the POST payload into (mode, profile_ids, multiplier, overrides).

    Two shapes are supported:
      • legacy form fields (profile_ids + multiplier) — unchanged behaviour;
      • a rich ``settings_json`` payload from the redesigned UI describing the
        mode, the enabled profiles and their per-profile download/upload
        percentages. Percentages are converted to safe multipliers (42% → 0.42).
    """
    raw = form.get("settings_json")
    if not raw:
        profile_ids = _ids(form.get("profile_ids") or "")
        multiplier = float(form.get("multiplier") or SPEED_PRESETS.get(preset, SPEED_PRESETS["normal"])["multiplier"])
        return "unified", profile_ids, multiplier, None

    data = json.loads(raw)
    mode = data.get("mode") or "unified"
    enabled = [p for p in (data.get("profiles") or []) if p.get("enabled")]
    profile_ids = [int(p["id"]) for p in enabled if str(p.get("id", "")).strip()]
    overrides = {
        int(p["id"]): {
            "down": _pct(p.get("down"), 100) / 100.0,
            "up": _pct(p.get("up"), 100) / 100.0,
        }
        for p in enabled
        if str(p.get("id", "")).strip()
    }
    glob = data.get("global") or {}
    if mode == "unified":
        multiplier = _pct(glob.get("down"), 100) / 100.0
    else:
        downs = [o["down"] for o in overrides.values()] or [1.0]
        multiplier = sum(downs) / len(downs)
    return mode, profile_ids, multiplier, overrides


def _pct(value, default: float) -> float:
    # 0–300%: profiles may be throttled (<100%) or boosted up to 3x the base speed.
    try:
        return max(0.0, min(300.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _ids(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip().isdigit()]
