"""Customer-side bridge-token bidirectional sync.

Two flows:

  CONSUME  — panel rotates the token → runtime-contract response includes a
             bridge_token block → we store the new value encrypted at rest
             (consume_panel_token).

  REPORT   — we mint a fresh token locally, store it encrypted, and POST it
             to the panel so both sides share the same value
             (generate_and_report / ensure_token_and_report_pending).

Storage contract
----------------
* ``token_enc``   is ALWAYS a Fernet ciphertext; raw values never reach the DB.
* ``token_hint``  is the last 4 chars only — safe for log lines.
* The Fernet key is stored in tenants_settings at
  ``license_admin_bridge.bridge_token_enc_key``, generated once on first use.
  If DB write fails, an in-process ephemeral key is used so that
  encrypt/decrypt calls within the same process remain consistent.

Security notes
--------------
* Never log the full token value.
* Bridge transport is HTTPS (enforced in post_bridge_token_report).
* The shared_secret in AdminBridgeConfig stays separate; the bridge token is
  an additional, independently-rotatable credential.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Any

from cryptography.fernet import Fernet

from app.radius.db.connection import db
from app.radius.services.admin_panel_client import (
    BRIDGE_TOKEN_REPORT_PATH,  # noqa: F401 (re-exported for callers)
    AdminBridgeConfig,
    AdminPanelClient,
)

LOG = logging.getLogger(__name__)

_ENC_KEY_SETTING = "license_admin_bridge.bridge_token_enc_key"
_DEFAULT_TENANT = 1

# Stable ephemeral fallback keys keyed by tenant_id — survive multiple
# BridgeTokenSyncService instantiations in the same process when the DB
# is temporarily unavailable during key bootstrap.
_EPHEMERAL_KEYS: dict[int, bytes] = {}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _hint(token: str) -> str:
    """Return the last 4 chars for safe log display."""
    return token[-4:] if len(token) >= 4 else "****"


def _fingerprint(token: str) -> str:
    """SHA-256 hex of the token — safe to send/log; the panel validates it
    against SHA256(bridge_token) and rejects a mismatch."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


