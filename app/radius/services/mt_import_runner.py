"""mt_import_runner — تنفيذ استيراد المشتركين من المعاينة + كتابة السجلّ.

الجزء الرابع من ميزة «استيراد المشتركين من المايكروتيك». يأخذ
:class:`ImportPreview` (المبنيّة في الزيادة 3) ووضع التعامل مع التكرار،
ثمّ يكتب المشتركين فعليًّا عبر مُحوِّل RADIUS (يُبقي جداول RADIUS متزامنة)،
ويسجّل العملية كاملةً في `mikrotik_import_logs`.

نموذج الكتابة — «أفضل-جهد محاسَب» لا «الكل-أو-لا-شيء»:
  كل صفّ يُكتب ذرّيًّا عبر `adapter.upsert_account` (المُحوِّل يلفّ الكتابة
  بمعاملة لكل حساب، فلا يبقى مشترك نصف-مكتوب أبدًا). صفّ فاشل (تحقّق/تعارض)
  يُسجَّل في `errors` ولا يُجهض الدفعة — صفّ سيّئ لا يُلغي مئة صفّ سليم. عطل
  بنيوي غير متوقّع يُجهض ويُسجَّل status=failed مع العدّادات الجزئية. هذا
  يطابق مخطّط السجلّ (imported/updated/skipped/failed) من الزيادة 1.

أوضاع التكرار (للموجودين مسبقًا بنفس username):
  • skip        — تخطٍّ صامت (يُحتسَب skipped).
  • only_new    — مثل skip (لا يُمسّ الموجود إطلاقًا).
  • update      — تحديث الموجود (الخطّة/كلمة المرور/MAC/الحالة) → updated.
  • conflict    — يُسجَّل تعارضًا (failed) ولا يُكتَب.

ربط الخطّة: المعاينة ربطت ما أمكن. للبروفايلات غير المربوطة:
  • create_missing_plans=True  → إنشاء خطّة آمنة باسم البروفايل وربطها.
  • create_missing_plans=False → يُستورَد الحساب بلا خطّة (plan_id=None) مع
    ملاحظة — لا نُسقط الحساب فقط لأن بروفايله غير معروف.

أمان: تُعاد كلمة مرور الراوتر للحساب الجديد كما هي (RADIUS PAP يحتاجها)؛ في
وضع التحديث لا نمسح كلمة مرور موجودة بقيمة فارغة. لا كلمات مرور في السجلّ.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Optional

from ..core.constants import STATUS_DISABLED, STATUS_ENABLED
from ..core.errors import RadiusValidationError
from ..core.types import Subscriber
from .mt_import_service import (
    ImportPreview, PLAN_MATCHED, ROW_DUPLICATE, ROW_INVALID, ROW_NEW,
)

# أوضاع التكرار.
DUP_SKIP = "skip"
DUP_ONLY_NEW = "only_new"
DUP_UPDATE = "update"
DUP_CONFLICT = "conflict"
DUP_MODES = (DUP_SKIP, DUP_ONLY_NEW, DUP_UPDATE, DUP_CONFLICT)

# مرادفات واردة من الواجهة → الوضع القانوني.
_DUP_ALIASES = {
    "skip_existing": DUP_SKIP, "skip": DUP_SKIP,
    "only_new": DUP_ONLY_NEW, "new_only": DUP_ONLY_NEW, "onlynew": DUP_ONLY_NEW,
    "update": DUP_UPDATE, "update_existing": DUP_UPDATE, "overwrite": DUP_UPDATE,
    "conflict": DUP_CONFLICT, "fail": DUP_CONFLICT,
}


@dataclass
class ImportResult:
    import_type: str = ""
    transport: str = ""
    duplicate_mode: str = DUP_SKIP
    dry_run: bool = False
    imported: int = 0          # حسابات جديدة كُتبت
    updated: int = 0           # موجودون حُدِّثوا
    skipped: int = 0           # متخطَّون (تكرار/غير صالح)
    failed: int = 0            # فشل/تعارض
    created_plans: list[str] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)   # {username, action, reason}
    status: str = "completed"
    log_id: Optional[int] = None

    @property
    def total(self) -> int:
        return self.imported + self.updated + self.skipped + self.failed

    def to_dict(self) -> dict:
        return {
            "import_type": self.import_type,
            "transport": self.transport,
            "duplicate_mode": self.duplicate_mode,
            "dry_run": self.dry_run,
            "total": self.total,
            "imported": self.imported,
            "updated": self.updated,
            "skipped": self.skipped,
            "failed": self.failed,
            "created_plans": list(self.created_plans),
            "errors": list(self.errors),
            "status": self.status,
            "log_id": self.log_id,
        }


def _norm_dup_mode(mode: str) -> str:
    m = str(mode or "").strip().lower()
    if m in DUP_MODES:
        return m
    return _DUP_ALIASES.get(m, DUP_SKIP)


def _subscriber_from_candidate(tenant_id: int, cand) -> Subscriber:
    """يبني Subscriber جديدًا من مرشّح المعاينة (للإنشاء)."""
    return Subscriber(
        id=None, tenant_id=int(tenant_id),
        username=cand.username, password=cand.password,
        user_type="subscriber", service_type=cand.service_type or "Hotspot",
        plan_id=cand.plan_id,
        mac_lock=(cand.mac or None),
        static_ip=(cand.static_ip or None),
        status=(STATUS_DISABLED if cand.disabled else STATUS_ENABLED),
    )


def _ensure_plan(tenant_id: int, cand, cache: dict, result: ImportResult,
                 dry_run: bool) -> None:
    """ينشئ خطّة آمنة باسم البروفايل غير المربوط ويربطها (إن طُلب)."""
    if cand.plan_status == PLAN_MATCHED or not cand.profile:
        return
    key = cand.profile.strip().lower()
    if key in cache:
        cand.plan_id = cache[key]
        return
    if dry_run:
        if cand.profile not in result.created_plans:
            result.created_plans.append(cand.profile)
        return
    from ..core.types import AccessPlan
    from ..db.repos import plans_repo
    plan = plans_repo.upsert_plan(AccessPlan(
        id=None, tenant_id=int(tenant_id), name=cand.profile, enabled=True,
        service_type=cand.service_type or "",
        description="أُنشئت تلقائيًّا أثناء استيراد المايكروتيك"))
    cache[key] = int(plan.id)
    cand.plan_id = int(plan.id)
    result.created_plans.append(cand.profile)


def run_import(*, tenant_id: int, nas: Mapping[str, Any], preview: ImportPreview,
               duplicate_mode: str = DUP_SKIP, actor: str = "",
               actor_name: str = "", actor_id: int = 0,
               create_missing_plans: bool = False,
               dry_run: bool = False) -> ImportResult:
    """ينفّذ الاستيراد من المعاينة ويكتب سجلّ العملية (إلا في dry_run).

    لا يرفع للراوت: الأخطاء المتوقّعة تُحتسَب في `failed`/`errors`؛ عطل
    بنيوي غير متوقّع يُنهي بـstatus=failed مع العدّادات الجزئية."""
    mode = _norm_dup_mode(duplicate_mode)
    started_at = datetime.utcnow().isoformat() + "Z"
    result = ImportResult(import_type=preview.import_type,
                          transport=preview.transport, duplicate_mode=mode,
                          dry_run=bool(dry_run))

    adapter = None
    if not dry_run:
        from ..integration.factory import get_radius_adapter
        adapter = get_radius_adapter()

    plan_cache: dict = {}

    try:
        for row in preview.rows:
            cand = row.candidate
            if row.status == ROW_INVALID:
                result.skipped += 1
                continue

            # تكرار: حسب الوضع.
            if row.status == ROW_DUPLICATE:
                if mode in (DUP_SKIP, DUP_ONLY_NEW):
                    result.skipped += 1
                    continue
                if mode == DUP_CONFLICT:
                    result.failed += 1
                    result.errors.append({"username": cand.username,
                                          "action": "conflict",
                                          "reason": "موجود مسبقًا (تعارض)"})
                    continue
                # mode == update → تحديث.
                if create_missing_plans:
                    _ensure_plan(tenant_id, cand, plan_cache, result, dry_run)
                try:
                    if not dry_run:
                        _apply_update(adapter, tenant_id, cand)
                    result.updated += 1
                except (RadiusValidationError, Exception) as exc:  # noqa: BLE001
                    result.failed += 1
                    result.errors.append({"username": cand.username,
                                          "action": "update",
                                          "reason": str(exc)[:200]})
                continue

            # جديد.
            if create_missing_plans:
                _ensure_plan(tenant_id, cand, plan_cache, result, dry_run)
            try:
                if not dry_run:
                    adapter.upsert_account(_subscriber_from_candidate(tenant_id, cand))
                result.imported += 1
            except (RadiusValidationError, Exception) as exc:  # noqa: BLE001
                result.failed += 1
                result.errors.append({"username": cand.username,
                                      "action": "create",
                                      "reason": str(exc)[:200]})
    except Exception as exc:  # noqa: BLE001 — عطل بنيوي يُجهض الدفعة
        result.status = "failed"
        result.errors.append({"username": "", "action": "fatal",
                              "reason": str(exc)[:200]})

    # كتابة السجلّ (إلا المحاكاة).
    if not dry_run:
        try:
            from ..db.repos import mikrotik_import_logs_repo as logs
            result.log_id = logs.create(
                tenant_id=int(tenant_id),
                nas_id=int(nas.get("id") or 0),
                nas_name=str(nas.get("name") or ""),
                import_type=preview.import_type,
                source="mikrotik", transport=preview.transport,
                duplicate_mode=mode,
                total=result.total, imported=result.imported,
                updated=result.updated, skipped=result.skipped,
                failed=result.failed, errors=result.errors,
                status=result.status,
                message=_summary_message(result),
                started_by=int(actor_id or 0), started_by_name=actor_name or actor,
                started_at=started_at,
                finished_at=datetime.utcnow().isoformat() + "Z")
        except Exception:  # noqa: BLE001 — فشل السجلّ لا يُبطل استيرادًا تمّ
            result.log_id = None
    return result


def _apply_update(adapter, tenant_id: int, cand) -> None:
    """يحدّث مشتركًا موجودًا: الخطّة/MAC/IP/الحالة، وكلمة المرور إن وُفّرت.

    لا يمسح كلمة مرور موجودة بقيمة فارغة (RADIUS PAP يحتاجها)."""
    existing = adapter.get_account(cand.username)
    changes: dict = {
        "service_type": cand.service_type or existing.service_type,
        "status": STATUS_DISABLED if cand.disabled else STATUS_ENABLED,
    }
    if cand.plan_id is not None:
        changes["plan_id"] = cand.plan_id
    if cand.mac:
        changes["mac_lock"] = cand.mac
    if cand.static_ip:
        changes["static_ip"] = cand.static_ip
    if (cand.password or "").strip():
        changes["password"] = cand.password
    adapter.upsert_account(replace(existing, **changes))


def _summary_message(result: ImportResult) -> str:
    return (f"جديد {result.imported} · محدّث {result.updated} · "
            f"متخطّى {result.skipped} · فاشل {result.failed}")


__all__ = [
    "DUP_SKIP", "DUP_ONLY_NEW", "DUP_UPDATE", "DUP_CONFLICT", "DUP_MODES",
    "ImportResult", "run_import",
]
