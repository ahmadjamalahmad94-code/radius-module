"""كاشفات نمط القيَم — تستنتج «نوع» العمود من محتواه لا من ترويسته.

مكمِّل لمرادفات الترويسة: عمود بلا ترويسة مفهومة يُصنَّف بمعاينة قيَمه
(هاتف؟ MAC؟ سرعة «X Mbps»؟ حجم «X GB»؟ تاريخ؟ مال؟ اسم مستخدم؟). يُستعمل
لتعزيز/إكمال الكشف عندما تفشل الترويسة — أساس «الشمول» (schema-agnostic).

``column_profile(values)`` يُعيد نسبة تطابق كل نوع (0..1). دوال خالصة.
"""
from __future__ import annotations

import re
from typing import Iterable

from . import valueparse as vp

# أنواع القيَم المعروفة.
T_MAC = "mac"
T_IP = "ip"
T_PHONE = "phone"
T_EMAIL = "email"
T_SPEED = "speed"
T_DATASIZE = "datasize"
T_DATE = "date"
T_MONEY = "money"
T_INT = "int"
T_USERNAME = "username"
T_NAME = "name"
T_BOOL = "bool"

_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}[:\-]){5}[0-9a-fA-F]{2}$")
_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
_PHONE_RE = re.compile(r"^\+?\d[\d\s\-]{6,16}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INT_RE = re.compile(r"^[-+]?\d{1,12}$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._\-!@#]{2,32}$")
_UNIT_SPEED = re.compile(r"\b(mbps|kbps|gbps|bps|mbit|kbit|m/s)\b|ميجابت|كيلوبت|جيجابت", re.I)
_UNIT_SIZE = re.compile(r"\b(t|g|m|k)?b\b|بايت|ميجابايت|جيجابايت|كيلوبايت|تيرابايت", re.I)
_HAS_LETTER = re.compile(r"[A-Za-z؀-ۿ]")


def _classify_value(v: str) -> set[str]:
    """أنواع محتملة لقيمة واحدة (قد تنتمي لأكثر من نوع)."""
    s = str(v or "").strip()
    if not s or s in ("--", "-", "n/a"):
        return set()
    out: set[str] = set()
    if _MAC_RE.match(s):
        out.add(T_MAC)
    if _IP_RE.match(s):
        out.add(T_IP)
    if _EMAIL_RE.match(s):
        out.add(T_EMAIL)
    if _PHONE_RE.match(s) and 7 <= len(re.sub(r"\D", "", s)) <= 15:
        out.add(T_PHONE)
    if vp.parse_bool(s).ok and len(s) <= 8:
        out.add(T_BOOL)
    # سرعة/حجم: تتطلّب وحدة صريحة لتفادي التقاط الأرقام المجرّدة.
    if _UNIT_SPEED.search(s) or vp._is_unlimited(s):
        if vp.parse_speed(s).ok:
            out.add(T_SPEED)
    if _UNIT_SIZE.search(s) and re.search(r"\d", s):
        if vp.parse_data_size(s).ok:
            out.add(T_DATASIZE)
    if vp.parse_date(s).ok:
        out.add(T_DATE)
        out.discard(T_PHONE)     # تاريخ أخصّ من «هاتف» بشُرَط/أرقام.
    if _INT_RE.match(s):
        out.add(T_INT)
    # مال: رقم عشريّ (نقطة) — أو عملة صريحة.
    if re.search(r"\d\.\d{1,2}$", s) or re.search(r"[$€£₪]|ils|usd|eur", s, re.I):
        if vp.parse_money(s).ok:
            out.add(T_MONEY)
    if _USERNAME_RE.match(s):
        out.add(T_USERNAME)
    if _HAS_LETTER.search(s) and " " in s.strip():
        out.add(T_NAME)
    return out


def column_profile(values: Iterable[str], *, sample: int = 60) -> dict[str, float]:
    """نسبة القيَم غير الفارغة المطابقة لكل نوع (0..1)."""
    vals = [str(v).strip() for v in values]
    vals = [v for v in vals if v and v not in ("--", "-", "n/a")]
    vals = vals[:sample]
    if not vals:
        return {}
    counts: dict[str, int] = {}
    for v in vals:
        for t in _classify_value(v):
            counts[t] = counts.get(t, 0) + 1
    n = len(vals)
    prof = {t: round(c / n, 3) for t, c in counts.items()}
    # تمييزات إضافيّة على مستوى العمود.
    uniq = len(set(vals))
    if uniq >= n * 0.9 and prof.get(T_USERNAME, 0) >= 0.6:
        prof["_unique_usernameish"] = round(uniq / n, 3)
    return prof


def dominant_type(values: Iterable[str], *, threshold: float = 0.6) -> str:
    """النوع المميِّز الغالب على العمود، أو '' عند غيابه. يقتصر عمدًا على
    الأنواع المميِّزة (mac/ip/email/speed/datasize/date/phone/money) — لا
    يُرجع username/name/int العامّة (لأنها تُطابق كثيرًا فتُضلِّل الكشف)."""
    prof = column_profile(values)
    if not prof:
        return ""
    priority = (T_MAC, T_EMAIL, T_IP, T_SPEED, T_DATASIZE, T_DATE, T_PHONE, T_MONEY)
    for t in priority:
        if prof.get(t, 0) >= threshold:
            return t
    return ""


__all__ = [
    "column_profile", "dominant_type",
    "T_MAC", "T_IP", "T_PHONE", "T_EMAIL", "T_SPEED", "T_DATASIZE",
    "T_DATE", "T_MONEY", "T_INT", "T_USERNAME", "T_NAME", "T_BOOL",
]
