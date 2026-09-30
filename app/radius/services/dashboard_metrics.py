"""
DashboardMetricsService — مجموعة helpers تُجمّع KPIs الـ Dashboard بشكل مُجمَّع
(alerts / subscribers / cards / plans / system) دون كسر `DashboardService` الموجود.

التصميم:
- كل قسم في دالة مستقلة، يرفض السقوط — fallback آمن إلى dict فارغ/قيم 0/None.
- لا queries ثقيلة في الـ template — كل الحسابات هنا.
- System health مع caching قصير (30s) لأن فحص VPS والشبكة قد يكون أبطأ.
- متعدد الـ tenant: يقرأ tenant_id من Flask `g` كباقي الخدمات.
"""
from __future__ import annotations

import time
from typing import Optional

from flask_babel import gettext as _

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
    """عدّادات «المشتركون» لبطاقات اللوحة/النظرة العامة.

    كلّها تمرّ عبر مصدر واحد `subscribers_repo.count_subscribers(user_type=
    'subscriber')` — نفس تصنيف صفحة «المشتركون» (قائمة users_list) بالضبط:
    يستثني صفوف الكروت (user_type='card'، ومنها مرايا الكروت المستوردة)،
    وكروت المتجر (card_marketplace)، وأيّ صفّ موجود في جدول cards
    (مطابقةً لـ resolve_real_types)، والمحذوفين (deleted_at IS NULL). فلا
    تُحسب الكروت المستوردة مشتركين بعد الآن (كانت تنفخ العدّاد إلى آلاف).
    «expired/expiring_soon» تظلّان بنفس نطاق روابط بطاقة «ما يحتاج انتباه».
    """
    from ..db.repos import subscribers_repo
    t = tenant_id if tenant_id is not None else _tid()

    def _n(**kw) -> int:
        try:
            return int(subscribers_repo.count_subscribers(
                t, user_type="subscriber", **kw))
        except Exception:  # noqa: BLE001 — لا نكسر اللوحة
            return 0

    out = {
        "total":         _n(),
        "active":        _n(status="enabled"),
        "expired":       _n(status="expired"),
        "suspended":     _n(status="suspended"),
        "disabled":      _n(status="disabled"),
        "banned":        _n(status="banned"),
        # ينتهي خلال 3 أيام — المفعّلون فقط، مطابقةً لبطاقة صفحة المشتركين.
        "expiring_soon": _n(status="enabled", expiring_within_days=3),
    }
    # «أخرى»: ما لا يقع في الفئات الخمس (pending/trial/حالة غير معروفة/NULL أو
    # مفعّل بتاريخ انتهاء غير قابل للقراءة) — كي تُجزّئ الفئاتُ الإجماليَّ تمامًا
    # (كان 764+126+78+15+15 = 998 من 1,004).
    grouped = sum(out[k] for k in ("active", "expired", "suspended", "disabled", "banned"))
    out["other"] = max(0, out["total"] - grouped)
    return out


def get_online_count(tenant_id: Optional[int] = None) -> int:
    """«المتصلون الآن» مشتقًّا من الحالة الحيّة للراوتر (سياسة المالك): جلسات
    الراوترات القابلة للوصول فقط — فارغ عند الانقطاع. يَرتدّ تلقائيًّا إلى عدّ
    radacct المفتوح حين لا سجلّ liveness (المُستطلِع متوقّف/راوتر بلا API)."""
    t = tenant_id if tenant_id is not None else _tid()
    # real_only: جلسات أسماءٍ موجودة كمشترك أو كرت فقط (لا T-<MAC>/«مؤقت»/اسم
    # مجهول) — الرقم نفسه في لوحة الويب والـAPI وشارة /online.
    try:
        from . import connected_live
        return connected_live.connected_now(t, real_only=True)
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة
        try:
            from . import live_sessions
            return int(live_sessions.tenant_active_count(t, real_only=True))
        except Exception:  # noqa: BLE001
            return 0


# ────────────────────────────────────────────────────────────────
# 3. Cards section
# ────────────────────────────────────────────────────────────────
_ELECTRONIC_BATCH_FILTER = """
        LOWER(COALESCE(b.metadata, '')) NOT LIKE '%printed%'
        AND (
            LOWER(COALESCE(b.metadata, '')) LIKE '%electronic%'
            OR LOWER(COALESCE(b.batch_code, '')) LIKE '%online%'
            OR LOWER(COALESCE(b.package_name, '')) LIKE '%online%'
            OR LOWER(COALESCE(b.package_name, '')) LIKE '%electronic%'
            OR COALESCE(b.package_name, '') LIKE '%إلكترون%'
            OR COALESCE(b.package_name, '') LIKE '%الكترون%'
        )
    """


