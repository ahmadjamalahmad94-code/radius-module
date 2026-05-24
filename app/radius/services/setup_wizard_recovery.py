"""Safe recovery analysis and operator repair plans for Setup Wizard.

This module is intentionally read-mostly. It may record recovery events and
terminal/abandoned operator decisions, but it never mutates MikroTik or VPS
state and never performs automatic repair.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ..db.connection import db, transaction
from .setup_wizard_common import SetupWizardValidationError
from .setup_wizard_operations import SetupWizardOperationRepo
from .setup_wizard_provisioning_orchestrator import PreparedWireGuardPeerService
from .setup_wizard_router_provisioning import RouterProvisioningService
from .setup_wizard_support import mask_secrets


RECOVERY_STATES = {
    "clean_resume",
    "waiting_user_action",
    "failed_verification",
    "partial_apply",
    "stale_inventory",
    "peer_key_missing",
    "duplicate_peer_conflict",
    "subnet_conflict",
    "unsupported_recovery",
    "terminal_retired",
}


def _now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _json_dumps(value: Any) -> str:
    return json.dumps(mask_secrets(value), ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _row_to_event(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["result_json"] = _json_loads(data.get("result_json"), {})
    return mask_secrets(data)


class SetupWizardRecoveryEventRepo:
    def record(
        self,
        *,
        tenant_id: int,
        run_id: int,
        event_type: str,
        action: str,
        reason: str = "",
        registry_id: int | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO setup_wizard_recovery_events (
                  wizard_run_id, tenant_id, registry_id, event_type,
                  reason, action, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    int(tenant_id),
                    int(registry_id) if registry_id else None,
                    str(event_type or "recovery_event")[:120],
                    str(reason or "")[:2000],
                    str(action or "")[:120],
                    _json_dumps(result or {}),
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM setup_wizard_recovery_events WHERE id=?",
                (int(cur.lastrowid),),
            ).fetchone()
        return _row_to_event(row)

    def list_for_run(self, *, tenant_id: int, run_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM setup_wizard_recovery_events
            WHERE tenant_id=? AND wizard_run_id=?
            ORDER BY id DESC LIMIT ?
            """,
            (int(tenant_id), int(run_id), int(limit)),
        ).fetchall()
        return [_row_to_event(row) for row in rows]


