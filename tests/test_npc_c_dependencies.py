"""NPC Phase C — dependency detector."""
from __future__ import annotations

from app.radius.services import npc_dependency_detector as dd


def test_module_has_no_side_effects():
    import importlib
    import app.radius.services.npc_dependency_detector as m
    importlib.reload(m)
    assert m.CONFIDENCE_CERTAIN == "certain"
    assert callable(m.analyze)


def _t(value, status="active"):
    return {"value": value,
            "normalized_value": value.lower(),
            "target_type": "domain",
            "category": "x", "status": status}


# ─── Empty inputs → no dependencies ──────────────────────────


def test_no_targets_returns_empty():
    out = dd.analyze(targets=[])
    assert out.dependencies == ()
    assert out.warnings_ar == ()


def test_disabled_targets_ignored():
    out = dd.analyze(
        targets=[_t("facebook.com", status="disabled")],
    )
    assert out.dependencies == ()


# ─── Known triggers ──────────────────────────────────────────


def test_facebook_triggers_meta_dependency():
    out = dd.analyze(targets=[_t("facebook.com")])
    names = {d.service_name for d in out.dependencies}
    assert any("Meta" in n for n in names)
    # Confidence is reasonable; reason is Arabic.
    dep = next(d for d in out.dependencies
                if "Meta" in d.service_name)
    assert dep.confidence == dd.CONFIDENCE_LIKELY
    assert "Messenger" in dep.impact_ar
    # related domains contain fbcdn.
    assert any("fbcdn" in r for r in dep.related_domains)


def test_tiktok_triggers_bytedance_dependency():
    out = dd.analyze(targets=[_t("tiktok.com")])
    assert any("TikTok" in d.service_name
               for d in out.dependencies)
    dep = next(d for d in out.dependencies
                if "TikTok" in d.service_name)
    assert any("bytecdn" in r or "byteoversea" in r
               for r in dep.related_domains)


def test_google_subdomain_matches_via_suffix():
    out = dd.analyze(
        targets=[_t("maps.googleapis.com")],
    )
    names = {d.service_name for d in out.dependencies}
    assert "Google" in names


def test_firebase_treated_as_google_family():
    out = dd.analyze(
        targets=[_t("myapp.firebaseio.com")],
    )
    names = {d.service_name for d in out.dependencies}
    # Either "Firebase / Google Cloud" or "Google" qualifies.
    assert any("Firebase" in n or "Google" in n for n in names)


def test_cloudflare_marked_high_confidence():
    out = dd.analyze(
        targets=[_t("cloudflareinsights.com")],
    )
    dep = next(d for d in out.dependencies
                if "Cloudflare" in d.service_name)
    assert dep.confidence == dd.CONFIDENCE_CERTAIN


def test_apple_dependency():
    out = dd.analyze(
        targets=[_t("icloud.com"), _t("apps.apple.com")],
    )
    names = {d.service_name for d in out.dependencies}
    assert "Apple" in names
    # Same service deduplicated even when multiple targets
    # trigger it.
    apple_deps = [d for d in out.dependencies
                  if d.service_name == "Apple"]
    assert len(apple_deps) == 1


def test_microsoft_dependency():
    out = dd.analyze(targets=[_t("outlook.com")])
    assert any("Microsoft" in d.service_name
               for d in out.dependencies)


# ─── Unknown domains ────────────────────────────────────────


def test_unknown_domain_yields_no_dependency():
    out = dd.analyze(targets=[_t("some-private-site.test")])
    assert out.dependencies == ()
    assert out.warnings_ar == ()


# ─── Warning surfacing ───────────────────────────────────────


def test_warning_present_when_dependencies_found():
    out = dd.analyze(targets=[_t("facebook.com")])
    assert out.warnings_ar
    assert "يدوياً" in out.warnings_ar[0]


# ─── JSON projection ─────────────────────────────────────────


def test_as_dict_shape():
    out = dd.analyze(targets=[_t("tiktok.com")])
    d = out.as_dict()
    assert isinstance(d["dependencies"], list)
    assert isinstance(d["warnings_ar"], list)
    dep0 = d["dependencies"][0]
    assert set(dep0.keys()) >= {
        "service_name", "impact_ar", "confidence",
        "reason_ar", "related_domains",
    }
