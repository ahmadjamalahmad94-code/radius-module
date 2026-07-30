"""L1 — Provisioner unit tests.

Pin the credential shape (so the DB column widths stay in sync)
and verify both RouterOS v6 + v7 scripts come out well-formed and
free of leftover format-string placeholders.
"""
from __future__ import annotations

import re

import pytest

from app.radius.services import mt_provisioner as p


# ─── Credentials ─────────────────────────────────────────────────


def test_generate_credentials_shape():
    c = p.generate_credentials()
    assert set(c) == {"api_user", "api_password", "radius_secret"}


def test_generate_credentials_user_prefix_and_length():
    c = p.generate_credentials()
    assert c["api_user"].startswith("hr-")
    suffix = c["api_user"][len("hr-"):]
    assert len(suffix) == 6
    # lowercase letters + digits only — no dashes, spaces, control.
    assert re.match(r"^[a-z0-9]{6}$", suffix)


def test_generate_credentials_password_length_and_charset():
    c = p.generate_credentials()
    assert len(c["api_password"]) == 32
    assert len(c["radius_secret"]) == 32
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~")
    assert set(c["api_password"]) <= safe
    assert set(c["radius_secret"]) <= safe


def test_generate_credentials_are_unique_each_call():
    """Two consecutive calls must NOT produce the same values —
    catches accidental class-level constants."""
    a = p.generate_credentials()
    b = p.generate_credentials()
    assert a["api_user"] != b["api_user"]
    assert a["api_password"] != b["api_password"]
    assert a["radius_secret"] != b["radius_secret"]


# ─── Script rendering ────────────────────────────────────────────


@pytest.fixture
def sample_args():
    return dict(
        nas_name="MT-Test",
        api_user="hr-abc123",
        api_password="P@SS-32-chars-without-double-quote!!",
        radius_secret="RS-32-chars-without-double-quote!!!!",
        server_ip="203.0.113.10",
        api_port=8728,
        coa_port=3799,
    )


@pytest.mark.parametrize("version", ["6", "7"])
def test_render_script_contains_every_field(sample_args, version):
    out = p.render_routeros_script(ros_version=version, **sample_args)
    # Every input is interpolated, not left as a placeholder.
    for v in (sample_args["nas_name"], sample_args["api_user"],
              sample_args["api_password"], sample_args["radius_secret"],
              sample_args["server_ip"]):
        assert v in out
    assert str(sample_args["api_port"]) in out
    assert str(sample_args["coa_port"]) in out
    # No leftover str.format placeholders — a `{word}` token. RouterOS's own
    # `do={ ... }` script blocks (idempotent guards) and `[find]` are legitimate
    # and must NOT trip this, so match only `{lower_snake}` placeholder names.
    assert not re.search(r"\{[a-z_]+\}", out), "unfilled placeholder remains"
    assert "{nas_name}" not in out
    assert "{server_ip}" not in out


@pytest.mark.parametrize("version", ["6", "7"])
def test_render_script_includes_required_commands(sample_args, version):
    out = p.render_routeros_script(ros_version=version, **sample_args)
    # The script MUST do these five things — otherwise the router
    # would fail validation against HobeRadius afterwards.
    assert "/user group add" in out
    assert "/user add" in out
    assert "/ip service set api" in out
    assert "/radius add" in out
    assert "/radius incoming set accept=yes" in out
    assert "/ip hotspot profile set" in out
    assert "/ppp aaa set use-radius=yes" in out


def test_render_script_v7_has_v7_marker(sample_args):
    out = p.render_routeros_script(ros_version="7", **sample_args)
    assert "RouterOS 7" in out


def test_render_script_v6_has_v6_marker(sample_args):
    out = p.render_routeros_script(ros_version="6", **sample_args)
    assert "RouterOS 6" in out


def test_render_script_rejects_unknown_version(sample_args):
    with pytest.raises(ValueError, match="unsupported"):
        p.render_routeros_script(ros_version="8", **sample_args)


def test_render_script_rejects_empty_required_fields(sample_args):
    bad = dict(sample_args)
    bad["nas_name"] = ""
    with pytest.raises(ValueError, match="missing"):
        p.render_routeros_script(ros_version="7", **bad)


def test_render_script_rejects_missing_server_ip(sample_args):
    bad = dict(sample_args)
    bad["server_ip"] = ""
    with pytest.raises(ValueError, match="server_ip"):
        p.render_routeros_script(ros_version="7", **bad)


def test_supported_versions_constant():
    """Pin the supported set; bumping it is a deliberate edit."""
    assert p.SUPPORTED_ROS_VERSIONS == ("6", "7")


def test_render_script_omits_api_address_line_by_default(sample_args):
    out = p.render_routeros_script(ros_version="7", **sample_args)
    # No `set api address=` directive when the caller doesn't ask.
    assert "set api address=" not in out


def test_render_script_binds_mgmt_services_to_both_gateways(sample_args):
    """M3 — when a tunnel context is present, api/winbox/www bind to a COMBINED
    allow-list with BOTH management gateways (WG subnet + SSTP/RADIUS gateway)
    so pasting the wizard script never clobbers the other tunnel path. Strictly
    tunnel-only (no WAN)."""
    out = p.render_routeros_script(
        ros_version="7", api_allowed_address="10.10.0.0/24", **sample_args,
    )
    # MT74 — دمجٌ لا استبدال (الاستبدال كان يَمحو عناوين الفنّيّ فيَفقد
    #   WinBox عبر الـIP). النيّة محفوظة: بوّابتان ولا WAN.
    for svc in ("api", "winbox", "www"):
        assert f"[/ip service get {svc} address]" in out
        assert f"/ip service set {svc} address=$c{svc[:3]}" in out
        assert f"/ip service set {svc} address=10." not in out
    # no bare WG-only line survives to clobber the SSTP gateway path
    assert "/ip service set api address=10.10.0.0/24\n" not in out
    assert "0.0.0.0/0" not in out
    # the address lines sit AFTER `set api disabled=no` (must be on enabled svc)
    enabled_idx = out.index("set api disabled=no")
    address_idx = out.index("/ip service set api address=$capi")   # MT74
    assert address_idx > enabled_idx


def test_render_script_is_idempotent_user_and_radius(sample_args):
    """Re-pasting the wizard script must not pile up duplicates: the API user
    group is added only-if-missing, the api user is removed-before-add (by our
    comment tag), and the RADIUS server entry is removed-before-add (by its
    comment) — so a duplicate RADIUS server (which breaks auth) never forms."""
    out = p.render_routeros_script(
        ros_version="7", api_allowed_address="10.10.0.0/24", **sample_args,
    )
    # group: add only if missing
    assert ':if ([:len [/user group find name="hr-api"]]=0) do={' in out
    # user: remove our prior tagged user before adding
    assert '/user remove [find comment="HobeRadius API"]' in out
    assert out.index('/user remove [find comment="HobeRadius API"]') < out.index("/user add name=")
    # RADIUS: remove-before-add, comment-tagged (only ours), exactly one add
    assert '/radius remove [find comment="HobeRadius"]' in out
    assert out.index("/radius remove [find") < out.index("/radius add address=")
    assert out.count("/radius add address=") == 1
