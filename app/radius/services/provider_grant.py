"""«بوابة منح المزوّد» (Provider service-grant gate) — مكتبة قراءة + قرار.

نموذج الحقيقة:
  مزوّد الخدمة (لوحة التراخيص) يصدر لكلّ عميل (tenant) لقطة عقد قدرات
  (capacity contract) تُحفَظ في جدول ``license_admin_bridge_snapshots``
  (snapshot_type=capacity_contract). شكل الـpayload:

      {
        "status":  "active" | "suspended" | "disabled" | …,
        "features": { "<feature_key>": "enabled|locked|hidden|readonly", … },
        "services": {
            "<service_key>": {
                "enabled":       bool,
                "status":        "active|suspended|disabled|…",
                "hidden_portal": bool   # ← «مخفية من البوابة» (اختياري)
            },
            …
        },
        "limits":  { "<dotted.path>": int, … }   # «مجانية محدودة»
      }

ثلاث حالات معتمدة من لوحة المزوّد:

  • «موقوفة (إيقاف فعلي)»  → عاطلة في كل الريديوس (سوبر-أدمن لا يستطيع تجاوزها).
  • «مخفية من البوابة»     → تُخفى عن بوّابة المشترك/الزبون فقط (الإدارة تراها).
  • «مجانية محدودة»         → سقف كميّ يُفرَض خادميًّا (إنشاء يتجاوز السقف يُرفَض).

هذه الوحدة tenant-scoped، نقية (لا Flask)، آمنة في hot path:
  - أي خطأ قراءة → نُعامل الخدمة كمسموحة (fail-open على فقد الاتصال بالمزوّد)
    لئلّا نَكسر اللوحة كلّها إذا تعطّل التزامن. القرار «الإيقاف» يتطلّب لقطة
    صريحة من المزوّد تقول كذلك.
  - منع التخطّي من السوبر-أدمن: هذه البوابة فوق RBAC الداخلي تمامًا. السوبر
    يتجاوز RBAC + الأقسام، لكن لا يتجاوز منح المزوّد. الحارس نفسه يرفض
    التخطّي.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

_LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# قراءة اللقطة (مع memoization لكل طلب عبر g إن أُتيح)
# ─────────────────────────────────────────────────────────────────────
def _load_snapshot_payload(tenant_id: int) -> Optional[dict[str, Any]]:
    """يقرأ payload آخر لقطة capacity ناجحة لهذا المستأجر. None لو لا لقطة
    (لم يربط المزوّد بعد). لا يكسر شيئًا — يُرجع None على أي خطأ."""
    try:
        from .admin_panel_client import SNAPSHOT_CAPACITY, LicenseAdminSnapshotStore
        store = LicenseAdminSnapshotStore()
        snap = store.latest_success(tenant_id=int(tenant_id),
                                     snapshot_type=SNAPSHOT_CAPACITY)
        if not snap:
            return None
        payload = snap.get("payload_json") or {}
        if not isinstance(payload, dict):
            return None
        # contract قد يحتوي features/services/limits بدلاً من الجذر — ندمج.
        contract = payload.get("contract")
        if isinstance(contract, dict) and (
                isinstance(contract.get("services"), dict)
                or isinstance(contract.get("features"), dict)
                or isinstance(contract.get("limits"), dict)):
            return contract
        return payload
    except Exception:  # noqa: BLE001 — fail-open
        _LOG.warning("provider_grant: failed to load snapshot for tenant=%s",
                     tenant_id, exc_info=True)
        return None


def get_payload(tenant_id: int) -> Optional[dict[str, Any]]:
    """يستخدم cache على flask.g إن وُجد طلب — لقطة واحدة لكل request."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            cache = getattr(g, "_provider_grant_cache", None)
            if cache is None:
                cache = {}
                g._provider_grant_cache = cache
            if int(tenant_id) in cache:
                return cache[int(tenant_id)]
            payload = _load_snapshot_payload(tenant_id)
            cache[int(tenant_id)] = payload
            return payload
    except Exception:  # noqa: BLE001
        pass
    return _load_snapshot_payload(tenant_id)


# ─────────────────────────────────────────────────────────────────────
# قراءة حقول الخدمة
# ─────────────────────────────────────────────────────────────────────
# حالات «الإيقاف الفعلي» — تختفي من الشريط الجانبي + الرابط يعطي 403/redirect.
# تُعامَل بنفس قسوة «موقوفة (إيقاف فعلي)» من لوحة المزوّد.
_DISABLED_STATUSES = {"disabled", "suspended", "expired", "cancelled"}

