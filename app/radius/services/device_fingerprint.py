"""Device fingerprinting from RADIUS accounting data.

R13.A.6 — turn the raw bytes we get from MikroTik into a human-friendly
picture: which kind of device is this card connecting from?

What we have to work with:

  • `Calling-Station-Id` (MAC address) — first 3 octets are the OUI
    that identifies the hardware vendor (per IEEE registry). The local
    bit in the first octet tells us whether the MAC is randomized.
  • `NAS-Port-Type` — usually "Wireless-802.11" or "Ethernet" — tells
    us the connection medium.
  • `Connect-Info` — vendor-specific string MT sometimes includes
    (e.g. "android wifi", "windows 10").
  • Vendors of common WiFi chipsets vs PC NICs vs gaming consoles can
    be mapped to device classes with high confidence.

This module is intentionally offline: a curated OUI table covering the
most common consumer devices we see in Arab-market hotspots. We don't
ship the 30k-entry IEEE registry — it would be 1+ MB and the long tail
isn't useful here. Unknown OUIs cleanly degrade to "غير معروف".

Public API:
    infer_device(mac, nas_port_type=None, connect_info=None) -> dict

Returned dict (stable contract — used by templates and tests):
    {
        "mac":         "AA:BB:CC:DD:EE:FF",   # input echoed, normalized
        "vendor":      "Apple",               # OUI lookup, or "Randomized"/"Unknown"
        "category":    "phone-ios",           # phone-ios|phone-android|laptop|
                                              # desktop|tablet|console|iot|router|unknown
        "label":       "iPhone أو iPad",      # human-readable Arabic
        "icon":        "mobile-screen",       # Font Awesome 6 name (without fa- prefix)
        "connection":  "Wi-Fi 802.11",        # decoded NAS-Port-Type
        "confidence":  "high",                # high|medium|low — for UI hint
        "is_random_mac": False,               # apple/android privacy random MAC
    }
"""
from __future__ import annotations

