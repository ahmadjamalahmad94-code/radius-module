"""وضع الاستضافة المفتوحة (open hosting) — MT16.

نسخة الاستضافة متعددة الجهات تعمل **بلا مفتاح ترخيص**: كل خدمات وقدرات
المزوّد متاحة، لا بوابة دورة حياة ترخيص، ولا حدّ على عدد الجهات. الحدود
الوحيدة هي التي يضبطها **مالك الاستضافة لكل جهة** (max_subscribers/max_nas
المخزّنة على كل جهة) — تبقى مُنفَّذة كما هي.

يُفعَّل بمتغيّر البيئة ``HOBERADIUS_OPEN_HOSTING`` (1/true/yes/on). معطّل
افتراضيًّا كي لا يمسّ النسخ المرخّصة أحادية الجهة إطلاقًا.

نقاط الإنفاذ (كلها تقصر دائرتها عند التفعيل):
  • provider_grant.lookup → كل خدمة/قدرة present+enabled+active.
  • license_lifecycle.evaluate → ACTIVE دائمًا (لا NEVER_ACTIVATED).
  • tenants._install_entity_limit → بلا حد على عدد الجهات.
"""
from __future__ import annotations

import os

_TRUE = {"1", "true", "t", "yes", "on"}


def open_hosting() -> bool:
    """True حين تعمل النسخة في وضع الاستضافة المفتوحة (بلا ترخيص)."""
    return (os.environ.get("HOBERADIUS_OPEN_HOSTING") or "").strip().lower() in _TRUE
