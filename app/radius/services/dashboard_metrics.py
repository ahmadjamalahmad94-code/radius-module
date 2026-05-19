"""
DashboardMetricsService — مجموعة helpers تُجمّع KPIs الـ Dashboard بشكل مُجمَّع
(alerts / subscribers / cards / plans / system) دون كسر `DashboardService` الموجود.

التصميم:
- كل قسم في دالة مستقلة، يرفض السقوط — fallback آمن إلى dict فارغ/قيم 0/None.
- لا queries ثقيلة في الـ template — كل الحسابات هنا.
- System health مع caching قصير (30s) لأن psutil/socket قد يكونان أبطأ.
- لا external network calls حقيقية — فحوصات محلية فقط (DB ping بـ SELECT 1).
- متعدد الـ tenant: يقرأ tenant_id من Flask `g` كباقي الخدمات.
"""
from __future__ import annotations

import os
import shutil
import socket
import time
from typing import Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db


# ────────────────────────────────────────────────────────────────
# 1. helpers — tenant + safe DB getters
# ────────────────────────────────────────────────────────────────
def _tid() -> int:
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (ImportError, RuntimeError):
        return DEFAULT_TENANT_ID


def _scalar(sql: str, params=()) -> int:
    """يُرجع integer من query (COUNT). 0 عند أي فشل."""
    try:
        row = db().execute(sql, params).fetchone()
        if not row: return 0
        v = row[0] if isinstance(row, tuple) else (row.get("c") if hasattr(row, "get") else row[0])
        return int(v or 0)
    except Exception:
        return 0


# ────────────────────────────────────────────────────────────────
# 2. Subscribers section
# ────────────────────────────────────────────────────────────────
def get_subscriber_counts(tenant_id: Optional[int] = None) -> dict:
    """العدّادات بصيغة بسيطة. كل count محمي بـ _scalar."""
    t = tenant_id if tenant_id is not None else _tid()
    return {
        "total":         _scalar("SELECT COUNT(*) FROM subscribers WHERE tenant_id=?", (t,)),
        "active":        _scalar("SELECT COUNT(*) FROM subscribers WHERE tenant_id=? AND status='enabled'", (t,)),
        "expired":       _scalar("SELECT COUNT(*) FROM subscribers WHERE tenant_id=? AND status='expired'", (t,)),
        "suspended":     _scalar("SELECT COUNT(*) FROM subscribers WHERE tenant_id=? AND status='suspended'", (t,)),
        "disabled":      _scalar("SELECT COUNT(*) FROM subscribers WHERE tenant_id=? AND status='disabled'", (t,)),
        "banned":        _scalar("SELECT COUNT(*) FROM subscribers WHERE tenant_id=? AND status='banned'", (t,)),
        # ينتهي خلال 3 أيام (تستثني المنتهية فعلًا)
        "expiring_soon": _scalar(
            "SELECT COUNT(*) FROM subscribers "
            "WHERE tenant_id=? AND expire_at IS NOT NULL "
            "AND expire_at >= datetime('now') "
            "AND expire_at <  datetime('now','+3 days')", (t,)),
    }


def get_online_count(tenant_id: Optional[int] = None) -> int:
    """عدد الجلسات المفتوحة الآن (من radacct، لا live polling)."""
    t = tenant_id if tenant_id is not None else _tid()
    return _scalar(
        "SELECT COUNT(*) FROM radacct WHERE tenant_id=? AND acctstoptime IS NULL", (t,))


# ────────────────────────────────────────────────────────────────
# 3. Cards section
# ────────────────────────────────────────────────────────────────
def get_card_counts(tenant_id: Optional[int] = None) -> dict:
    t = tenant_id if tenant_id is not None else _tid()
    total = _scalar("SELECT COUNT(*) FROM cards WHERE tenant_id=?", (t,))
    used  = _scalar("SELECT COUNT(*) FROM cards WHERE tenant_id=? AND used=1", (t,))
    return {
        "total":     total,
        "used":      used,
        "available": max(0, total - used),
        "batches":   _scalar("SELECT COUNT(*) FROM card_batches WHERE tenant_id=?", (t,)),
    }


