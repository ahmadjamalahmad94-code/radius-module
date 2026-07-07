"""MikroTikActiveSessionReconciler — reconcile HobeRadius online-session
state against the router's REAL active list *before* a disconnect/CoA, so we
never send a Disconnect-Request built from stale ``radacct`` attributes.

Why this exists
---------------
``radius_coa.find_all_nas_for_sessions`` reads the session keys it puts into a
Disconnect-Request — ``Acct-Session-Id`` / ``Framed-IP-Address`` /
``Calling-Station-Id`` — straight from ``radacct`` (the FreeRADIUS accounting
table). After a tunnel loss, a router reboot, a FreeRADIUS restart, an
accounting gap, or an imported/phantom ``radacct`` dump, those rows can carry
**stale** IP/MAC/session-id values (or be leftovers from a session the router
already replaced). MikroTik then rejects the packet with::

    Radius disconnect request has wrong attributes

The background ``mt_reconciler`` worker already closes ghosts and materialises
missing sessions, but it deliberately **never rewrites** ``framedipaddress`` /
``callingstationid`` on *real* RADIUS rows (a real row "wins untouched"), so the
disconnect path keeps reading the stale keys.

This service fetches the router's live ``/ip/hotspot/active`` (+ ``/ppp/active``)
list on demand, normalises each session into a canonical shape, matches the
target using the strongest keys available, enriches ``Acct-Session-Id`` from a
*matching* ``radacct`` row (only when it agrees with the live IP/MAC), and
returns a typed outcome. ``radius_coa.disconnect_user`` consults it and builds
the packet from the **verified-live** attributes, refusing to send when it
cannot identify an exact active session.

Design notes
------------
* Read-only against the router (``print`` verbs) — no accounting/auth change.
* ``fetch_active`` is injectable so tests never need a live RouterOS.
* A short per-router TTL cache keeps repeated disconnect clicks from hammering
  the API; the TTL is well under the reconciler's own cadence.
* A router that fails to answer is reported as *unreachable* — the caller then
  decides whether a freshness-gated ``radacct`` fallback is acceptable, rather
  than this service silently sending on stale data.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

_LOG = logging.getLogger(__name__)

# ─────────────── admin-visible outcome codes ───────────────
ERR_SESSION_NOT_ACTIVE = "session_not_active"
ERR_SESSION_STALE = "session_stale_reconcile_required"
ERR_MISSING_ATTRS = "cannot_disconnect_missing_session_attributes"
ERR_ROUTER_UNREACHABLE = "router_api_unreachable"
ERR_MULTIPLE = "multiple_matching_sessions"

# Arabic labels for the panel (surfaced via flash on the disconnect route).
ERROR_LABELS_AR = {
    ERR_SESSION_NOT_ACTIVE: "لا توجد جلسة نشطة على الراوتر لهذا المستخدم.",
    ERR_SESSION_STALE: "بيانات الجلسة قديمة — أعد مصالحة الراوتر ثم حاول ثانيةً.",
    ERR_MISSING_ATTRS: "تعذّر القطع: خصائص الجلسة (IP/MAC/Session-Id) غير متوفّرة.",
    ERR_ROUTER_UNREACHABLE: "واجهة الراوتر (API) غير قابلة للوصول — تعذّرت المصالحة.",
    ERR_MULTIPLE: "أكثر من جلسة مطابقة — حدّد الجلسة بدقّة (IP/MAC/Session-Id).",
}

SOURCE_MIKROTIK_ACTIVE = "mikrotik_active"

_DEFAULT_TTL_SEC = 8


def _ttl_sec() -> int:
    raw = (os.environ.get("HOBERADIUS_ACTIVE_RECONCILE_TTL_SEC") or "").strip()
    try:
        v = int(raw)
        return max(v, 0)
    except ValueError:
        return _DEFAULT_TTL_SEC


@dataclass
class LiveSession:
    """Canonical shape for one live router session (requirement #3)."""

    tenant_id: int
    router_id: Optional[int]
    nas_address: str                 # router host/address the session lives on
    username: str
    framed_ip_address: str = ""
    calling_station_id: str = ""     # normalised MAC (AA:BB:CC:DD:EE:FF)
    acct_session_id: str = ""        # enriched from radacct when it agrees
    login_by: str = ""
    uptime: str = ""
    idle_time: str = ""
    server: str = ""
    last_seen_at: str = ""
    source: str = SOURCE_MIKROTIK_ACTIVE
    # RADIUS dial info (filled during enrichment; needed to sign the CoA packet)
    nas_secret: str = ""
    coa_dial_ip: str = ""
    coa_port: int = 3799

    def has_disconnect_keys(self) -> bool:
        """True when we hold at least one strong session key the NAS can match
        (Acct-Session-Id, or Framed-IP-Address, or Calling-Station-Id).
        User-Name alone is NOT enough — that is what produced the malformed
        packets in the first place."""
        return bool(self.acct_session_id or self.framed_ip_address
                    or self.calling_station_id)


@dataclass
class ReconcileOutcome:
    """Result of resolving live disconnect targets for a username."""

    error: str = ""                                  # "" on success, else ERR_*
    sessions: list[LiveSession] = field(default_factory=list)
    routers_queried: int = 0
    routers_reachable: int = 0
    routers_unreachable: int = 0
    fetched_active_count: int = 0

    @property
    def any_router_unreachable(self) -> bool:
        return self.routers_unreachable > 0

    @property
    def message_ar(self) -> str:
        return ERROR_LABELS_AR.get(self.error, self.error)


# ─────────────── the reconciler ───────────────


class MikroTikActiveSessionReconciler:
    """Fetch + normalise + match a tenant's live router sessions.

    Parameters
    ----------
    tenant_id:
        The tenant whose routers/sessions to reconcile.
    fetch_active:
        Injectable ``(router_cfg) -> list[dict] | None`` returning the router's
        raw active-session attribute dicts (hotspot + ppp merged), or ``None``
        when the router API is unreachable. Defaults to the live RouterOS fetch.
    router_configs:
        Injectable ``() -> list[dict]`` returning router configs (host/creds).
        Defaults to the same dual-source collector used by ``mt_reconciler``.
    now:
        Injectable clock for tests (seconds float). Defaults to ``time.monotonic``.
    """

    def __init__(
        self,
        tenant_id: int,
        *,
        fetch_active: Optional[Callable[[dict], Optional[list[dict]]]] = None,
        router_configs: Optional[Callable[[], list[dict]]] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        self.tenant_id = int(tenant_id)
        self._fetch_active = fetch_active or _default_fetch_active
        self._router_configs = router_configs or (
            lambda: _default_router_configs(self.tenant_id))
        self._now = now or time.monotonic
        # host -> (fetched_at, rows|None)
        self._cache: dict[str, tuple[float, Optional[list[dict]]]] = {}

    # ── fetch (TTL cached) ────────────────────────────────────────────────
    def _cached_rows(self, cfg: dict) -> Optional[list[dict]]:
        host = (cfg.get("host") or "").strip()
        ttl = _ttl_sec()
        hit = self._cache.get(host)
        if hit is not None and ttl > 0 and (self._now() - hit[0]) < ttl:
            return hit[1]
        rows = self._fetch_active(cfg)
        self._cache[host] = (self._now(), rows)
        return rows

    def invalidate(self) -> None:
        """Drop the TTL cache — forces the next call to re-fetch (used right
        after a disconnect to reflect the removed session)."""
        self._cache.clear()

    # ── canonical mapping ─────────────────────────────────────────────────
    def _to_live_sessions(self, cfg: dict, rows: Sequence[dict]) -> list[LiveSession]:
        from app.workers.mt_reconciler import (_norm_mac, _parse_ros_uptime)

        host = (cfg.get("host") or "").strip()
        rid = cfg.get("id")
        seen_at = _utcnow_iso()
        out: list[LiveSession] = []
        for r in rows or []:
            # hotspot uses user/mac-address/address; ppp uses name/caller-id.
            user = (r.get("user") or r.get("name") or "").strip()
            if not user:
                continue
            mac = _norm_mac(r.get("mac-address") or r.get("caller-id") or "")
            out.append(LiveSession(
                tenant_id=self.tenant_id,
                router_id=int(rid) if rid is not None else None,
                nas_address=host,
                username=user,
                framed_ip_address=(r.get("address") or "").strip(),
                calling_station_id=mac,
                login_by=(r.get("login-by") or r.get("service") or "").strip(),
                uptime=(r.get("uptime") or "").strip(),
                idle_time=(r.get("idle-time") or "").strip(),
                server=(r.get("server") or "").strip(),
                last_seen_at=seen_at,
                source=SOURCE_MIKROTIK_ACTIVE,
            ))
            _ = _parse_ros_uptime  # kept importable for callers/tests
        return out

    # ── public: all live sessions (optionally for one user) ───────────────
    def live_sessions(self, username: Optional[str] = None) -> ReconcileOutcome:
        uname = (username or "").strip()
        outcome = ReconcileOutcome()
        collected: list[LiveSession] = []
        for cfg in self._router_configs():
            outcome.routers_queried += 1
            rows = self._cached_rows(cfg)
            if rows is None:
                outcome.routers_unreachable += 1
                _LOG.warning(
                    "active_reconcile: router unreachable tenant=%s host=%s",
                    self.tenant_id, cfg.get("host"))
                continue
            outcome.routers_reachable += 1
            sessions = self._to_live_sessions(cfg, rows)
            outcome.fetched_active_count += len(sessions)
            if uname:
                sessions = [s for s in sessions
                            if s.username.strip().lower() == uname.lower()]
            collected.extend(sessions)
        outcome.sessions = collected
        _LOG.info(
            "active_reconcile: tenant=%s user=%s queried=%d reachable=%d "
            "unreachable=%d fetched_active=%d matched=%d",
            self.tenant_id, uname or "*", outcome.routers_queried,
            outcome.routers_reachable, outcome.routers_unreachable,
            outcome.fetched_active_count, len(collected))
        return outcome

    # ── public: resolve exact disconnect targets ──────────────────────────
    def resolve_disconnect_targets(
        self, username: str, *,
        session_ids: Optional[list[str]] = None,
        framed_ip: Optional[str] = None,
        mac: Optional[str] = None,
    ) -> ReconcileOutcome:
        """Return live sessions to disconnect for ``username``, enriched with
        RADIUS creds + Acct-Session-Id, narrowed by the strongest selector the
        caller supplied. Sets ``outcome.error`` (ERR_*) when it can't safely
        target a live session; ``outcome.sessions`` is then empty."""
        from app.workers.mt_reconciler import _norm_mac

        uname = (username or "").strip()
        outcome = self.live_sessions(uname)

        # Router down + nothing seen → tell the caller to fall back / retry.
        if not outcome.sessions:
            if outcome.routers_reachable == 0 and outcome.any_router_unreachable:
                outcome.error = ERR_ROUTER_UNREACHABLE
            else:
                outcome.error = ERR_SESSION_NOT_ACTIVE
            return outcome

        # Enrich Acct-Session-Id + RADIUS dial creds per live session.
        for s in outcome.sessions:
            _enrich_from_radacct(s)
            _enrich_nas_creds(s)

        # Narrow by the strongest selector the caller gave (requirement #4).
        want_mac = _norm_mac(mac or "")
        want_ip = (framed_ip or "").strip()
        want_sids = {x.strip() for x in (session_ids or []) if x and x.strip()}

        selected = outcome.sessions
        if want_sids:
            # The UI passes radacct acctsessionids as the picker selector — some
            # are synthetic (`mtsync-…`, cookie sessions). Resolve each requested
            # id to its radacct MAC/IP, then select the LIVE session with that
            # MAC/IP. This targets the real session regardless of whether the
            # selector was a real or synthetic id.
            want_keys = _radacct_keys_for_session_ids(
                self.tenant_id, uname, want_sids)
            selected = [
                s for s in selected
                if s.acct_session_id in want_sids
                or (s.calling_station_id and s.calling_station_id in want_keys[0])
                or (s.framed_ip_address and s.framed_ip_address in want_keys[1])
            ]
        if want_ip:
            selected = [s for s in selected if s.framed_ip_address == want_ip]
        if want_mac:
            selected = [s for s in selected if s.calling_station_id == want_mac]

        if not selected:
            # The user IS online, but the specific session asked for is not in
            # the live set → stale request, do not send a blind packet.
            outcome.sessions = []
            outcome.error = ERR_SESSION_STALE
            return outcome

        # Every target must carry at least one strong key; drop keyless ones.
        usable = [s for s in selected if s.has_disconnect_keys()]
        if not usable:
            outcome.sessions = []
            outcome.error = ERR_MISSING_ATTRS
            return outcome

        # Only sessions we can actually sign a packet for (have a NAS secret).
        signable = [s for s in usable if s.nas_secret]
        if not signable:
            outcome.sessions = []
            outcome.error = ERR_MISSING_ATTRS
            return outcome

        outcome.sessions = signable
        outcome.error = ""
        return outcome


# ─────────────── enrichment helpers ───────────────


def _enrich_from_radacct(s: LiveSession) -> None:
    """Attach an Acct-Session-Id from an OPEN radacct row that AGREES with the
    live session (same MAC and/or Framed-IP). We never trust a radacct row that
    disagrees with the router — that is exactly the stale data we are avoiding.
    MikroTik's ``/ip/hotspot/active`` exposes only its volatile internal ``.id``,
    not the RADIUS Acct-Session-Id, so radacct is the sole source for it."""
    from app.workers.mt_reconciler import _norm_mac
    try:
        from ..db.connection import db
        rows = db().execute(
            "SELECT acctsessionid, framedipaddress, callingstationid "
            "  FROM radacct "
            " WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL",
            (s.tenant_id, s.username)).fetchall()
    except Exception:  # noqa: BLE001 — enrichment must never break disconnect
        return
    for r in rows or []:
        r_mac = _norm_mac(r["callingstationid"] or "")
        r_ip = (r["framedipaddress"] or "").strip()
        mac_ok = bool(s.calling_station_id) and r_mac == s.calling_station_id
        ip_ok = bool(s.framed_ip_address) and r_ip == s.framed_ip_address
        if mac_ok or ip_ok:
            sid = (r["acctsessionid"] or "").strip()
            # Never attach a panel-synthetic id (mt_reconciler materialises
            # cookie sessions as `mtsync-…`); it is not a real NAS Acct-Session-Id
            # and sending it would itself provoke a "wrong attributes" NAK.
            if sid and not sid.startswith("mtsync-"):
                s.acct_session_id = sid
                return


def _radacct_keys_for_session_ids(
        tenant_id: int, username: str,
        session_ids: set[str]) -> tuple[set[str], set[str]]:
    """Resolve requested acctsessionids → (set of normalised MACs, set of IPs)
    from the user's open radacct rows, so a picker selector (real OR synthetic)
    can be matched to the live session by MAC/IP."""
    from app.workers.mt_reconciler import _norm_mac
    macs: set[str] = set()
    ips: set[str] = set()
    if not session_ids:
        return macs, ips
    try:
        from ..db.connection import db
        rows = db().execute(
            "SELECT acctsessionid, framedipaddress, callingstationid "
            "  FROM radacct "
            " WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL",
            (tenant_id, username)).fetchall()
    except Exception:  # noqa: BLE001
        return macs, ips
    for r in rows or []:
        if (r["acctsessionid"] or "").strip() in session_ids:
            m = _norm_mac(r["callingstationid"] or "")
            ip = (r["framedipaddress"] or "").strip()
            if m:
                macs.add(m)
            if ip:
                ips.add(ip)
    return macs, ips


def _enrich_nas_creds(s: LiveSession) -> None:
    """Fill RADIUS shared secret + CoA dial address/port for the router this
    live session is on. Mirrors ``radius_coa.find_all_nas_for_sessions``' NAS
    lookup: match the router host against ``address`` OR ``vpn_peer_address``."""
    try:
        from ..db.connection import db
        from .nas_connection import resolve_connection_address
        host = s.nas_address
        nas_row = db().execute(
            "SELECT secret, coa_port, address, connection_mode, vpn_peer_address "
            "  FROM nas_devices "
            " WHERE tenant_id = ? AND enabled = 1 "
            "   AND (address = ? OR (vpn_peer_address = ? AND vpn_peer_address != '')) "
            " ORDER BY (address = ?) DESC LIMIT 1",
            (s.tenant_id, host, host, host)).fetchone()
    except Exception:  # noqa: BLE001
        return
    if not nas_row or not nas_row["secret"]:
        return
    s.nas_secret = nas_row["secret"]
    s.coa_dial_ip = resolve_connection_address(nas_row) or host
    try:
        s.coa_port = int(nas_row["coa_port"] or 3799)
    except (TypeError, ValueError, KeyError):
        s.coa_port = 3799


# ─────────────── default live fetch (real RouterOS) ───────────────


def _default_router_configs(tenant_id: int) -> list[dict]:
    """Reuse mt_reconciler's dual-source (mikrotik_configs + nas_devices)
    collector so we dial exactly the same routers the worker does."""
    from app.workers.mt_reconciler import _collect_router_configs
    return _collect_router_configs(int(tenant_id))


def _default_fetch_active(cfg: dict) -> Optional[list[dict]]:
    """Fetch raw hotspot + ppp active rows for one router; ``None`` on API
    failure (so the caller treats it as 'unreachable, do not send on stale')."""
    from app.radius.integration.mikrotik.errors import MikrotikError
    from app.radius.integration.mikrotik.pool import acquire as acquire_mt

    hotspot: list[dict] = []
    ppp: list[dict] = []
    try:
        with acquire_mt(cfg) as client:
            hotspot = list(client.print_("/ip/hotspot/active/print"))
            try:
                ppp = list(client.print_("/ppp/active/print"))
            except MikrotikError:
                ppp = []  # hotspot-only router
    except MikrotikError as e:
        _LOG.warning("active_reconcile: router=%s unreachable: %s",
                     cfg.get("host"), e)
        return None
    except Exception:  # noqa: BLE001
        _LOG.exception("active_reconcile: unexpected error router=%s",
                       cfg.get("host"))
        return None
    return list(hotspot) + list(ppp)


def _utcnow_iso() -> str:
    from datetime import datetime
    return datetime.utcnow().isoformat() + "Z"


def get_reconciler(tenant_id: int, **kw) -> MikroTikActiveSessionReconciler:
    """Factory — kept for parity with the other services' get_* accessors."""
    return MikroTikActiveSessionReconciler(int(tenant_id), **kw)
