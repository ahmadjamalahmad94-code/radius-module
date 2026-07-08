"""
ManualAdapter — تنفيذ in-memory للـ RadiusAdapter.

الاستخدام:
- التطوير المحلي قبل ربط الـ API.
- اختبارات الـ services بدون شبكة.
- وضع `RADIUS_MODE=manual` الحالي في HobeHub.

ملاحظات:
- البيانات تختفي عند إعادة التشغيل (مقصود لـ M2).
- M2.5 لاحقًا قد يبدّل التخزين بـ SQLite صغير في instance/.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from itertools import count
from typing import Optional, Sequence

from ..core.constants import (
    ADAPTER_MODE_MANUAL,
    STATUS_ENABLED,
)
from ..core.errors import RadiusConflict, RadiusNotFound, RadiusValidationError
from ..core.types import (
    AccessProfile,
    AccountingSession,
    NasDevice,
    OnlineSession,
    RadiusAccount,
    RadiusPolicy,
    RadiusSettings,
)
from .adapter import RadiusAdapter, register_adapter


class ManualAdapter(RadiusAdapter):
    mode = ADAPTER_MODE_MANUAL

    def __init__(self) -> None:
        self._nas: dict[int, NasDevice] = {}
        self._profiles: dict[int, AccessProfile] = {}
        self._accounts: dict[str, RadiusAccount] = {}
        self._policies: dict[int, RadiusPolicy] = {}
        self._accounting: list[AccountingSession] = []
        self._nas_seq = count(1)
        self._profile_seq = count(1)
        self._account_seq = count(1)
        self._policy_seq = count(1)

    # ─────────────── Settings / Health ───────────────

    def settings(self) -> RadiusSettings:
        return RadiusSettings(
            mode=self.mode,
            api_ready=False,
            api_writes_enabled=False,
            base_url="",
            timeout_sec=0,
        )

    def healthcheck(self) -> bool:
        return True

    # ─────────────── NAS Devices ───────────────

    def list_nas(self, *, limit: int = 100, offset: int = 0) -> Sequence[NasDevice]:
        items = list(self._nas.values())
        items.sort(key=lambda d: d.id or 0)
        return items[offset : offset + limit]

    def get_nas(self, nas_id: int) -> NasDevice:
        try:
            return self._nas[nas_id]
        except KeyError as exc:
            raise RadiusNotFound(f"NAS {nas_id} not found") from exc

    def upsert_nas(self, device: NasDevice) -> NasDevice:
        if not device.name or not device.address:
            raise RadiusValidationError("name and address are required")
        now = datetime.utcnow()
        if device.id is None:
            # تأكد من تفرّد الاسم
            if any(d.name == device.name for d in self._nas.values()):
                raise RadiusConflict(f"NAS name {device.name!r} already exists")
            new_id = next(self._nas_seq)
            saved = replace(device, id=new_id, created_at=now, updated_at=now)
        else:
            if device.id not in self._nas:
                raise RadiusNotFound(f"NAS {device.id} not found")
            saved = replace(device, updated_at=now)
        self._nas[saved.id] = saved
        return saved

    def delete_nas(self, nas_id: int) -> None:
        if nas_id not in self._nas:
            raise RadiusNotFound(f"NAS {nas_id} not found")
        del self._nas[nas_id]

    # ─────────────── Access Profiles ───────────────

    def list_profiles(self, *, limit: int = 100, offset: int = 0) -> Sequence[AccessProfile]:
        items = sorted(self._profiles.values(), key=lambda p: p.id or 0)
        return items[offset : offset + limit]

    def get_profile(self, profile_id: int) -> AccessProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise RadiusNotFound(f"profile {profile_id} not found") from exc

    def upsert_profile(self, profile: AccessProfile) -> AccessProfile:
        if not profile.name:
            raise RadiusValidationError("profile name required")
        now = datetime.utcnow()
        if profile.id is None:
            if any(p.name == profile.name for p in self._profiles.values()):
                raise RadiusConflict(f"profile {profile.name!r} exists")
            new_id = next(self._profile_seq)
            saved = replace(profile, id=new_id, created_at=now, updated_at=now)
        else:
            if profile.id not in self._profiles:
                raise RadiusNotFound(f"profile {profile.id} not found")
            saved = replace(profile, updated_at=now)
        self._profiles[saved.id] = saved
        return saved

    def delete_profile(self, profile_id: int) -> None:
        if profile_id not in self._profiles:
            raise RadiusNotFound(f"profile {profile_id} not found")
        if any(a.profile_id == profile_id for a in self._accounts.values()):
            raise RadiusConflict("profile in use by accounts")
        del self._profiles[profile_id]

    # ─────────────── Radius Accounts ───────────────

    def list_accounts(
        self,
        *,
        beneficiary_id: Optional[int] = None,
        status: Optional[str] = None,
        user_type: Optional[str] = None,
        search: Optional[str] = None,
        expiring_within_days: Optional[int] = None,
        owner_admin_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[RadiusAccount]:
        items = list(self._accounts.values())
        # عزل المدير غير مدعوم في الباكند اليدويّ (بلا سلسلة موزّعين) — يُتجاهَل.
        if owner_admin_id is not None:
            items = [a for a in items if getattr(a, "manager_id", None) == owner_admin_id]
        if beneficiary_id is not None:
            items = [a for a in items if a.beneficiary_id == beneficiary_id]
        if status:
            items = [a for a in items if a.status == status]
        # R9.0: in-memory filters للتوافق مع SqliteAdapter signature.
        if user_type:
            items = [a for a in items if getattr(a, "user_type", None) == user_type]
        if expiring_within_days is not None and expiring_within_days > 0:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now + timedelta(days=int(expiring_within_days))
            items = [a for a in items
                     if getattr(a, "expire_at", None) is not None
                     and now <= a.expire_at < cutoff]
        if search:
            s = search.lower()
            items = [a for a in items
                     if s in a.username.lower()
                     or s in (getattr(a, "full_name", "") or "").lower()
                     or s in (getattr(a, "mobile", "") or "")]
        items.sort(key=lambda a: a.username)
        return items[offset : offset + limit]

    def account_status_counts(self, *, user_type: Optional[str] = None,
                              search: Optional[str] = None,
                              plan_id: Optional[int] = None,
                              expiring_within_days: Optional[int] = None,
                              owner_admin_id: Optional[int] = None) -> dict:
        # توزيع الحالات in-memory (بلا limit/offset، بلا فلتر الحالة) —
        # يطابق list_accounts للاتساق مع SqliteAdapter.
        items = list(self._accounts.values())
        if owner_admin_id is not None:
            items = [a for a in items if getattr(a, "manager_id", None) == owner_admin_id]
        if user_type:
            items = [a for a in items if getattr(a, "user_type", None) == user_type]
        if plan_id is not None:
            items = [a for a in items if getattr(a, "plan_id", None) == plan_id]
        if expiring_within_days is not None and expiring_within_days > 0:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now + timedelta(days=int(expiring_within_days))
            items = [a for a in items
                     if getattr(a, "expire_at", None) is not None
                     and now <= a.expire_at < cutoff]
        if search:
            s = search.lower()
            items = [a for a in items
                     if s in a.username.lower()
                     or s in (getattr(a, "full_name", "") or "").lower()
                     or s in (getattr(a, "mobile", "") or "")]
        by_status: dict[str, int] = {}
        for a in items:
            st = getattr(a, "status", "") or ""
            by_status[st] = by_status.get(st, 0) + 1
        return {"total": len(items), "by_status": by_status}

    def get_account(self, username: str) -> RadiusAccount:
        try:
            return self._accounts[username]
        except KeyError as exc:
            raise RadiusNotFound(f"account {username!r} not found") from exc

    def upsert_account(self, account: RadiusAccount) -> RadiusAccount:
        if not account.username or not account.password:
            raise RadiusValidationError("username and password required")
        now = datetime.utcnow()
        existing = self._accounts.get(account.username)
        if account.id is None and existing is None:
            new_id = next(self._account_seq)
            saved = replace(
                account,
                id=new_id,
                status=account.status or STATUS_ENABLED,
                created_at=now,
                updated_at=now,
            )
        else:
            base_id = account.id or (existing.id if existing else next(self._account_seq))
            saved = replace(account, id=base_id, updated_at=now)
        self._accounts[saved.username] = saved
        return saved

    def delete_account(self, username: str) -> None:
        if username not in self._accounts:
            raise RadiusNotFound(f"account {username!r} not found")
        del self._accounts[username]

    def reset_password(self, username: str, new_password: str) -> None:
        if not new_password:
            raise RadiusValidationError("new password required")
        acc = self.get_account(username)
        self._accounts[username] = replace(acc, password=new_password, updated_at=datetime.utcnow())

    def rename_account(self, old_username: str, new_username: str,
                       *, disconnect: bool = True) -> dict:
        """In-memory equivalent of the cascade rename: move the account under
        its new key and rewrite the username on any accounting rows. Rejects a
        rename into an existing key (mirrors the UNIQUE constraint)."""
        old_username = (old_username or "").strip()
        new_username = (new_username or "").strip()
        if old_username not in self._accounts:
            raise RadiusNotFound(f"account {old_username!r} not found")
        if new_username and new_username != old_username and new_username in self._accounts:
            raise RadiusValidationError(f"username {new_username!r} already exists")
        if not new_username or new_username == old_username:
            return {"tables": {}, "had_live_session": False,
                    "old": old_username, "new": old_username}
        acc = self._accounts.pop(old_username)
        self._accounts[new_username] = replace(
            acc, username=new_username, updated_at=datetime.utcnow())
        renamed = 0
        for i, sess in enumerate(self._accounting):
            if getattr(sess, "username", None) == old_username:
                self._accounting[i] = replace(sess, username=new_username)
                renamed += 1
        return {"tables": {"accounts": 1, "accounting": renamed},
                "had_live_session": False,
                "old": old_username, "new": new_username}

    # ─────────────── Online Sessions ───────────────

    def list_online(self, *, limit: int = 200) -> Sequence[OnlineSession]:
        # manual mode: لا جلسات حية
        return []

    def disconnect(self, username: str, *, session_id: Optional[str] = None) -> None:
        # no-op في الوضع اليدوي
        return None

    # ─────────────── Accounting ───────────────

    def list_accounting(
        self,
        *,
        username: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[AccountingSession]:
        items = self._accounting
        if username:
            items = [s for s in items if s.username == username]
        return items[offset : offset + limit]

    # ─────────────── Policies ───────────────

    def list_policies(self) -> Sequence[RadiusPolicy]:
        return sorted(self._policies.values(), key=lambda p: (p.priority, p.id or 0))

    def upsert_policy(self, policy: RadiusPolicy) -> RadiusPolicy:
        if not policy.name:
            raise RadiusValidationError("policy name required")
        if policy.id is None:
            if any(p.name == policy.name for p in self._policies.values()):
                raise RadiusConflict(f"policy {policy.name!r} exists")
            new_id = next(self._policy_seq)
            saved = replace(policy, id=new_id)
        else:
            if policy.id not in self._policies:
                raise RadiusNotFound(f"policy {policy.id} not found")
            saved = policy
        self._policies[saved.id] = saved
        return saved

    def delete_policy(self, policy_id: int) -> None:
        if policy_id not in self._policies:
            raise RadiusNotFound(f"policy {policy_id} not found")
        del self._policies[policy_id]


# تسجيل الوضع
register_adapter(ADAPTER_MODE_MANUAL, ManualAdapter)
