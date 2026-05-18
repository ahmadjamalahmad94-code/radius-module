"""
OpenAPI 3.1 spec — يُولَّد من حلقة عبر الـ url_map.

يكشف:
- معلومات الـ API
- Bearer security
- شكل الـ envelope (ok / error)
- جميع الـ /api/v1/* routes
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template_string


def register(bp: Blueprint) -> None:
    bp.add_url_rule("/openapi.json", "openapi_json", openapi_json, methods=["GET"])
    bp.add_url_rule("/docs", "openapi_docs", openapi_docs, methods=["GET"])


def _build_spec() -> dict:
    paths: dict = {}
    for rule in current_app.url_map.iter_rules():
        if not rule.endpoint.startswith("api.v1."):
            continue
        # Flask rule converters: /<int:x> → OpenAPI {x}
        rule_str = rule.rule
        # تحويل بسيط
        import re
        path = re.sub(r"<(int:|string:|float:|path:)?([^>]+)>", r"{\2}", rule_str)
        methods = sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"})
        if not methods: continue
        node = paths.setdefault(path, {})
        for m in methods:
            node[m.lower()] = {
                "operationId": f"{rule.endpoint.split('.')[-1]}_{m.lower()}",
                "summary": rule.endpoint.split(".")[-1].replace("_", " "),
                "tags": [rule.endpoint.split(".")[-2] if rule.endpoint.count(".") >= 2 else "v1"],
                "security": [{"bearerAuth": []}] if rule.endpoint not in {"api.v1.health", "api.v1.version"} else [],
                "responses": {
                    "200": {"description": "OK", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}}},
                    "401": {"description": "Unauthorized", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "429": {"description": "Rate limited", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                    "500": {"description": "Internal error", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                },
            }
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "HobeRadius API",
            "description": "REST API لإدارة المشتركين والباقات والبطاقات والجلسات. يستخدم Bearer token مع scope لكل tenant.",
            "version": "0.1.0",
            "contact": {"name": "HobeRadius"},
        },
        "servers": [{"url": "/api"}],
        "tags": [
            {"name": "health"}, {"name": "accounts"}, {"name": "cards"},
            {"name": "profiles"}, {"name": "nas"}, {"name": "sessions"},
            {"name": "accounting"}, {"name": "webhooks"}, {"name": "mikrotik"},
        ],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "description": "Bearer + token"},
            },
            "schemas": {
                "Envelope": {
                    "type": "object",
                    "required": ["ok", "meta"],
                    "properties": {
                        "ok": {"type": "boolean"},
                        "data": {"type": "object"},
                        "meta": {
                            "type": "object",
                            "properties": {
                                "request_id": {"type": "string"},
                                "version": {"type": "string", "example": "v1"},
                            },
                        },
                    },
                },
                "Error": {
                    "type": "object",
                    "required": ["ok", "error", "meta"],
                    "properties": {
                        "ok": {"type": "boolean", "example": False},
                        "error": {
                            "type": "object",
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "details": {"type": "object"},
                            },
                        },
                        "meta": {"$ref": "#/components/schemas/Envelope/properties/meta"},
                    },
                },
            },
        },
        "paths": paths,
    }


def openapi_json():
    return jsonify(_build_spec())


_SWAGGER_HTML = """<!doctype html>
<html lang="ar" dir="rtl">
<head>
  <meta charset="utf-8">
  <title>HobeRadius API Docs</title>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
  <style>body{margin:0}</style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    window.ui = SwaggerUIBundle({
      url: "/api/openapi.json",
      dom_id: "#swagger-ui",
      deepLinking: true,
      docExpansion: "list",
      defaultModelsExpandDepth: 0,
    });
  </script>
</body>
</html>
"""


def openapi_docs():
    return render_template_string(_SWAGGER_HTML)
