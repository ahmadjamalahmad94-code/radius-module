"""Normalized accounting event ingestion over radacct.

This is a small backend layer for tests and future stable APIs. FreeRADIUS
remains the canonical writer in production; this service does not touch auth,
CoA, billing, or ledger behavior.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.radius.db.connection import db
from app.radius.services.device_limit import acct_norm_sql, to_space_ts

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
        # تطبيع طابع العمود والعتبة لصيغة «مسافة» موحَّدة قبل المقارنة، وإلّا
        # لظلّ طابع FreeRADIUS «مسافة» يبدو «أقدم» من عتبة ISO فيُغلَق صفّ حيّ
        # خطأً (المسافة 0x20 < ‎'T' 0x54). راجع device_limit.acct_norm_sql.
        norm = acct_norm_sql("COALESCE(acctupdatetime, acctstarttime)")
        cur = db().execute(
            f"""
            UPDATE radacct
            SET acctstoptime = COALESCE(acctupdatetime, acctstarttime, ?),
                acctterminatecause = 'Stale-Session-Timeout'
            WHERE tenant_id = ?
              AND acctstoptime IS NULL
              AND {norm} < ?
            """,
            (_utcnow(), int(tenant_id), to_space_ts(cutoff)),
        )
        return {"closed": int(cur.rowcount or 0), "cutoff": cutoff}

    def _start(self, event: dict[str, Any]) -> dict[str, Any]:
        existing = self._open_session(event)
        if existing:
            return {"status": "idempotent", "session": existing}
        # ميزة «بطاقة مشتركة / يومية»: قبل تسجيل الجلسة الجديدة نفصل أي
        # جلسة سابقة فعّالة لنفس الاسم إن كانت بطاقته تنتمي لعرض عليه علم
        # shared_single_session=1 (جلسة واحدة فعّالة — الدخول الجديد يفصل
        # القديم). نعيد استخدام disconnect_user (نفس مسار CoA الذي يستخدمه
        # زر «قطع اتصال» في صفحة المتصلين) — لا نُعيد تنفيذ منطق الفصل.
        kick_summary = self._maybe_kick_previous_shared_session(event)
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
        result = {"status": "started", "session": dict(db().execute("SELECT * FROM radacct WHERE radacctid = ?", (cur.lastrowid,)).fetchone())}
        if kick_summary is not None:
            result["shared_session_kick"] = kick_summary
        return result

    def _plan_requires_single_session(self, *, tenant_id: int, username: str) -> bool:
        """True عندما يكون اسم المستخدم بطاقةً تنتمي لعرض عليه علم
        shared_single_session=1 (بطاقة مشتركة/يومية — مقعد واحد).

        نطابق الكرت باسم المستخدم داخل نفس المستأجِر (كما يفعل
        _selected_online_row في صفحة المتصلين)، ثم نقرأ علم العرض. أي
        خطأ/غياب جدول يُعامَل كـ «لا إنفاذ» (نُرجع False) حتى لا يكسر
        مسار المحاسبة الحرِج أبدًا."""
        if not username:
            return False
        try:
            row = db().execute(
                """
                SELECT p.shared_single_session AS flag
                  FROM cards c
                  JOIN access_plans p ON p.id = c.plan_id
                 WHERE c.tenant_id = ? AND c.username = ?
                 ORDER BY c.id DESC
                 LIMIT 1
                """,
                (int(tenant_id), username),
            ).fetchone()
        except Exception:  # noqa: BLE001 — never break accounting on a lookup error
            return False
        return bool(row and row["flag"])

    def _maybe_kick_previous_shared_session(
        self, event: dict[str, Any]
    ) -> dict[str, Any] | None:
        """يفصل الجلسات السابقة الفعّالة لاسم مستخدم بطاقة مشتركة قبل بدء
        الجلسة الجديدة.

        يُرجع None عندما لا ينطبق الإنفاذ (ليس بطاقة مشتركة، أو لا اسم
        مستخدم). وإلا يُرجع ملخّص بالنتيجة. آمن: أي استثناء يُبتلَع — لا
        يُسمح له بكسر تسجيل الجلسة.

        التوقيت: الإنفاذ يحدث عند Accounting-Start، أي بعد أن يكون NAS قد
        قبِل المصادقة وبدأ الجلسة الجديدة فعليًا. لذلك «الدخول الجديد يفصل
        القديم» وليس «يمنع الدخول الجديد» — وهو ما يطابق سلوك البطاقة
        المشتركة المطلوب (طرد المستخدم السابق ليُكمل الجديد)."""
        tenant_id = int(event["tenant_id"])
        username = event["username"]
        new_session_id = event["acct_session_id"]
        if not self._plan_requires_single_session(
            tenant_id=tenant_id, username=username
        ):
            return None
        try:
            from ..integration.radius_coa import (
                disconnect_user,
                find_all_nas_for_sessions,
            )

            # كل جلسات هذا الاسم الفعّالة الآن (قبل INSERT للجلسة الجديدة).
            # نستثني الجلسة الجديدة نفسها بالمعرّف احتياطًا لو سبق NAS
            # وكتب صفًّا لها (مسارنا يكتب لاحقًا، لكن FreeRADIUS الإنتاجي
            # قد يكون كتب الصف قبل وصول هذا الحدث).
            sessions = find_all_nas_for_sessions(tenant_id, username)
            stale_ids = [
                s["session_id"]
                for s in sessions
                if s.get("session_id") and s["session_id"] != new_session_id
            ]
            if not stale_ids:
                return {"kicked": 0, "reason": "no_previous_session"}
            res = disconnect_user(tenant_id, username, session_ids=stale_ids)
            return {
                "kicked": len(stale_ids),
                "coa_ok": bool(getattr(res, "ok", False)),
                "coa_code": getattr(res, "code_name", ""),
                "session_ids": stale_ids,
            }
        except Exception as exc:  # noqa: BLE001
            try:
                import logging

                logging.getLogger("app.radius.accounting").warning(
                    "shared-session kick failed for %s: %s", username, exc
                )
            except Exception:  # noqa: BLE001
                pass
            return {"kicked": 0, "error": str(exc)}

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
        # Wave-B: أعلام «الفصل» للبطاقة (lock_to_mac_on_close +
        # close_user_session_on_disconnect). تُطلَق فقط حين سُجِّل Stop فعليّ
        # (rowcount>0) كي لا تتكرّر على أحداث مكرّرة. محصّنة بالكامل — لا
        # تُفشِل تسجيل المحاسبة أبدًا.
        if cur.rowcount:
            try:
                from .card_batch_flags import on_disconnect
                on_disconnect(
                    event["tenant_id"], event["username"],
                    event.get("calling_station_id", ""),
                    session_id=event["acct_session_id"])
            except Exception:  # noqa: BLE001
                import logging
                logging.getLogger("app.radius.accounting").warning(
                    "card_batch_flags.on_disconnect failed for %s",
                    event.get("username"), exc_info=True)
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
        # تطبيع موحَّد كي يَصِحّ حدّ الـdebounce لصيغتَي FreeRADIUS «مسافة»
        # وISO معًا — وإلّا لَما حُفظت جلسة إنتاجيّة «للتوّ» (مسافة) من المسح
        # الزائف عند Accounting-On مرتدّ. راجع device_limit.acct_norm_sql.
        norm = acct_norm_sql("COALESCE(acctupdatetime, acctstarttime)")
        cutoff_cmp = to_space_ts(cutoff)
        preserved = int(db().execute(
            "SELECT COUNT(*) AS n FROM radacct "
            "WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL "
            f"  AND {norm} >= ?",
            (tenant_id, nas_ip, cutoff_cmp),
        ).fetchone()["n"] or 0)
        cur = db().execute(
            f"""
            UPDATE radacct
            SET acctstoptime = ?, acctterminatecause = ?
            WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL
              AND {norm} < ?
            """,
            (now, event["status_type"], tenant_id, nas_ip, cutoff_cmp),
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
