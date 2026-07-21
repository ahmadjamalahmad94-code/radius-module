"""CardsStore — facade فوق cards_repo (SQLite-backed)."""
from __future__ import annotations

from threading import Lock
from typing import Optional

from ..core.tenant import DEFAULT_TENANT_ID
from ..core.types import Card, CardBatch
from ..db.repos import cards_repo


def _tid() -> int:
    try:
        from flask import g
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (ImportError, RuntimeError):
        return DEFAULT_TENANT_ID


def _effective_tid(dto_tid: Optional[int]) -> int:
    """جهة الكتابة الفعليّة: الطلب الحيّ يَحكم، وإلّا فقيمة الـDTO."""
    try:
        from flask import has_request_context
        if has_request_context():
            return _tid()
    except (ImportError, RuntimeError):  # noqa: BLE001
        pass
    return int(dto_tid or _tid())


class CardsStore:
    _inst: Optional["CardsStore"] = None
    _inst_lock = Lock()

    @classmethod
    def instance(cls) -> "CardsStore":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
        return cls._inst

    def list_batches(self, *, limit: int = 100, offset: int = 0) -> list[CardBatch]:
        return cards_repo.list_batches(_tid(), limit=limit, offset=offset)

    def list_batch_operations(self, **kw) -> list[dict]:
        return cards_repo.list_batch_operations(_tid(), **kw)

    def count_batch_operations(self, **kw) -> int:
        return cards_repo.count_batch_operations(_tid(), **kw)

    def batch_operations_totals(self, **kw) -> dict:
        return cards_repo.batch_operations_totals(_tid(), **kw)

    def get_batch(self, batch_id: int) -> Optional[CardBatch]:
        return cards_repo.get_batch(_tid(), batch_id)

    def create_batch(self, b: CardBatch) -> CardBatch:
        from dataclasses import replace
        # MT31 — داخل طلبٍ حيّ الجهة المحلولة تَحكم: ``b.tenant_id`` يأتي
        # افتراضيًّا = DEFAULT_TENANT_ID (قيمة صادقة) فكانت كل حزم الكروت
        # تهبط في الجهة 1 مهما كانت شبكة مُنشئها. (انظر _effective_tid في
        # integration/sqlite_adapter.py — نفس العلّة ونفس العلاج.)
        return cards_repo.create_batch(replace(b, tenant_id=_effective_tid(b.tenant_id)))

    def update_batch(self, batch_id: int, changes: dict) -> Optional[CardBatch]:
        return cards_repo.update_batch(_tid(), batch_id, changes)

    def generate_cards_for_batch(self, *, batch_id: int, plan_id: int, count_to_make: int,
                                   username_prefix: str = "", username_suffix: str = "",
                                   username_length: int = 8,
                                   password_length: int = 6, password_charset: str = "digits",
                                   expire_at=None, progress_callback=None) -> list[Card]:
        return cards_repo.generate_cards(
            tenant_id=_tid(), batch_id=batch_id, plan_id=plan_id, count=count_to_make,
            username_prefix=username_prefix, username_suffix=username_suffix,
            username_length=username_length,
            password_length=password_length, password_charset=password_charset,
            expire_at=expire_at, progress_callback=progress_callback,
        )

    def list_cards(self, **kw) -> list[Card]:
        return cards_repo.list_cards(_tid(), **kw)

    def count_cards(self, **kw) -> int:
        """R10.4: عدّ الكروت لاستخدام الـ UI في pagination."""
        return cards_repo.count_cards(_tid(), **kw)

    def cards_status_counts(self, **kw) -> dict:
        """عدّادات حالات الكروت (متاح/مستخدم/منتهي/محظور) لشريط KPI."""
        return cards_repo.cards_status_counts(_tid(), **kw)

    def mark_used(self, *, username: str, mac: str = "") -> None:
        # سيُستدعى من webhook لاحقًا — نمرّر الآن بدون فعل
        pass

    def revoke(self, card_id: int) -> None:
        cards_repo.revoke_card(_tid(), card_id)

    def stats(self) -> dict:
        return cards_repo.stats(_tid())
