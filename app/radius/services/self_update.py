"""Per-customer OPT-IN self-update — RADIUS-MODULE side.

The panel SEES that an update exists and is FREE to take it or not. The app
runs inside Docker and CANNOT rebuild itself — the actual update is done by a
HOST AGENT (deploy/updater/) that watches an on-disk marker. This module:

  1. periodically GETs the latest available version + changelog from the
     central licensing endpoint (reusing the license bridge's config + bearer
     auth — the same trust model), compares to the running version, and caches
     an "update available" state + changelog. Missing/unreachable endpoint →
     silent no-op, never crashes.
  2. on the owner's explicit confirm, writes the UPDATE-REQUEST marker file the
     host agent watches, plus a DB audit row.
  3. reads the host agent's UPDATE-STATUS marker so the panel can show progress
     (queued → running → success/failed).

Nothing here ever runs docker/git/build. It only signals + reads markers.

Endpoint contract (assumed; match this on the panel publish side):
    GET  {base_url}/api/integration/hoberadius/update/latest
         Authorization: Bearer <license_key>   (Simple_Link trust model)
    →    {version, released_at, changelog_md, mandatory, min_version}
         (optionally wrapped as {"ok":true,"data":{...}} — both accepted)

Request marker  (panel → host agent), default /var/lib/hoberadius/update-request.json:
    {requested_version, requested_by, requested_by_name, requested_at,
     current_version, install_id}
Status marker   (host agent → panel), default /var/lib/hoberadius/update-status.json:
    {state: running|success|failed, log, finished_at, request_at, ...}
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from markupsafe import Markup

from ..core import app_version, markdown_lite

LOG = logging.getLogger(__name__)

DEFAULT_UPDATE_DIR = "/var/lib/hoberadius"
DEFAULT_CHECK_PATH = "/api/integration/hoberadius/update/latest"

# tenant_settings keys.
SK_CACHE = "self_update.cache"                       # JSON blob of last check
SK_CHECK_PATH = "self_update.check_path"             # optional path override
SK_CHECK_INTERVAL = "self_update.check_interval_seconds"

REQUEST_FILENAME = "update-request.json"
STATUS_FILENAME = "update-status.json"


# ── time ──────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── marker paths (host-mounted) ───────────────────────────────────────
def update_dir() -> Path:
    """Directory shared with the host agent (bind-mounted into the container)."""
    raw = (os.environ.get("HOBERADIUS_UPDATE_DIR") or DEFAULT_UPDATE_DIR).strip()
    return Path(raw or DEFAULT_UPDATE_DIR)


def request_path() -> Path:
    return update_dir() / REQUEST_FILENAME


def status_path() -> Path:
    return update_dir() / STATUS_FILENAME


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic on POSIX


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        if not path.exists():
            return None
        parsed = json.loads(path.read_text(encoding="utf-8") or "{}")
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001 — a corrupt marker must never crash the panel
        return None


# ── settings cache ────────────────────────────────────────────────────
def _get_setting(tenant_id: int, key: str, default: str = "") -> str:
    try:
        from ..db.repos import tenants_repo
        return str(tenants_repo.get_setting(tenant_id, key, default) or "").strip()
    except Exception:  # noqa: BLE001
        return default


def _set_setting(tenant_id: int, key: str, value: str, *, by: int = 0) -> None:
    try:
        from ..db.repos import tenants_repo
        tenants_repo.set_setting(tenant_id, key, value, by=by)
    except Exception:  # noqa: BLE001
        LOG.warning("self_update: could not persist %s", key, exc_info=True)


def _default_state() -> dict[str, Any]:
    return {
        "ok": False,
        "available": False,
        "current": app_version.running_version(),
        "latest": "",
        "released_at": "",
        "changelog_md": "",
        "mandatory": False,
        "min_version": "",
        "below_min": False,
        # When the current version is below the latest's hard min_version, a
        # blind v1→vN jump is unsafe. The owner must take an intermediate step
        # first; `target_version` is what a confirm should actually request.
        "blocked_direct_jump": False,
        "target_version": "",
        "checked_at": "",
        "reason": "never_checked",
    }


def get_cached_state(tenant_id: int = 1) -> dict[str, Any]:
    """Last-known update state — never raises, always returns a full dict."""
    raw = _get_setting(tenant_id, SK_CACHE, "")
    if not raw:
        return _default_state()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return _default_state()
    except Exception:  # noqa: BLE001
        return _default_state()
    state = _default_state()
    state.update(parsed)
    # Always reflect the CURRENT running version (image may have changed).
    state["current"] = app_version.running_version()
    return state


# ── remote check ──────────────────────────────────────────────────────
def _check_path(tenant_id: int) -> str:
    env = os.environ.get("HOBERADIUS_UPDATE_CHECK_PATH")
    if env and env.strip():
        return env.strip()
    return _get_setting(tenant_id, SK_CHECK_PATH, DEFAULT_CHECK_PATH) or DEFAULT_CHECK_PATH


def _extract_payload(response: Any) -> Optional[dict[str, Any]]:
    """Normalise both bare and {ok,data}-wrapped responses to the version dict.

    A valid success envelope that carries NO version — ``{"version": null}``,
    an ``{ok:true, data:{...}}`` wrapper, or a bare ``{"ok": true}`` when
    nothing is published — is UP-TO-DATE, not an error. It yields an empty (or
    version-less) dict so ``check_for_update`` reports ``available=False`` with
    a clean ``ok`` state and never shows the «تعذّر التحقّق» banner. Only a
    genuinely malformed reply (non-dict, or a dict that is neither a version
    payload nor an ``ok`` envelope) returns ``None`` → ``bad_payload``.
    """
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if isinstance(data, dict):
        return data
    if "version" in response:
        return response
    if response.get("ok") is True:
        return {}
    return None


def _fetch_latest(tenant_id: int) -> dict[str, Any]:
    """Fetch the latest-version payload through the SIGNED license bridge.

    Reuses ``AdminPanelClient.get_update_latest`` so the request is
    authenticated exactly like every other bridge call — the license envelope
    (``_license_check_payload``, incl. ``current_version``) is POSTed as the
    JSON body, which the panel verifies. Never raises. On any failure returns
    ``{"ok": False, "reason": ...}`` with a SPECIFIC reason so the page can
    show why (connection / signature / service-down), while a valid up-to-date
    reply returns ``{"ok": True, "payload": {...}}``.
    """
    from .admin_panel_client import AdminBridgeConfig, AdminPanelClient

    config = AdminBridgeConfig.from_env()
    if not config.enabled:
        return {"ok": False, "reason": "disabled"}
    if config.missing_fields():
        return {"ok": False, "reason": "not_configured"}

    client = AdminPanelClient(config=config)
    result = client.get_update_latest(
        current_version=app_version.running_version(),
        channel="stable",
        path=_check_path(tenant_id),
    )
    if not result.get("ok"):
        return {"ok": False, "reason": str(result.get("reason") or "unreachable")}

    body = _extract_payload(result.get("payload"))
    if body is None:
        return {"ok": False, "reason": "bad_payload"}
    return {"ok": True, "payload": body}


def _cumulative_changelog(body: dict[str, Any], current: str) -> str:
    """Build the CUMULATIVE changelog spanning current→latest.

    If the panel supplies a ``releases`` array (oldest→newest, each with its
    own ``version`` + ``changelog_md``), concatenate the notes of every release
    strictly newer than ``current`` — so a customer who skipped v2 and v3 sees
    everything they missed, not just v4. Otherwise fall back to the flat
    ``changelog_md`` (the panel is expected to have already made it cumulative
    using the ``current`` query parameter we send).
    """
    releases = body.get("releases")
    if isinstance(releases, list) and releases:
        skipped = []
        for rel in releases:
            if not isinstance(rel, dict):
                continue
            ver = str(rel.get("version") or "").strip()
            if not ver or not app_version.is_newer(ver, current):
                continue
            skipped.append(rel)
        # Sort oldest→newest so the reader walks forward in time.
        skipped.sort(key=lambda r: app_version.parse_version(r.get("version")) or (0, 0, 0))
        if skipped:
            parts = []
            for rel in skipped:
                ver = str(rel.get("version") or "").strip()
                when = str(rel.get("released_at") or "").strip()
                head = f"## {ver}" + (f" — {when}" if when else "")
                notes = str(rel.get("changelog_md") or "").strip()
                parts.append(head + ("\n\n" + notes if notes else ""))
            return "\n\n---\n\n".join(parts)
    return str(body.get("changelog_md") or "")


def check_for_update(tenant_id: int = 1, *, force: bool = False) -> dict[str, Any]:
    """Run one remote check, update the cache, and return the fresh state.

    ``force`` is accepted for API symmetry (the worker always checks on its
    own cadence); it has no throttling here — throttling lives in the worker.
    Robust: any failure yields a not-available state, never an exception.
    """
    current = app_version.running_version()
    result = _fetch_latest(tenant_id)

    if not result.get("ok"):
        state = _default_state()
        state["checked_at"] = _now_iso()
        state["reason"] = str(result.get("reason") or "unreachable")
        _set_setting(tenant_id, SK_CACHE, json.dumps(state, ensure_ascii=False))
        return state

    body = result["payload"]
    latest = str(body.get("version") or "").strip()
    min_version = str(body.get("min_version") or "").strip()
    available = app_version.is_newer(latest, current)
    below_min = not app_version.meets_min(current, min_version)
    # A direct v1→vN jump is only unsafe when we are BELOW the latest's hard
    # floor. In that case the confirm must target the intermediate min_version
    # first; otherwise it targets the latest and applies ALL pending migrations
    # in one pass (the runner is order-safe + records each once).
    blocked = bool(available and below_min)
    target = min_version if blocked else latest
    state = {
        "ok": True,
        "available": available,
        "current": current,
        "latest": latest,
        "released_at": str(body.get("released_at") or ""),
        "changelog_md": _cumulative_changelog(body, current),
        "mandatory": bool(body.get("mandatory", False)),
        "min_version": min_version,
        "below_min": below_min,
        "blocked_direct_jump": blocked,
        "target_version": target,
        "checked_at": _now_iso(),
        "reason": "ok",
    }
    _set_setting(tenant_id, SK_CACHE, json.dumps(state, ensure_ascii=False))
    return state


def changelog_html(state: dict[str, Any]) -> Markup:
    """Render the cached changelog markdown to safe HTML."""
    return markdown_lite.render(str((state or {}).get("changelog_md") or ""))


# ── reason → UI label ─────────────────────────────────────────────────
# kind drives the page styling:
#   "ok"      — the check succeeded (up-to-date / update-available handled by state).
#   "neutral" — nothing to alarm about (not checked yet, bridge off, no channel).
#   "failed"  — a genuine failure → the amber «تعذّر التحقّق» banner.
_REASON_LABELS: dict[str, tuple[str, str]] = {
    "ok": ("ok", "تمّ التحقّق بنجاح."),
    "never_checked": ("neutral", "لم يتم التحقّق من التحديثات بعد."),
    "disabled": ("neutral", "الربط مع لوحة التراخيص غير مُفعَّل."),
    "not_configured": ("neutral", "إعداد الربط غير مكتمل (رابط اللوحة أو مفتاح الترخيص)."),
    "config_missing": ("neutral", "إعداد الربط غير مكتمل (رابط اللوحة أو مفتاح الترخيص)."),
    "not_found": ("neutral", "لا توجد قناة تحديث منشورة على اللوحة بعد."),
    "https_required": ("failed", "يتطلّب رابط لوحة تراخيص آمن (HTTPS)."),
    "timeout": ("failed", "تعذّر الاتصال بخادم التراخيص (انتهت المهلة)."),
    "unreachable": ("failed", "تعذّر الاتصال بخادم التراخيص."),
    "unauthorized": ("failed", "رُفض الطلب: مشكلة توقيع أو ترخيص (401)."),
    "service_unavailable": ("failed", "خدمة التحديث غير متوفرة حاليًا على اللوحة."),
    "bad_payload": ("failed", "وصل ردّ غير صالح من خادم التراخيص."),
}


def reason_info(reason: str) -> dict[str, str]:
    """Map a state ``reason`` code to ``{"kind", "message"}`` for the page.

    Unknown ``http_<code>`` reasons and any unmapped code fall back to a
    generic FAILED label so the owner still sees a diagnosable, non-empty
    message rather than a blank/generic failure.
    """
    key = str(reason or "").strip().lower()
    if key in _REASON_LABELS:
        kind, message = _REASON_LABELS[key]
        return {"kind": kind, "message": message}
    if key.startswith("http_"):
        return {"kind": "failed", "message": f"رُفض الطلب من الخادم (HTTP {key[5:]})."}
    return {"kind": "failed", "message": "تعذّر التحقّق من التحديثات الآن."}


# ── opt-in request (panel → host agent) ───────────────────────────────
def request_update(
    tenant_id: int,
    *,
    requested_version: str,
    requested_by: int,
    actor: str = "",
) -> dict[str, Any]:
    """Write the update-request marker + audit row. NEVER runs the update.

    The requested_version is the target the host agent resolves to a git ref
    (a ``vX.Y.Z`` tag, or ``main`` when empty/"latest"). Returns
    ``{"ok": True, "request": {...}}`` or ``{"ok": False, "reason": ...}``.
    """
    version = (requested_version or "").strip() or "latest"
    install_id = ""
    try:
        from .admin_panel_client import AdminBridgeConfig, AdminPanelClient
        client = AdminPanelClient(config=AdminBridgeConfig.from_env())
        install_id = str(client._license_check_payload().get("install_id") or "")
    except Exception:  # noqa: BLE001
        install_id = ""

    marker = {
        "requested_version": version,
        "requested_by": int(requested_by or 0),
        "requested_by_name": actor or "",
        "requested_at": _now_iso(),
        "current_version": app_version.running_version(),
        "install_id": install_id,
        # Schema version of THIS marker, so the host agent can guard.
        "marker_schema": 1,
    }
    try:
        _write_json_atomic(request_path(), marker)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("self_update: could not write request marker: %s", exc)
        return {"ok": False, "reason": "marker_write_failed", "detail": str(exc)}

    _log_event(
        tenant_id,
        event="requested",
        from_version=marker["current_version"],
        to_version=version,
        requested_by=int(requested_by or 0),
        actor=actor,
        detail="update requested by owner",
    )
    return {"ok": True, "request": marker}


# ── progress (host agent → panel) ─────────────────────────────────────
def _parse_iso(value: str):
    """Parse a ``...Z`` UTC timestamp → aware datetime, or None."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _coerce_percent(value: Any) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _to_local(value: str, tenant_id: int, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a UTC ISO string in the tenant-local timezone (site-wide helper)."""
    if not value:
        return value
    try:
        from ..core import system_config
        return system_config.to_local(value, fmt=fmt, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001 — display formatting must never break the poll
        return value


# Log line prefixes emitted by the host agent, in UTC:
#   new: "2026-07-09T04:36:04Z — <msg>"  (full ISO — localizable exactly)
#   old: "04:36:04Z — <msg>"             (bare time — needs the marker's date)
_LOG_ISO_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})Z\s*—\s*(.*)$")
_LOG_TIME_RE = re.compile(r"^\s*(\d{2}:\d{2}:\d{2})Z\s*—\s*(.*)$")


