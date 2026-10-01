"""GET /api/v1/provider/grants — عقد منح المزوّد لتطبيقات الكلاينت (Flutter).

عقد محدّد لتطبيق الـFlutter (وأيّ كلاينت آخر) كي يطبّق نفس قرار اللوحة الويب:
  • أيّ شاشات لايف-سايكل يعرضها للمستخدم (فعّل/منتهٍ/نشط)
  • أيّ أقسام يُخفيها من قائمة التطبيق (خدمات موقوفة)
  • أيّ أزرار «إنشاء» يَحظرها لأن السقف وصل (الكميّ)
  • متى يُعامل انقطاع التزامن كـfail-open (آخر معلوم) ضد انتهاء قاطع

النقطة tenant-scoped (g.tenant_id يأتي من require_api_token)، read-only،
لا تكتب شيئًا في DB، آمنة في hot path.

تُستهلك من الكلاينت كل بضع دقائق (أو عند الدخول/التحديث)؛ شكل الـpayload
ثابت كي لا يَكسر الكلاينت عند توسعات السقوف/الخدمات لاحقًا.
"""
from __future__ import annotations

from typing import Any

from flask import Blueprint, g

from ..auth import require_api_token
from ..responses import ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/provider/grants", "provider_grants",
                     provider_grants, methods=["GET"])


