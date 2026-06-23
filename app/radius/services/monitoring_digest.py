"""monitoring_digest — الإشعارات الدوريّة للمراقبة (تذكير + تقرير أسطول).

يُكمّل تنبيه الانتقال لمرّة واحدة (انقطع/عاد) في device_health_alerts +
router_health_monitor بإضافتين احترافيّتين، تُغطّيان أجهزة device-health
والراوترات معًا:

  (1) تذكير الانقطاع المستمرّ: ما دام عنصرٌ مفصولًا/غير متاح، يُعاد إرسال تذكير
      كل فترة مضبوطة («🔴 ما زال «{name}» مفصولًا منذ …»)، مخنوقًا بالفترة (لا كل
      دورة استطلاع)، ويتوقّف عند التعافي (حينها يعمل تنبيه «عاد الاتصال» القائم).

  (2) تقرير الفحص الدوريّ: كل فترة أطول، رسالة واحدة موجزة لكامل الأسطول —
      «✅ كل شيء سليم» أو تقرير منظّم (مفصول/ضعف موارد/بنج عالٍ/سليم) بالأعداد
      والأسماء ونقطة الضعف المحدّدة (CPU/حرارة/ذاكرة/قرص/بنج).

الإرسال عبر المسار القانوني device_health_alerts.dispatch (تلجرام + الجرس دائمًا).
الفترتان + تشغيل/إيقاف لكلٍّ قابلة للضبط لكل مستأجر من الواجهة (tenant_settings).
الخَنق محفوظ في monitoring_notify_state.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Any, Optional

from ..db.helpers import parse_dt
from ..db.repos import device_health_repo as dh_repo
from ..db.repos import monitoring_notify_repo as notify_repo
from ..db.repos import tenants_repo
from . import device_health_alerts as dha
from . import router_resource_monitor as rrm

_LOG = logging.getLogger(__name__)

# مفاتيح الإعدادات + الافتراضات (tenant_settings).
_CFG = {
    "reminder_enabled": ("monitoring.reminder.enabled", "1"),
    "reminder_minutes": ("monitoring.reminder.minutes", "30"),
    "digest_enabled":   ("monitoring.digest.enabled", "1"),
    "digest_minutes":   ("monitoring.digest.minutes", "60"),
}

_DOWN_DEVICE = ("down", "timeout", "unavailable")
_DOWN_ROUTER = ("timeout", "unreachable")
_DIGEST_SCOPE = "digest"


# ── الإعدادات (واجهة، tenant_settings) ──────────────────────────

def get_periodic_config(tenant_id: int) -> dict:
    out: dict[str, Any] = {}
    for field, (key, default) in _CFG.items():
        raw = tenants_repo.get_setting(int(tenant_id), key, default)
        if field.endswith("_enabled"):
            out[field] = str(raw or "1").strip().lower() in ("1", "true", "yes", "on")
        else:
            try:
                out[field] = max(1, int(float(str(raw).strip() or default)))
            except (TypeError, ValueError):
                out[field] = int(default)
    return out


def set_periodic_config(tenant_id: int, values: dict, *, by: int = 0) -> dict:
    for field, (key, _default) in _CFG.items():
        if field not in values:
            continue
        v = values[field]
        if field.endswith("_enabled"):
            v = "1" if (v in (True, 1, "1", "on", "true", "yes")) else "0"
        else:
            try:
                v = str(max(1, int(float(v))))
            except (TypeError, ValueError):
                continue
        tenants_repo.set_setting(int(tenant_id), key, str(v), by=int(by))
    return get_periodic_config(tenant_id)


# ── تحويلات ─────────────────────────────────────────────────────

def _now() -> _dt.datetime:
    return _dt.datetime.utcnow()


def _age_sec(since_iso: str, now: _dt.datetime) -> Optional[float]:
    dt = parse_dt(since_iso)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return (now - dt).total_seconds()


def _human_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return ""
    s = int(seconds)
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts: list[str] = []
    if d:
        parts.append(f"{d} يوم")
    if h:
        parts.append(f"{h} ساعة")
    if m and not d:
        parts.append(f"{m} دقيقة")
    if not parts:
        return "أقل من دقيقة"
    return " و".join(parts)


# ── جمع حالة الأسطول ────────────────────────────────────────────

def _routers(tenant_id: int) -> list[dict]:
    from ..db.connection import db
    rows = db().execute(
        "SELECT id, name, address, description, last_check_status, last_check_at "
        "FROM nas_devices WHERE tenant_id=? AND enabled=1 "
        "  AND (deleted_at IS NULL OR deleted_at='') ORDER BY id",
        (int(tenant_id),)).fetchall()
    return [dict(r) for r in rows]


def _weaknesses(sample: dict, thresholds: dict) -> list[str]:
    """نقاط ضعف الموارد المتجاوِزة للعتبة، كنصوص محدّدة (للراوتر المتصل فقط)."""
    mv = rrm._metric_view(sample)
    out: list[str] = []
    if mv["cpu"] is not None and mv["cpu"] > thresholds["cpu_pct"]:
        out.append(f"المعالج {int(mv['cpu'])}%")
    if mv["temp"] is not None and mv["temp"] > thresholds["temp_c"]:
        out.append(f"الحرارة {mv['temp']}°م")
    if mv["ram"] is not None and mv["ram"] > thresholds["ram_pct"]:
        out.append(f"الذاكرة {round(mv['ram'])}%")
    if mv["disk"] is not None and mv["disk"] < thresholds["disk_free_pct"]:
        out.append(f"القرص الحرّ {round(mv['disk'])}%")
    if thresholds["traffic_mbps"] > 0 and mv["traffic"] is not None \
            and mv["traffic"] > thresholds["traffic_mbps"]:
        out.append(f"الحركة {round(mv['traffic'], 1)} م.ب/ث")
    return out


def collect(tenant_id: int, *, now: Optional[_dt.datetime] = None,
            seed: bool = True) -> dict:
    """يَجمع حالة الأسطول مصنّفة. seed=True ⇒ يَختم بداية الانقطاع للعناصر
    المفصولة حديثًا ويَمسح تذكيرات المتعافية (دورة حياة down_since موحّدة)."""
    tid = int(tenant_id)
    now = now or _now()
    thresholds = rrm.get_thresholds(tid)

    down: list[dict] = []
    high_latency: list[dict] = []
    weak: list[dict] = []
    total = 0
    active_scopes: set = set()

    # ── الأجهزة ──
    for d in dh_repo.list_devices(tid, monitoring_only=True):
        total += 1
        st = (d.get("status") or "").strip().lower()
        name = d.get("name") or f"#{d.get('id')}"
        if st in _DOWN_DEVICE:
            scope = f"reminder:device:{d['id']}"
            since = d.get("last_status_change_at") or ""
            down.append({"scope": scope, "name": name, "kind": "device",
                         "status": st, "since": since})
            active_scopes.add(scope)
        elif st == "high_latency":
            lat = d.get("last_latency_ms")
            high_latency.append({"name": name,
                                 "detail": f"{int(lat)}ms" if lat is not None else ""})

    # ── الراوترات ──
    res_map = rrm_latest_map(tid)
    for r in _routers(tid):
        total += 1
        st = (r.get("last_check_status") or "").strip().lower()
        name = r.get("name") or f"#{r['id']}"
        if st in _DOWN_ROUTER:
            scope = f"reminder:router:{r['id']}"
            down.append({"scope": scope, "name": name, "kind": "router",
                         "status": st, "since": ""})   # الراوتر بلا ختم انتقال → seed
            active_scopes.add(scope)
        else:
            # راوتر متصل (أو لم يُفحَص) — افحص ضعف الموارد من آخر عيّنة.
            sample = res_map.get(int(r["id"]))
            if sample and sample.get("ok"):
                w = _weaknesses(sample, thresholds)
                if w:
                    weak.append({"name": name, "items": w})

    # ── ختم بداية الانقطاع + مسح المتعافية (دورة حياة موحّدة) ──
    if seed:
        for item in down:
            row = notify_repo.get(tid, item["scope"])
            if row is None:
                # device: ختم الانتقال الموثوق؛ router: الآن (لا ختم انتقال متاح).
                ds = item["since"] or _iso(now)
                notify_repo.upsert(tid, item["scope"], down_since=ds, last_sent_at=ds)
                item["down_since"] = ds
            else:
                item["down_since"] = row.get("down_since") or item["since"] or ""
        notify_repo.clear_recovered(tid, active_scopes)
    else:
        for item in down:
            row = notify_repo.get(tid, item["scope"])
            item["down_since"] = (row.get("down_since") if row else "") or item["since"] or ""

    problems = len(down) + len(high_latency) + len(weak)
    return {
        "now": now, "total": total, "down": down,
        "high_latency": high_latency, "weak": weak,
        "problems": problems, "healthy": max(0, total - problems),
        "all_good": problems == 0,
    }


def rrm_latest_map(tenant_id: int) -> dict:
    from ..db.repos import router_resource_repo
    try:
        return router_resource_repo.latest_map(int(tenant_id))
    except Exception:  # noqa: BLE001 — الموارد تحسين، لا تَكسر التقرير
        return {}


def _iso(dt: _dt.datetime) -> str:
    return dt.replace(microsecond=0).isoformat() + "Z"


# ── (1) تذكير الانقطاع المستمرّ ─────────────────────────────────

def reminder_sweep(tenant_id: int, *, now: Optional[_dt.datetime] = None) -> int:
    """يُعيد إرسال تذكير لكل عنصر ما زال مفصولًا، مخنوقًا بالفترة المضبوطة.
    يُرجع عدد التذكيرات المُرسَلة. آمن لكل عنصر."""
    tid = int(tenant_id)
    now = now or _now()
    cfg = get_periodic_config(tid)
    state = collect(tid, now=now, seed=True)          # seed/clear دائمًا (يحفظ التناسق)
    if not cfg["reminder_enabled"]:
        return 0
    interval = cfg["reminder_minutes"] * 60
    sent = 0
    for item in state["down"]:
        try:
            row = notify_repo.get(tid, item["scope"]) or {}
            last_sent = row.get("last_sent_at") or item.get("down_since") or ""
            age = _age_sec(last_sent, now)
            if age is not None and age < interval:
                continue                              # ضمن نافذة الخنق
            dur = _human_duration(_age_sec(item.get("down_since") or "", now))
            msg = _reminder_message(item, dur)
            ok, _r = dha.dispatch(
                tid, alert_type="reminder_down", message=msg, name=item["name"],
                link="/admin/radius/device-health")
            notify_repo.upsert(tid, item["scope"], last_sent_at=_iso(now))
            if ok:
                sent += 1
        except Exception:  # noqa: BLE001 — عنصر واحد لا يكسر الكنس
            _LOG.exception("reminder_sweep failed for %s", item.get("scope"))
    return sent


def _reminder_message(item: dict, duration: str) -> str:
    name = item["name"]
    since = f" منذ {duration}" if duration else ""
    if item["kind"] == "router":
        head = f"🔴 ما زال الراوتر «{name}» غير متصل{since}."
    elif item["status"] == "unavailable":
        head = f"📵 ما زال «{name}» غير متاح (الراوتر مفصول){since}."
    else:
        head = f"🔴 ما زال «{name}» مفصولًا{since}."
    return head + "\n" + f"الوقت: {dha._now_human()}"


# ── (2) تقرير الفحص الدوريّ ─────────────────────────────────────

def digest_sweep(tenant_id: int, *, now: Optional[_dt.datetime] = None) -> int:
    """يُرسل تقرير الأسطول إن حان موعده (الفترة المضبوطة). يُرجع 1/0."""
    tid = int(tenant_id)
    now = now or _now()
    cfg = get_periodic_config(tid)
    if not cfg["digest_enabled"]:
        return 0
    row = notify_repo.get(tid, _DIGEST_SCOPE)
    last = (row or {}).get("last_sent_at") or ""
    age = _age_sec(last, now)
    if age is not None and age < cfg["digest_minutes"] * 60:
        return 0                                      # لم يحن بعد
    state = collect(tid, now=now, seed=True)
    if state["total"] == 0:
        notify_repo.upsert(tid, _DIGEST_SCOPE, last_sent_at=_iso(now))
        return 0                                      # لا عناصر مُراقَبة → لا تقرير
    msg = build_digest_message(state)
    atype = "fleet_digest_ok" if state["all_good"] else "fleet_digest_issues"
    ok, _r = dha.dispatch(
        tid, alert_type=atype, message=msg, name="الأسطول",
        link="/admin/radius/device-health")
    notify_repo.upsert(tid, _DIGEST_SCOPE, last_sent_at=_iso(now))
    return 1 if ok else 0


def build_digest_message(state: dict) -> str:
    """يَبني نص تقرير الفحص الدوريّ — سليم تمامًا أو منظّم بالملاحظات."""
    n_dev = sum(1 for d in state["down"] if d["kind"] == "device") \
        + len(state["high_latency"])
    total = state["total"]
    when = dha._now_human()
    if state["all_good"]:
        return (f"✅ تم الفحص الدوري — كل الأجهزة والراوترات سليمة "
                f"({total} عنصرًا مُراقَبًا).\nالوقت: {when}")

    lines = [f"⚠️ تقرير الفحص الدوري — {when}"]
    if state["down"]:
        parts = []
        for d in state["down"]:
            dur = _human_duration(_age_sec(d.get("down_since") or "", state["now"]))
            parts.append(f"«{d['name']}»" + (f" (منذ {dur})" if dur else ""))
        lines.append("🔴 مفصول: " + "، ".join(parts))
    if state["weak"]:
        parts = [f"«{w['name']}» ({' · '.join(w['items'])})" for w in state["weak"]]
        lines.append("🟠 ضعف موارد: " + "، ".join(parts))
    if state["high_latency"]:
        parts = [f"«{h['name']}»" + (f" ({h['detail']})" if h["detail"] else "")
                 for h in state["high_latency"]]
        lines.append("🐌 بنج عالٍ: " + "، ".join(parts))
    lines.append(f"✅ سليم: {state['healthy']} من {total}")
    return "\n".join(lines)
