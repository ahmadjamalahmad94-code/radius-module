"""دورة حياة الترخيص (license lifecycle) — قرار سلوك اللوحة بحسب حالة العقد.

السياق التجاري: المالك يبيع الوصول. ينبغي أن يَفصل النظام بين:
  • انقطاع تزامن عابر (الترخيص ساري والجسر متعطّل دقائق)  → fail-open ✓
  • ترخيص منتهٍ صراحةً                                       → fail-closed
  • ترخيص لم يُفعَّل بعد (نسخة جديدة)                         → fail-closed

البوّابة الصلاحياتية السابقة (provider_grant) كانت fail-open على كل
المسارات — صحيح للانقطاع العابر، خطأ للانتهاء/عدم التفعيل. هذه الوحدة
تُفصِّل القرار:

    NEVER_ACTIVATED            — لا لقطة ترخيص ناجحة قطّ        → BLOCK (activate)
    ACTIVE                      — ترخيص ساري + لقطة حديثة         → ALLOW
    EXPIRED                     — status موقف/منتهٍ/معلَّق          → BLOCK (renew)
                                   أو expires_at مضى ولا grace
    SYNC_OUTAGE_IN_GRACE        — كان ساريًا، اللقطة تقادمت ضمن     → ALLOW (آخر معلوم)
                                   نافذة سماحية محلّية
    SYNC_OUTAGE_BEYOND_GRACE    — تقادمت اللقطة بعد انتهاء السماحية → BLOCK (treat as expired)

نموذج الـpayload (license snapshot) من admin_panel_client:
    {
      "status":      "active|valid|grace|expired|suspended|revoked|…",
      "expires_at":  "ISO" (اختياري),
      "grace_until": "ISO" (اختياري) ← المزوّد يستطيع تمديد السماحية.
    }

الصفحات/المسارات الضرورية للإصلاح (login/logout/locale + ترخيص + تشخيص +
الصفحات اللطيفة نفسها) تبقى مكشوفة في حارس _perm_guard كي لا يُحجَب
المسؤول كلّيًا. هذا قرار سلامة — لا يمكن إصلاح ترخيص منتهٍ إن لم تستطع
حتى الوصول لصفحة الترخيص.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional

_LOG = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════
# اللحظات/الحالات
# ════════════════════════════════════════════════════════════════════════
class LifecycleState(str, Enum):
    NEVER_ACTIVATED          = "never_activated"
    ACTIVE                    = "active"
    EXPIRED                   = "expired"
    SYNC_OUTAGE_IN_GRACE      = "sync_outage_in_grace"
    SYNC_OUTAGE_BEYOND_GRACE  = "sync_outage_beyond_grace"

    @property
    def blocks_panel(self) -> bool:
        return self in (
            LifecycleState.NEVER_ACTIVATED,
            LifecycleState.EXPIRED,
            LifecycleState.SYNC_OUTAGE_BEYOND_GRACE,
        )


@dataclass(frozen=True)
class LifecycleDecision:
    state: LifecycleState
    reason: str = ""                       # رمز فرعي للتشخيص
    last_status: str = ""                  # status من آخر لقطة (إن وُجد)
    expires_at: Optional[str] = None       # ISO، للعرض
    grace_until: Optional[str] = None      # ISO، للعرض
    fetched_at: Optional[str] = None       # ISO آخر تزامن ناجح
    stale_days: float = 0.0                # كم يومًا تأخّر التزامن
    grace_remaining_days: float = 0.0      # المتبقّي قبل التحوّل لـ beyond_grace

    @property
    def blocks_panel(self) -> bool:
        return self.state.blocks_panel


# ════════════════════════════════════════════════════════════════════════
# إعدادات نافذة السماحية المحلّية (للانقطاع العابر)
# ════════════════════════════════════════════════════════════════════════
# عدد الأيام المسموح فيها بـ«last-known-good» بعد آخر تزامن ناجح قبل
# اعتبار الانقطاع طويلًا وفرض الإقفال. القيمة الافتراضية 7 أيام — قابلة
# للتعديل عبر متغيّر بيئة لاختبارات وعمليات. حدّ أقصى 30 يومًا (لا
# نسمح بتعطيل المزامنة شهرًا دون إقفال).
def _sync_grace_days() -> float:
    try:
        raw = os.environ.get("HOBERADIUS_LICENSE_SYNC_GRACE_DAYS", "")
        if not raw:
            return 7.0
        val = float(raw)
        return max(0.5, min(30.0, val))
    except (TypeError, ValueError):
        return 7.0


# ════════════════════════════════════════════════════════════════════════
# تحليل الـpayload
# ════════════════════════════════════════════════════════════════════════
_BLOCKING_STATUSES = {"inactive", "expired", "blocked", "suspended", "revoked",
                       "denied", "disabled", "not_found", "invalid_request",
                       "fingerprint_denied"}
_ACTIVE_STATUSES = {"active", "valid", "ok", "healthy", "grace"}


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        # نقبل ISO مع/بدون 'Z'.
        return datetime.fromisoformat(str(s).replace("Z", ""))
    except (TypeError, ValueError):
        return None


def _extract_license_payload(success_snap: dict) -> dict:
    """يستخرج الـpayload من صفّ لقطة ناجحة (snapshot row). يدمج
    contract.license لو الترخيص داخل contract (بعض الـpayloads المُولّدة)."""
    payload = success_snap.get("payload_json") or {}
    if not isinstance(payload, dict):
        return {}
    contract = payload.get("contract")
    if isinstance(contract, dict):
        lic = contract.get("license")
        if isinstance(lic, dict):
            merged = dict(payload)
            for k in ("expires_at", "grace_until", "mode", "active", "status"):
                if k in lic and k not in payload:
                    merged[k] = lic[k]
            return merged
    return payload


# ════════════════════════════════════════════════════════════════════════
# نقطة الواجهة الرئيسة
# ════════════════════════════════════════════════════════════════════════
def _test_bypass_active() -> bool:
    """تفعيل تجاوز الحارس لاختبارات السلسلة الكاملة. متعمَّد التضييق:
    يتطلّب علامتين معًا (NO_SEED + LICENSE_GATE_TEST_BYPASS) كي لا يَنطفئ
    الحارس بالخطأ في الإنتاج لو ضُبط أحدهما سهوًا. الإنتاج لا يضبط أيّهما.
    الاختبارات المُختصّة بهذا الحارس تُلغي العلامة عبر monkeypatch.delenv."""
    return (os.environ.get("HOBERADIUS_NO_SEED") == "1"
            and os.environ.get("HOBERADIUS_LICENSE_GATE_TEST_BYPASS") == "1")


def evaluate(tenant_id: int,
              *, now: Optional[datetime] = None) -> LifecycleDecision:
    """يحسم حالة دورة حياة الترخيص للمستأجر. fail-safe — أيّ خطأ يَعتَبَر
    الحالة ACTIVE (لا نُغلِق اللوحة بسبب باجٍ في هذا الفحص — البوابة
    الصلاحياتية السابقة تتولّى الحماية ضدّ الأخطاء)."""
    if _test_bypass_active():
        return LifecycleDecision(state=LifecycleState.ACTIVE,
                                  reason="test_bypass")
    now = now or datetime.utcnow()
    try:
        from .admin_panel_client import SNAPSHOT_LICENSE, LicenseAdminSnapshotStore
        store = LicenseAdminSnapshotStore()
        st = store.state(tenant_id=int(tenant_id),
                          snapshot_type=SNAPSHOT_LICENSE)
        latest = st.get("snapshot")   # أيّ لقطة (ناجحة أو لا)
        success = st.get("last_success")  # ناجحة فقط (active/valid/grace/…)
    except Exception:  # noqa: BLE001 — لا نكسر اللوحة
        _LOG.warning("license_lifecycle: snapshot store unavailable",
                     exc_info=True)
        return LifecycleDecision(state=LifecycleState.ACTIVE,
                                  reason="store_unavailable")

    # (0) لقطة موجودة بحالة موقفة/منتهية صريحة — أعلى أولوية، إقفال فوري.
    # هذه تأتي من رسالة مزوّد قاطعة (مثل license_check → expired/revoked).
    # نتجاوز هنا last_success لأنّ store يَفلتر هذه الحالات منه عمدًا.
    if isinstance(latest, dict):
        latest_status = str(latest.get("normalized_status") or "").strip().lower()
        if latest_status in _BLOCKING_STATUSES:
            latest_payload = _extract_license_payload(latest)
            return LifecycleDecision(
                state=LifecycleState.EXPIRED,
                reason=f"status_{latest_status}",
                last_status=latest_status,
                expires_at=str(latest_payload.get("expires_at") or "") or None,
                grace_until=str(latest_payload.get("grace_until") or "") or None,
                fetched_at=str(latest.get("fetched_at") or "") or None,
            )

    if not success:
        # لم يحدث تزامن ناجح للترخيص قطّ → نسخة لم تُفعَّل بعد.
        return LifecycleDecision(state=LifecycleState.NEVER_ACTIVATED,
                                  reason="no_successful_license_snapshot")

    payload = _extract_license_payload(success)
    status = str(payload.get("status") or success.get("normalized_status")
                  or "").strip().lower()

    expires_at = payload.get("expires_at")
    grace_until = payload.get("grace_until")
    fetched_at_raw = success.get("fetched_at")
    fetched_at = _parse_iso(fetched_at_raw)
    expires_dt = _parse_iso(expires_at)
    grace_dt = _parse_iso(grace_until)

    # (1) حالة موقفة/منتهية صريحة من المزوّد — إقفال فوري بدون نظر للوقت.
    if status in _BLOCKING_STATUSES:
        return LifecycleDecision(
            state=LifecycleState.EXPIRED,
            reason=f"status_{status}",
            last_status=status,
            expires_at=str(expires_at) if expires_at else None,
            grace_until=str(grace_until) if grace_until else None,
            fetched_at=str(fetched_at_raw) if fetched_at_raw else None,
        )

    # (2) expires_at مضى ولا توجد grace_until فعّالة → منتهٍ.
    if expires_dt and now > expires_dt:
        if not grace_dt or now > grace_dt:
            return LifecycleDecision(
                state=LifecycleState.EXPIRED,
                reason="expires_at_passed",
                last_status=status or "expired",
                expires_at=str(expires_at) if expires_at else None,
                grace_until=str(grace_until) if grace_until else None,
                fetched_at=str(fetched_at_raw) if fetched_at_raw else None,
            )

    # (3) الترخيص في حالة فعّالة (active/valid/grace/healthy/ok). تحقّق من
    # حداثة اللقطة — انقطاع طويل بدون تجديد يستحقّ إقفالًا.
    stale_days = 0.0
    grace_days = _sync_grace_days()
    if fetched_at:
        delta_sec = (now - fetched_at).total_seconds()
        stale_days = max(0.0, delta_sec / 86400.0)
        stale_after = int(success.get("stale_after_seconds") or 86400)
        if delta_sec > stale_after:
            if stale_days > grace_days:
                return LifecycleDecision(
                    state=LifecycleState.SYNC_OUTAGE_BEYOND_GRACE,
                    reason="sync_grace_exhausted",
                    last_status=status or "active",
                    expires_at=str(expires_at) if expires_at else None,
                    grace_until=str(grace_until) if grace_until else None,
                    fetched_at=str(fetched_at_raw) if fetched_at_raw else None,
                    stale_days=round(stale_days, 1),
                    grace_remaining_days=0.0,
                )
            return LifecycleDecision(
                state=LifecycleState.SYNC_OUTAGE_IN_GRACE,
                reason="sync_stale_within_grace",
                last_status=status or "active",
                expires_at=str(expires_at) if expires_at else None,
                grace_until=str(grace_until) if grace_until else None,
                fetched_at=str(fetched_at_raw) if fetched_at_raw else None,
                stale_days=round(stale_days, 1),
                grace_remaining_days=round(max(0.0, grace_days - stale_days), 1),
            )

    # (4) كل شيء سليم: ساري وحديث.
    return LifecycleDecision(
        state=LifecycleState.ACTIVE,
        reason="active",
        last_status=status or "active",
        expires_at=str(expires_at) if expires_at else None,
        grace_until=str(grace_until) if grace_until else None,
        fetched_at=str(fetched_at_raw) if fetched_at_raw else None,
    )


# ─────────────────────────────────────────────────────────────────────
# memoization لكلّ طلب (مثل provider_grant) — لقطة واحدة لكل request
# ─────────────────────────────────────────────────────────────────────
def evaluate_cached(tenant_id: int) -> LifecycleDecision:
    try:
        from flask import g, has_request_context
        if has_request_context():
            cache = getattr(g, "_license_lifecycle_cache", None)
            if cache is None:
                cache = {}
                g._license_lifecycle_cache = cache
            if int(tenant_id) in cache:
                return cache[int(tenant_id)]
            decision = evaluate(tenant_id)
            cache[int(tenant_id)] = decision
            return decision
    except Exception:  # noqa: BLE001
        pass
    return evaluate(tenant_id)


__all__ = [
    "LifecycleState", "LifecycleDecision",
    "evaluate", "evaluate_cached",
]