# حالات «مدفوعة-غير-مفعّلة» (locked_upgrade): الخدمة موجودة في الكتالوج
# ومرئية في القائمة، لكنها تحتاج طلب تفعيل/ترقية من المالك. تختلف جوهريًّا
# عن «موقوفة»:
#   • تبقى مرئية في الشريط الجانبي (مع شارة «مقفلة — ترقية»).
#   • الرابط يُعيد التوجيه إلى صفحة «طلب تفعيل / ترقية» (لا 403 ولا hide).
#   • الـAPI يَنقل requires_upgrade=true لتطبيقات الكلاينت.
# نقبل عدّة أسماء يستعملها المزوّد كي تتطابق المعاجم بسلاسة:
_LOCKED_UPGRADE_STATUSES = {
    "locked_upgrade", "requires_activation", "requires_upgrade",
    "paid_not_active", "paid_locked", "upgrade_required",
    "pending_activation", "not_purchased",
}

# حالات الميزة (features.<k>) المقابلة لـ locked_upgrade.
_LOCKED_UPGRADE_FEATURE_STATES = {
    "locked_upgrade", "requires_activation", "upgrade_required",
}


@dataclass(frozen=True)
class ServiceGrant:
    """قراءة الخدمة من اللقطة."""
    key: str
    present:          bool = False     # هل المزوّد ذكر هذه الخدمة أصلًا؟
    enabled:          bool = True      # services.<k>.enabled (افتراضي True)
    status:           str  = "active"  # services.<k>.status
    hidden_portal:    bool = False     # services.<k>.hidden_portal
    feature_state:    str  = "enabled" # features.<k> = enabled|locked|hidden|readonly|locked_upgrade
    raw:              dict = None      # type: ignore[assignment]

    @property
    def requires_upgrade(self) -> bool:
        """«مدفوعة — غير مفعّلة»: تَظهر في القائمة بشارة ترقية، الرابط
        يُعيد إلى صفحة «طلب تفعيل / ترقية» (ليست hard-block ولا hidden).

        تختلف عن disabled في أن العرض الافتراضي مرئيّ + قرار الأكشن لطيف:
        المالك ينقر فيرى شاشة طلب تفعيل بدل صفحة 403.
        """
        status_norm = (self.status or "").strip().lower()
        if self.present and status_norm in _LOCKED_UPGRADE_STATUSES:
            return True
        if self.feature_state in _LOCKED_UPGRADE_FEATURE_STATES:
            return True
        return False

    @property
    def disabled(self) -> bool:
        """«موقوفة» — إيقاف فعلي على كل الريديوس (سوبر-أدمن لا يتجاوز).

        تُعتبر موقوفة إذا (أ) services.<k>.enabled=False أو status ضمن
        قائمة الإيقاف، أو (ب) features.<k> = locked أو hidden.

        ملاحظة: requires_upgrade ليست موقوفة — تَنبثق في مسار منفصل
        (شارة + صفحة طلب تفعيل). لا نَخلطها بـdisabled عمدًا.
        """
        if self.requires_upgrade:
            return False  # locked_upgrade تتقدّم على القراءات الأخرى
        if self.present:
            if not self.enabled:
                return True
            if (self.status or "").strip().lower() in _DISABLED_STATUSES:
                return True
        if self.feature_state in ("locked", "hidden"):
            return True
        return False

    @property
    def readonly(self) -> bool:
        """ميزة للقراءة فقط — تعرض لكن لا تقبل كتابة."""
        return self.feature_state in ("readonly", "read_only")

    @property
    def hidden_from_portal(self) -> bool:
        """«مخفية من البوابة» — تُخفى عن بوّابة الزبون/المشترك فقط (الإدارة تراها).
        تشمل ضمنًا كل موقوفة (الموقوف مخفي تلقائيًّا من البوابة)."""
        return bool(self.hidden_portal) or self.disabled


def lookup(tenant_id: int, service_key: str) -> ServiceGrant:
    """منحة خدمة واحدة لهذا المستأجر. fail-safe — أي خطأ يعيد مسموح."""
    key = (service_key or "").strip().lower()
    if not key:
        return ServiceGrant(key="", raw={})
    payload = get_payload(tenant_id) or {}
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    features = payload.get("features") if isinstance(payload.get("features"), dict) else {}

    svc_raw = services.get(key) if isinstance(services, dict) else None
    feat_raw = features.get(key) if isinstance(features, dict) else None

    present = isinstance(svc_raw, dict) or feat_raw is not None
    enabled = True
    status = "active"
    hidden_portal = False
    if isinstance(svc_raw, dict):
        enabled = bool(svc_raw.get("enabled", True))
        status = str(svc_raw.get("status") or "active")
        hidden_portal = bool(svc_raw.get("hidden_portal") or svc_raw.get("portal_hidden"))

    feature_state = "enabled"
    if isinstance(feat_raw, str):
        feature_state = (feat_raw or "enabled").strip().lower() or "enabled"
    elif isinstance(feat_raw, dict):
        feature_state = str(feat_raw.get("state") or feat_raw.get("status") or "enabled").strip().lower()

    return ServiceGrant(
        key=key, present=present, enabled=enabled, status=status,
        hidden_portal=hidden_portal, feature_state=feature_state,
        raw={"service": svc_raw, "feature": feat_raw},
    )


