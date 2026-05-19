"""
accounting_puller — يستعلم MT routers بشكل دوري عن الجلسات النشطة.

⚠ R3 (default): الكتابة إلى radacct معطّلة. الـ FreeRADIUS الآن هو
الـ canonical writer لـ radacct (عبر rlm_sql_sqlite بعد R1+R2).
الـ puller يبقى يقوم بالاستعلام (heartbeat + مراقبة) فقط، ولا يكتب
INSERT/UPDATE على radacct. لإعادة تفعيل الـ writes الـ legacy:
    HOBERADIUS_ACCT_PULLER_WRITES=1
(للطوارئ فقط، عندما يفشل FreeRADIUS sql لسبب ما.)

التصميم:
- كل N ثانية (افتراضي 30s)، يمر على كل tenants.
- لكل tenant: يقرأ MT configs المفعّلة.
- لكل router: يطلب /ip/hotspot/active/print.
- يُحصي عدد الجلسات للـ heartbeat.
- إذا writes_enabled=True (legacy mode):
    - يُنشئ/يُحدّث صفًا في radacct لكل جلسة (key = acctsessionid).
    - يُحدّث subscribers.last_seen_at.
    - الجلسات التي اختفت → يضع acctstoptime + يُرسل webhook.

ملاحظات:
- لا يُغلق DB connections بين الـ ticks (نُعيد استخدامها).
- يَستخدم MT connection pool.
- إذا router fails → يُسجّل warning ويتابع.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime

from app.radius.db.connection import db, transaction
from app.radius.db.helpers import now_iso

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "accounting_puller"

_started = False
_started_lock = threading.Lock()


def _interval() -> int:
    try: return int(os.environ.get("HOBERADIUS_ACCT_INTERVAL_SEC") or 30)
    except ValueError: return 30


def acct_puller_writes_enabled() -> bool:
    """هل يجب أن يكتب الـ puller إلى radacct؟

    Default: False — بعد R3، الـ FreeRADIUS هو المصدر الوحيد لكتابة
    radacct (rlm_sql_sqlite في accounting{}). الـ puller يبقى يستعلم MT
    للـ heartbeat ولا يكتب.

    لإعادة تفعيل الـ legacy writes (للطوارئ فقط):
        HOBERADIUS_ACCT_PULLER_WRITES=1
    تُقبَل القيم: "1" / "true" / "yes" / "on" (case-insensitive).
    """
    raw = (os.environ.get("HOBERADIUS_ACCT_PULLER_WRITES") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _all_tenants() -> list[int]:
    cur = db().execute("SELECT id FROM tenants WHERE status = 'active'")
    return [r["id"] for r in cur.fetchall()]


def _enabled_routers(tenant_id: int) -> list[dict]:
    from app.radius.db.repos import mikrotik_repo
    return [r for r in mikrotik_repo.list_configs(tenant_id) if r["enabled"]]


def _safe_int(v) -> int:
    try: return int(v or 0)
    except (TypeError, ValueError): return 0


def _upsert_session(*, tenant_id: int, nas_addr: str, row: dict) -> None:
    """ينشئ/يُحدّث radacct row."""
    now = now_iso()
    session_id = row.get(".id") or row.get("session-id") or ""
    if not session_id: return
    username = row.get("user") or ""
    if not username: return

    bytes_in = _safe_int(row.get("bytes-in"))
    bytes_out = _safe_int(row.get("bytes-out"))
    uptime_sec = _parse_uptime_sec(row.get("uptime"))
    calling = row.get("mac-address") or ""
    framed_ip = row.get("address") or ""

    with transaction() as conn:
        existing = conn.execute(
            "SELECT radacctid, acctstarttime FROM radacct "
            "WHERE tenant_id = ? AND acctsessionid = ? AND nasipaddress = ?",
            (tenant_id, session_id, nas_addr)
        ).fetchone()
        if existing:
            conn.execute("""
                UPDATE radacct SET
                    acctupdatetime=?, acctsessiontime=?,
                    acctinputoctets=?, acctoutputoctets=?,
                    framedipaddress=?, callingstationid=?
                WHERE radacctid = ?
            """, (now, uptime_sec, bytes_in, bytes_out,
                  framed_ip, calling, existing["radacctid"]))
        else:
            conn.execute("""
                INSERT INTO radacct(tenant_id, acctsessionid, acctuniqueid, username,
                    nasipaddress, acctstarttime, acctupdatetime, acctsessiontime,
                    acctinputoctets, acctoutputoctets,
                    framedipaddress, callingstationid, servicetype, framedprotocol)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (tenant_id, session_id, session_id, username,
                  nas_addr, now, now, uptime_sec, bytes_in, bytes_out,
                  framed_ip, calling, "Login-User", "PPP"))

        # حدّث المستخدم
        conn.execute("""
            UPDATE subscribers
            SET last_seen_at = ?, online_count = COALESCE(online_count, 0)
            WHERE tenant_id = ? AND username = ?
        """, (now, tenant_id, username))


