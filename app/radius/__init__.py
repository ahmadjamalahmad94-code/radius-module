"""
HobeHub RADIUS Module
=====================

طبقة RADIUS الداخلية لـ HobeHub.

فلسفة الوحدة:
- HobeHub هو System of Record، وهذه الوحدة تدير منطق RADIUS فقط.
- تتعامل مع مصدر RADIUS عبر `integration.adapter.RadiusAdapter` فقط.
- لا تكتب SQL مباشر داخل routes/templates.
- كل ملف ≤ 400 سطر، كل وحدة مستقلة قابلة للاستبدال.

الوحدات:
    core/         - أنواع، ثوابت، أخطاء مشتركة (بدون I/O).
    integration/  - Adapter Layer بين HobeHub و RADIUS backend.
    devices/      - NAS Devices (إدارة).
    profiles/     - Access Profiles (خطط الباقات).
    accounting/   - جلسات المحاسبة (Accounting Sessions).
    sessions/     - الجلسات المباشرة (Online Sessions).
    policies/     - سياسات RADIUS (Policies).
    services/     - منطق التطبيق (orchestration).
    routes/       - Flask blueprints (طبقة عرض رقيقة).
    templates/    - قوالب Jinja (عرض فقط، لا منطق).
    docs/         - وثائق الوحدة (MODULE_MAP, SCHEMAS, INTEGRATION).

التسجيل في HobeHub: راجع `docs/INTEGRATION.md`.
"""

from .integration.adapter import RadiusAdapter  # noqa: F401

__all__ = ["RadiusAdapter"]
