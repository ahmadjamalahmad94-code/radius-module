"""Operations Center and dry-run Speed Control Center routes."""
from __future__ import annotations

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
            profile_ids = _ids(request.form.get("profile_ids") or "")
            preset = request.form.get("preset") or "normal"
            multiplier = float(request.form.get("multiplier") or SPEED_PRESETS.get(preset, SPEED_PRESETS["normal"])["multiplier"])
            if request.form.get("save_policy") == "1":
                policy = svc.save_speed_policy(
                    policy_key=request.form.get("policy_key") or "",
                    title=request.form.get("title") or "",
                    preset=preset,
                    multiplier=multiplier,
                    profile_ids=profile_ids,
                    actor=_actor(),
                )
                flash("Speed policy saved as dry-run. No live RADIUS/CoA change was applied.", "success")
                return redirect(url_for("radius.operations_speed_control", policy_id=policy["id"]))
            preview = svc.speed_preview(preset=preset, multiplier=multiplier, profile_ids=profile_ids)
        except (OperationsSpeedError, ValueError) as exc:
            flash(str(exc), "error")
    return render_template(
        "radius/operations_speed_control.html",
        presets=SPEED_PRESETS,
        preview=preview,
        policies=svc.list_policies(),
    )


def _ids(raw: str) -> list[int]:
    return [int(part.strip()) for part in raw.split(",") if part.strip().isdigit()]