def card_batch_dashboard_summary(tenant_id: int) -> dict:
    """مخزون الكروت (مطبوعة/إلكترونيّة) — **المصدر الوحيد** للوحة الويب و
    ``/api/v1/dashboard``. الحزم المؤرشفة/المحذوفة (deleted_at) وكروتها لا
    تُحسب، ولا الكرت المحذوف منفردًا (كان الـAPI يعدّ كلّ صفوف cards
    و card_batches: «38,384 متاح · 185 حزمة» مقابل «357 · 20» في الويب)."""
    common = """
        WITH purchased_cards AS (
            SELECT tenant_id, card_id
            FROM card_user_purchases
            WHERE tenant_id=? AND status='completed' AND card_id IS NOT NULL
            GROUP BY tenant_id, card_id
        ),
        online_cards AS (
            SELECT tenant_id, username
            FROM radacct
            WHERE tenant_id=? AND acctstoptime IS NULL
            GROUP BY tenant_id, username
        )
        SELECT
            COUNT(DISTINCT b.id) AS batches,
            COUNT(CASE WHEN COALESCE(c.deleted_at, '') = '' THEN c.id END) AS total,
            COALESCE(SUM(CASE
                WHEN COALESCE(c.deleted_at, '') = '' AND c.used=1
                THEN 1 ELSE 0 END), 0) AS used,
            COALESCE(SUM(CASE
                WHEN COALESCE(c.deleted_at, '') = ''
                 AND c.revoked=0
                 AND c.used=0
                 AND pc.card_id IS NULL
                 AND (c.expire_at IS NULL OR c.expire_at >= strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                THEN 1 ELSE 0 END), 0) AS available,
            COUNT(DISTINCT CASE
                WHEN COALESCE(c.deleted_at, '') = ''
                 AND c.revoked=0
                 AND oc.username IS NOT NULL
                THEN c.id END) AS connected,
            COALESCE(SUM(CASE
                WHEN COALESCE(c.deleted_at, '') = ''
                 AND c.used=1
                 AND SUBSTR(COALESCE(c.first_used_at, ''), 1, 10) = date('now')
                THEN 1 ELSE 0 END), 0) AS used_today,
            COALESCE(SUM(CASE
                WHEN pc.card_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS sold_total
        FROM card_batches b
        LEFT JOIN cards c
          ON c.tenant_id=b.tenant_id AND c.batch_id=b.id
        LEFT JOIN purchased_cards pc
          ON pc.tenant_id=c.tenant_id AND pc.card_id=c.id
        LEFT JOIN online_cards oc
          ON oc.tenant_id=c.tenant_id AND oc.username=c.username
        WHERE b.tenant_id=?
          AND COALESCE(b.deleted_at, '') = ''
          AND {filter_clause}
    """
    sold_today_sql = """
        SELECT COUNT(*) AS c
        FROM card_user_purchases p
        JOIN cards c
          ON c.tenant_id=p.tenant_id AND c.id=p.card_id
        JOIN card_batches b
          ON b.tenant_id=c.tenant_id AND b.id=c.batch_id
        WHERE p.tenant_id=?
          AND p.status='completed'
          AND SUBSTR(COALESCE(p.created_at, ''), 1, 10) = date('now')
          AND COALESCE(b.deleted_at, '') = ''
          AND {filter_clause}
    """

    def one(kind: str) -> dict:
        filter_clause = (_ELECTRONIC_BATCH_FILTER if kind == "electronic"
                         else f"NOT ({_ELECTRONIC_BATCH_FILTER})")
        try:
            row = db().execute(common.format(filter_clause=filter_clause),
                               (tenant_id, tenant_id, tenant_id)).fetchone()
            sold_today = db().execute(
                sold_today_sql.format(filter_clause=filter_clause),
                (tenant_id,),
            ).fetchone()
        except Exception:
            return {"batches": 0, "total": 0, "used": 0, "available": 0,
                    "connected": 0, "sold_today": 0}
        used_today = int(row["used_today"] or 0) if row else 0
        marketplace_sold_today = int(sold_today["c"] or 0) if sold_today else 0
        return {
            "batches": int(row["batches"] or 0) if row else 0,
            "total": int(row["total"] or 0) if row else 0,
            "used": int(row["used"] or 0) if row else 0,
            "available": int(row["available"] or 0) if row else 0,
            "connected": int(row["connected"] or 0) if row else 0,
            "sold_today": marketplace_sold_today if kind == "electronic" else used_today,
        }

    return {"printed": one("printed"), "electronic": one("electronic")}


