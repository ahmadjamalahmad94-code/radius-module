"""VX2.2 — Importer, validator, classifier (pure tests).

These services touch no DB and no Flask context, so the tests
are plain pytest with no app fixture. That keeps them fast and
makes regressions trivial to isolate.
"""
from __future__ import annotations

import pytest


# ─── Validator ───────────────────────────────────────────────


@pytest.mark.parametrize("good", [
    "example.com",
    "sub.example.com",
    "deep.sub.example.com",
    "EXAMPLE.com",                  # case is normalised
    "example.com.",                 # trailing dot tolerated
    "xn--80akhbyknj4f.xn--p1ai",    # punycode IDN form
])
def test_validator_accepts_valid_domains(good):
    from app.radius.services import site_exit_validator as v
    r = v.validate(good)
    assert r.valid, f"{good!r}: {r.reason}"
    assert r.target_type == "domain"
    assert r.normalized.islower()
    assert not r.normalized.endswith(".")


@pytest.mark.parametrize("bad,why", [
    ("",                  "empty"),
    (None,                "None"),
    ("localhost",         "single-label"),
    (".com",              "leading dot → empty label"),
    ("foo..bar.com",      "empty label"),
    ("-foo.com",          "label starts with hyphen"),
    ("foo-.com",          "label ends with hyphen"),
    ("foo.123",           "numeric-only TLD"),
    ("*.example.com",     "wildcard syntax"),
    ("a" * 254 + ".com",  "too long"),
    ("https://example.com",      "URL scheme"),
    ("http://example.com/path",  "URL with path"),
    ("example.com/page",         "path-like"),
    ("example.com?q=1",          "query string"),
    ("example.com#frag",         "fragment"),
    ("example com",              "whitespace"),
])
def test_validator_rejects_bad_inputs(bad, why):
    from app.radius.services import site_exit_validator as v
    r = v.validate(bad)
    assert not r.valid, f"{bad!r} should be rejected ({why})"
    assert r.reason  # non-empty diagnostic


def test_validator_accepts_public_ipv4():
    from app.radius.services import site_exit_validator as v
    r = v.validate("8.8.8.8")
    assert r.valid
    assert r.target_type == "ip"
    assert r.normalized == "8.8.8.8"


def test_validator_accepts_public_cidr():
    from app.radius.services import site_exit_validator as v
    r = v.validate("1.1.1.0/24")
    assert r.valid
    assert r.target_type == "cidr"
    assert r.normalized == "1.1.1.0/24"


def test_validator_normalizes_cidr_with_host_bits():
    """`1.2.3.4/24` is operator-friendly shorthand for the /24
    network it belongs to."""
    from app.radius.services import site_exit_validator as v
    r = v.validate("1.2.3.4/24")
    assert r.valid
    assert r.normalized == "1.2.3.0/24"


def test_validator_rejects_catch_all_cidr_default_and_advanced():
    from app.radius.services import site_exit_validator as v
    for mode in (False, True):
        r = v.validate("0.0.0.0/0", advanced_mode=mode)
        assert not r.valid, "0.0.0.0/0 must be rejected always"
        assert "0.0.0.0/0" in r.reason or "all traffic" in r.reason


def test_validator_rejects_catch_all_cidr_normalised_forms():
    """Catching `0.0.0.0/0` but also any variant that
    normalises to /0."""
    from app.radius.services import site_exit_validator as v
    # `1.2.3.4/0` normalises to `0.0.0.0/0` under strict=False
    # — must still be rejected.
    r = v.validate("1.2.3.4/0")
    assert not r.valid


@pytest.mark.parametrize("priv", [
    "10.0.0.1",
    "192.168.1.1",
    "172.16.0.1",
    "127.0.0.1",
    "169.254.1.1",
    "224.0.0.1",           # multicast
    "240.0.0.1",           # reserved
    "0.0.0.0",             # unspecified
    "192.168.0.0/16",
    "10.0.0.0/8",
])
def test_validator_rejects_private_reserved_by_default(priv):
    from app.radius.services import site_exit_validator as v
    r = v.validate(priv)
    assert not r.valid
    assert "private" in r.reason or "reserved" in r.reason \
        or "rejected" in r.reason


