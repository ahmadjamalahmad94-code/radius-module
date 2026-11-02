"""البطاقاتُ الإلكترونيّة وسوقُ البطاقات — مخفيّةٌ إلّا لزبونٍ فُعِّلت له.

قرار المالك 2026-11-02: «كل اشي بخص البطاقات الالكترونية وسوق البطاقات خليه
مخفي تماما لحد ما يتم تفعيله بشكل مخصص لكل زبون»، و«هيكون بس عنا حزم مطبوعة
فقط ديفولت ما في خيار الكتروني».

المفتاحُ خدمةُ «سوق البطاقات» (``card_marketplace``) في عقد لوحة التراخيص:
تُفعَّل لكلّ زبونٍ من صفحة خدماته هناك، وتصل السيرفرَ مع المزامنة كلَّ خمس
دقائق — نفسُ طريقة «النسخ الاحتياطي السحابي» (routes/backups.py). غيابُ العقد
أو الخدمة أو أيُّ خطأٍ ⇒ مخفيّ: الافتراضُ مغلق.

ما يُخفى حين تكون مطفأة:
  · القائمةُ الجانبيّة: قسمُ «البطاقات الإلكترونيّة» كلُّه وتقريراه.
  · نموذجُ إنشاء الحزمة: خيارُ «إلكترونيّة» — الحزمُ مطبوعةٌ فقط، والخادمُ
    يُعيد أيَّ «electronic» مُرسَلٍ إلى «printed» (routes/cards.py).
  · لوحةُ التحكّم: بطاقةُ «البطاقات الإلكترونيّة»، والصفحةُ العامّة: مدخلُ «السوق».
  · المساراتُ نفسُها (لوحة الإدارة · API · بوّابة المشتري): 404، لا مجرّد زرٍّ
    مخفيّ يبقى رابطُه يعمل لمن يعرفه.
"""
from __future__ import annotations

import os

from flask import abort, g, jsonify, request

from app.i18n_text import _tr

SERVICE_KEY = "card_marketplace"

# عائلاتُ المسارات حسب اسم نقطة النهاية — البادئةُ تلتقط الأخوات كلّها
# (card_users_list · card_user_360 · card_user_recharge …) فلا يفلت مسارٌ يُضاف لاحقًا.
_GATED_PREFIXES = (
    "radius.card_marketplace",
    "radius.card_user",
    "radius.cards_recharge",
    "radius.store_support",
    "api.v1.card_marketplace",
    "api.v1.card_user",
    "api.v1.cards_recharge",
    "api.v1.store_",
    "portal.card_",
)
_GATED_EXACT = frozenset({
    "radius.rep_card_store_events",
    "radius.rep_login_states_card_store",
    "radius.docs_card_marketplace",
    "radius.docs_card_users",
    "radius.docs_store_support",
})


def is_gated_endpoint(endpoint: str | None) -> bool:
    if not endpoint:
        return False
    return endpoint in _GATED_EXACT or endpoint.startswith(_GATED_PREFIXES)


def _service_from_snapshot(tenant_id: int) -> dict | None:
    from .admin_panel_client import SNAPSHOT_CAPACITY, LicenseAdminSnapshotStore
    snap = LicenseAdminSnapshotStore().latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY)
    if not snap or not isinstance(snap.get("payload_json"), dict):
        return None
    pj = snap["payload_json"]
    services = (pj.get("contract") or {}).get("services") or pj.get("services") or {}
    return services.get(SERVICE_KEY) if isinstance(services, dict) else None


def ecards_enabled(tenant_id: int | None = None) -> bool:
    """هل فُعِّلت البطاقاتُ الإلكترونيّة وسوقُ البطاقات لهذا الزبون؟

    تُحفظ في ``g`` لكلّ طلب: القائمةُ والصفحةُ والحارسُ يسألون معًا. الترخيصُ
    للسيرفر لا للواجهة، فواجهةٌ بلا لقطةٍ خاصّة ترث لقطةَ الواجهة الأولى.
    """
    # تجاوزٌ صريحٌ للاختبار والتطوير المحلّيّ: "1" يفتح و"0" يُغلق؛ غيابُه ⇒ العقد.
    # متغيّرُ بيئةٍ لا علمٌ في الوحدة كي يصمد أمام اختباراتٍ تُعيد استيراد ‎app.*‎.
    forced = (os.environ.get("HOBERADIUS_ECARDS_FORCE") or "").strip()
    if forced in ("0", "1"):
        return forced == "1"
    from ..core.tenant import DEFAULT_TENANT_ID
    tid = int(tenant_id if tenant_id is not None else getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    cache = g.setdefault("_hr_ecards_enabled", {})
    if tid in cache:
        return cache[tid]
    enabled = False
    try:
        svc = _service_from_snapshot(tid)
        if svc is None and tid != DEFAULT_TENANT_ID:
            svc = _service_from_snapshot(DEFAULT_TENANT_ID)
        if isinstance(svc, dict):
            # «مخفي» في لوحة التراخيص يُخفي أيضًا — ولو بقيت الخدمة مفعّلة بدرجةٍ مجانيّة.
            enabled = (svc.get("enabled") is True
                       and str(svc.get("status") or "").strip().lower() == "active"
                       and not svc.get("hidden"))
    except Exception:  # noqa: BLE001 — قراءةُ العقد لا تكسر صفحة؛ الخطأ ⇒ مخفيّ
        enabled = False
    cache[tid] = enabled
    return enabled


def install(app) -> None:
    app.jinja_env.globals["ecards_enabled"] = ecards_enabled

    @app.before_request
    def _ecards_gate():
        if not is_gated_endpoint(request.endpoint) or ecards_enabled():
            return None
        if request.endpoint.startswith("api.") or request.is_json:
            return jsonify({
                "ok": False,
                "error": "service_not_enabled",
                "message": _tr("هذه الخدمة غير مفعلة لهذا العميل من لوحة التراخيص."),
            }), 404
        abort(404)
