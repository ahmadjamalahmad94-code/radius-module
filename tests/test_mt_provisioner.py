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
    # No leftover {placeholders}.
    assert "{" not in out or "{find}" in out  # `[find]` is fine; raw `{x}` is not
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
