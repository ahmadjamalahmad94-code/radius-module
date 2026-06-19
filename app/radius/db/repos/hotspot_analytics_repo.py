# -*- coding: utf-8 -*-
"""hotspot_analytics_repo — أحداث تحليلات صفحة الدخول + تجميعها.

تستقبل beacon (impression/connect/click) من الصفحات المنشورة وتخزّنها
موسومة بالراوتر/القالب/النشاط/مجموعة A/B، ثم تُجمَّع للوحة التحليلات.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ..connection import db, transaction

_EVENTS = {"impression", "connect", "click"}


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def record_event(
    tenant_id: int, *, nas_id: int = 0, template_slug: str = "",
    vertical: str = "", event: str = "", ab_bucket: str = "",
) -> bool:
    """يسجّل حدثًا واحدًا. يتجاهل بصمت الأحداث المجهولة (fail-open —
    التحليلات لا تُفشل أبدًا طلب الزبون)."""
    ev = str(event or "").strip().lower()
    if ev not in _EVENTS:
        return False
    ab = str(ab_bucket or "").strip().upper()
    if ab not in ("A", "B"):
        ab = ""
    with transaction() as c:
        c.execute(
            "INSERT INTO hotspot_analytics_events "
            "(tenant_id, nas_id, template_slug, vertical, event, "
            " ab_bucket, created_at) VALUES (?,?,?,?,?,?,?)",
            (int(tenant_id), int(nas_id or 0),
             str(template_slug or "")[:80], str(vertical or "")[:40],
             ev, ab, _now()))
    return True


def _rate(connects: int, impressions: int) -> float:
    return round(100.0 * connects / impressions, 1) if impressions else 0.0


def _rollup(rows: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    """يجمّع صفوف العدّ الخام (event→count لكل مفتاح) إلى صفوف بمؤشرات."""
    agg: dict[tuple, dict] = {}
    for r in rows:
        key = tuple(r.get(k) or "" for k in key_fields)
        a = agg.setdefault(key, {"impressions": 0, "connects": 0, "clicks": 0})
        ev = r["event"]
        n = int(r["n"])
        if ev == "impression":
            a["impressions"] += n
        elif ev == "connect":
            a["connects"] += n
        elif ev == "click":
            a["clicks"] += n
    out = []
    for key, a in agg.items():
        row = {k: key[i] for i, k in enumerate(key_fields)}
        row.update(a)
        row["cvr"] = _rate(a["connects"], a["impressions"])
        out.append(row)
    out.sort(key=lambda x: x["impressions"], reverse=True)
    return out


def summary(tenant_id: int, *, nas_id: int | None = None) -> dict[str, Any]:
    """ملخّص التحليلات: إجمالي + per-template + per-vertical + per-A/B."""
    where = "WHERE tenant_id=?"
    params: list = [int(tenant_id)]
    if nas_id is not None:
        where += " AND nas_id=?"
        params.append(int(nas_id))
    rows = [dict(r) for r in db().execute(
        "SELECT template_slug, vertical, ab_bucket, event, COUNT(*) AS n "
        "FROM hotspot_analytics_events " + where + " "
        "GROUP BY template_slug, vertical, ab_bucket, event", params).fetchall()]
    totals = {"impressions": 0, "connects": 0, "clicks": 0}
    for r in rows:
        if r["event"] == "impression":
            totals["impressions"] += int(r["n"])
        elif r["event"] == "connect":
            totals["connects"] += int(r["n"])
        elif r["event"] == "click":
            totals["clicks"] += int(r["n"])
    totals["cvr"] = _rate(totals["connects"], totals["impressions"])
    return {
        "totals": totals,
        "by_template": _rollup(rows, ("template_slug",)),
        "by_vertical": _rollup(rows, ("vertical",)),
        "by_ab": _rollup(rows, ("ab_bucket",)),
    }


__all__ = ["record_event", "summary"]
