"""Internal pilot drill readiness checks for setup wizard.

This module is intentionally read-only. It prepares operators for a lab pilot
by checking prerequisites and producing a checklist; it never applies router
configuration.
"""
from __future__ import annotations

from typing import Any

from .setup_wizard_inventory import RouterRiskAnalyzer
from .setup_wizard_operations import (
    OP_STATUS_APPLIED,
    OP_STATUS_DRY_RUN_READY,
    OP_STATUS_FAILED,
    SetupWizardOperationRepo,
    live_apply_enabled,
)


SCRIPT_STEPS = {
    "internet": "internet_script_preview",
    "vpn": "vpn_radius_script_preview",
    "vpn-radius": "vpn_radius_script_preview",
    "vpn_radius": "vpn_radius_script_preview",
    "hotspot": "hotspot_script_preview",
    "broadband": "broadband_script_preview",
}


class SetupWizardPilotDrillService:
    def __init__(
        self,
        *,
        wizard_service: Any,
        operation_repo: SetupWizardOperationRepo | None = None,
        risk_analyzer: RouterRiskAnalyzer | None = None,
    ) -> None:
        self.wizard_service = wizard_service
        self.operation_repo = operation_repo or SetupWizardOperationRepo()
        self.risk_analyzer = risk_analyzer or RouterRiskAnalyzer()

    def build_drill(
        self, *, tenant_id: int, run_id: int, step_key: str = "internet"
    ) -> dict[str, Any]:
        normalized_step = _normalize_step(step_key)
        summary = self.wizard_service.get_run_summary(tenant_id=tenant_id, run_id=run_id)
        step_index = dict(summary.get("step_index") or {})
        snapshot = summary.get("latest_router_snapshot") or None
        operations = self.operation_repo.list_for_run(
            tenant_id=tenant_id,
            run_id=run_id,
            step_key=normalized_step,
        )
        blocking: list[dict[str, str]] = []
        risks: list[dict[str, Any]] = []

        internet_step = step_index.get("internet_script_preview") or {}
        vpn_step = step_index.get("vpn_radius_script_preview") or {}
        selected_step = step_index.get(SCRIPT_STEPS[normalized_step]) or {}

        if internet_step.get("status") != "generated":
            blocking.append(_reason("internet_script_missing", "يجب توليد معاينة سكربت الإنترنت أولًا."))
        if vpn_step.get("status") != "generated":
            blocking.append(_reason("vpn_radius_script_missing", "يجب توليد معاينة سكربت الربط والمصادقة أولًا."))
        if not snapshot:
            blocking.append(_reason("inventory_missing", "لقطة جرد الراوتر مطلوبة قبل التدريب الداخلي."))
        if not operations:
            blocking.append(_reason("dry_run_missing", "عمليات التجربة الجافة مطلوبة للخطوة المحددة."))
        elif not any(op.get("status") == OP_STATUS_DRY_RUN_READY for op in operations):
            blocking.append(_reason("dry_run_not_ready", "الخطوة المحددة لا تملك عمليات جاهزة للتجربة الجافة."))

        step_input = dict(selected_step.get("input_json") or {})
        if snapshot:
            candidate_cidrs = _candidate_cidrs_for_step(normalized_step, step_input)
            risk_report = self.risk_analyzer.analyze(
                snapshot=snapshot,
                selected_wan_interface=str((summary.get("run") or {}).get("selected_wan_interface") or ""),
                candidate_cidrs=candidate_cidrs,
            )
            risks.extend(risk_report.get("warnings") or [])
            _append_interface_blocks(
                blocking=blocking,
                step_key=normalized_step,
                step_input=step_input,
                excluded_interfaces=set(risk_report.get("excluded_interfaces") or []),
            )
            for overlap in risk_report.get("subnet_overlaps") or []:
                blocking.append(
                    _reason(
                        "subnet_overlap",
                        f"الشبكة المرشحة {overlap.get('candidate')} تتداخل مع {overlap.get('existing')}.",
                    )
                )
            if int(risk_report.get("existing_nat_count") or 0) > 0:
                risks.append(
                    {
                        "code": "existing_nat_detected",
                        "message_ar": "تم العثور على قواعد ترجمة عناوين حالية؛ راجع العمليات المحددة بعناية.",
                    }
                )

        applied_ops = [op for op in operations if op.get("status") == OP_STATUS_APPLIED]
        failed_ops = [op for op in operations if op.get("status") == OP_STATUS_FAILED]
        rollback_ops = [op for op in operations if str(op.get("rollback_command") or "").strip()]
        checklist = _checklist(
            operation_count=len(operations),
            rollback_available=bool(rollback_ops),
            validation_commands=list(selected_step.get("validation_commands_json") or []),
        )
        return {
            "eligible": not blocking,
            "step_key": normalized_step,
            "blocking_reasons": blocking,
            "checklist": checklist,
            "risks": risks,
            "expected_operation_count": len(operations),
            "rollback_available": bool(rollback_ops),
            "applied_operation_count": len(applied_ops),
            "failed_operation_count": len(failed_ops),
            "verification_commands": list(selected_step.get("validation_commands_json") or []),
            "required_manual_confirmations": [
                "تم أخذ نسخة احتياطية وتصدير للراوتر",
                "تم تأكيد وجود وصول خارجي للطوارئ",
                "تم التحقق من واجهة الإنترنت",
                "تمت مراجعة خطة التراجع",
                "يبقى التطبيق الفعلي متوقفًا إلا داخل مختبر مضبوط",
            ],
            "live_apply_enabled": live_apply_enabled(),
            "next_safe_action_ar": (
                "راجع القائمة ثم نفذ dry-run فقط."
                if blocking
                else "جاهز لتدريب داخلي مضبوط: راجع النسخة الاحتياطية وخطة الرجوع قبل أي تفعيل مخبري."
            ),
        }


