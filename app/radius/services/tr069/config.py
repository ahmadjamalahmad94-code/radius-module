"""إعداد وحدة TR-069 — الأعلام والاتصال بـ GenieACS NBI.

كل شيء يُقرأ من env_settings (env → DB → default) كنمط المشروع. الوحدة معطّلة
افتراضيًّا: تظهر فقط حين `HOBERADIUS_TR069_ENABLED` صادق. GenieACS NBI يُفترَض
محليًّا على 127.0.0.1:7557 (لا يُكشف للإنترنت).
"""
from __future__ import annotations

from ...core import env_settings


def _truthy(v: object) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def tr069_enabled() -> bool:
    """هل الوحدة التجريبيّة مفعّلة على هذه النسخة؟ (علم بيئة/إعداد)."""
    return _truthy(env_settings.env("HOBERADIUS_TR069_ENABLED", ""))


def nbi_base_url() -> str:
    """عنوان GenieACS NBI (الشمالي) — REST. محليّ افتراضيًّا."""
    return (env_settings.env("HOBERADIUS_GENIEACS_NBI_URL", "")
            or "http://127.0.0.1:7557").rstrip("/")


def nbi_timeout() -> float:
    try:
        return float(env_settings.env("HOBERADIUS_GENIEACS_NBI_TIMEOUT", "") or 6.0)
    except (TypeError, ValueError):
        return 6.0


def cwmp_public_url() -> str:
    """عنوان CWMP العامّ الذي يُوضع في الراوتر (خلف Nginx/HTTPS). للعرض في التسجيل."""
    return (env_settings.env("HOBERADIUS_GENIEACS_CWMP_URL", "") or "").strip()


def cwmp_global_username() -> str:
    """اسم مصادقة CWMP العامّ (اختياريّ) — إن فُعِّل على GenieACS، يوضَع في كلّ
    راوتر في مسار «بلا لمس». يُعرَض في صفحة الإعداد (لا كلمة المرور)."""
    return (env_settings.env("HOBERADIUS_GENIEACS_CWMP_USERNAME", "") or "").strip()


def cwmp_global_auth_enabled() -> bool:
    """هل ضُبطت مصادقة CWMP عامّة (اسم مستخدم)؟"""
    return bool(cwmp_global_username())


def enrollment_ttl_hours() -> int:
    try:
        return max(1, int(env_settings.env("HOBERADIUS_TR069_ENROLL_TTL_H", "") or 72))
    except (TypeError, ValueError):
        return 72


def offline_after_minutes(tenant_id: int | None = None) -> int:
    """كم دقيقة بلا Inform قبل اعتبار الراوتر مفصولًا عن ACS. من إعدادات
    المستأجر (tr069.offline_after_minutes) ثم البيئة ثم الافتراضيّ 10.

    نجعله ≥ ضِعف الفاصل الدوريّ المعتاد كي لا نُطلق فصلًا كاذبًا على تأخّر
    Inform واحد."""
    default = env_settings.env("HOBERADIUS_TR069_OFFLINE_MIN", "") or 10
    if tenant_id is not None:
        try:
            from ...db.repos import tenants_repo
            raw = tenants_repo.get_setting(int(tenant_id),
                                           "tr069.offline_after_minutes", "")
            if str(raw or "").strip():
                default = raw
        except Exception:  # noqa: BLE001
            pass
    try:
        return max(2, int(default))
    except (TypeError, ValueError):
        return 10
