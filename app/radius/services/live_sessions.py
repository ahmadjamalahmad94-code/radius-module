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
    """هل الصفّ المفتوح ضمن نافذة الحياة؟ زومبي = طابع مُحلَّل أقدم من العتبة.

    خطأ الإنتاج الجذريّ: كانت المقارنة معجمية بين عتبة ISO وطابع FreeRADIUS
    «مسافة» (المسافة 0x20 < ‎'T' 0x54) فكلّ جلسة إنتاجيّة حيّة تبدو «أقدم» =
    زومبي فتُستبعَد → العدّ 0. هنا نُقارن كأوقات. غياب/تعذّر التحليل (جلسة
    لم يصلها محاسبة بعد) → تُحتسَب احتياطًا (لا نَقدر إثبات أنها ميّتة)."""
    last = _row_last_dt(row)
    return last is None or last >= cutoff


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
                               limit: int = 500) -> dict:
    """جلسات نشطة لراوتر واحد من radacct (المصدر الموثوق).

    يُرجع {count, hotspot, ppp, other, sessions:[{username, type, framed_ip,
    calling_station, started, uptime_sec}]}. مطابقة على IP العام أو نفق
    الواير جارد، ضمن نافذة الحياة.
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
    live: list[tuple[_dt.datetime, dict]] = []
    for r in rows:
        d = dict(r)
        if not _is_live(d, cutoff):
            continue
        # الأحدث أوّلًا؛ صفّ بلا طابع (جديد) → max كي يَتصدّر.
        live.append((_row_last_dt(d) or _dt.datetime.max, d))
    live.sort(key=lambda t: t[0], reverse=True)

    out = {"count": 0, "hotspot": 0, "ppp": 0, "other": 0, "sessions": []}
    for _, d in live[:int(limit)]:
        kind = _kind(d)
        out[kind] = out.get(kind, 0) + 1
        out["count"] += 1
        out["sessions"].append({
            "username": d.get("username") or "",
            "type": kind,
            "framed_ip": d.get("framedipaddress") or "",
            "calling_station": d.get("callingstationid") or "",
            "started": d.get("acctstarttime") or "",
            "uptime_sec": _uptime_seconds(d),
        })
    return out


def tenant_active_count(tenant_id: int, *, window_min: Optional[int] = None) -> int:
    """إجمالي الجلسات النشطة للمستأجر (لكل الراوترات) ضمن النافذة.

    نَسحب الصفوف المفتوحة ونُرشّحها بالوقت في بايثون (يَصِحّ لصيغتَي
    FreeRADIUS «مسافة» وISO «…T…Z» معًا) — كان العدّ يُرجع 0 في الإنتاج لأنّ
    المقارنة المعجمية تَستبعد طوابع FreeRADIUS «مسافة»."""
    cutoff = _cutoff_dt(window_min)
    rows = db().execute(
        "SELECT acctstarttime, acctupdatetime FROM radacct "
        "WHERE tenant_id=? AND (acctstoptime IS NULL OR acctstoptime='')",
        (int(tenant_id),),
    ).fetchall()
    return sum(1 for r in rows if _is_live(dict(r), cutoff))


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