# ─────────────────────────────────────────────────────────────────────
# مساعدات الاستعلام السريع
# ─────────────────────────────────────────────────────────────────────
def is_service_disabled(tenant_id: int, service_key: str) -> bool:
    """True إذا أوقف المزوّد هذه الخدمة. سوبر-أدمن لا يتجاوز."""
    return lookup(tenant_id, service_key).disabled


def is_hidden_from_portal(tenant_id: int, service_key: str) -> bool:
    """True إذا طلب المزوّد إخفاءها من بوّابة المشترك/الزبون.
    الإدارة تبقى تراها (إلا إذا كانت موقوفة)."""
    return lookup(tenant_id, service_key).hidden_from_portal


def is_readonly(tenant_id: int, service_key: str) -> bool:
    return lookup(tenant_id, service_key).readonly


def requires_upgrade(tenant_id: int, service_key: str) -> bool:
    """True إذا كانت الخدمة «مدفوعة-غير-مفعّلة» (locked_upgrade) — مرئية
    في الشريط الجانبي بشارة ترقية، الرابط يَفتح صفحة «طلب تفعيل / ترقية»."""
    return lookup(tenant_id, service_key).requires_upgrade


def is_capability_granted(tenant_id: int, service_key: str) -> bool:
    """قدرة «افتراضيًّا مُطفأة» (default-OFF): تُمنَح **فقط** عندما يَذكرها
    عقد المزوّد صراحةً مفعّلةً ونشطةً. هذا عكس `is_service_disabled`
    (fail-open): الغياب/الإيقاف/قفل-الترقية = **غير ممنوحة**.

    تُستعمَل للميزات المخفيّة حتى يُفعّلها المزوّد من لوحة التراخيص (مثل
    «إدارة أقسام الواجهة» قيد التطوير). نفس نمط `multi_tenant` (present +
    enabled + active). fail-closed عمدًا: إن لم نَستطِع التحقّق نُبقيها
    مُطفأة (الإخفاء هو الافتراضي الآمن لميزة غير مُنجَزة).
    """
    try:
        g = lookup(tenant_id, service_key)
    except Exception:  # noqa: BLE001 — fail-closed لقدرة default-off
        return False
    if not g.present:
        return False           # العقد لا يَذكرها = لم تُمنَح بعد
    if g.disabled or g.requires_upgrade:
        return False           # موقوفة أو بانتظار تفعيل = غير ممنوحة
    return True


# ─────────────────────────────────────────────────────────────────────
# سقوف الكمّ («مجانية محدودة»)
# ─────────────────────────────────────────────────────────────────────
def get_limit(tenant_id: int, dotted_path: str) -> Optional[int]:
    """حدّ ضمن payload.limits[dotted.path]. None لو غير محدّد. سالب → None."""
    payload = get_payload(tenant_id) or {}
    node: Any = payload.get("limits")
    for part in (dotted_path or "").split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    try:
        val = int(node)
    except (TypeError, ValueError):
        return None
    return val if val >= 0 else None


# الخرائط المعتمدة بين «مفتاح خدمة» و(مفتاح-قياس-استخدام، مسار-سقف).
# نمط متوافق مع license_admin_capacity.CAPACITY_FEATURES.
#
# ملاحظة جوهرية (2026-06-18): «اكتف»/active-online ليست سقفًا على إجمالي
# الحسابات (subscribers.max_total) بل على عدد الجلسات المتزامنة المتصلة
# الآن في كل أنواع الاتصال (cards + subscribers + PPPoE + hotspot). يُفرَض
# auth-time في policy_engine._check_provider_active_cap، لا create-time.
# لذلك أُزيلت subscribers من LIMIT_PATHS وأُضيفت active_online بصِفته
# السقف الرئيسي. تَبقى create-time caps الأخرى (cards/nas/…) كما هي.
LIMIT_PATHS: dict[str, tuple[str, str]] = {
    "active_online":  ("active_online_now",       "active_online.max"),
    "cards":          ("cards_generated_month",   "cards.monthly_generated"),
    "cards_batch":    ("",                         "cards.generate_per_batch"),
    "nas":            ("nas_count",               "nas.max_total"),
    "routers":        ("routers_count",           "routers.max_total"),
    "profiles":       ("profiles_plans_count",    "profiles.max_total"),
    "print_templates":("print_templates_count",   "print_templates.max_active"),
    "admins":         ("admins_count",            "admins.max_total"),
}


