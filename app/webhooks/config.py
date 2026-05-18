"""
WebhookConfigStore — facade فوق webhooks_repo (subscriptions في DB).

تعطي «subscription» واحدة للـ tenant ككيان «config»، حفاظًا على
توافق الكود القديم (API /webhooks/config).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Iterable, Optional

from .events import EVENT_TYPES
from app.radius.core.types_saas import WebhookSubscription


@dataclass
class WebhookConfig:
    target_url: str = ""
    secret: str = ""
    enabled_events: tuple[str, ...] = field(default_factory=lambda: tuple(EVENT_TYPES))


class WebhookConfigStore:
    _inst: Optional["WebhookConfigStore"] = None
    _inst_lock = Lock()

    @classmethod
    def instance(cls) -> "WebhookConfigStore":
        with cls._inst_lock:
            if cls._inst is None:
                cls._inst = cls()
        return cls._inst

    def _tenant_id(self) -> int:
        try:
            from flask import g
            return int(getattr(g, "tenant_id", 1))
        except (ImportError, RuntimeError):
            return 1

    def _first_or_none(self) -> Optional[WebhookSubscription]:
        from app.radius.db.repos import webhooks_repo
        subs = webhooks_repo.list_subs(self._tenant_id())
        return subs[0] if subs else None

    def get(self) -> WebhookConfig:
        s = self._first_or_none()
        if not s:
            return WebhookConfig()
        return WebhookConfig(target_url=s.target_url, secret=s.secret,
                              enabled_events=s.enabled_events)

    def update(self, *, target_url: Optional[str] = None,
               secret: Optional[str] = None,
               enabled_events: Optional[Iterable[str]] = None) -> WebhookConfig:
        from app.radius.db.repos import webhooks_repo
        tenant_id = self._tenant_id()
        s = self._first_or_none()
        if not s:
            s = WebhookSubscription(
                id=None, tenant_id=tenant_id,
                target_url=target_url or "",
                secret=secret or "",
                enabled_events=tuple(e for e in (enabled_events or EVENT_TYPES) if e in EVENT_TYPES),
                enabled=True,
            )
            saved = webhooks_repo.upsert_sub(s)
        else:
            from dataclasses import replace
            patch = {}
            if target_url is not None: patch["target_url"] = target_url
            if secret is not None: patch["secret"] = secret
            if enabled_events is not None:
                patch["enabled_events"] = tuple(e for e in enabled_events if e in EVENT_TYPES)
            saved = webhooks_repo.upsert_sub(replace(s, **patch))
        return WebhookConfig(target_url=saved.target_url, secret=saved.secret,
                              enabled_events=saved.enabled_events)
