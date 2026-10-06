"""The conversation loop between the admin, the MODEL and the EXECUTOR.

    admin text ─▶ transcript (ops_messages) ─▶ model (ONE JSON object)
                         ▲                        │
                         │  CHOICES / RESULT      ├─ choose / list_*  → executor CHOICES → model again
                         │  (≤ MAX_HOPS)          ├─ INFO (read-only)  → executor RESULT  → model again
                         └────────────────────────┤
                                                  ├─ ask / refuse / cancel / reply → the model's message
                                                  └─ action / plan → executor validates → confirmation card

* Every model reply is parsed as exactly ONE JSON object
  (``model_client.parse_proposal``) and then validated by the EXECUTOR (the
  same ``/api/v1/ops/.../proposals`` endpoint the app uses — never re-coded
  here). Invalid output → a friendly Arabic error + a log/audit line; nothing
  is executed and nothing is appended to the transcript.
* Execution happens ONLY in ``confirm`` (an explicit admin click carrying the
  proposal id + hash the executor issued).
* The transcript holds what the model sees: CONTEXT (system), CHOICES and
  RESULT (tool) lines, user / assistant turns. A ``show_once`` password and
  card codes never enter it: RESULT is the executor's redacted
  ``model_result``.
* CHOICES / level-4 events are rendered in the item schemas the model was
  trained on (SPEC_DATA_v2 §4/§5, v3 §4/§8) — same ids, the executor's
  issued set is unchanged.
* The admin sees the model's ``message`` (SPEC_DATA_v3; ``summary_ar`` for a
  round-1/2 model). An empty CHOICES list becomes an empty-state line, and a
  lookup that already came back empty in this conversation is answered here
  without asking the system (or the model) again.
"""
from __future__ import annotations
from app.i18n_text import _tr

import json
import logging
from datetime import datetime
from typing import Any, Callable, Optional

from . import model_client
from .model_client import InvalidModelOutput, ModelError

_LOG = logging.getLogger(__name__)

MAX_HOPS = 3                      # CHOICES / RESULT fetches per admin message
MAX_TEXT = 2000                   # admin message length
LIST_SOURCES = ("list_plans", "list_offers", "find_subscriber", "list_card_batches")
INFO_ACTIONS = ("card_batch_status", "subscriber_info", "online_sessions")


# ─────────────────────────── transcript ────────────────────────────

def append(cid: str, tenant_id: int, role: str, content: str) -> None:
    from ...db.connection import transaction
    from ...db.helpers import now_iso
    with transaction() as conn:
        conn.execute("INSERT INTO ops_messages(conversation_id, tenant_id, role, content, created_at) "
                     "VALUES (?,?,?,?,?)", (cid, int(tenant_id), role, content, now_iso()))


def transcript(cid: str, tenant_id: int) -> list[dict]:
    from ...db.connection import db
    rows = db().execute("SELECT role, content FROM ops_messages WHERE conversation_id=? AND "
                        "tenant_id=? ORDER BY id", (cid, int(tenant_id))).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in rows]


def model_messages(cid: str, tenant_id: int) -> list[dict]:
    """SYSTEM_PROMPT + the stored transcript (CONTEXT first) — before rendering."""
    return [{"role": "system", "content": model_client.system_prompt()}] + transcript(cid, tenant_id)


# ─────────────────────────── rendering (SPEC_DATA_v2 §4/§5) ────────────────────────────