# ─────────────────────────────────────────────────────────────────────
# «اكتف» — السقف الرئيسي للمتصلين المتزامنين (concurrent online sessions)
# ─────────────────────────────────────────────────────────────────────
# مسارات حقل العقد التي نَقرأ منها السقف. الأوّل المفضَّل، البواقي توافقية.
# نَتبع التَدرّج لكي يَعمل النظام مع أكثر من صيغة يُرسلها المزوّد بدون
# تحديثات قسرية. التَوصية للمزوّد: استعمال active_online.max.
_ACTIVE_ONLINE_PATHS: tuple[str, ...] = (
    "active_online.max",        # ← المفضَّل
    "active.max",
    "concurrent_online.max",
    "active_subscribers.max",   # تَوافقية مع الاسم القديم (كان يُفهَم خطأً
                                #  كـtotal accounts؛ بقي للاسم نفسه فقط).
)


def get_active_online_cap(tenant_id: int) -> Optional[int]:
    """يَقرأ سقف «اكتف» من العقد. يَحاول عدّة مسارات للحقل (المُفضَّل أوّلًا).

    None  = لا سقف (Unlimited).
    عدد   = السقف (e.g. 100 للtrial، 250/500/1000 للباقات).
    """
    for path in _ACTIVE_ONLINE_PATHS:
        val = get_limit(int(tenant_id), path)
        if val is not None:
            return val
    return None


def count_active_sessions(tenant_id: int,
                            *, exclude_username: str = "") -> int:
    """عدد الجلسات الحيّة فعلاً (مفتوحة + ضمن نافذة الحياة) لهذا المستأجر عبر
    كل أنواع NAS/الجلسات (cards + subscribers + PPPoE + hotspot). يُستعمَل
    auth-time لإنفاذ سقف «اكتف».

    نافذة الحياة (نفس ``live_sessions``/``device_limit``): جلسةٌ يتيمة (راوتر
    أُعيد إقلاعه بلا Accounting-Off) لا تُحتسَب ضدّ السقف للأبد فتَحجب دخولًا
    شرعيًّا. الترشيح بالوقت في بايثون عبر المُحلِّل المُشترَك ``parse_acct_dt``
    فيَصِحّ لصيغتَي FreeRADIUS «مسافة» وISO «…T…Z» معًا (المقارنة المعجمية
    كانت تَستبعد طوابع FreeRADIUS «مسافة»). جلسة بلا طابع (لم يصلها محاسبة
    بعد) تُحتسَب احتياطًا.

    exclude_username (اختياري): يَستبعد جلسات هذا المستخدم من العدّ —
    مفيد عند فحص re-auth كي لا تُحتسب جلسات المستخدم الراهنة ضدّه.
    """
    try:
        import datetime as _dt
        from ..db.connection import db
        from .device_limit import parse_acct_dt
        from .live_sessions import window_minutes
        cutoff = _dt.datetime.utcnow() - _dt.timedelta(minutes=window_minutes())
        if exclude_username:
            rows = db().execute(
                "SELECT acctstarttime, acctupdatetime FROM radacct "
                "WHERE tenant_id = ? AND (acctstoptime IS NULL OR acctstoptime='') "
                "  AND username != ?",
                (int(tenant_id), str(exclude_username)),
            ).fetchall()
        else:
            rows = db().execute(
                "SELECT acctstarttime, acctupdatetime FROM radacct "
                "WHERE tenant_id = ? AND (acctstoptime IS NULL OR acctstoptime='')",
                (int(tenant_id),),
            ).fetchall()
        n = 0
        for r in rows:
            d = dict(r)
            last = (parse_acct_dt(d.get("acctupdatetime"))
                    or parse_acct_dt(d.get("acctstarttime")))
            if last is not None and last < cutoff:
                continue  # يتيمة/زومبي — ليست متصلة الآن
            n += 1
        return n
    except Exception:  # noqa: BLE001 — fail-safe: 0 يَفتح الباب (آمن للـauth)
        return 0


