"""Normalized accounting event ingestion over radacct.

This is a small backend layer for tests and future stable APIs. FreeRADIUS
remains the canonical writer in production; this service does not touch auth,
CoA, billing, or ledger behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.radius.db.connection import db

STATUS_START = "Start"
STATUS_INTERIM = "Interim-Update"
STATUS_STOP = "Stop"
STATUS_ON = "Accounting-On"
STATUS_OFF = "Accounting-Off"

SUPPORTED_STATUS_TYPES = {STATUS_START, STATUS_INTERIM, STATUS_STOP, STATUS_ON, STATUS_OFF}


def _utcnow() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


class AccountingEventsService:
    def ingest(self, *, tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.normalize(tenant_id=tenant_id, payload=payload)
        status = event["status_type"]
        if status == STATUS_START:
            return self._start(event)
        if status == STATUS_INTERIM:
            return self._interim(event)
        if status == STATUS_STOP:
            return self._stop(event)
        if status in {STATUS_ON, STATUS_OFF}:
            return self._accounting_on_off(event)
        raise ValueError(f"unsupported accounting status type: {status}")

    def normalize(self, *, tenant_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        status = str(
            payload.get("status_type")
            or payload.get("Acct-Status-Type")
            or payload.get("acct_status_type")
            or ""
        ).strip()
        if status not in SUPPORTED_STATUS_TYPES:
            raise ValueError("unsupported or missing accounting status_type")
        username = str(payload.get("username") or payload.get("User-Name") or "").strip()
        session_id = str(payload.get("acct_session_id") or payload.get("Acct-Session-Id") or "").strip()
        nas_ip = str(payload.get("nas_ip_address") or payload.get("NAS-IP-Address") or "").strip()
        if status in {STATUS_START, STATUS_INTERIM, STATUS_STOP} and not session_id:
            raise ValueError("acct_session_id is required")
        if not nas_ip:
            raise ValueError("nas_ip_address is required")
        unique_id = str(
            payload.get("acct_unique_session_id")
            or payload.get("Acct-Unique-Session-Id")
            or f"{nas_ip}-{session_id}-{username}"
        ).strip()
        return {
            "tenant_id": int(payload.get("tenant_id") or tenant_id),
            "username": username,
            "acct_session_id": session_id,
            "acct_unique_session_id": unique_id,
            "nas_ip_address": nas_ip,
            "calling_station_id": str(payload.get("calling_station_id") or payload.get("Calling-Station-Id") or ""),
            "framed_ip_address": str(payload.get("framed_ip_address") or payload.get("Framed-IP-Address") or ""),
            "input_octets": _int(payload.get("input_octets") or payload.get("Acct-Input-Octets")),
            "output_octets": _int(payload.get("output_octets") or payload.get("Acct-Output-Octets")),
            "session_time": _int(payload.get("session_time") or payload.get("Acct-Session-Time")),
            "status_type": status,
        }

    def list_online(self, *, tenant_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM radacct
            WHERE tenant_id = ? AND acctstoptime IS NULL
            ORDER BY radacctid DESC
            LIMIT ?
            """,
            (int(tenant_id), max(1, min(int(limit or 100), 500))),
        ).fetchall()
        return [dict(row) for row in rows]

    def session_detail(self, *, tenant_id: int, session_id: str) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM radacct
            WHERE tenant_id = ? AND acctsessionid = ?
            ORDER BY radacctid DESC
            LIMIT 1
            """,
            (int(tenant_id), str(session_id)),
        ).fetchone()
        return dict(row) if row else None

    def list_history(self, *, tenant_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = db().execute(
            """
            SELECT * FROM radacct
            WHERE tenant_id = ?
            ORDER BY radacctid DESC
            LIMIT ?
            """,
            (int(tenant_id), max(1, min(int(limit or 100), 500))),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_stale(self, *, tenant_id: int, older_than_seconds: int = 3600) -> dict[str, Any]:
        cutoff = (datetime.utcnow() - timedelta(seconds=max(60, int(older_than_seconds or 3600)))).isoformat() + "Z"
        cur = db().execute(
            """
            UPDATE radacct
            SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, ?),
                acctterminatecause = 'Stale-Session-Timeout'
            WHERE tenant_id = ?
              AND acctstoptime IS NULL
              AND COALESCE(acctupdatetime, acctstarttime, '') < ?
            """,
            (_utcnow(), int(tenant_id), cutoff),
        )
        return {"closed": int(cur.rowcount or 0), "cutoff": cutoff}

    def _start(self, event: dict[str, Any]) -> dict[str, Any]:
        existing = self._open_session(event)
        if existing:
            return {"status": "idempotent", "session": existing}
        now = _utcnow()
        cur = db().execute(
            """
            INSERT INTO radacct (
              tenant_id, acctsessionid, acctuniqueid, username, nasipaddress,
              acctstarttime, acctupdatetime, callingstationid, framedipaddress,
              acctinputoctets, acctoutputoctets, acctsessiontime
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["tenant_id"],
                event["acct_session_id"],
                event["acct_unique_session_id"],
                event["username"],
                event["nas_ip_address"],
                now,
                now,
                event["calling_station_id"],
                event["framed_ip_address"],
                event["input_octets"],
                event["output_octets"],
                event["session_time"],
            ),
        )
        return {"status": "started", "session": dict(db().execute("SELECT * FROM radacct WHERE radacctid = ?", (cur.lastrowid,)).fetchone())}

    def _interim(self, event: dict[str, Any]) -> dict[str, Any]:
        now = _utcnow()
        cur = db().execute(
            """
            UPDATE radacct
            SET acctupdatetime = ?, acctinputoctets = ?, acctoutputoctets = ?,
                acctsessiontime = ?, framedipaddress = ?
            WHERE tenant_id = ? AND acctsessionid = ? AND nasipaddress = ?
              AND acctstoptime IS NULL
            """,
            (
                now,
                event["input_octets"],
                event["output_octets"],
                event["session_time"],
                event["framed_ip_address"],
                event["tenant_id"],
                event["acct_session_id"],
                event["nas_ip_address"],
            ),
        )
        return {"status": "updated" if cur.rowcount else "not_found", "session": self._open_session(event)}

    def _stop(self, event: dict[str, Any]) -> dict[str, Any]:
        now = _utcnow()
        cur = db().execute(
            """
            UPDATE radacct
            SET acctstoptime = ?, acctupdatetime = ?, acctinputoctets = ?,
                acctoutputoctets = ?, acctsessiontime = ?,
                acctterminatecause = COALESCE(NULLIF(acctterminatecause, ''), 'User-Request')
            WHERE tenant_id = ? AND acctsessionid = ? AND nasipaddress = ?
              AND acctstoptime IS NULL
            """,
            (
                now,
                now,
                event["input_octets"],
                event["output_octets"],
                event["session_time"],
                event["tenant_id"],
                event["acct_session_id"],
                event["nas_ip_address"],
            ),
        )
        return {
            "status": "stopped" if cur.rowcount else "not_found",
            "session": self.session_detail(tenant_id=event["tenant_id"], session_id=event["acct_session_id"]),
        }

    def _accounting_on_off(self, event: dict[str, Any]) -> dict[str, Any]:
        """Accounting-On/Off = the NAS declares a (re)start, so its prior open
        sessions are gone — close them.

        Guard against a SPURIOUS Accounting-On: on a flapping accounting link a
        router re-emits Accounting-On seconds after a genuine Acct-Start, which
        would wipe a session that is actually live. So we DO NOT close sessions
        that started/updated within a short debounce window (default 60s,
        ``HOBERADIUS_ACCT_ONOFF_DEBOUNCE_SEC``). A real reboot has no such
        just-started sessions, so all stale sessions are still closed normally,
        and a real per-session Acct-Stop is handled elsewhere (``_stop``).
        """
        import os

        try:
            debounce = int(os.environ.get("HOBERADIUS_ACCT_ONOFF_DEBOUNCE_SEC", "60"))
        except (TypeError, ValueError):
            debounce = 60
        debounce = max(0, debounce)
        now = _utcnow()
        nas_ip = event["nas_ip_address"]
        tenant_id = event["tenant_id"]

        if debounce <= 0:
            # Guard disabled — original behaviour (close every open session).
            cur = db().execute(
                """
                UPDATE radacct
                SET acctstoptime = ?, acctterminatecause = ?
                WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL
                """,
                (now, event["status_type"], tenant_id, nas_ip),
            )
            return {"status": "nas_reset", "closed": int(cur.rowcount or 0), "preserved": 0}

        cutoff = (datetime.utcnow() - timedelta(seconds=debounce)).isoformat() + "Z"
        preserved = int(db().execute(
            "SELECT COUNT(*) AS n FROM radacct "
            "WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL "
            "  AND COALESCE(acctupdatetime, acctstarttime, '') >= ?",
            (tenant_id, nas_ip, cutoff),
        ).fetchone()["n"] or 0)
        cur = db().execute(
            """
            UPDATE radacct
            SET acctstoptime = ?, acctterminatecause = ?
            WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL
              AND COALESCE(acctupdatetime, acctstarttime, '') < ?
            """,
            (now, event["status_type"], tenant_id, nas_ip, cutoff),
        )
        closed = int(cur.rowcount or 0)
        if preserved:
            try:
                import logging
                logging.getLogger("app.radius.accounting").warning(
                    "Ignored phantom %s from NAS %s: preserved %d just-started "
                    "session(s) (<%ds), closed %d stale session(s).",
                    event.get("status_type"), nas_ip, preserved, debounce, closed,
                )
            except Exception:  # noqa: BLE001 — logging must never break accounting
                pass
        return {"status": "nas_reset", "closed": closed, "preserved": preserved}

    def _open_session(self, event: dict[str, Any]) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM radacct
            WHERE tenant_id = ? AND acctsessionid = ? AND nasipaddress = ?
              AND acctstoptime IS NULL
            ORDER BY radacctid DESC
            LIMIT 1
            """,
            (event["tenant_id"], event["acct_session_id"], event["nas_ip_address"]),
        ).fetchone()
        return dict(row) if row else None
