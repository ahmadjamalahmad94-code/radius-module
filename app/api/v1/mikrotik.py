"""
endpoints إدارة اتصالات MikroTik + اختبار الـ connectivity.
يتيح لباقي البيئات (HobeHub, …) فحص حالة الـ MTs برمجيًا.
"""
from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, request

from ..auth import require_api_token
from ..responses import fail, ok


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/mikrotik", "mt_list",
                    require_api_token(mt_list), methods=["GET"])
    bp.add_url_rule("/mikrotik", "mt_add",
                    require_api_token(mt_add), methods=["POST"])
    bp.add_url_rule("/mikrotik/<int:cfg_id>", "mt_update",
                    require_api_token(mt_update), methods=["PATCH"])
    bp.add_url_rule("/mikrotik/<int:cfg_id>", "mt_delete",
                    require_api_token(mt_delete), methods=["DELETE"])
    bp.add_url_rule("/mikrotik/<int:cfg_id>/test", "mt_test",
                    require_api_token(mt_test), methods=["POST"])
    bp.add_url_rule("/mikrotik/test-credentials", "mt_test_creds",
                    require_api_token(mt_test_creds), methods=["POST"])


def _store():
    from app.radius.integration.mikrotik.settings import MikrotikConfigStore
    return MikrotikConfigStore.instance()


def _safe(cfg) -> dict:
    d = asdict(cfg)
    d.pop("password", None)
    return d


def mt_list():
    items = [_safe(c) for c in _store().list()]
    return ok({"items": items, "count": len(items)})


def mt_add():
    from app.radius.integration.mikrotik.settings import MikrotikConfig
    body = request.get_json(silent=True) or {}
    required = ("host", "username", "password")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return fail("validation_error", f"حقول مفقودة: {missing}",
                    status=422, details={"missing": missing})
    cfg = MikrotikConfig(
        id=None,
        name=body.get("name") or body["host"],
        host=body["host"],
        port=int(body.get("port") or 0) or (8729 if body.get("use_tls") else 8728),
        username=body["username"],
        password=body["password"],
        use_tls=bool(body.get("use_tls")),
        verify_tls=bool(body.get("verify_tls", True)),
        timeout_sec=int(body.get("timeout_sec") or 10),
        enabled=bool(body.get("enabled", True)),
    )
    saved = _store().add(cfg)
    return ok(_safe(saved), status=201)


def mt_update(cfg_id: int):
    body = request.get_json(silent=True) or {}
    saved = _store().update(cfg_id, **body)
    if not saved:
        return fail("not_found", f"mt {cfg_id} غير موجود", status=404)
    return ok(_safe(saved))


def mt_delete(cfg_id: int):
    _store().delete(cfg_id)
    return ok({"deleted": cfg_id})


def mt_test(cfg_id: int):
    cfg = _store().get(cfg_id)
    if not cfg:
        return fail("not_found", f"mt {cfg_id} غير موجود", status=404)
    return _do_test(cfg.host, cfg.port, cfg.username, cfg.password,
                    cfg.use_tls, cfg.verify_tls, cfg.timeout_sec)


def mt_test_creds():
    """اختبار اتصال بمعطيات مرسلة (دون حفظ)."""
    body = request.get_json(silent=True) or {}
    required = ("host", "username", "password")
    missing = [k for k in required if not body.get(k)]
    if missing:
        return fail("validation_error", f"مفقود: {missing}", status=422)
    return _do_test(
        body["host"], int(body.get("port") or 8728),
        body["username"], body["password"],
        bool(body.get("use_tls")),
        bool(body.get("verify_tls", True)),
        int(body.get("timeout_sec") or 10),
    )


def _do_test(host, port, user, pw, tls, verify, timeout):
    from app.radius.integration.mikrotik import (
        AuthError, ConnectError, MikrotikClient, MikrotikError,
    )
    try:
        with MikrotikClient(
            host=host, port=port, username=user, password=pw,
            use_tls=tls, verify_tls=verify, timeout=timeout,
        ) as c:
            identity = list(c.print_("/system/identity/print"))
            resource = list(c.print_("/system/resource/print",
                                     proplist=["board-name","version","uptime","cpu-load"]))
            return ok({
                "connected": True,
                "identity": identity[0] if identity else {},
                "resource": resource[0] if resource else {},
            })
    except AuthError as e:
        return fail("auth_error", str(e), status=401)
    except ConnectError as e:
        return fail("connect_error", str(e), status=502)
    except MikrotikError as e:
        return fail("mikrotik_error", str(e), status=502)