def user_has_open_session(tenant_id: int, username: str) -> bool:
    """هل لدى هذا المستخدم جلسة مفتوحة الآن؟ يَفيد لاستثناء re-auth من
    حساب السقف (المستخدم لن يَزيد الإجمالي عند re-auth)."""
    if not username:
        return False
    try:
        from ..db.connection import db
        row = db().execute(
            "SELECT 1 FROM radacct WHERE tenant_id = ? AND username = ? "
            "  AND acctstoptime IS NULL LIMIT 1",
            (int(tenant_id), str(username)),
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001
        return False


@dataclass(frozen=True)
class LimitDecision:
    feature_key: str
    allowed: bool
    current: int = 0
    limit: Optional[int] = None
    reason: str = ""            # رمز داخلي
    message_ar: str = ""        # رسالة عربية للمستخدم


def _current_usage(tenant_id: int, metric: str) -> int:
    """عدّاد استخدام حالي — يفوّض لـlicense_admin_usage_metering لكلّ مقياس
    عادي، ويُحوّل لـcount_active_sessions لمقياس «اكتف» الخاصّ (لأنّه
    قراءة مباشرة من radacct، ليس عبر usage metering service)."""
    if not metric:
        return 0
    if metric == "active_online_now":
        return count_active_sessions(int(tenant_id))
    try:
        from .license_admin_usage_metering import UsageMeteringService
        return int(UsageMeteringService().collect_metrics(
            tenant_id=int(tenant_id)).get(metric) or 0)
    except Exception:  # noqa: BLE001
        return 0


def check_limit(tenant_id: int, feature_key: str,
                increment: int = 1) -> LimitDecision:
    """يفحص إن كان إنشاء `increment` عناصر جديدة سيتجاوز السقف. آمن — أي خطأ
    يعيد allowed=True (لا نكسر الـauth إن تعطّل التزامن)."""
    spec = LIMIT_PATHS.get((feature_key or "").lower())
    if not spec:
        return LimitDecision(feature_key=feature_key, allowed=True)
    usage_metric, limit_path = spec
    limit = get_limit(tenant_id, limit_path)
    if limit is None:
        return LimitDecision(feature_key=feature_key, allowed=True, limit=None)
    current = _current_usage(tenant_id, usage_metric) if usage_metric else 0
    if current + int(increment or 0) > limit:
        msg = (f"تم الوصول إلى الحدّ المسموح من المزوّد لهذه الخدمة "
                f"({current} من {limit}).")
        return LimitDecision(feature_key=feature_key, allowed=False,
                              current=current, limit=limit,
                              reason="provider_limit_exceeded",
                              message_ar=msg)
    return LimitDecision(feature_key=feature_key, allowed=True,
                          current=current, limit=limit)


# ─────────────────────────────────────────────────────────────────────
# جرد كل المنح للعرض في صفحة الحالة
# ─────────────────────────────────────────────────────────────────────
def list_all_grants(tenant_id: int) -> list[dict[str, Any]]:
    """جرد كل الخدمات/الميزات المعروفة في اللقطة + حالتها — لصفحة الحالة."""
    payload = get_payload(tenant_id) or {}
    out: list[dict[str, Any]] = []
    services = payload.get("services") if isinstance(payload.get("services"), dict) else {}
    features = payload.get("features") if isinstance(payload.get("features"), dict) else {}
    keys = sorted(set(list((services or {}).keys()) + list((features or {}).keys())))
    for k in keys:
        g = lookup(tenant_id, k)
        out.append({
            "key": k,
            "present": g.present,
            "enabled": g.enabled,
            "status": g.status,
            "feature_state": g.feature_state,
            "disabled": g.disabled,
            "requires_upgrade": g.requires_upgrade,
            "readonly": g.readonly,
            "hidden_portal": g.hidden_portal,
            "hidden_from_portal_effective": g.hidden_from_portal,
        })
    return out


def has_snapshot(tenant_id: int) -> bool:
    """هل وصلت أي لقطة من المزوّد؟ يفيد لمعرفة هل النظام مربوط أصلًا."""
    return get_payload(tenant_id) is not None


__all__ = [
    "ServiceGrant", "LimitDecision",
    "get_payload", "lookup",
    "is_service_disabled", "is_hidden_from_portal", "is_readonly",
    "requires_upgrade", "is_capability_granted",
    "get_limit", "check_limit", "LIMIT_PATHS",
    "list_all_grants", "has_snapshot",
    # active-online (concurrent cap) — السقف الرئيسي «اكتف»
    "get_active_online_cap", "count_active_sessions",
    "user_has_open_session",
]
