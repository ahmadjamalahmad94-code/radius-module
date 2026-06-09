"""
MikrotikAdapter — يربط RadiusAdapter ABC بـ MikrotikClient.

التركيز الحالي: hotspot.
- Subscribers ↔ /ip/hotspot/user/
- AccessPlans (Profiles) ↔ /ip/hotspot/user/profile/
- OnlineSessions ↔ /ip/hotspot/active/
- NasDevices ↔ قائمة MTs المُدارة (من MikrotikConfigStore)

ملاحظات:
- نمط القراءة: نفتح اتصال لكل عملية لتجنّب stale connections في P1.
  في P2 سننقل لـ pool ثابت.
- الحقول الخاصة بـ HobeRadius (batch_id, beneficiary_ref...) تُخزَّن في
  حقل `comment` الـ MT كـ key=value pairs ثم نُحلّلها.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Iterable, Optional, Sequence

from ..core.constants import (
    ADAPTER_MODE_DIRECT,
    NAS_VENDOR_MIKROTIK,
    STATUS_DISABLED,
    STATUS_ENABLED,
)
from ..core.errors import (
    AdapterUnavailable,
    RadiusAdapterError,
    RadiusNotFound,
    RadiusValidationError,
)
from ..core.types import (
    AccessProfile,
    AccountingSession,
    NasDevice,
    OnlineSession,
    RadiusAccount,
    RadiusPolicy,
    RadiusSettings,
    Subscriber,
)
from .adapter import RadiusAdapter, register_adapter
from .mikrotik import MikrotikClient
from .mikrotik.errors import AuthError, ConnectError, MikrotikError, MikrotikTrap
from .mikrotik.settings import MikrotikConfig, MikrotikConfigStore

_LOG = logging.getLogger(__name__)


class MikrotikAdapter(RadiusAdapter):
    """ينفّذ RadiusAdapter باستخدام MikroTik API.

    multi-device support: قراءة من كل الـ MTs المفعّلة، كتابة على الـ primary.
    """

    mode = ADAPTER_MODE_DIRECT

    def __init__(self) -> None:
        self._store = MikrotikConfigStore.instance()

    # ─────────────── helpers ───────────────

    def _primary(self) -> MikrotikConfig:
        cfg = self._store.primary()
        if not cfg:
            raise AdapterUnavailable("لا يوجد اتصال MikroTik مضبوط")
        return cfg

    def _enabled(self) -> list[MikrotikConfig]:
        return [c for c in self._store.list() if c.enabled]

    def _open(self, cfg: MikrotikConfig) -> MikrotikClient:
        try:
            c = MikrotikClient(
                host=cfg.host, port=cfg.port,
                username=cfg.username, password=cfg.password,
                use_tls=cfg.use_tls, verify_tls=cfg.verify_tls,
                timeout=cfg.timeout_sec,
            )
            c.connect()
            return c
        except AuthError as e:
            raise RadiusAdapterError(f"فشل تسجيل الدخول لـ {cfg.host}: {e}") from e
        except ConnectError as e:
            raise AdapterUnavailable(f"تعذّر الاتصال بـ {cfg.host}: {e}") from e

    # ─────────────── settings / health ───────────────

    def settings(self) -> RadiusSettings:
        primary = self._store.primary()
        return RadiusSettings(
            mode=self.mode,
            api_ready=primary is not None,
            api_writes_enabled=primary is not None,
            base_url=f"{primary.host}:{primary.port}" if primary else "",
            timeout_sec=primary.timeout_sec if primary else 10,
        )

    def healthcheck(self) -> bool:
        try:
            cfg = self._primary()
        except AdapterUnavailable:
            return False
        try:
            with self._open(cfg) as c:
                return c.healthcheck()
        except MikrotikError:
            return False

    # ─────────────── NAS ───────────────

    def list_nas(self, *, limit: int = 100, offset: int = 0) -> Sequence[NasDevice]:
        items: list[NasDevice] = []
        for cfg in self._enabled()[offset : offset + limit]:
            items.append(_cfg_to_nas(cfg))
        return items

    def get_nas(self, nas_id: int) -> NasDevice:
        cfg = self._store.get(nas_id)
        if not cfg:
            raise RadiusNotFound(f"NAS {nas_id} غير موجود")
        return _cfg_to_nas(cfg)

    def upsert_nas(self, device: NasDevice) -> NasDevice:
        if device.id is None:
            saved = self._store.add(_nas_to_cfg(device))
        else:
            saved = self._store.update(device.id, **_nas_to_cfg_dict(device))
            if not saved:
                raise RadiusNotFound(f"NAS {device.id} غير موجود")
        return _cfg_to_nas(saved)

    def delete_nas(self, nas_id: int) -> None:
        self._store.delete(nas_id)

    # ─────────────── Profiles (Plans) ───────────────

    def list_profiles(self, *, limit: int = 100, offset: int = 0) -> Sequence[AccessProfile]:
        with self._open(self._primary()) as c:
            rows = list(c.print_("/ip/hotspot/user/profile/print"))
        out = [_row_to_profile(i, r) for i, r in enumerate(rows, start=1)]
        return out[offset : offset + limit]

    def get_profile(self, profile_id: int) -> AccessProfile:
        items = list(self.list_profiles(limit=10_000))
        for p in items:
            if p.id == profile_id:
                return p
        raise RadiusNotFound(f"profile {profile_id} غير موجود")

    def upsert_profile(self, profile: AccessProfile) -> AccessProfile:
        attrs = _profile_to_mt(profile)
        with self._open(self._primary()) as c:
            if profile.id is None or not _profile_exists(c, profile.name):
                c.run("/ip/hotspot/user/profile/add", attrs)
            else:
                # نُحدّث عبر name كـ key
                mt_id = _profile_mt_id(c, profile.name)
                attrs.pop("name", None)
                c.run("/ip/hotspot/user/profile/set", {**attrs, ".id": mt_id})
        return replace(profile, updated_at=datetime.utcnow())

    def delete_profile(self, profile_id: int) -> None:
        p = self.get_profile(profile_id)
        with self._open(self._primary()) as c:
            mt_id = _profile_mt_id(c, p.name)
            c.run("/ip/hotspot/user/profile/remove", {".id": mt_id})

    # ─────────────── Accounts (Subscribers) ───────────────

    def list_accounts(
        self,
        *,
        beneficiary_id: Optional[int] = None,
        status: Optional[str] = None,
        user_type: Optional[str] = None,
        search: Optional[str] = None,
        expiring_within_days: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[RadiusAccount]:
        with self._open(self._primary()) as c:
            rows = list(c.print_("/ip/hotspot/user/print"))
        out = [_row_to_subscriber(r) for r in rows]
        if status:
            out = [s for s in out if s.status == status]
        # R9.0: in-memory filters للتوافق مع SqliteAdapter signature.
        if user_type:
            out = [s for s in out if getattr(s, "user_type", None) == user_type]
        if expiring_within_days is not None and expiring_within_days > 0:
            from datetime import datetime, timedelta, timezone
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cutoff = now + timedelta(days=int(expiring_within_days))
            out = [s for s in out
                   if getattr(s, "expire_at", None) is not None
                   and now <= s.expire_at < cutoff]
        if search:
            t = search.lower()
            out = [s for s in out
                   if t in s.username.lower()
                   or t in (getattr(s, "full_name", "") or "").lower()
                   or t in (getattr(s, "mobile", "") or "")]
        return out[offset : offset + limit]

    def get_account(self, username: str) -> RadiusAccount:
        with self._open(self._primary()) as c:
            for r in c.print_("/ip/hotspot/user/print",
                              queries=[f"?name={username}"]):
                return _row_to_subscriber(r)
        raise RadiusNotFound(f"account {username!r} غير موجود")

    def upsert_account(self, account: RadiusAccount) -> RadiusAccount:
        if not account.username or not account.password:
            raise RadiusValidationError("username + password مطلوبان")
        attrs = _subscriber_to_mt(account)
        with self._open(self._primary()) as c:
            existing_id = _user_mt_id(c, account.username)
            if existing_id is None:
                c.run("/ip/hotspot/user/add", attrs)
            else:
                attrs.pop("name", None)
                c.run("/ip/hotspot/user/set", {**attrs, ".id": existing_id})
        return replace(account, updated_at=datetime.utcnow())

    def delete_account(self, username: str) -> None:
        with self._open(self._primary()) as c:
            mt_id = _user_mt_id(c, username)
            if mt_id is None:
                raise RadiusNotFound(f"account {username!r} غير موجود")
            c.run("/ip/hotspot/user/remove", {".id": mt_id})

    def reset_password(self, username: str, new_password: str) -> None:
        with self._open(self._primary()) as c:
            mt_id = _user_mt_id(c, username)
            if mt_id is None:
                raise RadiusNotFound(f"account {username!r} غير موجود")
            c.run("/ip/hotspot/user/set", {".id": mt_id, "password": new_password})

    # ─────────────── Sessions ───────────────

    def list_online(self, *, limit: int = 200) -> Sequence[OnlineSession]:
        out: list[OnlineSession] = []
        for cfg in self._enabled():
            try:
                with self._open(cfg) as c:
                    for r in c.print_("/ip/hotspot/active/print"):
                        out.append(_row_to_session(r, nas_name=cfg.name, nas_addr=cfg.host))
            except MikrotikError:
                _LOG.warning("failed to read active from %s", cfg.host, exc_info=True)
                continue
            if len(out) >= limit:
                break
        return out[:limit]

    def disconnect(self, username: str, *, session_id: Optional[str] = None) -> None:
        for cfg in self._enabled():
            try:
                with self._open(cfg) as c:
                    target_id = session_id
                    if not target_id:
                        # ابحث عن الـ active id بـ username
                        for r in c.print_("/ip/hotspot/active/print",
                                          queries=[f"?user={username}"]):
                            target_id = r.get(".id")
                            break
                    if target_id:
                        c.run("/ip/hotspot/active/remove", {".id": target_id})
                        return
            except MikrotikError:
                continue
        raise RadiusNotFound(f"جلسة {username!r} غير موجودة")

    # ─────────────── Accounting / Policies (stubs لاحقًا) ───────────────

    def list_accounting(self, *, username: Optional[str] = None,
                         limit: int = 100, offset: int = 0) -> Sequence[AccountingSession]:
        return []

    def list_policies(self) -> Sequence[RadiusPolicy]:
        return []

    def upsert_policy(self, policy: RadiusPolicy) -> RadiusPolicy:
        raise RadiusValidationError("policies غير مدعومة في MikroTik adapter بعد")

    def delete_policy(self, policy_id: int) -> None:
        return None


# ────────────────────────── mappers ──────────────────────────


def _cfg_to_nas(cfg: MikrotikConfig) -> NasDevice:
    return NasDevice(
        id=cfg.id, name=cfg.name, address=cfg.host, secret=cfg.password,
        vendor=NAS_VENDOR_MIKROTIK, nas_type="hotspot",
        auth_port=cfg.port, acct_port=cfg.port, coa_port=3799,
        enabled=cfg.enabled, monitoring_enabled=True,
    )


def _nas_to_cfg(n: NasDevice) -> MikrotikConfig:
    return MikrotikConfig(
        id=n.id, name=n.name, host=n.address,
        port=n.auth_port or 8728, username="admin", password=n.secret,
        use_tls=(n.auth_port == 8729), verify_tls=False,
        enabled=n.enabled,
    )


def _nas_to_cfg_dict(n: NasDevice) -> dict:
    return {
        "name": n.name, "host": n.address, "password": n.secret,
        "port": n.auth_port or 8728, "use_tls": (n.auth_port == 8729),
        "enabled": n.enabled,
    }


def _row_to_profile(idx: int, r: dict) -> AccessProfile:
    """يحوّل صف /ip/hotspot/user/profile إلى AccessProfile."""
    def _int(k, default=0):
        v = r.get(k)
        try:
            return int(v) if v else default
        except (TypeError, ValueError):
            return default
    # rate-limit مثل "10M/5M" أو فارغ
    up = down = 0
    rl = r.get("rate-limit") or ""
    if rl and "/" in rl:
        up_s, _, down_s = rl.partition("/")
        up, down = _parse_rate_kbps(up_s), _parse_rate_kbps(down_s)
    return AccessProfile(
        id=idx,
        name=r.get("name") or "",
        speed_up_kbps=up,
        speed_down_kbps=down,
        session_timeout_sec=_parse_duration_sec(r.get("session-timeout") or ""),
        idle_timeout_sec=_parse_duration_sec(r.get("idle-timeout") or ""),
        concurrent_sessions=_int("shared-users", 1),
        address_pool=r.get("address-pool") or "",
        description=r.get("on-login") or "",
        enabled=True,
    )


def _profile_to_mt(p: AccessProfile) -> dict:
    out: dict = {"name": p.name}
    if p.speed_down_kbps or p.speed_up_kbps:
        out["rate-limit"] = f"{p.speed_up_kbps}k/{p.speed_down_kbps}k"
    if p.session_timeout_sec:
        out["session-timeout"] = f"{p.session_timeout_sec}s"
    if p.idle_timeout_sec:
        out["idle-timeout"] = f"{p.idle_timeout_sec}s"
    if p.concurrent_sessions:
        out["shared-users"] = str(p.concurrent_sessions)
    if p.address_pool:
        out["address-pool"] = p.address_pool
    return out


def _profile_exists(c: MikrotikClient, name: str) -> bool:
    return _profile_mt_id(c, name) is not None


def _profile_mt_id(c: MikrotikClient, name: str) -> Optional[str]:
    for r in c.print_("/ip/hotspot/user/profile/print", queries=[f"?name={name}"]):
        return r.get(".id")
    return None


def _row_to_subscriber(r: dict) -> Subscriber:
    return Subscriber(
        id=None,
        username=r.get("name") or "",
        password=r.get("password") or "",
        plan_id=None,
        mac_lock=r.get("mac-address") or None,
        static_ip=r.get("address") or None,
        email=r.get("email") or "",
        remark=r.get("comment") or "",
        status=STATUS_DISABLED if (r.get("disabled") == "true") else STATUS_ENABLED,
    )


def _subscriber_to_mt(s: Subscriber) -> dict:
    out: dict = {"name": s.username, "password": s.password}
    if s.mac_lock:
        out["mac-address"] = s.mac_lock
    if s.static_ip:
        out["address"] = s.static_ip
    if s.email:
        out["email"] = s.email
    if s.remark:
        out["comment"] = s.remark
    out["disabled"] = "yes" if s.status == STATUS_DISABLED else "no"
    return out


def _user_mt_id(c: MikrotikClient, username: str) -> Optional[str]:
    for r in c.print_("/ip/hotspot/user/print", queries=[f"?name={username}"]):
        return r.get(".id")
    return None


def _row_to_session(r: dict, *, nas_name: str, nas_addr: str) -> OnlineSession:
    return OnlineSession(
        username=r.get("user") or "",
        session_id=r.get(".id") or "",
        nas_id=nas_name,
        nas_address=nas_addr,
        framed_ip=r.get("address") or "",
        mac_address=r.get("mac-address") or "",
        started_at=datetime.utcnow(),
        last_update_at=datetime.utcnow(),
        bytes_in=_safe_int(r.get("bytes-in")),
        bytes_out=_safe_int(r.get("bytes-out")),
    )


# ────────────────────────── parsers ──────────────────────────


def _safe_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _parse_rate_kbps(s: str) -> int:
    """يحوّل '10M' / '512k' / '1G' إلى kbps."""
    if not s:
        return 0
    s = s.strip()
    mul = 1
    if s[-1].lower() == "k":
        s = s[:-1]
    elif s[-1].lower() == "m":
        mul = 1000
        s = s[:-1]
    elif s[-1].lower() == "g":
        mul = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mul)
    except ValueError:
        return 0


def _parse_duration_sec(s: str) -> int:
    """يحوّل '1h30m', '45s', '10m' إلى ثواني."""
    if not s:
        return 0
    total = 0
    buf = ""
    for ch in s:
        if ch.isdigit():
            buf += ch
        else:
            n = int(buf or 0)
            if ch == "d":   total += n * 86400
            elif ch == "h": total += n * 3600
            elif ch == "m": total += n * 60
            elif ch == "s": total += n
            buf = ""
    if buf:  # رقم وحده = ثواني
        total += int(buf)
    return total


# تسجيل
register_adapter(ADAPTER_MODE_DIRECT, MikrotikAdapter)


__all__ = ["MikrotikAdapter"]
