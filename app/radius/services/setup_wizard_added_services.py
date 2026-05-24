"""Added services catalog and preview planner for Setup Wizard.

This layer intentionally delegates to existing Network Policy Center
and Site Exit planners. It does not apply scripts, mutate routers, or
invent duplicate networking logic.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

from . import npc_script_renderer
from . import npc_walled_garden_planner
from . import npc_web_block_planner
from . import site_exit_script_planner
from . import site_exit_script_renderer


@dataclass(frozen=True)
class AddedService:
    key: str
    title_ar: str
    description_ar: str
    risk_level: str
    status: str
    required_inputs: list[str]
    planner_delegate: str
    verification_delegate: str
    rollback_capability: str

    @property
    def supported(self) -> bool:
        return self.status == "supported"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title_ar": self.title_ar,
            "description_ar": self.description_ar,
            "risk_level": self.risk_level,
            "status": self.status,
            "supported": self.supported,
            "required_inputs": self.required_inputs,
            "planner_delegate": self.planner_delegate,
            "verification_delegate": self.verification_delegate,
            "rollback_capability": self.rollback_capability,
        }


class AddedServicesCatalog:
    _ALIASES = {
        "web_block": "block_sites",
        "site_exit": "site_exit_public_ip",
    }

    def services(self) -> list[AddedService]:
        return [
            AddedService(
                key="anti_sharing",
                title_ar="منع مشاركة الاتصال",
                description_ar=(
                    "هذه الخدمة تحتاج محرك كشف مستقر قبل إدخالها في معالج الإعداد."
                ),
                risk_level="high",
                status="not_supported_yet",
                required_inputs=[],
                planner_delegate="not_supported_yet",
                verification_delegate="manual_future",
                rollback_capability="none",
            ),
            AddedService(
                key="walled_garden",
                title_ar="مواقع مفتوحة بدون تسجيل دخول",
                description_ar=(
                    "يسمح بمواقع محددة قبل تسجيل دخول Hotspot عبر مخطط NPC الحالي."
                ),
                risk_level="medium",
                status="partial",
                required_inputs=["domains"],
                planner_delegate="npc_walled_garden_planner",
                verification_delegate="hotspot_walled_garden_print",
                rollback_capability="scoped_npc_comment",
            ),
            AddedService(
                key="block_sites",
                title_ar="حجب مواقع",
                description_ar=(
                    "يخطط لحجب وجهات محددة عبر مخطط NPC web-block الحالي."
                ),
                risk_level="medium",
                status="partial",
                required_inputs=["domains"],
                planner_delegate="npc_web_block_planner",
                verification_delegate="firewall_address_list_print",
                rollback_capability="scoped_npc_comment",
            ),
            AddedService(
                key="web_block",
                title_ar="حجب مواقع",
                description_ar="Alias قديم لخدمة block_sites للحفاظ على التوافق.",
                risk_level="medium",
                status="partial",
                required_inputs=["domains"],
                planner_delegate="npc_web_block_planner",
                verification_delegate="firewall_address_list_print",
                rollback_capability="scoped_npc_comment",
            ),
            AddedService(
                key="site_exit_public_ip",
                title_ar="تغيير Public IP / Site Exit",
                description_ar=(
                    "يخطط لتوجيه وجهات مختارة عبر نفق VPS باستخدام VX2 الحالي."
                ),
                risk_level="high",
                status="partial",
                required_inputs=["destinations", "wireguard_interface_name"],
                planner_delegate="site_exit_script_planner",
                verification_delegate="site_exit_policy_preview",
                rollback_capability="scoped_site_exit_comment",
            ),
            AddedService(
                key="site_exit",
                title_ar="تغيير Public IP / Site Exit",
                description_ar="Alias قديم لخدمة site_exit_public_ip للحفاظ على التوافق.",
                risk_level="high",
                status="partial",
                required_inputs=["destinations", "wireguard_interface_name"],
                planner_delegate="site_exit_script_planner",
                verification_delegate="site_exit_policy_preview",
                rollback_capability="scoped_site_exit_comment",
            ),
        ]

    def presets(self) -> dict[str, dict[str, Any]]:
        return {
            "isp_basic": {
                "title_ar": "ISP Basic",
                "services": ["block_sites", "walled_garden"],
                "inputs": {
                    "block_sites": {"domains": ["example-bad-site.test"]},
                    "walled_garden": {"domains": ["hoberadius.local"]},
                },
            },
            "hotel_cafe": {
                "title_ar": "Hotel / Cafe",
                "services": ["walled_garden", "block_sites"],
                "inputs": {
                    "walled_garden": {"domains": ["hotel.local", "payment.local"]},
                    "block_sites": {"domains": ["malware.test"]},
                },
            },
            "school": {
                "title_ar": "School",
                "services": ["block_sites", "walled_garden"],
                "inputs": {
                    "block_sites": {"domains": ["adult.example", "games.example"]},
                    "walled_garden": {"domains": ["school.edu", "library.school"]},
                },
            },
            "gaming_center": {
                "title_ar": "Gaming Center",
                "services": ["site_exit_public_ip", "block_sites"],
                "inputs": {
                    "site_exit_public_ip": {
                        "destinations": ["speedtest.net"],
                        "wireguard_interface_name": "hr-wg",
                    },
                    "block_sites": {"domains": ["malware.test"]},
                },
            },
        }

    def normalize_key(self, key: str) -> str:
        raw = (key or "").strip()
        return self._ALIASES.get(raw, raw)

    def get(self, key: str) -> AddedService | None:
        normalized = self.normalize_key(key)
        return next((svc for svc in self.services() if svc.key == normalized), None)


class AddedServicesPlanner:
    def __init__(self, catalog: AddedServicesCatalog | None = None) -> None:
        self.catalog = catalog or AddedServicesCatalog()

    def catalog_payload(self) -> dict[str, Any]:
        return {
            "services": [svc.to_dict() for svc in self.catalog.services()],
            "presets": self.catalog.presets(),
        }

    def plan(
        self,
        *,
        wizard_run_id: int,
        service_key: str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        svc = self.catalog.get(service_key)
        if not svc:
            return self._rejected(service_key=service_key, diagnostic="unknown_service")
        data = inputs or {}
        if svc.status == "not_supported_yet":
            return self._unsupported(svc)

        missing = [name for name in svc.required_inputs if not self._has_input(data, name)]
        if missing:
            return {
                **self._base(svc),
                "plan_status": "blocked",
                "supported": False,
                "warnings": [f"missing required inputs: {', '.join(missing)}"],
                "diagnostics": [
                    {
                        "code": "missing_inputs",
                        "arabic_title": "مدخلات ناقصة",
                        "explanation_ar": "أكمل الحقول المطلوبة قبل توليد الخطة.",
                    }
                ],
            }

        if svc.key == "walled_garden":
            return self._plan_walled_garden(wizard_run_id=wizard_run_id, svc=svc, inputs=data)
        if svc.key == "block_sites":
            return self._plan_block_sites(wizard_run_id=wizard_run_id, svc=svc, inputs=data)
        if svc.key == "site_exit_public_ip":
            return self._plan_site_exit(wizard_run_id=wizard_run_id, svc=svc, inputs=data)
        return self._unsupported(svc)

    def dry_run(
        self,
        *,
        wizard_run_id: int,
        service_key: str,
        inputs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        plan = self.plan(
            wizard_run_id=wizard_run_id,
            service_key=service_key,
            inputs=inputs or {},
        )
        if plan.get("plan_status") not in {"preview", "partial"}:
            return {
                "status": "blocked",
                "blocked_reason": plan.get("plan_status", "not_ready"),
                "plan": plan,
            }
        commands = [
            line for line in str(plan.get("script_preview") or "").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return {
            "status": "dry_run_ready",
            "operation_count": len(commands),
            "operations": [
                {"order": idx + 1, "command_preview": command, "status": "planned"}
                for idx, command in enumerate(commands)
            ],
            "warnings": plan.get("warnings", []),
            "plan": plan,
        }

    def verify_guidance(self, *, service_key: str) -> dict[str, Any]:
        svc = self.catalog.get(service_key)
        if not svc:
            return {"status": "blocked", "diagnostics": ["unknown_service"], "gate_unlocked": False}
        return {
            "status": "blocked",
            "gate_unlocked": False,
            "diagnostics": [
                {
                    "code": "manual_verification_required",
                    "arabic_title": "التحقق يدوي في هذه المرحلة",
                    "explanation_ar": (
                        "راجع أوامر التحقق الخاصة بالخدمة بعد تنفيذ السكربت يدويًا أو عبر مسار المختبر المحروس."
                    ),
                }
            ],
            "validation_commands": self._validation_commands(svc.key),
        }

    def _plan_walled_garden(
        self, *, wizard_run_id: int, svc: AddedService, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        policy_id = self._policy_id(wizard_run_id, 11)
        domains = self._list(inputs.get("domains"))
        policy = {"id": policy_id, "hotspot_profile": inputs.get("hotspot_profile", "")}
        entries = [
            {
                "id": idx,
                "entry_type": "dst_host",
                "value": value,
                "normalized_value": value,
                "status": "active",
            }
            for idx, value in enumerate(domains, start=1)
        ]
        plan = npc_walled_garden_planner.plan(policy, entries)
        plan = self._tag_npc_plan(plan, self._tag(wizard_run_id, svc.key))
        script = npc_script_renderer.render_forward_script(plan)
        rollback = npc_script_renderer.render_rollback_script(plan)
        return self._planned_response(
            svc=svc,
            script=script,
            rollback=rollback,
            summary=npc_script_renderer.script_summary(plan),
            warnings=list(plan.warnings) + [
                "partial: Setup Wizard delegates to NPC planner; persisted NPC policy creation remains outside this flow."
            ],
            validation=self._validation_commands(svc.key),
        )

    def _plan_block_sites(
        self, *, wizard_run_id: int, svc: AddedService, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        policy_id = self._policy_id(wizard_run_id, 22)
        domains = self._list(inputs.get("domains"))
        policy = {"id": policy_id, "fail_open": 1}
        targets = [
            {
                "id": idx,
                "target_type": "domain",
                "category": inputs.get("category", "custom"),
                "value": value,
                "normalized_value": value,
                "status": "active",
            }
            for idx, value in enumerate(domains, start=1)
        ]
        plan = npc_web_block_planner.plan(policy, targets)
        plan = self._tag_npc_plan(plan, self._tag(wizard_run_id, svc.key))
        script = npc_script_renderer.render_forward_script(plan)
        rollback = npc_script_renderer.render_rollback_script(plan)
        return self._planned_response(
            svc=svc,
            script=script,
            rollback=rollback,
            summary=npc_script_renderer.script_summary(plan),
            warnings=list(plan.warnings) + [
                "partial: Setup Wizard delegates to NPC planner; categories and persistence stay in NPC."
            ],
            validation=self._validation_commands(svc.key),
        )

    def _plan_site_exit(
        self, *, wizard_run_id: int, svc: AddedService, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        policy_id = self._policy_id(wizard_run_id, 33)
        destinations = self._list(inputs.get("destinations"))
        policy = {
            "id": policy_id,
            "fail_mode": inputs.get("fail_mode", "block_when_vps_down"),
            "include_subdomains": 1 if inputs.get("include_subdomains") else 0,
            "include_router_output": 0,
        }
        exit_node = {
            "enabled": 1,
            "wireguard_interface_name": inputs.get("wireguard_interface_name", ""),
        }
        targets = [
            {
                "id": idx,
                "group_name": inputs.get("group_name", "wizard"),
                "target_type": "domain",
                "value": value,
                "normalized_value": value,
                "status": "active",
                "include_subdomains": 1 if inputs.get("include_subdomains") else 0,
            }
            for idx, value in enumerate(destinations, start=1)
        ]
        plan = site_exit_script_planner.build_plan(
            policy=policy,
            exit_node=exit_node,
            targets=targets,
            wan_interface_list=inputs.get("wan_interface_list", "WAN"),
            enable_dns_helper=bool(inputs.get("enable_dns_helper", False)),
        )
        plan = self._tag_site_exit_plan(plan, self._tag(wizard_run_id, svc.key))
        script = site_exit_script_renderer.render_forward_script(plan)
        rollback = site_exit_script_renderer.render_rollback_script(plan)
        status = "partial" if plan.can_apply else "blocked"
        return {
            **self._base(svc),
            "plan_status": status,
            "supported": status == "partial",
            "script_preview": script,
            "rollback_notes": rollback or "No rollback generated because the site-exit plan is blocked.",
            "warnings": list(plan.warnings) + [
                "partial: Site Exit requires existing VX2 policy/node lifecycle before production use."
            ],
            "validation_commands": self._validation_commands(svc.key),
            "diagnostics": list(plan.blocking_errors),
            "summary": site_exit_script_renderer.script_summary(plan),
        }

    def _planned_response(
        self,
        *,
        svc: AddedService,
        script: str,
        rollback: str,
        summary: dict[str, Any],
        warnings: list[str],
        validation: list[str],
    ) -> dict[str, Any]:
        return {
            **self._base(svc),
            "plan_status": "partial",
            "supported": True,
            "script_preview": script,
            "warnings": warnings,
            "required_inputs": svc.required_inputs,
            "validation_commands": validation,
            "rollback_notes": rollback,
            "diagnostics": [],
            "summary": summary,
        }

    def _base(self, svc: AddedService) -> dict[str, Any]:
        return {
            "service_key": svc.key,
            "title_ar": svc.title_ar,
            "risk_level": svc.risk_level,
            "status": svc.status,
            "required_inputs": svc.required_inputs,
            "planner_delegate": svc.planner_delegate,
            "verification_delegate": svc.verification_delegate,
            "rollback_capability": svc.rollback_capability,
        }

    def _unsupported(self, svc: AddedService) -> dict[str, Any]:
        return {
            **self._base(svc),
            "plan_status": "not_supported_yet",
            "supported": False,
            "script_preview": "",
            "warnings": ["هذه الخدمة تحتاج تفعيلًا لاحقًا ولا يوجد مخطط آمن لها الآن."],
            "validation_commands": [],
            "rollback_notes": "لا يوجد rollback لأن الخدمة غير مدعومة في هذا المسار.",
            "diagnostics": [
                {
                    "code": "not_supported_yet",
                    "arabic_title": "غير مدعومة حاليًا",
                    "explanation_ar": "لن يتم توليد سكربت وهمي لهذه الخدمة.",
                }
            ],
        }

    def _rejected(self, *, service_key: str, diagnostic: str) -> dict[str, Any]:
        return {
            "service_key": service_key,
            "plan_status": "rejected",
            "supported": False,
            "warnings": ["Unknown added service key."],
            "required_inputs": [],
            "validation_commands": [],
            "rollback_notes": "",
            "diagnostics": [diagnostic],
        }

    def _tag_npc_plan(self, plan: npc_script_renderer.ScriptPlan, tag: str) -> npc_script_renderer.ScriptPlan:
        return replace(
            plan,
            cleanup_ops=self._tag_npc_commands(plan.cleanup_ops, tag),
            address_list_ops=self._tag_npc_commands(plan.address_list_ops, tag),
            filter_ops=self._tag_npc_commands(plan.filter_ops, tag),
            walled_garden_ops=self._tag_npc_commands(plan.walled_garden_ops, tag),
            scheduler_ops=self._tag_npc_commands(plan.scheduler_ops, tag),
            rollback_ops=plan.rollback_ops,
        )

    def _tag_npc_commands(
        self, commands: Iterable[npc_script_renderer.PlanCommand], tag: str
    ) -> tuple[npc_script_renderer.PlanCommand, ...]:
        tagged = []
        for command in commands:
            if command.kind == "add":
                attrs = dict(command.attrs)
                attrs["comment"] = self._append_tag(attrs.get("comment", ""), tag)
                tagged.append(replace(command, attrs=attrs))
            else:
                tagged.append(command)
        return tuple(tagged)

    def _tag_site_exit_plan(
        self,
        plan: site_exit_script_planner.ScriptPlan,
        tag: str,
    ) -> site_exit_script_planner.ScriptPlan:
        return replace(
            plan,
            cleanup_ops=self._tag_site_exit_commands(plan.cleanup_ops, tag),
            routing_table_ops=self._tag_site_exit_commands(plan.routing_table_ops, tag),
            address_list_ops=self._tag_site_exit_commands(plan.address_list_ops, tag),
            dns_ops=self._tag_site_exit_commands(plan.dns_ops, tag),
            route_ops=self._tag_site_exit_commands(plan.route_ops, tag),
            mangle_ops=self._tag_site_exit_commands(plan.mangle_ops, tag),
            nat_ops=self._tag_site_exit_commands(plan.nat_ops, tag),
            firewall_filter_ops=self._tag_site_exit_commands(plan.firewall_filter_ops, tag),
            rollback_ops=plan.rollback_ops,
        )

    def _tag_site_exit_commands(
        self, commands: Iterable[site_exit_script_planner.PlanCommand], tag: str
    ) -> tuple[site_exit_script_planner.PlanCommand, ...]:
        tagged = []
        for command in commands:
            if command.kind == "add":
                attrs = dict(command.attrs)
                attrs["comment"] = self._append_tag(attrs.get("comment", ""), tag)
                tagged.append(replace(command, attrs=attrs))
            else:
                tagged.append(command)
        return tuple(tagged)

    def _validation_commands(self, service_key: str) -> list[str]:
        if service_key == "walled_garden":
            return ["/ip hotspot walled-garden print detail", "/ip hotspot walled-garden ip print detail"]
        if service_key == "block_sites":
            return ["/ip firewall address-list print detail", "/ip firewall filter print detail"]
        if service_key == "site_exit_public_ip":
            return ["/ip route print detail", "/ip firewall mangle print detail", "/routing table print detail"]
        return []

    def _tag(self, wizard_run_id: int, service_key: str) -> str:
        return f"HOBERADIUS_SETUP:{int(wizard_run_id)}:added-service:{service_key}"

    def _policy_id(self, wizard_run_id: int, offset: int) -> int:
        return int(wizard_run_id) * 100 + offset

    def _has_input(self, inputs: dict[str, Any], key: str) -> bool:
        value = inputs.get(key)
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, (list, tuple, set)):
            return bool(value)
        return value is not None

    def _list(self, value: Any) -> list[str]:
        if isinstance(value, str):
            raw = value.replace("\n", ",").split(",")
        elif isinstance(value, (list, tuple, set)):
            raw = list(value)
        else:
            raw = []
        return [str(item).strip() for item in raw if str(item).strip()]

    def _append_tag(self, comment: str, tag: str) -> str:
        if tag in comment:
            return comment
        return f"{comment} {tag}".strip()


__all__ = ["AddedService", "AddedServicesCatalog", "AddedServicesPlanner"]
