"""Operations Center and dry-run Speed Control Center routes."""
from __future__ import annotations

import json

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from ..services.operations_speed_center import OperationsSpeedCenterService, OperationsSpeedError, SPEED_PRESETS


def register_operations_center_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/operations", "operations_center", operations_center, methods=["GET"])
    bp.add_url_rule("/operations/speed-control", "operations_speed_control", operations_speed_control, methods=["GET", "POST"])


def _tid() -> int:
    return int(session.get("tenant_id") or 1)


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _svc() -> OperationsSpeedCenterService:
    return OperationsSpeedCenterService(tenant_id=_tid())


def operations_center():
    return render_template("radius/operations_center.html", snapshot=_svc().operations_snapshot())


def operations_speed_control():
    svc = _svc()
    preview = None
    if request.method == "POST":
        try:
            preset = request.form.get("preset") or "normal"
            mode, profile_ids, multiplier, overrides = _parse_control_payload(request.form, preset)
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
                return redirect(url_for("radius.operations_speed_control", policy_id=policy["id"]))
            preview = svc.speed_preview(
                preset=preset, multiplier=multiplier, profile_ids=profile_ids, overrides=overrides
            )
        except (OperationsSpeedError, ValueError) as exc:
            flash(str(exc), "error")
    return render_template(
        "radius/operations_speed_control.html",
        presets=SPEED_PRESETS,
        preview=preview,
        policies=svc.list_policies(),
        control_profiles=svc.control_profiles(),
    )


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
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _ids(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip().isdigit()]
