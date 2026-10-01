"""بيانات الاتصال على بطاقة المشترك في القائمة (تحميل · رفع · IP · مدّة).

قرار المالك 2026-10-01: المربّعات ثابتةٌ لكلّ بطاقات المشتركين — المتّصل يرى
جلسته الحاليّة، وغير المتّصل يرى **آخر جلسة** له (أو لا شيء إن لم يتّصل قطّ).

استعلامان مجمَّعان لصفحة القائمة كلّها (لا استعلامَ لكلّ صفّ): أحدثُ جلسةٍ
مفتوحة لكلّ اسم، وإلّا أحدثُ جلسة. ``bytes_in`` = ‏acctinputoctets (رفع
المشترك) و``bytes_out`` = ‏acctoutputoctets (تحميله) — نفس عقد «المتصلون».
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..db.connection import db
from ..db.helpers import parse_dt
from .access_type import classify_session

_CHUNK = 400


def _iso_z(value) -> str | None:
    dt = parse_dt(value) if not isinstance(value, datetime) else value
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat(timespec="seconds") + "Z"


def live_usage(tenant_id: int, usernames, *, now: datetime | None = None) -> dict[str, dict]:
    """{username: {online, bytes_in, bytes_out, framed_ip, started_at,
    stopped_at, session_time, access_type}} لمن له جلسةٌ في radacct."""
    names = sorted({u for u in usernames if u})
    if not names:
        return {}
    now = now or datetime.utcnow()
    ids: dict[str, int] = {}
    for i in range(0, len(names), _CHUNK):
        chunk = names[i:i + _CHUNK]
        ph = ",".join("?" for _ in chunk)
        # مفتوحة أوّلًا، ثمّ أحدثُ جلسةٍ لمن لا جلسةَ مفتوحةً له.
        for r in db().execute(
            f"SELECT username, MAX(radacctid) AS rid FROM radacct "
            f" WHERE tenant_id = ? AND username IN ({ph}) AND acctstoptime IS NULL "
            f" GROUP BY username", (int(tenant_id), *chunk)).fetchall():
            ids[r["username"]] = int(r["rid"])
        rest = [u for u in chunk if u not in ids]
        if rest:
            ph2 = ",".join("?" for _ in rest)
            for r in db().execute(
                f"SELECT username, MAX(radacctid) AS rid FROM radacct "
                f" WHERE tenant_id = ? AND username IN ({ph2}) GROUP BY username",
                (int(tenant_id), *rest)).fetchall():
                ids[r["username"]] = int(r["rid"])
    if not ids:
        return {}
    out: dict[str, dict] = {}
    rids = list(ids.values())
    for i in range(0, len(rids), _CHUNK):
        chunk = rids[i:i + _CHUNK]
        ph = ",".join("?" for _ in chunk)
        for r in db().execute(
            f"SELECT username, acctstarttime, acctstoptime, acctsessiontime, "
            f"       acctinputoctets, acctoutputoctets, framedipaddress, "
            f"       nasporttype, framedprotocol "
            f"  FROM radacct WHERE radacctid IN ({ph})", chunk).fetchall():
            started = parse_dt(r["acctstarttime"])
            stopped = parse_dt(r["acctstoptime"])
            online = r["acctstoptime"] in (None, "")
            if online and started is not None:
                st = started.replace(tzinfo=None) if started.tzinfo else started
                session_time = max(0, int((now - st).total_seconds()))
            else:
                session_time = int(r["acctsessiontime"] or 0)
            out[r["username"]] = {
                "online": bool(online),
                "bytes_in": int(r["acctinputoctets"] or 0),
                "bytes_out": int(r["acctoutputoctets"] or 0),
                "framed_ip": r["framedipaddress"] or "",
                "started_at": _iso_z(started),
                "stopped_at": _iso_z(stopped),
                "session_time": session_time,
                "access_type": classify_session(
                    nas_port_type=r["nasporttype"], framed_protocol=r["framedprotocol"]),
            }
    return out
