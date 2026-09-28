"""Safe RADIUS apply layer for accounting decisions.

Accounting decides entitlement. This module performs the operational account
update through the same RadiusAdapter used by the web/API paths.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import uuid4

from ..core.constants import (
    AUDIT_ACTION_UPDATE, STATUS_DISABLED, STATUS_ENABLED, STATUS_SUSPENDED,
)
from ..core.errors import RadiusValidationError
from ..db.helpers import dt_to_iso
from ..integration.factory import get_radius_adapter
from .audit import get_audit_service


def apply_activation_minutes(
    *,
    username: str,
    minutes: int,
    actor: str,
    source: str,
    dry_run: bool = False,
    respect_unlimited: bool = False,
) -> dict:
    """Add ``minutes`` to the account (anchor = max(now, expiry)).

    ``respect_unlimited`` (loans): an account with NO expiry is an unlimited
    subscription — a temporary-access loan must not impose an end on it, so
    nothing is changed and the result says why. Payments keep the historical
    rule (a payment on a never-activated account without expiry activates it
    for the purchased period — the API/app «create then pay» flow)."""
    if not username:
        raise RadiusValidationError("username required for RADIUS apply")
    if minutes <= 0:
        raise RadiusValidationError("minutes must be > 0 for RADIUS apply")

    adapter = get_radius_adapter()
    account = adapter.get_account(username)
    now = datetime.utcnow()
    current_expire = account.expire_at
    if current_expire is None and respect_unlimited:
        # 🔴 مشتركٌ بلا تاريخ انتهاء = اشتراكٌ غير محدود. «إضافة» مدّةٍ إليه
        # كانت **تفرض** نهايةً (سلفة ساعة تجعل غير المحدود ساعةً واحدة). لا
        # نمسّ الحساب؛ المال يبقى مسجّلًا والنتيجة تقول لماذا لم يتغيّر الوقت.
        return {
            "applied_to_radius": False,
            "dry_run": bool(dry_run),
            "radius_action_id": None,
            "source": source,
            "username": username,
            "minutes": minutes,
            "old_expire_at": None,
            "new_expire_at": None,
            "status": "skipped",
            "reason": "unlimited_subscriber",
            "message": "المشترك بلا تاريخ انتهاء (غير محدود) — لم يُغيَّر وقته.",
        }
    base = current_expire if current_expire and current_expire > now else now
    new_expire = base + timedelta(minutes=minutes)
    action_id = f"radius-apply-{uuid4().hex[:12]}"
    result = {
        "applied_to_radius": False,
        "dry_run": bool(dry_run),
        "radius_action_id": action_id,
        "source": source,
        "username": username,
        "minutes": minutes,
        "old_expire_at": dt_to_iso(current_expire),
        "new_expire_at": dt_to_iso(new_expire),
        "status": "planned" if dry_run else "applied",
    }
    if dry_run:
        return result

    # الوقت المُضاف يُفعّل حسابًا «منتهيًا/معلّقًا» — لكنّه لا يرفع حظرًا
    # وضعه المشغّل يدويًّا: المعطَّل/الموقوف يبقى كما هو (كانت السلفة تُعيد
    # تفعيل المعطَّل بصمت). إعادة التفعيل قرارٌ صريح بزرّ «تفعيل».
    keep_status = account.status in {STATUS_DISABLED, STATUS_SUSPENDED}
    saved = adapter.upsert_account(replace(
        account,
        expire_at=new_expire,
        status=account.status if keep_status else STATUS_ENABLED,
    ))
    result.update({
        "applied_to_radius": True,
        "saved_status": saved.status,
        "saved_expire_at": dt_to_iso(saved.expire_at),
    })
    try:
        get_audit_service().record(
            actor=actor,
            action=AUDIT_ACTION_UPDATE,
            target_type="subscriber",
            target_id=username,
            payload={
                "radius_action_id": action_id,
                "source": source,
                "minutes": minutes,
                "old_expire_at": dt_to_iso(current_expire),
                "new_expire_at": dt_to_iso(new_expire),
            },
        )
    except Exception:
        # Account update is authoritative; audit failure should not roll it back.
        pass
    return result


def revoke_activation_minutes(*, username: str, minutes: int, actor: str,
                              source: str) -> dict:
    """عكسُ ``apply_activation_minutes`` عند إلغاء دفعة: يطرح المدّة المضافة
    **بالضبط** من نهاية الاشتراك، ولا ينزل بها عن «الآن» (لا نهايةَ في الماضي
    ولا مساس بما قبل الدفعة إن كان قد انقضى أصلًا). غير المحدود لا يُمسّ."""
    if not username or int(minutes or 0) <= 0:
        return {"status": "skipped", "minutes": 0}
    adapter = get_radius_adapter()
    account = adapter.get_account(username)
    current_expire = account.expire_at
    if current_expire is None:
        return {"status": "skipped", "minutes": 0, "reason": "unlimited_subscriber"}
    now = datetime.utcnow()
    new_expire = max(current_expire - timedelta(minutes=int(minutes)), now)
    if new_expire >= current_expire:
        return {"status": "skipped", "minutes": 0, "reason": "already_expired",
                "old_expire_at": dt_to_iso(current_expire)}
    adapter.upsert_account(replace(account, expire_at=new_expire))
    removed = int((current_expire - new_expire).total_seconds() // 60)
    try:
        get_audit_service().record(
            actor=actor, action=AUDIT_ACTION_UPDATE, target_type="subscriber",
            target_id=username,
            payload={"source": source, "minutes": -removed,
                     "old_expire_at": dt_to_iso(current_expire),
                     "new_expire_at": dt_to_iso(new_expire)},
        )
    except Exception:  # noqa: BLE001 — التدقيق لا يُلغي الاسترجاع
        pass
    return {"status": "reverted", "minutes": removed,
            "old_expire_at": dt_to_iso(current_expire),
            "new_expire_at": dt_to_iso(new_expire)}
