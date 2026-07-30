"""MT110 — حارسٌ واحد يمنع تجاوز الهوت سبوت لأكثر من مضيفٍ واحد.

خلفيّته حادثةٌ حقيقيّة: «تتبّع الأجهزة» كان يكتب على الراوتر

    /ip/hotspot/ip-binding/add address=192.168.20.0/24 type=bypassed

والهوت سبوت يعمل **على المدخل**، وذلك المدخل يحمل شبكة الزبائن وشبكة
الإدارة معًا. فأيّ زبونٍ يضبط لنفسه عنوانًا ثابتًا داخل النطاق يتجاوز
البوابة كلَّها: إنترنت بلا تسجيل دخول ولا محاسبة. ولا يُبلّغ عن هذا أحد —
يُكتشف حين يتوقّف الناس عن الشراء.

أُصلح المُنادي، لكنّ إصلاح المُنادي وحده يُصلح الحادثة لا الصنف: أيّ مسارٍ
جديد يكتب ربطًا سيقع في الحفرة نفسها. لذا الحارس هنا، **عند الكتابة**،
يُنادى من كلّ من يُنشئ ربط تجاوز.

القاعدة: عنوان التجاوز مضيفٌ **واحد** — عنوانٌ مجرّد، أو ‎/32 (IPv4) أو
‎/128 (IPv6). لا نطاقات ولا مدَيات (``a-b``). والرفض صريحٌ بالعربيّة كي
يفهم المشغّل سببه بدل أن يظنّه عطبًا عابرًا.
"""

from __future__ import annotations

import ipaddress


class HotspotBypassScopeError(ValueError):
    """محاولة تجاوزٍ تشمل أكثر من مضيفٍ واحد."""


_FULL_PREFIX = {4: 32, 6: 128}


def ensure_single_host(address: str, *, field: str = "address") -> str:
    """يُعيد العنوان كما هو إن كان مضيفًا واحدًا، وإلّا يرفع الاستثناء.

    يقبل: ``192.168.3.15`` و``192.168.3.15/32`` و``2001:db8::1/128``.
    يرفض: ``192.168.3.0/24`` و``192.168.0.0/16`` و``10.0.0.1-10.0.0.9``.
    """
    raw = str(address or "").strip()
    if not raw:
        raise HotspotBypassScopeError(
            f"عنوان التجاوز فارغ ({field}) — يجب تحديد عنوان الجهاز.")

    if "-" in raw:
        raise HotspotBypassScopeError(
            f"عنوان التجاوز مدًى ({raw}) — التجاوز لجهازٍ واحد لا لمدًى، "
            "وإلّا مرّ كلّ من في المدى بلا تسجيل دخول.")

    if "/" in raw:
        try:
            net = ipaddress.ip_network(raw, strict=False)
        except ValueError as exc:
            raise HotspotBypassScopeError(
                f"عنوان تجاوزٍ غير صالح ({raw}): {exc}") from exc
        if net.prefixlen != _FULL_PREFIX[net.version]:
            raise HotspotBypassScopeError(
                f"عنوان التجاوز شبكة كاملة ({raw}) — هذا يفتح الإنترنت بلا "
                "تسجيل دخول لكلّ من يضبط لنفسه عنوانًا ثابتًا داخلها. "
                f"استخدم عنوان الجهاز وحده (مثال: {net.network_address}).")
        return raw

    try:
        ipaddress.ip_address(raw)
    except ValueError as exc:
        raise HotspotBypassScopeError(
            f"عنوان تجاوزٍ غير صالح ({raw}): {exc}") from exc
    return raw


def is_single_host(address: str) -> bool:
    """صيغةٌ لا ترفع — للفحص والعرض (تمييز الربوط الواسعة في الواجهة)."""
    try:
        ensure_single_host(address)
        return True
    except HotspotBypassScopeError:
        return False
