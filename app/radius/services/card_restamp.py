"""card_restamp — a batch/plan time change reaches the cards that ALREADY started.

🔴 client20 · 2026-10-05 · batch 66 «علاء نت» (time_value=0, from first
connect) was imported on plan «ساعة» (60 min) then moved to «2 ميجا - 16
ساعة» (960 min). Card 77821145 had logged in on the 1-hour plan, so its
``expire_at`` was stamped ``first_used + 1h``. After the move the checker /
app said «متبقّي ≈ 15h57m» (current budget − used) while RADIUS rejected at
one hour («انتهت صلاحية الاشتراك»): authorization reads the STAMP.

🔑 Owner decision («أ»): when a batch's time settings / count mode / plan
change — or a plan's ``duration_minutes`` / ``validity_days`` change — the
cards that already started take the NEW duration counted from THEIR OWN first
login, the same formula as the first-login stamp::

    expire_at = clamp_expiry(first_used_at, new_budget + extra_seconds)

written to ``cards`` AND the ``subscribers`` mirror in one transaction.
Unstarted cards need nothing (they stamp at first login).

Budget per mode — identical to what the first-login stamp writes
(``policy_engine._do_update_login_timestamps`` + ``card_batch_flags.
_materialize_first_login_validity``):

* from first connect: the batch window (``time_value/time_unit`` then
  ``validity_after_first_login_days``), else the plan of the batch
  (``duration_minutes`` then ``validity_days``); plus ``extra_seconds``.
* by seconds (count_by_seconds and not from-first): ``time_value`` is a USAGE
  balance, not a calendar window — only the calendar cap
  (``validity_after_first_login_days``) is a stamp; no extra (grants go to the
  usage balance). A cap of 0 = no calendar end.
* neither flag (legacy third state): the batch window + extra, no plan.

«Budget plan» = the BATCH's plan, falling back to the card's own — the same
source the checker reads. (Since the owner decision of 2026-10-05 a started
card also moves its ``plan_id`` — hence its SPEED — with the batch;
``cards_repo.update_batch`` + ``bandwidth_apply.push_batch_speed_live``.)

Drift (time the operator added OUTSIDE the budget) is carried, never wiped:
``drift = old expire_at − (first_used_at + OLD budget)``.

* from-first / legacy: a POSITIVE drift > 60 s (a thaw after «تعطيل» adds the
  paused time; auto-renew) is kept; a negative one is a stale stamp from an
  older budget and is corrected — that is the incident itself.
* by-seconds: the cap was shifted by grants («إضافة وقت») in both directions,
  so the drift is carried with its sign.

A budget that drops to 0 («no time limit») clears ``expire_at`` (unlimited, as
at first login) — but only when the OLD budget was > 0, so an idempotent
re-save never turns a legacy dated card into an unlimited one.

Never touched: revoked / frozen / deleted cards, cards of a deleted batch,
batches with ``auto_renew_after_first_use`` (their end is a renewal, not a
window), and cards exhausted by a deduction (``base + extra <= 0``).

Live sessions of changed cards get the new Session-Timeout by CoA (or a
reconciled disconnect when the new window is already over, or when a
SHORTER window's CoA is refused — MikroTik answers Unsupported-Extension to a
Session-Timeout CoA, and a session that outlives its window is free time) in a
background thread that never blocks the save and never raises.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

from . import card_accounting

_LOG = logging.getLogger(__name__)

#: drift below this is clock noise (the first-login stamp uses «now» a few ms
#: after first_used_at) — not a deliberate operator shift.
_DRIFT_TOLERANCE_SEC = 60

#: batch fields whose change can move a started card's window.
BATCH_TIME_FIELDS = (
    "time_value", "time_unit", "validity_after_first_login_days",
    "count_by_seconds", "count_from_first_connect", "plan_id",
)


def _g(row: Any, key: str, default: Any = None) -> Any:
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def _i(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _mode(batch: Any) -> str:
    """``from_first`` · ``by_seconds`` · ``legacy`` (neither flag)."""
    if bool(_i(_g(batch, "count_from_first_connect", 0))):
        return "from_first"
    if bool(_i(_g(batch, "count_by_seconds", 0))):
        return "by_seconds"
    return "legacy"


def stamp_seconds(batch: Any, plan: Any, extra_seconds: int) -> tuple[str, int, bool]:
    """``(mode, seconds, exhausted)`` — the window the first-login stamp would
    write for this batch/plan today (0 = no end). ``batch``/``plan`` are
    mappings (sqlite rows or dicts); ``plan`` may be None."""
    from .policy_engine import _card_window_seconds
    mode = _mode(batch)
    window = _card_window_seconds(batch)   # by-seconds ⇒ the calendar cap only
    if mode == "by_seconds":
        return mode, max(0, window), False
    base = window
    if base <= 0 and mode == "from_first":
        base = card_accounting.budget_seconds(
            duration_minutes=_i(_g(plan, "duration_minutes", 0)),
            validity_days=_i(_g(plan, "validity_days", 0)),
        )
    if base <= 0:
        return mode, 0, False
    if card_accounting.is_exhausted(base, extra_seconds):
        return mode, 0, True
    return mode, card_accounting.budget_with_extra(base, extra_seconds), False


def _scope_sql(batch_id: Optional[int], plan_id: Optional[int]) -> tuple[str, tuple]:
    if batch_id is not None:
        return " AND c.batch_id = ?", (int(batch_id),)
    if plan_id is not None:
        return " AND COALESCE(NULLIF(b.plan_id, 0), c.plan_id) = ?", (int(plan_id),)
    raise ValueError("restamp_started_cards needs batch_id or plan_id")


def restamp_started_cards(
    tenant_id: int, *,
    batch_id: Optional[int] = None,
    plan_id: Optional[int] = None,
    previous_batch: Optional[dict] = None,
    previous_plan: Optional[dict] = None,
    reason: str = "",
    actor: str = "system",
    push_live: bool = True,
) -> dict:
    """Recompute ``expire_at`` of the STARTED cards in a batch (``batch_id``)
    or in every batch whose budget plan is ``plan_id``.

    ``previous_batch`` / ``previous_plan`` = the values BEFORE the edit (used
    only to measure each card's drift); omitted ⇒ today's values (an idempotent
    re-save: stale stamps are corrected, deliberate shifts kept).

    Returns ``{"scanned", "changed", "cleared", "expired_now", "still_live",
    "skipped"}`` — never raises (a failure returns zeros + ``"error"``).
    """
    out = {"scanned": 0, "changed": 0, "cleared": 0, "expired_now": 0,
           "still_live": 0, "skipped": 0}
    try:
        live = _restamp(int(tenant_id), batch_id=batch_id, plan_id=plan_id,
                        previous_batch=previous_batch,
                        previous_plan=previous_plan, out=out)
    except Exception as exc:  # noqa: BLE001 — a save never fails for this
        _LOG.exception("card_restamp[%s]: failed (batch=%s plan=%s)",
                       reason, batch_id, plan_id)
        out["error"] = str(exc)[:200]
        return out
    if out["changed"]:
        _audit(int(tenant_id), actor=actor, reason=reason, batch_id=batch_id,
               plan_id=plan_id, counts=out)
    if push_live and live:
        _spawn(lambda: push_live_windows(int(tenant_id), live, reason=reason))
    return out


def _restamp(tenant_id: int, *, batch_id, plan_id, previous_batch,
             previous_plan, out: dict) -> list[dict]:
    from ..db.connection import transaction
    from ..db.helpers import parse_dt
    scope, args = _scope_sql(batch_id, plan_id)
    now = datetime.utcnow()
    changed: list[dict] = []
    with transaction() as conn:
        rows = conn.execute(
            "SELECT c.id, c.username, c.first_used_at, c.expire_at, "
            "       COALESCE(c.extra_seconds, 0) AS extra_seconds, "
            "       b.time_value, b.time_unit, b.validity_after_first_login_days, "
            "       b.count_by_seconds, b.count_from_first_connect, "
            "       COALESCE(b.auto_renew_after_first_use, 0) AS auto_renew, "
            "       p.duration_minutes, p.validity_days "
            "  FROM cards c "
            "  JOIN card_batches b ON b.tenant_id = c.tenant_id AND b.id = c.batch_id "
            "  LEFT JOIN access_plans p ON p.tenant_id = c.tenant_id "
            "       AND p.id = COALESCE(NULLIF(b.plan_id, 0), c.plan_id) "
            " WHERE c.tenant_id = ? "
            "   AND c.deleted_at IS NULL AND b.deleted_at IS NULL "
            "   AND COALESCE(c.revoked, 0) = 0 "
            "   AND COALESCE(c.frozen_remaining_seconds, 0) = 0 "
            "   AND c.first_used_at IS NOT NULL AND c.first_used_at != ''"
            + scope,
            (tenant_id, *args)).fetchall()
        for r in rows:
            out["scanned"] += 1
            first = parse_dt(r["first_used_at"])
            if first is None or _i(r["auto_renew"]):
                out["skipped"] += 1
                continue
            extra = _i(r["extra_seconds"])
            old_batch = dict(r)
            if previous_batch:
                old_batch.update({k: v for k, v in previous_batch.items()
                                  if k in BATCH_TIME_FIELDS})
            old_plan = dict(r)
            if previous_plan:
                old_plan = dict(previous_plan)
            mode, new_secs, exhausted = stamp_seconds(r, r, extra)
            if exhausted:
                out["skipped"] += 1           # a deduction already ended it
                continue
            old_mode, old_secs, _old_exh = stamp_seconds(old_batch, old_plan, extra)
            cur = parse_dt(r["expire_at"])

            carry = 0
            if cur is not None and old_secs > 0:
                drift = int((cur - (first + timedelta(seconds=old_secs))).total_seconds())
                if mode == "by_seconds" and old_mode == "by_seconds":
                    carry = drift if abs(drift) > _DRIFT_TOLERANCE_SEC else 0
                elif drift > _DRIFT_TOLERANCE_SEC:
                    carry = drift

            if new_secs <= 0:
                # «بلا حدٍّ زمنيّ» — مثل أوّل دخول: لا نخترع انتهاءً. ولا نمسح
                # إلّا ما كان له حدٌّ فعلًا قبل التعديل (لا بطاقةً قديمةً مؤرَّخة).
                if cur is None or old_secs <= 0:
                    continue
                new = None
            else:
                new = card_accounting.clamp_expiry(first, new_secs + carry)

            if cur is not None and new is not None \
                    and abs((cur - new).total_seconds()) < 1:
                continue
            stamp = (new.isoformat() + "Z") if new is not None else None
            conn.execute(
                "UPDATE cards SET expire_at = ? WHERE tenant_id = ? AND id = ?",
                (stamp, tenant_id, r["id"]))
            # الرفضُ يُنفَّذ من المرآة أيضًا — ختمُ الكرت وحده يكذب.
            conn.execute(
                "UPDATE subscribers SET expire_at = ? "
                " WHERE tenant_id = ? AND username = ? "
                "   AND (user_type = 'card' OR card_batch_id IS NOT NULL)",
                (stamp, tenant_id, r["username"]))
            out["changed"] += 1
            if new is None:
                out["cleared"] += 1
                out["still_live"] += 1
                remaining = None
            else:
                remaining = int((new - now).total_seconds())
                if remaining <= 0:
                    out["expired_now"] += 1
                else:
                    out["still_live"] += 1
            old_remaining = (int((cur - now).total_seconds())
                             if cur is not None else None)
            changed.append({"username": r["username"], "remaining": remaining,
                            "old_remaining": old_remaining})
    if not changed:
        return []
    return _with_open_sessions(tenant_id, changed)


def _with_open_sessions(tenant_id: int, items: list[dict]) -> list[dict]:
    """Only the changed cards that have an open radacct row."""
    from ..db.connection import db
    names = [it["username"] for it in items]
    online: set[str] = set()
    for i in range(0, len(names), 500):
        chunk = names[i:i + 500]
        ph = ",".join("?" * len(chunk))
        for row in db().execute(
                f"SELECT DISTINCT username FROM radacct WHERE tenant_id = ? "
                f"   AND acctstoptime IS NULL AND username IN ({ph})",
                (tenant_id, *chunk)).fetchall():
            online.add(str(row["username"]))
    return [it for it in items if it["username"] in online]


def push_live_windows(tenant_id: int, items: Iterable[dict], *,
                      reason: str = "") -> dict:
    """New Session-Timeout for each live card session, or a reconciled
    disconnect when its window is over. Never raises."""
    from ..integration import radius_coa
    stats = {"coa": 0, "disconnected": 0, "failed": 0}
    for it in items:
        username = it.get("username") or ""
        remaining = it.get("remaining")
        try:
            if remaining is not None and remaining <= 0:
                res = radius_coa.disconnect_user(tenant_id, username)
                stats["disconnected" if getattr(res, "ok", False) else "failed"] += 1
                continue
            if remaining is None:
                # صار بلا حدٍّ زمنيّ — لا «Session-Timeout=0» (معناه عند
                # الراوتر بلا حدّ أصلًا، وبعضها يرفضه)؛ الجلسة تستمرّ والدخول
                # التالي يأخذ القاعدة الجديدة.
                continue
            res = radius_coa.change_user_session_timeout(
                tenant_id, username, session_timeout=int(remaining))
            if getattr(res, "ok", False):
                stats["coa"] += 1
                continue
            old = it.get("old_remaining")
            shortened = old is None or int(remaining) < int(old)
            if shortened:
                # مايكروتيك يرفض CoA للمهلة؛ جلسةٌ تتجاوز نافذتها الجديدة وقتٌ
                # مجّانيّ ⇒ تُطرد فتعود بعدّادٍ صحيح.
                d = radius_coa.disconnect_user(tenant_id, username)
                stats["disconnected" if getattr(d, "ok", False) else "failed"] += 1
            else:
                stats["failed"] += 1      # أطول: يُقطع عند العدّاد القديم ويعود
        except Exception:  # noqa: BLE001
            stats["failed"] += 1
            _LOG.warning("card_restamp[%s]: live push failed for %r",
                         reason, username, exc_info=True)
    _LOG.info("card_restamp[%s]: live sessions coa=%d disconnected=%d failed=%d",
              reason, stats["coa"], stats["disconnected"], stats["failed"])
    return stats


def _spawn(fn) -> None:
    """Background, fire-and-forget (tests monkeypatch this to run inline)."""
    def _run():
        try:
            fn()
        except Exception:  # noqa: BLE001
            _LOG.exception("card_restamp: background push failed")
    try:
        threading.Thread(target=_run, name="card-restamp-live", daemon=True).start()
    except Exception:  # noqa: BLE001
        _LOG.exception("card_restamp: could not spawn the live push")


def _audit(tenant_id: int, *, actor: str, reason: str, batch_id, plan_id,
           counts: dict) -> None:
    try:
        from ..db.repos import audit_repo
        audit_repo.record(
            tenant_id=tenant_id, actor=actor or "system",
            action="cards.restamp_started",
            target_type="card_batch" if batch_id is not None else "plan",
            target_id=str(batch_id if batch_id is not None else plan_id),
            payload={"reason": reason, **{k: v for k, v in counts.items()
                                          if k != "error"}},
        )
    except Exception:  # noqa: BLE001 — the audit never breaks the restamp
        _LOG.debug("card_restamp: audit skipped", exc_info=True)


__all__ = ["BATCH_TIME_FIELDS", "push_live_windows", "restamp_started_cards",
           "stamp_seconds"]
