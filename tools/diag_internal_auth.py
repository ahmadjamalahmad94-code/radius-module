"""tools/diag_internal_auth.py — CLI لاختبار /api/v1/internal/auth بدون FreeRADIUS.

يتجاوز X-Internal-Secret تمامًا — يستدعي policy_engine.authorize() مباشرة
داخل عملية Flask الفعلية كي يستخدم نفس الـ DB connection والـ env vars.

الاستخدام داخل الـ container:
    docker exec hoberadius python -m tools.diag_internal_auth user1000 123456
    docker exec hoberadius python -m tools.diag_internal_auth 1419 8769 --tenant 1
    docker exec hoberadius python -m tools.diag_internal_auth --user user1000 --pass 123456

يطبع:
    db_path
    tenant_id_used
    found_in (subscribers / cards / none)
    decision: ok, reason, message, reply_attrs

أمثلة على reason: user_not_found / password_wrong / disabled / expired /
outside_hours / outside_days / quota_exhausted / mac_mismatch / concurrent_limit.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# ـ Linux container stdout = utf-8 افتراضيًا. هذا الإصلاح لكي يعمل أيضًا على
# Windows console (cp1252) لمن يستخدم الـ tool محليًا قبل النشر.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def _main() -> int:
    ap = argparse.ArgumentParser(prog="diag_internal_auth",
                                  description="Diag tool for /api/v1/internal/auth")
    ap.add_argument("username", nargs="?", help="username (positional)")
    ap.add_argument("password", nargs="?", help="password (positional)")
    ap.add_argument("--user", help="username (named, overrides positional)")
    ap.add_argument("--pass", dest="pwd", help="password (named, overrides positional)")
    ap.add_argument("--tenant", type=int, default=1, help="tenant_id (default: 1)")
    args = ap.parse_args()

    username = (args.user or args.username or "").strip()
    password = (args.pwd or args.password or "")
    tenant_id = args.tenant

    if not username:
        print("ERROR: username required", file=sys.stderr)
        return 2

    # ـ نعدّ env قبل الاستيراد كي يلتقطه connection layer ـ
    os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")

    from app import create_app
    app = create_app()
    with app.app_context():
        from app.radius.db.connection import _resolve_db_path  # type: ignore
        from app.radius.db.repos import cards_repo, subscribers_repo
        from app.radius.services.policy_engine import AuthRequest, authorize

        db_path = _resolve_db_path()
        sub = subscribers_repo.get_subscriber(tenant_id, username)
        card = cards_repo.get_card_by_username(tenant_id, username) if not sub else None
        found_in = "subscribers" if sub else ("cards" if card else "none")

        decision = authorize(AuthRequest(
            username=username, password=password, tenant_id=tenant_id,
        ))

        out = {
            "db_path": db_path,
            "tenant_id": tenant_id,
            "username": username,
            "found_in": found_in,
            "subscriber_status": sub.status if sub else None,
            "card_revoked": bool(card.revoked) if card else None,
            "decision": {
                "ok": decision.ok,
                "reason": decision.reason,
                "message": decision.message,
                "reply_attrs": dict(decision.reply_attrs),
            },
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if decision.ok else 1


if __name__ == "__main__":
    sys.exit(_main())
