"""Setup Wizard v3 — unified single-page onboarding service.

v3 is a deliberately small state machine. Instead of v2's 13
disjoint forms each with their own validate/paste cycle, every
v3 run lives in ONE row of `setup_wizard_runs` and progresses
through eight states. The browser polls a single endpoint
(`/state`) every 2-3 seconds; the page updates accordingly.

State machine
=============

    COLLECTING            ─── operator picked router name + type
        ▼                     (POST /router-info)
    PLANNING              ─── wizard reserved an IP + generated
        ▼                     the unified `.rsc` script
                              (POST /generate-script)
    AWAITING_HANDSHAKE    ─── operator ran the script on MikroTik;
        ▼                     wizard is waiting for the first
                              WireGuard handshake
                              (paste WG output OR poll wg show)
    APPLYING_SERVER_PEER  ─── wizard extracts router's public key,
        ▼                     writes peers.d/router-N.conf
                              (POST /submit-key)
    VERIFYING             ─── wg-reload picked the file up,
        ▼                     verifying handshake succeeded
    REGISTERING           ─── wizard registers the router in
        ▼                     nas_devices + ops room
    COMPLETE              ─── done, summary page shown

    BLOCKED               ─── any state can transition here on
                              an unrecoverable error. Carries a
                              diagnostic_code the UI can render.

Each state transition is idempotent — running it twice produces
the same result. This makes the wizard recoverable: if the
browser closes mid-flow, the operator reopens the wizard, lands
on the same state, and continues.

Pure-Python module — no Flask coupling. The route layer thinly
wraps the methods below.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from ..db.connection import db, transaction


# ─── State constants ────────────────────────────────────────


STATE_COLLECTING            = "COLLECTING"
STATE_PLANNING              = "PLANNING"
STATE_AWAITING_HANDSHAKE    = "AWAITING_HANDSHAKE"
STATE_APPLYING_SERVER_PEER  = "APPLYING_SERVER_PEER"
STATE_VERIFYING             = "VERIFYING"
STATE_REGISTERING           = "REGISTERING"
STATE_COMPLETE              = "COMPLETE"
STATE_BLOCKED               = "BLOCKED"


_TERMINAL = {STATE_COMPLETE, STATE_BLOCKED}


_PUB_KEY_RE = re.compile(r"[A-Za-z0-9+/]{43}=")


# ─── Errors ────────────────────────────────────────────────


class V3Error(RuntimeError):
    """All v3 service errors derive from this."""


class V3InvalidState(V3Error):
    """Operation attempted from a state that doesn't allow it."""


class V3NotFound(V3Error):
    """The wizard run doesn't exist."""


# ─── Helpers ───────────────────────────────────────────────


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _next_short_code() -> str:
    """Public path segment for `/wz/<code>.rsc`. 8 chars of
    URL-safe random, lowercase to avoid case confusion."""
    return secrets.token_urlsafe(6)[:8].lower()


def _extract_pubkey(text: str) -> str:
    """Pull WireGuard public key from pasted `/interface
    wireguard print detail` output, or accept a bare key."""
    if not text:
        return ""
    m = re.search(
        r'public-key="?([A-Za-z0-9+/]{43}=)"?',
        text,
    )
    if m:
        return m.group(1)
    for line in text.splitlines():
        line = line.strip()
        if _PUB_KEY_RE.fullmatch(line):
            return line
    return ""


@dataclass(frozen=True)
class RunState:
    """View-model for one wizard run."""
    id:               int
    tenant_id:        int
    state:            str
    diagnostics:      list[dict[str, Any]]
    router_name:      str
    router_type:      str
    router_vpn_ip:    str
    nas_device_id:    Optional[int]
    ops_room_router_id: Optional[int]
    unified_script_short_code: Optional[str]
    handshake_first_seen_at: Optional[str]
    completed_at:     Optional[str]
    created_at:       str
    updated_at:       str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id":                  self.id,
            "tenant_id":           self.tenant_id,
            "state":               self.state,
            "diagnostics":         self.diagnostics,
            "router_name":         self.router_name,
            "router_type":         self.router_type,
            "router_vpn_ip":       self.router_vpn_ip,
            "nas_device_id":       self.nas_device_id,
            "ops_room_router_id":  self.ops_room_router_id,
            "unified_script_short_code":
                self.unified_script_short_code,
            "handshake_first_seen_at":
                self.handshake_first_seen_at,
            "completed_at":        self.completed_at,
            "created_at":          self.created_at,
            "updated_at":          self.updated_at,
            "ar_state_label":      _STATE_AR.get(
                self.state, self.state,
            ),
            "is_terminal":         self.state in _TERMINAL,
        }


_STATE_AR = {
    STATE_COLLECTING:           "جمع بيانات الراوتر",
    STATE_PLANNING:             "تجهيز السكربت",
    STATE_AWAITING_HANDSHAKE:   "بانتظار تشغيل السكربت",
    STATE_APPLYING_SERVER_PEER: "تسجيل الراوتر على الخادم",
    STATE_VERIFYING:            "التحقّق من الاتصال",
    STATE_REGISTERING:          "تسجيل الراوتر في النظام",
    STATE_COMPLETE:             "اكتمل ✓",
    STATE_BLOCKED:              "متوقّف — يحتاج تدخّل",
}


# ─── Repo layer ────────────────────────────────────────────


