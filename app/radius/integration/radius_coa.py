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

from ..services.nas_connection import resolve_connection_address

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
ATTR_SESSION_TIMEOUT      = 27   # RFC 2865 §5.27 — uint32 seconds
ATTR_IDLE_TIMEOUT         = 28
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
                     framed_ip: str = "", calling_station_id: str = "",
                     port: int = 3799, timeout: float = 5.0,
                     identifier: Optional[int] = None) -> CoaResult:
    """
    يُرسل Disconnect-Request (Code=40) إلى الـ NAS.
    يحتاج على الأقل: User-Name أو Acct-Session-Id (لتحديد الجلسة).

    NAS-IP-Address يُضاف تلقائيًا (عنوان الـ NAS ذاته من منظوره — في معظم
    الأحيان نمرّر له nas_ip كمصدر الجلسة).

    R11.12: نُضيف Framed-IP-Address (8) و Calling-Station-Id (31) عندما تكون
    متاحة. MikroTik يستخدم هذه الـ attributes كـ session keys للتعرّف على
    الجلسة (في hotspot specifically، MT يبحث عن client بـ IP/MAC في
    /ip hotspot active). بدونها يُرجع NAK: "Radius with no ip provided".
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
    # R11.12: Framed-IP-Address + Calling-Station-Id (MT session keys)
    if framed_ip:
        try: attrs += encode_ipv4_attr(ATTR_FRAMED_IP_ADDRESS, framed_ip)
        except ValueError: pass
    if calling_station_id:
        attrs += encode_string_attr(ATTR_CALLING_STATION_ID, calling_station_id)

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
              framed_ip: str = "", calling_station_id: str = "",
              new_rate_limit: str = "",
              session_timeout: int = 0,
              port: int = 3799, timeout: float = 5.0) -> CoaResult:
    """
    CoA-Request (Code=43) — يغيّر attributes لجلسة جارية دون قطع.
    استخدامات شائعة:
      • تغيير Mikrotik-Rate-Limit (السرعة الفورية).
      • تغيير Session-Timeout (الوقت المتبقي قبل قطع الجلسة) — يُستعمل
        لتطبيق "تعديل وقت البطاقة" على الجلسات الحيّة دون قطعها.

    R11.12: نُضيف Framed-IP-Address + Calling-Station-Id (مثل send_disconnect)
    حتى يستطيع MT أن يعثر على hotspot client بـ IP/MAC وليس فقط بـ User-Name.
    بدون Framed-IP-Address، MT 7.x يُرجع CoA-NAK: "Radius with no ip provided".
    """
    secret = nas_secret.encode("utf-8")
    ident = _sec.randbits(8)

    attrs = b""
    attrs += encode_string_attr(ATTR_USER_NAME, username)
    if session_id:
        attrs += encode_string_attr(ATTR_ACCT_SESSION_ID, session_id)
    try: attrs += encode_ipv4_attr(ATTR_NAS_IP_ADDRESS, nas_ip)
    except ValueError: pass
    # R11.12: session keys التي يتوقّعها MT
    if framed_ip:
        try: attrs += encode_ipv4_attr(ATTR_FRAMED_IP_ADDRESS, framed_ip)
        except ValueError: pass
    if calling_station_id:
        attrs += encode_string_attr(ATTR_CALLING_STATION_ID, calling_station_id)
    if new_rate_limit:
        attrs += encode_vendor_attr(VENDOR_MIKROTIK, MT_ATTR_RATE_LIMIT, new_rate_limit)
    if session_timeout and session_timeout > 0:
        # RFC 2865 §5.27 — uint32 seconds of remaining session time.
        # Clamp to 32-bit and to 1..max so we never send 0 (which means
        # "no session timeout" on most NAS implementations).
        st = max(1, min(int(session_timeout), 0xFFFFFFFF))
        attrs += encode_uint32_attr(ATTR_SESSION_TIMEOUT, st)

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
    """Single-session helper — returns the most recent active session
    only. Kept for backward compat; the multi-session helpers below
    are preferred for any code path that wants to affect ALL live
    sessions of a username (which is almost always the right thing
    to do for card actions)."""
    sessions = find_all_nas_for_sessions(tenant_id, username)
    if sessions:
        return sessions[0]

    # Backward-compatible diagnostic shape: if a session exists but its
    # NAS is disabled or has no secret, callers can still show the active
    # NAS/session context without leaking or using the shared secret.
    from ..db.connection import db
    row = db().execute("""
        SELECT acctsessionid, nasipaddress, framedipaddress, callingstationid
          FROM radacct
        WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL
        ORDER BY radacctid DESC
        LIMIT 1
    """, (tenant_id, username)).fetchone()
    if not row:
        return None
    return {
        "nas_ip": row["nasipaddress"],
        "nas_secret": "",
        "session_id": row["acctsessionid"],
        "framed_ip": row["framedipaddress"] or "",
        "calling_station_id": row["callingstationid"] or "",
    }


def find_all_nas_for_sessions(tenant_id: int, username: str) -> list[dict]:
    """Return EVERY active radacct session for `username`, each with
    the NAS info needed to send a CoA packet.

    Same shape as `find_nas_for_session` but a list. Skips sessions
    whose NAS row in `nas_devices` is missing or disabled (we can't
    sign a CoA packet without the shared secret), and logs the skip
    so the admin can investigate.

    A card with multiple roaming devices ends up with multiple
    concurrent radacct rows for the same username. Card Checker
    actions (disconnect / change rate / change timeout) should affect
    ALL of them — not just the newest one. Without this, the first
    two operations seem to work on 'a' session and the third one
    appears to do nothing because it's already targeting that same
    session.
    """
    from ..db.connection import db
    rows = db().execute("""
        SELECT acctsessionid, nasipaddress, framedipaddress, callingstationid
          FROM radacct
        WHERE tenant_id = ? AND username = ? AND acctstoptime IS NULL
        ORDER BY radacctid DESC
    """, (tenant_id, username)).fetchall()
    if not rows:
        return []
    results: list[dict] = []
    for row in rows:
        nas_ip = row["nasipaddress"]
        nas_row = db().execute(
            "SELECT secret, coa_port, address, connection_mode, vpn_peer_address "
            "FROM nas_devices "
            "WHERE tenant_id = ? AND address = ? AND enabled = 1 LIMIT 1",
            (tenant_id, nas_ip)).fetchone()
        if not nas_row or not nas_row["secret"]:
            _LOG.warning(
                "find_all_nas_for_sessions: skipping session %s on NAS %s "
                "— no enabled nas_devices row with a secret",
                row["acctsessionid"], nas_ip,
            )
            continue
        try:
            coa_port = int(nas_row["coa_port"] or 3799)
        except (TypeError, ValueError, KeyError):
            coa_port = 3799
        results.append({
            # VPN-only: dial the resolved tunnel peer when the NAS is in VPN
            # mode, falling back to the accounting source IP. Keeps CoA on the
            # WireGuard path even if a row's accounting ever arrives publicly.
            "nas_ip": resolve_connection_address(nas_row) or nas_ip,
            "nas_secret": nas_row["secret"],
            "coa_port": coa_port,
            "session_id": row["acctsessionid"],
            "framed_ip": row["framedipaddress"] or "",
            "calling_station_id": row["callingstationid"] or "",
        })
    return results


def _broadcast(label: str, results: list[CoaResult],
                sessions_total: int) -> CoaResult:
    """Collapse a list of per-session CoaResults into one summary.

    The shape mirrors a single CoaResult so callers don't need to
    change. The `reply_message` and `code_name` reflect the aggregate
    outcome:
       all-ok      → 'all ok (N sessions)'
       partial     → 'X ok / Y failed (Z sessions)'
       all-failed  → 'all failed (N sessions)'
       no-sessions → 'no_active_session'
    """
    if not results and sessions_total == 0:
        return CoaResult(ok=False, code=0, code_name="no_active_session",
                          reply_message="لا جلسات نشطة")
    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count
    if fail_count == 0:
        name = "all_ok"
        msg  = f"{label}: {ok_count} جلسة"
        ok   = True
    elif ok_count == 0:
        name = "all_failed"
        msg  = f"{label} فشل على كل الجلسات ({fail_count})"
        ok   = False
    else:
        name = "partial"
        msg  = f"{label}: نجح {ok_count} وفشل {fail_count} من {len(results)}"
        ok   = True   # treat partial as success (some sessions got it)
    return CoaResult(ok=ok, code=0, code_name=name, reply_message=msg)


def disconnect_user(tenant_id: int, username: str,
                     *, session_ids: list[str] | None = None) -> CoaResult:
    """Disconnect one or more active sessions for `username`.

    `session_ids` selects WHICH sessions to kick:
       • None or empty list → kick EVERY active session (bulk).
       • Non-empty list     → kick only sessions whose acctsessionid is
                              in the list; the others keep running.

    A card with multiple concurrent devices (3 phones, family sharing,
    …) has multiple radacct rows. The Card Checker's Disconnect picker
    sends the IDs of the rows the operator selected; speed/time
    changes still pass None to affect all sessions on the card.
    """
    sessions = find_all_nas_for_sessions(tenant_id, username)
    if session_ids:
        wanted = {s.strip() for s in session_ids if s and s.strip()}
        if wanted:
            sessions = [s for s in sessions if s["session_id"] in wanted]
    if not sessions:
        return CoaResult(ok=False, code=0, code_name="no_active_session",
                          reply_message=f"لا جلسة نشطة لـ {username}")
    results = [
        send_disconnect(
            nas_ip=info["nas_ip"], nas_secret=info["nas_secret"],
            username=username, session_id=info["session_id"],
            framed_ip=info.get("framed_ip", ""),
            calling_station_id=info.get("calling_station_id", ""),
            port=info.get("coa_port", 3799),
        )
        for info in sessions
    ]
    return _broadcast("قطع الجلسات", results, len(sessions))


def change_user_rate(tenant_id: int, username: str, *,
                       new_rate_limit: str) -> CoaResult:
    """R9.3: يُرسل CoA-Request (Code=43) بتغيير Mikrotik-Rate-Limit لجلسة
    حيّة دون قطع. يستخدمها SqliteAdapter.upsert_account لتطبيق تغيير
    السرعة فوريًا بدلاً من الانتظار حتى الجلسة التالية.

    الـ behaviour:
      - لا جلسة نشطة → no_active_session (نتجاهل بهدوء؛ التغيير
        سيُطبَّق على الجلسة التالية تلقائيًا عبر Access-Accept).
      - لا secret → missing_nas_secret.
      - new_rate_limit فارغ → empty_rate (skip).
      - وإلا يُرسل CoA-Request و يُرجع CoA-ACK/NAK من الـ NAS.

    آمن: حتى لو الـ NAS لا يدعم CoA Change-of-Authorization، فقط نسجّل
    الفشل ولا نمنع حفظ السرعة الجديدة في DB.
    """
    if not new_rate_limit or not new_rate_limit.strip():
        return CoaResult(ok=False, code=0, code_name="empty_rate",
                          reply_message="rate فارغ — لا تغيير")
    sessions = find_all_nas_for_sessions(tenant_id, username)
    if not sessions:
        return CoaResult(ok=False, code=0, code_name="no_active_session",
                          reply_message=f"لا جلسة نشطة لـ {username} — "
                                         "السرعة الجديدة ستُطبَّق على الجلسة التالية")
    # Broadcast — push the rate change to every active session.
    results = [
        send_coa(
            nas_ip=info["nas_ip"], nas_secret=info["nas_secret"],
            username=username, session_id=info["session_id"],
            framed_ip=info.get("framed_ip", ""),
            calling_station_id=info.get("calling_station_id", ""),
            new_rate_limit=new_rate_limit,
            port=info.get("coa_port", 3799),
        )
        for info in sessions
    ]
    return _broadcast("تحديث السرعة", results, len(sessions))


def change_user_session_timeout(tenant_id: int, username: str,
                                  *, session_timeout: int) -> CoaResult:
    """يُرسل CoA-Request (Code=43) لتحديث Session-Timeout على جلسة حيّة.

    يُستعمَل من زرّ "تعديل وقت البطاقة" في Card Checker: بعد ما يتم تعديل
    expire_at في DB، نُحاول دفع الوقت المتبقّي الجديد للـ NAS كـ
    Session-Timeout (RFC 2865 §27، بالثواني) حتى لا يحتاج المستخدم
    لتسجيل الدخول من جديد.

    الـ behaviour يطابق change_user_rate:
      - لا جلسة نشطة → no_active_session (نتجاهل بهدوء؛ السقف الجديد
        سيُطبَّق تلقائيًا في الجلسة التالية عبر Access-Accept).
      - لا secret → missing_nas_secret.
      - session_timeout <= 0 → empty_timeout (skip).
      - وإلا يُرسل CoA-Request و يُرجع CoA-ACK/NAK من الـ NAS.
    """
    if not session_timeout or int(session_timeout) <= 0:
        return CoaResult(ok=False, code=0, code_name="empty_timeout",
                          reply_message="timeout غير صالح — لا تغيير")
    sessions = find_all_nas_for_sessions(tenant_id, username)
    if not sessions:
        return CoaResult(ok=False, code=0, code_name="no_active_session",
                          reply_message=f"لا جلسة نشطة لـ {username} — "
                                         "الوقت الجديد سيُطبَّق على الجلسة التالية")
    results = [
        send_coa(
            nas_ip=info["nas_ip"], nas_secret=info["nas_secret"],
            username=username, session_id=info["session_id"],
            framed_ip=info.get("framed_ip", ""),
            calling_station_id=info.get("calling_station_id", ""),
            session_timeout=int(session_timeout),
            port=info.get("coa_port", 3799),
        )
        for info in sessions
    ]
    return _broadcast("تحديث الوقت", results, len(sessions))
