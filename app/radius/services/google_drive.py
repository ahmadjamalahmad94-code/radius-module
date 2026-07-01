"""Google Drive backup integration for the RADIUS instance — Device Flow.

The radius server has no public HTTPS domain (it is reached at http://IP),
so a normal OAuth redirect flow is impossible. Instead we use Google's
OAuth 2.0 Limited-Input Device flow: the operator opens google.com/device,
enters a short code, authorises, and the radius polls for the token. No
redirect URI / public URL is required.

Everything here uses only the standard library + cryptography (already a
dependency). The per-install refresh token is stored ENCRYPTED in tenant
settings. Backups upload directly from the radius to the operator's own
Drive folder (scope drive.file).
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from app.radius.db.repos import tenants_repo

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart"
SCOPE = "https://www.googleapis.com/auth/drive.file email"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
FOLDER_NAME = "HobeRadius Backups"

# settings keys (tenant-scoped)
K_CLIENT_ID = "google_drive.client_id"
K_CLIENT_SECRET = "google_drive.client_secret"
K_REFRESH = "google_drive.refresh_token_enc"
K_EMAIL = "google_drive.email"
K_FOLDER = "google_drive.folder_id"
K_CONNECTED = "google_drive.connected"
K_DEVICE = "google_drive.device_code_enc"
K_DEVICE_AT = "google_drive.device_started_at"
K_LAST_UPLOAD = "google_drive.last_upload_at"
K_LAST_ERROR = "google_drive.last_error"


def _get(tid: int, key: str, default: str = "") -> str:
    return tenants_repo.get_setting(tid, key, default) or default


def _set(tid: int, key: str, value: str) -> None:
    tenants_repo.set_setting(tid, key, value or "")


# ── encryption ──────────────────────────────────────────────────────
def _fernet():
    from cryptography.fernet import Fernet
    from flask import current_app

    secret = str(current_app.config.get("SECRET_KEY") or current_app.secret_key or "hoberadius-radius").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def _encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode("utf-8")).decode("ascii") if text else ""


def _decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8") if token else ""


# ── config ──────────────────────────────────────────────────────────
def oauth_client(tid: int) -> tuple[str, str]:
    return _get(tid, K_CLIENT_ID).strip(), _get(tid, K_CLIENT_SECRET).strip()


def is_configured(tid: int) -> bool:
    cid, csec = oauth_client(tid)
    return bool(cid and csec)


def save_client(tid: int, client_id: str, client_secret: str) -> None:
    _set(tid, K_CLIENT_ID, (client_id or "").strip())
    if client_secret:  # keep existing if blank
        _set(tid, K_CLIENT_SECRET, client_secret.strip())


# ── HTTP helper ─────────────────────────────────────────────────────
def _post_form(url: str, data: dict[str, Any], timeout: int = 15) -> tuple[int, dict[str, Any]]:
    body = urllib.parse.urlencode(data).encode("ascii")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            return exc.code, {"error": "http_error", "error_description": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return 0, {"error": "unavailable", "error_description": str(exc)}


# ── device flow ─────────────────────────────────────────────────────
def start_device_flow(tid: int) -> dict[str, Any]:
    cid, _ = oauth_client(tid)
    if not is_configured(tid):
        return {"ok": False, "error": "not_configured"}
    status_code, data = _post_form(DEVICE_CODE_URL, {"client_id": cid, "scope": SCOPE})
    if status_code != 200 or "device_code" not in data:
        return {"ok": False, "error": data.get("error") or "device_start_failed",
                "detail": data.get("error_description") or ""}
    _set(tid, K_DEVICE, _encrypt(data["device_code"]))
    _set(tid, K_DEVICE_AT, str(int(time.time())))
    return {
        "ok": True,
        "user_code": data.get("user_code"),
        "verification_url": data.get("verification_url") or data.get("verification_uri") or "https://www.google.com/device",
        "expires_in": data.get("expires_in", 1800),
        "interval": data.get("interval", 5),
    }


def poll_device_flow(tid: int) -> dict[str, Any]:
    cid, csec = oauth_client(tid)
    device_code = _decrypt(_get(tid, K_DEVICE))
    if not device_code:
        return {"ok": False, "error": "no_pending_request"}
    status_code, data = _post_form(TOKEN_URL, {
        "client_id": cid, "client_secret": csec,
        "device_code": device_code, "grant_type": DEVICE_GRANT,
    })
    if status_code == 200 and data.get("refresh_token"):
        _set(tid, K_REFRESH, _encrypt(data["refresh_token"]))
        _set(tid, K_CONNECTED, "1")
        _set(tid, K_DEVICE, "")
        _set(tid, K_LAST_ERROR, "")
        email = _fetch_email(data.get("access_token") or "")
        if email:
            _set(tid, K_EMAIL, email)
        return {"ok": True, "connected": True, "email": email}
    err = data.get("error") or "pending"
    # authorization_pending / slow_down → keep waiting
    if err in {"authorization_pending", "slow_down"}:
        return {"ok": False, "pending": True, "error": err}
    return {"ok": False, "pending": False, "error": err,
            "detail": data.get("error_description") or ""}


def _fetch_email(access_token: str) -> str:
    if not access_token:
        return ""
    try:
        req = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return str(json.loads(resp.read().decode("utf-8")).get("email") or "")
    except Exception:
        return ""


# ── token refresh + drive ───────────────────────────────────────────
def _access_token(tid: int) -> str:
    cid, csec = oauth_client(tid)
    refresh = _decrypt(_get(tid, K_REFRESH))
    if not refresh:
        raise RuntimeError("not_connected")
    status_code, data = _post_form(TOKEN_URL, {
        "client_id": cid, "client_secret": csec,
        "refresh_token": refresh, "grant_type": "refresh_token",
    })
    if status_code != 200 or not data.get("access_token"):
        raise RuntimeError(data.get("error_description") or data.get("error") or "token_refresh_failed")
    return data["access_token"]


def _ensure_folder(tid: int, access_token: str) -> str:
    folder_id = _get(tid, K_FOLDER)
    if folder_id:
        return folder_id
    meta = json.dumps({"name": FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}).encode("utf-8")
    req = urllib.request.Request(
        DRIVE_FILES_URL + "?fields=id", data=meta, method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        fid = str(json.loads(resp.read().decode("utf-8")).get("id") or "")
    if fid:
        _set(tid, K_FOLDER, fid)
    return fid


def upload_backup(tid: int, file_path: str | Path, filename: str) -> dict[str, Any]:
    if _get(tid, K_CONNECTED) != "1":
        return {"ok": False, "error": "not_connected"}
    path = Path(file_path)
    if not path.exists():
        return {"ok": False, "error": "file_missing"}
    try:
        token = _access_token(tid)
        folder_id = _ensure_folder(tid, token)
        meta = {"name": filename, "parents": [folder_id] if folder_id else []}
        boundary = "hbrbnd" + hashlib.sha1(str(time.time()).encode()).hexdigest()[:16]
        body = bytearray()
        body += f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode("utf-8")
        body += json.dumps(meta).encode("utf-8") + b"\r\n"
        ctype = "application/gzip" if str(filename).lower().endswith(".gz") else "application/x-sqlite3"
        body += f"--{boundary}\r\nContent-Type: {ctype}\r\n\r\n".encode("utf-8")
        body += path.read_bytes() + b"\r\n"
        body += f"--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            DRIVE_UPLOAD_URL + "&fields=id", data=bytes(body), method="POST",
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": f"multipart/related; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            fid = str(json.loads(resp.read().decode("utf-8")).get("id") or "")
        _set(tid, K_LAST_UPLOAD, time.strftime("%Y-%m-%d %H:%M:%S"))
        _set(tid, K_LAST_ERROR, "")
        return {"ok": True, "file_id": fid}
    except Exception as exc:  # noqa: BLE001
        _set(tid, K_LAST_ERROR, str(exc)[:300])
        return {"ok": False, "error": str(exc)}


def disconnect(tid: int) -> None:
    refresh = _decrypt(_get(tid, K_REFRESH))
    if refresh:
        try:
            _post_form(REVOKE_URL, {"token": refresh}, timeout=8)
        except Exception:
            pass
    for k in (K_REFRESH, K_EMAIL, K_FOLDER, K_DEVICE, K_DEVICE_AT, K_LAST_UPLOAD, K_LAST_ERROR):
        _set(tid, k, "")
    _set(tid, K_CONNECTED, "0")


def status(tid: int) -> dict[str, Any]:
    return {
        "configured": is_configured(tid),
        "connected": _get(tid, K_CONNECTED) == "1",
        "email": _get(tid, K_EMAIL),
        "folder_name": FOLDER_NAME,
        "last_upload_at": _get(tid, K_LAST_UPLOAD),
        "last_error": _get(tid, K_LAST_ERROR),
        "pending": bool(_get(tid, K_DEVICE)),
    }
