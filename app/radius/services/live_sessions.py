"""live_sessions — مصدر «المتصلون الآن» الموثوق من جدول radacct.

لماذا radacct لا الـAPI الحيّ:
  حالة «متصل/المتصلون الآن» كانت تأتي حصراً من اتصال RouterOS-API حيّ
  (counters / hotspot-active / ppp-active). راوتر غير قابل للوصول عبر الـAPI
  (RADIUS-only / خلف NAT بلا منفذ API / لوحة بلا API token) يَظهر فارغاً رغم
  أنه يخدم مستخدمين فعلاً. radacct هو الحقيقة: أي جلسة مفتوحة تُثبت أن الراوتر
  حيّ ويمرّر RADIUS — بصرف النظر عن نوع الاتصال (واير جارد/SSTP/مباشر) أو
  توفّر الـAPI.

مطابقة الراوتر بجلساته (آمنة للنفق):
  مصدر حساب الجلسة (radacct.nasipaddress) هو IP المصدر كما يراه FreeRADIUS:
  للراوتر المباشر = الـIP العام (= nas_devices.address)؛ وللراوتر عبر نفق
  واير جارد/SSTP = IP النفق (= nas_devices.vpn_peer_address). لذا نطابق على
  ``nasipaddress IN (address, vpn_peer_address)`` فيَظهر متّصلاً في الحالتين.

نافذة الحياة (freshness):
  «جلسة نشطة» = ``acctstoptime IS NULL`` مع آخر تحديث/بدء ضمن نافذة. مايكروتيك
  يُرسل Acct-Interim-Interval كل 60s (freeradius_translator)، فنافذة 15 دقيقة
  تَحتمل ~15 تحديثاً مفقوداً (تعثّر شبكة/راوتر) قبل اعتبار الجلسة زومبي — تمنع
  عدّ جلسات لم يصل لها accounting-stop. قابلة للضبط عبر
  ``HOBERADIUS_LIVE_SESSION_WINDOW_MIN``.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Mapping, Optional

from ..core import env_settings
from ..db.connection import db
from .device_limit import acct_norm_sql, parse_acct_dt, to_space_ts
from .live_session_control import detect_session_type

# نافذة اعتبار الجلسة/الراوتر «حيّاً» (دقائق). 15 = ~15 تحديث interim (60s) مفقود.
_DEFAULT_WINDOW_MIN = 15


def window_minutes() -> int:
    raw = (env_settings.env("HOBERADIUS_LIVE_SESSION_WINDOW_MIN") or "").strip()
    try:
        return max(1, int(raw)) if raw else _DEFAULT_WINDOW_MIN
    except ValueError:
        return _DEFAULT_WINDOW_MIN


def _cutoff_dt(window_min: Optional[int] = None) -> _dt.datetime:
    """عتبة الحياة كـ datetime UTC ساذج — للمقارنة بالوقت (لا بالنصّ)."""
    w = window_min if window_min is not None else window_minutes()
    return _dt.datetime.utcnow() - _dt.timedelta(minutes=w)


def _row_last_dt(row: Mapping[str, Any]) -> Optional[_dt.datetime]:
    """آخر إشارة حياة للصفّ (acctupdatetime ثمّ acctstarttime) كـ datetime، أو
    None إن غابت/تعذّر تحليلها. يُحلّل صيغتَي FreeRADIUS «مسافة» و ISO «…T…Z»."""
    return (parse_acct_dt(row.get("acctupdatetime"))
            or parse_acct_dt(row.get("acctstarttime")))


def _is_live(row: Mapping[str, Any], cutoff: _dt.datetime) -> bool:
    """Is this open row genuinely active *now*? Requires positive evidence of
    recent life: a parseable acctstarttime/acctupdatetime within the window.

    Root production bug #1 (timestamp format): the window compare used to be
    lexical between an ISO cutoff and a FreeRADIUS «space» timestamp (0x20 < 'T'
    0x54), so every live production row looked «older» = zombie and the count
    collapsed to 0. We compare as datetimes here, which is correct for both
    formats.

    Root production bug #2 (phantom import rows): a row with NO parseable
    start/update timestamp at all is NOT a session this system created — every
    insert path (FreeRADIUS rlm_sql on Accounting-Start, mt_reconciler
    materialize) always writes acctstarttime. Such a row is a foreign import
    artifact (e.g. a MySQL radacct dump restored with acctstoptime IS NULL and
    NULL timestamps) that never got an Accounting-Stop. It must NOT count as
    live — it inflates «connected now» counters forever while contributing a
    blank, meaningless row to the list. We can only prove a session live with a
    fresh timestamp, so «no timestamp» ⇒ not live (reaped separately by
    session_reconciler)."""
    last = _row_last_dt(row)
    return last is not None and last >= cutoff


def resolve_real_types(tenant_id: int, usernames) -> dict[str, str]:
    """Map each username that resolves to a REAL subscriber or card in this
    tenant → its kind (``'card'`` | ``'subscriber'``).

    A username absent from the returned map is a router-local / trial artifact —
    a MikroTik hotspot mac-cookie session (``T-<MAC>``, "trying to log in by
    mac-cookie"), the router's built-in trial (``Default service`` / ``مؤقت``),
    or any name we never issued a RADIUS Access-Accept for. Those must NOT count
    or show as «connected now»: they authenticate against the router, not us.

    Cards win the classification (a card may also carry a mirror ``subscribers``
    row with ``user_type='card'``). Matching mirrors the /online list join
    (``subscribers.username`` / ``cards.username`` for this tenant) so the list,
    the counters, and this resolver always agree. Never raises — an empty map on
    error keeps callers falling back to their pre-filter behaviour."""
    names = sorted({str(u).strip() for u in usernames if str(u or "").strip()})
    if not names:
        return {}
    out: dict[str, str] = {}
    ph = ",".join("?" * len(names))
    # subscribers first, then cards, so a card overrides to 'card'.
    for table, kind in (("subscribers", "subscriber"), ("cards", "card")):
        try:
            rows = db().execute(
                f"SELECT username FROM {table} "
                f"WHERE tenant_id=? AND username IN ({ph})",
                [int(tenant_id), *names],
            ).fetchall()
        except Exception:  # noqa: BLE001 — a bad/absent table must not break the view
            rows = []
        for r in rows:
            out[str(r["username"])] = kind
    return out


def count_real_sessions(tenant_id: int, usernames) -> int:
    """Count how many of ``usernames`` (a per-session sequence, duplicates kept)
    resolve to a real subscriber/card — used to feed «connected now» counters
    from raw router active-session lists without surfacing trial/local ones."""
    real = resolve_real_types(int(tenant_id), usernames)
    return sum(1 for u in usernames if str(u or "").strip() in real)


def router_match_ips(nas: Mapping[str, Any]) -> list[str]:
    """عناوين IP التي قد تَصل عليها جلسات هذا الراوتر: العام + نفق الواير جارد."""
    ips: list[str] = []
    addr = str((nas or {}).get("address") or "").strip()
    vpn = str((nas or {}).get("vpn_peer_address") or "").strip()
    if addr:
        ips.append(addr)
    if vpn and vpn != addr:
        ips.append(vpn)
    return ips


def _kind(row: Mapping[str, Any]) -> str:
    """ppp (برودباند) / hotspot (بوابة دخول) / other.

    أولاً المُصنّف المشترك (nasporttype/servicetype)، فإن لم يَحسم نَستند إلى
    سمة IETF القياسية ``Framed-Protocol = PPP`` (تَصلنا كـ framedprotocol) —
    إشارة موثوقة على جلسة برودباند حتى لو كان nasporttype عامًّا."""
    t = detect_session_type(dict(row))
    if not t:
        proto = str(row.get("framedprotocol") or "").strip().lower()
        if "ppp" in proto:
            t = "pppoe"
    if t == "pppoe":
        return "ppp"
    if t == "hotspot":
        return "hotspot"
    return "other"


def _uptime_seconds(row: Mapping[str, Any]) -> Optional[int]:
    """مدّة الجلسة: acctsessiontime إن وُجد، وإلا من acctstarttime حتى الآن."""
    secs = row.get("acctsessiontime")
    try:
        if secs is not None and int(secs) > 0:
            return int(secs)
    except (TypeError, ValueError):
        pass
    s = parse_acct_dt(row.get("acctstarttime"))
    if s is None:
        return None
    return max(0, int((_dt.datetime.utcnow() - s).total_seconds()))


def active_sessions_for_router(tenant_id: int, nas: Mapping[str, Any], *,
                               window_min: Optional[int] = None,
                               limit: int = 500,
                               real_only: bool = False) -> dict:
    """جلسات نشطة لراوتر واحد من radacct (المصدر الموثوق).

    يُرجع {count, hotspot, ppp, other, sessions:[{username, type, user_type,
    framed_ip, calling_station, started, uptime_sec}]}. مطابقة على IP العام أو
    نفق الواير جارد، ضمن نافذة الحياة.

    ``real_only=True`` (used by «المتصلون الآن» card) additionally excludes any
    session whose username does NOT resolve to a real subscriber/card in this
    tenant — i.e. MikroTik mac-cookie (``T-<MAC>``) and built-in trial
    (``Default service`` / ``مؤقت``) sessions, which never got an Access-Accept
    from us. The invariant ``hotspot+ppp+other == count == len(sessions)`` holds
    after the exclusion.
    """
    ips = router_match_ips(nas)
    empty = {"count": 0, "hotspot": 0, "ppp": 0, "other": 0, "sessions": []}
    if not ips:
        return empty
    cutoff = _cutoff_dt(window_min)
    ph = ",".join("?" * len(ips))
    # نَسحب الصفوف المفتوحة لهذا الراوتر بلا عتبة معجمية في SQL، ثمّ نُرشّح
    # ونُرتّب بالوقت في بايثون (يَصِحّ لصيغتَي FreeRADIUS وISO معًا).
    rows = db().execute(
        "SELECT username, nasporttype, servicetype, framedprotocol, "
        "       framedipaddress, callingstationid, acctsessionid, "
        "       acctstarttime, acctupdatetime, acctsessiontime "
        "FROM radacct "
        "WHERE tenant_id=? AND (acctstoptime IS NULL OR acctstoptime='') "
        f"  AND nasipaddress IN ({ph}) ",
        [int(tenant_id), *ips],
    ).fetchall()
    live: list[tuple[_dt.datetime, int, dict]] = []
    for idx, r in enumerate(rows):
        d = dict(r)
        if not _is_live(d, cutoff):
            continue
        # الأحدث أوّلًا. المفتاح الثانويّ (idx) يَمنع مقارنة القواميس حين
        # يَتساوى طابعان — sort على (datetime, dict) يَرمي TypeError عند التعادل.
        live.append((_row_last_dt(d) or _dt.datetime.max, idx, d))
    live.sort(key=lambda t: (t[0], t[1]), reverse=True)

    real_types: dict[str, str] = {}
    if real_only:
        real_types = resolve_real_types(
            int(tenant_id), [d.get("username") for (_, _idx, d) in live])

    out = {"count": 0, "hotspot": 0, "ppp": 0, "other": 0, "sessions": []}
    for _, _idx, d in live[:int(limit)]:
        uname = str(d.get("username") or "").strip()
        if real_only and uname not in real_types:
            continue  # router-local / trial — never issued an Access-Accept
        kind = _kind(d)
        out[kind] = out.get(kind, 0) + 1
        out["count"] += 1
        out["sessions"].append({
            "username": d.get("username") or "",
            "type": kind,
            "user_type": real_types.get(uname, ""),
            "framed_ip": d.get("framedipaddress") or "",
            "calling_station": d.get("callingstationid") or "",
            "started": d.get("acctstarttime") or "",
            "uptime_sec": _uptime_seconds(d),
        })
    return out


def tenant_active_count(tenant_id: int, *, window_min: Optional[int] = None,
                        real_only: bool = False) -> int:
    """إجمالي الجلسات النشطة للمستأجر (لكل الراوترات) ضمن النافذة.

    نَسحب الصفوف المفتوحة ونُرشّحها بالوقت في بايثون (يَصِحّ لصيغتَي
    FreeRADIUS «مسافة» وISO «…T…Z» معًا) — كان العدّ يُرجع 0 في الإنتاج لأنّ
    المقارنة المعجمية تَستبعد طوابع FreeRADIUS «مسافة».

    ``real_only=True`` counts only sessions whose username resolves to a real
    subscriber/card (excludes mac-cookie ``T-<MAC>`` and ``Default service`` /
    ``مؤقت`` trial) so the «connected now» number matches the filtered list."""
    cutoff = _cutoff_dt(window_min)
    rows = db().execute(
        "SELECT username, acctstarttime, acctupdatetime FROM radacct "
        "WHERE tenant_id=? AND (acctstoptime IS NULL OR acctstoptime='')",
        (int(tenant_id),),
    ).fetchall()
    live_names = [dict(r).get("username") for r in rows if _is_live(dict(r), cutoff)]
    if not real_only:
        return len(live_names)
    real = resolve_real_types(int(tenant_id), live_names)
    return sum(1 for u in live_names if str(u or "").strip() in real)


def live_map(tenant_id: int, *, window_min: Optional[int] = None) -> dict[str, dict]:
    """خريطة nasipaddress → {active, last_seen} لكل المستأجر بدفعة واحدة.

    `active`   = عدد الجلسات المفتوحة ضمن النافذة على هذا الـIP.
    `last_seen`= آخر نشاط محاسبي (مفتوح أو مغلق) على هذا الـIP — يكشف راوتراً
                 يمرّر RADIUS الآن حتى لو لا جلسة متزامنة هذه اللحظة.
    يُستهلك من mt_operations لاشتقاق «متصل» لكل راوتر بصرف النظر عن الـAPI.

    `active` يُحسب بترشيح بايثون على الصفوف المفتوحة (وقت لا نصّ). `last_seen`
    يَمسح الصفوف (مفتوحة/مغلقة) بتطبيع SQL مُوحَّد (acct_norm_sql) فيَصِحّ
    لكلتا الصيغتين معًا، ويُعيد طابعًا مُطبَّعًا «مسافة» قابلًا للمقارنة لاحقًا.
    """
    cutoff = _cutoff_dt(window_min)
    cutoff_s = to_space_ts(cutoff.isoformat())  # حدّ بصيغة «مسافة» للتطبيع
    out: dict[str, dict] = {}
    # active: الصفوف المفتوحة، مُرشَّحة بالوقت في بايثون (fail-safe يَعدّ المُتعذِّر).
    for r in db().execute(
        "SELECT nasipaddress AS ip, acctstarttime, acctupdatetime FROM radacct "
        "WHERE tenant_id=? AND (acctstoptime IS NULL OR acctstoptime='')",
        (int(tenant_id),),
    ).fetchall():
        d = dict(r)
        if not _is_live(d, cutoff):
            continue
        e = out.setdefault(str(d.get("ip") or ""), {})
        e["active"] = int(e.get("active", 0)) + 1
    # last_seen: أيّ صفّ ضمن النافذة (مفتوح/مغلق) — تطبيع SQL لمقارنة صحيحة.
    norm = acct_norm_sql("COALESCE(acctupdatetime, acctstarttime)")
    for r in db().execute(
        "SELECT nasipaddress AS ip, "
        f"       MAX({norm}) AS last_seen "
        "FROM radacct "
        f"WHERE tenant_id=? AND {norm} >= ? "
        "GROUP BY nasipaddress",
        (int(tenant_id), cutoff_s),
    ).fetchall():
        out.setdefault(str(r["ip"] or ""), {})["last_seen"] = str(r["last_seen"] or "")
    return out


def router_live(nas: Mapping[str, Any], lmap: dict[str, dict]) -> dict:
    """يدمج إشارات radacct لراوتر واحد من live_map. يُرجع
    {online, active, last_seen} — online لو جلسات نشطة>0 أو نشاط حديث."""
    active = 0
    last_seen = ""
    for ip in router_match_ips(nas):
        info = lmap.get(ip)
        if not info:
            continue
        active += int(info.get("active") or 0)
        ls = str(info.get("last_seen") or "")
        if ls > last_seen:
            last_seen = ls
    return {"online": bool(active > 0 or last_seen), "active": active,
            "last_seen": last_seen}