def _names(tenant_id: int, table: str, ids) -> dict[int, str]:
    ids = sorted({int(i) for i in ids if str(i or "").strip().lstrip("-").isdigit()})
    if not ids:
        return {}
    from ...db.connection import db
    marks = ",".join("?" * len(ids))
    try:
        rows = db().execute(f"SELECT id, name FROM {table} WHERE tenant_id=? AND id IN ({marks})",
                            (int(tenant_id), *ids)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {int(r["id"]): r["name"] or "" for r in rows}


def plan_names(tenant_id: int, ids) -> dict[int, str]:
    return _names(tenant_id, "access_plans", ids)


def offer_names(tenant_id: int, ids) -> dict[int, str]:
    return _names(tenant_id, "card_offers", ids)


def _duration(minutes: Any) -> tuple[Optional[int], Optional[str]]:
    try:
        m = int(minutes or 0)
    except (TypeError, ValueError):
        return None, None
    if m <= 0:
        return None, None
    if m % 43200 == 0:
        return m // 43200, "months"
    if m % 1440 == 0:
        return m // 1440, "days"
    if m % 60 == 0:
        return m // 60, "hours"
    return m, "minutes"


def _local(expire_at: Any, tenant_id: int) -> Optional[str]:
    if not expire_at:
        return None
    from . import units
    try:
        s = str(expire_at).replace(" ", "T").rstrip("Z")[:19]
        dt = datetime.fromisoformat(s)
        return units.to_local(dt, tenant_id).strftime("%Y-%m-%dT%H:%M")
    except Exception:  # noqa: BLE001
        return None




def render_choices(choices: dict, tenant_id: int) -> dict:
    """Executor CHOICES → the item keys of SPEC_DATA_v2 §4 (ids unchanged)."""
    src = choices.get("source")
    items = choices.get("items") or []
    out: list[dict] = []
    if src == "list_plans":
        for it in items:
            v, u = _duration(it.get("duration_minutes"))
            row = {"n": it.get("n"), "id": it.get("id"), "name": it.get("name"),
                   "price": it.get("price"), "currency": it.get("currency"),
                   "duration_value": v, "duration_unit": u, "plan_type": it.get("plan_type")}
            out.append({k: x for k, x in row.items() if x is not None})
    elif src == "list_offers":
        names = plan_names(tenant_id, [it.get("plan_id") for it in items])
        for it in items:
            v, u = _duration(it.get("duration_minutes"))
            row = {"n": it.get("n"), "id": it.get("id"), "name": it.get("name"),
                   "plan_id": it.get("plan_id"),
                   "plan_name": names.get(int(it.get("plan_id") or 0)),
                   "price": it.get("selling"), "wholesale_price": it.get("wholesale"),
                   "currency": it.get("currency"), "duration_value": v, "duration_unit": u}
            out.append({k: x for k, x in row.items() if x is not None})
    elif src == "find_subscriber":
        names = plan_names(tenant_id, [it.get("plan_id") for it in items])
        for it in items:
            row = {"n": it.get("n"), "username": it.get("username"),
                   "full_name": it.get("full_name"),
                   "plan": names.get(int(it.get("plan_id") or 0)),
                   "status": it.get("status"),
                   "expires_local": _local(it.get("expire_at"), tenant_id)}
            out.append({k: x for k, x in row.items() if x is not None})
    elif src == "change_plan_policies":
        # SPEC_DATA_v3 §8: {n, id, label_ar} — the proposal field is ``policy`` = id
        # (a round-1/2 adapter, prompt v1, was trained on {n, policy, label_ar})
        key = "policy" if model_client.system_prompt() == model_client.SYSTEM_PROMPT_V1 else "id"
        for it in items:
            pol = it.get("id") or it.get("name")
            out.append({"n": it.get("n"), key: pol,
                        "label_ar": model_client.POLICY_LABEL.get(pol, pol)})
    else:
        out = [dict(it) for it in items]
    res = {"source": src, "items": out}
    if src == "change_plan_policies" and choices.get("direction"):
        res = {"source": src, "direction": choices["direction"], "items": out}
    if choices.get("truncated"):
        res["truncated"] = True
    return res


# ─────────────────────────── text / empty results (SPEC_DATA_v3 §2, §6) ────────────────────────────

def admin_text(obj: dict) -> str:
    """What the admin reads: the model's ``message`` (ops-v2), else its
    ``summary_ar`` (a round-1/2 model). Shown with textContent — never HTML."""
    for key in ("message", "summary_ar"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:600]
    return ""


def _lookup_key(action: Any, fields: dict) -> Optional[tuple[str, str]]:
    if action == "choose":
        src = fields.get("source")
    elif action in LIST_SOURCES:
        src = action
    else:
        return None
    if src not in LIST_SOURCES:
        return None
    return str(src), str(fields.get("query") or "").strip().casefold()


def _empty_before(cid: str, tenant_id: int, key: tuple[str, str]) -> bool:
    """The same lookup (source + query) already returned an EMPTY list in this
    conversation (an assistant turn immediately followed by ``CHOICES`` with
    ``items: []``)."""
    msgs = transcript(cid, tenant_id)
    for prev, nxt in zip(msgs, msgs[1:]):
        if prev["role"] != "assistant" or nxt["role"] != "tool" or \
                not nxt["content"].startswith("CHOICES "):
            continue
        try:
            obj = json.loads(prev["content"])
            ch = json.loads(nxt["content"][len("CHOICES "):])
        except ValueError:
            continue
        f = obj.get("fields") if isinstance(obj.get("fields"), dict) else {}
        if _lookup_key(obj.get("action"), f) == key and not (ch.get("items") or []):
            return True
    return False


def empty_text(source: str, *, direct_generation: bool = False) -> str:
    """The empty-state line shown instead of an empty list card."""
    if source == "list_offers":
        if direct_generation:
            return _tr("لا توجد عروض مسجّلة في النظام بعد. يمكنك توليد الكروت مباشرةً من باقة، "
                       "أو إنشاء عرض أوّلًا.")
        return _tr("لا توجد عروض مسجّلة في النظام بعد.")
    if source == "list_plans":
        return _tr("لا توجد باقات مفعّلة في النظام بعد.")
    if source == "find_subscriber":
        return _tr("لا يوجد مشترك يطابق هذا البحث. جرّب اسم مستخدم آخر أو رقم الجوال.")
    if source == "list_card_batches":
        return _tr("لا توجد حزم بطاقات تطابق هذا البحث.")
    return _tr("لا توجد نتائج.")


def _direct_generation(cid: str, tenant_id: int) -> bool:
    for m in transcript(cid, tenant_id):
        if m["role"] == "system" and m["content"].startswith("CONTEXT "):
            try:
                ctx = json.loads(m["content"][len("CONTEXT "):])
            except ValueError:
                return False
            return "cards.generate_direct" in ((ctx.get("admin") or {}).get("permissions") or [])
    return False


def tool_line(rendered: dict) -> str:
    return "CHOICES " + json.dumps(rendered, ensure_ascii=False)


def event_records(event: dict) -> list[dict]:
    """The records of one executor event the admin can start a conversation from."""
    t, data = event.get("type"), event.get("data") or {}
    if t == "expiring_tomorrow":
        return [{"index": 0, "count": int(data.get("count") or 0),
                 "date_local": data.get("date_local"),
                 "usernames": [s.get("username") for s in data.get("subscribers", [])][:5]}]
    if t == "repeated_rejects":
        return [{"index": i, "nas": r.get("nas"), "rejects": r.get("rejects"),
                 "window_minutes": data.get("window_minutes")}
                for i, r in enumerate(data.get("nas", []))]
    if t == "low_card_stock":
        return [{"index": i, "plan_id": p.get("plan_id"), "plan_name": p.get("plan_name"),
                 "remaining": p.get("unused_cards"), "threshold": data.get("threshold")}
                for i, p in enumerate(data.get("plans", []))]
    if t == "plan_without_offers":
        return [{"index": i, "plan_id": p.get("plan_id"), "plan_name": p.get("plan_name")}
                for i, p in enumerate(data.get("plans", []))]
    return []


def render_event(event: dict, index: int, tenant_id: int) -> tuple[dict, dict]:
    """(CONTEXT.event, CHOICES{"source":"event"}) per SPEC_DATA_v2 §5 for ONE record."""
    t, data = event.get("type"), event.get("data") or {}
    if t == "expiring_tomorrow":
        subs = data.get("subscribers", [])
        names = plan_names(tenant_id, [s.get("plan_id") for s in subs])
        items = []
        for n, s in enumerate(subs, start=1):
            row = {"n": n, "username": s.get("username"),
                   "plan": names.get(int(s.get("plan_id") or 0)),
                   "expires_local": _local(s.get("expire_at"), tenant_id),
                   "last_renewal": None}
            items.append({k: x for k, x in row.items() if x is not None or k == "last_renewal"})
        return ({"type": t, "data": {"date_local": data.get("date_local")}},
                {"source": "event", "items": items})
    recs = event_records(event)
    if not recs:
        return {"type": t, "data": {}}, {"source": "event", "items": []}
    r = recs[max(0, min(int(index or 0), len(recs) - 1))]
    if t == "repeated_rejects":
        return ({"type": t, "data": {"nas_name": r["nas"], "count": r["rejects"],
                                     "window_min": r["window_minutes"]}},
                {"source": "event", "items": []})
    if t == "low_card_stock":
        return ({"type": t, "data": {"plan_name": r["plan_name"], "remaining": r["remaining"],
                                     "threshold": r["threshold"]}},
                {"source": "event", "items": [{"n": 1, "plan_id": r["plan_id"],
                                               "plan_name": r["plan_name"],
                                               "remaining": r["remaining"]}]})
    # plan_without_offers
    return ({"type": t, "data": {"plan_name": r["plan_name"]}},
            {"source": "event", "items": [{"n": 1, "id": r["plan_id"], "name": r["plan_name"]}]})


# ─────────────────────────── loop ────────────────────────────

Api = Callable[..., Any]       # web_bridge.Api — (method, path, body=None, query=None) → ApiResult


class TurnError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _audit(tenant_id: int, actor: str, admin_id: int, cid: str, outcome: str,
           details: Optional[dict] = None) -> None:
    try:
        from ...db.repos import audit_repo
        audit_repo.record(tenant_id=int(tenant_id), actor=actor or f"admin#{admin_id}",
                          action="ops.model", target_type="ops_assistant", target_id=cid,
                          payload={"conversation_id": cid, "admin_id": int(admin_id),
                                   **({"details": details} if details else {})},
                          severity="warning", result_status=outcome[:32])
    except Exception:  # noqa: BLE001
        _LOG.warning("ops assistant: audit failed", exc_info=True)


def _api_error(res) -> TurnError:
    err = res.error or {}
    viol = ((err.get("details") or {}).get("violations") or []) if isinstance(
        err.get("details"), dict) else []
    msgs = [v.get("message") for v in viol if isinstance(v, dict) and v.get("message")]
    text = "; ".join(dict.fromkeys(msgs)) or err.get("message") or ""
    return TurnError(err.get("code") or "api_error", text, res.status)


def start(api: Api, *, event_type: Optional[str] = None, index: int = 0) -> dict:
    """New conversation through the executor; stores CONTEXT (+ event CHOICES)."""
    body = {"event_type": event_type} if event_type else {}
    res = api("POST", "/ops/conversations", body=body)
    if not res.ok:
        raise _api_error(res)
    cid = res.data["conversation_id"]
    ctx = dict(res.data["context"])
    tid = api.tenant_id
    if ctx.get("event"):
        ev, ch = render_event(ctx["event"], index, tid)
        ctx["event"] = ev
        append(cid, tid, "system", "CONTEXT " + json.dumps(ctx, ensure_ascii=False))
        append(cid, tid, "tool", tool_line(ch))
    else:
        append(cid, tid, "system", res.data["context_message"])
    return {"conversation_id": cid}


def _policy_args(cid: str, tenant_id: int) -> dict:
    """username + plan_id of the latest assistant turn that named both (for
    ``choose change_plan_policies`` — the catalog's choose has no such fields)."""
    for m in reversed(transcript(cid, tenant_id)):
        if m["role"] != "assistant":
            continue
        try:
            f = (json.loads(m["content"]).get("fields") or {})
        except ValueError:
            continue
        if f.get("username") and f.get("plan_id") is not None:
            return {"username": f["username"], "plan_id": f["plan_id"]}
    return {}


def run_model(api: Api, cid: str, *, actor: str = "", call=None) -> list[dict]:
    """Call the model until it answers with something for the admin (≤ MAX_HOPS
    CHOICES fetches). Returns UI replies."""
    chat = call or model_client.chat
    tid, aid = api.tenant_id, api.admin_id
    replies: list[dict] = []
    for hop in range(MAX_HOPS + 1):
        try:
            text = chat(model_messages(cid, tid))
        except ModelError as e:
            _LOG.warning("ops assistant: model unavailable (%s)", e.code)
            replies.append({"type": "error", "code": "model_unavailable",
                            "text": _tr("نموذج المساعد غير متاح الآن. حاول بعد قليل، أو نفّذ "
                                        "العمليّة من صفحتها المعتادة.")})
            return replies
        try:
            obj = model_client.parse_proposal(text)
        except InvalidModelOutput as e:
            _LOG.warning("ops assistant: invalid model output (%s, %d chars)", e.reason,
                         len(text or ""))
            _audit(tid, actor, aid, cid, "invalid_output", {"reason": e.reason})
            replies.append({"type": "error", "code": "invalid_model_output",
                            "text": _tr("لم أفهم ردّ النموذج — لم يُنفَّذ أيّ شيء. أعد صياغة "
                                        "طلبك بجملة أوضح.")})
            return replies
        action = obj.get("action")
        fields = obj.get("fields") if isinstance(obj.get("fields"), dict) else {}
        summary = admin_text(obj)
        is_list = action in LIST_SOURCES or action == "choose"
        is_info = action in INFO_ACTIONS

        if (is_list or is_info) and hop >= MAX_HOPS:
            _audit(tid, actor, aid, cid, "too_many_hops", {"hops": hop})
            replies.append({"type": "error", "code": "too_many_hops",
                            "text": _tr("احتاج المساعد قوائم كثيرة لهذا الطلب. حدّد الاسم أو "
                                        "الرقم بدقّة أكثر وأعد المحاولة.")})
            return replies

        key = _lookup_key(action, fields)
        if key is not None and _empty_before(cid, tid, key):
            # loop guard (SPEC_DATA_v3 §6): the same lookup already came back empty —
            # answer the empty state here; no second lookup, no further model call.
            _audit(tid, actor, aid, cid, "repeated_empty_lookup", {"source": key[0]})
            replies.append({"type": "assistant", "action": "reply", "empty": True,
                            "text": empty_text(key[0], direct_generation=_direct_generation(cid, tid))})
            return replies

        res = api("POST", f"/ops/conversations/{cid}/proposals",
                  body={"proposal": obj, "mode": "execute"})
        if not res.ok:
            e = _api_error(res)
            _audit(tid, actor, aid, cid, "rejected", {"code": e.code, "action": str(action)[:40]})
            replies.append({"type": "error", "code": e.code,
                            "text": _tr("المقترح مرفوض من نظام التحقّق ولم يُنفَّذ: %(why)s",
                                        why=e.message or e.code)})
            return replies
        out = res.data or {}

        if is_list:
            ch = out.get("choices")
            if ch is None and action == "choose" and fields.get("source") == "change_plan_policies":
                args = _policy_args(cid, tid)
                if not args:
                    model_append(cid, tid, obj)
                    replies.append({"type": "assistant", "action": "ask",
                                    "text": summary or _tr("حدّد المشترك والباقة الجديدة أوّلًا.")})
                    return replies
                r2 = api("POST", f"/ops/conversations/{cid}/choices",
                         body={"source": "change_plan_policies", **args})
                if not r2.ok:
                    e = _api_error(r2)
                    replies.append({"type": "error", "code": e.code,
                                    "text": _tr("تعذّر جلب القائمة: %(why)s", why=e.message or e.code)})
                    return replies
                ch = (r2.data or {}).get("choices")
            if ch is None:
                model_append(cid, tid, obj)
                replies.append({"type": "assistant", "action": action, "text": summary})
                return replies
            rendered = render_choices(ch, tid)
            model_append(cid, tid, obj)
            append(cid, tid, "tool", tool_line(rendered))
            rep = {"type": "choices", "source": rendered["source"],
                   "items": rendered["items"], "text": summary,
                   "truncated": bool(rendered.get("truncated"))}
            if not rendered["items"]:
                rep["empty"] = empty_text(rendered["source"],
                                          direct_generation=_direct_generation(cid, tid))
            replies.append(rep)
            continue

        if out.get("kind") == "info":
            result = out.get("result") if isinstance(out.get("result"), dict) else {}
            model_append(cid, tid, obj)
            append(cid, tid, "tool", out.get("tool_message") or "RESULT {}")
            replies.append({"type": "result", "action": action, "text": summary,
                            "source": result.get("source") or action,
                            "data": result.get("data"), "error": result.get("error")})
            continue

        model_append(cid, tid, obj)
        if out.get("kind") == "control":
            replies.append({"type": "assistant", "action": action, "text": summary})
            return replies
        replies.append({"type": "proposal", "action": action, "text": summary,
                        "proposal": {"proposal_id": out.get("proposal_id"),
                                     "proposal_hash": out.get("proposal_hash"),
                                     "level": out.get("level"),
                                     "steps": out.get("confirmation") or [],
                                     "not_executable_steps": out.get("not_executable_steps") or []}})
        return replies
    return replies


def model_append(cid: str, tenant_id: int, obj: dict) -> None:
    append(cid, tenant_id, "assistant", model_client.dumps(obj))


def say(api: Api, cid: str, text: str, *, actor: str = "", call=None) -> list[dict]:
    text = (text or "").strip()
    if not text:
        raise TurnError("validation_error", _tr("اكتب رسالة أوّلًا."))
    if len(text) > MAX_TEXT:
        raise TurnError("validation_error", _tr("الرسالة طويلة جدًّا."))
    append(cid, api.tenant_id, "user", text)
    return run_model(api, cid, actor=actor, call=call)


def confirm(api: Api, cid: str, proposal_id: str, proposal_hash: str) -> dict:
    """The ONLY path that executes: the admin's explicit confirmation."""
    res = api("POST", f"/ops/conversations/{cid}/confirm",
              body={"proposal_id": proposal_id, "proposal_hash": proposal_hash})
    if not res.ok:
        raise _api_error(res)
    data = res.data or {}
    # the model gets the executor's redacted RESULT line — never show_once
    if data.get("model_result"):
        append(cid, api.tenant_id, "tool", data["model_result"])
    return {"report": data.get("report") or {}, "show_once": data.get("show_once")}


def cancel(api: Api, cid: str) -> None:
    append(cid, api.tenant_id, "user", model_client.CANCEL_TEXT)


__all__ = ["start", "say", "run_model", "confirm", "cancel", "render_choices", "render_event",
           "event_records", "transcript", "model_messages", "append", "TurnError", "MAX_HOPS",
           "plan_names", "offer_names"]
