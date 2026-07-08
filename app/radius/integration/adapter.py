"""
RadiusAdapter — العقد بين وحدة RADIUS داخل HobeHub وأي backend خارجي.

لماذا adapter بدل استدعاء RadiusClient/الـ DB مباشرة؟
- نقدر نبدّل الـ backend (manual ↔ app_ad2 ↔ MikroTik direct) بدون لمس
  أي service/route داخل وحدة RADIUS.
- نختبر منطق الـ services بـ FakeAdapter بدون شبكة/DB.
- يتماشى مع قاعدة CLAUDE.md §0.1 (services معيارية + ABC).

ملاحظات تنفيذ:
- هذا الملف يحتوي ABC فقط + factory رقيق. لا I/O هنا.
- الـ implementations الفعلية تأتي في ملفات مستقلة:
    integration/manual_adapter.py
    integration/api_adapter.py        (يلف app/services/radius_client/)
    integration/direct_adapter.py     (مستقبلًا — MikroTik API)
- كل ميثود يحتمل الفشل يرفع RadiusAdapterError أو من أحفاده.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional, Sequence

from ..core.constants import ADAPTER_MODES
from ..core.errors import RadiusConfigError
from ..core.types import (
    AccessProfile,
    AccountingSession,
    NasDevice,
    OnlineSession,
    RadiusAccount,
    RadiusPolicy,
    RadiusSettings,
)


class RadiusAdapter(ABC):
    """العقد المجرَّد. كل المتاحدثات بين services ↔ backend تمر هنا."""

    mode: str = "abstract"

    # ─────────────── Settings / Health ───────────────

    @abstractmethod
    def settings(self) -> RadiusSettings: ...

    @abstractmethod
    def healthcheck(self) -> bool: ...

    # ─────────────── NAS Devices ───────────────

    @abstractmethod
    def list_nas(self, *, limit: int = 100, offset: int = 0) -> Sequence[NasDevice]: ...

    @abstractmethod
    def get_nas(self, nas_id: int) -> NasDevice: ...

    @abstractmethod
    def upsert_nas(self, device: NasDevice) -> NasDevice: ...

    @abstractmethod
    def delete_nas(self, nas_id: int) -> None: ...

    # ─────────────── Access Profiles ───────────────

    @abstractmethod
    def list_profiles(self, *, limit: int = 100, offset: int = 0) -> Sequence[AccessProfile]: ...

    @abstractmethod
    def get_profile(self, profile_id: int) -> AccessProfile: ...

    @abstractmethod
    def upsert_profile(self, profile: AccessProfile) -> AccessProfile: ...

    @abstractmethod
    def delete_profile(self, profile_id: int) -> None: ...

    # ─────────────── Radius Accounts ───────────────

    @abstractmethod
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
    ) -> Sequence[RadiusAccount]: ...

    @abstractmethod
    def account_status_counts(
        self,
        *,
        user_type: Optional[str] = None,
        search: Optional[str] = None,
        plan_id: Optional[int] = None,
        expiring_within_days: Optional[int] = None,
        owner_admin_id: Optional[int] = None,
    ) -> dict: ...

    @abstractmethod
    def get_account(self, username: str) -> RadiusAccount: ...

    @abstractmethod
    def upsert_account(self, account: RadiusAccount) -> RadiusAccount: ...

    @abstractmethod
    def delete_account(self, username: str) -> None: ...

    @abstractmethod
    def reset_password(self, username: str, new_password: str) -> None: ...

    # ─────────────── Online Sessions ───────────────

    @abstractmethod
    def list_online(self, *, limit: int = 200) -> Sequence[OnlineSession]: ...

    @abstractmethod
    def disconnect(self, username: str, *, session_id: Optional[str] = None) -> None: ...

    # ─────────────── Accounting ───────────────

    @abstractmethod
    def list_accounting(
        self,
        *,
        username: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[AccountingSession]: ...

    # ─────────────── Policies ───────────────

    @abstractmethod
    def list_policies(self) -> Sequence[RadiusPolicy]: ...

    @abstractmethod
    def upsert_policy(self, policy: RadiusPolicy) -> RadiusPolicy: ...

    @abstractmethod
    def delete_policy(self, policy_id: int) -> None: ...

    # ─────────────── Bulk helpers (اختياري) ───────────────

    def upsert_accounts(self, accounts: Iterable[RadiusAccount]) -> list[RadiusAccount]:
        return [self.upsert_account(a) for a in accounts]

    # ─────────────── Username rename (اختياري) ───────────────

    def rename_account(self, old_username: str, new_username: str) -> dict:
        """Atomically rename a subscriber's login username across every table
        that stores it by value (auth, accounting, per-user rules …) and
        re-provision RADIUS under the new name. Concrete backends override this;
        the default declines so unsupported modes fail loudly rather than
        silently leave a half-renamed account."""
        from ..core.errors import RadiusValidationError
        raise RadiusValidationError("تغيير اسم الدخول غير مدعوم في هذا الوضع.")


# ─────────────── Factory ───────────────

_REGISTRY: dict[str, type[RadiusAdapter]] = {}


def register_adapter(mode: str, cls: type[RadiusAdapter]) -> None:
    if mode not in ADAPTER_MODES:
        raise RadiusConfigError(f"unknown adapter mode: {mode}")
    _REGISTRY[mode] = cls


def get_adapter(mode: str, **kwargs) -> RadiusAdapter:
    """تُستدعى من طبقة الـ services أو من factory أعلى مستوى.

    الـ kwargs تمر مباشرة لـ constructor الـ implementation المختار.
    """
    if mode not in _REGISTRY:
        raise RadiusConfigError(
            f"no adapter registered for mode={mode!r}. "
            f"تحقق من تحميل integration/{mode}_adapter.py"
        )
    return _REGISTRY[mode](**kwargs)