class BridgeTokenSyncService:
    """Manage the customer-side bridge-token state."""

    def __init__(
        self,
        *,
        config: AdminBridgeConfig | None = None,
        admin_client: AdminPanelClient | None = None,
    ) -> None:
        self.config = config or AdminBridgeConfig.from_env()
        self.admin_client = admin_client or AdminPanelClient(config=self.config)

    # ── Public API ─────────────────────────────────────────────────────────

    def consume_panel_token(
        self, runtime_payload: dict[str, Any], *, tenant_id: int = _DEFAULT_TENANT
    ) -> dict[str, Any]:
        """Extract bridge_token block from a runtime-contract payload and store it.

        Safe to call when bridge_token is absent — returns action='absent'.
        Idempotent: same panel_seq triggers no write.
        """
        bridge_block = runtime_payload.get("bridge_token")
        if not isinstance(bridge_block, dict):
            return {"ok": True, "action": "absent"}

        token_value = str(bridge_block.get("token") or "").strip()
        if not token_value:
            return {"ok": True, "action": "empty_token"}

        panel_seq = str(bridge_block.get("seq") or "").strip()
        issued_at = str(bridge_block.get("issued_at") or "").strip()

        existing = self._active_row(tenant_id)
        if existing and panel_seq and existing.get("panel_seq") == panel_seq:
            return {"ok": True, "action": "already_current", "seq": panel_seq}

        token_enc = self._encrypt(token_value, tenant_id)
        now = _utcnow()
        self._deactivate_all(tenant_id)
        db().execute(
            """
            INSERT INTO bridge_token_states
                (tenant_id, source, token_enc, token_hint, panel_seq,
                 issued_at, reported_at, panel_acked, active, created_at, updated_at)
            VALUES (?, 'panel', ?, ?, ?, ?, NULL, 0, 1, ?, ?)
            """,
            (
                int(tenant_id),
                token_enc,
                _hint(token_value),
                panel_seq,
                issued_at or now,
                now,
                now,
            ),
        )
        LOG.info(
            "bridge_token: stored panel token hint=...%s seq=%s",
            _hint(token_value),
            panel_seq or "(none)",
        )
        return {"ok": True, "action": "stored_panel_token", "seq": panel_seq}

    @staticmethod
    def _report_enabled() -> bool:
        """Dedicated opt-out for the OUTBOUND bridge-token report, separate from
        the license sync. When the panel doesn't (yet) serve
        /api/integration/hoberadius/bridge-token/report the report keeps
        failing every cycle (`panel report failed status=...`); set
        ``license_admin_bridge.bridge_token_enabled=0`` (or env
        HOBERADIUS_ADMIN_BRIDGE_TOKEN=0) to stop the retries + log noise
        WITHOUT touching the working license sync. Default ON (unchanged)."""
        from .admin_panel_client import bridge_flag
        return bridge_flag("HOBERADIUS_ADMIN_BRIDGE_TOKEN",
                           "license_admin_bridge.bridge_token_enabled",
                           default=True)

    def generate_and_report(
        self, *, tenant_id: int = _DEFAULT_TENANT
    ) -> dict[str, Any]:
        """Mint a fresh local bridge token, store it encrypted, and report to panel."""
        if not self._report_enabled():
            return {"ok": True, "action": "disabled"}
        token_value = secrets.token_urlsafe(32)
        return self._store_local_and_report(token_value, tenant_id=tenant_id)

    def ensure_token_and_report_pending(
        self, *, tenant_id: int = _DEFAULT_TENANT
    ) -> dict[str, Any]:
        """Called by the sync worker each cycle.

        * No active token → mint one and report it (version 0).
        * Otherwise → report the current active token as a HEARTBEAT so panel
          rotations propagate back and both sides stay converged. The reconcile
          (adopt the panel's token / mark acked) happens in _report_existing.
        """
        if not self._report_enabled():
            return {"ok": True, "action": "disabled"}
        row = self._active_row(tenant_id)
        if not row or not row.get("token_enc"):
            LOG.info("bridge_token: no active token — generating")
            return self.generate_and_report(tenant_id=tenant_id)

        try:
            token_value = self._decrypt(row["token_enc"], tenant_id)
        except Exception:  # noqa: BLE001
            LOG.warning("bridge_token: failed to decrypt stored token; regenerating")
            return self.generate_and_report(tenant_id=tenant_id)
        try:
            local_version = int(str(row.get("panel_seq") or "0") or 0)
        except (TypeError, ValueError):
            local_version = 0
        return self._report_existing(
            token_value, int(row["id"]),
            tenant_id=tenant_id, local_version=local_version)

    def get_active_token(
        self, *, tenant_id: int = _DEFAULT_TENANT
    ) -> str | None:
        """Return the decrypted active bridge token, or None if unavailable."""
        row = self._active_row(tenant_id)
        if not row or not row.get("token_enc"):
            return None
        try:
            return self._decrypt(row["token_enc"], tenant_id)
        except Exception:  # noqa: BLE001
            LOG.warning(
                "bridge_token: decryption failed hint=...%s",
                row.get("token_hint", ""),
            )
            return None

    def current_state(
        self, *, tenant_id: int = _DEFAULT_TENANT
    ) -> dict[str, Any]:
        """Non-secret metadata about the active token — safe for UI/debugging."""
        row = self._active_row(tenant_id)
        if not row:
            return {"has_token": False}
        return {
            "has_token": bool(row.get("token_enc")),
            "source": row.get("source", ""),
            "token_hint": row.get("token_hint", ""),
            "panel_seq": row.get("panel_seq", ""),
            "issued_at": row.get("issued_at"),
            "reported_at": row.get("reported_at"),
            "panel_acked": bool(row.get("panel_acked")),
        }

    # ── Private helpers ────────────────────────────────────────────────────

    def _store_local_and_report(
        self, token_value: str, *, tenant_id: int
    ) -> dict[str, Any]:
        token_enc = self._encrypt(token_value, tenant_id)
        now = _utcnow()
        self._deactivate_all(tenant_id)
        cur = db().execute(
            """
            INSERT INTO bridge_token_states
                (tenant_id, source, token_enc, token_hint, panel_seq,
                 issued_at, reported_at, panel_acked, active, created_at, updated_at)
            VALUES (?, 'local', ?, ?, '', ?, NULL, 0, 1, ?, ?)
            """,
            (int(tenant_id), token_enc, _hint(token_value), now, now, now),
        )
        row_id = int(cur.lastrowid)
        LOG.info("bridge_token: generated local token hint=...%s", _hint(token_value))
        return self._report_existing(token_value, row_id, tenant_id=tenant_id)

    def _report_existing(
        self, token_value: str, row_id: int, *, tenant_id: int,
        local_version: int = 0,
    ) -> dict[str, Any]:
        """POST the token to the panel (unified protocol) and reconcile.

        Sends our token + version + fingerprint. The panel replies with the
        canonical ``{outcome, token, version, fingerprint}``:
          * ``panel_wins`` / ``stale_report`` → the panel's token is canonical;
            we ADOPT it locally so both sides converge.
          * ``adopted_customer`` / ``no_change`` → our token stands; mark acked.
          * legacy ``{ok, seq}`` (older panel) → mark acked (back-compat).
        Transport/panel failure → leave the row, keep retrying next cycle.
        """
        fp = _fingerprint(token_value)
        result = self.admin_client.post_bridge_token_report(
            token=token_value, version=int(local_version or 0), fingerprint=fp)
        now = _utcnow()
        transport_ok = result.get("ok") is True
        resp = result.get("response") or {}
        panel_ok = resp.get("ok") is not False  # True or absent = accepted

        if not (transport_ok and panel_ok):
            db().execute(
                "UPDATE bridge_token_states SET updated_at = ? WHERE id = ?",
                (now, row_id),
            )
            LOG.warning("bridge_token: panel report failed status=%s",
                        result.get("status"))
            return {"ok": False, "action": "report_failed",
                    "status": result.get("status") or "error",
                    "error": result.get("error") or resp}

        outcome = str(resp.get("outcome") or "").strip()
        panel_version = resp.get("version")
        pv = (str(int(panel_version)) if isinstance(panel_version, int)
              else str(resp.get("seq") or resp.get("token_seq") or "").strip())
        panel_token = resp.get("token")

        if (outcome in ("panel_wins", "stale_report")
                and isinstance(panel_token, str) and len(panel_token) >= 16):
            # Panel is canonical → adopt its token (new active 'panel' row).
            token_enc = self._encrypt(panel_token, tenant_id)
            self._deactivate_all(tenant_id)
            db().execute(
                """
                INSERT INTO bridge_token_states
                    (tenant_id, source, token_enc, token_hint, panel_seq,
                     issued_at, reported_at, panel_acked, active, created_at, updated_at)
                VALUES (?, 'panel', ?, ?, ?, ?, ?, 1, 1, ?, ?)
                """,
                (int(tenant_id), token_enc, _hint(panel_token), pv, now, now, now, now),
            )
            LOG.info("bridge_token: adopted panel token outcome=%s v=%s hint=...%s",
                     outcome, pv or "(none)", _hint(panel_token))
            return {"ok": True, "action": "adopted_panel",
                    "outcome": outcome, "seq": pv}

        # adopted_customer / no_change / legacy → our token stands; mark acked.
        db().execute(
            """
            UPDATE bridge_token_states
               SET reported_at = ?,
                   panel_acked = 1,
                   panel_seq   = CASE WHEN ? != '' THEN ? ELSE panel_seq END,
                   updated_at  = ?
             WHERE id = ?
            """,
            (now, pv, pv, now, row_id),
        )
        LOG.info("bridge_token: panel acknowledged our token (outcome=%s)",
                 outcome or "legacy")
        return {"ok": True, "action": "reported",
                "outcome": outcome or "legacy", "status": result.get("status")}

    def _active_row(self, tenant_id: int) -> dict[str, Any] | None:
        row = db().execute(
            """
            SELECT * FROM bridge_token_states
             WHERE tenant_id = ? AND active = 1
             ORDER BY id DESC LIMIT 1
            """,
            (int(tenant_id),),
        ).fetchone()
        return dict(row) if row else None

    def _deactivate_all(self, tenant_id: int) -> None:
        db().execute(
            "UPDATE bridge_token_states SET active = 0, updated_at = ? WHERE tenant_id = ?",
            (_utcnow(), int(tenant_id)),
        )

    # ── Encryption / decryption ────────────────────────────────────────────

    def _encrypt(self, plaintext: str, tenant_id: int) -> str:
        return Fernet(self._fernet_key(tenant_id)).encrypt(
            plaintext.encode("utf-8")
        ).decode("ascii")

    def _decrypt(self, token_enc: str, tenant_id: int) -> str:
        return Fernet(self._fernet_key(tenant_id)).decrypt(
            token_enc.encode("ascii")
        ).decode("utf-8")

    def _fernet_key(self, tenant_id: int) -> bytes:
        """Return (or generate and persist) the Fernet encryption key for this tenant.

        Key lifecycle:
          1. DB has a valid key → use it (clears any ephemeral fallback).
          2. No key yet → generate, try to persist, return it.
          3. DB write fails → use module-level ephemeral key so the same key is
             returned on every call within this process (avoids encrypt/decrypt
             mismatch). On next process start the ephemeral key is gone; a fresh
             key is generated and any previously-stored ciphertext becomes
             unreadable — the worker then detects the failure and regenerates.
        """
        tid = int(tenant_id or _DEFAULT_TENANT)
        try:
            from app.radius.core.tenant import DEFAULT_TENANT_ID
            from app.radius.db.repos import tenants_repo

            stored = str(
                tenants_repo.get_setting(
                    int(DEFAULT_TENANT_ID), _ENC_KEY_SETTING, ""
                ) or ""
            ).strip()
            if stored:
                try:
                    Fernet(stored.encode("ascii"))  # validate format
                    _EPHEMERAL_KEYS.pop(tid, None)
                    return stored.encode("ascii")
                except Exception:  # noqa: BLE001
                    pass  # stored key corrupt; fall through to generate
            new_key = Fernet.generate_key()
            try:
                tenants_repo.set_setting(
                    int(DEFAULT_TENANT_ID), _ENC_KEY_SETTING, new_key.decode("ascii")
                )
                _EPHEMERAL_KEYS.pop(tid, None)
                return new_key
            except Exception:  # noqa: BLE001
                pass  # DB write failed; fall through to ephemeral
        except Exception:  # noqa: BLE001
            pass  # import or DB access failed entirely

        if tid not in _EPHEMERAL_KEYS:
            _EPHEMERAL_KEYS[tid] = Fernet.generate_key()
        return _EPHEMERAL_KEYS[tid]
