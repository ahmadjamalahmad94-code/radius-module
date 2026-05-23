"""NPC remote-access URLs — pure function tests."""
from __future__ import annotations

from app.radius.services.npc_remote_access_urls import (
    compute_access_urls,
)


def _nas(address="1.2.3.4", ssh_port=22):
    return {"address": address, "ssh_port": ssh_port}


def test_returns_empty_when_no_address():
    assert compute_access_urls(
        {"allow_winbox": True}, {"address": ""},
    ) == []


def test_winbox_url_is_host_colon_8291():
    out = compute_access_urls(
        {"allow_winbox": True}, _nas(address="192.168.5.1"),
    )
    assert len(out) == 1
    assert out[0]["service"] == "winbox"
    assert out[0]["url"] == "192.168.5.1:8291"
    assert out[0]["port"] == 8291
    assert out[0]["clipboard"] == "192.168.5.1:8291"


def test_webfig_https_uses_https_scheme():
    out = compute_access_urls(
        {"allow_webfig_https": True}, _nas(),
    )
    assert out[0]["service"] == "webfig_https"
    assert out[0]["url"].startswith("https://")
    assert out[0]["url"] == "https://1.2.3.4/"


def test_webfig_http_uses_http_scheme():
    out = compute_access_urls(
        {"allow_webfig_http": True}, _nas(),
    )
    assert out[0]["url"] == "http://1.2.3.4/"


def test_ssh_uses_nas_ssh_port():
    out = compute_access_urls(
        {"allow_ssh": True},
        _nas(address="10.0.0.1", ssh_port=2222),
    )
    assert out[0]["service"] == "ssh"
    assert out[0]["port"] == 2222
    assert "10.0.0.1:2222" in out[0]["url"]
    # Default port should NOT appear in the clipboard hint.
    assert "ssh -p 2222 admin@10.0.0.1" in out[0]["clipboard"]


def test_ssh_default_port_omits_p_flag():
    out = compute_access_urls(
        {"allow_ssh": True}, _nas(ssh_port=22),
    )
    assert "-p" not in out[0]["clipboard"]


def test_api_and_api_ssl_have_distinct_ports():
    out = compute_access_urls(
        {"allow_api": True, "allow_api_ssl": True}, _nas(),
    )
    services = {e["service"]: e for e in out}
    assert services["api"]["port"] == 8728
    assert services["api_ssl"]["port"] == 8729


def test_all_services_enabled_returns_full_list():
    out = compute_access_urls(
        {
            "allow_winbox":       True,
            "allow_ssh":          True,
            "allow_api":          True,
            "allow_api_ssl":      True,
            "allow_webfig_http":  True,
            "allow_webfig_https": True,
        },
        _nas(),
    )
    services = {e["service"] for e in out}
    assert services == {
        "winbox", "ssh", "api", "api_ssl",
        "webfig_http", "webfig_https",
    }


def test_no_services_enabled_returns_empty_list():
    out = compute_access_urls({}, _nas())
    assert out == []


def test_each_entry_has_arabic_label():
    out = compute_access_urls(
        {"allow_winbox": True, "allow_webfig_https": True},
        _nas(),
    )
    for entry in out:
        assert entry["service_ar"]
        # Arabic chars present (a sanity smoke check, not an
        # encoding assertion).
        assert any(
            "؀" <= ch <= "ۿ"
            for ch in entry["service_ar"]
        )
