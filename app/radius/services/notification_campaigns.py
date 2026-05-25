"""Notification and campaign engine foundation.

The default provider is intentionally queued-only. It records internal delivery
state without sending SMS/WhatsApp/Telegram/email traffic or storing external
provider secrets.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..db.connection import db
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import EventService


class NotificationCampaignError(ValueError):
    """Safe validation error for notification/campaign operations."""


CHANNELS = {"internal", "sms", "whatsapp", "telegram", "email", "push"}
RECIPIENT_TYPES = {"subscriber", "card_user", "manager", "distributor", "company"}


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _load(raw: Any, default: Any = None) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return {} if default is None else default


def _channel(value: str) -> str:
    channel = str(value or "internal").strip().lower()
    if channel not in CHANNELS:
        raise NotificationCampaignError("unsupported channel")
    return channel


def _recipient_type(value: str) -> str:
    rtype = str(value or "").strip().lower()
    if rtype not in RECIPIENT_TYPES:
        raise NotificationCampaignError("unsupported recipient type")
    return rtype


@dataclass(frozen=True)
class ProviderResult:
    status: str
    provider_key: str
    provider_message_id: str = ""
    error_message: str = ""
    result: dict[str, Any] | None = None


class ProviderConfig:
    """Non-secret provider configuration descriptor."""

    def __init__(self, *, provider_key: str = "null", channel: str = "internal", secret_ref: str = "") -> None:
        self.provider_key = provider_key or "null"
        self.channel = _channel(channel)
        self.secret_ref = secret_ref or ""


class NotificationProvider:
    def send(self, *, delivery: dict[str, Any], notification: dict[str, Any]) -> ProviderResult:
        raise NotImplementedError


class QueuedOnlyProvider(NotificationProvider):
    """Provider abstraction used until external adapters are configured."""

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config or ProviderConfig()

    def send(self, *, delivery: dict[str, Any], notification: dict[str, Any]) -> ProviderResult:  # noqa: ARG002
        return ProviderResult(
            status="queued",
            provider_key=self.config.provider_key,
            result={"provider_mode": "queued_only", "external_send": False},
        )


class NotificationCampaignService:
    def __init__(self, *, tenant_id: int = 1, provider: NotificationProvider | None = None) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.provider = provider or QueuedOnlyProvider()
        self.events = EventService()

    def create_template(
        self,
        *,
        template_key: str,
        title: str,
        body: str,
        channel: str = "internal",
        subject: str = "",
        variables: list[str] | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        key = self._key(template_key)
        ch = _channel(channel)
        now = now_iso()
        db().execute(
            """
            INSERT INTO notification_templates(
                tenant_id, template_key, title, channel, subject, body,
                variables_json, status, created_by, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, template_key) DO UPDATE SET
                title=excluded.title,
                channel=excluded.channel,
                subject=excluded.subject,
                body=excluded.body,
                variables_json=excluded.variables_json,
                status='active',
                updated_at=excluded.updated_at
            """,
            (
                self.tenant_id,
                key,
                title,
                ch,
                subject,
                body,
                _json(variables or []),
                "active",
                actor,
                now,
                now,
            ),
        )
        return self.get_template(key)

    def get_template(self, template_key_or_id: str | int) -> dict[str, Any]:
        if str(template_key_or_id).isdigit():
            row = db().execute(
                "SELECT * FROM notification_templates WHERE tenant_id=? AND id=?",
                (self.tenant_id, int(template_key_or_id)),
            ).fetchone()
        else:
            row = db().execute(
                "SELECT * FROM notification_templates WHERE tenant_id=? AND template_key=?",
                (self.tenant_id, self._key(str(template_key_or_id))),
            ).fetchone()
        if not row:
            raise NotificationCampaignError("template not found")
        return self._template_row(row_to_dict(row))

    def list_templates(self) -> list[dict[str, Any]]:
        return [
            self._template_row(row_to_dict(row))
            for row in db().execute(
                "SELECT * FROM notification_templates WHERE tenant_id=? ORDER BY id DESC LIMIT 200",
                (self.tenant_id,),
            ).fetchall()
        ]

    def render_template(self, template: dict[str, Any] | str | int, context: dict[str, Any]) -> dict[str, str]:
        tmpl = self.get_template(template) if not isinstance(template, dict) else template
        return {
            "subject": self._render(tmpl.get("subject") or "", context),
            "body": self._render(tmpl.get("body") or "", context),
        }

    def create_segment(
        self,
        *,
        segment_key: str,
        title: str,
        filters: dict[str, Any],
        actor: str = "system",
    ) -> dict[str, Any]:
        key = self._key(segment_key)
        now = now_iso()
        db().execute(
            """
            INSERT INTO audience_segments(
                tenant_id, segment_key, title, filters_json, status,
                created_by, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, segment_key) DO UPDATE SET
                title=excluded.title,
                filters_json=excluded.filters_json,
                status='active',
                updated_at=excluded.updated_at
            """,
            (self.tenant_id, key, title, _json(filters), "active", actor, now, now),
        )
        row = db().execute(
            "SELECT * FROM audience_segments WHERE tenant_id=? AND segment_key=?",
            (self.tenant_id, key),
        ).fetchone()
        return self._segment_row(row_to_dict(row))

    def list_segments(self) -> list[dict[str, Any]]:
        return [
            self._segment_row(row_to_dict(row))
            for row in db().execute(
                "SELECT * FROM audience_segments WHERE tenant_id=? ORDER BY id DESC LIMIT 100",
                (self.tenant_id,),
            ).fetchall()
        ]

    def preview_audience(self, audience: dict[str, Any]) -> list[dict[str, Any]]:
        target = str(audience.get("target") or audience.get("recipient_type") or "subscriber").strip().lower()
        if target == "selected_subscribers":
            ids = [int(item) for item in audience.get("ids") or []]
            return self._subscribers(ids=ids)
        if target == "subscriber":
            return self._subscribers(manager_id=audience.get("manager_id"), limit=int(audience.get("limit") or 200))
        if target == "card_user":
            return self._card_users(ids=audience.get("ids"), limit=int(audience.get("limit") or 200))
        if target == "manager":
            return self._managers(ids=audience.get("ids"), limit=int(audience.get("limit") or 200))
        if target == "distributor":
            return self._distributors(ids=audience.get("ids"), limit=int(audience.get("limit") or 200))
        if target == "company":
            return [{"recipient_type": "company", "recipient_id": self.tenant_id, "display_name": "Company", "address": ""}]
        raise NotificationCampaignError("unsupported audience target")

    def queue_notification(
        self,
        *,
        recipient_type: str,
        recipient_id: int,
        channel: str,
        subject: str,
        body: str,
        template_id: int | None = None,
        campaign_id: int | None = None,
        metadata: dict[str, Any] | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        rtype = _recipient_type(recipient_type)
        ch = _channel(channel)
        now = now_iso()
        cur = db().execute(
            """
            INSERT INTO message_notifications(
                tenant_id, notification_type, channel, recipient_type,
                recipient_id, template_id, campaign_id, subject, body,
                status, metadata_json, created_by, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                "campaign" if campaign_id else "manual",
                ch,
                rtype,
                int(recipient_id or 0),
                template_id,
                campaign_id,
                subject or "",
                body,
                "queued",
                _json(metadata or {}),
                actor,
                now,
            ),
        )
        notification = self.get_notification(int(cur.lastrowid))
        delivery_id = self._create_delivery(notification)
        delivery = self.get_delivery(delivery_id)
        result = self.provider.send(delivery=delivery, notification=notification)
        self._update_delivery(delivery_id, result)
        return {"notification": self.get_notification(notification["id"]), "delivery": self.get_delivery(delivery_id)}

    def send_manual(
        self,
        *,
        audience: dict[str, Any],
        channel: str,
        message: str,
        subject: str = "",
        actor: str = "system",
    ) -> dict[str, Any]:
        recipients = self.preview_audience(audience)
        queued = [
            self.queue_notification(
                recipient_type=item["recipient_type"],
                recipient_id=int(item["recipient_id"]),
                channel=channel,
                subject=subject,
                body=message,
                metadata={"audience": audience},
                actor=actor,
            )
            for item in recipients
        ]
        self.events.record_event(
            tenant_id=self.tenant_id,
            category="notification",
            event_key="notification.manual_queued",
            message="Manual notification queued",
            actor_type="admin",
            target_type="notification_campaign",
            metadata={"count": len(queued), "channel": _channel(channel)},
        )
        return {"queued_count": len(queued), "deliveries": queued}

    def campaign_dry_run(
        self,
        *,
        title: str,
        campaign_key: str,
        template_id: int,
        audience: dict[str, Any],
        actions: list[dict[str, Any]] | None = None,
        actor: str = "system",
    ) -> dict[str, Any]:
        template = self.get_template(template_id)
        recipients = self.preview_audience(audience)
        action_plan = self._action_plan(actions or [], recipients)
        dry_run = {
            "recipient_count": len(recipients),
            "recipients": recipients[:20],
            "actions": action_plan,
            "external_send": False,
            "requires_permission_check": bool(actions),
        }
        key = self._key(campaign_key)
        now = now_iso()
        db().execute(
            """
            INSERT INTO message_campaigns(
                tenant_id, campaign_key, title, campaign_type, channel,
                template_id, audience_json, actions_json, dry_run_json,
                status, created_by, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tenant_id, campaign_key) DO UPDATE SET
                title=excluded.title,
                channel=excluded.channel,
                template_id=excluded.template_id,
                audience_json=excluded.audience_json,
                actions_json=excluded.actions_json,
                dry_run_json=excluded.dry_run_json,
                status='dry_run_ready',
                updated_at=excluded.updated_at
            """,
            (
                self.tenant_id,
                key,
                title,
                "manual",
                template["channel"],
                int(template["id"]),
                _json(audience),
                _json(actions or []),
                _json(dry_run),
                "dry_run_ready",
                actor,
                now,
                now,
            ),
        )
        row = db().execute(
            "SELECT * FROM message_campaigns WHERE tenant_id=? AND campaign_key=?",
            (self.tenant_id, key),
        ).fetchone()
        return self._campaign_row(row_to_dict(row))

    def delivery_log(self, *, limit: int = 100) -> list[dict[str, Any]]:
        return [
            self._delivery_row(row_to_dict(row))
            for row in db().execute(
                """
                SELECT d.*, n.recipient_type, n.recipient_id, n.subject, n.body
                FROM message_deliveries d
                JOIN message_notifications n ON n.id=d.notification_id
                WHERE d.tenant_id=?
                ORDER BY d.id DESC LIMIT ?
                """,
                (self.tenant_id, int(limit)),
            ).fetchall()
        ]

    def get_notification(self, notification_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM message_notifications WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(notification_id)),
        ).fetchone()
        if not row:
            raise NotificationCampaignError("notification not found")
        return self._notification_row(row_to_dict(row))

    def get_delivery(self, delivery_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM message_deliveries WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(delivery_id)),
        ).fetchone()
        if not row:
            raise NotificationCampaignError("delivery not found")
        return self._delivery_row(row_to_dict(row))

    def dashboard(self) -> dict[str, Any]:
        rows = db().execute(
            """
            SELECT status, COUNT(*) AS c
            FROM message_deliveries
            WHERE tenant_id=?
            GROUP BY status
            """,
            (self.tenant_id,),
        ).fetchall()
        counts = {row["status"]: int(row["c"] or 0) for row in rows}
        return {
            "templates": len(self.list_templates()),
            "segments": len(self.list_segments()),
            "queued": counts.get("queued", 0),
            "sent": counts.get("sent", 0),
            "failed": counts.get("failed", 0),
        }

    def _create_delivery(self, notification: dict[str, Any]) -> int:
        cur = db().execute(
            """
            INSERT INTO message_deliveries(
                tenant_id, notification_id, campaign_id, channel,
                provider_key, recipient_address, status, result_json,
                created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                int(notification["id"]),
                notification.get("campaign_id"),
                notification["channel"],
                "null",
                "",
                "queued",
                _json({}),
                now_iso(),
            ),
        )
        return int(cur.lastrowid)

    def _update_delivery(self, delivery_id: int, result: ProviderResult) -> None:
        db().execute(
            """
            UPDATE message_deliveries
            SET status=?, provider_key=?, provider_message_id=?,
                error_message=?, result_json=?, sent_at=CASE WHEN ?='sent' THEN ? ELSE sent_at END
            WHERE tenant_id=? AND id=?
            """,
            (
                result.status,
                result.provider_key,
                result.provider_message_id,
                result.error_message,
                _json(result.result or {}),
                result.status,
                now_iso(),
                self.tenant_id,
                int(delivery_id),
            ),
        )
        db().execute(
            "UPDATE message_notifications SET status=?, updated_at=? WHERE tenant_id=? AND id=(SELECT notification_id FROM message_deliveries WHERE id=?)",
            (result.status, now_iso(), self.tenant_id, int(delivery_id)),
        )

    def _action_plan(self, actions: list[dict[str, Any]], recipients: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed = {"record_event", "wallet_credit", "add_free_days"}
        planned = []
        for action in actions:
            kind = str(action.get("type") or "").strip()
            if kind not in allowed:
                planned.append({"type": kind, "status": "blocked", "reason": "unsupported action"})
                continue
            planned.append(
                {
                    "type": kind,
                    "status": "dry_run_only",
                    "recipient_count": len(recipients),
                    "permission_required": f"campaign.action.{kind}",
                }
            )
        return planned

    def _subscribers(self, *, ids: list[int] | None = None, manager_id: Any = None, limit: int = 200) -> list[dict[str, Any]]:
        params: list[Any] = [self.tenant_id]
        where = "tenant_id=? AND deleted_at IS NULL"
        if ids:
            placeholders = ",".join("?" for _ in ids)
            where += f" AND id IN ({placeholders})"
            params.extend(ids)
        if manager_id not in (None, ""):
            where += " AND manager_id=?"
            params.append(int(manager_id))
        params.append(int(limit))
        rows = db().execute(
            f"SELECT id, username, full_name, mobile, email FROM subscribers WHERE {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [
            {
                "recipient_type": "subscriber",
                "recipient_id": int(row["id"]),
                "display_name": row["full_name"] or row["username"],
                "address": row["mobile"] or row["email"] or "",
            }
            for row in rows
        ]

    def _card_users(self, *, ids: list[int] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        params: list[Any] = [self.tenant_id]
        where = "tenant_id=?"
        if ids:
            clean = [int(item) for item in ids]
            placeholders = ",".join("?" for _ in clean)
            where += f" AND id IN ({placeholders})"
            params.extend(clean)
        params.append(int(limit))
        rows = db().execute(
            f"SELECT id, display_name, mobile, email FROM card_users WHERE {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [
            {
                "recipient_type": "card_user",
                "recipient_id": int(row["id"]),
                "display_name": row["display_name"] or f"Card user #{row['id']}",
                "address": row["mobile"] or row["email"] or "",
            }
            for row in rows
        ]

    def _managers(self, *, ids: list[int] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = "1=1"
        if ids:
            clean = [int(item) for item in ids]
            placeholders = ",".join("?" for _ in clean)
            where += f" AND id IN ({placeholders})"
            params.extend(clean)
        params.append(int(limit))
        rows = db().execute(
            f"SELECT id, username, full_name, mobile, email FROM admins WHERE {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [
            {
                "recipient_type": "manager",
                "recipient_id": int(row["id"]),
                "display_name": row["full_name"] or row["username"],
                "address": row["mobile"] or row["email"] or "",
            }
            for row in rows
        ]

    def _distributors(self, *, ids: list[int] | None = None, limit: int = 200) -> list[dict[str, Any]]:
        params: list[Any] = [self.tenant_id]
        where = "tenant_id=?"
        if ids:
            clean = [int(item) for item in ids]
            placeholders = ",".join("?" for _ in clean)
            where += f" AND id IN ({placeholders})"
            params.extend(clean)
        params.append(int(limit))
        rows = db().execute(
            f"SELECT id, name, display_name, phone, email FROM distributors WHERE {where} ORDER BY id DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [
            {
                "recipient_type": "distributor",
                "recipient_id": int(row["id"]),
                "display_name": row["display_name"] or row["name"],
                "address": row["phone"] or row["email"] or "",
            }
            for row in rows
        ]

    def _template_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["variables"] = _load(row.get("variables_json"), [])
        return row

    def _segment_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["filters"] = _load(row.get("filters_json"), {})
        return row

    def _campaign_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["audience"] = _load(row.get("audience_json"), {})
        row["actions"] = _load(row.get("actions_json"), [])
        row["dry_run"] = _load(row.get("dry_run_json"), {})
        return row

    def _notification_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["metadata"] = _load(row.get("metadata_json"), {})
        return row

    def _delivery_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["result"] = _load(row.get("result_json"), {})
        return row

    @staticmethod
    def _render(text: str, context: dict[str, Any]) -> str:
        def repl(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            value = context.get(key, "")
            return "" if value is None else str(value)

        return re.sub(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}", repl, text or "")

    @staticmethod
    def _key(value: str) -> str:
        key = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "").strip().lower()).strip("-")
        if not key:
            raise NotificationCampaignError("key required")
        return key[:120]