def _localize_log(log: str, tenant_id: int, fallback_iso: str = "") -> str:
    """Rewrite each log line's leading UTC timestamp to tenant-local time.

    The host agent bakes only a machine-readable UTC timestamp; the panel is the
    single place that formats to the tenant's timezone (so it's right regardless
    of the server clock). Backward-compatible with the old bare-``HH:MM:SSZ``
    format (localized using the marker's date) and with un-timestamped lines.
    """
    if not log:
        return log
    fb_date = ""
    mfb = re.match(r"^(\d{4}-\d{2}-\d{2})", str(fallback_iso or ""))
    if mfb:
        fb_date = mfb.group(1)
    out: list[str] = []
    for line in str(log).splitlines():
        m = _LOG_ISO_RE.match(line)
        if m:
            local = _to_local(m.group(1) + "Z", tenant_id, fmt="%H:%M:%S")
            out.append(f"{local} — {m.group(2)}" if local and local != "—" else m.group(2))
            continue
        m = _LOG_TIME_RE.match(line)
        if m and fb_date:
            local = _to_local(f"{fb_date}T{m.group(1)}Z", tenant_id, fmt="%H:%M:%S")
            out.append(f"{local} — {m.group(2)}" if local and local != "—" else m.group(2))
            continue
        # Last resort (old marker, no date to anchor on): strip the bare ``Z``
        # so no misleading UTC marker leaks into the display.
        out.append(re.sub(r"\b(\d{2}:\d{2}:\d{2})Z\b", r"\1", line))
    return "\n".join(out)