def _normalize_step(step_key: str) -> str:
    raw = str(step_key or "internet").strip().lower()
    if raw in {"vpn-radius", "vpn_radius"}:
        return "vpn"
    if raw not in {"internet", "vpn", "hotspot", "broadband"}:
        return "internet"
    return raw


def _reason(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _selected_interfaces(step_input: dict[str, Any]) -> set[str]:
    raw = step_input.get("selected_interfaces") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _append_interface_blocks(
    *,
    blocking: list[dict[str, str]],
    step_key: str,
    step_input: dict[str, Any],
    excluded_interfaces: set[str],
) -> None:
    if step_key not in {"hotspot", "broadband"}:
        return
    selected = _selected_interfaces(step_input)
    blocked = sorted(selected & excluded_interfaces)
    for iface in blocked:
        blocking.append(
            _reason(
                "blocked_interface_selected",
                f"الواجهة {iface} مستثناة لأنها واجهة إنترنت أو ربط خاص ولا يجوز استخدامها.",
            )
        )


def _candidate_cidrs_for_step(step_key: str, step_input: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    if step_key == "hotspot" and step_input.get("network_cidr"):
        candidates.append(str(step_input["network_cidr"]))
    if step_key == "broadband" and step_input.get("remote_pool_cidr"):
        candidates.append(str(step_input["remote_pool_cidr"]))
    return candidates


def _checklist(
    *, operation_count: int, rollback_available: bool, validation_commands: list[str]
) -> list[dict[str, Any]]:
    return [
        {"key": "backup", "label_ar": "خذ نسخة احتياطية وتصديرًا للراوتر قبل أي تجربة.", "required": True},
        {"key": "oob", "label_ar": "أكد وجود دخول خارجي أو منفذ تحكم خارج مسار الإنترنت.", "required": True},
        {"key": "wan", "label_ar": "راجع واجهة الإنترنت ولا تستخدمها لخدمات الهوتسبوت أو البرودباند.", "required": True},
        {"key": "ops", "label_ar": f"راجع عدد العمليات المتوقع: {operation_count}.", "required": True},
        {"key": "rollback", "label_ar": "راجع خطة الرجوع والعمليات الموسومة فقط.", "required": True, "available": rollback_available},
        {"key": "validation", "label_ar": "جهز أوامر التحقق بعد التنفيذ المخبري.", "required": True, "commands": validation_commands},
        {"key": "flag", "label_ar": "اترك التطبيق الفعلي مطفأ إلا في مختبر راوتر افتراضي مضبوط.", "required": True},
    ]
