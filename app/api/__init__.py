"""
HobeRadius REST API.

كل الـ endpoints تحت `/api/v{N}/`. اليوم v1 فقط.
كل breaking change يُولّد v(N+1) جنبًا إلى جنب — v1 لا يكسر أبدًا.
"""
from .blueprint import get_api_blueprint  # noqa: F401
