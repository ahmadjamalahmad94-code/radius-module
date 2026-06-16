"""reports/login-states — v1 JSON API (feat/api-first-parity, group 8).

Mirrors the web login-states reports (`routes/reports.py`,
`rep_login_states*` → `_render_login_states_detail` /
`rep_login_states` overview) as JSON. Reuses `login_events.fetch_login_events`
+ `login_states_overview` (no duplicated logic). The five "kinds" carry the
exact same actor + source-lock the web routes pin, so the API can't mix RADIUS
with portal events.
"""
from __future__ import annotations

from flask import Blueprint, g, request

from ...radius.services.login_events import (
    fetch_login_events, login_states_overview,
)
from ..auth import require_api_token
from ..responses import fail, ok

# kind → (actor, source_lock) — مطابق تمامًا لمسارات rep_login_states_*.
_KINDS = {
    "subscribers": ("subscriber", "network"),
    "cards":       ("card", "network"),
    "sub_portal":  ("subscriber", "portal"),
    "card_store":  ("card", "portal"),
    "admin":       ("admin", ""),
}


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1))


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/reports/login-states", "reports_login_states_overview",
                    require_api_token(overview), methods=["GET"])
    bp.add_url_rule("/reports/login-states/<kind>", "reports_login_states_detail",
                    require_api_token(detail), methods=["GET"])


def overview():
    """GET /reports/login-states — البطاقات المصغّرة (يطابق rep_login_states)."""
    return ok({"overview": login_states_overview(_tid()), "kinds": list(_KINDS)})


def detail(kind: str):
    """GET /reports/login-states/<kind> — تفصيل قسم واحد (يطابق
    _render_login_states_detail). kind ∈ subscribers|cards|sub_portal|
    card_store|admin. الفلاتر: result, source, q, date_from, date_to.

    source مثبّت على مستوى المسار (source_lock) للأقسام الخمسة عدا admin —
    لا يُتجاوز من الـquery (يمنع خلط RADIUS بالبوابة، مطابقة للويب)."""
    if kind not in _KINDS:
        return fail("not_found", "قسم حالات الدخول غير معروف.", status=404,
                    details={"kinds": list(_KINDS)})
    actor, source_lock = _KINDS[kind]
    a = request.args
    effective_source = source_lock or (a.get("source") or "").strip()
    data = fetch_login_events(
        _tid(),
        actor=actor,
        result=(a.get("result") or "").strip(),
        source=effective_source,
        q=(a.get("q") or "").strip(),
        date_from=(a.get("date_from") or "").strip(),
        date_to=(a.get("date_to") or "").strip(),
    )
    return ok({
        "kind": kind,
        "actor": actor,
        "source_locked": bool(source_lock),
        "rows": data["rows"],
        "stats": data["stats"],
        "shown": data["shown"],
        "matched": data["matched"],
    })