from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# OUI table — first 3 octets (uppercase, colon-separated) → vendor name.
#
# Curated for Arab-market consumer devices. Sources: IEEE OUI registry +
# Wireshark manuf db (most-common 200 entries trimmed by hand).
# Not exhaustive on purpose — unknown OUIs cleanly fall through.
# ─────────────────────────────────────────────────────────────────────────────
_OUI_DB: dict[str, str] = {
    # ─── Apple ───
    "00:03:93": "Apple", "00:0A:27": "Apple", "00:0A:95": "Apple",
    "00:0D:93": "Apple", "00:0E:35": "Apple", "00:10:fa": "Apple",
    "00:11:24": "Apple", "00:14:51": "Apple", "00:16:CB": "Apple",
    "00:17:F2": "Apple", "00:19:E3": "Apple", "00:1B:63": "Apple",
    "00:1C:B3": "Apple", "00:1D:4F": "Apple", "00:1E:52": "Apple",
    "00:1E:C2": "Apple", "00:1F:5B": "Apple", "00:1F:F3": "Apple",
    "00:21:E9": "Apple", "00:22:41": "Apple", "00:23:12": "Apple",
    "00:23:32": "Apple", "00:23:6C": "Apple", "00:23:DF": "Apple",
    "00:24:36": "Apple", "00:25:00": "Apple", "00:25:4B": "Apple",
    "00:25:BC": "Apple", "00:26:08": "Apple", "00:26:4A": "Apple",
    "00:26:B0": "Apple", "00:26:BB": "Apple", "00:50:E4": "Apple",
    "04:0C:CE": "Apple", "04:15:52": "Apple", "04:26:65": "Apple",
    "04:54:53": "Apple", "04:DB:56": "Apple", "04:E5:36": "Apple",
    "04:F1:3E": "Apple", "04:F7:E4": "Apple", "08:00:07": "Apple",
    "0C:30:21": "Apple", "0C:74:C2": "Apple", "0C:77:1A": "Apple",
    "10:40:F3": "Apple", "10:9A:DD": "Apple", "10:DD:B1": "Apple",
    "14:10:9F": "Apple", "14:5A:05": "Apple", "20:7D:74": "Apple",
    "28:6A:BA": "Apple", "28:CF:E9": "Apple", "28:E0:2C": "Apple",
    "34:36:3B": "Apple", "34:51:C9": "Apple", "34:A3:95": "Apple",
    "38:B5:4D": "Apple", "3C:07:54": "Apple", "3C:2E:F9": "Apple",
    "40:30:04": "Apple", "40:33:1A": "Apple", "44:00:10": "Apple",
    "48:60:BC": "Apple", "48:74:6E": "Apple", "4C:8D:79": "Apple",
    "54:72:4F": "Apple", "58:55:CA": "Apple", "5C:F5:DA": "Apple",
    "60:33:4B": "Apple", "60:F4:45": "Apple", "64:20:0C": "Apple",
    "64:B9:E8": "Apple", "68:5B:35": "Apple", "6C:40:08": "Apple",
    "70:48:0F": "Apple", "70:DE:E2": "Apple", "74:E2:F5": "Apple",
    "78:31:C1": "Apple", "78:7B:8A": "Apple", "78:CA:39": "Apple",
    "7C:6D:62": "Apple", "84:38:35": "Apple", "84:78:8B": "Apple",
    "88:53:95": "Apple", "8C:7C:92": "Apple", "8C:8F:E9": "Apple",
    "90:60:F1": "Apple", "90:84:0D": "Apple", "94:E9:6A": "Apple",
    "98:5A:EB": "Apple", "98:F0:AB": "Apple", "9C:35:EB": "Apple",
    "A0:99:9B": "Apple", "A4:5E:60": "Apple", "A4:B1:97": "Apple",
    "A8:51:AB": "Apple", "AC:CF:5C": "Apple", "B0:65:BD": "Apple",
    "B4:F0:AB": "Apple", "B8:E8:56": "Apple", "BC:54:36": "Apple",
    "C0:9F:42": "Apple", "C4:B3:01": "Apple", "C8:1E:8E": "Apple",
    "CC:08:8D": "Apple", "D0:23:DB": "Apple", "D8:1D:72": "Apple",
    "DC:2B:2A": "Apple", "DC:86:D8": "Apple", "E0:5F:45": "Apple",
    "E0:C9:7A": "Apple", "E4:CE:8F": "Apple", "EC:35:86": "Apple",
    "F0:18:98": "Apple", "F0:99:BF": "Apple", "F4:0F:24": "Apple",
    "F4:F1:5A": "Apple", "F8:FF:C2": "Apple", "FC:25:3F": "Apple",

    # ─── Samsung ───
    "00:00:F0": "Samsung", "00:07:AB": "Samsung", "00:12:47": "Samsung",
    "00:15:99": "Samsung", "00:1A:8A": "Samsung", "00:21:19": "Samsung",
    "00:23:99": "Samsung", "00:24:54": "Samsung", "00:26:37": "Samsung",
    "08:08:C2": "Samsung", "08:37:3D": "Samsung", "08:EC:A9": "Samsung",
    "0C:14:20": "Samsung", "10:1D:C0": "Samsung", "14:32:D1": "Samsung",
    "18:3F:47": "Samsung", "20:13:E0": "Samsung", "24:DB:ED": "Samsung",
    "28:BA:B5": "Samsung", "2C:F0:A2": "Samsung", "34:23:BA": "Samsung",
    "38:0A:94": "Samsung", "3C:5A:37": "Samsung", "40:0E:85": "Samsung",
    "44:4E:1A": "Samsung", "5C:0A:5B": "Samsung", "5C:F8:A1": "Samsung",
    "78:F7:BE": "Samsung", "84:11:9E": "Samsung", "88:32:9B": "Samsung",
    "8C:71:F8": "Samsung", "90:18:7C": "Samsung", "94:35:0A": "Samsung",
    "9C:65:B0": "Samsung", "A0:0B:BA": "Samsung", "A8:9F:BA": "Samsung",
    "B0:DF:3A": "Samsung", "BC:14:85": "Samsung", "BC:79:AD": "Samsung",
    "C0:BD:D1": "Samsung", "CC:07:AB": "Samsung", "D8:31:CF": "Samsung",
    "E8:50:8B": "Samsung", "EC:1F:72": "Samsung", "F0:5A:09": "Samsung",
    "F8:04:2E": "Samsung", "FC:F1:36": "Samsung",

    # ─── Xiaomi ───
    "10:2A:B3": "Xiaomi", "14:F6:5A": "Xiaomi", "28:E3:1F": "Xiaomi",
    "34:CE:00": "Xiaomi", "50:8F:4C": "Xiaomi", "58:44:98": "Xiaomi",
    "64:09:80": "Xiaomi", "64:B4:73": "Xiaomi", "68:DF:DD": "Xiaomi",
    "74:23:44": "Xiaomi", "74:51:BA": "Xiaomi", "78:11:DC": "Xiaomi",
    "8C:BE:BE": "Xiaomi", "98:FA:E3": "Xiaomi", "AC:F7:F3": "Xiaomi",
    "B0:E2:35": "Xiaomi", "C4:6A:B7": "Xiaomi", "D4:97:0B": "Xiaomi",
    "E0:B5:2D": "Xiaomi", "F0:B4:29": "Xiaomi", "F8:A4:5F": "Xiaomi",

    # ─── Huawei ───
    "00:25:9E": "Huawei", "00:34:FE": "Huawei", "04:25:C5": "Huawei",
    "04:F9:38": "Huawei", "08:7A:4C": "Huawei", "0C:96:BF": "Huawei",
    "10:1B:54": "Huawei", "20:A6:80": "Huawei", "28:31:52": "Huawei",
    "28:6E:D4": "Huawei", "34:6B:D3": "Huawei", "3C:DF:BD": "Huawei",
    "48:00:31": "Huawei", "4C:1F:CC": "Huawei", "60:DE:44": "Huawei",
    "70:54:F5": "Huawei", "80:38:BC": "Huawei", "8C:34:FD": "Huawei",
    "9C:28:EF": "Huawei", "AC:E2:15": "Huawei", "C4:07:2F": "Huawei",
    "D0:D7:83": "Huawei", "DC:D9:16": "Huawei", "E0:24:7F": "Huawei",

    # ─── OPPO / Vivo / Realme / OnePlus ───
    "78:11:DD": "OPPO", "94:DB:DA": "OPPO", "B0:E5:ED": "OPPO",
    "00:0E:50": "Vivo", "70:B1:4E": "Vivo", "C8:14:51": "Vivo",
    "20:CF:30": "Realme", "AC:E0:10": "Realme",
    "94:65:2D": "OnePlus", "C0:EE:FB": "OnePlus",

    # ─── PC NIC vendors (laptops/desktops via Ethernet or WiFi) ───
    "00:02:B3": "Intel", "00:03:47": "Intel", "00:13:02": "Intel",
    "00:13:CE": "Intel", "00:13:E8": "Intel", "00:15:00": "Intel",
    "00:15:17": "Intel", "00:16:6F": "Intel", "00:16:EA": "Intel",
    "00:18:DE": "Intel", "00:19:D1": "Intel", "00:1B:21": "Intel",
    "00:1C:BF": "Intel", "00:1D:E0": "Intel", "00:1F:3B": "Intel",
    "00:21:5C": "Intel", "00:21:5D": "Intel", "00:22:FA": "Intel",
    "00:24:D6": "Intel", "00:26:C7": "Intel", "00:27:10": "Intel",
    "00:7E:E5": "Intel", "10:0B:A9": "Intel", "10:F0:05": "Intel",
    "1C:39:47": "Intel", "1C:75:08": "Intel", "20:16:B9": "Intel",
    "28:B2:BD": "Intel", "2C:6E:85": "Intel", "30:3A:64": "Intel",
    "34:13:E8": "Intel", "34:DE:1A": "Intel", "44:85:00": "Intel",
    "5C:E0:C5": "Intel", "60:F2:62": "Intel", "68:07:15": "Intel",
    "70:1C:E7": "Intel", "78:0C:B8": "Intel", "84:A6:C8": "Intel",
    "8C:16:45": "Intel", "98:4F:EE": "Intel", "9C:B6:D0": "Intel",
    "A0:88:B4": "Intel", "AC:7B:A1": "Intel", "B4:6D:83": "Intel",
    "BC:77:37": "Intel", "C4:8E:8F": "Intel", "C8:F7:33": "Intel",
    "D0:7E:35": "Intel", "DC:A6:32": "Intel", "E0:94:67": "Intel",
    "E4:42:A6": "Intel", "F4:06:69": "Intel", "F8:34:41": "Intel",
    "FC:F8:AE": "Intel",

    "00:E0:4C": "Realtek", "00:1F:1F": "Realtek", "00:E0:4D": "Realtek",
    "52:54:00": "Realtek", "EC:8E:B5": "Realtek",

    # ─── Laptop OEMs ───
    "00:08:74": "Dell", "00:11:43": "Dell", "00:14:22": "Dell",
    "00:18:8B": "Dell", "00:1D:09": "Dell", "00:22:19": "Dell",
    "00:23:AE": "Dell", "00:24:E8": "Dell", "00:26:B9": "Dell",
    "B8:CA:3A": "Dell", "F0:1F:AF": "Dell", "F8:B1:56": "Dell",

    "00:0F:20": "HP", "00:14:38": "HP", "00:17:08": "HP",
    "00:1B:78": "HP", "00:1F:29": "HP", "00:21:5A": "HP",
    "00:23:7D": "HP", "00:25:B3": "HP", "00:26:55": "HP",
    "9C:8E:99": "HP", "BC:30:5B": "HP",

    "00:0A:F5": "Lenovo", "00:14:51": "Lenovo", "00:19:99": "Lenovo",
    "08:11:96": "Lenovo", "54:E1:AD": "Lenovo", "70:F3:95": "Lenovo",
    "78:0C:B8": "Lenovo", "88:70:8C": "Lenovo", "C8:DE:C9": "Lenovo",

    "00:01:6C": "Asus", "00:13:D4": "Asus", "00:15:F2": "Asus",
    "00:17:31": "Asus", "00:1B:FC": "Asus", "00:1F:C6": "Asus",
    "08:60:6E": "Asus", "10:7B:44": "Asus", "1C:87:2C": "Asus",
    "30:85:A9": "Asus", "AC:9E:17": "Asus", "BC:EE:7B": "Asus",
    "F0:79:59": "Asus", "F8:32:E4": "Asus",

    # ─── Gaming consoles ───
    "00:04:1F": "Sony PlayStation", "00:13:15": "Sony PlayStation",
    "00:19:C5": "Sony PlayStation", "00:1F:A7": "Sony PlayStation",
    "00:24:8D": "Sony PlayStation", "00:25:E0": "Sony PlayStation",
    "00:0D:3A": "Microsoft Xbox", "00:50:F2": "Microsoft Xbox",
    "00:17:FA": "Microsoft Xbox", "7C:1E:52": "Microsoft Xbox",
    "00:17:AB": "Nintendo", "00:19:1D": "Nintendo", "00:1A:E9": "Nintendo",
    "00:24:1E": "Nintendo", "0C:FE:45": "Nintendo", "8C:CD:E8": "Nintendo",

    # ─── Routers / network gear (so we don't mislabel them as devices) ───
    "00:0C:42": "MikroTik", "4C:5E:0C": "MikroTik", "48:8F:5A": "MikroTik",
    "6C:3B:6B": "MikroTik", "B8:69:F4": "MikroTik", "C4:AD:34": "MikroTik",
    "DC:2C:6E": "MikroTik", "E4:8D:8C": "MikroTik",

    "00:11:32": "Synology",
    "00:1F:33": "Netgear", "20:E5:2A": "Netgear", "44:94:FC": "Netgear",
    "C0:3F:0E": "Netgear",
    "08:96:D7": "TP-Link", "14:CC:20": "TP-Link", "1C:61:B4": "TP-Link",
    "30:B5:C2": "TP-Link", "B0:48:7A": "TP-Link", "C4:6E:1F": "TP-Link",
    "D4:6E:0E": "TP-Link", "E4:6F:13": "TP-Link",
    "00:1E:8C": "ASUSTek",

    # ─── Smart TVs / streaming sticks ───
    "08:CC:27": "Roku", "B0:A7:37": "Roku",
    "5C:CF:7F": "LG Electronics", "8C:79:F5": "LG Electronics",

    # ─── IoT — common smart home ───
    "DC:A6:32": "Raspberry Pi", "B8:27:EB": "Raspberry Pi",
    "44:65:0D": "Amazon", "F0:D2:F1": "Amazon", "FC:65:DE": "Amazon",
    "8C:85:90": "Amazon",
    "20:DF:B9": "Google", "44:07:0B": "Google", "F4:F5:E8": "Google",
}