# ─────────────────────────────────────────────────────────────────────
# نقطة العرض
# ─────────────────────────────────────────────────────────────────────
@require_api_token
def provider_grants():
    """يُرجع حالة الترخيص + المنح + السقوف + حداثة التزامن للمستأجر الحالي."""
    tenant_id = int(getattr(g, "tenant_id", 1) or 1)

    # (1) lifecycle state — نفس قرار حارس اللوحة الويب
    from app.radius.services.license_lifecycle import evaluate_cached
    decision = evaluate_cached(tenant_id)

    # (2) خدمات المزوّد — نفس list_all_grants التي تَستعملها صفحة الحالة
    from app.radius.services import provider_grant
    services = provider_grant.list_all_grants(tenant_id)
    has_snapshot = provider_grant.has_snapshot(tenant_id)

    # (3) سقوف الكميّ — لكل feature_key مُسجَّل، current + limit + remaining
    limits: dict[str, dict[str, Any]] = {}
    for feature_key, (usage_metric, limit_path) in provider_grant.LIMIT_PATHS.items():
        # check_limit يحسبهما داخليًّا بأمان + يستخدم نفس مقاييس الاستخدام
        # التي يستعملها /api/v1/system/capacity (UsageMeteringService).
        dec = provider_grant.check_limit(tenant_id, feature_key, increment=0)
        # increment=0: نريد القراءة فقط (current + limit) دون رفض حدّيّ.
        remaining = None
        if dec.limit is not None:
            remaining = max(0, int(dec.limit) - int(dec.current or 0))
        limits[feature_key] = {
            "current":    int(dec.current or 0),
            "limit":      dec.limit,                   # None = بلا سقف
            "remaining":  remaining,                   # None = بلا سقف
            "limit_path": limit_path,                  # للتشخيص/الربط
            "usage_metric": usage_metric or None,
        }

    # (4) license payload للعرض في الكلاينت
    license_block = {
        "state":                decision.state.value,
        "blocks_panel":         decision.blocks_panel,
        "status":               decision.last_status or "",
        "reason":               decision.reason,
        "expires_at":           decision.expires_at,
        "grace_until":          decision.grace_until,
        "fetched_at":           decision.fetched_at,
        "stale_days":           decision.stale_days,
        "grace_remaining_days": decision.grace_remaining_days,
    }

    # (5) sync — معلومات أعلى-مستوى للكلاينت كي يميّز transient عن definitive
    from app.radius.services.license_lifecycle import _sync_grace_days
    sync_block = {
        "has_snapshot":         has_snapshot,
        "stale":                decision.stale_days > 0
                                  and decision.state.value in (
                                      "sync_outage_in_grace",
                                      "sync_outage_beyond_grace"),
        "stale_days":           decision.stale_days,
        "grace_days":           _sync_grace_days(),
        "grace_remaining_days": decision.grace_remaining_days,
    }

    # 🔒 SEC r5perms — صفحةُ الويبِ المكافئةُ (`provider_grants_status_page`)
    # مقصورةٌ على المالك **لأنّها تكشف محتوى عقد التراخيص**، بينما هذه
    # النهايةُ كانت «مصادقةٌ فقط» (`API_AUTH_ONLY`) فأيُّ مديرٍ ولو بـ
    # `dashboard.view` وحدَه كان يقرأ العقدَ كاملًا: تواريخَ الانتهاءِ
    # والسماحِ وعددَ المدراءِ الحقيقيَّ والسقوفَ الكميّةَ لكلِّ ميزة.
    # الإبقاءُ على ما يَحتاجه التطبيقُ فعلًا لرسمِ نفسِه فقط:
    #   • `services` (أيُّ بنودٍ تُرسَم/تُقفَل) — للجميع.
    #   • `limits.active_online` — تطبيقُ فلاتر يَعرض «N من M متّصل».
    #   • حالةُ الترخيصِ التي تَحجب اللوحة (state/blocks_panel/status).
    # وحجبُ الباقي (تواريخ العقدِ وأرقامُ السماحِ وسقوفُ المدراء/الكروت/
    # الحزم) عن غيرِ المالك. المالكُ/الشريكُ يَرى كلَّ شيءٍ كما كان.
    # النهايةُ محروسةٌ أصلًا بمصادقةِ أدمن (`API_AUTH_ONLY`)، فالمجهولُ لا
    # يَصلها. وغيابُ `admin_id` يعني **اعتمادًا رئيسيًّا غيرَ مربوطٍ بحساب**
    # (توكن بيئة) وهو بمقامِ المالكِ بعرفِ `auth/owner` نفسِه
    # (‏`actor_id=None` ⇒ مالك) — فلا نَحجب عنه ولا نَكسر عقدَ v3.
    _owner_view = True
    try:
        from app.radius.auth.owner import is_owner_like_id
        from flask import session as _s
        _aid = getattr(g, "admin_id", None) or _s.get("admin_id")
        _owner_view = (not _aid) or is_owner_like_id(int(_aid))
    except Exception:  # noqa: BLE001 — لا نَكسر رسمَ قوائمِ التطبيق
        _owner_view = True
    if not _owner_view:
        limits = {k: v for k, v in limits.items() if k == "active_online"}
        license_block = {
            "state":        license_block["state"],
            "blocks_panel": license_block["blocks_panel"],
            "status":       license_block["status"],
            "reason":       license_block["reason"],
        }
        sync_block = {"has_snapshot": has_snapshot, "stale": sync_block["stale"]}

    return ok({
        "license":   license_block,
        "services":  services,   # كل عنصر فيه requires_upgrade (v2+) — يَستعمله
                                  # الكلاينت لإبقاء البند مرئيًّا مع شارة قفل
                                  # بدل إخفائه (الإخفاء فقط للموقوفة فعلًا).
        "limits":    limits,    # v3+: «اكتف» (active_online) هو السقف الرئيسي،
                                #  current = جلسات radacct المفتوحة، limit من
                                #  العقد، remaining = limit - current. حلّ
                                #  محل subscribers.max_total القديم. الكلاينت
                                #  Flutter يَستعمل limits.active_online لعرض
                                #  «N من M متّصل» وحظر الإنشاء عند الامتلاء.
        "has_snapshot": has_snapshot,
        "sync":      sync_block,
        # v3 (2026-06-18): subscribers لم يَعد سقفًا (concurrent cap بدلاً منه).
        # active_online مُضاف بدلاً منه في limits. services[].requires_upgrade
        # من v2.
        "schema_version": 3,
    })


__all__ = ["register"]