def get_recent_batches(*, limit: int = 5, tenant_id: Optional[int] = None) -> list[dict]:
    """آخر N حزمة — فقط ما يحتاجه القالب."""
    t = tenant_id if tenant_id is not None else _tid()
    try:
        rows = db().execute(
            "SELECT id, batch_code, package_name, count, generated, used, created_at "
            "FROM card_batches WHERE tenant_id=? ORDER BY id DESC LIMIT ?",
            (t, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ────────────────────────────────────────────────────────────────
# 4. Plans section
# ────────────────────────────────────────────────────────────────
def get_plan_counts(tenant_id: Optional[int] = None) -> dict:
    t = tenant_id if tenant_id is not None else _tid()
    total    = _scalar("SELECT COUNT(*) FROM access_plans WHERE tenant_id=?", (t,))
    enabled  = _scalar("SELECT COUNT(*) FROM access_plans WHERE tenant_id=? AND enabled=1", (t,))
    return {
        "total":    total,
        "enabled":  enabled,
        "disabled": max(0, total - enabled),
    }


def get_top_plan(tenant_id: Optional[int] = None) -> Optional[dict]:
    """أكثر باقة استخدامًا (placeholder لو لا اشتراكات)."""
    t = tenant_id if tenant_id is not None else _tid()
    try:
        row = db().execute(
            "SELECT p.id, p.name, COUNT(s.id) AS subs "
            "FROM access_plans p LEFT JOIN subscribers s "
            "  ON s.plan_id=p.id AND s.tenant_id=p.tenant_id "
            "WHERE p.tenant_id=? GROUP BY p.id, p.name "
            "ORDER BY subs DESC LIMIT 1", (t,)).fetchone()
        if not row or not row["subs"]:
            return None
        return {"id": row["id"], "name": row["name"], "subs": int(row["subs"])}
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────
# 5. NAS section
# ────────────────────────────────────────────────────────────────
def get_nas_summary(tenant_id: Optional[int] = None) -> dict:
    t = tenant_id if tenant_id is not None else _tid()
    return {
        "total":   _scalar("SELECT COUNT(*) FROM nas_devices WHERE tenant_id=?", (t,)),
        "enabled": _scalar("SELECT COUNT(*) FROM nas_devices WHERE tenant_id=? AND enabled=1", (t,)),
    }


# ────────────────────────────────────────────────────────────────
# 6. System health (cached 30s — psutil قد يكون بطيء)
# ────────────────────────────────────────────────────────────────
_BOOT_TIME = time.time()
_SYS_CACHE: dict = {"at": 0.0, "data": None}
_SYS_CACHE_TTL = 30.0


def _format_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d: return f"{d}ي {h}س {m}د"
    if h: return f"{h}س {m}د"
    return f"{m}د"


def get_system_health() -> dict:
    """صحة النظام مع caching 30s. لا external pings — سريع وآمن.
    fallback لكل metric — لا يفشل أبدًا."""
    now = time.time()
    if _SYS_CACHE["data"] is not None and (now - _SYS_CACHE["at"]) < _SYS_CACHE_TTL:
        return _SYS_CACHE["data"]

    out = {
        "db_ok":          False,
        "radius_ok":      False,
        "process_uptime": _format_uptime(now - _BOOT_TIME),
        "system_uptime":  None,
        "cpu_pct":        None,
        "ram_pct":        None,
        "disk_pct":       None,
        "hostname":       "",
    }

    # DB ping
    try:
        db().execute("SELECT 1").fetchone()
        out["db_ok"] = True
    except Exception:
        out["db_ok"] = False

    # Radius adapter health
    try:
        from ..integration.factory import get_radius_adapter
        out["radius_ok"] = bool(get_radius_adapter().healthcheck())
    except Exception:
        out["radius_ok"] = False

    # hostname
    try: out["hostname"] = socket.gethostname()
    except Exception: pass

    # psutil — optional
    try:
        import psutil  # type: ignore
        out["cpu_pct"]  = round(psutil.cpu_percent(interval=0.05), 1)
        out["ram_pct"]  = round(psutil.virtual_memory().percent, 1)
        out["disk_pct"] = round(psutil.disk_usage(os.getcwd()).percent, 1)
        out["system_uptime"] = _format_uptime(time.time() - psutil.boot_time())
    except Exception:
        # fallback: disk عبر shutil (لا CPU/RAM بدون psutil)
        try:
            u = shutil.disk_usage(os.getcwd())
            if u.total: out["disk_pct"] = round((u.total - u.free) / u.total * 100, 1)
        except Exception:
            pass

    _SYS_CACHE["at"] = now
    _SYS_CACHE["data"] = out
    return out


# ────────────────────────────────────────────────────────────────
# 7. Alerts (derived — لا queries إضافية)
# ────────────────────────────────────────────────────────────────
def build_alerts(*, subs: dict, cards: dict, plans: dict,
                  nas: dict, system: dict) -> list[dict]:
    """يُكوّن قائمة تنبيهات بناءً على المؤشرات. كل alert: {level, message, link?}.
    levels: danger | warn | info"""
    out: list[dict] = []

    # نظام
    if not system.get("db_ok"):
        out.append({"level": "danger",
                     "message": "تعذّر الاتصال بقاعدة البيانات."})
    if not system.get("radius_ok"):
        out.append({"level": "warn",
                     "message": "RADIUS adapter غير جاهز — افحص الإعدادات."})

    # موارد
    for k, label in (("cpu_pct", "CPU"), ("ram_pct", "RAM"), ("disk_pct", "Disk")):
        v = system.get(k)
        if v is not None and v >= 90:
            out.append({"level": "danger",
                         "message": f"استخدام {label} مرتفع جدًا ({v}%)."})
        elif v is not None and v >= 75:
            out.append({"level": "warn",
                         "message": f"استخدام {label} مرتفع ({v}%)."})

    # مشتركون
    exp_soon = subs.get("expiring_soon") or 0
    if exp_soon > 0:
        out.append({"level": "warn", "link_endpoint": "radius.users_list",
                     "message": f"{exp_soon} مشترك ينتهي اشتراكه خلال 3 أيام."})
    expired = subs.get("expired") or 0
    if expired > 0:
        out.append({"level": "info", "link_endpoint": "radius.users_list",
                     "message": f"{expired} مشترك انتهى اشتراكه — جدّد أو احذف."})

    # كروت
    avail = cards.get("available") or 0
    if cards.get("total", 0) > 0 and avail == 0:
        out.append({"level": "danger",
                     "message": "لا توجد كروت متاحة — وَلِّد دفعة جديدة."})
    elif 0 < avail < 10:
        out.append({"level": "warn", "link_endpoint": "radius.cards_generate",
                     "message": f"الكروت المتاحة منخفضة ({avail}) — جدّد المخزون."})

    # خطط
    if plans.get("total", 0) == 0:
        out.append({"level": "info", "link_endpoint": "radius.plans_new",
                     "message": "لا توجد باقات بعد — أنشئ أول باقة."})

    # NAS
    if nas.get("total", 0) == 0:
        out.append({"level": "info", "link_endpoint": "radius.devices_list",
                     "message": "لا توجد أجهزة NAS مُسجَّلة — أضِف router/AP."})

    return out


# ────────────────────────────────────────────────────────────────
# 8. واجهة موحَّدة — يُستدعى من route
# ────────────────────────────────────────────────────────────────
def build_dashboard_metrics(tenant_id: Optional[int] = None) -> dict:
    """يجمع كل المؤشرات في dict واحد للـ template. لا يرفع أبدًا."""
    t = tenant_id if tenant_id is not None else _tid()
    try: subs = get_subscriber_counts(t)
    except Exception: subs = {}
    # online من radacct — مستقل عن status
    subs["online"] = get_online_count(t)
    try: cards = get_card_counts(t)
    except Exception: cards = {}
    try: plans = get_plan_counts(t)
    except Exception: plans = {}
    top_plan = get_top_plan(t)
    if top_plan: plans["top"] = top_plan
    try: recent_batches = get_recent_batches(tenant_id=t, limit=5)
    except Exception: recent_batches = []
    try: nas = get_nas_summary(t)
    except Exception: nas = {}
    try: system = get_system_health()
    except Exception: system = {}
    alerts = build_alerts(subs=subs, cards=cards, plans=plans,
                            nas=nas, system=system)
    return {
        "subscribers":    subs,
        "cards":          cards,
        "recent_batches": recent_batches,
        "plans":          plans,
        "nas":            nas,
        "system":         system,
        "alerts":         alerts,
    }
