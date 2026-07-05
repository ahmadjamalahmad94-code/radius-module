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
from typing import Optional

from . import bandwidth_rate

_LOG = logging.getLogger(__name__)


def _coa_user(tenant_id: int, username: str, rate: str) -> bool:
    """Push one CoA rate change; True on a confirmed apply. Never raises."""
    if not rate:
        return False
    try:
        from ..integration import radius_coa
        res = radius_coa.change_user_rate(tenant_id, username, new_rate_limit=rate)
        return bool(getattr(res, "ok", False))
    except Exception:  # noqa: BLE001 — one user/router must not abort the batch
        _LOG.exception("CoA rate change failed for %s", username)
        return False


def apply_users_effective(tenant_id: int, usernames, *, at=None,
                          dry_run: bool = False) -> dict:
    """For each username push its cascade-correct effective rate via CoA.

    Returns ``{"targets","applied","skipped","dry_run","results":[...]}``.
    ``skipped`` counts users with no resolvable rate or no active session.
    """
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
        ok = _coa_user(tenant_id, username, rate)
        if ok:
            applied += 1
        results.append({"username": username, "ok": ok, "rate": rate})
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
    stats = apply_users_effective(tenant_id, usernames, dry_run=not enabled)
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
    stats = apply_users_effective(tenant_id, usernames, at=at, dry_run=not enabled)
    stats["live_enabled"] = enabled
    stats["phase"] = phase
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


__all__ = ["apply_profile_live", "apply_schedule_users_live", "apply_users_effective"]