def test_validator_advanced_mode_allows_private_ip():
    from app.radius.services import site_exit_validator as v
    r = v.validate("10.0.0.1", advanced_mode=True)
    assert r.valid
    assert r.target_type == "ip"


def test_validator_advanced_mode_allows_private_cidr():
    from app.radius.services import site_exit_validator as v
    r = v.validate("192.168.0.0/16", advanced_mode=True)
    assert r.valid
    assert r.target_type == "cidr"


def test_validator_rejects_ipv6():
    from app.radius.services import site_exit_validator as v
    r = v.validate("2001:db8::1")
    assert not r.valid
    assert "IPv6" in r.reason or "unsupported" in r.reason


def test_validator_strips_inline_comment():
    from app.radius.services import site_exit_validator as v
    r = v.validate("speedtest.net  # comment after value")
    assert r.valid
    assert r.normalized == "speedtest.net"


# ─── Classifier ──────────────────────────────────────────────


@pytest.mark.parametrize("host", [
    "speedtest.net",
    "www.speedtest.net",
    "c.speedtest.net",
    "ooklaserver.net",
    "speedtestcustom.com",
    "fast.com",
    "api.fast.com",
    "test-ipv6.com",
    "pingtest.net",
    "webtest.net",
])
def test_classifier_speedtest_exact(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_SPEEDTEST_MEASUREMENT


@pytest.mark.parametrize("host", [
    "regional-speedtest.example.com",
    "iperf.something.net",
    "bwtest-isp.com",
    "ookla-east.example.org",
])
def test_classifier_speedtest_substring(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_SPEEDTEST_MEASUREMENT


def test_classifier_fast_substring_is_not_over_matched():
    """`fast` is in the spec but using it as substring would
    catch `fastcompany.com`. Make sure that case lands in
    manual_review instead."""
    from app.radius.services import site_exit_classifier as c
    assert c.classify("fastcompany.com", "domain") == c.GROUP_MANUAL_REVIEW
    assert c.classify("breakfast.com", "domain") == c.GROUP_MANUAL_REVIEW


@pytest.mark.parametrize("host", [
    "whatismyip.com",
    "whatsmyip.org",
    "myip.com",
    "ifconfig.co",
    "iplocation.net",
    "ipinfo.info",
    "whoer.net",
    "ipchicken.com",
    "wtfismyip.com",
    "bearsmyip.com",
])
def test_classifier_public_ip_checkers(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_PUBLIC_IP_CHECKERS


@pytest.mark.parametrize("host", [
    "expressvpn.com", "www.expressvpn.com",
    "nordvpn.com",    "www.nordvpn.com",
    "purevpn.com",
    "privateinternetaccess.com",
    "astrill.com",
    "hide.me",
    "perfect-privacy.com",
    "zenmate.com",
    "cactusvpn.com",
    "overplay.net",
    "goldenfrog.com",
    "ipburger.com",
])
def test_classifier_vpn_provider_pages(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_VPN_PROVIDER_PAGES


@pytest.mark.parametrize("host", [
    "mxtoolbox.com", "www.mxtoolbox.com",
    "ultratools.com",
    "yougetsignal.com",
])
def test_classifier_network_diagnostics(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_NETWORK_DIAGNOSTICS


@pytest.mark.parametrize("host", [
    "google.com", "www.google.com",
    "google.ps",  "www.google.ps",
    "wolframalpha.com", "m.wolframalpha.com",
])
def test_classifier_general_probe_sites(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_GENERAL_PROBE_SITES


@pytest.mark.parametrize("v,t", [
    ("8.8.8.8",       "ip"),
    ("1.1.1.1",       "ip"),
    ("203.0.113.10",  "ip"),
    ("1.1.1.0/24",    "cidr"),
    ("8.8.8.0/24",    "cidr"),
])
def test_classifier_raw_ips_and_cidrs(v, t):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(v, t) == c.GROUP_RAW_IP_TARGETS


@pytest.mark.parametrize("host", [
    "acme.example.com",
    "random.org",
    "totallyunknown.test",
])
def test_classifier_unknown_goes_to_manual_review(host):
    from app.radius.services import site_exit_classifier as c
    assert c.classify(host, "domain") == c.GROUP_MANUAL_REVIEW


def test_classifier_is_pure_and_deterministic():
    from app.radius.services import site_exit_classifier as c
    samples = [
        ("speedtest.net", "domain"),
        ("8.8.8.8", "ip"),
        ("unknown.example", "domain"),
        ("", "domain"),
    ]
    first = [c.classify(*s) for s in samples]
    second = [c.classify(*s) for s in samples]
    assert first == second


def test_classifier_default_enabled_set_matches_spec():
    from app.radius.services import site_exit_classifier as c
    assert c.GROUP_SPEEDTEST_MEASUREMENT in c.DEFAULT_ENABLED_GROUPS
    assert c.GROUP_PUBLIC_IP_CHECKERS in c.DEFAULT_ENABLED_GROUPS
    assert c.GROUP_RAW_IP_TARGETS in c.DEFAULT_ENABLED_GROUPS
    # Risky ones must be off by default.
    assert c.GROUP_VPN_PROVIDER_PAGES in c.DEFAULT_DISABLED_GROUPS
    assert c.GROUP_GENERAL_PROBE_SITES in c.DEFAULT_DISABLED_GROUPS
    assert c.GROUP_MANUAL_REVIEW in c.DEFAULT_DISABLED_GROUPS


# ─── Importer ────────────────────────────────────────────────


_SEED_SAMPLE = """\
# This is a comment — must be ignored.
/ip firewall address-list

add address=speedtest.net list=speedtest
add address=whatismyip.com list=speedtest
add address=fast.com list=speedtest
add address=104.102.35.193 list=speedtest

; mikrotik also uses ; for comments sometimes
add address=expressvpn.com list=vpns
add address=google.com list=probes

# duplicates below
add address=SPEEDTEST.NET list=speedtest
add address=speedtest.net list=speedtest

# invalids
add address=*.example.com list=bad
add address=https://example.com/path list=bad
add address=0.0.0.0/0 list=disaster
add address=192.168.1.1 list=lab

# missing address field — must be ignored
add list=speedtest

# unrelated mikrotik command
/ip firewall filter add chain=forward action=accept
"""


def test_importer_parses_seed_sample():
    from app.radius.services import site_exit_importer as imp
    result = imp.parse_address_list(_SEED_SAMPLE)
    # Total `add address=...` lines that PARSED (incl. duplicates
    # and invalids, excluding the malformed `add list=...`).
    # 6 unique-valid + 2 duplicates + 4 invalids = 12.
    assert result.total_parsed == 12
    s = result.summary()
    assert s["unique_accepted"] >= 4
    assert s["duplicates"] == 2
    # invalids: *.example.com, https URL, 0.0.0.0/0, 192.168.1.1
    assert s["invalid"] == 4


def test_importer_groups_targets_correctly():
    from app.radius.services import site_exit_classifier as c
    from app.radius.services import site_exit_importer as imp
    result = imp.parse_address_list(_SEED_SAMPLE)
    by_norm = {t.normalized_value: t for t in result.accepted}
    by_norm.update(
        {t.normalized_value: t for t in result.manual_review})
    assert by_norm["speedtest.net"].group_name == \
        c.GROUP_SPEEDTEST_MEASUREMENT
    assert by_norm["whatismyip.com"].group_name == \
        c.GROUP_PUBLIC_IP_CHECKERS
    assert by_norm["fast.com"].group_name == \
        c.GROUP_SPEEDTEST_MEASUREMENT
    assert by_norm["104.102.35.193"].group_name == \
        c.GROUP_RAW_IP_TARGETS
    assert by_norm["expressvpn.com"].group_name == \
        c.GROUP_VPN_PROVIDER_PAGES
    assert by_norm["google.com"].group_name == \
        c.GROUP_GENERAL_PROBE_SITES


def test_importer_target_types_split_correctly():
    from app.radius.services import site_exit_importer as imp
    result = imp.parse_address_list(_SEED_SAMPLE)
    types = {t.normalized_value: t.target_type
              for t in (*result.accepted, *result.manual_review)}
    assert types["speedtest.net"] == "domain"
    assert types["104.102.35.193"] == "ip"


def test_importer_preserves_original_value_casing():
    from app.radius.services import site_exit_importer as imp
    content = "add address=Speedtest.NET list=foo\n"
    result = imp.parse_address_list(content)
    assert result.accepted[0].value == "Speedtest.NET"
    assert result.accepted[0].normalized_value == "speedtest.net"


def test_importer_ignores_header_blanks_comments_and_other_commands():
    from app.radius.services import site_exit_importer as imp
    content = """\
# pure noise

/ip firewall address-list

/ip firewall filter add chain=forward action=accept
; another comment
add address=speedtest.net list=x
"""
    result = imp.parse_address_list(content)
    assert result.total_parsed == 1
    assert len(result.accepted) == 1


def test_importer_idempotent_duplicates_reported_separately():
    from app.radius.services import site_exit_importer as imp
    content = (
        "add address=speedtest.net list=a\n"
        "add address=SPEEDTEST.NET list=a\n"
        "add address=speedtest.net list=b\n"
    )
    result = imp.parse_address_list(content)
    assert len(result.accepted) == 1
    assert len(result.duplicates) == 2
    # Total parsed counts every recognized add-line, not just
    # the unique ones.
    assert result.total_parsed == 3


def test_importer_invalid_entries_are_separated_with_reasons():
    from app.radius.services import site_exit_importer as imp
    content = (
        "add address=*.example.com list=x\n"
        "add address=0.0.0.0/0 list=x\n"
        "add address=https://google.com/maps list=x\n"
    )
    result = imp.parse_address_list(content)
    assert result.accepted == ()
    assert len(result.invalid) == 3
    reasons = " ".join(r.reason for r in result.invalid)
    assert "wildcard" in reasons
    assert "0.0.0.0/0" in reasons or "all traffic" in reasons
    assert "URL" in reasons or "scheme" in reasons


def test_importer_returns_zero_group_counts_for_empty_input():
    from app.radius.services import site_exit_importer as imp
    result = imp.parse_address_list("")
    s = result.summary()
    assert s["total_parsed"] == 0
    assert s["unique_accepted"] == 0
    assert all(v == 0 for v in s["group_counts"].values())


def test_importer_advanced_mode_lets_private_ips_through():
    from app.radius.services import site_exit_classifier as c
    from app.radius.services import site_exit_importer as imp
    content = "add address=10.0.0.1 list=lab\n"
    strict = imp.parse_address_list(content)
    advanced = imp.parse_address_list(content, advanced_mode=True)
    assert len(strict.accepted) == 0
    assert len(strict.invalid) == 1
    assert len(advanced.accepted) == 1
    assert advanced.accepted[0].group_name == c.GROUP_RAW_IP_TARGETS


def test_importer_group_counts_match_classification():
    from app.radius.services import site_exit_classifier as c
    from app.radius.services import site_exit_importer as imp
    content = """\
add address=speedtest.net list=x
add address=fast.com list=x
add address=whatismyip.com list=x
add address=8.8.8.8 list=x
add address=expressvpn.com list=x
"""
    result = imp.parse_address_list(content)
    gc = result.group_counts
    assert gc[c.GROUP_SPEEDTEST_MEASUREMENT] == 2
    assert gc[c.GROUP_PUBLIC_IP_CHECKERS] == 1
    assert gc[c.GROUP_RAW_IP_TARGETS] == 1
    assert gc[c.GROUP_VPN_PROVIDER_PAGES] == 1


def test_importer_does_not_touch_the_db():
    """No fixture, no app context — if the importer secretly
    tried to call DB code we'd get a RuntimeError. Calling it
    here proves the contract: pure function, zero side effects."""
    from app.radius.services import site_exit_importer as imp
    # If this raises, the importer broke its pure-function
    # contract.
    imp.parse_address_list("add address=example.com list=x\n")
