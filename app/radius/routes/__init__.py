"""
Routes module — Flask blueprint(s) لوحدة RADIUS.

التسجيل يتم في `app/radius/routes/blueprint.py` ويُربط في
`app/legacy.py` فقط (راجع docs/INTEGRATION.md). لا تستورد من هنا
في legacy.py مباشرة — استخدم get_radius_blueprint().
"""

from .blueprint import get_radius_blueprint  # noqa: F401
