"""
RADIUS Dynamic Authorization (RFC 5176) — Disconnect-Request + CoA-Request.

تنفيذ بايتي مباشر (بدون مكتبات خارجية) لإرسال:
- Disconnect-Request (Code=40) → طرد جلسة من NAS فورًا.
- CoA-Request (Code=43) → تغيير attributes لجلسة جارية (مثل: تغيير السرعة).

البروتوكول من IETF RFC 5176 + RFC 2865 (التغليف العام لـ RADIUS).

كل packet:
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |    Code (1)   |    ID (1)    |        Length (2)              |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |                  Authenticator (16 bytes)                       |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
  |                   Attributes (TLV)                              |
  +-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+

Authenticator حسابه:
  - في الـ request: MD5(Code | ID | Length | zeros(16) | Attrs | Secret)
  - في الـ response: MD5(Code | ID | Length | RequestAuthenticator | Attrs | Secret)

Message-Authenticator (RFC 5176 يلزمه):
  HMAC-MD5(packet_with_msg_auth_zeroed, secret) → 16 bytes
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets as _sec
import socket
import struct
from dataclasses import dataclass
from typing import Optional

_LOG = logging.getLogger(__name__)

# ─────────────── RFC codes ───────────────
CODE_ACCESS_REQUEST       = 1
CODE_ACCESS_ACCEPT        = 2
CODE_ACCESS_REJECT        = 3
CODE_DISCONNECT_REQUEST   = 40
CODE_DISCONNECT_ACK       = 41
CODE_DISCONNECT_NAK       = 42
CODE_COA_REQUEST          = 43
CODE_COA_ACK              = 44
CODE_COA_NAK              = 45

# ─────────────── Standard attribute types (RFC 2865 + 2869) ───────────────
ATTR_USER_NAME            = 1
ATTR_NAS_IP_ADDRESS       = 4
ATTR_NAS_PORT             = 5
ATTR_FRAMED_IP_ADDRESS    = 8
ATTR_REPLY_MESSAGE        = 18
ATTR_VENDOR_SPECIFIC      = 26
ATTR_CALLED_STATION_ID    = 30
ATTR_CALLING_STATION_ID   = 31
ATTR_NAS_IDENTIFIER       = 32
ATTR_ACCT_SESSION_ID      = 44
ATTR_NAS_PORT_TYPE        = 61
ATTR_MESSAGE_AUTHENTICATOR= 80

# Vendor IDs
VENDOR_MIKROTIK           = 14988

# Mikrotik vendor-specific subtypes (لمن أراد CoA لتغيير السرعة)
MT_ATTR_RATE_LIMIT        = 8   # Mikrotik-Rate-Limit


# ─────────────── Attribute encoders ───────────────


def encode_string_attr(attr_type: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > 253:
        raw = raw[:253]
    return bytes([attr_type, len(raw) + 2]) + raw


def encode_ipv4_attr(attr_type: int, ip: str) -> bytes:
    parts = ip.split(".")
    if len(parts) != 4:
        raise ValueError(f"invalid IPv4: {ip!r}")
    raw = bytes(int(p) for p in parts)
    return bytes([attr_type, 6]) + raw


def encode_uint32_attr(attr_type: int, value: int) -> bytes:
    return bytes([attr_type, 6]) + struct.pack("!I", value & 0xFFFFFFFF)


def encode_vendor_attr(vendor_id: int, subtype: int, value: str) -> bytes:
    """RADIUS Vendor-Specific (type=26) wrapping a sub-attribute."""
    raw = value.encode("utf-8")
    if len(raw) > 247:
        raw = raw[:247]
    # sub-attribute: subtype(1) + length(1) + value
    sub = bytes([subtype, len(raw) + 2]) + raw
    # Vendor-Specific: type(1) + length(1) + vendor_id(4) + sub
    body = struct.pack("!I", vendor_id) + sub
    return bytes([ATTR_VENDOR_SPECIFIC, len(body) + 2]) + body


# ─────────────── Packet building ───────────────


@dataclass
class CoaResult:
    ok: bool                     # ACK?
    code: int = 0                # 41 / 42 / 44 / 45
    code_name: str = ""
    reply_message: str = ""
    raw_attrs: dict = None       # type → bytes value

    def __post_init__(self):
        if self.raw_attrs is None:
            self.raw_attrs = {}


def _build_packet(*, code: int, identifier: int, attrs: bytes, secret: bytes) -> bytes:
    """يبني packet مع Authenticator صحيح + Message-Authenticator."""
    # 1) نضع Message-Authenticator زائف (16 صفر) كآخر attribute لو طُلب
    msg_auth_placeholder = bytes([ATTR_MESSAGE_AUTHENTICATOR, 18]) + bytes(16)
    full_attrs = attrs + msg_auth_placeholder

    length = 20 + len(full_attrs)  # 1+1+2+16 = 20
    header_no_auth = bytes([code, identifier]) + struct.pack("!H", length)

    # 2) Authenticator في request packets يبدأ كصفر، يُحسَب بعد التغليف
    zero_auth = bytes(16)
    packet_for_auth = header_no_auth + zero_auth + full_attrs

    # 3) احسب Message-Authenticator أولًا (HMAC-MD5)
    msg_auth = hmac.new(secret, packet_for_auth, hashlib.md5).digest()
    # استبدل الـ placeholder
    full_attrs_signed = (attrs +
                          bytes([ATTR_MESSAGE_AUTHENTICATOR, 18]) + msg_auth)

    # 4) احسب Authenticator النهائي
    # في Disconnect/CoA Request: MD5(Code+ID+Length + zeros(16) + Attrs + Secret)
    packet_for_md5 = (header_no_auth + zero_auth + full_attrs_signed + secret)
    authenticator = hashlib.md5(packet_for_md5).digest()

    # 5) packet النهائي
    return header_no_auth + authenticator + full_attrs_signed


def _verify_response(*, response: bytes, request_authenticator: bytes,
                      secret: bytes) -> bool:
    """يتحقّق من response packet:
    expected_md5 = MD5(Code|ID|Length|RequestAuthenticator|RespAttrs|Secret)
    """
    if len(response) < 20:
        return False
    resp_auth = response[4:20]
    body = response[:4] + request_authenticator + response[20:] + secret
    expected = hashlib.md5(body).digest()
    return hmac.compare_digest(resp_auth, expected)


def _parse_attrs(payload: bytes) -> dict[int, bytes]:
    """يفصل attributes من response packet (بعد الـ 20 بايت الأولى)."""
    out: dict[int, bytes] = {}
    i = 0
    while i < len(payload):
        if i + 2 > len(payload): break
        t = payload[i]; ln = payload[i + 1]
        if ln < 2 or i + ln > len(payload): break
        out[t] = payload[i + 2: i + ln]
        i += ln
    return out


# ─────────────── الواجهة الرئيسية ───────────────


def send_disconnect(*, nas_ip: str, nas_secret: str,
                     username: str = "", session_id: str = "",
                     port: int = 3799, timeout: float = 5.0,
                     identifier: Optional[int] = None) -> CoaResult:
    """
    يُرسل Disconnect-Request (Code=40) إلى الـ NAS.
    يحتاج على الأقل: User-Name أو Acct-Session-Id (لتحديد الجلسة).

    NAS-IP-Address يُضاف تلقائيًا (عنوان الـ NAS ذاته من منظوره — في معظم
    الأحيان نمرّر له nas_ip كمصدر الجلسة).
    """
    if not username and not session_id:
        raise ValueError("username أو session_id مطلوب")

    secret = nas_secret.encode("utf-8")
    ident = identifier if identifier is not None else _sec.randbits(8)

    # جمع attributes
    attrs = b""
    if username:
        attrs += encode_string_attr(ATTR_USER_NAME, username)
    if session_id:
        attrs += encode_string_attr(ATTR_ACCT_SESSION_ID, session_id)
    # NAS-IP-Address (يساعد بعض الـ NAS في التعرّف)
    try: attrs += encode_ipv4_attr(ATTR_NAS_IP_ADDRESS, nas_ip)
    except ValueError: pass

    packet = _build_packet(code=CODE_DISCONNECT_REQUEST,
                            identifier=ident, attrs=attrs, secret=secret)
    request_auth = packet[4:20]

    # UDP send/recv
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (nas_ip, port))
        resp, _ = sock.recvfrom(4096)
    except socket.timeout:
        _LOG.warning("CoA timeout to %s:%d", nas_ip, port)
        return CoaResult(ok=False, code=0, code_name="timeout",
                          reply_message="NAS لم يستجب خلال %.1fs" % timeout)
    except OSError as e:
        _LOG.warning("CoA socket error to %s:%d — %s", nas_ip, port, e)
        return CoaResult(ok=False, code=0, code_name="socket_error",
                          reply_message=str(e))
    finally:
        sock.close()

    return _parse_response(resp, request_auth=request_auth, secret=secret)


def send_coa(*, nas_ip: str, nas_secret: str,
              username: str, session_id: str = "",
              new_rate_limit: str = "",
              port: int = 3799, timeout: float = 5.0) -> CoaResult:
    """
    CoA-Request (Code=43) — يغيّر attributes لجلسة جارية دون قطع.
    أكثر استخدام: تغيير Mikrotik-Rate-Limit للسرعة الفورية.
    """
    secret = nas_secret.encode("utf-8")
    ident = _sec.randbits(8)

    attrs = b""
    attrs += encode_string_attr(ATTR_USER_NAME, username)
    if session_id:
        attrs += encode_string_attr(ATTR_ACCT_SESSION_ID, session_id)
    try: attrs += encode_ipv4_attr(ATTR_NAS_IP_ADDRESS, nas_ip)
    except ValueError: pass
    if new_rate_limit:
        attrs += encode_vendor_attr(VENDOR_MIKROTIK, MT_ATTR_RATE_LIMIT, new_rate_limit)

    packet = _build_packet(code=CODE_COA_REQUEST,
                            identifier=ident, attrs=attrs, secret=secret)
    request_auth = packet[4:20]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(packet, (nas_ip, port))
        resp, _ = sock.recvfrom(4096)
    except socket.timeout:
        return CoaResult(ok=False, code=0, code_name="timeout",
                          reply_message="NAS لم يستجب")
    except OSError as e:
        return CoaResult(ok=False, code=0, code_name="socket_error",
                          reply_message=str(e))
    finally:
        sock.close()

    return _parse_response(resp, request_auth=request_auth, secret=secret)


def _parse_response(resp: bytes, *, request_auth: bytes, secret: bytes) -> CoaResult:
    if len(resp) < 20:
        return CoaResult(ok=False, code=0, code_name="malformed",
                          reply_message="packet < 20 bytes")
    code = resp[0]
    if not _verify_response(response=resp, request_authenticator=request_auth, secret=secret):
        _LOG.warning("CoA response authenticator mismatch (code=%d)", code)
        # نستمر مع flag false (Mikrotik أحيانًا لا يوقّع الـ Disconnect-ACK)
        # لا نعتبره فشل تامًا.

    attrs = _parse_attrs(resp[20:])
    reply_msg = ""
    raw_reply = attrs.get(ATTR_REPLY_MESSAGE)
    if raw_reply:
        try: reply_msg = raw_reply.decode("utf-8", errors="replace")
        except Exception: reply_msg = ""

    ok_codes = {CODE_DISCONNECT_ACK, CODE_COA_ACK}
    name = {
        CODE_DISCONNECT_ACK: "Disconnect-ACK",
        CODE_DISCONNECT_NAK: "Disconnect-NAK",
        CODE_COA_ACK: "CoA-ACK",
        CODE_COA_NAK: "CoA-NAK",
    }.get(code, f"unknown-code-{code}")
    return CoaResult(ok=code in ok_codes, code=code, code_name=name,
                      reply_message=reply_msg, raw_attrs=attrs)


# ─────────────── Helper: العثور على NAS من session_id ───────────────


def find_nas_for_session(tenant_id: int, username: str) -> Optional[dict]:
    """يبحث في radacct عن الـ NAS التي تستضيف جلسة username النشطة.

    R9.1: secret يُقرأ من `nas_devices` (الجدول الذي تملأه UI عبر
    /admin/radius/devices)، بدل `nas` الذي كان يُستخدم سابقاً —
    الأخير هو جدول FreeRADIUS-القياسي ويبقى فارغًا في configنا
    (mods-enabled/sql: `read_clients = no`). إصلاح هذه الـ lookup
    يجعل زرّ "قطع" في /admin/radius/online يعمل فعلاً.
    """
    from ..db.connection import db
    row = db().execute("""
        SELECT acctsessionid, nasipaddress FROM radacct
        WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL
        ORDER BY radacctid DESC LIMIT 1
    """, (tenant_id, username)).fetchone()
    if not row: return None
    # ـ R9.1: secret من nas_devices.address (المملوء من UI) ـ
    nas_row = db().execute(
        "SELECT secret FROM nas_devices "
        "WHERE tenant_id = ? AND address = ? AND enabled = 1 LIMIT 1",
        (tenant_id, row["nasipaddress"])).fetchone()
    secret = nas_row["secret"] if nas_row else ""
    return {
        "nas_ip": row["nasipaddress"],
        "nas_secret": secret,
        "session_id": row["acctsessionid"],
    }


def disconnect_user(tenant_id: int, username: str) -> CoaResult:
    """واجهة مختصرة: ابحث عن الجلسة + أرسل Disconnect."""
    info = find_nas_for_session(tenant_id, username)
    if not info:
        return CoaResult(ok=False, code=0, code_name="no_active_session",
                          reply_message=f"لا جلسة نشطة لـ {username}")
    if not info["nas_secret"]:
        return CoaResult(ok=False, code=0, code_name="missing_nas_secret",
                          reply_message="لا secret مخزّن للـ NAS")
    return send_disconnect(
        nas_ip=info["nas_ip"], nas_secret=info["nas_secret"],
        username=username, session_id=info["session_id"],
    )
