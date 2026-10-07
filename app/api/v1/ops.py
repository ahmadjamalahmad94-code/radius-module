"""Operations assistant — the deterministic EXECUTOR's API (docs/OPS_EXECUTOR.md).

The model (outside this app) only converses and emits ONE JSON proposal per
turn. These endpoints build its CONTEXT, serve CHOICES, validate proposals,
produce level-1 drafts, and execute level-2/3 proposals after an explicit
confirmation — always as the logged-in admin, through the real /api/v1
handlers (``services/ops_assistant/dispatch.py``).

  GET  /api/v1/ops/status                          availability (flag + password gate, counts)
  POST /api/v1/ops/flag                            owner: enable/disable for this tenant
  GET  /api/v1/ops/events                          level-4 events detected for this admin
  POST /api/v1/ops/conversations                   new conversation (+ optional event) → CONTEXT
  GET  /api/v1/ops/conversations/<cid>/context     fresh CONTEXT
  POST /api/v1/ops/conversations/<cid>/choices     CHOICES list (ids issued to the conversation)
  POST /api/v1/ops/conversations/<cid>/proposals   validate a proposal (mode draft|execute);
                                                   an INFO action (read-only) answers with RESULT
  POST /api/v1/ops/conversations/<cid>/confirm     execute a pending proposal (hash + Idempotency-Key)

The MODEL TURN for the mobile app — a 1:1 mirror of the web chat routes
(radius/routes/ops_assistant.py), same bodies inside the ``{ok, data}`` envelope:

  POST /api/v1/ops/assistant/message      {conversation_id?, text} → {conversation_id, replies[]}
  POST /api/v1/ops/assistant/start-event  {event_type, index}      → {conversation_id, replies[]}
  POST /api/v1/ops/assistant/confirm      {conversation_id, proposal_id, proposal_hash}
                                          + Idempotency-Key → {conversation_id, report, show_once?}
                                          (a replay returns the stored report, never show_once)
  POST /api/v1/ops/assistant/cancel       {conversation_id} → {conversation_id}

Never with an unbound credential (env token / token without ``created_by``):
the assistant acts only with a real admin's permissions. Tenant comes from the
credential only.
"""
from __future__ import annotations
from app.i18n_text import _tr

from typing import Any

from flask import Blueprint, g, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    rules = (
        ("/ops/status", "ops_status", ops_status, ["GET"]),
        ("/ops/flag", "ops_flag", ops_flag, ["POST"]),
        ("/ops/events", "ops_events", ops_events, ["GET"]),
        ("/ops/conversations", "ops_conversation_create", ops_conversation_create, ["POST"]),
        ("/ops/conversations/<cid>/context", "ops_conversation_context",
         ops_conversation_context, ["GET"]),
        ("/ops/conversations/<cid>/choices", "ops_choices", ops_choices, ["POST"]),
        ("/ops/conversations/<cid>/proposals", "ops_proposal", ops_proposal, ["POST"]),
        ("/ops/conversations/<cid>/confirm", "ops_confirm", ops_confirm, ["POST"]),
        ("/ops/assistant/message", "ops_assistant_message", ops_assistant_message, ["POST"]),
        ("/ops/assistant/start-event", "ops_assistant_start_event",
         ops_assistant_start_event, ["POST"]),
        ("/ops/assistant/confirm", "ops_assistant_confirm", ops_assistant_confirm, ["POST"]),
        ("/ops/assistant/cancel", "ops_assistant_cancel", ops_assistant_cancel, ["POST"]),
    )
    for path, endpoint, view, methods in rules:
        bp.add_url_rule(path, endpoint, require_api_token(view), methods=methods)


MAX_VIOLATIONS = 20


def _tid() -> int:
    return int(getattr(g, "tenant_id", 1) or 1)


def _aid() -> int:
    return int(getattr(g, "admin_id", 0) or 0)


def _require_admin():
    if _aid() <= 0:
        return fail("forbidden",
                    _tr("المساعد يعمل بصلاحيّات مدير مسجَّل الدخول فقط — لا بتوكن غير مربوط بمدير."),
                    status=403, details={"reason": "ops_requires_admin"})
    return None


def _gate():
    err = _require_admin()
    if err is not None:
        return err
    from ...radius.services.ops_assistant.gate import availability
    av = availability(_tid())
    if av["available"]:
        return None
    if av["reason"] == "disabled":
        return fail("forbidden", _tr("مساعد العمليّات غير مفعّل لهذه الشبكة."), status=403,
                    details={"reason": "assistant_disabled"})
    return fail("forbidden",
                _tr("المساعد غير متاح: يوجد مدير بكلمة مرور افتراضيّة أو مؤقّتة — غيّرها أوّلًا."),
                status=403, details={"reason": "weak_admin_passwords",
                                     "password_gate": av["password_gate"]})


