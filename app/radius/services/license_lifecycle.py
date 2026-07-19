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


def _capacity_implies_active(tenant_id: int) -> tuple[Optional[dict],
                                                         Optional[dict],
                                                         Optional[dict]]:
    """يقرأ آخر لقطة capacity_contract ناجحة + يُحاول استخراج license-block
    منها. يُرجع (success_snapshot, payload, license_block) أو (None, None, None).

    لقطة capacity من المزوّد قد تحمل license-block مدمجًا تحت أحد المسارَين:
      payload.license.{status,expires_at,activated,…}
      payload.contract.license.{...}
    وقد لا تحمله إطلاقًا — في تلك الحالة وجود اللقطة نفسها (مع services
    وlimits) دليل ضمنيّ على «تم التفعيل» من المزوّد. fail-safe."""
    try:
        from .admin_panel_client import (SNAPSHOT_CAPACITY,
                                           LicenseAdminSnapshotStore)
        store = LicenseAdminSnapshotStore()
        st = store.state(tenant_id=int(tenant_id),
                          snapshot_type=SNAPSHOT_CAPACITY)
        success = st.get("last_success")
        if not success:
            return None, None, None
        payload = success.get("payload_json") or {}
        if not isinstance(payload, dict):
            return success, {}, None
        # license قد يكون على الجذر أو داخل contract.
        lic = payload.get("license") if isinstance(payload.get("license"), dict) else None
        if lic is None:
            contract = payload.get("contract")
            if isinstance(contract, dict):
                lic = contract.get("license") if isinstance(contract.get("license"), dict) else None
        return success, payload, lic
    except Exception:  # noqa: BLE001
        return None, None, None


def _activated_from_license_block(lic: dict) -> bool:
    """هل license-block يقول صراحةً «مفعّل»؟ يقبل عدّة أشكال متوقّعة من المزوّد:
      • license.status in {active,valid,ok,healthy,grace}
      • license.activated == True
      • license.state in {active,valid,…}
      • license.active == True (نمط قديم)
    """
    if not isinstance(lic, dict):
        return False
    if lic.get("activated") is True or lic.get("active") is True:
        return True
    for key in ("status", "state"):
        val = str(lic.get(key) or "").strip().lower()
        if val and val in _ACTIVE_STATUSES:
            return True
    return False


def evaluate(tenant_id: int,
              *, now: Optional[datetime] = None) -> LifecycleDecision:
    """يحسم حالة دورة حياة الترخيص للمستأجر. fail-safe — أيّ خطأ يَعتَبَر
    الحالة ACTIVE (لا نُغلِق اللوحة بسبب باجٍ في هذا الفحص — البوابة
    الصلاحياتية السابقة تتولّى الحماية ضدّ الأخطاء)."""
    if _test_bypass_active():
        return LifecycleDecision(state=LifecycleState.ACTIVE,
                                  reason="test_bypass")
    # MT16 — وضع الاستضافة المفتوحة: لا بوابة ترخيص إطلاقًا (النسخة تعمل
    # بلا مفتاح؛ الحدود من مالك الاستضافة لكل جهة، لا من ترخيص مركزيّ).
    from ..core.hosting_mode import open_hosting
    if open_hosting():
        return LifecycleDecision(state=LifecycleState.ACTIVE,
                                  reason="open_hosting")
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
        # لا لقطة license مستقلّة. قبل أن نقفل بـ never_activated، ننظر
        # في لقطة capacity_contract — قد تحمل license-block مدمجًا أو
        # تكفي وجودها (مع services/limits) كدليل على «تم التفعيل» من
        # المزوّد. هذا فيكس الحالة التي يُرسل فيها المزوّد عقد قدرات بدون
        # license-block منفصل (لقطة شركتي).
        cap_success, cap_payload, cap_license = _capacity_implies_active(tenant_id)
        if cap_success and isinstance(cap_payload, dict):
            cap_fetched_raw = cap_success.get("fetched_at")
            cap_fetched_dt = _parse_iso(cap_fetched_raw)
            # حالة موقوفة/منتهية صريحة في license-block المضمَّن → إقفال.
            if isinstance(cap_license, dict):
                lic_status = str(cap_license.get("status")
                                  or cap_license.get("state")
                                  or "").strip().lower()
                if lic_status in _BLOCKING_STATUSES:
                    return LifecycleDecision(
                        state=LifecycleState.EXPIRED,
                        reason=f"status_{lic_status}",
                        last_status=lic_status,
                        expires_at=str(cap_license.get("expires_at") or "") or None,
                        grace_until=str(cap_license.get("grace_until") or "") or None,
                        fetched_at=str(cap_fetched_raw) if cap_fetched_raw else None,
                    )
                # license-block يقول مفعّل: تحقّق من expires_at.
                if _activated_from_license_block(cap_license):
                    lic_expires = cap_license.get("expires_at")
                    lic_grace = cap_license.get("grace_until")
                    exp_dt = _parse_iso(lic_expires)
                    grace_dt = _parse_iso(lic_grace)
                    if exp_dt and now > exp_dt and (not grace_dt or now > grace_dt):
                        return LifecycleDecision(
                            state=LifecycleState.EXPIRED,
                            reason="expires_at_passed",
                            last_status=str(cap_license.get("status") or "expired"),
                            expires_at=str(lic_expires) if lic_expires else None,
                            grace_until=str(lic_grace) if lic_grace else None,
                            fetched_at=str(cap_fetched_raw) if cap_fetched_raw else None,
                        )
                    return LifecycleDecision(
                        state=LifecycleState.ACTIVE,
                        reason="active_via_capacity_license_block",
                        last_status=str(cap_license.get("status")
                                          or cap_license.get("state") or "active"),
                        expires_at=str(lic_expires) if lic_expires else None,
                        grace_until=str(lic_grace) if lic_grace else None,
                        fetched_at=str(cap_fetched_raw) if cap_fetched_raw else None,
                    )
            # لا license-block، لكن العقد نفسه يحمل grants فعلية (services
            # أو limits أو features) من مزوّد متّصل → دليل ضمنيّ على
            # التفعيل. هذا ما يُصلح حالة شركتي (44 خدمة + لا license-block).
            has_grants = (isinstance(cap_payload.get("services"), dict)
                          and bool(cap_payload.get("services"))) \
                or (isinstance(cap_payload.get("limits"), dict)
                    and bool(cap_payload.get("limits"))) \
                or (isinstance(cap_payload.get("features"), dict)
                    and bool(cap_payload.get("features"))) \
                or (isinstance(cap_payload.get("contract"), dict)
                    and any(isinstance(cap_payload["contract"].get(k), dict)
                             and cap_payload["contract"][k]
                             for k in ("services", "limits", "features")))
            if has_grants:
                return LifecycleDecision(
                    state=LifecycleState.ACTIVE,
                    reason="active_via_capacity_grants",
                    last_status=str(cap_payload.get("status") or "active"),
                    fetched_at=str(cap_fetched_raw) if cap_fetched_raw else None,
                )
        # لم تَفلح أيّ من المسارات → لم يُفعَّل بعد فعلًا.
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
