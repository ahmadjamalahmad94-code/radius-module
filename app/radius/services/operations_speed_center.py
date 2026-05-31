"""Operations Center and dry-run Speed Control Center."""
from __future__ import annotations

import json
import re
from typing import Any

from ..db.connection import db
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import EventService


class OperationsSpeedError(ValueError):
    """Safe validation error for operations/speed center."""


SPEED_PRESETS: dict[str, dict[str, Any]] = {
    "normal": {"label": "Normal", "multiplier": 1.0, "vip_protected": True},
    "pressure": {"label": "Pressure mode", "multiplier": 0.7, "vip_protected": True},
    "night": {"label": "Night mode", "multiplier": 1.25, "vip_protected": True},
    "emergency": {"label": "Emergency", "multiplier": 0.4, "vip_protected": True},
    "vip_protected": {"label": "VIP protected", "multiplier": 1.0, "vip_protected": True},
}


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _load(raw: Any, default: Any = None) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return {} if default is None else default


class OperationsSpeedCenterService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.events = EventService()

    def operations_snapshot(self) -> dict[str, Any]:
        return {
            "online_users": self._online_count(),
            "active_sessions": self.active_sessions(limit=20),
            "nas_health": self._nas_health(),
            "radius_health": self._radius_health(),
            "vpn_api_health": self._vpn_api_health(),
            "accounting_failures": self._accounting_failures(),
            "emergency_actions": [
                {
                    "key": "disconnect_all",
                    "status": "blocked_until_live_approval",
                    "reason": "هذا الإجراء يحتاج ربطًا حيًا آمنًا وموافقة صريحة من المشغّل قبل التنفيذ.",
                },
                {
                    "key": "global_speed_cut",
                    "status": "dry_run_only",
                    "reason": "استخدم معاينة التحكم بالسرعة أولًا. هذا المركز لا يرسل أوامر CoA حيّة مباشرة.",
                },
            ],
        }

    def active_sessions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT radacctid, acctsessionid, username, nasipaddress,
                   framedipaddress, callingstationid, acctstarttime
            FROM radacct
            WHERE tenant_id=? AND acctstoptime IS NULL
            ORDER BY radacctid DESC LIMIT ?
            """,
            (self.tenant_id, int(limit)),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def speed_preview(
        self,
        *,
        preset: str = "normal",
        multiplier: float | None = None,
        profile_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        preset_def = SPEED_PRESETS.get(preset or "normal", SPEED_PRESETS["normal"])
        mult = float(multiplier if multiplier is not None else preset_def["multiplier"])
        if mult <= 0 or mult > 5:
            raise OperationsSpeedError("multiplier out of safe preview range")
        plans = self._plans(profile_ids=profile_ids)
        impacts = []
        for plan in plans:
            down = int(plan.get("speed_down_kbps") or 0)
            up = int(plan.get("speed_up_kbps") or 0)
            sub_count = self._subscriber_count_for_plan(int(plan["id"]))
            online = self._online_count_for_plan(int(plan["id"]))
            impacts.append(
                {
                    "profile_id": int(plan["id"]),
                    "profile_name": plan["name"],
                    "affected_subscribers": sub_count,
                    "affected_profiles": 1,
                    "base_down_kbps": down,
                    "base_up_kbps": up,
                    "multiplier": mult,
                    "effective_down_kbps": int(down * mult),
                    "effective_up_kbps": int(up * mult),
                    "coa_required": online > 0,
                    "online_sessions": online,
                }
            )
        return {
            "preset": preset,
            "preset_label": preset_def["label"],
            "multiplier": mult,
            "applied_to_radius": False,
            "impact": impacts,
            "total_subscribers": sum(item["affected_subscribers"] for item in impacts),
            "total_online_sessions": sum(item["online_sessions"] for item in impacts),
            "coa_required": any(item["coa_required"] for item in impacts),
        }

    def save_speed_policy(
        self,
        *,
        policy_key: str,
        title: str,
        preset: str = "normal",
        multiplier: float | None = None,
        profile_ids: list[int] | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        preview = self.speed_preview(preset=preset, multiplier=multiplier, profile_ids=profile_ids)
        event = self.events.record_event(
            tenant_id=self.tenant_id,
            category="system",
            severity="info",
            event_key="speed_control.dry_run_saved",
            message="Speed control dry-run policy saved",
            actor_type="admin",
            target_type="speed_control_policy",
            metadata={"preset": preset, "applied_to_radius": False},
        )
        key = self._key(policy_key)
        now = now_iso()
        db().execute(
            """
            INSERT INTO speed_control_policies(
                tenant_id, policy_key, title, preset, multiplier, target_json,
                preview_json, status, applied_to_radius, event_id, created_by,
                created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, policy_key) DO UPDATE SET
                title=excluded.title,
                preset=excluded.preset,
                multiplier=excluded.multiplier,
                target_json=excluded.target_json,
                preview_json=excluded.preview_json,
                status='dry_run_ready',
                applied_to_radius=0,
                event_id=excluded.event_id,
                updated_at=excluded.updated_at
            """,
            (
                self.tenant_id,
                key,
                title,
                preset,
                preview["multiplier"],
                _json({"profile_ids": profile_ids or []}),
                _json(preview),
                "dry_run_ready",
                0,
                event["id"],
                actor,
                now,
                now,
            ),
        )
        return self.get_policy(key)

    def get_policy(self, policy_key_or_id: str | int) -> dict[str, Any]:
        if str(policy_key_or_id).isdigit():
            row = db().execute(
                "SELECT * FROM speed_control_policies WHERE tenant_id=? AND id=?",
                (self.tenant_id, int(policy_key_or_id)),
            ).fetchone()
        else:
            row = db().execute(
                "SELECT * FROM speed_control_policies WHERE tenant_id=? AND policy_key=?",
                (self.tenant_id, self._key(str(policy_key_or_id))),
            ).fetchone()
        if not row:
            raise OperationsSpeedError("speed policy not found")
        return self._policy_row(row_to_dict(row))

    def list_policies(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._policy_row(row_to_dict(row))
            for row in db().execute(
                "SELECT * FROM speed_control_policies WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
                (self.tenant_id, int(limit)),
            ).fetchall()
        ]

    def _plans(self, *, profile_ids: list[int] | None = None) -> list[dict[str, Any]]:
        params: list[Any] = [self.tenant_id]
        where = "tenant_id=? AND deleted_at IS NULL"
        if profile_ids:
            placeholders = ",".join("?" for _ in profile_ids)
            where += f" AND id IN ({placeholders})"
            params.extend(int(item) for item in profile_ids)
        rows = db().execute(
            f"""
            SELECT id, name, speed_down_kbps, speed_up_kbps
            FROM access_plans
            WHERE {where}
            ORDER BY id ASC LIMIT 200
            """,
            tuple(params),
        ).fetchall()
        return [row_to_dict(row) for row in rows]

    def _subscriber_count_for_plan(self, plan_id: int) -> int:
        return int(db().execute(
            "SELECT COUNT(*) AS c FROM subscribers WHERE tenant_id=? AND plan_id=? AND deleted_at IS NULL",
            (self.tenant_id, int(plan_id)),
        ).fetchone()["c"] or 0)

    def _online_count_for_plan(self, plan_id: int) -> int:
        return int(db().execute(
            """
            SELECT COUNT(*) AS c
            FROM radacct a
            JOIN subscribers s ON s.tenant_id=a.tenant_id AND s.username=a.username
            WHERE a.tenant_id=? AND s.plan_id=? AND a.acctstoptime IS NULL
            """,
            (self.tenant_id, int(plan_id)),
        ).fetchone()["c"] or 0)

    def _online_count(self) -> int:
        return int(db().execute(
            "SELECT COUNT(*) AS c FROM radacct WHERE tenant_id=? AND acctstoptime IS NULL",
            (self.tenant_id,),
        ).fetchone()["c"] or 0)

    def _nas_health(self) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN enabled=1 THEN 1 ELSE 0 END) AS enabled
            FROM nas_devices WHERE tenant_id=?
            """,
            (self.tenant_id,),
        ).fetchone()
        return {"total": int(row["total"] or 0), "enabled": int(row["enabled"] or 0)}

    def _radius_health(self) -> dict[str, Any]:
        try:
            db().execute("SELECT 1 FROM radacct LIMIT 1").fetchone()
            return {"status": "ok", "source": "sqlite"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "degraded", "error": str(exc)}

    def _vpn_api_health(self) -> dict[str, Any]:
        row = db().execute(
            "SELECT COUNT(*) AS c FROM nas_devices WHERE tenant_id=? AND api_port>0",
            (self.tenant_id,),
        ).fetchone()
        return {"configured_api_devices": int(row["c"] or 0), "live_probe": False}

    def _accounting_failures(self) -> dict[str, Any]:
        row = db().execute(
            """
            SELECT COUNT(*) AS c FROM business_events
            WHERE tenant_id=? AND category='radius' AND severity IN ('warning','error','critical')
            """,
            (self.tenant_id,),
        ).fetchone()
        return {"recent_radius_event_failures": int(row["c"] or 0)}

    def _policy_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["target"] = _load(row.get("target_json"), {})
        row["preview"] = _load(row.get("preview_json"), {})
        row["applied_to_radius"] = bool(row.get("applied_to_radius"))
        return row

    @staticmethod
    def _key(value: str) -> str:
        key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-")
        if not key:
            raise OperationsSpeedError("policy key required")
        return key[:120]
