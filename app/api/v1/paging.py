"""Shared list paging for /api/v1 — one clamp for every money list.

``min(int(limit), 500)`` let a negative limit through, and SQLite treats
``LIMIT -5`` as «no limit» (``/payments?limit=-5`` returned 1,012 rows). Every
list goes through ``page_args``: limit clamped to ``1..maximum``, offset ≥ 0,
non-numeric input → a 422 with an Arabic message (``PagingError``).
"""
from __future__ import annotations
from app.i18n_text import N_

from flask import request


class PagingError(ValueError):
    """Bad limit/offset — the view answers 422 with ``message``."""

    message = N_("قيم limit و offset يجب أن تكون أرقامًا صحيحة.")


def page_args(default: int = 100, maximum: int = 500) -> tuple[int, int]:
    try:
        raw_limit = request.args.get("limit")
        limit = int(raw_limit) if raw_limit not in (None, "") else int(default)
        raw_offset = request.args.get("offset")
        offset = int(raw_offset) if raw_offset not in (None, "") else 0
    except (TypeError, ValueError):
        raise PagingError() from None
    return max(1, min(limit, int(maximum))), max(0, offset)
