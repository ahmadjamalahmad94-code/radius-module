"""خادم RADIUS بسيط لمصادقة حسابات VPN.

يستمع على UDP ويستقبل Access-Request من الوكيل المركزي ويُعيد Accept/Reject.

آليات المصادقة المدعومة:
- PAP (User-Password المشفّر بسر RADIUS)
- MS-CHAPv2 (يتطلب pycryptodome — يتحقق من NT hash)
- CHAP غير مدعوم (يحتاج كلمة المرور المُشفَّرة بـ MD5 + الـ challenge)

إعداد RouterOS (CHR):
    /ppp profile set default use-encryption=no         # للـ SSTP مع PAP
    /ppp aaa set use-radius=yes
    /radius add address=<proxy_ip> secret=<secret> service=ppp

المتغيرات البيئية:
    HOBERADIUS_RADIUS_LISTEN_HOST   (default: 0.0.0.0)
    HOBERADIUS_RADIUS_AUTH_PORT     (default: 1812)
    HOBERADIUS_RADIUS_ACCT_PORT     (default: 1813)
    HOBERADIUS_RADIUS_SECRET        (مطلوب — السر المشترك مع الوكيل)
    HOBERADIUS_TENANT_ID            (default: 1)
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
import logging
import os
import struct
from typing import Optional

LOG = logging.getLogger(__name__)

# ── RADIUS Constants ──────────────────────────────────────────────────────────
ACCESS_REQUEST      = 1
ACCESS_ACCEPT       = 2
ACCESS_REJECT       = 3
ACCOUNTING_REQUEST  = 4
ACCOUNTING_RESPONSE = 5

ATTR_USER_NAME         = 1
ATTR_USER_PASSWORD     = 2
ATTR_SESSION_TIMEOUT   = 27
ATTR_REPLY_MESSAGE     = 18
ATTR_VENDOR_SPECIFIC   = 26
ATTR_MS_CHAP_CHALLENGE = 60

VENDOR_MIKROTIK   = 14988
MT_RATE_LIMIT     = 8
VENDOR_MICROSOFT  = 311
MS_CHAP2_RESPONSE = 25

# ── Config ────────────────────────────────────────────────────────────────────

def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _shared_secret() -> bytes:
    s = _env("HOBERADIUS_RADIUS_SECRET")
    if not s:
        raise RuntimeError(
            "HOBERADIUS_RADIUS_SECRET غير مضبوط. "
            "أضفه إلى ملف البيئة: HOBERADIUS_RADIUS_SECRET=your-secret"
        )
    return s.encode("utf-8")


def _tenant_id() -> int:
    try:
        return int(_env("HOBERADIUS_TENANT_ID", "1"))
    except ValueError:
        return 1


# ── RADIUS packet parsing ─────────────────────────────────────────────────────

def _parse_attrs(data: bytes) -> list[tuple[int, bytes]]:
    attrs: list[tuple[int, bytes]] = []
    i = 0
    while i + 2 <= len(data):
        t = data[i]
        length = data[i + 1]
        if length < 2 or i + length > len(data):
            break
        attrs.append((t, data[i + 2: i + length]))
        i += length
    return attrs


def _parse_packet(data: bytes) -> dict | None:
    if len(data) < 20:
        return None
    code, ident, length = struct.unpack("!BBH", data[:4])
    if length < 20 or length > len(data):
        return None
    auth = data[4:20]
    attrs = _parse_attrs(data[20:length])
    return {"code": code, "id": ident, "auth": auth, "attrs": attrs}


def _get_attr(attrs: list, t: int) -> bytes | None:
    for at, av in attrs:
        if at == t:
            return av
    return None


def _get_str(attrs: list, t: int) -> str:
    v = _get_attr(attrs, t)
    return v.decode("utf-8", errors="replace") if v else ""


# ── User-Password decryption (RFC 2865 §5.2) ─────────────────────────────────

def _decrypt_user_password(enc: bytes, request_auth: bytes, secret: bytes) -> str:
    result = bytearray()
    prev = request_auth
    i = 0
    while i < len(enc):
        block = enc[i:i + 16]
        pad = hashlib.md5(secret + prev).digest()
        result.extend(b ^ p for b, p in zip(block, pad))
        prev = block
        i += 16
    return result.rstrip(b"\x00").decode("utf-8", errors="replace")


# ── MS-CHAPv2 verification (RFC 2759) ────────────────────────────────────────

def _nt_hash(password: str) -> bytes:
    pw_utf16 = password.encode("utf-16-le")
    try:
        return hashlib.new("md4", pw_utf16).digest()
    except ValueError:
        # md4 not available in this OpenSSL build
        return b""


def _challenge_hash(peer_challenge: bytes, auth_challenge: bytes, username: str) -> bytes:
    h = hashlib.sha1(peer_challenge + auth_challenge + username.encode("ascii", "replace"))
    return h.digest()[:8]


def _desl(hash21: bytes, data: bytes) -> bytes:
    """DESL per RFC 2759 §8.1 — three single-DES encryptions."""
    try:
        from Crypto.Cipher import DES

        def _single_des(key7: bytes) -> bytes:
            k = bytearray(8)
            k[0] = key7[0]
            k[1] = ((key7[0] & 0x01) << 7) | (key7[1] >> 1)
            k[2] = ((key7[1] & 0x03) << 6) | (key7[2] >> 2)
            k[3] = ((key7[2] & 0x07) << 5) | (key7[3] >> 3)
            k[4] = ((key7[3] & 0x0f) << 4) | (key7[4] >> 4)
            k[5] = ((key7[4] & 0x1f) << 3) | (key7[5] >> 5)
            k[6] = ((key7[5] & 0x3f) << 2) | (key7[6] >> 6)
            k[7] = (key7[6] & 0x7f) << 1
            return DES.new(bytes(k), DES.MODE_ECB).encrypt(data)

        padded = hash21.ljust(21, b"\x00")
        return _single_des(padded[0:7]) + _single_des(padded[7:14]) + _single_des(padded[14:21])
    except ImportError:
        return b""


def _get_ms_vendor_attr(attrs: list, ms_type: int) -> bytes | None:
    for at, av in attrs:
        if at == ATTR_VENDOR_SPECIFIC and len(av) >= 6:
            vendor = struct.unpack("!I", av[:4])[0]
            if vendor == VENDOR_MICROSOFT and av[4] == ms_type:
                return av[6:]
    return None


def _verify_ms_chapv2(username: str, password: str, attrs: list) -> bool:
    chap_challenge = _get_attr(attrs, ATTR_MS_CHAP_CHALLENGE)
    if not chap_challenge or len(chap_challenge) < 8:
        return False
    chap2_resp = _get_ms_vendor_attr(attrs, MS_CHAP2_RESPONSE)
    if not chap2_resp or len(chap2_resp) < 50:
        return False
    peer_challenge = chap2_resp[2:18]
    nt_response = chap2_resp[26:50]
    challenge = _challenge_hash(peer_challenge, chap_challenge, username)
    nt_hash = _nt_hash(password)
    if not nt_hash:
        return False
    hash21 = nt_hash.ljust(21, b"\x00")
    expected = _desl(hash21, challenge)
    if not expected:
        return False
    return _hmac.compare_digest(expected, nt_response)


# ── Response building ─────────────────────────────────────────────────────────

def _build_attr(t: int, v: bytes) -> bytes:
    return struct.pack("!BB", t, 2 + len(v)) + v


def _build_vendor_attr(vendor: int, vt: int, v: bytes) -> bytes:
    inner = struct.pack("!BB", vt, 2 + len(v)) + v
    return _build_attr(ATTR_VENDOR_SPECIFIC, struct.pack("!I", vendor) + inner)


def _response_authenticator(code: int, ident: int, length: int, request_auth: bytes, payload: bytes, secret: bytes) -> bytes:
    return hashlib.md5(
        struct.pack("!BBH", code, ident, length) + request_auth + payload + secret
    ).digest()


def _build_response(code: int, ident: int, request_auth: bytes, secret: bytes, attrs: list[bytes]) -> bytes:
    payload = b"".join(attrs)
    length = 20 + len(payload)
    resp_auth = _response_authenticator(code, ident, length, request_auth, payload, secret)
    return struct.pack("!BBH", code, ident, length) + resp_auth + payload


def _accept(ident: int, auth: bytes, secret: bytes, account: dict) -> bytes:
    attrs = [_build_attr(ATTR_SESSION_TIMEOUT, struct.pack("!I", 86400))]
    speed = account.get("speed_limit_mbps") or account.get("alloc_speed_mbps") or 0
    if speed:
        rate = f"{speed}M/{speed}M".encode("ascii")
        attrs.append(_build_vendor_attr(VENDOR_MIKROTIK, MT_RATE_LIMIT, rate))
    return _build_response(ACCESS_ACCEPT, ident, auth, secret, attrs)


def _reject(ident: int, auth: bytes, secret: bytes, msg: str = "") -> bytes:
    attrs = []
    if msg:
        attrs.append(_build_attr(ATTR_REPLY_MESSAGE, msg.encode("utf-8")[:253]))
    return _build_response(ACCESS_REJECT, ident, auth, secret, attrs)


def _acct_response(ident: int, auth: bytes, secret: bytes) -> bytes:
    return _build_response(ACCOUNTING_RESPONSE, ident, auth, secret, [])


# ── Protocol handler ──────────────────────────────────────────────────────────

class _RadiusProtocol(asyncio.DatagramProtocol):
    def __init__(self, secret: bytes, tenant_id: int):
        self.secret = secret
        self.tenant_id = tenant_id
        self.transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data: bytes, addr):
        packet = _parse_packet(data)
        if not packet:
            return
        if packet["code"] == ACCESS_REQUEST:
            self._handle_auth(packet, addr)
        elif packet["code"] == ACCOUNTING_REQUEST:
            self._handle_acct(packet, addr)

    def error_received(self, exc):
        LOG.warning("RADIUS socket error: %s", exc)

    def _handle_auth(self, pkt: dict, addr: tuple):
        ident = pkt["id"]
        auth = pkt["auth"]
        attrs = pkt["attrs"]
        username_full = _get_str(attrs, ATTR_USER_NAME)
        # Strip @realm suffix — the proxy already routed us by realm
        username = username_full.split("@")[0].lower() if "@" in username_full else username_full.lower()

        from .vpn_account_service import verify_password_for_radius, get_decrypted_password

        enc_pw = _get_attr(attrs, ATTR_USER_PASSWORD)
        if enc_pw:
            # ── PAP ──
            raw_pw = _decrypt_user_password(enc_pw, auth, self.secret)
            account = verify_password_for_radius(self.tenant_id, username, raw_pw)
            if account:
                LOG.info("ACCEPT PAP %s from %s", username_full, addr[0])
                self.transport.sendto(_accept(ident, auth, self.secret, account), addr)
            else:
                LOG.warning("REJECT PAP %s from %s", username_full, addr[0])
                self.transport.sendto(_reject(ident, auth, self.secret, "Access Denied"), addr)
            return

        chap2_resp = _get_ms_vendor_attr(attrs, MS_CHAP2_RESPONSE)
        if chap2_resp:
            # ── MS-CHAPv2 ──
            raw_pw = get_decrypted_password(self.tenant_id, username)
            if raw_pw and _verify_ms_chapv2(username, raw_pw, attrs):
                # Fetch full account for speed attributes
                from .vpn_account_service import get_decrypted_password as _  # already imported
                from ..db.connection import db
                row = db().execute(
                    """SELECT va.*, sam.speed_limit_mbps AS alloc_speed_mbps
                       FROM vpn_account va
                       JOIN service_allocation_mirror sam ON sam.id = va.allocation_mirror_id
                       WHERE va.tenant_id=? AND va.username=? AND va.status='active'
                         AND sam.status='active' LIMIT 1""",
                    (self.tenant_id, username),
                ).fetchone()
                account = dict(row) if row else {}
                LOG.info("ACCEPT MS-CHAPv2 %s from %s", username_full, addr[0])
                self.transport.sendto(_accept(ident, auth, self.secret, account), addr)
            else:
                LOG.warning("REJECT MS-CHAPv2 %s from %s", username_full, addr[0])
                self.transport.sendto(_reject(ident, auth, self.secret, "Access Denied"), addr)
            return

        LOG.warning("REJECT (no supported auth method) %s from %s", username_full, addr[0])
        self.transport.sendto(_reject(ident, auth, self.secret, "No supported auth"), addr)

    def _handle_acct(self, pkt: dict, addr: tuple):
        username = _get_str(pkt["attrs"], ATTR_USER_NAME)
        LOG.debug("ACCT %s from %s", username, addr[0])
        self.transport.sendto(_acct_response(pkt["id"], pkt["auth"], self.secret), addr)


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_server(
    host: str | None = None,
    auth_port: int | None = None,
    acct_port: int | None = None,
    tenant_id: int | None = None,
    secret: bytes | None = None,
) -> None:
    host = host or _env("HOBERADIUS_RADIUS_LISTEN_HOST", "0.0.0.0")
    auth_port = auth_port or int(_env("HOBERADIUS_RADIUS_AUTH_PORT", "1812"))
    acct_port = acct_port or int(_env("HOBERADIUS_RADIUS_ACCT_PORT", "1813"))
    tenant_id = tenant_id or _tenant_id()
    secret = secret or _shared_secret()

    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(
        lambda: _RadiusProtocol(secret, tenant_id),
        local_addr=(host, auth_port),
    )
    await loop.create_datagram_endpoint(
        lambda: _RadiusProtocol(secret, tenant_id),
        local_addr=(host, acct_port),
    )
    LOG.info(
        "RADIUS server on %s auth=%d acct=%d tenant=%d",
        host, auth_port, acct_port, tenant_id,
    )
    try:
        await asyncio.sleep(float("inf"))
    except (asyncio.CancelledError, KeyboardInterrupt):
        LOG.info("RADIUS server stopping.")
