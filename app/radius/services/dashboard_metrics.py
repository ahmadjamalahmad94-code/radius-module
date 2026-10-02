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

from ..core.ar_text import ar_count
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
    # fix3 (F02 H2 / F01 F8): the request admin's subscriber scope.
    from .subscriber_scope import current_scope_admin_id
    _scope = current_scope_admin_id(tenant_id=t)

    def _n(**kw) -> int:
        try:
            return int(subscribers_repo.count_subscribers(
                t, user_type="subscriber", owner_admin_id=_scope, **kw))
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
    # fix3: a scoped manager counts only HIS subscribers'/cards' open sessions.
    from .subscriber_scope import current_scope_admin_id, scope_sql
    _scope = current_scope_admin_id(tenant_id=t)
    if _scope is not None:
        try:
            sc, sv = scope_sql("username", scope=int(_scope), tenant_id=t, use_request=False)
            return int(_scalar("SELECT COUNT(DISTINCT username) FROM radacct "
                               "WHERE tenant_id=? AND acctstoptime IS NULL" + sc, (t, *sv)))
        except Exception:  # noqa: BLE001
            return 0
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
                 AND COALESCE(c.first_used_at, '') != ''
                 AND datetime(c.first_used_at) >= datetime(?)
                 AND datetime(c.first_used_at) < datetime(?)
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
          AND datetime(p.created_at) >= datetime(?)
          AND datetime(p.created_at) < datetime(?)
          AND COALESCE(b.deleted_at, '') = ''
          AND {filter_clause}
    """

    # fix3 (F01 F10 / F07 M2): «رؤية كل حِزم البطاقات» — the request admin's
    # card-batch scope on the stock figures too (one predicate).
    from .card_batch_scope import batch_scope_sql
    bsc, bsv = batch_scope_sql(alias="b", tenant_id=int(tenant_id))
    # «مباع اليوم» = يوم اللوحة المحلّيّ (Asia/Gaza) لا يوم UTC (كان
    # date('now') يعدّ بطاقة 01:30 بتوقيت غزّة لأمس) — fix3 integration.
    try:
        from ..core.system_config import local_period_utc_range, local_today
        day_lo, day_hi = local_period_utc_range(
            "daily", local_today(int(tenant_id)).isoformat(), int(tenant_id))
    except Exception:  # noqa: BLE001
        day_lo, day_hi = "9999-01-01 00:00:00", "9999-01-01 00:00:00"

    def one(kind: str) -> dict:
        filter_clause = (_ELECTRONIC_BATCH_FILTER if kind == "electronic"
                         else f"NOT ({_ELECTRONIC_BATCH_FILTER})")
        filter_clause = "(" + filter_clause + ")" + bsc
        try:
            row = db().execute(common.format(filter_clause=filter_clause),
                               (tenant_id, tenant_id, day_lo, day_hi, tenant_id,
                                *bsv)).fetchone()
            sold_today = db().execute(
                sold_today_sql.format(filter_clause=filter_clause),
                (tenant_id, day_lo, day_hi, *bsv),
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
    # fix3 (F07 M2): «آخر الحزم» — only the batches this admin may see.
    from .card_batch_scope import batch_scope_sql
    bsc, bsv = batch_scope_sql(alias="card_batches", tenant_id=int(t))
    try:
        rows = db().execute(
            "SELECT id, batch_code, package_name, count, generated, used, created_at "
            "FROM card_batches WHERE tenant_id=? AND COALESCE(deleted_at, '') = ''" + bsc
            + " ORDER BY id DESC LIMIT ?",
            (t, *bsv, limit)).fetchall()
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
                     # صيغةُ المعدود بحسب العدد (ar_count): كان «2 مشترك ينتهي اشتراكه» و
                     # «25 مشترك انتهى اشتراكه» — المفردُ مع كلِّ عدد (r6ui، التطبيقُ يعرضها كما هي).
                     "message": _("ينتهي خلال 3 أيام اشتراكُ %(cnt)s.", cnt=ar_count(exp_soon, "subscriber"))})
    expired = subs.get("expired") or 0
    if expired > 0:
        out.append({"level": "info", "link_endpoint": "radius.users_list",
                     "link_args": {"attention": "expired"},
                     "message": _("انتهى اشتراكُ %(cnt)s — جدّد أو احذف.", cnt=ar_count(expired, "subscriber"))})

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
def dashboard_access() -> dict:
    """fix3 (F01 F8): which dashboard sections the request admin may see —
    the same keys as their pages (owner / co-owner / unbound credential: all).
    ``system`` (server hostname / OS / resources) stays owner-only."""
    full = {"subscribers": True, "cards": True, "network": True, "plans": True,
            "finance": True, "system": True}
    try:
        from .subscriber_scope import request_admin_id, request_is_owner_session
        if request_is_owner_session():
            return full
        aid = request_admin_id()
        if not aid:
            return full
        from ..auth.owner import is_owner_like
        if is_owner_like(int(aid)):
            return full
        from ..db.repos import admins_repo
        admin = admins_repo.get_admin(int(aid))
        perms = set(admins_repo.admin_permissions(admin)) if admin is not None else set()
    except Exception:  # noqa: BLE001 — fail-closed
        perms = set()
    return {
        "subscribers": "users.view" in perms,
        "cards": "cards.view" in perms,
        "network": "nas.view" in perms,
        "plans": "plans.view" in perms,
        "finance": "reports.finance" in perms,
        "system": False,
    }


def get_sales_today(tenant_id: Optional[int] = None, *,
                    access: Optional[dict] = None,
                    date_from: str = "", date_to: str = "") -> dict:
    """«إجمالي مبيعات اليوم» (طلب المالك 2026-09-30) — مثال «100 بطاقة · 200 شيكل».

    «اليوم» = يوم اللوحة المحلّيّ (Asia/Gaza بتوقيتها الصيفيّ، ``local_today``)،
    والكلّ مقصورٌ على المدير (مشتركوه وحِزمه — نفس محمول التقارير):

    * ``cards_count`` — البطاقات المباعة اليوم = دخلت أوّل مرّة اليوم؛ المصدر
      نفسه لـ«مبيعات اليوم» في تقرير الكروت (``cards_sold_by_batch``).
    * ``payments`` — دفعات المشتركين اليوم = **سطر اليوم في «تقرير المبيعات
      اليوميّة»** (``accounting_repo.sales_summary(grain='daily')``) حرفيًّا:
      ``transactions`` و``by_currency`` نفسهما.
    * ``cards_value`` — قيمة بطاقات اليوم بسعر بطاقة حزمتها وعملة باقتها
      (البطاقات المطبوعة لا تُقيَّد دفعةً في الدفتر، فبدونها تكون البطاقة «0 ₪»).
    * ``by_currency`` = قيمة بطاقات اليوم لكلّ عملة — رقم البطاقة (قرار المالك:
      مبيعات البطاقات وسعرها فقط، لا دفعات المشتركين). ``payments`` مرجعٌ منفصل.

    بلا «التقارير المالية» (``reports.finance``) يُرسَل ``cards_count`` فقط
    و``money_visible: false`` (لا مبالغ). بلا مفتاح البطاقات ولا المالية ⇒ 0."""
    t = tenant_id if tenant_id is not None else _tid()
    access = access if access is not None else dashboard_access()
    from ..core.system_config import local_today
    today = local_today(t).isoformat()
    # فترة يختارها المالك (يوم/أسبوع/شهر/من–إلى، 2026-10-02) — الافتراض اليوم.
    d_from = (date_from or "").strip() or today
    d_to = (date_to or "").strip() or d_from
    money = bool(access.get("finance"))
    out: dict = {"date": d_from, "date_to": d_to, "cards_count": 0,
                 "money_visible": money}
    if not (access.get("cards") or money):
        return out
    try:
        from .dashboard_reports import DashboardReportsService
        sold = DashboardReportsService(tenant_id=t).cards_sold_by_batch(
            d_from, d_to if d_to != d_from else "")
    except Exception:  # noqa: BLE001 — لا تكسر اللوحة
        sold = []
    out["cards_count"] = sum(int(r["count"]) for r in sold)
    if not money:
        return out
    # ── قيمة البطاقات: سعر بطاقة الحزمة × عددها، بعملة باقة الحزمة ──
    from .card_batch_price import batch_currency
    cards_by: dict[str, dict] = {}
    ids = [int(r["batch_id"]) for r in sold if r.get("batch_id")]
    batches: dict[int, dict] = {}
    if ids:
        try:
            marks = ",".join("?" for _ in ids)
            for b in db().execute(
                    f"SELECT id, plan_id, price_per_card FROM card_batches "
                    f"WHERE tenant_id=? AND id IN ({marks})", (t, *ids)).fetchall():
                batches[int(b["id"])] = dict(b)
        except Exception:  # noqa: BLE001
            batches = {}
    for r in sold:
        b = batches.get(int(r.get("batch_id") or 0)) or {}
        cur = (batch_currency(t, b.get("plan_id")) or "").strip().upper()
        slot = cards_by.setdefault(cur, {"currency": cur, "count": 0, "total": 0.0})
        slot["count"] += int(r["count"])
        try:
            price = float(b.get("price_per_card") or 0)
        except (TypeError, ValueError):
            price = 0.0
        slot["total"] += max(price, 0.0) * int(r["count"])
    # ── دفعات اليوم: سطر اليوم من «تقرير المبيعات اليوميّة» نفسه ──
    try:
        from ..db.repos import accounting_repo
        rows = accounting_repo.sales_summary(t, grain="daily")
    except Exception:  # noqa: BLE001
        rows = []
    row = next((r for r in rows if str(r.get("period")) == today), None) or {}
    pay_by = [{"currency": str(c["currency"]).upper(), "total": round(float(c["total"] or 0), 2),
               "transactions": int(c.get("transactions") or 0)}
              for c in (row.get("by_currency") or [])]
    from ..core.numbers import round_money
    # قرار المالك 2026-09-30: بطاقة «مبيعات اليوم» = مبيعات البطاقات وسعرها فقط
    # (لا دفعات المشتركين النقديّة). ``payments`` يبقى مرجعًا منفصلًا للاطلاع،
    # لكنّ ``by_currency`` — رقم البطاقة — هو قيمة بطاقات اليوم وحدها.
    for c in cards_by.values():
        c["total"] = round_money(c["total"])
    from ..core.system_config import default_currency
    system = (default_currency() or "").strip().upper()
    by_currency = [{"currency": c["currency"], "total": c["total"]}
                   for c in cards_by.values()]
    by_currency.sort(key=lambda c: (c["currency"] != system, -abs(c["total"])))
    out.update({
        "payments": {"transactions": int(row.get("transactions") or 0),
                     "total": round(float(row.get("total") or 0), 2),
                     "by_currency": pay_by},
        "cards_value": {"by_currency": sorted(cards_by.values(),
                                              key=lambda c: (c["currency"] != system,
                                                             -abs(c["total"])))},
        "by_currency": by_currency,
        "currency": system,
    })
    return out


def build_dashboard_metrics(tenant_id: Optional[int] = None) -> dict:
    """يجمع كل المؤشرات في dict واحد للـ template. لا يرفع أبدًا.

    fix3 (F02 H2 / F01 F8 / F07 M2): scoped to the request admin (his
    subscribers / batches) and gated per section key (``access``)."""
    t = tenant_id if tenant_id is not None else _tid()
    access = dashboard_access()
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

    def _alert_section(a: dict) -> str:
        ep = str(a.get("link_endpoint") or "")
        if ep.endswith(("users_list", "users_new")):
            return "subscribers"
        if "cards" in ep:
            return "cards"
        if "plans" in ep:
            return "plans"
        if "devices" in ep or "nas" in ep:
            return "network"
        return "system"      # settings / server resources / no link
    alerts = [a for a in alerts if access.get(_alert_section(a))]
    if not access["system"]:
        system = {}
    if not access["subscribers"]:
        subs = {k: 0 for k in ("total", "active", "expired", "suspended", "disabled",
                               "banned", "expiring_soon", "other", "online")}
    if not access["cards"]:
        cards = {k: 0 for k in ("total", "used", "available", "batches", "connected")}
        cards.update({"printed": {}, "electronic": {}})
        recent_batches = []
    if not access["plans"]:
        plans = {"total": 0, "enabled": 0, "disabled": 0}
    if not access["network"]:
        nas = {}
    try: sales_today = get_sales_today(t, access=access)
    except Exception: sales_today = {"cards_count": 0, "money_visible": False}
    return {
        "sales_today":    sales_today,
        "subscribers":    subs,
        "cards":          cards,
        "recent_batches": recent_batches,
        "plans":          plans,
        "nas":            nas,
        "system":         system,
        "alerts":         alerts,
        "access":         access,
    }
