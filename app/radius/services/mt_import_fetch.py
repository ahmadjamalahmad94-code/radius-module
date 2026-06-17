"""mt_import_fetch — طبقة جلب حسابات المستخدمين من راوتر MikroTik للاستيراد.

الجزء الثاني من ميزة «استيراد المشتركين من المايكروتيك». مهمّتها الوحيدة:
الاتصال براوتر موجود في `nas_devices` وقراءة سجلّات المستخدمين الخام منه —
لا تحويل ولا كتابة لقاعدة البيانات (ذاك دور الزيادات التالية).

مصدران حسب نوع الاستيراد:

  • هوتسبوت     →  `/ip/hotspot/user`   (مستخدمو صفحة الدخول)
  • نطاق عريض   →  `/ppp/secret`        (حسابات PPPoE/PPTP/L2TP)

نقلان، يُفضَّل REST ثم يسقط إلى API الثنائي:

  • **REST** (RouterOS v7): ‏`GET https://<host>/rest/<path>` باعتماد Basic.
    أسرع وأنظف (JSON مباشر) لكنه يتطلّب خدمة www/www-ssl مفعّلة وراوتر v7.
  • **API الثنائي** (8728/8729): يُعاد استخدام عميل الإدارة الموجود
    (`mikrotik_admin_client._safe_dial`) — يعمل على v6 وv7.

اختيار النقل من عمود `nas_devices.api_type`:
    auto → جرّب REST ثم API   |   rest → REST فقط   |   api → API فقط

أمان: نُعيد استخدام اعتماد الراوتر المخزَّن خادمِيًّا (api_user/api_password)
ولا نطبعها أبدًا في السجلّ. سجلّات المستخدمين قد تحوي حقل `password` — تبقى
في الذاكرة خادمِيًّا ولا تُسجَّل قطّ.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from .nas_connection import resolve_connection_address

_LOG = logging.getLogger(__name__)

# ── أنواع الاستيراد ──────────────────────────────────────────────────
IMPORT_HOTSPOT = "hotspot"
IMPORT_BROADBAND = "broadband"
IMPORT_TYPES = (IMPORT_HOTSPOT, IMPORT_BROADBAND)

# مسار REST (بلا بادئة /rest) ومسار print للـAPI الثنائي، لكل نوع.
_REST_PATH = {
    IMPORT_HOTSPOT: "ip/hotspot/user",
    IMPORT_BROADBAND: "ppp/secret",
}
_API_PRINT_PATH = {
    IMPORT_HOTSPOT: "/ip/hotspot/user/print",
    IMPORT_BROADBAND: "/ppp/secret/print",
}

# نقل صالح في عمود api_type.
TRANSPORT_AUTO = "auto"
TRANSPORT_REST = "rest"
TRANSPORT_API = "api"

# مهلة REST الافتراضية (ث) — قصيرة كي لا تتجمّد الواجهة على راوتر بطيء.
_REST_TIMEOUT_SEC = 10


@dataclass
class FetchResult:
    """ما تُعيده :func:`fetch_users` — لا ترفع استثناءً للراوت أبدًا."""
    ok: bool = False
    import_type: str = ""
    transport: str = ""            # النقل الذي نجح فعلًا (rest|api|"")
    records: list[dict] = field(default_factory=list)
    error: str = ""
    attempted: list[str] = field(default_factory=list)  # النقل المُجرَّب بالترتيب

    @property
    def count(self) -> int:
        return len(self.records)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "import_type": self.import_type,
            "transport": self.transport,
            "count": self.count,
            "error": self.error,
            "attempted": list(self.attempted),
        }


def _norm_import_type(import_type: str) -> str:
    t = str(import_type or "").strip().lower()
    if t in IMPORT_TYPES:
        return t
    # مرادفات شائعة من الواجهة.
    if t in ("ppp", "pppoe", "secret", "broadband"):
        return IMPORT_BROADBAND
    if t in ("hotspot", "hs"):
        return IMPORT_HOTSPOT
    raise ValueError(f"نوع استيراد غير مدعوم: {import_type!r}")


def _resolve_transport(nas: Mapping[str, Any], override: str) -> str:
    raw = str(override or nas.get("api_type") or TRANSPORT_AUTO).strip().lower()
    return raw if raw in (TRANSPORT_AUTO, TRANSPORT_REST, TRANSPORT_API) else TRANSPORT_AUTO


# ── نقل REST ─────────────────────────────────────────────────────────

def _rest_base(nas: Mapping[str, Any]) -> tuple[str, bool]:
    """يبني (base_url, verify_tls) لخدمة REST على الراوتر.

    المخطّط من `api_use_tls`: https إن مفعّل وإلا http. المنفذ من
    `rest_port` إن حُدّد، وإلا الافتراضي القياسي (443/80) — خدمة www/www-ssl
    لا منفذ API الثنائي. شهادات الراوتر موقّعة ذاتيًّا عادةً فالتحقّق مُعطَّل
    افتراضيًّا (يُفعَّل عبر `api_verify_tls`)."""
    host = resolve_connection_address(nas)
    use_tls = bool(nas.get("api_use_tls") or 0)
    scheme = "https" if use_tls else "http"
    default_port = 443 if use_tls else 80
    port = int(nas.get("rest_port") or default_port)
    verify = bool(nas.get("api_verify_tls") or 0)
    return f"{scheme}://{host}:{port}", verify


def _http_get_json(url: str, *, user: str, password: str, verify: bool,
                   timeout: int) -> list[dict]:
    """يجلب JSON من نقطة REST عبر Basic. معزول كي تستبدله الاختبارات.

    يُعيد قائمة قواميس (REST يرجع مصفوفة سجلّات). أي خطأ يُرفَع للمُستدعي
    الذي يحوّله إلى نصّ خطأ في FetchResult. لا نطبع كلمة المرور أبدًا."""
    import requests

    resp = requests.get(
        url, auth=(user, password), verify=verify, timeout=timeout,
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):       # بعض الإصدارات تلفّ بمفتاح مفرد
        data = data.get("data") or data.get("ret") or []
    if not isinstance(data, list):
        return []
    return [dict(r) for r in data if isinstance(r, (dict, Mapping))]


def _fetch_rest(nas: Mapping[str, Any], import_type: str) -> FetchResult:
    res = FetchResult(import_type=import_type, attempted=[TRANSPORT_REST])
    host = resolve_connection_address(nas)
    if not host:
        res.error = "عنوان الراوتر غير محدد"
        return res
    base, verify = _rest_base(nas)
    path = _REST_PATH[import_type]
    url = f"{base}/rest/{path}"
    user = str(nas.get("api_user") or "")
    password = str(nas.get("api_password") or "")
    try:
        rows = _http_get_json(url, user=user, password=password,
                              verify=verify, timeout=_REST_TIMEOUT_SEC)
    except Exception as exc:  # noqa: BLE001 — نُطبِّع كل خطأ نقل لنصّ
        # لا نُدرج كلمة المرور أبدًا — نسجّل المضيف/المسار فقط.
        _LOG.info("MT import REST fetch failed host=%s path=%s — %s",
                  host, path, exc.__class__.__name__)
        res.error = f"تعذّر الجلب عبر REST: {exc}"
        return res
    res.ok = True
    res.transport = TRANSPORT_REST
    res.records = [_normalize_record(r) for r in rows]
    return res


# ── نقل API الثنائي ──────────────────────────────────────────────────

def _fetch_api(nas: Mapping[str, Any], import_type: str) -> FetchResult:
    res = FetchResult(import_type=import_type, attempted=[TRANSPORT_API])
    from . import mikrotik_admin_client as mac

    print_path = _API_PRINT_PATH[import_type]
    mt = mac._safe_dial(
        nas=nas,
        operation=f"import/fetch:{import_type}",
        work=lambda c: list(c.print_(print_path)),
    )
    if not mt.ok:
        res.error = mt.error or "تعذّر الجلب عبر API"
        return res
    rows = mt.data if isinstance(mt.data, list) else []
    res.ok = True
    res.transport = TRANSPORT_API
    res.records = [_normalize_record(r) for r in rows]
    return res


# ── تطبيع السجلّ ─────────────────────────────────────────────────────

def _normalize_record(row: Any) -> dict:
    """يطبّع سجلّ راوتر (REST أو API) إلى قاموس بمفاتيح موحّدة.

    REST يرجع مفاتيح بـkebab-case (`.id`, `name`, `profile`, `limit-bytes-total`)
    والـAPI كذلك تقريبًا — نُبقي كل الحقول كما هي ونضيف `_id` كمعرّف داخلي
    موحّد كي لا تتسرّب نقطة `.id` الغريبة إلى المستهلكين."""
    if not isinstance(row, (dict, Mapping)):
        return {}
    out = {str(k): v for k, v in dict(row).items()}
    rid = out.get(".id") or out.get("id") or ""
    out["_id"] = str(rid)
    # توحيد علم التعطيل: RouterOS يرجع "true"/"false" نصًّا.
    if "disabled" in out:
        out["_disabled"] = str(out.get("disabled")).strip().lower() in ("true", "yes", "1")
    else:
        out["_disabled"] = False
    return out


# ── الواجهة العامة ───────────────────────────────────────────────────

def fetch_users(nas: Mapping[str, Any], import_type: str, *,
                transport: str = "") -> FetchResult:
    """يجلب سجلّات المستخدمين من الراوتر حسب النوع والنقل المطلوبين.

    `transport` يتجاوز `nas.api_type` إن مُرِّر. سلوك auto: REST أولًا، فإن
    فشل يسقط إلى API الثنائي ويُجمّع المُجرَّب في `attempted`. لا يرفع أبدًا."""
    try:
        itype = _norm_import_type(import_type)
    except ValueError as exc:
        return FetchResult(import_type=str(import_type or ""), error=str(exc))

    mode = _resolve_transport(nas, transport)

    if mode == TRANSPORT_REST:
        return _fetch_rest(nas, itype)
    if mode == TRANSPORT_API:
        return _fetch_api(nas, itype)

    # auto: REST ثم API
    rest = _fetch_rest(nas, itype)
    if rest.ok:
        return rest
    api = _fetch_api(nas, itype)
    api.attempted = [TRANSPORT_REST, TRANSPORT_API]
    if not api.ok and not api.error:
        api.error = rest.error
    return api


__all__ = [
    "IMPORT_HOTSPOT", "IMPORT_BROADBAND", "IMPORT_TYPES",
    "TRANSPORT_AUTO", "TRANSPORT_REST", "TRANSPORT_API",
    "FetchResult", "fetch_users",
]
