"""Live application of bandwidth rates via CoA — side-effecting layer.

Two operator/automation entry points share one cascade-correct, gate-respecting
implementation:

  * :func:`apply_profile_live` — the bandwidth-profile «apply now» button: push
    the (now profile-driven) rate to every live session on plans that reference
    the profile. Owner: «لما نختار بروفايل ونعمل تطبيق يتطبق فعلاً».
  * :func:`apply_schedule_users_live` — used by the auto-schedule worker on a
    window enter/exit transition: recompute each affected user's EFFECTIVE rate
    (cascade) and CoA it.

Both recompute the effective rate per user (active schedule > subscriber
override > plan/profile base), so applying never fights a higher-precedence
rule. Both honor :func:`bandwidth_rate.live_apply_enabled` (default ON; the
kill-switch is HOBERADIUS_ENABLE_LIVE_SPEED_APPLY=0). Failures are per-user
isolated and never raise to the caller.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from . import bandwidth_rate

_LOG = logging.getLogger(__name__)


def _coa_user_result(tenant_id: int, username: str, rate: str):
    """Push one CoA rate change; return the CoaResult (``.ok`` / ``.code_name``)
    or None. Never raises."""
    if not rate:
        return None
    try:
        from ..integration import radius_coa
        return radius_coa.change_user_rate(tenant_id, username, new_rate_limit=rate)
    except Exception:  # noqa: BLE001 — one user/router must not abort the batch
        _LOG.exception("CoA rate change failed for %s", username)
        return None


def _reauth_fallback(tenant_id: int, username: str):
    """Disconnect the user's live session(s) so it re-authenticates and picks up
    the new rate. Uses the reconcile-first disconnect (queries the router's real
    active list), so it can land where a radacct-built rate-CoA couldn't match.
    Returns the disconnect CoaResult or None. Never raises."""
    try:
        from ..integration import radius_coa
        return radius_coa.disconnect_user(tenant_id, username)
    except Exception:  # noqa: BLE001
        _LOG.exception("reauth-fallback disconnect failed for %s", username)
        return None


def fallback_disconnect_enabled() -> bool:
    """Whether a failed rate-CoA falls back to a disconnect→re-auth so the new
    speed still takes effect on the live session. Default ON (owner: «لازم
    تتغيّر السرعة فعلاً على المتصل»). Kill-switch:
    HOBERADIUS_SPEED_COA_FALLBACK_DISCONNECT=0."""
    raw = (os.environ.get("HOBERADIUS_SPEED_COA_FALLBACK_DISCONNECT") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def apply_users_effective(tenant_id: int, usernames, *, at=None,
                          dry_run: bool = False,
                          fallback_disconnect: bool = False) -> dict:
    """For each username push its cascade-correct effective rate via CoA.

    ``fallback_disconnect``: when the live rate-CoA is not ACK'd (router NAK / the
    session can't be matched from radacct), disconnect the user so it re-auths at
    the new rate (which the auth path returns). Honest counting — a user counts as
    ``applied`` only on a real CoA-ACK OR a confirmed reauth disconnect; each
    result carries ``method`` ("coa" | "reauth") so callers report truthfully.

    Returns ``{"targets","applied","skipped","dry_run","results":[...]}``.
    ``skipped`` counts users with no resolvable rate or no confirmed apply.
    """
    if fallback_disconnect and not fallback_disconnect_enabled():
        fallback_disconnect = False
    results = []
    applied = 0
    for username in usernames:
        rate = bandwidth_rate.effective_rate_limit(tenant_id, username, at=at)
        if not rate:
            results.append({"username": username, "ok": False, "rate": "", "reason": "no_rate"})
            continue
        if dry_run:
            results.append({"username": username, "ok": False, "rate": rate, "reason": "dry_run"})
            continue
        res = _coa_user_result(tenant_id, username, rate)
        ok = bool(getattr(res, "ok", False))
        method = "coa"
        code = str(getattr(res, "code_name", "") or "")
        if not ok and fallback_disconnect:
            # rate-CoA didn't land — force a re-auth so the new rate still applies.
            d = _reauth_fallback(tenant_id, username)
            if bool(getattr(d, "ok", False)):
                ok, method = True, "reauth"
                code = str(getattr(d, "code_name", "") or code)
        if ok:
            applied += 1
        results.append({"username": username, "ok": ok, "rate": rate,
                        "method": method if ok else "", "code": code})
    return {
        "targets": len(results),
        "applied": applied,
        "skipped": len(results) - applied,
        "dry_run": dry_run,
        "results": results,
    }


def rebalance_device_split(tenant_id: int, username: str) -> bool:
    """عند اتصال/فصل جهاز: إن كان المشترك مفعِّلًا «تقسيم السرعة على الأجهزة»،
    أعِد حساب الحصّة المقسَّمة (``effective_rate_limit`` صار مقسِّمًا) وادفعها بـCoA
    لكلّ جلساته الحيّة. **لا شيء (ولا CoA) حين التقسيم معطّل — وهو الافتراضيّ**،
    فلا يُثقَل مسار المحاسبة لعامّة المشتركين. محصّنة — لا تُفشِل المحاسبة أبدًا.

    ملاحظة توقيت: تُستدعى بعد أن يكون radacct قد سجّل الاتصال (Start) أو أغلق
    الجلسة (Stop)، فعدّ الأجهزة الحيّ صحيح والقسمة على العدد الصحيح.
    """
    try:
        from ..db.repos import subscribers_repo
        sub = subscribers_repo.get_subscriber(tenant_id, username)
        if not sub:
            return False
        if not (getattr(sub, "equal_share_download", 0) or getattr(sub, "equal_share_upload", 0)):
            return False
        apply_users_effective(tenant_id, [username])
        return True
    except Exception:  # noqa: BLE001 — accounting must never fail on this
        _LOG.exception("rebalance_device_split failed for %s", username)
        return False


def propagate_plan_split(tenant_id: int, plan_id: int,
                         ed: bool, eu: bool) -> list[str]:
    """توريث «تقسيم السرعة على الأجهزة» من العرض (الخطّة) — **للمشتركين فقط**.

    قرار المالك الصريح: البطاقات **لا** تَرِث من العرض إطلاقًا (حتى لو حمل
    صفّها plan_id المباشر) — قالب تقسيم البطاقات هو عرض الكروت (card_offers)
    وقت التوليد، لا خطّة الوصول. لذا النطاق مقيَّد بـ user_type='subscriber'.
    يكتب الأعلام ثمّ يدفع السرعة المُعاد حسابها للجلسات الحيّة بـCoA (خلفيًّا).
    يعيد أسماء الحسابات المُحدَّثة (للاختبارات/السجلّ)."""
    from ..db.connection import db, transaction
    scope = ("tenant_id = ? AND plan_id = ? AND user_type = 'subscriber' "
             "AND COALESCE(deleted_at,'') = ''")
    vals = (int(tenant_id), int(plan_id))
    with transaction() as conn:
        conn.execute(
            f"UPDATE subscribers SET equal_share_download=?, equal_share_upload=?, "
            f"updated_at=datetime('now') WHERE {scope}",
            (1 if ed else 0, 1 if eu else 0, *vals))
    try:
        rows = db().execute(
            f"SELECT username FROM subscribers WHERE {scope}", vals).fetchall()
        names = [str(r["username"]) for r in rows if r["username"]]
    except Exception:  # noqa: BLE001
        names = []
    if names:
        import threading

        def _bw():
            try:
                apply_users_effective(int(tenant_id), names)
            except Exception:  # noqa: BLE001
                _LOG.exception("plan-split CoA push failed")
        threading.Thread(target=_bw, name="plan-split-propagate",
                         daemon=True).start()
    return names


def _usernames_on_profile(tenant_id: int, bw_id: int, *, limit: int = 2000) -> list[str]:
    """Active subscribers whose plan references this bandwidth profile."""
    from ..db.connection import db
    rows = db().execute(
        """
        SELECT s.username AS username
          FROM subscribers s
          JOIN access_plans p ON p.id = s.plan_id AND p.tenant_id = s.tenant_id
         WHERE s.tenant_id = ? AND p.bandwidth_id = ?
           AND COALESCE(s.status,'') NOT IN ('deleted','archived')
         ORDER BY s.id
         LIMIT ?
        """,
        (tenant_id, bw_id, limit),
    ).fetchall()
    return [str(r["username"]) for r in rows if r["username"]]


def apply_profile_live(tenant_id: int, bw_id: int, *, actor: str = "system") -> dict:
    """Push the profile's (now enforced) rate to all live sessions on plans that
    use it. Honors the global live gate; logs/audits the outcome."""
    enabled = bandwidth_rate.live_apply_enabled()
    usernames = _usernames_on_profile(tenant_id, bw_id)
    stats = apply_users_effective(tenant_id, usernames, dry_run=not enabled,
                                  fallback_disconnect=True)
    stats["live_enabled"] = enabled
    stats["bw_id"] = bw_id
    try:
        from ..db.repos import audit_repo
        audit_repo.record(
            tenant_id=tenant_id, actor=actor,
            action="bandwidth_profile.apply_live" if enabled else "bandwidth_profile.apply_dry",
            target_type="bandwidth_profile", target_id=str(bw_id),
            payload={"targets": stats["targets"], "applied": stats["applied"],
                     "live_enabled": enabled},
        )
    except Exception:  # noqa: BLE001 — audit must never break the apply
        _LOG.debug("audit record skipped for profile apply", exc_info=True)
    _LOG.info("bandwidth profile %s apply: targets=%d applied=%d live=%s",
              bw_id, stats["targets"], stats["applied"], enabled)
    return stats


def apply_schedule_users_live(tenant_id: int, schedule: dict, *, at=None,
                              phase: str = "engage") -> dict:
    """Recompute + CoA the effective rate for every user in a schedule's scope.

    Called by the auto-schedule worker on a window enter (``phase='engage'``) or
    exit (``phase='release'``) transition. On release the effective rate naturally
    falls back (no active schedule now) to the subscriber/plan base."""
    from ..db.repos import operations_repo

    enabled = bandwidth_rate.live_apply_enabled()
    try:
        usernames = operations_repo.usernames_for_bandwidth_schedule(
            tenant_id, schedule, limit=1000)
    except Exception:  # noqa: BLE001
        _LOG.exception("scope resolution failed for schedule %s", schedule.get("id"))
        usernames = []
    stats = apply_users_effective(tenant_id, usernames, at=at, dry_run=not enabled,
                                  fallback_disconnect=True)
    stats["live_enabled"] = enabled
    stats["phase"] = phase
    # Per-online-user audit → all three logs (MikroTik-actions feed + manager
    # audit + subscriber timeline). Scoped to users with a LIVE session (the CoA
    # target); offline users pick up the new rate at next auth, which is not a
    # live router action worth a per-tick row. Actor = system:scheduler.
    if not stats.get("dry_run"):
        _audit_schedule_speed_changes(tenant_id, schedule, stats.get("results") or [],
                                      phase=phase, at=at)
    try:
        operations_repo.log_bandwidth_schedule(
            tenant_id, int(schedule.get("id") or 0),
            action=f"auto_{phase}",
            status=("applied" if stats["applied"] else
                    ("dry_run" if not enabled else "no_active_sessions")),
            message=f"auto {phase}: applied {stats['applied']}/{stats['targets']} "
                    f"(live={enabled})",
        )
    except Exception:  # noqa: BLE001
        _LOG.debug("schedule log skipped", exc_info=True)
    return stats


def _audit_schedule_speed_changes(tenant_id: int, schedule: dict, results: list,
                                  *, phase: str, at=None) -> None:
    """Emit one speed-change audit row per LIVE user whose rate the schedule
    transition actually changed. Feeds all three logs via the shared
    ``mt_action_log.record_speed_change`` helper. Fail-safe (best-effort)."""
    from datetime import datetime, timedelta
    try:
        from .mt_action_log import record_speed_change, _session_nas_ip
    except Exception:  # noqa: BLE001
        return
    action = "bandwidth_schedule.engage" if phase == "engage" else "bandwidth_schedule.release"
    sched_name = str(schedule.get("name") or schedule.get("id") or "").strip()
    note = (f"جدولة تلقائية «{sched_name}»" if sched_name else "جدولة تلقائية") + (
        " — بدء النافذة" if phase == "engage" else " — نهاية النافذة")
    # `at` is the transition instant; a moment just before it reflects the
    # OPPOSITE phase (out-of-window on engage, in-window on release), so the
    # cascade there gives the honest «previous» rate for the old→new diff.
    now = at or datetime.utcnow()
    prev_at = now - timedelta(seconds=61)
    for res in results:
        try:
            username = str(res.get("username") or "").strip()
            new_rate = str(res.get("rate") or "").strip()
            if not username or not new_rate:
                continue
            nas_ip = _session_nas_ip(tenant_id, username)
            if not nas_ip:
                continue                          # not live → not a router action
            old_rate = bandwidth_rate.effective_rate_limit(
                tenant_id, username, at=prev_at) or ""
            if old_rate == new_rate:
                continue                          # no real change this transition
            # Honest method: a direct rate-CoA, or a reauth-disconnect fallback.
            _method = str(res.get("method") or "")
            _note = note + (" (عبر إعادة اتصال)" if _method == "reauth" else "")
            record_speed_change(
                tenant_id=int(tenant_id), actor="system:scheduler",
                username=username, action=action,
                old_rate=old_rate, new_rate=new_rate,
                ok=bool(res.get("ok")), nas_ip=nas_ip, note=_note,
                error="" if res.get("ok") else "CoA undeliverable",
            )
        except Exception:  # noqa: BLE001 — one user must not stall the rest
            _LOG.debug("schedule speed-change audit skipped for %s",
                       res.get("username"), exc_info=True)


__all__ = ["apply_profile_live", "apply_schedule_users_live", "apply_users_effective"]
