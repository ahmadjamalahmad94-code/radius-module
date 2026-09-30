"""fix3 — Arabic 404/409 responses for panel routes (HTML inside the panel
chrome, or JSON for AJAX) — never a raw werkzeug page or a «Redirecting…» body
with an error status (F02 L2 / D22, F01 F3)."""
from __future__ import annotations

from typing import Optional

from flask import jsonify, render_template


def status_notice(status: int, title: str, message: str, *,
                  back_url: Optional[str] = None, back_label: Optional[str] = None,
                  code: Optional[str] = None, **extra):
    from .blueprint import _wants_json_response
    if _wants_json_response():
        body = {"ok": False, "error": message, "code": code or str(status)}
        body.update(extra)
        return jsonify(body), status
    try:
        return render_template("radius/status_notice.html", notice_status=status,
                               notice_title=title, notice_message=message,
                               back_url=back_url, back_label=back_label), status
    except Exception:  # noqa: BLE001 — never 500 over chrome
        return (f'<h1 dir="rtl">{title}</h1><p dir="rtl">{message}</p>', status,
                {"Content-Type": "text/html; charset=utf-8"})


__all__ = ["status_notice"]
