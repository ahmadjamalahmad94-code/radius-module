"""«هوت سبوت» أم «برود باند» — مصدرٌ واحدٌ للتصنيف (الجلسات + المشتركون).

للجلسة الحيّة نقرأ ما أرسله الراوتر أوّلًا (radacct):
  * ``Framed-Protocol = PPP`` أو منفذٌ افتراضيّ/خطّيّ ⇒ برود باند (PPPoE) —
    الراوتر قد يُرسل ``NAS-Port-Type = Ethernet`` للاثنين، فالبروتوكول يحسم.
  * منفذٌ لاسلكيّ/إيثرنت بلا PPP ⇒ هوت سبوت.
ثمّ نوعُ خدمة الحساب/باقته (‏``hotspot`` · ``pppoe`` · ``both``)، ثمّ الكرت ⇒
هوت سبوت. وإلّا ``""`` (مجهول) فلا يظهر تحت أيٍّ من الفلترين.
"""
from __future__ import annotations

HOTSPOT = "hotspot"
BROADBAND = "broadband"
BOTH = "both"

_BROADBAND_PORTS = frozenset({
    "virtual", "async", "sync", "isdn", "isdn-sync", "isdn-async-v120",
    "isdn-async-v110", "xdsl", "adsl-cap", "adsl-dmt", "idsl", "sdsl",
    "cable", "pppoa", "pppoeoa", "pppoee", "pppoeovlan", "pppoeoqinq",
})
_HOTSPOT_SERVICES = frozenset({"hotspot"})
_BROADBAND_SERVICES = frozenset({"pppoe", "ppp", "broadband"})


def normalize_access(value: str | None) -> str | None:
    """قيمة فلتر من الطلب ⇒ ``hotspot`` | ``broadband`` | None (الكل)."""
    v = (value or "").strip().lower()
    if v in ("hotspot", "hs"):
        return HOTSPOT
    if v in ("broadband", "pppoe", "ppp", "bb"):
        return BROADBAND
    return None


def service_types_for(access: str) -> tuple[str, ...]:
    """قيمُ ``service_type`` (بأحرفٍ صغيرة) التي تقع تحت الفلتر؛ «both» في الاثنين."""
    if access == HOTSPOT:
        return tuple(sorted(_HOTSPOT_SERVICES | {BOTH}))
    if access == BROADBAND:
        return tuple(sorted(_BROADBAND_SERVICES | {BOTH}))
    return ()


def from_service_type(service_type: str | None) -> str:
    st = (service_type or "").strip().lower()
    if st in _HOTSPOT_SERVICES:
        return HOTSPOT
    if st in _BROADBAND_SERVICES:
        return BROADBAND
    if st == BOTH:
        return BOTH
    return ""


def classify_session(*, nas_port_type: str | None = "", framed_protocol: str | None = "",
                     service_type: str | None = "", user_type: str | None = "") -> str:
    fp = (framed_protocol or "").strip().lower()
    npt = (nas_port_type or "").strip().lower()
    if fp == "ppp" or npt in _BROADBAND_PORTS:
        return BROADBAND
    if npt.startswith("wireless") or npt in ("ethernet", "hotspot"):
        return HOTSPOT
    by_service = from_service_type(service_type)
    if by_service in (HOTSPOT, BROADBAND):
        return by_service
    if (user_type or "").strip().lower() == "card":
        return HOTSPOT
    return ""
