# -*- coding: utf-8 -*-
"""Speed-pair bidi guard — «7.5 Mbps / 7.5 Mbps» must be LTR-isolated.

The live defect: in the RTL admin pages a down/up speed pair is a Latin
digits+slash run; without isolation the Unicode bidi algorithm reshuffles
it into «Mbps / 7.5 Mbps 7.5» (seen on /online «سرعة العرض/السرعة الحالية»).
The fix wraps every such pair in a dir="ltr" element. This guard keeps the
wrappers from regressing — it asserts, at template-source level, that each
known speed-pair render site sits inside a dir="ltr" wrapper.
"""
from __future__ import annotations

import os
import re

BASE = os.path.join(os.path.dirname(__file__), "..", "app", "templates", "radius")


def _src(name: str) -> str:
    with open(os.path.join(BASE, name), encoding="utf-8") as fh:
        return fh.read()


def test_sessions_list_speed_cells_are_ltr_isolated():
    src = _src("sessions_list.html")
    # «سرعة العرض» — the plan pair lives inside a dir="ltr" span.
    assert re.search(
        r'<span dir="ltr">\{\{ fmt_speed\(s\.plan_down_kbps', src), \
        "plan speed pair lost its dir=ltr isolation"
    # «السرعة الحالية» — the applied pair's span carries dir="ltr".
    assert re.search(
        r'<span dir="ltr" class="online-cur-speed', src), \
        "current speed pair lost its dir=ltr isolation"


def test_other_speed_pair_sites_are_ltr_isolated():
    expectations = {
        "bandwidth_schedules.html":
            r'CIR <span dir="ltr">\{\{ item\.get\(\'cir_down_kbps\'\)',
        "_speed_schedules_panel.html":
            r'CIR <span dir="ltr">\{\{ item\.get\(\'cir_down_kbps\'\)',
        "sgrp_list.html":
            r'<span dir="ltr">\{\{ g\.shared_speed_down_kbps \}\}',
        "cards_offers.html":
            r'<span dir="ltr">\{\{ speed\(ps\.speed_down_kbps\) \}\}',
        "cards_offer_use.html":
            r'<span class="v" dir="ltr">\{\{ speed\(ps\.speed_down_kbps\) \}\}',
        "cards_checker.html":
            r'<span dir="ltr">\{\{ card\.profile\.speed_down_kbps or 0 \}\}',
        "cards_generate.html":
            r'id="cg-mgr-speed" dir="ltr"',
    }
    for name, pattern in expectations.items():
        assert re.search(pattern, _src(name)), \
            f"{name}: speed pair lost its dir=ltr isolation"
