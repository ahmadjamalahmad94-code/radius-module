"""تفعيل/ربط بضغطة واحدة (one-click activate + link).

النموذج: عميل يَفتح صفحة /admin/radius/_license/connect على نسخة جديدة،
يَلصق رابط لوحة المزوّد + مفتاح الترخيص، يَضغط زرّ «ربط وتفعيل الآن»
فيَحدث هذا داخليًّا:

  1. validate(url + license_key) — رفض الواضح غير الصالح.
  2. save_config — يُكتب base_url + license_key + enabled=1 إلى tenant_settings
     (نَلْتزم بنفس مفاتيح AdminBridgeConfig.from_env).
  3. fetch_capacity_contract — أوّل مزامنة فورًا (لقطة capacity).
  4. fetch_license_snapshot — لقطة الترخيص.
  5. Verdict عربي: success أو سبب الفشل (URL/token/شبكة).

الـreset: action معاكس يَحذف اللقطات وإعدادات الجسر فتَرجع الحالة إلى
NEVER_ACTIVATED — للاختبار الكامل لدورة remove → pending → re-activate.

ملاحظات سلامة:
  • لا يُغيّر متغيّرات البيئة (فإن ضُبط HOBERADIUS_ADMIN_BASE_URL في env
    فهو يَتجاوز إعداد DB — نُبلِّغ المستخدم بهذه الحالة بشكل صريح).
  • فشل الـsync لا يَحذف الـconfig المحفوظ — كي يَستطيع المستخدم تعديل
    حقل واحد وإعادة المحاولة بدون كتابة كل شيء من الأوّل.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

_LOG = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# نتيجة موحَّدة (Verdict) للعمليّتين link + reset + sync
# ─────────────────────────────────────────────────────────────────────
@dataclass
class ActivationResult:
    ok: bool
    code: str                # رمز داخلي للتشخيص (validation_url/sync_failed/...)
    message_ar: str          # رسالة عربية للمستخدم
    details: dict            # حقول إضافية للعرض/التشخيص


# ─────────────────────────────────────────────────────────────────────
# مفاتيح إعدادات الجسر (نفس ما يَستهلكه AdminBridgeConfig.from_env)
# ─────────────────────────────────────────────────────────────────────
_SETTING_KEYS = {
    "base_url":              "license_admin_bridge.base_url",
    "license_key":           "license_admin_bridge.license_key",
    "enabled":               "license_admin_bridge.enabled",
    "worker_enabled":        "license_admin_bridge.worker_enabled",
    "runtime_contract_sync": "license_admin_bridge.runtime_contract_sync",
}

# مفاتيح env التي تَتجاوز DB — نَحذّر المستخدم لو كانت مضبوطة وتُغيّر سلوكه.
_ENV_OVERRIDES = (
    "HOBERADIUS_ADMIN_BASE_URL",
    "HOBERADIUS_LICENSE_KEY",
    "INSTANCE_LICENSE_KEY",
    "HOBERADIUS_ADMIN_BRIDGE_ENABLED",
)


# ─────────────────────────────────────────────────────────────────────
# تحقّق المدخلات
# ─────────────────────────────────────────────────────────────────────
def _validate_url(raw: str) -> tuple[bool, str]:
    """يُرجع (صالح، رسالة-خطأ-عربية-إن-وُجد). يَقبل http/https فقط؛ يَرفض
    عناوين IP محلّية بلا host واضح."""
    if not raw or not isinstance(raw, str):
        return False, "أدخل رابط لوحة المزوّد (مثل https://hoberadius.com)."
    s = raw.strip()
    if not s.lower().startswith(("http://", "https://")):
        return False, "الرابط يجب أن يبدأ بـhttps:// (أو http:// لاختبار محلّي)."
    try:
        parsed = urlparse(s)
    except (TypeError, ValueError):
        return False, "صيغة الرابط غير صحيحة."
    if not parsed.netloc:
        return False, "الرابط يفتقد اسم المضيف (host)."
    if " " in s:
        return False, "الرابط لا يجوز أن يحوي مسافات."
    return True, ""


def _validate_license_key(raw: str) -> tuple[bool, str]:
    if not raw or not isinstance(raw, str):
        return False, "أدخل مفتاح الترخيص من صفحة العميل في لوحة المزوّد."
    s = raw.strip()
    if len(s) < 8:
        return False, "مفتاح الترخيص قصير جدًّا — انسخه كاملًا من لوحة المزوّد."
    if len(s) > 256:
        return False, "مفتاح الترخيص طويل بشكل غير معقول — تأكّد أنّك نَسختَ مفتاحًا واحدًا فقط."
    return True, ""


def detect_env_overrides() -> list[str]:
    """يُرجع قائمة env-vars المضبوطة التي تَتجاوز إعدادات DB. القائمة فارغة
    = الإعدادات الحالية تَأتي من DB حصرًا."""
    import os
    out: list[str] = []
    for name in _ENV_OVERRIDES:
        val = os.environ.get(name)
        if val is not None and str(val).strip() != "":
            out.append(name)
    return out


# ─────────────────────────────────────────────────────────────────────
# قراءة الحالة الحالية للعرض في الصفحة
# ─────────────────────────────────────────────────────────────────────
def current_config_view(tenant_id: int) -> dict:
    """قراءة آمنة لإعدادات الجسر الحالية + ما إذا كانت تأتي من env override."""
    try:
        from .admin_panel_client import AdminBridgeConfig, mask_license_key
        cfg = AdminBridgeConfig.from_env()
        return {
            "base_url":           cfg.base_url or "",
            "license_key_masked": mask_license_key(cfg.license_key),
            "has_license_key":    bool(cfg.license_key),
            "enabled":            bool(cfg.enabled),
            "env_overrides":      detect_env_overrides(),
        }
    except Exception:  # noqa: BLE001
        return {"base_url": "", "license_key_masked": "", "has_license_key": False,
                "enabled": False, "env_overrides": []}


# ─────────────────────────────────────────────────────────────────────
# ربط + تفعيل (الإجراء الرئيسي)
# ─────────────────────────────────────────────────────────────────────
def link_and_activate(tenant_id: int, *, base_url: str,
                       license_key: str, by: int = 0) -> ActivationResult:
    """يَنفّذ ربط النسخة بلوحة المزوّد + يَجلب أوّل لقطتَين (license + capacity).

    خطوات:
      1. تحقّق المدخلات (URL + license_key).
      2. حفظ في tenant_settings.
      3. fetch_license_snapshot → بصري لـsystem activation.
      4. fetch_capacity_contract → بصري لـmanage services.
      5. تقييم النجاح الكلّي.

    آمن: لا يَكسر اللوحة عند أيّ خطأ. كل خطوة تُلتقَط بـtry/except وتُحوّل
    لرسالة عربية واضحة. إذا نجح الحفظ لكن فشل التزامن، الإعدادات تَبقى
    محفوظة كي يَستطيع المستخدم الضغط «إعادة المحاولة» دون كتابة من الأوّل.
    """
    # (1) تحقّق
    ok_url, url_err = _validate_url(base_url)
    if not ok_url:
        return ActivationResult(ok=False, code="validation_url",
                                 message_ar=url_err,
                                 details={"field": "base_url"})
    ok_key, key_err = _validate_license_key(license_key)
    if not ok_key:
        return ActivationResult(ok=False, code="validation_license_key",
                                 message_ar=key_err,
                                 details={"field": "license_key"})

    base_url_norm = base_url.strip().rstrip("/")
    license_key_norm = license_key.strip()

    # (2) حفظ
    try:
        from ..db.repos import tenants_repo
        tid = int(tenant_id)
        tenants_repo.set_setting(tid, _SETTING_KEYS["base_url"],
                                  base_url_norm, by=int(by))
        tenants_repo.set_setting(tid, _SETTING_KEYS["license_key"],
                                  license_key_norm, by=int(by))
        tenants_repo.set_setting(tid, _SETTING_KEYS["enabled"], "1", by=int(by))
        tenants_repo.set_setting(tid, _SETTING_KEYS["worker_enabled"], "1",
                                  by=int(by))
        tenants_repo.set_setting(tid, _SETTING_KEYS["runtime_contract_sync"],
                                  "1", by=int(by))
    except Exception:  # noqa: BLE001
        _LOG.exception("activation_connect: failed to persist bridge config")
        return ActivationResult(ok=False, code="save_failed",
                                 message_ar="تعذّر حفظ إعدادات الربط في قاعدة البيانات.",
                                 details={})

    # (3+4) جلب اللقطات
    sync_errors: list[dict] = []
    license_ok = False
    capacity_ok = False
    try:
        from .admin_panel_client import AdminPanelClient
        client = AdminPanelClient()
        # license snapshot أوّلًا — لو فشل هذا غالبًا فشل التحقّق من المفتاح
        lic_res = client.fetch_license_snapshot(tenant_id=int(tenant_id))
        license_ok = bool(lic_res.get("ok"))
        if not license_ok:
            sync_errors.append({"step": "license",
                                 "status": str(lic_res.get("status") or "failed"),
                                 "error": _short_err(lic_res.get("error"))})
        # capacity سواء نجح أو فشل سابقه — قد تَعمل لقطة capacity دون license
        # على بعض المزوّدين.
        cap_res = client.fetch_capacity_contract(tenant_id=int(tenant_id))
        capacity_ok = bool(cap_res.get("ok"))
        if not capacity_ok:
            sync_errors.append({"step": "capacity",
                                 "status": str(cap_res.get("status") or "failed"),
                                 "error": _short_err(cap_res.get("error"))})
    except Exception as exc:  # noqa: BLE001
        _LOG.exception("activation_connect: sync raised")
        return ActivationResult(
            ok=False, code="sync_exception",
            message_ar=("الإعدادات حُفظت لكن تعذّر الاتصال بلوحة المزوّد: "
                        f"{str(exc)[:200]}. تأكّد من الرابط والشبكة وأعد المحاولة."),
            details={"exception": str(exc)[:500]},
        )

    if license_ok or capacity_ok:
        return ActivationResult(
            ok=True, code="activated",
            message_ar=("تمّ الربط وتفعيل النسخة. اللوحة جاهزة للاستخدام."
                         if (license_ok and capacity_ok)
                         else "تمّ الربط جزئيًّا — تَزامُن واحد نَجح. "
                              "اضغط «مزامنة الآن» لإعادة المحاولة على الباقي."),
            details={"license_ok": license_ok, "capacity_ok": capacity_ok},
        )

    # كل التزامنات فشلت — الإعدادات محفوظة للمحاولة التالية
    return ActivationResult(
        ok=False, code="sync_failed",
        message_ar=("الإعدادات حُفظت لكن لوحة المزوّد لم تَستجب. "
                     "راجع الرابط ومفتاح الترخيص ثم اضغط «إعادة المحاولة»."),
        details={"errors": sync_errors},
    )


# ─────────────────────────────────────────────────────────────────────
# مزامنة الآن (إعادة محاولة بعد ربط محفوظ)
# ─────────────────────────────────────────────────────────────────────
def sync_now(tenant_id: int) -> ActivationResult:
    """يُجبر تزامن license + capacity على الفور بدون لمس الإعدادات.
    يُستعمل بعد link فاشل، أو لرفش الحالة من زرّ «مزامنة الآن»."""
    try:
        from .admin_panel_client import AdminPanelClient
        client = AdminPanelClient()
        lic = client.fetch_license_snapshot(tenant_id=int(tenant_id))
        cap = client.fetch_capacity_contract(tenant_id=int(tenant_id))
    except Exception as exc:  # noqa: BLE001
        return ActivationResult(ok=False, code="sync_exception",
                                 message_ar=("تعذّر الاتصال بلوحة المزوّد: "
                                              f"{str(exc)[:200]}."),
                                 details={"exception": str(exc)[:500]})
    license_ok = bool(lic.get("ok"))
    capacity_ok = bool(cap.get("ok"))
    if license_ok or capacity_ok:
        return ActivationResult(
            ok=True, code="synced",
            message_ar=("تمّت المزامنة بنجاح."
                         if (license_ok and capacity_ok)
                         else "نَجحت مزامنة واحدة — أعد المحاولة لاستكمال الثانية."),
            details={"license_ok": license_ok, "capacity_ok": capacity_ok},
        )
    return ActivationResult(ok=False, code="sync_failed",
                             message_ar="لوحة المزوّد لم تَستجب. تأكّد من الرابط والمفتاح والشبكة.",
                             details={"license": _short_err(lic.get("error")),
                                       "capacity": _short_err(cap.get("error"))})


# ─────────────────────────────────────────────────────────────────────
# إعادة التعيين (فكّ الربط) — للاختبار الكامل لدورة remove → pending → re-activate
# ─────────────────────────────────────────────────────────────────────
def reset_link(tenant_id: int, *, by: int = 0,
                wipe_snapshots: bool = True) -> ActivationResult:
    """يَفكّ الربط بالكامل:
       • يَمسح إعدادات الجسر من tenant_settings (base_url/license_key/enabled).
       • يَحذف لقطات license + capacity من DB (لو wipe_snapshots=True — افتراضي).

       النتيجة: lifecycle.evaluate يَرجع NEVER_ACTIVATED → صفحة activate
       تَظهر للجميع. هذا ما يَحتاجه المالك لاختبار دورة remove→pending→
       re-activate live."""
    tid = int(tenant_id)
    cleared_settings: list[str] = []
    try:
        from ..db.repos import tenants_repo
        for key in (_SETTING_KEYS["base_url"], _SETTING_KEYS["license_key"],
                     _SETTING_KEYS["enabled"], _SETTING_KEYS["worker_enabled"],
                     _SETTING_KEYS["runtime_contract_sync"]):
            old = tenants_repo.get_setting(tid, key, "")
            if old:
                tenants_repo.set_setting(tid, key, "", by=int(by))
                cleared_settings.append(key)
    except Exception:  # noqa: BLE001
        _LOG.exception("activation_connect.reset: settings wipe failed")
        return ActivationResult(ok=False, code="reset_settings_failed",
                                 message_ar="تعذّر مسح إعدادات الربط من قاعدة البيانات.",
                                 details={})

    deleted_snapshots = 0
    if wipe_snapshots:
        try:
            from ..db.connection import db
            cur = db().execute(
                "DELETE FROM license_admin_bridge_snapshots WHERE tenant_id = ?",
                (tid,),
            )
            deleted_snapshots = int(cur.rowcount or 0)
        except Exception:  # noqa: BLE001
            _LOG.exception("activation_connect.reset: snapshot wipe failed")
            return ActivationResult(ok=False, code="reset_snapshots_failed",
                                     message_ar="مُسحت الإعدادات لكن تعذّر حذف اللقطات. أعد المحاولة.",
                                     details={"cleared_settings": cleared_settings})

    # نَحذف cache lifecycle/grant على الـrequest الحالي (الـg memoization)
    try:
        from flask import g, has_request_context
        if has_request_context():
            for attr in ("_license_lifecycle_cache", "_provider_grant_cache"):
                if hasattr(g, attr):
                    delattr(g, attr)
    except Exception:  # noqa: BLE001
        pass

    return ActivationResult(
        ok=True, code="reset_done",
        message_ar=(f"تم فكّ الربط. مُسحت {len(cleared_settings)} إعدادًا "
                     f"و{deleted_snapshots} لقطة. النسخة الآن في حالة "
                     "«بانتظار التفعيل» — تَستطيع البدء من جديد."),
        details={"cleared_settings": cleared_settings,
                  "deleted_snapshots": deleted_snapshots},
    )


# ─────────────────────────────────────────────────────────────────────
# حالة التفعيل (للعرض في الصفحة)
# ─────────────────────────────────────────────────────────────────────
def activation_state(tenant_id: int) -> dict:
    """ملخّص الحالة الفعلية الآن — لعرضها على الكلاينت في صفحة connect.

    Returns:
        {
          "phase":       "pending|linked_syncing|active|expired|error|sync_outage",
          "phase_ar":    "بانتظار التفعيل|...",
          "license_state": "<value of LifecycleState>",
          "blocks_panel": bool,
          "has_config":  bool,
          "has_snapshot": bool,
          "expires_at":  ISO|null,
          "fetched_at":  ISO|null,
          "env_overrides": [...],
        }
    """
    try:
        from .license_lifecycle import evaluate_cached
        from . import provider_grant
        decision = evaluate_cached(int(tenant_id))
        has_snap = provider_grant.has_snapshot(int(tenant_id))
        cfg = current_config_view(int(tenant_id))
        has_config = bool(cfg.get("has_license_key") and cfg.get("base_url"))

        # الـphase مُشتقّة من حالة الـlifecycle:
        phase = "pending"
        phase_ar = "بانتظار التفعيل"
        if decision.state.value == "active":
            phase = "active"
            phase_ar = "مفعّل ✓"
        elif decision.state.value == "expired":
            phase = "expired"
            phase_ar = "منتهي — يحتاج تجديد"
        elif decision.state.value in ("sync_outage_in_grace",
                                        "sync_outage_beyond_grace"):
            phase = "sync_outage"
            phase_ar = ("انقطاع تزامن مؤقّت (ضمن سماحية)"
                         if decision.state.value == "sync_outage_in_grace"
                         else "انقطاع تزامن طويل — مقفل")
        elif has_config and not has_snap:
            phase = "linked_syncing"
            phase_ar = "تمّ الربط، بانتظار أوّل تزامن…"
        return {
            "phase":         phase,
            "phase_ar":      phase_ar,
            "license_state": decision.state.value,
            "license_reason": decision.reason,
            "blocks_panel":  decision.blocks_panel,
            "has_config":    has_config,
            "has_snapshot":  has_snap,
            "expires_at":    decision.expires_at,
            "fetched_at":    decision.fetched_at,
            "env_overrides": cfg.get("env_overrides") or [],
            "config":        cfg,
        }
    except Exception:  # noqa: BLE001
        _LOG.exception("activation_state failed")
        return {"phase": "pending", "phase_ar": "بانتظار التفعيل",
                "license_state": "never_activated", "license_reason": "error",
                "blocks_panel": True, "has_config": False, "has_snapshot": False,
                "expires_at": None, "fetched_at": None, "env_overrides": [],
                "config": {}}


# ─────────────────────────────────────────────────────────────────────
# مساعد عرض أخطاء الـsync (المُختصرة)
# ─────────────────────────────────────────────────────────────────────
def _short_err(err: object) -> str:
    if not err:
        return ""
    if isinstance(err, dict):
        return str(err.get("code") or err.get("message") or "")[:200]
    return str(err)[:200]


__all__ = [
    "ActivationResult",
    "link_and_activate", "sync_now", "reset_link",
    "activation_state", "current_config_view", "detect_env_overrides",
]