# ─────────────────────────────────────────────────────────────────────────────
# Vendor → device category mapping. Used when we have a vendor but need to
# guess the device class. Different vendors lean toward different classes;
# the connection medium (wireless vs ethernet) narrows it further.
# ─────────────────────────────────────────────────────────────────────────────
_PHONE_VENDORS  = {"Samsung", "Xiaomi", "Huawei", "OPPO", "Vivo",
                    "Realme", "OnePlus"}
_NIC_VENDORS    = {"Intel", "Realtek", "Broadcom", "Atheros"}
_LAPTOP_OEMS    = {"Dell", "HP", "Lenovo", "Asus", "ASUSTek", "Acer"}
_CONSOLE_VENDORS = {"Sony PlayStation", "Microsoft Xbox", "Nintendo"}
_ROUTER_VENDORS  = {"MikroTik", "TP-Link", "Netgear", "Synology"}
_TV_VENDORS      = {"Roku", "LG Electronics"}
_IOT_VENDORS     = {"Amazon", "Google", "Raspberry Pi"}


def _normalize_mac(mac: str) -> str:
    """Return MAC in `AA:BB:CC:DD:EE:FF` form, or '' if invalid."""
    if not mac:
        return ""
    cleaned = mac.replace("-", ":").replace(".", ":").upper().strip()
    parts = cleaned.split(":")
    if len(parts) != 6 or not all(len(p) == 2 for p in parts):
        # try without separators (some MTs send `AABBCCDDEEFF`)
        only_hex = "".join(c for c in cleaned if c in "0123456789ABCDEF")
        if len(only_hex) == 12:
            cleaned = ":".join(only_hex[i:i + 2] for i in range(0, 12, 2))
            parts = cleaned.split(":")
        else:
            return ""
    try:
        for p in parts:
            int(p, 16)
    except ValueError:
        return ""
    return ":".join(parts)


