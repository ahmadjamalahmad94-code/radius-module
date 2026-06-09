"""Events, investigation, risk, and fraud center foundations."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from ..db.connection import db
from ..db.helpers import now_iso, row_to_dict
from .business_os_finance import EventService
from .event_labels import (
    ACTOR_TYPE_LABELS,
    TARGET_TYPE_LABELS,
    event_key_label,
    category_label,
    severity_label,
)


class EventsRiskError(ValueError):
    """Safe validation error for the events/risk center."""


def _json(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _load(raw: Any, default: Any = None) -> Any:
    try:
        return json.loads(raw or "")
    except (TypeError, ValueError):
        return {} if default is None else default


def _ph(ids: list[int]) -> str:
    """بناء placeholder لقائمة IN."""
    return ",".join("?" * len(ids))


def _bulk_names(
    conn: Any,
    *,
    table: str,
    name_col: str,
    fallback_col: str,
    ids: list[int],
    tenant_id: int | None = None,
) -> dict[int, str]:
    """جلب الأسماء بشكل مجمّع من جدول ما لقائمة معرّفات."""
    if not ids:
        return {}
    where = "id IN (" + _ph(ids) + ")"
    params: list[Any] = list(ids)
    if tenant_id is not None:
        where += " AND tenant_id=?"
        params.append(tenant_id)
    rows = conn.execute(
        f"SELECT id, {name_col}, {fallback_col} FROM {table} WHERE {where}",
        tuple(params),
    ).fetchall()
    result: dict[int, str] = {}
    for row in rows:
        name = (row[name_col] or "").strip() or (row[fallback_col] or "").strip()
        if name:
            result[int(row["id"])] = name
    return result


def _resolve_names_bulk(
    rows: list[dict[str, Any]],
    *,
    tenant_id: int,
) -> dict[str, dict[int, str]]:
    """
    جمع كل (type, id) فريد من صفوف الأحداث، ثم جلب الأسماء الحقيقية
    بثلاث استعلامات أقصى (admins, card_users, subscribers).
    يُعيد {type_key: {id: name}}.
    """
    conn = db()
    # تجميع المعرّفات الفريدة لكل نوع
    admin_ids: set[int] = set()
    card_user_ids: set[int] = set()
    subscriber_ids: set[int] = set()

    def _classify(typ: str | None, eid: Any) -> None:
        if not eid:
            return
        try:
            eid_int = int(eid)
        except (TypeError, ValueError):
            return
        t = (typ or "").lower()
        if t in ("admin", "manager"):
            admin_ids.add(eid_int)
        elif t == "card_user":
            card_user_ids.add(eid_int)
        elif t in ("subscriber", "user"):
            subscriber_ids.add(eid_int)

    for row in rows:
        _classify(row.get("actor_type"), row.get("actor_id"))
        _classify(row.get("target_type"), row.get("target_id"))

    result: dict[str, dict[int, str]] = {}

    if admin_ids:
        result["admin"] = _bulk_names(
            conn, table="admins",
            name_col="full_name", fallback_col="username",
            ids=list(admin_ids),
        )

    if card_user_ids:
        result["card_user"] = _bulk_names(
            conn, table="card_users",
            name_col="display_name", fallback_col="display_name",
            ids=list(card_user_ids), tenant_id=tenant_id,
        )

    if subscriber_ids:
        result["subscriber"] = _bulk_names(
            conn, table="subscribers",
            name_col="full_name", fallback_col="username",
            ids=list(subscriber_ids), tenant_id=tenant_id,
        )

    return result


def _make_entity_label(
    entity_type: str | None,
    entity_id: Any,
    name_map: dict[str, dict[int, str]],
    *,
    label_map: dict[str, str],
) -> str:
    """
    بناء تسمية عربية للفاعل أو الهدف:
    - نوع system/api/فارغ  → اسم ثابت
    - معرَّف موجود في name_map → الاسم الحقيقي فقط
    - معرَّف غير موجود     → الاسم النوعي العربي (بلا #id)
    """
    t = (entity_type or "").lower().strip()
    if not t or t in ("system", "risk_engine"):
        return "النظام"
    if t in ("api_token", "api"):
        return "واجهة برمجية"
    if t == "anonymous":
        return "غير معروف"

    # تحديد مجموعة الأسماء المناسبة
    if t in ("admin", "manager"):
        names = name_map.get("admin", {})
    elif t == "card_user":
        names = name_map.get("card_user", {})
    elif t in ("subscriber", "user"):
        names = name_map.get("subscriber", {})
    else:
        names = {}

    if entity_id:
        try:
            eid = int(entity_id)
            if eid in names:
                return names[eid]
        except (TypeError, ValueError):
            pass

    # الاسم النوعي العربي — بلا # أو رقم
    return label_map.get(t, t)


class EventsRiskCenterService:
    def __init__(self, *, tenant_id: int = 1) -> None:
        self.tenant_id = int(tenant_id or 1)
        self.events = EventService()

    def list_events(
        self,
        *,
        category: str = "",
        severity: str = "",
        actor_type: str = "",
        actor_id: int | None = None,
        target_type: str = "",
        target_id: int | None = None,
        correlation_id: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM business_events WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        for column, value in (
            ("category", category),
            ("severity", severity),
            ("actor_type", actor_type),
            ("target_type", target_type),
            ("correlation_id", correlation_id),
        ):
            if value:
                sql += f" AND {column}=?"
                params.append(value)
        if actor_id is not None:
            sql += " AND actor_id=?"
            params.append(int(actor_id))
        if target_id is not None:
            sql += " AND target_id=?"
            params.append(int(target_id))
        if date_from:
            sql += " AND created_at>=?"
            params.append(date_from)
        if date_to:
            sql += " AND created_at<=?"
            params.append(date_to)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))

        raw_rows = [row_to_dict(row) for row in db().execute(sql, tuple(params)).fetchall()]

        # جلب الأسماء الحقيقية بثلاث استعلامات مجمّعة كحد أقصى
        name_map = _resolve_names_bulk(raw_rows, tenant_id=self.tenant_id)

        return [self._event_row(row, name_map=name_map) for row in raw_rows]

    def get_event(self, event_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM business_events WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(event_id)),
        ).fetchone()
        if not row:
            raise EventsRiskError("event not found")
        d = row_to_dict(row)
        name_map = _resolve_names_bulk([d], tenant_id=self.tenant_id)
        return self._event_row(d, name_map=name_map)

    def entity_timeline(self, *, entity_type: str, entity_id: int, limit: int = 100) -> list[dict[str, Any]]:
        return self.list_events(target_type=entity_type, target_id=int(entity_id), limit=limit)

    def create_fraud_flag(
        self,
        *,
        flag_key: str,
        severity: str = "warning",
        entity_type: str = "",
        entity_id: int | None = None,
        event_id: int | None = None,
        risk_score: int = 50,
        summary: str = "",
        evidence: dict[str, Any] | None = None,
        correlation_id: str = "",
        actor: str = "risk_engine",
    ) -> dict[str, Any]:
        sev = severity if severity in {"info", "warning", "error", "critical"} else "warning"
        key = str(flag_key or "").strip()
        if not key:
            raise EventsRiskError("flag key required")
        cur = db().execute(
            """
            INSERT INTO fraud_flags(
                tenant_id, flag_key, severity, status, entity_type,
                entity_id, event_id, risk_score, summary, evidence_json,
                correlation_id, created_by, created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                key,
                sev,
                "open",
                entity_type,
                entity_id,
                event_id,
                max(0, min(100, int(risk_score or 0))),
                summary,
                _json(evidence or {}),
                correlation_id,
                actor,
                now_iso(),
            ),
        )
        return self.get_fraud_flag(int(cur.lastrowid))

    def get_fraud_flag(self, flag_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM fraud_flags WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(flag_id)),
        ).fetchone()
        if not row:
            raise EventsRiskError("flag not found")
        return self._flag_row(row_to_dict(row))

    def list_fraud_flags(self, *, status: str = "", limit: int = 200) -> list[dict[str, Any]]:
        sql = "SELECT * FROM fraud_flags WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [self._flag_row(row_to_dict(row)) for row in db().execute(sql, tuple(params)).fetchall()]

    def create_investigation(
        self,
        *,
        title: str,
        severity: str = "warning",
        entity_type: str = "",
        entity_id: int | None = None,
        linked_events: list[int] | None = None,
        linked_flags: list[int] | None = None,
        summary: str = "",
        actor: str = "system",
    ) -> dict[str, Any]:
        cur = db().execute(
            """
            INSERT INTO investigations(
                tenant_id, title, status, severity, entity_type, entity_id,
                opened_by, summary, linked_events_json, linked_flags_json,
                created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                self.tenant_id,
                title,
                "open",
                severity if severity in {"info", "warning", "error", "critical"} else "warning",
                entity_type,
                entity_id,
                actor,
                summary,
                _json(linked_events or []),
                _json(linked_flags or []),
                now_iso(),
                now_iso(),
            ),
        )
        return self.get_investigation(int(cur.lastrowid))

    def get_investigation(self, investigation_id: int) -> dict[str, Any]:
        row = db().execute(
            "SELECT * FROM investigations WHERE tenant_id=? AND id=?",
            (self.tenant_id, int(investigation_id)),
        ).fetchone()
        if not row:
            raise EventsRiskError("investigation not found")
        return self._investigation_row(row_to_dict(row))

    def list_investigations(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM investigations WHERE tenant_id=?"
        params: list[Any] = [self.tenant_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        return [self._investigation_row(row_to_dict(row)) for row in db().execute(sql, tuple(params)).fetchall()]

    def run_risk_rules(self) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        findings.extend(self._detect_negative_wallets())
        findings.extend(self._detect_repeated_failed_logins())
        findings.extend(self._detect_repeated_loans())
        findings.extend(self._detect_large_discounts())
        findings.extend(self._detect_revenue_ledger_mismatch())
        flags = [
            self.create_fraud_flag(
                flag_key=item["flag_key"],
                severity=item["severity"],
                entity_type=item.get("entity_type", ""),
                entity_id=item.get("entity_id"),
                risk_score=item["risk_score"],
                summary=item["summary"],
                evidence=item.get("evidence", {}),
            )
            for item in findings
        ]
        return {"findings": findings, "flags_created": len(flags), "flags": flags}

    def dashboard(self) -> dict[str, Any]:
        event_count = db().execute(
            "SELECT COUNT(*) AS c FROM business_events WHERE tenant_id=?",
            (self.tenant_id,),
        ).fetchone()["c"]
        open_flags = db().execute(
            "SELECT COUNT(*) AS c FROM fraud_flags WHERE tenant_id=? AND status='open'",
            (self.tenant_id,),
        ).fetchone()["c"]
        critical = db().execute(
            "SELECT COUNT(*) AS c FROM business_events WHERE tenant_id=? AND severity='critical'",
            (self.tenant_id,),
        ).fetchone()["c"]
        return {"events": int(event_count or 0), "open_flags": int(open_flags or 0), "critical_events": int(critical or 0)}

    # تعريب نوع مالك المحفظة في ملخص التنبيه (القيمة التقنية تبقى في entity_type)
    _OWNER_LABELS = {
        "subscriber": "مشترك",
        "user": "مشترك",
        "distributor": "موزّع",
        "manager": "مدير",
        "admin": "مدير",
        "tenant": "مستأجر",
    }

    def _detect_negative_wallets(self) -> list[dict[str, Any]]:
        rows = db().execute(
            "SELECT * FROM wallets WHERE tenant_id=? AND balance_minor<0",
            (self.tenant_id,),
        ).fetchall()
        return [
            {
                "flag_key": "wallet_negative",
                "severity": "critical",
                "entity_type": row["owner_type"],
                "entity_id": row["owner_id"],
                "risk_score": 90,
                # ملخص عربي للتنبيه — يظهر مباشرة في جدول مركز المخاطر
                "summary": (
                    f"محفظة {self._OWNER_LABELS.get(row['owner_type'], row['owner_type'])}"
                    f" ‎#{row['owner_id']} برصيد سالب."
                ),
                "evidence": {"wallet_id": row["id"], "balance_minor": row["balance_minor"]},
            }
            for row in rows
        ]

    def _detect_repeated_failed_logins(self) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT actor_type, actor_id, target_type, target_id, COUNT(*) AS c
            FROM business_events
            WHERE tenant_id=? AND category='security' AND event_key LIKE '%login.failed%'
            GROUP BY actor_type, actor_id, target_type, target_id
            HAVING COUNT(*) >= 3
            """,
            (self.tenant_id,),
        ).fetchall()
        return [
            {
                "flag_key": "repeated_failed_logins",
                "severity": "warning",
                "entity_type": row["target_type"] or row["actor_type"],
                "entity_id": row["target_id"] or row["actor_id"],
                "risk_score": min(95, 40 + int(row["c"]) * 10),
                # ملخص عربي للتنبيه
                "summary": f"رُصدت محاولات دخول فاشلة متكررة (العدد: {row['c']}).",
                "evidence": {"count": int(row["c"] or 0)},
            }
            for row in rows
        ]

    def _detect_repeated_loans(self) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT target_type, target_id, COUNT(*) AS c
            FROM ledger_entries
            WHERE tenant_id=? AND entry_type='loan'
            GROUP BY target_type, target_id
            HAVING COUNT(*) >= 3
            """,
            (self.tenant_id,),
        ).fetchall()
        return [
            {
                "flag_key": "subscriber_repeated_loans",
                "severity": "warning",
                "entity_type": row["target_type"] or "subscriber",
                "entity_id": row["target_id"],
                "risk_score": min(90, 35 + int(row["c"]) * 10),
                # ملخص عربي للتنبيه
                "summary": f"رُصدت سلف متكررة لنفس المستفيد (العدد: {row['c']}).",
                "evidence": {"count": int(row["c"] or 0)},
            }
            for row in rows
        ]

    def _detect_large_discounts(self) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM price_snapshots
            WHERE tenant_id=? AND discount_amount_minor > 0
            ORDER BY discount_amount_minor DESC LIMIT 50
            """,
            (self.tenant_id,),
        ).fetchall()
        return [
            {
                "flag_key": "suspicious_discount",
                "severity": "warning",
                "entity_type": row["reference_type"],
                "entity_id": row["reference_id"],
                "risk_score": 60,
                # ملخص عربي للتنبيه
                "summary": "خصم مسجّل على لقطة سعر يستوجب المراجعة.",
                "evidence": {"discount_amount_minor": row["discount_amount_minor"], "snapshot_id": row["id"]},
            }
            for row in rows
        ]

    def _detect_revenue_ledger_mismatch(self) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT r.* FROM revenue_records r
            LEFT JOIN ledger_entries l
              ON l.tenant_id=r.tenant_id AND l.reference_type=r.source_type AND l.reference_id=r.source_id
            WHERE r.tenant_id=? AND r.collected_amount_minor>0 AND l.id IS NULL
            LIMIT 50
            """,
            (self.tenant_id,),
        ).fetchall()
        return [
            {
                "flag_key": "revenue_ledger_mismatch",
                "severity": "error",
                "entity_type": row["source_type"],
                "entity_id": row["source_id"],
                "risk_score": 80,
                # ملخص عربي للتنبيه
                "summary": "سجل إيراد بمبلغ محصّل بلا قيد مطابق في الدفتر.",
                "evidence": {"revenue_record_id": row["id"], "collected_amount_minor": row["collected_amount_minor"]},
            }
            for row in rows
        ]

    def _event_row(
        self,
        row: dict[str, Any],
        *,
        name_map: dict[str, dict[int, str]] | None = None,
    ) -> dict[str, Any]:
        row["metadata"] = _load(row.get("metadata_json"), {})

        # تسميات عربية دقيقة
        row["event_key_label"] = event_key_label(row.get("event_key"))
        row["category_label"] = category_label(row.get("category"))
        row["severity_label"] = severity_label(row.get("severity"))

        nm = name_map or {}

        row["actor_label"] = _make_entity_label(
            row.get("actor_type"),
            row.get("actor_id"),
            nm,
            label_map=ACTOR_TYPE_LABELS,
        )
        row["target_label"] = _make_entity_label(
            row.get("target_type"),
            row.get("target_id"),
            nm,
            label_map=TARGET_TYPE_LABELS,
        )

        return row

    def _flag_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["evidence"] = _load(row.get("evidence_json"), {})
        return row

    def _investigation_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["linked_events"] = _load(row.get("linked_events_json"), [])
        row["linked_flags"] = _load(row.get("linked_flags_json"), [])
        return row