def _body() -> tuple[dict | None, Any]:
    from ..json_input import json_object
    return json_object()


def _conversation(cid: str):
    from ...radius.services.ops_assistant import store
    conv = store.get_conversation(cid, _tid(), _aid())
    if conv is None:
        return None, fail("not_found", _tr("المحادثة غير موجودة."), status=404)
    return conv, None


# ─────────────────────────── status / flag ────────────────────────────

def ops_status():
    err = _require_admin()
    if err is not None:
        return err
    from ...radius.services.ops_assistant import catalog
    from ...radius.services.ops_assistant.gate import availability
    av = availability(_tid())
    return ok({**av, "catalog_version": catalog.catalog_version(), "tenant_id": _tid()})


def ops_flag():
    err = _require_admin()
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    if not isinstance(body.get("enabled"), bool):
        return fail("validation_error", _tr("أرسل enabled قيمةً منطقيّة (true/false)."), status=422)
    from ...radius.services.ops_assistant import audit
    from ...radius.services.ops_assistant.gate import availability, set_flag
    set_flag(_tid(), body["enabled"], by=_aid())
    audit.record("flag", conversation_id="", outcome="enabled" if body["enabled"] else "disabled")
    return ok(availability(_tid()))


# ─────────────────────────── level 4 ────────────────────────────

def _detect(types=None) -> list[dict]:
    from ...radius.services.ops_assistant import context, detectors
    from ..access_control import batch_in_scope, is_full_access, subscriber_in_scope
    full = is_full_access()
    return detectors.detect(
        _tid(), full_access=full, owner=context.is_owner(),
        in_scope=None if full else (lambda u: subscriber_in_scope(username=u)),
        batch_ok=None if full else batch_in_scope,
        types=tuple(types) if types else detectors.EVENT_TYPES)


def ops_events():
    err = _gate()
    if err is not None:
        return err
    events = _detect()
    return ok({"items": events, "count": len(events)})


# ─────────────────────────── conversations ────────────────────────────

def ops_conversation_create():
    err = _gate()
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    from ...radius.services.ops_assistant import audit, context, detectors, store
    event = None
    etype = body.get("event_type")
    if etype not in (None, ""):
        if etype not in detectors.EVENT_TYPES:
            return fail("validation_error", _tr("نوع الحدث غير معروف."), status=422,
                        details={"allowed": list(detectors.EVENT_TYPES)})
        found = _detect([etype])
        if not found:
            return fail("not_found", _tr("لا يوجد حدث من هذا النوع الآن."), status=404)
        event = found[0]
    cid = store.new_conversation(_tid(), _aid(), event)
    if event:
        for kind, values in detectors.issued_from_event(event).items():
            store.issue(cid, _tid(), kind, values, f"event:{event['type']}")
    ctx = context.build_context(event)
    audit.record("conversation", conversation_id=cid, outcome="created",
                 details={"event_type": event["type"] if event else None,
                          "role": ctx["admin"]["role"]})
    return ok({"conversation_id": cid, "context": ctx,
               "context_message": _context_message(ctx)}, status=201)


def _context_message(ctx: dict) -> str:
    import json
    return "CONTEXT " + json.dumps(ctx, ensure_ascii=False)


def ops_conversation_context(cid: str):
    err = _gate()
    if err is not None:
        return err
    conv, err = _conversation(cid)
    if err is not None:
        return err
    from ...radius.services.ops_assistant import context
    ctx = context.build_context(conv.get("event"))
    return ok({"conversation_id": cid, "context": ctx, "context_message": _context_message(ctx)})


# ─────────────────────────── CHOICES ────────────────────────────

def _run_choices(conv: dict, source: str, args: dict):
    from ...radius.services.ops_assistant import audit, context
    from ...radius.services.ops_assistant import catalog
    if source in catalog.LIST_SOURCES and not context.action_permitted(source):
        return None, fail("forbidden", _tr("لا تملك صلاحية عرض هذه القائمة."), status=403,
                          details={"reason": "missing_permission", "source": source})
    try:
        if source == "list_plans":
            ch = context.list_plans(conv, str(args.get("query") or ""))
        elif source == "list_offers":
            ch = context.list_offers(conv, str(args.get("query") or ""))
        elif source == "find_subscriber":
            ch = context.find_subscriber(conv, str(args.get("query") or ""),
                                         str(args.get("status") or ""))
        elif source == "list_card_batches":
            ch = context.list_card_batches(conv, str(args.get("query") or ""), args.get("limit"))
        elif source == "change_plan_policies":
            ch = context.change_plan_policies(conv, str(args.get("username") or ""),
                                              args.get("plan_id"))
        else:
            return None, fail("validation_error", _tr("مصدر القائمة غير معروف."), status=422)
    except context.ChoiceError as e:
        return None, fail(e.code, e.message or e.code, status=e.status,
                          details=e.details if isinstance(e.details, dict) else {})
    audit.record("choices", conversation_id=conv["id"], outcome="issued",
                 details={"source": source, "count": len(ch.get("items", []))})
    return {"choices": ch, "tool_message": context.tool_message(ch)}, None