def get_card_counts(tenant_id: Optional[int] = None) -> dict:
    """إجماليّات الكروت للـAPI واللوحة = مجموع المطبوعة + الإلكترونيّة من
    :func:`card_batch_dashboard_summary` (مصدرٌ واحد؛ بلا المؤرشف/المحذوف).
    ``printed``/``electronic`` مضافان (نفس أرقام بطاقتَي لوحة الويب)."""
    t = tenant_id if tenant_id is not None else _tid()
    split = card_batch_dashboard_summary(t)
    p, e = split["printed"], split["electronic"]

    def _sum(key: str) -> int:
        return int(p.get(key) or 0) + int(e.get(key) or 0)

    return {
        "total":      _sum("total"),
        "used":       _sum("used"),
        "available":  _sum("available"),
        "batches":    _sum("batches"),
        "connected":  _sum("connected"),
        "printed":    p,
        "electronic": e,
    }


def get_recent_batches(*, limit: int = 5, tenant_id: Optional[int] = None) -> list[dict]:
    """آخر N حزمة — فقط ما يحتاجه القالب."""
    t = tenant_id if tenant_id is not None else _tid()
    try:
        rows = db().execute(
            "SELECT id, batch_code, package_name, count, generated, used, created_at "
            "FROM card_batches WHERE tenant_id=? AND COALESCE(deleted_at, '') = '' "
            "ORDER BY id DESC LIMIT ?",
            (t, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ────────────────────────────────────────────────────────────────
# 4. Plans section
# ────────────────────────────────────────────────────────────────
def get_plan_counts(tenant_id: Optional[int] = None) -> dict:
    t = tenant_id if tenant_id is not None else _tid()
    # المؤرشفة (deleted_at) خارج العدّ — كانت «39 عرض» بينما صفحة العروض والـAPI
    # 37 (F04 N-L6). نفس شرط /profiles و«العروض».
    total    = _scalar("SELECT COUNT(*) FROM access_plans WHERE tenant_id=? "
                       "AND deleted_at IS NULL", (t,))
    enabled  = _scalar("SELECT COUNT(*) FROM access_plans WHERE tenant_id=? AND enabled=1 "
                       "AND deleted_at IS NULL", (t,))
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
        # deleted_at IS NULL — a device delete is a SOFT delete (archive) that
        # only stamps deleted_at + forces enabled=0. Without this filter the
        # "total" tile counted archived routers/NAS forever (e.g. 7 shown while
        # only 1 live device exists). "enabled" was already correct by side
        # effect (delete sets enabled=0); align "total" with every other repo
        # query that excludes deleted rows.
        "total":   _scalar("SELECT COUNT(*) FROM nas_devices WHERE tenant_id=? AND deleted_at IS NULL", (t,)),
        "enabled": _scalar("SELECT COUNT(*) FROM nas_devices WHERE tenant_id=? AND enabled=1 AND deleted_at IS NULL", (t,)),
    }


# ────────────────────────────────────────────────────────────────
# 6. System health (cached 30s — psutil قد يكون بطيء)
# ────────────────────────────────────────────────────────────────
_SYS_CACHE: dict = {"at": 0.0, "data": None}
_SYS_CACHE_TTL = 30.0


def _format_uptime(seconds: float) -> str:
    # Latin unit letters (d/h/m) — Arabic «ي/س/د» scrambles next to Latin
    # digits in RTL (see core.duration_fmt). Shared single source of truth.
    from ..core.duration_fmt import fmt_uptime_short  # noqa: WPS433
    return fmt_uptime_short(seconds)


def get_system_health() -> dict:
    """صحة النظام مع caching 30s. fallback لكل metric — لا يفشل أبدًا."""
    now = time.time()
    if _SYS_CACHE["data"] is not None and (now - _SYS_CACHE["at"]) < _SYS_CACHE_TTL:
        return _SYS_CACHE["data"]

    try:
        from .system_probe import get_vps_status
        vps = get_vps_status()
    except Exception:
        vps = {}

    memory = vps.get("memory") if isinstance(vps.get("memory"), dict) else {}
    disk = vps.get("disk") if isinstance(vps.get("disk"), dict) else {}
    network = vps.get("network") if isinstance(vps.get("network"), dict) else {}

    out = {
        "db_ok":          False,
        "radius_ok":      False,
        "process_uptime": vps.get("process_uptime") or "",
        "system_uptime":  vps.get("system_uptime") or None,
        "cpu_pct":        vps.get("cpu_pct"),
        "ram_pct":        memory.get("percent"),
        "disk_pct":       disk.get("percent"),
        "hostname":       vps.get("hostname") or "",
        "platform":       vps.get("platform") or "",
        "cpu_count":      vps.get("cpu_count") or 0,
        "load":           vps.get("load") or {},
        "memory":         memory,
        "disk":           disk,
        "network":        network,
        "ping_ms":        network.get("ping_ms"),
        "ping_ok":        network.get("ping_ok"),
        "dns_ok":         network.get("dns_ok"),
        "vps":            vps,
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

    # نظام — كل تنبيه يحمل link_endpoint حتى يكون قابلاً للنقر في الواجهة
    # ملاحظة i18n: نصوص التنبيهات مغلّفة بـ gettext (نمط طبقة بايثون). الرسائل
    # ذات الأرقام تستخدم نمط %(name)s المسمّى ليُترجَم النص دون كسر الاستيفاء.
    if not system.get("db_ok"):
        out.append({"level": "danger", "link_endpoint": "radius.settings_page",
                     "message": _("تعذّر الاتصال بقاعدة البيانات.")})
    if not system.get("radius_ok"):
        out.append({"level": "warn", "link_endpoint": "radius.settings_page",
                     "message": _("RADIUS adapter غير جاهز — افحص الإعدادات.")})

    # موارد
    for k, label in (("cpu_pct", "CPU"), ("ram_pct", "RAM"), ("disk_pct", "Disk")):
        v = system.get(k)
        if v is not None and v >= 90:
            out.append({"level": "danger",
                         "message": _("استخدام %(label)s مرتفع جدًا (%(v)s%%).",
                                      label=label, v=v)})
        elif v is not None and v >= 75:
            out.append({"level": "warn",
                         "message": _("استخدام %(label)s مرتفع (%(v)s%%).",
                                      label=label, v=v)})

    # مشتركون — كل تنبيه يحمل link_args ليفتح قائمة المشتركين مفلترة
    # على نفس المجموعة بالضبط (?attention=expiring_3d أو ?attention=expired)
    # حتى يطابق عدد الصفوف العدّاد المعروض هنا.
    exp_soon = subs.get("expiring_soon") or 0
    if exp_soon > 0:
        out.append({"level": "warn", "link_endpoint": "radius.users_list",
                     "link_args": {"attention": "expiring_3d"},
                     "message": _("%(n)s مشترك ينتهي اشتراكه خلال 3 أيام.", n=exp_soon)})
    expired = subs.get("expired") or 0
    if expired > 0:
        out.append({"level": "info", "link_endpoint": "radius.users_list",
                     "link_args": {"attention": "expired"},
                     "message": _("%(n)s مشترك انتهى اشتراكه — جدّد أو احذف.", n=expired)})

    # كروت
    avail = cards.get("available") or 0
    if cards.get("total", 0) > 0 and avail == 0:
        # رابط مباشر لتوليد دفعة جديدة من الكروت
        out.append({"level": "danger", "link_endpoint": "radius.cards_generate",
                     "message": _("لا توجد كروت متاحة — وَلِّد دفعة جديدة.")})
    elif 0 < avail < 10:
        out.append({"level": "warn", "link_endpoint": "radius.cards_generate",
                     "message": _("الكروت المتاحة منخفضة (%(n)s) — جدّد المخزون.", n=avail)})

    # خطط
    if plans.get("total", 0) == 0:
        out.append({"level": "info", "link_endpoint": "radius.plans_new",
                     "message": _("لا توجد باقات بعد — أنشئ أول باقة.")})

    # NAS
    if nas.get("total", 0) == 0:
        out.append({"level": "info", "link_endpoint": "radius.devices_list",
                     "message": _("لا توجد أجهزة NAS مُسجَّلة — أضِف router/AP.")})

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
