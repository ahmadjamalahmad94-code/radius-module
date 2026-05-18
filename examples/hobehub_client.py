"""
hobehub_client.py — مرجع SDK لاستخدام HobeRadius من جهة HobeHub.

ضعه لاحقًا في:  HobeHub/app/services/hoberadius_client.py

يستخدم urllib فقط حتى لا يضيف dependency على HobeHub.
"""
from __future__ import annotations

import hmac
import hashlib
import json
import os
import urllib.error
import urllib.request
from typing import Any, Optional


class HobeRadiusError(Exception):
    def __init__(self, code: str, message: str, status: int, details: Optional[dict] = None):
        super().__init__(f"[{status} {code}] {message}")
        self.code = code
        self.status = status
        self.details = details or {}


class HobeRadiusClient:
    """
    Thin client للـ HobeRadius API.

    التهيئة من env:
        HOBERADIUS_BASE_URL   = https://radius.example.com
        HOBERADIUS_API_TOKEN  = <bearer>

    استخدام:
        c = HobeRadiusClient()
        c.health()
        c.list_online_sessions()
        c.create_account(username="u1", password="x", profile_id=2,
                         beneficiary_ref="1234", idempotency_key="ben-1234-create")
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout: int = 10,
    ) -> None:
        self.base_url = (base_url or os.environ.get("HOBERADIUS_BASE_URL", "")).rstrip("/")
        self.token = token or os.environ.get("HOBERADIUS_API_TOKEN", "")
        self.timeout = timeout
        if not self.base_url:
            raise ValueError("HOBERADIUS_BASE_URL مفقود")
        if not self.token:
            raise ValueError("HOBERADIUS_API_TOKEN مفقود")

    # ─────────────── core HTTP ───────────────

    def _request(
        self, method: str, path: str, *, json_body: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> dict:
        url = f"{self.base_url}/api/v1{path}"
        body = json.dumps(json_body).encode("utf-8") if json_body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8") or "{}")
                if not payload.get("ok"):
                    err = payload.get("error", {})
                    raise HobeRadiusError(
                        err.get("code", "unknown"), err.get("message", ""),
                        resp.status, err.get("details", {}),
                    )
                return payload.get("data") or {}
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
                err = payload.get("error", {})
                raise HobeRadiusError(
                    err.get("code", "http_error"), err.get("message", str(e)),
                    e.code, err.get("details", {}),
                ) from None
            except (ValueError, AttributeError):
                raise HobeRadiusError("http_error", str(e), e.code) from None

    # ─────────────── helpers per resource ───────────────

    def health(self) -> dict:
        return self._request("GET", "/health")

    # accounts
    def create_account(self, *, username: str, password: str, profile_id: int,
                       beneficiary_ref: str = "", idempotency_key: Optional[str] = None) -> dict:
        return self._request("POST", "/accounts",
                             json_body={"username": username, "password": password,
                                        "profile_id": profile_id,
                                        "beneficiary_ref": beneficiary_ref},
                             idempotency_key=idempotency_key)

    def disable_account(self, username: str) -> dict:
        return self._request("POST", f"/accounts/{username}/disable")

    def reset_password(self, username: str, new_password: str) -> dict:
        return self._request("POST", f"/accounts/{username}/reset_password",
                             json_body={"new_password": new_password})

    # cards
    def generate_cards(self, *, category_code: str, count: int = 1,
                       idempotency_key: Optional[str] = None) -> dict:
        return self._request("POST", "/cards/generate",
                             json_body={"category_code": category_code, "count": count},
                             idempotency_key=idempotency_key)

    # sessions
    def list_online_sessions(self) -> list[dict]:
        return self._request("GET", "/sessions/online").get("items", [])

    def disconnect(self, username: str, session_id: Optional[str] = None) -> dict:
        return self._request("POST", "/sessions/disconnect",
                             json_body={"username": username, "session_id": session_id})

    # nas/profiles
    def list_nas(self) -> list[dict]:
        return self._request("GET", "/nas").get("items", [])

    def list_profiles(self) -> list[dict]:
        return self._request("GET", "/profiles").get("items", [])


# ─────────────── webhook verification (للـ HobeHub جهة الاستقبال) ───────────────


def verify_webhook_signature(body: bytes, signature_header: str, secret: str) -> bool:
    """
    يستخدم في HobeHub داخل الـ view الذي يستقبل webhooks من HobeRadius:

        @app.post("/webhooks/radius")
        def _():
            raw = request.get_data()
            sig = request.headers.get("X-HobeRadius-Signature", "")
            if not verify_webhook_signature(raw, sig, os.environ["HOBERADIUS_WEBHOOK_SECRET"]):
                abort(401)
            ...
    """
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