class V3Repo:
    """Direct SQL — keeps the service module pure."""

    def create_run(
        self, *, tenant_id: int, actor: str,
    ) -> RunState:
        now = _now()
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO setup_wizard_runs
                    (tenant_id, status, current_step,
                     state_json, v3_state,
                     v3_diagnostics_json,
                     created_by, created_at, updated_at)
                VALUES (?, 'in_progress', 'v3_collecting',
                        '{}', ?, '[]', ?, ?, ?)
                """,
                (
                    int(tenant_id),
                    STATE_COLLECTING,
                    actor,
                    now, now,
                ),
            )
            new_id = int(cur.lastrowid)
        return self.get(tenant_id, new_id)

    def get(
        self, tenant_id: int, run_id: int,
    ) -> RunState:
        row = db().execute(
            """
            SELECT * FROM setup_wizard_runs
            WHERE tenant_id=? AND id=?
            """,
            (int(tenant_id), int(run_id)),
        ).fetchone()
        if not row:
            raise V3NotFound(
                f"wizard run {run_id} not found"
            )
        keys = row.keys() if hasattr(row, "keys") else []
        data = dict(row) if not isinstance(row, dict) else row
        state_json_raw = data.get("state_json") or "{}"
        try:
            state_json = json.loads(state_json_raw)
        except (TypeError, ValueError):
            state_json = {}
        diag_raw = data.get("v3_diagnostics_json") or "[]"
        try:
            diagnostics = json.loads(diag_raw) or []
        except (TypeError, ValueError):
            diagnostics = []
        return RunState(
            id=int(data["id"]),
            tenant_id=int(data["tenant_id"]),
            state=str(data.get("v3_state") or STATE_COLLECTING),
            diagnostics=diagnostics,
            router_name=str(
                state_json.get("router_name") or ""
            ),
            router_type=str(
                state_json.get("router_type") or ""
            ),
            router_vpn_ip=str(
                state_json.get("router_vpn_ip") or ""
            ),
            nas_device_id=data.get("nas_device_id"),
            ops_room_router_id=data.get("ops_room_router_id"),
            unified_script_short_code=data.get(
                "unified_script_short_code",
            ),
            handshake_first_seen_at=data.get(
                "handshake_first_seen_at",
            ),
            completed_at=data.get("v3_completed_at"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def update_state(
        self, *,
        tenant_id: int,
        run_id: int,
        state: Optional[str] = None,
        state_json_patch: Optional[dict] = None,
        diagnostics: Optional[list] = None,
        unified_script_short_code: Optional[str] = None,
        handshake_first_seen_at: Optional[str] = None,
        nas_device_id: Optional[int] = None,
        ops_room_router_id: Optional[int] = None,
        v3_completed_at: Optional[str] = None,
    ) -> RunState:
        cur = self.get(tenant_id, run_id)
        existing_state_json = self._raw_state_json(
            tenant_id, run_id,
        )
        if state_json_patch:
            existing_state_json.update(state_json_patch)
        sets: list[str] = []
        vals: list[Any] = []
        now = _now()

        if state is not None:
            sets.append("v3_state=?")
            vals.append(state)
        sets.append("state_json=?")
        vals.append(json.dumps(existing_state_json,
                                ensure_ascii=False))
        if diagnostics is not None:
            sets.append("v3_diagnostics_json=?")
            vals.append(json.dumps(diagnostics,
                                    ensure_ascii=False))
        if unified_script_short_code is not None:
            sets.append("unified_script_short_code=?")
            vals.append(unified_script_short_code)
        if handshake_first_seen_at is not None:
            sets.append("handshake_first_seen_at=?")
            vals.append(handshake_first_seen_at)
        if nas_device_id is not None:
            sets.append("nas_device_id=?")
            vals.append(int(nas_device_id))
        if ops_room_router_id is not None:
            sets.append("ops_room_router_id=?")
            vals.append(int(ops_room_router_id))
        if v3_completed_at is not None:
            sets.append("v3_completed_at=?")
            vals.append(v3_completed_at)
        sets.append("updated_at=?")
        vals.append(now)
        vals.extend([int(tenant_id), int(run_id)])

        with transaction() as conn:
            conn.execute(
                f"UPDATE setup_wizard_runs SET "
                f"{', '.join(sets)} "
                f"WHERE tenant_id=? AND id=?",
                vals,
            )
        return self.get(tenant_id, run_id)

    def _raw_state_json(
        self, tenant_id: int, run_id: int,
    ) -> dict:
        row = db().execute(
            "SELECT state_json FROM setup_wizard_runs "
            "WHERE tenant_id=? AND id=?",
            (int(tenant_id), int(run_id)),
        ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["state_json"] or "{}") or {}
        except (TypeError, ValueError):
            return {}

    def save_unified_script(
        self, *,
        tenant_id: int, wizard_run_id: int,
        short_code: str, body: str,
        ttl_minutes: int = 60,
    ) -> dict:
        sha = hashlib.sha256(
            body.encode("utf-8"),
        ).hexdigest()
        now = _now()
        expires_at = (
            datetime.utcnow() + timedelta(minutes=ttl_minutes)
        ).isoformat() + "Z"
        with transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO setup_wizard_v3_unified_scripts
                    (tenant_id, wizard_run_id, short_code,
                     script_body, script_sha256,
                     expires_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(tenant_id), int(wizard_run_id),
                    short_code, body, sha,
                    expires_at, now,
                ),
            )
            return {
                "id":         int(cur.lastrowid),
                "short_code": short_code,
                "sha256":     sha,
                "expires_at": expires_at,
            }

    def get_unified_script_by_code(
        self, short_code: str,
    ) -> Optional[dict]:
        row = db().execute(
            "SELECT * FROM setup_wizard_v3_unified_scripts "
            "WHERE short_code=?",
            (short_code,),
        ).fetchone()
        return dict(row) if row else None

    def mark_script_fetched(
        self, *,
        short_code: str,
        user_agent: str = "",
        remote_addr: str = "",
    ) -> None:
        now = _now()
        with transaction() as conn:
            conn.execute(
                """
                UPDATE setup_wizard_v3_unified_scripts
                SET fetched_at=COALESCE(fetched_at, ?),
                    fetched_user_agent=?,
                    fetched_remote_addr=?,
                    fetch_count=fetch_count+1
                WHERE short_code=?
                """,
                (now, user_agent, remote_addr, short_code),
            )


# ─── Service ────────────────────────────────────────────────


class WizardV3Service:
    """The state machine. Each public method drives ONE
    transition. Idempotent within a state — re-running produces
    the same result. Blocked state carries diagnostic codes the
    UI maps to Arabic copy."""

    def __init__(self, *, repo: Optional[V3Repo] = None):
        self._repo = repo or V3Repo()

    # ─── Entry: create a new run ─────────────────────────

    def start_new_run(
        self, *, tenant_id: int, actor: str,
    ) -> RunState:
        return self._repo.create_run(
            tenant_id=tenant_id, actor=actor,
        )

    # ─── Polling: get current state ──────────────────────

    def get_state(
        self, *, tenant_id: int, run_id: int,
    ) -> RunState:
        return self._repo.get(tenant_id, run_id)

    # ─── State 1: COLLECTING → PLANNING ─────────────────

    def submit_router_info(
        self, *,
        tenant_id: int,
        run_id: int,
        router_name: str,
        router_type: str = "hotspot",
    ) -> RunState:
        run = self._repo.get(tenant_id, run_id)
        if run.state not in (
            STATE_COLLECTING, STATE_PLANNING,
        ):
            raise V3InvalidState(
                f"cannot submit router info from state "
                f"{run.state}"
            )
        name = (router_name or "").strip()
        if not name:
            raise V3Error("router_name is required")
        if len(name) > 64:
            raise V3Error(
                "router_name must be 64 chars or fewer"
            )
        rtype = (router_type or "hotspot").strip().lower()
        if rtype not in {"hotspot", "pppoe", "mixed"}:
            raise V3Error(
                "router_type must be hotspot/pppoe/mixed"
            )
        # Reject upfront if the name is already taken in
        # nas_devices. This catches the operator's mistake at
        # Step 1 instead of letting them walk through 5 more
        # steps only to fail at Step 6 with a cryptic
        # UNIQUE-constraint error. Tenant-scoped: same name
        # can exist under a different tenant.
        existing = db().execute(
            "SELECT id FROM nas_devices "
            "WHERE tenant_id=? AND LOWER(name)=LOWER(?) "
            "LIMIT 1",
            (int(tenant_id), name),
        ).fetchone()
        if existing:
            raise V3Error(
                f"اسم الراوتر «{name}» مستخدم من قبل في "
                f"NAS. اختر اسماً آخر."
            )
        return self._repo.update_state(
            tenant_id=tenant_id, run_id=run_id,
            state=STATE_PLANNING,
            state_json_patch={
                "router_name": name,
                "router_type": rtype,
            },
        )

    # ─── State 2: PLANNING → AWAITING_HANDSHAKE ─────────

    def generate_unified_script(
        self, *,
        tenant_id: int,
        run_id: int,
        vps_public_endpoint: str,
        vps_wg_pubkey: str,
        wg_listen_port: int = 13231,
        vps_endpoint_port: int = 51820,
    ) -> dict[str, Any]:
        """Reserve a VPN IP, build the unified .rsc body, store
        it under a short code, transition to
        AWAITING_HANDSHAKE."""
        run = self._repo.get(tenant_id, run_id)
        if run.state not in (
            STATE_PLANNING, STATE_AWAITING_HANDSHAKE,
        ):
            raise V3InvalidState(
                f"cannot generate script from state "
                f"{run.state}"
            )

        # Pick the next VPN IP for this tenant — simple
        # monotone allocator. Skips any IP currently held by an
        # un-retired peer.
        router_vpn_ip = self._allocate_router_vpn_ip(
            tenant_id=tenant_id,
        )

        # Reuse already-allocated credentials if the operator
        # regenerates the script on the same run, otherwise
        # mint fresh ones. All stored in state_json so later
        # steps (hotspot, broadband, register-router) read the
        # same values — no operator input needed.
        existing_state = self._repo._raw_state_json(
            tenant_id, run_id,
        )
        radius_secret = str(
            existing_state.get("radius_secret") or "",
        ).strip()
        api_user = str(
            existing_state.get("api_user") or "",
        ).strip()
        api_password = str(
            existing_state.get("api_password") or "",
        ).strip()
        if not radius_secret or not api_user or not api_password:
            import secrets as _secrets
            if not radius_secret:
                radius_secret = _secrets.token_hex(16)
            if not api_user:
                api_user = f"hr-api-{run_id}"
            if not api_password:
                # 24 chars of base64-url — safe for RouterOS
                # password field (no quotes, no shell metachars).
                api_password = (
                    _secrets.token_urlsafe(18)
                    .replace("-", "x").replace("_", "y")
                )

        short_code = _next_short_code()
        body = self._render_unified_script(
            run_id=run_id,
            router_vpn_ip=router_vpn_ip,
            vps_public_endpoint=vps_public_endpoint,
            vps_wg_pubkey=vps_wg_pubkey,
            wg_listen_port=wg_listen_port,
            vps_endpoint_port=vps_endpoint_port,
            short_code=short_code,
            radius_secret=radius_secret,
            api_user=api_user,
            api_password=api_password,
        )
        rec = self._repo.save_unified_script(
            tenant_id=tenant_id,
            wizard_run_id=run_id,
            short_code=short_code,
            body=body,
        )
        updated = self._repo.update_state(
            tenant_id=tenant_id, run_id=run_id,
            state=STATE_AWAITING_HANDSHAKE,
            state_json_patch={
                "router_vpn_ip": router_vpn_ip,
                "radius_secret": radius_secret,
                "api_user": api_user,
                "api_password": api_password,
            },
            unified_script_short_code=short_code,
        )

        # ── ATOMICITY GUARANTEE (postmortem #20) ────────────
        # Before this point, the unified script body (about to
        # be served to the router via /tool fetch) embeds a
        # /radius add line with the freshly-generated secret.
        # If we let the operator paste the script BEFORE the
        # matching wizard-run-<id>.conf exists on the server,
        # there's a race where the router has the new secret
        # but the server still has an old one (or none) —
        # every Access-Request from the router gets dropped
        # with 'invalid Message-Authenticator'.
        #
        # Fix: write the server-side clients.conf entry RIGHT
        # NOW, as part of the same logical operation that
        # generated the secret. The router + server move in
        # lockstep:
        #   1. secret generated → stored in state_json
        #   2. wizard-run-<id>.conf written + reload trigger
        #   3. router gets the same secret via /tool fetch
        # If step 2 fails, this method raises and the operator
        # never sees the script — preserving the invariant
        # 'router never holds a secret the server doesn't
        # know about'.
        server_provisioning: dict[str, Any] | None = None
        try:
            from .setup_wizard_v3_radius_server_provisioning import (
                write_client_for_run,
            )
            server_provisioning = write_client_for_run(
                run_id=run_id,
                router_vpn_ip=router_vpn_ip,
                radius_secret=radius_secret,
                shortname=(
                    self._safe_shortname(run, run_id)
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # Bail out of script generation rather than handing
            # the operator a script that will never authenticate.
            # The state stays at AWAITING_HANDSHAKE — the
            # operator can retry once the server-side issue is
            # fixed.
            import logging
            logging.getLogger(__name__).exception(
                "v3 atomic radius-server write failed "
                "for run %s — refusing to serve script",
                run_id,
            )
            raise V3Error(
                f"تعذّر كتابة إعداد RADIUS على الخادم: {exc}. "
                f"لا يمكن المتابعة لأن الراوتر سيحصل على سرّ "
                f"غير معروف على الخادم."
            ) from exc

        return {
            "run":          updated.to_dict(),
            "script":       body,
            "short_code":   short_code,
            "sha256":       rec["sha256"],
            "expires_at":   rec["expires_at"],
            "radius_secret": radius_secret,
            "api_user":     api_user,
            "api_password": api_password,
            "server_radius_provisioning": server_provisioning,
        }

    @staticmethod
    def _safe_shortname(run, run_id: int) -> str:
        """Build a FreeRADIUS-safe shortname from the operator's
        router_name. Falls back to a deterministic
        `wizard-router-<run>` when the name has no usable
        alphanum characters."""
        candidate = (run.router_name or "").strip()
        if candidate:
            return f"wizard-{candidate}-{run_id}"
        return f"wizard-router-{run_id}"

    def _allocate_router_vpn_ip(
        self, *, tenant_id: int,
    ) -> str:
        """Next free 10.10.0.x (mgmt WG pool 10.10.0.0/24). Skips 10.10.0.1
        (VPS), and any IP held by a LIVE nas_devices.vpn_peer_address or another
        in-flight v3 run. Fills the lowest gap first (2→249).

        Soft-deleted NAS are EXCLUDED (``deleted_at IS NULL``): deleting a router
        immediately frees its 10.10.0.x so the next add reuses it, instead of the
        pool only ever incrementing. Without this filter an archived NAS keeps
        its IP reserved forever and every add leaks a brand-new IP."""
        used: set[str] = {"10.10.0.1"}
        for r in db().execute(
            "SELECT vpn_peer_address FROM nas_devices "
            "WHERE tenant_id=? AND vpn_peer_address IS NOT NULL "
            "  AND vpn_peer_address != '' "
            "  AND (deleted_at IS NULL OR deleted_at = '')",
            (int(tenant_id),),
        ).fetchall():
            used.add(str(r["vpn_peer_address"]).strip())
        for r in db().execute(
            "SELECT state_json FROM setup_wizard_runs "
            "WHERE tenant_id=? AND v3_state IS NOT NULL "
            "  AND v3_state NOT IN (?,?)",
            (int(tenant_id), STATE_COMPLETE, STATE_BLOCKED),
        ).fetchall():
            try:
                sj = json.loads(r["state_json"] or "{}")
                ip = sj.get("router_vpn_ip")
                if ip:
                    used.add(str(ip).strip())
            except (TypeError, ValueError):
                pass
        for i in range(2, 250):
            cand = f"10.10.0.{i}"
            if cand not in used:
                return cand
        raise V3Error(
            "VPN IP pool exhausted (10.10.0.2-249)"
        )

    def _render_unified_script(
        self, *,
        run_id: int,
        router_vpn_ip: str,
        vps_public_endpoint: str,
        vps_wg_pubkey: str,
        wg_listen_port: int,
        vps_endpoint_port: int,
        short_code: str,
        radius_secret: str = "",
        api_user: str = "",
        api_password: str = "",
    ) -> str:
        """Build the one-paste RouterOS .rsc body.

        The script:
          * cleans up any prior HobeRadius WG config
          * creates fresh interface (generates new keys)
          * assigns the VPN IP
          * adds the peer to VPS
          * adds RADIUS server entry (10.10.0.1 + pre-allocated
            secret) — idempotent guard so re-pasting is safe
          * prints the new public key in a marker block the UI
            can highlight for the operator to copy back
        """
        vpn_tag = f"HOBERADIUS_SETUP:{run_id}:vpn"
        radius_tag = f"HOBERADIUS_SETUP:{run_id}:radius"
        api_tag = f"HOBERADIUS_SETUP:{run_id}:api"
        rtag = f"HOBERADIUS_RUN:{run_id}"
        # Management ACL block — rendered from the ONE shared source
        # (mgmt_acl.service_lockdown_lines) so the both-gateways binding is
        # byte-identical with every other generator and can never drift. A
        # WG-only value here once clobbered the SSTP path and took a router
        # offline. Tunnel-only, no WAN.
        from . import mgmt_acl as _mgmt_acl
        mgmt_lockdown_lines = _mgmt_acl.service_lockdown_lines(wg_first=True)
        lines = [
            "# ════════════════════════════════════════════════",
            "# HobeRadius Setup Wizard v3 — Unified Bootstrap",
            f"# Run: {run_id}",
            f"# Short code: {short_code}",
            f"# Assigned VPN IP: {router_vpn_ip}",
            "# This script is idempotent — running it twice is",
            "# safe. It cleans up any prior HobeRadius config",
            "# before creating fresh state.",
            "# ════════════════════════════════════════════════",
            "",
            "# Step 1 — Cleanup any stale HobeRadius config",
            f'/interface wireguard remove [find where name="hr-wg" and comment~"HOBERADIUS"]',
            f'/ip address remove [find where interface="hr-wg" and comment~"HOBERADIUS"]',
            f'/ip route remove [find where gateway="hr-wg" and comment~"HOBERADIUS"]',
            "",
            "# Step 1.5 — NTP time sync (يمنع رفض مصافحة WireGuard بسبب ساعة قديمة:",
            "# بعد انقطاع كهرباء طويل ترجع ساعة الراوتر للماضي فيرفض WG المصافحة",
            "# (طابع زمنيّ قديم / anti-replay) ويظهر «فشل الاتصال» بلا سبب واضح. NTP",
            "# يصحّح الوقت تلقائياً عند كل إقلاع ويمنع تكرار المشكلة. idempotent +",
            "# متفرّع حسب إصدار RouterOS.",
            ":local rosVer [/system resource get version]",
            ':local rosMajor [:tonum [:pick $rosVer 0 [:find $rosVer "."]]]',
            ":if ($rosMajor >= 7) do={",
            "  /system ntp client set enabled=yes mode=unicast",
            '  :if ([:len [/system ntp client servers find where address="216.239.35.0"]] = 0) do={ /system ntp client servers add address=216.239.35.0 }',
            '  :if ([:len [/system ntp client servers find where address="162.159.200.1"]] = 0) do={ /system ntp client servers add address=162.159.200.1 }',
            "} else={",
            "  /system ntp client set enabled=yes primary-ntp=216.239.35.0 secondary-ntp=162.159.200.1",
            "}",
            "",
            "# Step 2 — Create fresh WireGuard interface. A conservative 1380 MTU"
            " makes the router advertise a small TCP MSS so the panel's large"
            " writes (hotspot login.html ~80KB) fit every underlay (PPPoE/"
            "double-tunnel) → no mid-transfer 'Connection reset'.",
            f'/interface wireguard add name="hr-wg" mtu=1380 listen-port={wg_listen_port} comment="{vpn_tag} {rtag}"',
            f'/ip address add interface="hr-wg" address="{router_vpn_ip}/24" comment="{vpn_tag} {rtag}"',
            "",
            "# Step 3 — Add VPS as a WireGuard peer",
            f'/interface wireguard peers add interface="hr-wg" public-key="{vps_wg_pubkey}" endpoint-address="{vps_public_endpoint}" endpoint-port={vps_endpoint_port} allowed-address="10.10.0.1/32" persistent-keepalive=25s comment="{vpn_tag}:vps-peer {rtag}"',
            f'/ip route add dst-address="10.10.0.1/32" gateway="hr-wg" pref-src="{router_vpn_ip}" distance=1 comment="{vpn_tag} {rtag}"',
            "",
            "# Step 4a — Dedicated API user for HobeRadius",
            "# Creates an API-only user with a strong random",
            "# password (pre-generated by the wizard). Binds",
            "# api/winbox/www to BOTH mgmt gateways (WG + SSTP),",
            "# tunnel-only, so re-pasting never cuts off either",
            "# management path. Idempotent.",
            f'/user remove [find where name="{api_user}"]',
            f'/user add name="{api_user}" password="{api_password}" group=full comment="{api_tag} {rtag}"',
            "/ip service enable api",
            *mgmt_lockdown_lines,
            "",
            "# Step 4 — RADIUS server entry (idempotent)",
            "# Adds 10.10.0.1 as the RADIUS server with a",
            "# pre-allocated shared secret. The same secret must",
            "# be present in FreeRADIUS clients.conf on the VPS",
            "# (the wizard UI displays it to the operator after",
            "# script generation). Re-pasting is safe: we remove",
            "# any prior tag-matching row first, then add fresh.",
            f'/radius remove [find where comment~"HOBERADIUS_SETUP:{run_id}:radius"]',
            f'/radius add service=hotspot,ppp,login address=10.10.0.1 secret="{radius_secret}" authentication-port=1812 accounting-port=1813 src-address={router_vpn_ip} timeout=3000ms comment="{radius_tag} {rtag}"',
            "/radius incoming set accept=yes port=3799",
            "",
            "# Step 5 — Print the new public key (copy + paste back)",
            "# IMPORTANT: each pasted line in MikroTik Terminal runs",
            "# in its own scope, so `:local` from one line is not",
            "# visible in the next. We inline the read into a single",
            "# `:put` statement to keep everything on one line.",
            "/interface wireguard print where name=\"hr-wg\"",
            ":put \"\"",
            ":put \"════════════════════════════════════════════════\"",
            ":put (\"HOBERADIUS_PUBLIC_KEY=\" . [/interface wireguard get [find name=\"hr-wg\"] public-key])",
            ":put \"════════════════════════════════════════════════\"",
            ":put \"\"",
            ":put \"Copy the HOBERADIUS_PUBLIC_KEY=... line above and paste it back in the wizard.\"",
            "",
        ]
        return "\n".join(lines)

    # ─── State 3: AWAITING_HANDSHAKE → APPLYING_SERVER_PEER

    def submit_router_public_key(
        self, *,
        tenant_id: int,
        run_id: int,
        pasted_or_key: str,
    ) -> RunState:
        """Operator pastes either the bare key or the entire
        WG print output or the script's marker line. We extract
        the public key and proceed.

        The actual peer-file write happens in
        `apply_server_peer()` which is called immediately after
        — but as a separate state so the UI can render
        "writing" then "verifying" distinctly."""
        run = self._repo.get(tenant_id, run_id)
        if run.state not in (
            STATE_AWAITING_HANDSHAKE,
            STATE_APPLYING_SERVER_PEER,
            STATE_VERIFYING,
        ):
            raise V3InvalidState(
                f"cannot submit key from state {run.state}"
            )
        # Also accept the "HOBERADIUS_PUBLIC_KEY=..." marker
        # the script prints.
        candidate = pasted_or_key or ""
        m = re.search(
            r"HOBERADIUS_PUBLIC_KEY=([A-Za-z0-9+/]{43}=)",
            candidate,
        )
        if m:
            key = m.group(1)
        else:
            key = _extract_pubkey(candidate)
        if not key:
            return self._repo.update_state(
                tenant_id=tenant_id, run_id=run_id,
                diagnostics=run.diagnostics + [{
                    "code": "PUBLIC_KEY_NOT_FOUND",
                    "ar":
                        "تعذّر العثور على مفتاح WireGuard "
                        "في النص الملصق. أعد لصق المخرجات "
                        "كاملةً.",
                    "at": _now(),
                }],
            )
        return self._repo.update_state(
            tenant_id=tenant_id, run_id=run_id,
            state=STATE_APPLYING_SERVER_PEER,
            state_json_patch={
                "router_public_key": key,
            },
        )

    # ─── State 4: APPLYING_SERVER_PEER → VERIFYING ──────

    def apply_server_peer(
        self, *,
        tenant_id: int,
        run_id: int,
    ) -> RunState:
        """Write
        /etc/hoberadius/wg-peers.d/wizard-v3-<run_id>.conf with
        the captured public key. wg-reload.path picks it up
        within ~1 second."""
        run = self._repo.get(tenant_id, run_id)
        if run.state not in (
            STATE_APPLYING_SERVER_PEER,
            STATE_VERIFYING,
        ):
            raise V3InvalidState(
                f"cannot apply server peer from state "
                f"{run.state}"
            )
        raw = self._repo._raw_state_json(tenant_id, run_id)
        pubkey = str(raw.get("router_public_key") or "").strip()
        vpn_ip = str(raw.get("router_vpn_ip") or "").strip()
        if not pubkey or not vpn_ip:
            return self._repo.update_state(
                tenant_id=tenant_id, run_id=run_id,
                state=STATE_BLOCKED,
                diagnostics=run.diagnostics + [{
                    "code": "MISSING_PEER_INPUT",
                    "ar":
                        "مدخلات الـ peer ناقصة — أعد "
                        "السكربت وألصق المخرجات مجدّداً.",
                    "at": _now(),
                }],
            )
        # Write the peers.d file. Same shape the v2
        # PeersDirectoryWriteAdapter writes, so the wg-reload
        # path-unit handles both.
        from pathlib import Path
        import os
        peers_dir = Path(
            os.environ.get("HOBERADIUS_WG_PEERS_DIR")
            or "/etc/hoberadius/wg-peers.d",
        )
        try:
            peers_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return self._repo.update_state(
                tenant_id=tenant_id, run_id=run_id,
                state=STATE_BLOCKED,
                diagnostics=run.diagnostics + [{
                    "code": "PEERS_DIR_UNWRITABLE",
                    "ar":
                        f"مجلّد wg-peers.d غير قابل للكتابة: "
                        f"{exc}",
                    "at": _now(),
                }],
            )
        path = peers_dir / f"wizard-v3-{run_id}.conf"
        block = (
            f"# HOBERADIUS_RUN:{run_id} wizard-v3-server-peer\n"
            f"[Peer]\n"
            f"PublicKey = {pubkey}\n"
            f"AllowedIPs = {vpn_ip}/32\n"
            f"PersistentKeepalive = 25\n"
        )
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(block, encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            return self._repo.update_state(
                tenant_id=tenant_id, run_id=run_id,
                state=STATE_BLOCKED,
                diagnostics=run.diagnostics + [{
                    "code": "PEER_FILE_WRITE_FAILED",
                    "ar": f"تعذّرت كتابة ملف peer: {exc}",
                    "at": _now(),
                }],
            )
        # Trigger wg-reload (it watches the dir, this just
        # bumps a marker so logs show what we did).
        try:
            (peers_dir / ".reload").touch()
        except OSError:
            pass
        return self._repo.update_state(
            tenant_id=tenant_id, run_id=run_id,
            state=STATE_VERIFYING,
            state_json_patch={
                "peer_file_path": str(path),
            },
        )

    # ─── State 5: VERIFYING → REGISTERING ───────────────

    def mark_handshake_observed(
        self, *,
        tenant_id: int,
        run_id: int,
    ) -> RunState:
        """Called by the polling endpoint after it sees the
        handshake on wg0 (via wg show). Operator-facing UI calls
        this manually as a fallback ("ping worked, mark verified")."""
        run = self._repo.get(tenant_id, run_id)
        if run.state not in (STATE_VERIFYING, STATE_REGISTERING):
            raise V3InvalidState(
                f"cannot mark handshake from state {run.state}"
            )
        return self._repo.update_state(
            tenant_id=tenant_id, run_id=run_id,
            state=STATE_REGISTERING,
            handshake_first_seen_at=_now(),
        )

    # ─── State 6: REGISTERING → COMPLETE ────────────────

    def register_router_in_inventory(
        self, *,
        tenant_id: int,
        run_id: int,
        api_user: str = "admin",
        api_password: str = "",
    ) -> RunState:
        """Create the nas_devices row + ops-room entry. After
        this the wizard is COMPLETE and the operator can open
        the router in the dashboard.

        Marks the run COMPLETE with the new nas_device_id."""
        run = self._repo.get(tenant_id, run_id)
        if run.state not in (
            STATE_REGISTERING, STATE_COMPLETE,
        ):
            raise V3InvalidState(
                f"cannot register from state {run.state}"
            )
        raw = self._repo._raw_state_json(tenant_id, run_id)
        name = str(raw.get("router_name") or "").strip()
        vpn_ip = str(raw.get("router_vpn_ip") or "").strip()
        if not name or not vpn_ip:
            return self._repo.update_state(
                tenant_id=tenant_id, run_id=run_id,
                state=STATE_BLOCKED,
                diagnostics=run.diagnostics + [{
                    "code": "MISSING_REGISTRATION_INPUT",
                    "ar":
                        "بيانات تسجيل الراوتر ناقصة.",
                    "at": _now(),
                }],
            )
        # Insert into nas_devices with connection_mode='vpn'.
        # Name uniqueness was validated at Step 1
        # (submit_router_info) so a UNIQUE-constraint collision
        # here is a race or a manual nas_devices edit — in
        # either case we surface the error to the operator
        # rather than silently renaming.
        # Pull the wizard-generated RADIUS secret from state.
        # Without this, nas_devices.secret stays empty and the
        # CoA dispatcher (find_all_nas_for_sessions) skips
        # this NAS — disconnect + bandwidth-change actions
        # fail with 'no enabled nas_devices row with a
        # secret'. See postmortem #22.
        radius_secret = str(
            raw.get("radius_secret") or "",
        ).strip()
        if not radius_secret:
            import logging
            logging.getLogger(__name__).warning(
                "v3 register: state_json has no radius_secret "
                "for run %s — CoA disconnect/bandwidth will "
                "fail until the secret is populated",
                run_id,
            )

        now = _now()
        try:
            with transaction() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO nas_devices
                        (tenant_id, name, shortname, address,
                         secret, vendor, nas_type, ports,
                         snmp_community, auth_port,
                         acct_port, coa_port, api_port,
                         api_user, api_password, api_use_tls,
                         location, coordinates,
                         monitoring_enabled, description,
                         enabled, require_message_authenticator,
                         ssh_port, tags, metadata,
                         created_at, updated_at,
                         connection_mode, vpn_peer_address,
                         vpn_interface, ros_version)
                    VALUES (?, ?, ?, ?, ?, 'mikrotik',
                            'router', 0, '', 1812, 1813,
                            3799, 8728, ?, ?, 0,
                            '', '', 0, '', 1, 0, 22,
                            'wizard-v3', '{}', ?, ?,
                            'vpn', ?, 'wg0', '7')
                    """,
                    (
                        int(tenant_id), name, name[:24],
                        vpn_ip, radius_secret,
                        api_user, api_password or "",
                        now, now, vpn_ip,
                    ),
                )
                nas_id = int(cur.lastrowid)
        except Exception as exc:  # noqa: BLE001
            ar_msg = str(exc)
            if "unique" in str(exc).lower():
                ar_msg = (
                    f"اسم الراوتر «{name}» مستخدم من قبل. "
                    f"اختر اسماً مختلفاً واستأنف الإعداد."
                )
            return self._repo.update_state(
                tenant_id=tenant_id, run_id=run_id,
                state=STATE_BLOCKED,
                diagnostics=run.diagnostics + [{
                    "code": "NAS_INSERT_FAILED",
                    "ar":
                        f"تعذّر تسجيل الراوتر في NAS: {ar_msg}",
                    "at": _now(),
                }],
            )

        # ── Surface the run in the fleet dashboard ─────────
        # The fleet page reads from router_provisioning_registry.
        # Without this insert, a completed v3 run would never
        # appear in /admin/radius/setup-wizard/fleet — the
        # operator would see the wizard say "✅ done" but the
        # fleet stays empty. Idempotent: upserts by wizard
        # run id so re-completing a run doesn't duplicate.
        try:
            self._upsert_fleet_registry(
                tenant_id=tenant_id,
                wizard_run_id=run_id,
                router_label=name,
                router_vpn_ip=vpn_ip,
                api_user=api_user,
                nas_device_id=nas_id,
            )
        except Exception:  # noqa: BLE001
            # Don't fail the wizard finish on this — the
            # operator's router is already in nas_devices and
            # the tunnel is up. A missing fleet row is a
            # visibility issue, not a functional one.
            import logging
            logging.getLogger(__name__).exception(
                "v3 fleet-registry upsert failed for run %s",
                run_id,
            )

        return self._repo.update_state(
            tenant_id=tenant_id, run_id=run_id,
            state=STATE_COMPLETE,
            nas_device_id=nas_id,
            v3_completed_at=_now(),
        )

    def _upsert_fleet_registry(
        self, *,
        tenant_id: int,
        wizard_run_id: int,
        router_label: str,
        router_vpn_ip: str,
        api_user: str,
        nas_device_id: int,
    ) -> None:
        """Insert (or update) the matching row in
        router_provisioning_registry so the fleet page picks
        the run up. Permanent (no TTL) because the run has
        reached COMPLETE — the tentative reclaimer leaves
        these rows alone.

        Derives allocation_index from the last octet of the
        VPN IP (e.g. 10.10.0.13 → 13). This matches the
        convention v2 uses for the unique-index constraint."""
        # Allocation index = last octet of the assigned IP.
        try:
            last_octet = int(router_vpn_ip.rsplit(".", 1)[-1])
        except (ValueError, AttributeError):
            last_octet = int(wizard_run_id)
        now = _now()
        with transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM router_provisioning_registry "
                "WHERE tenant_id=? AND wizard_run_id=?",
                (int(tenant_id), int(wizard_run_id)),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE router_provisioning_registry SET
                         router_label=?,
                         status='verified',
                         lifecycle_state='fully_onboarded',
                         lifecycle_updated_at=?,
                         router_vpn_ip=?,
                         api_username=?,
                         tentative_expires_at='',
                         tentative_started_at='',
                         updated_at=?
                       WHERE id=?""",
                    (
                        router_label, now, router_vpn_ip,
                        api_user, now, int(existing["id"]),
                    ),
                )
                return
            conn.execute(
                """INSERT INTO router_provisioning_registry
                     (tenant_id, wizard_run_id, router_label,
                      router_identity, status, lifecycle_state,
                      lifecycle_updated_at,
                      vpn_pool_cidr, router_vpn_ip,
                      server_vpn_ip,
                      wireguard_interface_name,
                      wireguard_peer_name,
                      wireguard_public_key,
                      wireguard_private_key_ref,
                      radius_secret_ref,
                      api_username, api_password_ref,
                      allocation_index, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'verified',
                           'fully_onboarded', ?,
                           '10.10.0.0/24', ?, '10.10.0.1',
                           'hr-wg', ?, '',
                           '', '', ?, '', ?, ?, ?)""",
                (
                    int(tenant_id), int(wizard_run_id),
                    router_label, router_label[:48],
                    now, router_vpn_ip,
                    f"wizard-v3-run-{wizard_run_id}",
                    api_user, last_octet, now, now,
                ),
            )


def recover_nas_secrets_from_state_json(
    *, tenant_id: int | None = None,
) -> dict[str, Any]:
    """One-shot recovery for postmortem #22: existing wizard-
    created nas_devices rows that have empty secrets (from
    before the fix) get backfilled from state_json.

    Idempotent: only touches rows with empty secret AND a
    matching state_json.radius_secret. Writes an audit_log
    entry per change.

    Returns a structured summary suitable for the operator
    or a one-off CLI script. Safe to run any time."""
    import json as _json
    import logging
    log = logging.getLogger(__name__)

    sql = (
        "SELECT n.id, n.tenant_id, n.name, n.address, "
        "       n.metadata, w.id AS run_id, w.state_json "
        "FROM nas_devices n "
        "LEFT JOIN setup_wizard_runs w "
        "  ON w.tenant_id = n.tenant_id "
        " AND w.nas_device_id = n.id "
        "WHERE n.tags = 'wizard-v3' "
        "  AND COALESCE(n.secret, '') = ''"
    )
    params: tuple = ()
    if tenant_id is not None:
        sql += " AND n.tenant_id = ?"
        params = (int(tenant_id),)
    rows = db().execute(sql, params).fetchall()
    summary: dict[str, Any] = {
        "scanned": len(rows),
        "fixed":   [],
        "skipped": [],
    }
    for r in rows:
        try:
            state = _json.loads(r["state_json"] or "{}") or {}
        except (TypeError, ValueError):
            state = {}
        secret = str(state.get("radius_secret") or "").strip()
        if not secret:
            # If the link via nas_device_id failed, fall back
            # to matching by router_vpn_ip = nas_devices.address.
            fallback = db().execute(
                "SELECT state_json FROM setup_wizard_runs "
                "WHERE tenant_id=? AND v3_state IN "
                "  ('VERIFYING','REGISTERING','COMPLETE')",
                (int(r["tenant_id"]),),
            ).fetchall()
            for f in fallback:
                try:
                    fs = _json.loads(f["state_json"] or "{}")
                except (TypeError, ValueError):
                    fs = {}
                if (str(fs.get("router_vpn_ip") or "").strip()
                        == r["address"]):
                    secret = str(
                        fs.get("radius_secret") or "",
                    ).strip()
                    break
        if not secret:
            summary["skipped"].append({
                "nas_id": int(r["id"]),
                "name": r["name"],
                "address": r["address"],
                "reason": "no radius_secret in state_json",
            })
            continue
        with transaction() as conn:
            conn.execute(
                "UPDATE nas_devices SET secret=?, updated_at=? "
                "WHERE id=?",
                (secret, _now(), int(r["id"])),
            )
        summary["fixed"].append({
            "nas_id": int(r["id"]),
            "name": r["name"],
            "address": r["address"],
        })
        log.warning(
            "recover_nas_secrets: backfilled secret for "
            "NAS #%s (%s @ %s)",
            r["id"], r["name"], r["address"],
        )
        try:
            from .audit import RadiusAuditService
            RadiusAuditService().record(
                actor="setup_wizard_v3_recovery",
                action="wizard_nas_secret_backfilled",
                target_type="nas_device",
                target_id=str(r["id"]),
                payload={
                    "name": r["name"],
                    "address": r["address"],
                    "tenant_id": int(r["tenant_id"]),
                    "reason":
                        "empty secret on wizard-v3-tagged NAS",
                },
                severity="warning",
                result_status="recovered",
            )
        except Exception:  # noqa: BLE001
            log.warning(
                "audit failed for NAS %s recovery",
                r["id"], exc_info=True,
            )
    return summary


__all__ = [
    "STATE_COLLECTING", "STATE_PLANNING",
    "STATE_AWAITING_HANDSHAKE", "STATE_APPLYING_SERVER_PEER",
    "STATE_VERIFYING", "STATE_REGISTERING", "STATE_COMPLETE",
    "STATE_BLOCKED",
    "V3Error", "V3InvalidState", "V3NotFound",
    "RunState", "V3Repo", "WizardV3Service",
    "recover_nas_secrets_from_state_json",
]