def get_progress(tenant_id: int = 1) -> dict[str, Any]:
    """Compute the display state for the poller. Never raises.

    States: idle | queued | running | success | failed.
      • queued  — a request marker exists but the agent hasn't reported on
                  THIS request yet (status missing or for an older request).
      • running/success/failed — the agent's status for THIS request, or the
                  last result if the request marker was already consumed.

    Granular fields (additive; the host agent writes them at each stage —
    older markers without them degrade gracefully to empty/0):
      stage, stage_label, percent, updated_at, error, failed_stage,
      rolled_back. ``queued_seconds`` is computed here so the panel can warn
      when the agent hasn't picked the request up for too long.
    """
    req = _read_json(request_path())
    st = _read_json(status_path())

    result: dict[str, Any] = {
        "state": "idle",
        "log": "",
        "finished_at": "",
        "requested_version": "",
        "requested_at": "",
        "current": app_version.running_version(),
        # granular progress (additive)
        "stage": "",
        "stage_label": "",
        "percent": 0,
        "updated_at": "",
        "error": "",
        "failed_stage": "",
        "rolled_back": False,
        "queued_seconds": 0,
    }

    if req:
        result["requested_version"] = str(req.get("requested_version") or "")
        result["requested_at"] = str(req.get("requested_at") or "")

    if st:
        st_state = str(st.get("state") or "").lower()
        st_for = str(st.get("request_at") or "")
        st_valid = st_state in {"running", "success", "failed"}
        if req:
            # Status must correspond to the current request; otherwise the
            # agent hasn't picked it up yet → still queued.
            if st_valid and (not st_for or st_for == req.get("requested_at")):
                result["state"] = st_state
            else:
                result["state"] = "queued"
        elif st_valid:
            # Request already consumed by the agent — surface the last result.
            result["state"] = st_state
        result["log"] = str(st.get("log") or "")
        result["finished_at"] = str(st.get("finished_at") or "")
        # Only trust the granular fields when this status is for THIS request
        # (or the request was already consumed). A stale/queued status must not
        # bleed a previous run's percent/stage into the current attempt.
        if result["state"] in {"running", "success", "failed"}:
            result["stage"] = str(st.get("stage") or "")
            result["stage_label"] = str(st.get("stage_label") or "")
            result["percent"] = _coerce_percent(st.get("percent"))
            result["updated_at"] = str(st.get("updated_at") or "")
            result["error"] = str(st.get("error") or "")
            result["failed_stage"] = str(st.get("failed_stage") or "")
            result["rolled_back"] = bool(st.get("rolled_back", False))
    elif req:
        result["state"] = "queued"

    # Pin the endpoints so the bar never lies, and time the wait when queued.
    if result["state"] == "success":
        result["percent"] = 100
    elif result["state"] == "queued":
        result["percent"] = 0
        started = _parse_iso(result["requested_at"])
        if started is not None:
            try:
                result["queued_seconds"] = max(
                    0, int((datetime.now(timezone.utc) - started).total_seconds())
                )
            except Exception:  # noqa: BLE001
                result["queued_seconds"] = 0

    # ── Timezone unification (owner: times must match the site's local tz) ──
    # The agent bakes only UTC timestamps; the panel formats them to the
    # tenant-local timezone here, at render time. `requested_at` stays raw UTC
    # (it's an internal correlation key + drives queued_seconds, never shown).
    if result["log"]:
        result["log"] = _localize_log(
            result["log"], tenant_id,
            fallback_iso=(str(st.get("updated_at") or "") if st else ""),
        )
    if result["updated_at"]:
        result["updated_at"] = _to_local(result["updated_at"], tenant_id)
    if result["finished_at"]:
        result["finished_at"] = _to_local(result["finished_at"], tenant_id)

    return result


# ── audit ─────────────────────────────────────────────────────────────
def _log_event(
    tenant_id: int,
    *,
    event: str,
    from_version: str = "",
    to_version: str = "",
    state: str = "",
    requested_by: int = 0,
    actor: str = "",
    detail: str = "",
) -> None:
    try:
        from ..db.connection import db
        db().execute(
            """INSERT INTO self_update_events
                 (tenant_id, event, from_version, to_version, state,
                  requested_by, actor, detail, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (int(tenant_id), event, from_version, to_version, state,
             int(requested_by or 0), actor, detail, _now_iso()),
        )
    except Exception:  # noqa: BLE001 — audit failure must not block the request
        LOG.warning("self_update: audit insert failed", exc_info=True)


def recent_events(tenant_id: int = 1, limit: int = 20) -> list[dict[str, Any]]:
    try:
        from ..db.connection import db
        cur = db().execute(
            """SELECT event, from_version, to_version, state, actor, detail, created_at
                 FROM self_update_events
                WHERE tenant_id = ?
                ORDER BY id DESC LIMIT ?""",
            (int(tenant_id), int(limit)),
        )
        return [dict(r) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        return []
