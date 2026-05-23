"""NPC Phase 1 — pure domain analyzer tests."""
from __future__ import annotations


# ─── No-side-effects contract ────────────────────────────────


def test_module_has_no_side_effects_on_import():
    import importlib
    import app.radius.services.npc_domain_analyzer as m
    importlib.reload(m)
    # Module-level constants exist and the public API is
    # importable without any app context.
    assert m.KIND_DOMAIN == "domain"
    assert m.KIND_INVALID == "invalid"
    assert callable(m.analyze_line)
    assert callable(m.analyze_text)


# ─── Domain classification ───────────────────────────────────


def test_simple_domain_accepted_and_lowercased():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("TikTok.COM")
    assert e.kind == a.KIND_DOMAIN
    assert e.normalized == "tiktok.com"
    assert e.reason == ""


def test_trailing_dot_stripped_in_normalized():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("example.com.")
    assert e.kind == a.KIND_DOMAIN
    assert e.normalized == "example.com"


def test_url_scheme_is_stripped_and_host_used():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("https://example.com/path?x=1")
    assert e.kind == a.KIND_DOMAIN
    assert e.normalized == "example.com"
    assert "stripped scheme" in e.note


def test_url_with_port_classified_to_host():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("http://example.com:8080/")
    assert e.kind == a.KIND_DOMAIN
    assert e.normalized == "example.com"


def test_invalid_hostname_rejected_with_reason():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("just_garbage_no_dot")
    assert e.kind == a.KIND_INVALID
    assert "نطاق/IP/CIDR" in e.reason


def test_wildcards_rejected_outright():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("*.tiktok.com")
    assert e.kind == a.KIND_INVALID
    assert "النجمية" in e.reason


# ─── IPv4 ────────────────────────────────────────────────────


def test_bare_public_ipv4_accepted():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("8.8.8.8")
    assert e.kind == a.KIND_IP
    assert e.normalized == "8.8.8.8"


def test_private_ipv4_rejected_with_reason():
    from app.radius.services import npc_domain_analyzer as a
    for raw in ("10.0.0.5", "192.168.1.1", "172.16.0.1",
                "127.0.0.1"):
        e = a.analyze_line(raw)
        assert e.kind == a.KIND_INVALID
        assert "محلية" in e.reason or "خاصة" in e.reason


# ─── CIDR ────────────────────────────────────────────────────


def test_public_cidr_accepted_and_canonicalised():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("8.8.8.0/24")
    assert e.kind == a.KIND_CIDR
    # The network address gets normalized.
    assert e.normalized == "8.8.8.0/24"


def test_cidr_with_host_bits_set_still_accepted_and_clipped():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("8.8.8.7/24")
    assert e.kind == a.KIND_CIDR
    assert e.normalized == "8.8.8.0/24"


def test_blackhole_cidr_rejected():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("0.0.0.0/0")
    assert e.kind == a.KIND_INVALID
    assert "الإنترنت كاملاً" in e.reason


def test_private_cidr_rejected():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("10.0.0.0/8")
    assert e.kind == a.KIND_INVALID


# ─── IPv6 + edge cases ───────────────────────────────────────


def test_ipv6_rejected_with_specific_reason():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("2001:db8::1")
    assert e.kind == a.KIND_INVALID
    assert "IPv6" in e.reason


def test_empty_line_rejected_with_empty_reason():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("   ")
    assert e.kind == a.KIND_INVALID
    assert e.reason == a.REASON_EMPTY


def test_inline_hash_comment_stripped():
    from app.radius.services import npc_domain_analyzer as a
    e = a.analyze_line("tiktok.com # block this")
    assert e.kind == a.KIND_DOMAIN
    assert e.normalized == "tiktok.com"


# ─── Batch API ───────────────────────────────────────────────


def test_analyze_text_splits_and_classifies_each_line():
    from app.radius.services import npc_domain_analyzer as a
    text = """
        tiktok.com
        8.8.8.8
        10.0.0.5
        not-a-domain
        # whole line comment that is not a destination
        https://api.payments.test/v1
        2001:db8::1
        8.8.8.0/24
    """
    out = a.analyze_text(text)
    # 6 valid attempts after blank-line stripping; the comment
    # line classifies as 'not-a-domain'-style invalid via the
    # bare-text path, so we end up:
    #   accepted: tiktok.com, 8.8.8.8, api.payments.test,
    #             8.8.8.0/24            = 4
    #   rejected: 10.0.0.5, not-a-domain, # ..., 2001:db8::1
    #                                       = 4
    assert len(out.accepted) == 4
    assert len(out.rejected) == 4
    kinds = {e.normalized for e in out.accepted}
    assert "tiktok.com" in kinds
    assert "8.8.8.8" in kinds
    assert "api.payments.test" in kinds
    assert "8.8.8.0/24" in kinds


def test_analyze_text_blank_lines_are_silently_dropped():
    from app.radius.services import npc_domain_analyzer as a
    out = a.analyze_text("\n\n   \n")
    assert out.total == 0


def test_kind_counts_aggregate():
    from app.radius.services import npc_domain_analyzer as a
    out = a.analyze_text(
        "tiktok.com\nfacebook.com\n8.8.8.8\nnot a domain\n"
    )
    counts = out.kind_counts()
    assert counts["domain"] == 2
    assert counts["ip"] == 1
    assert counts["invalid"] == 1
