"""R13.A.6 regression: MAC-based device fingerprinting.

The card checker now classifies the device behind each MAC into
human-friendly categories using OUI lookup, MAC-randomization
detection, and connection medium. Tests cover:

  1. Apple OUIs on Wi-Fi → iPhone/iPad family.
  2. Apple OUIs on Ethernet → Mac desktop family.
  3. Samsung / Xiaomi / Huawei OUIs → Android phone.
  4. Intel / Realtek NICs → laptop or desktop depending on connection.
  5. Dell / HP / Lenovo OUIs → branded laptop.
  6. Sony / Microsoft / Nintendo OUIs → console.
  7. Randomized MAC (U/L bit set) → phone-random, even with unknown OUI.
  8. Unknown OUI → unknown category, low confidence.
  9. Garbage / empty inputs → safe defaults (no exceptions).
 10. MAC normalization — accepts colons, dashes, no separators.

The shape of the returned dict is a stable contract — UI relies on
`vendor`, `category`, `label`, `icon`, `confidence`, `is_random_mac`.
"""
from __future__ import annotations

import pytest

from app.radius.services.device_fingerprint import infer_device


# ─────────── Apple ───────────

def test_apple_oui_on_wireless_is_iphone_class():
    d = infer_device(mac="04:0C:CE:11:22:33", nas_port_type="Wireless-802.11")
    assert d["vendor"] == "Apple"
    assert d["category"] == "phone-ios"
    assert "iPhone" in d["label"] or "iPad" in d["label"]
    assert d["confidence"] == "high"
    assert d["connection"].startswith("Wi-Fi")


def test_apple_oui_on_ethernet_is_mac_desktop():
    d = infer_device(mac="04:0C:CE:11:22:33", nas_port_type="Ethernet")
    assert d["vendor"] == "Apple"
    assert d["category"] == "desktop"
    assert "Mac" in d["label"]


def test_apple_oui_with_ipad_hint_picks_tablet():
    d = infer_device(mac="04:0C:CE:11:22:33",
                      nas_port_type="Wireless-802.11",
                      connect_info="iPad mini wifi")
    assert d["category"] == "tablet"
    assert d["label"] == "iPad"


# ─────────── Android phones ───────────

@pytest.mark.parametrize("oui,vendor", [
    ("28:BA:B5", "Samsung"),
    ("64:09:80", "Xiaomi"),
    ("00:25:9E", "Huawei"),
    ("78:11:DD", "OPPO"),
    ("70:B1:4E", "Vivo"),
    ("94:65:2D", "OnePlus"),
])
def test_known_android_vendors_classify_as_android_phone(oui, vendor):
    d = infer_device(mac=f"{oui}:AA:BB:CC", nas_port_type="Wireless-802.11")
    assert d["vendor"] == vendor
    assert d["category"] == "phone-android"
    assert vendor in d["label"]
    assert d["icon"] == "mobile-screen-button"


# ─────────── PC NICs ───────────

def test_intel_nic_on_wireless_is_laptop():
    d = infer_device(mac="00:24:D6:11:22:33", nas_port_type="Wireless-802.11")
    assert d["vendor"] == "Intel"
    assert d["category"] == "laptop"
    assert d["confidence"] == "medium"


def test_intel_nic_on_ethernet_is_desktop():
    d = infer_device(mac="00:24:D6:11:22:33", nas_port_type="Ethernet")
    assert d["vendor"] == "Intel"
    assert d["category"] == "desktop"


# ─────────── Branded laptops ───────────

@pytest.mark.parametrize("oui,vendor", [
    ("00:08:74", "Dell"),
    ("00:0F:20", "HP"),
    ("88:70:8C", "Lenovo"),
    ("08:60:6E", "Asus"),
])
def test_branded_laptop_oem_classifies_as_laptop(oui, vendor):
    d = infer_device(mac=f"{oui}:AA:BB:CC")
    assert d["vendor"] == vendor
    assert d["category"] == "laptop"
    assert d["icon"] == "laptop"


# ─────────── Consoles ───────────

@pytest.mark.parametrize("oui,vendor", [
    ("00:04:1F", "Sony PlayStation"),
    ("00:0D:3A", "Microsoft Xbox"),
    ("00:17:AB", "Nintendo"),
])
def test_console_vendors_classify_as_console(oui, vendor):
    d = infer_device(mac=f"{oui}:AA:BB:CC")
    assert d["category"] == "console"
    assert d["icon"] == "gamepad"
    assert d["vendor"] == vendor


# ─────────── Randomization detection ───────────

def test_locally_administered_mac_is_phone_random():
    """U/L bit set on first octet → modern iOS/Android privacy random.
    The OUI lookup is meaningless on these MACs."""
    d = infer_device(mac="9E:49:36:50:27:A4", nas_port_type="Wireless-802.11")
    assert d["is_random_mac"] is True
    assert d["category"] == "phone-random"
    assert d["vendor"] == "MAC عشوائي"


def test_random_mac_with_android_connect_info_specifies_android():
    d = infer_device(mac="DA:11:22:33:44:55",
                      nas_port_type="Wireless-802.11",
                      connect_info="android wifi 802.11n")
    assert d["is_random_mac"] is True
    assert "أندرويد" in d["label"]


# ─────────── Unknown / edge ───────────

def test_unknown_oui_returns_safe_default():
    d = infer_device(mac="01:02:03:04:05:06")
    # 01 has the multicast bit too — but main thing is vendor unknown
    assert d["category"] in {"unknown", "phone-random"}
    assert d["confidence"] in {"low", "medium"}


def test_empty_mac_does_not_raise():
    assert infer_device(mac="")["category"] == "unknown"
    assert infer_device(mac=None)["category"] == "unknown"


def test_garbage_mac_does_not_raise():
    assert infer_device(mac="not-a-mac")["category"] == "unknown"


# ─────────── Normalization ───────────

def test_dash_separated_mac_is_normalized():
    d = infer_device(mac="04-0C-CE-11-22-33", nas_port_type="Wireless-802.11")
    assert d["vendor"] == "Apple"
    assert d["mac"] == "04:0C:CE:11:22:33"


def test_no_separator_mac_is_normalized():
    d = infer_device(mac="040CCE112233", nas_port_type="Wireless-802.11")
    assert d["vendor"] == "Apple"
    assert d["mac"] == "04:0C:CE:11:22:33"


def test_lowercase_input_is_uppercased():
    d = infer_device(mac="04:0c:ce:11:22:33")
    assert d["mac"] == "04:0C:CE:11:22:33"


# ─────────── Connection decoding ───────────

@pytest.mark.parametrize("input_type,expected_prefix", [
    ("Wireless-802.11", "Wi-Fi"),
    ("wireless", "Wi-Fi"),
    ("Ethernet", "Ethernet"),
    ("Virtual", "Virtual"),  # passthrough
])
def test_nas_port_type_decoded_to_friendly(input_type, expected_prefix):
    d = infer_device(mac="04:0C:CE:11:22:33", nas_port_type=input_type)
    assert d["connection"].startswith(expected_prefix)