def _is_random_mac(mac: str) -> bool:
    """The U/L bit (bit 1 of first octet) — set means locally administered.
    Modern iOS / Android randomize per-SSID for privacy; the result has
    this bit set. Identifying these matters because the OUI lookup will
    be meaningless on a random MAC."""
    try:
        first = int(mac[:2], 16)
    except (TypeError, ValueError):
        return False
    return bool(first & 0x02)


def is_random_mac(mac: Optional[str]) -> bool:
    """عام/قابل لإعادة الاستخدام: هل هذا العنوان عشوائي (locally-administered)؟

    أجهزة iOS 14+ و Android 10+ تولّد عنوان MAC عشوائيًا لكل شبكة حفاظًا على
    الخصوصية. علامة ذلك هي «بت الإدارة المحلية» (U/L bit) = البت الثاني في
    أول ثُماني (octet) من العنوان. عمليًا: إذا كانت الخانة السداسية الثانية
    من أول بايت ضمن {2, 6, A, E} فالعنوان عشوائي/خاص.

    تُطبّع المدخلات أولًا (تقبل AA:BB:.. أو AA-BB-.. أو AABBCC..). تُرجع False
    عند أي مدخل غير صالح — محصّنة، لا ترمي استثناءات.
    """
    norm = _normalize_mac(mac or "")
    if not norm:
        return False
    return _is_random_mac(norm)