def _parse_uptime_sec(s) -> int:
    """يحوّل '1h2m3s' أو '5m' أو '30s' إلى ثوانٍ."""
    if not s: return 0
    s = str(s)
    total = 0
    buf = ""
    for ch in s:
        if ch.isdigit():
            buf += ch
        else:
            n = int(buf or 0)
            if   ch == "d": total += n * 86400
            elif ch == "h": total += n * 3600
            elif ch == "m": total += n * 60
            elif ch == "s": total += n
            elif ch == "w": total += n * 604800
            buf = ""
    if buf: total += int(buf)
    return total


def _close_stale_sessions(tenant_id: int, *, active_session_ids: set[str],
                          nas_addr: str) -> None:
    """يضع acctstoptime للجلسات في DB التي لم تعد موجودة على MT."""
    cur = db().execute("""
        SELECT radacctid, acctsessionid FROM radacct
        WHERE tenant_id = ? AND nasipaddress = ? AND acctstoptime IS NULL
    """, (tenant_id, nas_addr))
    now = now_iso()
    closed = 0
    for row in cur.fetchall():
        if row["acctsessionid"] not in active_session_ids:
            with transaction() as conn:
                conn.execute("""
                    UPDATE radacct SET acctstoptime = ?, acctterminatecause = 'User-Request'
                    WHERE radacctid = ?
                """, (now, row["radacctid"]))
            closed += 1
            # webhook session.stopped
            try:
                from app.webhooks.dispatcher import dispatch_event
                dispatch_event("session.stopped",
                               {"session_id": row["acctsessionid"]},
                               tenant_id=tenant_id)
            except Exception: pass
    if closed:
        _LOG.info("closed %d stale sessions for tenant=%d nas=%s", closed, tenant_id, nas_addr)


def _tick() -> int:
    """يُرجع العدد الإجمالي للجلسات المرئية في هذه الدورة.

    إذا acct_puller_writes_enabled() = False (default after R3) فإن هذا
    الـ tick يستعلم MT للـ heartbeat فقط، لا writes على radacct.
    الـ writes الفعلية تأتي من FreeRADIUS rlm_sql_sqlite في accounting{}.
    """
    from app.radius.integration.mikrotik.errors import MikrotikError
    from app.radius.integration.mikrotik.pool import acquire as acquire_mt
    writes_enabled = acct_puller_writes_enabled()
    total_sessions = 0
    for tenant_id in _all_tenants():
        for cfg in _enabled_routers(tenant_id):
            try:
                with acquire_mt(cfg) as c:
                    rows = list(c.print_("/ip/hotspot/active/print"))
            except MikrotikError as e:
                _LOG.warning("acct_puller: router=%s failed: %s", cfg["host"], e)
                continue
            active_ids: set[str] = set()
            for r in rows:
                if writes_enabled:
                    _upsert_session(tenant_id=tenant_id, nas_addr=cfg["host"], row=r)
                sid = r.get(".id") or r.get("session-id")
                if sid: active_ids.add(sid)
            total_sessions += len(active_ids)
            if writes_enabled:
                _close_stale_sessions(tenant_id, active_session_ids=active_ids,
                                       nas_addr=cfg["host"])
    return total_sessions


def _run_loop() -> None:
    interval = _interval()
    writes = acct_puller_writes_enabled()
    if writes:
        _LOG.warning(
            "accounting_puller started — interval=%ds, WRITES=ENABLED (legacy mode). "
            "FreeRADIUS sql + هذا الـ puller الآن يكتبان معًا → تكرار rows متوقّع. "
            "اضبط HOBERADIUS_ACCT_PULLER_WRITES=0 لإيقاف الـ writes (الافتراضي بعد R3).",
            interval,
        )
    else:
        _LOG.info(
            "accounting_puller started — interval=%ds, writes=DISABLED (R3 default). "
            "FreeRADIUS rlm_sql_sqlite هو الكاتب الوحيد لـ radacct. للطوارئ: "
            "HOBERADIUS_ACCT_PULLER_WRITES=1.",
            interval,
        )
    while True:
        sessions_seen = 0
        try:
            sessions_seen = _tick() or 0
        except Exception:  # noqa: BLE001
            _LOG.exception("accounting_puller tick failed")
        beat(_NAME, info={"interval_sec": interval, "last_sessions_seen": sessions_seen})
        time.sleep(interval)


def start_accounting_puller() -> None:
    global _started
    with _started_lock:
        if _started: return
        t = threading.Thread(target=_run_loop, daemon=True, name="hr-acct-puller")
        t.start()
        _started = True
