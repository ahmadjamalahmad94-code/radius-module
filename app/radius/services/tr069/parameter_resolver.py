"""طبقة تجريد اختلاف الموديلات — تحوّل نيّة موحّدة إلى مسار TR-069 الحقيقيّ.

لا نفترض مسارًا واحدًا لكل الأجهزة. الأولويّة: ملفّ الموديل المخزَّن (paths_json)
ثم افتراضات data_model (tr181 الأحدث / tr098 القديم). أي مفتاح غير معروف →
None فيفشل الأمر بأمان («غير مدعوم لهذا الموديل») بدل إرسال setParameterValues
لمسار غير موثوق.
"""
from __future__ import annotations

import json
from typing import Any

# مسارات افتراضيّة معروفة لكلّ نموذج بيانات. القيم مبسّطة (أول واجهة) — الموديلات
# ذات الفهرسة المختلفة تُعرَّف صراحةً في tr069_model_profiles.paths_json.
_DEFAULTS: dict[str, dict[str, str]] = {
    "tr181": {
        "wifi_ssid": "Device.WiFi.SSID.1.SSID",
        "wifi_password": "Device.WiFi.AccessPoint.1.Security.KeyPassphrase",
        "wifi_enable": "Device.WiFi.Radio.1.Enable",
        "pppoe_username": "Device.PPP.Interface.1.Username",
        "pppoe_password": "Device.PPP.Interface.1.Password",
        "wan_ip": "Device.IP.Interface.1.IPv4Address.1.IPAddress",
        "wan_mac": "Device.Ethernet.Interface.1.MACAddress",
        "connected_hosts": "Device.Hosts.HostNumberOfEntries",
        "uptime": "Device.DeviceInfo.UpTime",
        "software_version": "Device.DeviceInfo.SoftwareVersion",
    },
    "tr098": {
        "wifi_ssid": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.SSID",
        "wifi_password": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.PreSharedKey.1.KeyPassphrase",
        "wifi_enable": "InternetGatewayDevice.LANDevice.1.WLANConfiguration.1.Enable",
        "pppoe_username": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Username",
        "pppoe_password": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.Password",
        "wan_ip": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANPPPConnection.1.ExternalIPAddress",
        "wan_mac": "InternetGatewayDevice.WANDevice.1.WANConnectionDevice.1.WANIPConnection.1.MACAddress",
        "connected_hosts": "InternetGatewayDevice.LANDevice.1.Hosts.HostNumberOfEntries",
        "uptime": "InternetGatewayDevice.DeviceInfo.UpTime",
        "software_version": "InternetGatewayDevice.DeviceInfo.SoftwareVersion",
    },
}


def _model_for(device: dict[str, Any]) -> str:
    dm = str(device.get("data_model") or "").strip().lower()
    if dm in ("tr181", "tr098"):
        return dm
    root = str(device.get("root_object") or "").strip()
    if root.startswith("Device"):
        return "tr181"
    if root.startswith("InternetGatewayDevice"):
        return "tr098"
    return "tr181"  # الافتراض الأحدث (وثائق BBF)


def resolve_path(device: dict[str, Any], key: str,
                 profile: dict | None = None) -> str | None:
    """يُرجع المسار الحقيقيّ للمفتاح، أو None إن كان غير مدعوم لهذا الجهاز."""
    # 1) ملفّ الموديل المخصّص إن وُجد
    if profile:
        raw = profile.get("paths_json")
        try:
            paths = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            paths = {}
        if key in paths and paths[key]:
            return str(paths[key])
    # 2) افتراضات نموذج البيانات
    model = _model_for(device)
    return _DEFAULTS.get(model, {}).get(key)


def known_keys() -> list[str]:
    return sorted(set(_DEFAULTS["tr181"]) | set(_DEFAULTS["tr098"]))