def _decode_connection(nas_port_type: Optional[str]) -> str:
    """Turn `NAS-Port-Type` into a friendly Arabic label."""
    if not nas_port_type:
        return ""
    t = nas_port_type.lower()
    if "wireless" in t or "wifi" in t or "802.11" in t:
        return "Wi-Fi 802.11"
    if "ethernet" in t or "wired" in t:
        return "Ethernet (سلكي)"
    if "ppp" in t:
        return "PPP"
    if "isdn" in t:
        return "ISDN"
    return nas_port_type


def _connect_info_lower(s: Optional[str]) -> str:
    return (s or "").lower()


def _device(*, mac: str, vendor: str, category: str, label: str,
             icon: str, connection: str, confidence: str,
             is_random_mac: bool = False) -> dict:
    return {
        "mac": mac,
        "vendor": vendor,
        "category": category,
        "label": label,
        "icon": icon,
        "connection": connection,
        "confidence": confidence,
        "is_random_mac": is_random_mac,
    }


def infer_device(mac: Optional[str],
                  nas_port_type: Optional[str] = None,
                  connect_info: Optional[str] = None) -> dict:
    """Best-effort device classification from a MAC + connection hints.

    Always returns a dict (never raises). Unknown MACs degrade to category
    'unknown' with confidence 'low' so the UI can still render something.
    """
    norm = _normalize_mac(mac or "")
    connection = _decode_connection(nas_port_type)
    ci = _connect_info_lower(connect_info)

    if not norm:
        return _device(mac="", vendor="غير معروف", category="unknown",
                        label="غير معروف", icon="circle-question",
                        connection=connection, confidence="low")

    if _is_random_mac(norm):
        # randomized MAC → iOS 14+ / Android 10+ privacy feature
        guess = "iPhone أو أندرويد حديث"
        if "android" in ci:
            guess = "جوال أندرويد (MAC عشوائي)"
        elif "iphone" in ci or "ios" in ci:
            guess = "iPhone (MAC عشوائي)"
        return _device(mac=norm, vendor="MAC عشوائي",
                        category="phone-random",
                        label=guess, icon="mobile-screen",
                        connection=connection, confidence="medium",
                        is_random_mac=True)

    oui = norm[:8]
    vendor = _OUI_DB.get(oui)
    if not vendor:
        # try the first 2 octets as a last-ditch heuristic
        # (some vendors have many sub-OUIs not in our table)
        return _device(mac=norm, vendor="غير معروف",
                        category="unknown",
                        label="جهاز غير معروف",
                        icon="circle-question",
                        connection=connection, confidence="low")

    # ─── Apple ───
    if vendor == "Apple":
        if "wireless" in connection.lower() or "wi-fi" in connection.lower():
            if "ipad" in ci or "tablet" in ci:
                label, icon = "iPad", "tablet-screen-button"
                category = "tablet"
            elif "mac" in ci and "iphone" not in ci:
                label, icon = "MacBook", "laptop"
                category = "laptop"
            else:
                label, icon = "iPhone أو iPad", "mobile-screen"
                category = "phone-ios"
        else:
            label, icon = "Mac (سلكي)", "desktop"
            category = "desktop"
        return _device(mac=norm, vendor=vendor, category=category,
                        label=label, icon=icon, connection=connection,
                        confidence="high")

    # ─── Android phones ───
    if vendor in _PHONE_VENDORS:
        label = f"جوال أندرويد · {vendor}"
        return _device(mac=norm, vendor=vendor, category="phone-android",
                        label=label, icon="mobile-screen-button",
                        connection=connection, confidence="high")

    # ─── PC NICs ───
    if vendor in _NIC_VENDORS:
        # NIC vendor + wireless → laptop; ethernet → desktop or laptop
        is_wifi = "wi-fi" in connection.lower() or "wireless" in connection.lower()
        if is_wifi:
            label = f"لابتوب ({vendor} Wi-Fi)"
            icon, category = "laptop", "laptop"
            conf = "medium"
        else:
            label = f"كمبيوتر ({vendor})"
            icon, category = "desktop", "desktop"
            conf = "low"
        return _device(mac=norm, vendor=vendor, category=category,
                        label=label, icon=icon, connection=connection,
                        confidence=conf)

    # ─── Branded laptops ───
    if vendor in _LAPTOP_OEMS:
        return _device(mac=norm, vendor=vendor, category="laptop",
                        label=f"لابتوب {vendor}", icon="laptop",
                        connection=connection, confidence="high")

    # ─── Consoles ───
    if vendor in _CONSOLE_VENDORS:
        return _device(mac=norm, vendor=vendor, category="console",
                        label=vendor, icon="gamepad",
                        connection=connection, confidence="high")

    # ─── Routers / network gear (our own infrastructure, usually) ───
    if vendor in _ROUTER_VENDORS:
        return _device(mac=norm, vendor=vendor, category="router",
                        label=f"شبكة · {vendor}", icon="network-wired",
                        connection=connection, confidence="high")

    # ─── Smart TVs ───
    if vendor in _TV_VENDORS:
        return _device(mac=norm, vendor=vendor, category="tv",
                        label=f"تلفاز · {vendor}", icon="tv",
                        connection=connection, confidence="high")

    # ─── IoT ───
    if vendor in _IOT_VENDORS:
        return _device(mac=norm, vendor=vendor, category="iot",
                        label=f"جهاز ذكي · {vendor}", icon="house-signal",
                        connection=connection, confidence="medium")

    # Generic vendor — known OUI but no category mapping
    return _device(mac=norm, vendor=vendor, category="unknown",
                    label=vendor, icon="microchip",
                    connection=connection, confidence="low")
