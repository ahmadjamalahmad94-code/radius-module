"""مسارات «مطابقة احتساب الكروت» — أداة المالك لإصلاح صلاحية الكروت المُستورَدة.

  • GET  /admin/radius/cards/reconcile-accounting        — صفحة الأداة.
  • POST /admin/radius/cards/reconcile-accounting/plan   — تشغيل جافّ (dry-run):
        يَعرض ما سيتغيّر (صلاحية كل كرت = أول اتصال + الميزانية) **بلا أيّ كتابة**.
  • POST /admin/radius/cards/reconcile-accounting/apply  — نسخة احتياطيّة
        إلزاميّة موثَّقة أوّلًا، ثمّ تطبيق ذرّيّ (extend-only، مُتساوي القوى).

أمان: **المالك وحده** (الحارس المركزيّ __super__ + ``_require_owner`` دفاعًا
عميقًا). لا تُطبَّق أيّ كتابة إلّا بعد نجاح النسخة الاحتياطيّة والتحقّق منها؛
والتطبيق «يُطيل فقط» صلاحية الكرت (لا يُقصّرها أبدًا) وهو مُتساوي القوى (تشغيله
مرّتين لا يغيّر شيئًا في الثانية). عمليّة واحدة في المرّة (قفل).
"""
from __future__ import annotations

import logging
import os
import threading

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from ..auth.decorators import login_required
from ..core.tenant import DEFAULT_TENANT_ID
from ..services.card_accounting_reconcile import (
    get_card_accounting_reconcile_service,
)

_LOG = logging.getLogger(__name__)

# كلمة التأكيد الحرفيّة قبل التطبيق.
CONFIRM_WORD = "مطابقة"

_RECONCILE_LOCK = threading.Lock()


def register_card_accounting_reconcile_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/cards/reconcile-accounting", "cards_reconcile_accounting_page",
                    login_required(reconcile_page), methods=["GET"])
    bp.add_url_rule("/cards/reconcile-accounting/plan", "cards_reconcile_accounting_plan",
                    login_required(reconcile_plan), methods=["POST"])
    bp.add_url_rule("/cards/reconcile-accounting/apply", "cards_reconcile_accounting_apply",
                    login_required(reconcile_apply), methods=["POST"])


def _tid() -> int:
    try:
        return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
    except (TypeError, ValueError):
        return DEFAULT_TENANT_ID


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "owner"


def _current_admin_id() -> int | None:
    from ..auth.session_helpers import current_admin_id
    return current_admin_id()


def _require_owner() -> None:
    from ..db.repos import admins_repo
    if not admins_repo.is_primary_owner(_current_admin_id()):
        abort(403)


def reconcile_page():
    _require_owner()
    return render_template(
        "radius/cards_reconcile_accounting.html",
        confirm_word=CONFIRM_WORD,
    )


def reconcile_plan():
    """Dry-run: report proposed changes without touching the DB."""
    _require_owner()
    rp = get_card_accounting_reconcile_service().plan(_tid())
    return jsonify({"ok": True, "plan": rp.public_dict()})


def reconcile_apply():
    """Backup-first, atomic, idempotent apply."""
    _require_owner()
    payload = request.get_json(silent=True) or {}
    confirm = str(payload.get("confirm") or "").strip()
    if confirm != CONFIRM_WORD:
        return jsonify({"ok": False, "code": "confirm",
                        "message": f"للمتابعة اكتب كلمة التأكيد «{CONFIRM_WORD}» بالضبط."}), 200

    if not _RECONCILE_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "code": "busy",
                        "message": "هناك عمليّة مطابقة جارية بالفعل. انتظر انتهاءها."}), 200
    try:
        t = _tid()
        actor = _actor()
        # ── (1) نسخة احتياطيّة إلزاميّة موثَّقة أوّلًا ──
        from ..services.operations import get_operations_service
        try:
            bk = get_operations_service().run_local_backup(
                tenant_id=t, actor=actor, lean=False)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("reconcile backup crashed")
            return jsonify({"ok": False, "code": "backup_failed",
                            "message": "تعذّر إنشاء نسخة احتياطيّة — أُلغيت المطابقة "
                                       "ولم يتغيّر شيء.",
                            "detail": str(exc)}), 200
        if not bk.get("verified"):
            msg = (bk.get("run") or {}).get("message") or "فشل التحقّق من النسخة."
            return jsonify({"ok": False, "code": "backup_failed",
                            "message": "تعذّر إنشاء نسخة احتياطيّة موثوقة — أُلغيت "
                                       "المطابقة ولم يتغيّر شيء.",
                            "detail": msg}), 200
        backup_name = os.path.basename((bk.get("run") or {}).get("path") or "")

        # ── (2) التطبيق الذرّيّ (extend-only، مُتساوي القوى) ──
        try:
            report = get_card_accounting_reconcile_service().apply(
                t, actor, allow_apply=True)
        except Exception as exc:  # noqa: BLE001
            _LOG.exception("reconcile apply failed")
            return jsonify({"ok": False, "code": "error", "backup": backup_name,
                            "message": f"تعذّرت المطابقة — راجع السجلّ. {exc}",
                            "detail": str(exc)}), 200

        # ── (3) تدقيق ──
        try:
            from ..services.audit import get_audit_service
            get_audit_service().record(
                actor=actor, action="cards.reconcile_accounting",
                target_type="tenant", target_id=str(t),
                payload={"backup": backup_name,
                         "applied_cards": report.get("applied_cards", 0),
                         "applied_batches": report.get("applied_batches", 0)})
        except Exception:  # noqa: BLE001
            pass
        report["ok"] = True
        report["backup"] = backup_name
        report["message"] = "تمّت المطابقة بنجاح."
        return jsonify(report)
    finally:
        _RECONCILE_LOCK.release()