class SetupWizardRecoveryAnalyzer:
    def __init__(self, *, stale_minutes: int = 120) -> None:
        self.stale_minutes = int(stale_minutes)

    def analyze(
        self,
        *,
        run: dict[str, Any],
        steps: list[dict[str, Any]],
        registry: dict[str, Any] | None = None,
        prepared_peer: dict[str, Any] | None = None,
        operations: list[dict[str, Any]] | None = None,
        snapshot: dict[str, Any] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        operations = operations or []
        diagnostics = diagnostics or []
        problems: list[dict[str, Any]] = []
        recommended_actions: list[dict[str, Any]] = []
        safe_actions: list[str] = ["support_bundle", "resume"]
        blocked_actions: list[dict[str, str]] = []
        recovery_state = "clean_resume"
        severity = "low"

        registry_state = str((registry or {}).get("lifecycle_state") or "")
        registry_status = str((registry or {}).get("status") or "")
        run_status = str((run or {}).get("status") or "")
        if "retired" in {registry_state, registry_status, run_status}:
            recovery_state = "terminal_retired"
            severity = "terminal"
            problems.append(_problem("router_retired", "تم إيقاف هذا الراوتر", "هذا المسار أصبح نهائيًا ولا يمكن استكماله بشكل طبيعي."))
            blocked_actions.append({"action": "resume", "reason": "router_retired"})
            return self._result(
                recovery_state,
                severity,
                problems,
                recommended_actions=[_action("review_support_bundle", "راجع حزمة الدعم قبل فتح مسار جديد")],
                safe_actions=["support_bundle"],
                blocked_actions=blocked_actions,
                registry=registry,
                prepared_peer=prepared_peer,
            )

        applied_ops = [op for op in operations if str(op.get("status") or "") == "applied"]
        failed_ops = [op for op in operations if str(op.get("status") or "") == "failed"]
        if applied_ops and failed_ops:
            recovery_state = "partial_apply"
            severity = "high"
            problems.append(_problem("partial_apply_detected", "تم تطبيق جزء من العمليات ثم فشل جزء آخر", "يجب مراجعة العمليات المطبقة والرجوع للـ rollback المتاح قبل المتابعة."))
            recommended_actions.append(_action("review_rollback", "راجع خطة الرجوع للعمليات التي تحمل وسم HOBERADIUS فقط"))
            safe_actions.extend(["repair_plan", "retry_verification"])

        risk = (snapshot or {}).get("risk_report") or {}
        if _has_subnet_conflict(risk):
            recovery_state = "subnet_conflict"
            severity = _max_severity(severity, "high")
            problems.append(_problem("subnet_conflict", "يوجد تداخل في الشبكات", "الشبكة المقترحة تتداخل مع WAN أو VPN أو شبكة موجودة على الراوتر."))
            recommended_actions.append(_action("regenerate_plan", "أعد توليد الخطة بنفس الحجز مع اختيار مدى مختلف"))

        if snapshot and _is_stale(str(snapshot.get("created_at") or ""), self.stale_minutes):
            if recovery_state == "clean_resume":
                recovery_state = "stale_inventory"
            severity = _max_severity(severity, "medium")
            problems.append(_problem("stale_inventory", "معلومات الراوتر قديمة", "اجمع inventory جديد قبل التخطيط أو dry-run."))
            recommended_actions.append(_action("refresh_inventory", "حدّث قراءة الراوتر أو الصق مخرجات inventory جديدة"))

        peer_status = str((prepared_peer or {}).get("status") or "")
        public_key_masked = str((prepared_peer or {}).get("router_public_key_masked") or "")
        if prepared_peer and peer_status in {"prepared", "waiting_router_key"} and not public_key_masked:
            if recovery_state == "clean_resume":
                recovery_state = "peer_key_missing"
            severity = _max_severity(severity, "medium")
            problems.append(_problem("peer_key_missing", "مفتاح الراوتر غير موجود", "يجب لصق public key الناتج من MikroTik قبل تجهيز peer على السيرفر."))
            recommended_actions.append(_action("submit_router_key", "الصق public key للراوتر ثم أعد فحص الربط"))

        duplicate_codes = {"duplicate_peer_conflict", "duplicate_public_key", "duplicate_allowed_ip"}
        if any(str(item.get("code") or "") in duplicate_codes for item in diagnostics):
            recovery_state = "duplicate_peer_conflict"
            severity = _max_severity(severity, "high")
            problems.append(_problem("duplicate_peer_conflict", "تعارض في WireGuard peer", "يوجد public key أو allowed IP مستخدم مسبقًا. لا تعيد الإصدار قبل مراجعة الحجز."))
            recommended_actions.append(_action("review_peer_collision", "راجع الحجز الحالي وتأكد أن المفتاح ليس مستخدمًا لراوتر آخر"))

        failed_steps = [step for step in steps if str(step.get("status") or "") == "failed"]
        vpn_failed = any("vpn" in str(step.get("step_key") or "") for step in failed_steps)
        if failed_steps and recovery_state not in {
            "partial_apply",
            "subnet_conflict",
            "duplicate_peer_conflict",
            "terminal_retired",
        }:
            recovery_state = "failed_verification" if vpn_failed else "waiting_user_action"
            severity = _max_severity(severity, "high" if vpn_failed else "medium")
            problems.append(_problem("verification_failed", "فشل تحقق سابق", "يمكن إعادة التحقق بعد معالجة السبب الظاهر في التشخيص."))
            recommended_actions.append(_action("retry_verification", "أعد التحقق بعد لصق المخرجات أو تحديث التشخيص"))
            safe_actions.append("retry_verification")

        if recovery_state == "clean_resume":
            recommended_actions.append(_action("continue_current_step", "استكمل من آخر خطوة آمنة"))
        elif recovery_state not in {"partial_apply", "failed_verification"}:
            safe_actions.append("repair_plan")

        return self._result(
            recovery_state,
            severity,
            problems,
            recommended_actions=recommended_actions,
            safe_actions=safe_actions,
            blocked_actions=blocked_actions,
            registry=registry,
            prepared_peer=prepared_peer,
        )

    def _result(
        self,
        recovery_state: str,
        severity: str,
        problems: list[dict[str, Any]],
        *,
        recommended_actions: list[dict[str, Any]],
        safe_actions: list[str],
        blocked_actions: list[dict[str, str]],
        registry: dict[str, Any] | None,
        prepared_peer: dict[str, Any] | None,
    ) -> dict[str, Any]:
        can_resume = recovery_state not in {"terminal_retired", "unsupported_recovery"}
        peer_applied = str((prepared_peer or {}).get("status") or "") in {"applied", "verified_handshake"}
        can_reissue_peer = bool(prepared_peer) and not peer_applied and recovery_state in {
            "peer_key_missing",
            "duplicate_peer_conflict",
            "waiting_user_action",
        }
        result = {
            "recovery_state": recovery_state,
            "severity": severity,
            "problems": problems,
            "recommended_actions": recommended_actions,
            "safe_actions": sorted(set(safe_actions)),
            "blocked_actions": blocked_actions,
            "can_resume": can_resume,
            "can_regenerate_script": can_resume and str((registry or {}).get("status") or "") != "retired",
            "can_reissue_peer": can_reissue_peer,
            "can_retry_verification": recovery_state in {"failed_verification", "partial_apply"},
            "can_retire_router": bool(registry) and str((registry or {}).get("status") or "") != "retired",
        }
        return mask_secrets(result)


class SetupWizardRecoveryService:
    def __init__(
        self,
        *,
        wizard_service: Any,
        operation_repo: SetupWizardOperationRepo | None = None,
        registry: RouterProvisioningService | None = None,
        peer_service: PreparedWireGuardPeerService | None = None,
        event_repo: SetupWizardRecoveryEventRepo | None = None,
        analyzer: SetupWizardRecoveryAnalyzer | None = None,
    ) -> None:
        self.wizard_service = wizard_service
        self.operation_repo = operation_repo or SetupWizardOperationRepo()
        self.registry = registry or RouterProvisioningService()
        self.peer_service = peer_service or PreparedWireGuardPeerService()
        self.event_repo = event_repo or SetupWizardRecoveryEventRepo()
        self.analyzer = analyzer or SetupWizardRecoveryAnalyzer()

    def analyze(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        ctx = self._context(tenant_id=tenant_id, run_id=run_id)
        analysis = self.analyzer.analyze(**ctx)
        analysis["events"] = self.event_repo.list_for_run(tenant_id=tenant_id, run_id=run_id)
        analysis["next_safe_step"] = self._next_safe_step(ctx=ctx, analysis=analysis)
        return mask_secrets(analysis)

    def resume(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        analysis = self.analyze(tenant_id=tenant_id, run_id=run_id)
        if not analysis.get("can_resume"):
            return {"status": "blocked", "reason": analysis["recovery_state"], "analysis": analysis}
        return {
            "status": "ready",
            "next_safe_step": analysis.get("next_safe_step"),
            "analysis": analysis,
            "message_ar": "يمكنك الاستكمال من آخر خطوة آمنة.",
        }

    def retry_verification(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str = "",
        mode: str = "pasted_output",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        step = self._normalize_verification_step(step_key)
        if not step:
            analysis = self.analyze(tenant_id=tenant_id, run_id=run_id)
            step = self._failed_verification_step(analysis, tenant_id=tenant_id, run_id=run_id)
        if not step:
            return {"status": "blocked", "reason": "verification_step_required"}
        dispatch = {
            "internet": self.wizard_service.verify_internet,
            "vpn_radius": self.wizard_service.verify_vpn_radius,
            "hotspot": self.wizard_service.verify_hotspot,
            "broadband": self.wizard_service.verify_broadband,
        }
        result = dispatch[step](tenant_id=tenant_id, run_id=run_id, mode=mode, payload=payload or {})
        registry = self.registry.latest_for_run(tenant_id=tenant_id, wizard_run_id=run_id)
        self.event_repo.record(
            tenant_id=tenant_id,
            run_id=run_id,
            registry_id=int(registry["id"]) if registry else None,
            event_type="retry_verification",
            action=step,
            result=result,
        )
        return mask_secrets({"status": "completed", "verification_step": step, "result": result})

    def regenerate_script(self, *, tenant_id: int, run_id: int, step_key: str) -> dict[str, Any]:
        normalized = str(step_key or "vpn_radius").strip().lower()
        if normalized not in {"vpn_radius", "vpn_radius_script_preview"}:
            return {
                "status": "blocked",
                "reason": "unsupported_regeneration_step",
                "message_ar": "إعادة التوليد الآمنة مفعلة حاليًا لسكربت VPN/RADIUS فقط.",
            }
        before = self.registry.latest_for_run(tenant_id=tenant_id, wizard_run_id=run_id)
        payload = {
            "router_label": (before or {}).get("router_label", ""),
            "router_identity": (before or {}).get("router_identity", ""),
            "recovery_regeneration": True,
        }
        plan = self.wizard_service.generate_vpn_radius_script(
            tenant_id=tenant_id,
            run_id=run_id,
            payload=payload,
        )
        after = self.registry.latest_for_run(tenant_id=tenant_id, wizard_run_id=run_id)
        preserved = bool(before and after and int(before["id"]) == int(after["id"]))
        event = self.event_repo.record(
            tenant_id=tenant_id,
            run_id=run_id,
            registry_id=int(after["id"]) if after else None,
            event_type="regenerate_script",
            action="vpn_radius",
            result={"allocation_preserved": preserved, "plan": plan},
        )
        return mask_secrets(
            {
                "status": "generated",
                "allocation_preserved": preserved,
                "plan": plan,
                "event": event,
            }
        )

    def reissue_router_credentials(self, *, tenant_id: int, run_id: int, reason: str = "") -> dict[str, Any]:
        ctx = self._context(tenant_id=tenant_id, run_id=run_id)
        peer = ctx.get("prepared_peer") or {}
        if str(peer.get("status") or "") in {"applied", "verified_handshake"}:
            return {"status": "blocked", "reason": "peer_already_applied"}
        return {
            "status": "plan_only",
            "reason": reason,
            "message_ar": "إعادة إصدار بيانات الراوتر تحتاج مراجعة مشغل قبل إنشاء حجز جديد.",
        }

    def abandon_step(
        self,
        *,
        tenant_id: int,
        run_id: int,
        step_key: str,
        reason: str,
    ) -> dict[str, Any]:
        if not str(reason or "").strip():
            raise SetupWizardValidationError("abandon reason is required")
        registry = self.registry.latest_for_run(tenant_id=tenant_id, wizard_run_id=run_id)
        event = self.event_repo.record(
            tenant_id=tenant_id,
            run_id=run_id,
            registry_id=int(registry["id"]) if registry else None,
            event_type="abandon_step",
            action=str(step_key or "current_step"),
            reason=reason,
            result={"step_key": step_key},
        )
        return {"status": "recorded", "event": event}

    def retire_router(self, *, tenant_id: int, run_id: int, reason: str) -> dict[str, Any]:
        if not str(reason or "").strip():
            raise SetupWizardValidationError("retire reason is required")
        registry = self.registry.latest_for_run(tenant_id=tenant_id, wizard_run_id=run_id)
        if not registry:
            raise SetupWizardValidationError("router provisioning reservation not found")
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE router_provisioning_registry
                SET status='retired', lifecycle_state='retired',
                    failure_reason=?, retired_at=?, updated_at=?, lifecycle_updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (str(reason)[:2000], now, now, now, int(tenant_id), int(registry["id"])),
            )
            conn.execute(
                """
                UPDATE setup_wizard_runs
                SET status='retired', last_error=?, updated_at=?
                WHERE tenant_id=? AND id=?
                """,
                (str(reason)[:2000], now, int(tenant_id), int(run_id)),
            )
        event = self.event_repo.record(
            tenant_id=tenant_id,
            run_id=run_id,
            registry_id=int(registry["id"]),
            event_type="retire_router",
            action="retire",
            reason=reason,
            result={"ip_reuse": "blocked_until_manual_review"},
        )
        return {"status": "retired", "event": event, "ip_reuse": "blocked_until_manual_review"}

    def repair_plan(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        analysis = self.analyze(tenant_id=tenant_id, run_id=run_id)
        state = str(analysis.get("recovery_state") or "")
        steps = {
            "partial_apply": [
                "راجع العمليات المطبقة فقط.",
                "استخدم rollback للعمليات الموسومة HOBERADIUS بعد dry-run.",
                "أعد التحقق بعد الرجوع أو الإصلاح اليدوي.",
            ],
            "stale_inventory": ["اجمع inventory جديد.", "أعد تشغيل dry-run قبل أي تطبيق مختبري."],
            "peer_key_missing": ["الصق public key للراوتر.", "نفذ dry-run للـ server peer ثم تحقق."],
            "failed_verification": ["افتح التشخيص.", "عالج السبب المحتمل.", "أعد التحقق بالمخرجات الجديدة."],
        }.get(state, ["استكمل من الخطوة الآمنة المقترحة."])
        return {"status": "plan_ready", "recovery_state": state, "steps": steps, "analysis": analysis}

    def _context(self, *, tenant_id: int, run_id: int) -> dict[str, Any]:
        summary = self.wizard_service.get_run_summary(tenant_id=tenant_id, run_id=run_id)
        registry = summary.get("router_provisioning")
        peer = summary.get("prepared_wireguard_peer")
        diagnostics = []
        for step in summary.get("steps") or []:
            result = step.get("verification_result_json") or {}
            diagnostics.extend(result.get("diagnostics") or [])
        return {
            "run": summary.get("run") or {},
            "steps": summary.get("steps") or [],
            "registry": registry,
            "prepared_peer": peer,
            "operations": summary.get("operations") or [],
            "snapshot": summary.get("latest_router_snapshot"),
            "diagnostics": diagnostics,
        }

    def _next_safe_step(self, *, ctx: dict[str, Any], analysis: dict[str, Any]) -> str:
        state = str(analysis.get("recovery_state") or "")
        if state == "terminal_retired":
            return ""
        if state == "peer_key_missing":
            return "router_public_key"
        if state == "failed_verification":
            return self._failed_step_key(ctx.get("steps") or []) or str((ctx.get("run") or {}).get("current_step") or "")
        if state == "stale_inventory":
            return "inventory"
        return str((ctx.get("run") or {}).get("current_step") or "welcome")

    def _failed_verification_step(self, analysis: dict[str, Any], *, tenant_id: int, run_id: int) -> str:
        summary = self.wizard_service.get_run_summary(tenant_id=tenant_id, run_id=run_id)
        failed = self._failed_step_key(summary.get("steps") or [])
        return self._normalize_verification_step(failed)

    @staticmethod
    def _failed_step_key(steps: list[dict[str, Any]]) -> str:
        for step in steps:
            if str(step.get("status") or "") == "failed":
                return str(step.get("step_key") or "")
        return ""

    @staticmethod
    def _normalize_verification_step(step_key: str) -> str:
        value = str(step_key or "").strip().lower()
        if "internet" in value:
            return "internet"
        if "vpn" in value or "radius" in value:
            return "vpn_radius"
        if "hotspot" in value:
            return "hotspot"
        if "broadband" in value or "pppoe" in value:
            return "broadband"
        return ""


def _problem(code: str, title_ar: str, explanation_ar: str) -> dict[str, str]:
    return {"code": code, "title_ar": title_ar, "explanation_ar": explanation_ar}


def _action(action: str, title_ar: str) -> dict[str, str]:
    return {"action": action, "title_ar": title_ar}


def _max_severity(current: str, candidate: str) -> str:
    order = {"low": 1, "medium": 2, "high": 3, "terminal": 4}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def _has_subnet_conflict(risk: dict[str, Any]) -> bool:
    if risk.get("subnet_overlaps"):
        return True
    warnings = risk.get("warnings") or []
    return any("subnet" in str(item.get("code") or "") for item in warnings if isinstance(item, dict))


def _is_stale(created_at: str, stale_minutes: int) -> bool:
    if not created_at:
        return False
    try:
        parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return False
    return datetime.utcnow() - parsed > timedelta(minutes=stale_minutes)
