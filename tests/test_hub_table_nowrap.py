# -*- coding: utf-8 -*-
"""Owner rule: every unified-table cell renders on ONE line — no stacked
words in names or any other column («كل خلية عسطر واحد مش كلمات فوق بعض»),
across subscribers / online / cards / everywhere.

The rule lives centrally on ``.hub-table`` in hub_v2.css (all unified tables
carry that class inside the horizontally-scrolling ``.hub-table-wrap``).
Cells that genuinely need wrapping opt out with the ``.wrap`` class.
"""
from __future__ import annotations

import os
import re

_CSS = os.path.join(os.path.dirname(__file__), "..", "app", "static", "css",
                    "hub_v2.css")


def _rule_block(src: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", src)
    assert m, f"selector {selector!r} missing from hub_v2.css"
    return m.group(1)


def test_hub_table_cells_are_single_line():
    with open(_CSS, encoding="utf-8") as fh:
        src = fh.read()
    assert "white-space: nowrap" in _rule_block(src, ".hub-table tbody td")
    assert "white-space: nowrap" in _rule_block(src, ".hub-table thead th")


def test_wrap_optout_exists():
    with open(_CSS, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(
        r"\.hub-table thead th\.wrap,\s*\.hub-table tbody td\.wrap\s*"
        r"\{\s*white-space:\s*normal;?\s*\}", src)
    assert m, "the .wrap opt-out rule must exist for intentional wrapping"