def ops_choices(cid: str):
    err = _gate()
    if err is not None:
        return err
    conv, err = _conversation(cid)
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    out, err = _run_choices(conv, str(body.get("source") or ""), body)
    if err is not None:
        return err
    return ok(out)


# ─────────────────────────── proposals ────────────────────────────

def _card(steps) -> list[dict]:
    from ...radius.services.ops_assistant import catalog
    acts = catalog.catalog()["actions"]
    out = []
    for i, st in enumerate(steps, start=1):
        values = {k: v for k, v in (st.body or {}).items() if k != "password"}
        out.append({"n": i, "action": st.action,
                    "title_ar": acts.get(st.action, {}).get("title_ar", st.action),
                    "danger": catalog.DANGER.get(st.action, "L2"),
                    "values": values, "display": st.display,
                    "password": "generated_and_shown_once" if st.needs_password else None,
                    "pending_refs": {k: f"$step{n}.{f}" for k, (n, f) in st.refs.items()},
                    "executable": st.executable})
    return out


def ops_proposal(cid: str):
    err = _gate()
    if err is not None:
        return err
    conv, err = _conversation(cid)
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    mode = str(body.get("mode") or "execute")
    if mode not in ("draft", "execute"):
        return fail("validation_error", _tr("mode يجب أن يكون draft أو execute."), status=422)
    from ...radius.services.ops_assistant import audit, catalog, executor, info, store
    from ...radius.services.ops_assistant.validator import ProposalRejected, validate_proposal
    proposal = body.get("proposal")
    try:
        v = validate_proposal(conv, proposal, mode=mode)
    except ProposalRejected as e:
        # attacker-controlled: never copy an unbounded value into the audit row
        viol = [x.as_dict() for x in e.violations[:MAX_VIOLATIONS]]
        act = proposal.get("action") if isinstance(proposal, dict) else ""
        audit.record("validate", conversation_id=cid, outcome="rejected",
                     action=str(act)[:40], details={"violations": viol})
        return fail("proposal_forbidden" if e.forbidden else "proposal_rejected",
                    e.violations[0].message, status=403 if e.forbidden else 422,
                    details={"violations": viol})

    if v.kind == "control":
        audit.record("validate", conversation_id=cid, outcome="control", action=v.action,
                     proposal_hash=v.proposal_hash)
        out: dict[str, Any] = {"kind": "control", "action": v.action}
        fields = proposal.get("fields") or {}
        if v.action == "choose" and fields.get("source") in catalog.LIST_SOURCES:
            res, err = _run_choices(conv, fields["source"], fields)
            if err is not None:
                return err
            out.update(res)
        return ok(out)
    if v.kind == "lookup":
        res, err = _run_choices(conv, v.action, proposal.get("fields") or {})
        if err is not None:
            return err
        return ok({"kind": "lookup", "action": v.action, **res})
    if v.kind == "info":
        result = info.run(conv, v.action, dict(proposal.get("fields") or {}))
        audit.record("info", conversation_id=cid,
                     outcome="error" if result.get("error") else "answered", action=v.action,
                     details={"error": result.get("error")} if result.get("error") else None)
        return ok({"kind": "info", "action": v.action, "result": result,
                   "tool_message": info.result_line(result)})

    saved = store.save_proposal(cid=cid, tenant_id=_tid(), admin_id=_aid(), action=v.action,
                                mode=mode, proposal=proposal, proposal_hash=v.proposal_hash)
    if mode == "draft":
        audit.record("draft", conversation_id=cid, outcome="draft", proposal_id=saved["id"],
                     proposal_hash=v.proposal_hash, action=v.action)
        return ok({"kind": v.kind, "level": 1, "proposal_id": saved["id"],
                   "proposal_hash": v.proposal_hash,
                   "draft": executor.draft_payload(v.steps)}, status=201)
    not_exec = [i + 1 for i, st in enumerate(v.steps) if not st.executable]
    audit.record("validate", conversation_id=cid, outcome="validated", proposal_id=saved["id"],
                 proposal_hash=v.proposal_hash, action=v.action)
    return ok({"kind": v.kind, "level": 3 if v.kind == "plan" else 2,
               "proposal_id": saved["id"], "proposal_hash": v.proposal_hash,
               "requires_confirmation": True, "confirmation": _card(v.steps),
               "not_executable_steps": not_exec}, status=201)


