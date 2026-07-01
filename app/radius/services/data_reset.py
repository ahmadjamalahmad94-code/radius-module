"""محرّك «تصفير / تنظيف البيانات» — للمالك فقط.

يُتيح للمالك (المبدأ غير المقيَّد) تنظيف نسخة تجريبيّة/اختبار من البيانات
المُضافة (مشتركون، كروت، مدراء، موزّعون، باقات، أجهزة، راوترات، حركات ماليّة،
جلسات، إشعارات) **مع الحفاظ** على النظام نفسه وحساب المالك حتى تبقى اللوحة
عاملة تمامًا بعد التصفير (قوائم فارغة = حالة صالحة).

خصائص الأمان:
  • **نسخة احتياطية إلزاميّة أوّلًا** (gzip)‏ — يُستدعى المحرّك بعد التأكّد
    من نجاح النسخة؛ إن فشلت النسخة يُلغى التصفير بالكامل.
  • **ذرّي (transaction واحد)** — أيّ خطأ يُرجع كل شيء (ROLLBACK)، فلا حالة
    نصف-محذوفة.
  • **مفاتيح خارجيّة مُفعّلة** (PRAGMA foreign_keys=ON) — الحذف يمرّ بترتيب
    الأبناء→الآباء، وأيّ قيد RESTRICT من فئة غير مُختارة يُجهض العمليّة
    برسالة واضحة بدل ترك مرجع مكسور.
  • **دفاعيّ ضدّ انحراف المخطّط** — كل جدول/عمود يُفحَص وجوده قبل الحذف،
    فالجداول الغائبة تُتخطّى بهدوء.

ملاحظة على FreeRADIUS: المشتركون والكروت يُكتَبون في radcheck/radreply/
radusergroup/radacct بمفتاح (tenant_id, username). لذا عند حذف فئة المشتركين
(أو الكروت) نحذف صفوف rad* الخاصّة بأسماء مستخدميها **فقط** عبر استعلام فرعيّ
— فلا نمسّ حسابات نفق إدارة الراوتر (rtr-*) التي ليست في subscribers/cards.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..db.connection import db, get_conn
from ..db.repos import admins_repo

_LOG = logging.getLogger(__name__)

# كلمة التأكيد التي يجب على المالك كتابتها حرفيًّا قبل التنفيذ.
CONFIRM_WORD = "تصفير"


# ─────────────────────────── أدوات فحص/حذف دفاعيّة ───────────────────────────

def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def _count(conn, table: str, *, tenant_id: int, where: str = "",
           params: tuple = (), tenant_scope: bool = True) -> int:
    """عدد الصفوف المطابقة — 0 إن كان الجدول غير موجود."""
    if not _table_exists(conn, table):
        return 0
    clauses: list[str] = []
    vals: list = []
    if tenant_scope and "tenant_id" in _columns(conn, table):
        clauses.append("tenant_id = ?")
        vals.append(tenant_id)
    if where:
        clauses.append(where)
        vals.extend(params)
    sql = f"SELECT COUNT(*) AS c FROM {table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    try:
        return int(conn.execute(sql, vals).fetchone()["c"])
    except Exception:  # noqa: BLE001
        return 0


def _delete(conn, table: str, *, tenant_id: int, where: str = "",
            params: tuple = (), tenant_scope: bool = True) -> int:
    """حذف صفوف — يُرجع عددها. يتخطّى الجدول الغائب (0). يَحصر بالمستأجر إذا
    وُجد عمود tenant_id (وطُلب ذلك)."""
    if not _table_exists(conn, table):
        return 0
    clauses: list[str] = []
    vals: list = []
    if tenant_scope and "tenant_id" in _columns(conn, table):
        clauses.append("tenant_id = ?")
        vals.append(tenant_id)
    if where:
        clauses.append(where)
        vals.extend(params)
    sql = f"DELETE FROM {table}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    cur = conn.execute(sql, vals)
    n = cur.rowcount
    return int(n) if n and n > 0 else 0


# ───────────────────────────── سياق التنفيذ ─────────────────────────────

@dataclass
class _Ctx:
    tenant_id: int
    preserve_admin_ids: tuple[int, ...]   # حسابات المالكين + الجلسة الحاليّة (لا تُحذف)


# ─────────────────────────── تعريف الفئات ───────────────────────────
# كل فئة: key ثابت، label عربيّ، group للتجميع في الواجهة، optional (اختياريّة
# افتراضيًّا غير مُحدَّدة)، primary_table (عمود «X» في التقرير)، ودالة تنفيذ.

@dataclass(frozen=True)
class Category:
    key: str
    label: str
    group: str
    optional: bool
    primary_table: str
    run: Callable[["DataResetService", object, _Ctx], dict]
    hint: str = ""


class DataResetService:
    """محرّك التصفير — بلا حالة؛ كل الاستدعاءات تأخذ tenant_id صراحةً."""

    # ═══════════════════════ فئات الحذف (بالترتيب العالميّ الآمن) ═══════════════════════
    # الترتيب مهمّ عند اختيار عدّة فئات معًا: الأبناء قبل الآباء عبر الفئات كي
    # تُحترم قيود RESTRICT (مثال: الكروت تُشير لِلباقات بـRESTRICT، فتُحذف قبلها).

    def categories(self) -> list[Category]:
        return [
            Category("notifications", "الإشعارات والسجلّات", "logs", True,
                     "audit_log", DataResetService._wipe_notifications,
                     hint="سجل التدقيق، الإشعارات، الرسائل، أحداث المخاطر."),
            Category("sessions", "الجلسات والاستهلاك", "logs", True,
                     "radacct", DataResetService._wipe_sessions,
                     hint="سجلّات radacct/الجلسات والاستهلاك اليوميّ."),
            Category("financial", "الحركات المالية والمحافظ", "money", True,
                     "wallet_transactions", DataResetService._wipe_financial,
                     hint="المحافظ، القيود، الدفعات، السلف، الفواتير، التسويات."),
            Category("cards", "الكروت والحِزم", "core", False,
                     "cards", DataResetService._wipe_cards,
                     hint="الكروت + حِزمها + متجر الكروت + العروض + القسائم."),
            Category("subscribers", "المشتركون", "core", False,
                     "subscribers", DataResetService._wipe_subscribers,
                     hint="المشتركون + حقولهم + مجموعاتهم + بصمات أجهزتهم + radius."),
            Category("distributors", "الموزّعون", "core", False,
                     "distributors", DataResetService._wipe_distributors,
                     hint="الموزّعون + دفاترهم + تخصيص الحِزم + سياساتهم."),
            Category("plans", "العروض/الباقات والبروفايلات", "core", False,
                     "access_plans", DataResetService._wipe_plans,
                     hint="الباقات + بروفايلات السرعة + الجداول + عروض الكروت."),
            Category("nas", "NAS / عملاء الراديوس", "network", False,
                     "nas", DataResetService._wipe_nas,
                     hint="جدول nas (عملاء FreeRADIUS)."),
            Category("routers", "السيرفرات / الراوترات والأسطول", "network", False,
                     "nas_devices", DataResetService._wipe_routers,
                     hint="الراوترات + لقطاتها + مقاييسها + الأنفاق + أجهزة الشبكة."),
            Category("managers", "المدراء (عدا المالك)", "core", False,
                     "admins", DataResetService._wipe_managers,
                     hint="حسابات المدراء (لا يُحذف المالك ولا حسابك الحاليّ)."),
        ]

    def category_map(self) -> dict[str, Category]:
        return {c.key: c for c in self.categories()}

    # ───────────────────────── حساب المالكين المحفوظين ─────────────────────────

    def preserve_admin_ids(self, current_admin_id: Optional[int]) -> tuple[int, ...]:
        """مجموعة معرّفات الحسابات التي **يُحظر** حذفها: كل المالكين المعيَّنين
        + المالك الرئيسيّ (احتياط) + الحساب المُسجَّل حاليًّا."""
        ids: set[int] = set()
        if current_admin_id:
            ids.add(int(current_admin_id))
        try:
            pid = admins_repo.primary_admin_id()
            if pid:
                ids.add(int(pid))
        except Exception:  # noqa: BLE001
            pass
        try:
            keys = admins_repo.designated_owner_keys()
            if keys:
                for a in admins_repo.list_admins(include_deleted=True):
                    u = (getattr(a, "username", "") or "").strip().lower()
                    e = (getattr(a, "email", "") or "").strip().lower()
                    if (u and u in keys) or (e and e in keys):
                        ids.add(int(a.id))
        except Exception:  # noqa: BLE001
            pass
        return tuple(sorted(ids))

    # ───────────────────────────── الملخّص (قبل الحذف) ─────────────────────────────

    def summarize(self, *, tenant_id: int, keys: list[str],
                  current_admin_id: Optional[int]) -> dict:
        """يُرجع، لكل فئة مُختارة صالحة، عدد الصفوف في جدولها الرئيسيّ (X) — كي
        يرى المالك بالضبط ما سيُحذف قبل التأكيد. قراءة فقط (لا يكتب شيئًا)."""
        conn = get_conn()
        cmap = self.category_map()
        preserve = self.preserve_admin_ids(current_admin_id)
        out: list[dict] = []
        for key in keys:
            cat = cmap.get(key)
            if not cat:
                continue
            if cat.key == "managers":
                count = self._count_managers(conn, tenant_id, preserve)
            else:
                count = _count(conn, cat.primary_table, tenant_id=tenant_id)
            out.append({
                "key": cat.key, "label": cat.label, "group": cat.group,
                "primary_table": cat.primary_table, "count": count,
                "hint": cat.hint,
            })
        return {"ok": True, "categories": out,
                "total": sum(c["count"] for c in out)}

    def _count_managers(self, conn, tenant_id: int, preserve: tuple[int, ...]) -> int:
        if not _table_exists(conn, "admins"):
            return 0
        ph = ",".join("?" * len(preserve)) if preserve else "NULL"
        row = conn.execute(
            f"SELECT COUNT(*) AS c FROM admins WHERE id NOT IN ({ph})",
            tuple(preserve),
        ).fetchone()
        return int(row["c"]) if row else 0

    # ───────────────────────────── التنفيذ (الحذف) ─────────────────────────────

    def wipe(self, *, tenant_id: int, keys: list[str],
             current_admin_id: Optional[int]) -> dict:
        """ينفّذ الحذف الذرّيّ للفئات المُختارة (بالترتيب العالميّ الآمن).

        **ملاحظة مهمّة**: لا يُنشئ النسخة الاحتياطيّة — المُستدعي (الراوت)
        مسؤول عن إنشاء النسخة والتحقّق منها **قبل** استدعاء هذه الدالة، وإلغاء
        العمليّة إن فشلت. هنا نُركّز على الحذف الذرّيّ فقط.

        يُرجع تقريرًا: لكل فئة {label, primary(X المحذوف), rows(إجمالي الصفوف),
        tables:{جدول:عدد}}. عند أيّ خطأ → ROLLBACK ورفع الاستثناء."""
        conn = get_conn()
        cmap = self.category_map()
        preserve = self.preserve_admin_ids(current_admin_id)
        ctx = _Ctx(tenant_id=tenant_id, preserve_admin_ids=preserve)

        # نفّذ الفئات بالترتيب العالميّ المُعرَّف (لا بترتيب اختيار المستخدم).
        ordered = [c for c in self.categories() if c.key in set(keys)]

        report: list[dict] = []
        # foreign_keys تبقى ON (الافتراض في الاتصال). نُدير المعاملة يدويًّا كي
        # نضمن BEGIN/COMMIT/ROLLBACK واحدًا يلفّ كل الفئات.
        conn.execute("BEGIN")
        try:
            for cat in ordered:
                res = cat.run(self, conn, ctx)
                tables = res.get("tables", {})
                report.append({
                    "key": cat.key,
                    "label": cat.label,
                    "primary": res.get("primary", 0),
                    "primary_table": cat.primary_table,
                    "rows": sum(tables.values()),
                    "tables": tables,
                })
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        # استرجاع المساحة بعد حذف كبير (خارج المعاملة). لا يكسر النتيجة إن فشل.
        try:
            conn.execute("VACUUM")
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "report": report,
                "total_rows": sum(r["rows"] for r in report),
                "preserved_admin_ids": list(preserve)}

    # ═══════════════════════════ منفّذات الفئات ═══════════════════════════
    # كل منفّذ يُرجع {"primary": X, "tables": {table: n, ...}} حيث primary هو
    # عدّاد الجدول الرئيسيّ (رأس التقرير) وtables تفصيل كل جدول حُذف.

    def _wipe_subscribers(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "subscribers", tenant_id=t)
        # (1) نظّف صفوف FreeRADIUS الخاصّة بأسماء المشتركين فقط (استعلام فرعيّ)
        #     — قبل حذف صفوف subscribers كي يبقى الاستعلام الفرعيّ صالحًا.
        sub_q = "username IN (SELECT username FROM subscribers WHERE tenant_id = ?)"
        for rt in ("radacct", "radpostauth", "radcheck", "radreply", "radusergroup"):
            n = _delete(conn, rt, tenant_id=t, where=sub_q, params=(t,))
            if n:
                tables[rt] = tables.get(rt, 0) + n
        # (2) الجداول الابنة ثمّ الجدول الرئيسيّ.
        for tbl in ("subscriber_fields", "subscriber_recharges", "share_group_members",
                    "device_fingerprints", "mac_clone_bindings", "mac_clone_events",
                    "login_attempt_passwords", "login_failure_tracker",
                    "bandwidth_usage_daily", "share_groups", "subscriber_groups",
                    "subscribers"):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}

    def _wipe_cards(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "cards", tenant_id=t)
        # نظّف radius لأسماء الكروت فقط (لا تمسّ المشتركين ولا rtr-*).
        card_q = "username IN (SELECT username FROM cards WHERE tenant_id = ?)"
        for rt in ("radacct", "radpostauth", "radcheck", "radreply", "radusergroup"):
            n = _delete(conn, rt, tenant_id=t, where=card_q, params=(t,))
            if n:
                tables[rt] = tables.get(rt, 0) + n
        # الأبناء أوّلًا: العروض/المتجر/التخصيصات ثمّ الكروت ثمّ الحِزم.
        for tbl in ("card_offer_visibility", "card_offers",
                    "hotspot_card_sms_attempts", "hotspot_card_purchases",
                    "card_user_purchases", "card_users", "card_marketplace_packages",
                    "card_batch_assignments", "card_batch_financial_costs",
                    "cards", "vouchers", "card_batches"):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}

    def _wipe_distributors(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "distributors", tenant_id=t)
        n = _delete(conn, "distributor_ledger_entries", tenant_id=t)
        if n:
            tables["distributor_ledger_entries"] = n
        n = _delete(conn, "card_batch_assignments", tenant_id=t,
                    where="distributor_id IS NOT NULL")
        if n:
            tables["card_batch_assignments"] = n
        for tbl in ("manager_distributor_policies", "manager_distributor_operations"):
            n = _delete(conn, tbl, tenant_id=t, where="entity_type = ?",
                        params=("distributor",))
            if n:
                tables[tbl] = n
        n = _delete(conn, "distributors", tenant_id=t)
        if n:
            tables["distributors"] = n
        return {"primary": primary, "tables": tables}

    def _wipe_plans(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "access_plans", tenant_id=t)
        # جداول تابعة للباقات/السرعات.
        for tbl in ("bandwidth_schedule_logs", "bandwidth_schedules",
                    "speed_control_policies", "card_offer_visibility", "card_offers",
                    "admin_plan_prices"):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        # تعريفات مجموعات الراديوس المشتقّة من الباقات (اسم المجموعة = plan_<id>).
        grp_q = ("groupname IN (SELECT 'plan_' || id FROM access_plans "
                 "WHERE tenant_id = ?)")
        for rt in ("radgroupcheck", "radgroupreply"):
            n = _delete(conn, rt, tenant_id=t, where=grp_q, params=(t,))
            if n:
                tables[rt] = n
        for tbl in ("bandwidth_profiles", "access_plans"):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}

    def _wipe_nas(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "nas", tenant_id=t)
        n = _delete(conn, "nas", tenant_id=t)
        if n:
            tables["nas"] = n
        return {"primary": primary, "tables": tables}

    def _wipe_routers(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "nas_devices", tenant_id=t)
        # أبناء الراوترات: لقطات/مقاييس/جلسات/أنفاق/أجهزة شبكة (بأفضل جهد،
        # الجداول الغائبة تُتخطّى). ثمّ nas_devices، ثمّ ip_pools، ثمّ nas.
        for tbl in (
            "router_remote_sessions", "router_resource_samples", "router_resource_state",
            "router_metric_samples", "router_metric_state", "router_backups",
            "router_snapshots", "router_loop_checks", "router_loop_probes",
            "router_alert_settings", "router_lifecycle_events",
            "router_provisioning_registry", "router_ip_allocations",
            "network_device_monitor_events", "network_device_monitor_alerts",
            "network_device_monitor_bindings", "network_device_monitor_network_scopes",
            "network_device_monitor_devices", "network_device_alerts",
            "network_device_checks", "network_device_health_checks",
            "remote_access_sessions", "network_devices",
            "site_exit_deployments", "site_exit_targets", "site_exit_script_versions",
            "site_exit_policies",
            "npc_change_set_targets", "npc_change_sets", "npc_deployments",
            "npc_script_versions", "npc_walled_garden_entries", "npc_walled_garden_policies",
            "npc_web_block_targets", "npc_web_block_policies",
            "npc_remote_port_mappings", "npc_remote_access_policies",
            "npc_snapshots",
            "prepared_wireguard_peer_operations", "prepared_wireguard_peers",
            "wireguard_peer", "data_connection_wg_peers", "wireguard_data_service",
            "bridge_tunnels", "vps_exit_nodes", "vpn_account",
            "mikrotik_import_logs", "mikrotik_configs",
            "hotspot_designs",
            "nas_devices", "ip_pools",
            # حذف الراوترات يُزيل أيضًا عملاء nas المُشتقّين (كي لا يبقى عميل
            # راديوس مُعلَّق لِراوتر لم يَعُد موجودًا).
            "nas",
        ):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}

    def _wipe_managers(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        preserve = ctx.preserve_admin_ids
        tables: dict[str, int] = {}
        primary = self._count_managers(conn, t, preserve)
        ph = ",".join("?" * len(preserve)) if preserve else "NULL"
        not_owner = f"id NOT IN ({ph})"
        entity_not_owner = f"entity_id NOT IN ({ph})"
        # جداول تابعة للمدير (تُقصر على غير المالكين).
        n = _delete(conn, "admin_plan_prices", tenant_id=t,
                    where=f"admin_id NOT IN ({ph})", params=tuple(preserve))
        if n:
            tables["admin_plan_prices"] = n
        n = _delete(conn, "manager_credit_ledger", tenant_id=t,
                    where=f"manager_id NOT IN ({ph})", params=tuple(preserve))
        if n:
            tables["manager_credit_ledger"] = n
        for tbl in ("manager_distributor_policies", "manager_distributor_operations"):
            n = _delete(conn, tbl, tenant_id=t,
                        where=f"entity_type = ? AND {entity_not_owner}",
                        params=("manager", *preserve))
            if n:
                tables[tbl] = n
        n = _delete(conn, "tenant_memberships", tenant_id=t,
                    where=f"admin_id NOT IN ({ph})", params=tuple(preserve),
                    tenant_scope=False)
        if n:
            tables["tenant_memberships"] = n
        # أخيرًا: حسابات المدراء أنفسهم (عدا المالكين والجلسة الحاليّة).
        n = _delete(conn, "admins", tenant_id=t, where=not_owner,
                    params=tuple(preserve), tenant_scope=False)
        if n:
            tables["admins"] = n
        return {"primary": primary, "tables": tables}

    def _wipe_financial(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "wallet_transactions", tenant_id=t)
        # الأبناء أوّلًا (قيود/تفاصيل) ثمّ الآباء (طلبات/محافظ).
        for tbl in (
            "wallet_transactions", "ledger_entries", "accounting_ledger_entries",
            "settlement_entries", "loan_entries", "payment_transactions",
            "payment_proofs", "payment_collection_transactions",
            "payment_webhook_events", "payment_requests", "payment_checkouts",
            "deposit_requests", "withdrawal_requests",
            "revenue_records", "profit_shares", "price_snapshots",
            "financial_report_snapshots", "manager_credit_ledger",
            "distributor_ledger_entries", "company_expenses",
            "company_inventory_movements", "invoices", "wallets",
        ):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}

    def _wipe_sessions(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "radacct", tenant_id=t)
        for tbl in ("radacct", "radpostauth", "bandwidth_usage_daily",
                    "bandwidth_schedule_logs", "sync_queue"):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}

    def _wipe_notifications(self, conn, ctx: _Ctx) -> dict:
        t = ctx.tenant_id
        tables: dict[str, int] = {}
        primary = _count(conn, "audit_log", tenant_id=t)
        for tbl in (
            "webhook_deliveries", "webhook_subscriptions",
            "panel_notifications", "notifications", "provider_messages",
            "message_deliveries", "message_notifications", "message_campaigns",
            "audience_segments", "inbox_messages",
            "audit_log", "service_audit_log", "lifecycle_events",
            "business_events", "network_device_monitor_events",
            "hotspot_analytics_events", "fraud_flags", "investigations",
            "login_attempt_passwords", "login_failure_tracker",
        ):
            n = _delete(conn, tbl, tenant_id=t)
            if n:
                tables[tbl] = n
        return {"primary": primary, "tables": tables}


_SERVICE: Optional[DataResetService] = None


def get_data_reset_service() -> DataResetService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DataResetService()
    return _SERVICE
