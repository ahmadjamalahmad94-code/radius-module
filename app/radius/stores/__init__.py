"""
stores — in-memory persistence لكيانات الإدارة (Cards/Admins/Roles).

كيانات الـ RADIUS الفعلية (NAS/Plans/Subscribers/Sessions) تبقى في الـ adapter
لأنها قد تأتي من backend خارجي. هذه stores خاصة بـ HobeRadius كنظام إدارة.

عند P2 يُستبدل التخزين بـ SQLite دون تغيير الواجهة.
"""

from .cards_store import CardsStore  # noqa: F401
from .admins_store import AdminsStore  # noqa: F401