def ops_confirm(cid: str):
    err = _gate()
    if err is not None:
        return err
    conv, err = _conversation(cid)
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    from ...radius.services.ops_assistant import executor
    try:
        out = executor.confirm(conv, str(body.get("proposal_id") or ""),
                               body.get("proposal_hash"),
                               request.headers.get("Idempotency-Key") or "")
    except executor.ConfirmError as e:
        return fail(e.code, e.message, status=e.status,
                    details=e.details if isinstance(e.details, dict) else {})
    report = out["report"]
    data = {"report": report, "model_result": executor.model_result(report)}
    if out.get("show_once"):
        data["show_once"] = out["show_once"]
    return ok(data)


# ─────────────────────────── model turn (mobile app) ────────────────────────────

def _actor() -> str:
    name = getattr(g, "admin_username", None)
    if name:
        return str(name)
    try:
        from ...radius.db.connection import db
        row = db().execute("SELECT username FROM admins WHERE id=?", (_aid(),)).fetchone()
        return str(row["username"]) if row else ""
    except Exception:  # noqa: BLE001 — the actor is for the audit line only
        return ""


class _CallerApi:
    """``conversation.*`` caller bound to THIS request's own credential: every
    nested /api/v1 call authenticates and is authorised exactly like the app's
    request (tenant + admin come from the token, never from the body)."""

    def __init__(self) -> None:
        self.admin_id = _aid()
        self.tenant_id = _tid()
        self.username = _actor()

    def __call__(self, method: str, path: str, body=None, query=None, idempotency_key: str = ""):
        from ...radius.services.ops_assistant.dispatch import call
        return call(method, path, body=body, query=query, idempotency_key=idempotency_key)


def _turn(fn):
    from ...radius.services.ops_assistant.conversation import TurnError
    try:
        return fn()
    except TurnError as e:
        return fail(e.code, e.message or _tr("تعذّر تنفيذ الطلب."),
                    status=e.status if e.status >= 400 else 422)


def _lost():
    return fail("not_found", _tr("المحادثة غير موجودة — ابدأ محادثة جديدة."), status=404)


def _own_conversation(cid: Any):
    from ...radius.services.ops_assistant import store
    if not isinstance(cid, str) or not cid:
        return None
    return store.get_conversation(cid, _tid(), _aid())


def ops_assistant_message():
    err = _gate()
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return fail("validation_error", _tr("اكتب رسالة أوّلًا."), status=422)
    cid = body.get("conversation_id")
    if cid not in (None, "") and _own_conversation(cid) is None:
        return _lost()
    from ...radius.services.ops_assistant import conversation

    def run():
        api = _CallerApi()
        c = cid or conversation.start(api)["conversation_id"]
        replies = conversation.say(api, c, text, actor=api.username)
        return ok({"conversation_id": c, "replies": conversation.decorate(replies, _tid())})
    return _turn(run)


def ops_assistant_start_event():
    err = _gate()
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    etype = body.get("event_type")
    if not isinstance(etype, str) or not etype:
        return fail("validation_error", _tr("نوع الحدث مطلوب."), status=422)
    try:
        index = max(0, int(body.get("index") or 0))
    except (TypeError, ValueError):
        index = 0
    from ...radius.services.ops_assistant import conversation

    def run():
        api = _CallerApi()
        c = conversation.start(api, event_type=etype, index=index)["conversation_id"]
        replies = conversation.run_model(api, c, actor=api.username)
        return ok({"conversation_id": c, "replies": conversation.decorate(replies, _tid())})
    return _turn(run)


def ops_assistant_confirm():
    err = _gate()
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    cid = body.get("conversation_id")
    if _own_conversation(cid) is None:
        return _lost()
    pid, phash = body.get("proposal_id"), body.get("proposal_hash")
    if not isinstance(pid, str) or not isinstance(phash, str) or not pid or not phash:
        return fail("validation_error", _tr("تأكيد ناقص."), status=422)
    key = (request.headers.get("Idempotency-Key") or "").strip()
    from ...radius.services.ops_assistant import conversation

    def run():
        out = conversation.confirm(_CallerApi(), cid, pid, phash, idempotency_key=key)
        data = {"conversation_id": cid, "report": out["report"]}
        if out.get("show_once"):
            data["show_once"] = out["show_once"]       # this response only: never stored or logged
        resp, status = ok(data)
        resp.headers["Cache-Control"] = "no-store"
        return resp, status
    return _turn(run)


def ops_assistant_cancel():
    err = _gate()
    if err is not None:
        return err
    body, err = _body()
    if err is not None:
        return err
    cid = body.get("conversation_id")
    if _own_conversation(cid) is None:
        return _lost()
    from ...radius.services.ops_assistant import conversation
    conversation.cancel(_CallerApi(), cid)
    return ok({"conversation_id": cid})


__all__ = ["register"]
