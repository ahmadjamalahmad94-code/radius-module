"""Live-session CoA control — packet-shape regression for all three
actions on both session types (PPPoE + hotspot).

Uses a real UDP socket bound to 127.0.0.1 as the mock NAS. The mock:
  • Listens on a free ephemeral port.
  • Decodes the request packet just enough to assert its code +
    attribute set are EXACTLY what the design promises.
  • Replies with an ACK (44/41) signed against the request authenticator
    so the production code's response-verification passes.

This is NOT a mocked CoaClient — it exercises the actual byte-level
encoder via send_coa / send_disconnect over a real UDP socket. If the
encoder ever stops emitting Framed-IP-Address for set_ip, or stops
emitting Mikrotik-Rate-Limit (VSA 14988 / sub 8) for set_speed, these
tests fail.
"""
from __future__ import annotations

import hashlib
import socket
import struct
import threading
from contextlib import closing
from typing import Optional

import pytest


# ── packet decoder ───────────────────────────────────────────────────


def _decode_attrs(payload: bytes) -> dict:
    """Return {attr_type: bytes_value} for top-level RADIUS attrs."""
    out: dict[int, bytes] = {}
    i = 0
    while i < len(payload):
        if i + 2 > len(payload):
            break
        t = payload[i]
        ln = payload[i + 1]
        if ln < 2 or i + ln > len(payload):
            break
        out[t] = payload[i + 2: i + ln]
        i += ln
    return out


def _decode_vsa(raw: bytes) -> dict:
    """Decode a Vendor-Specific (type=26) attribute body into
    {(vendor_id, subtype): value}."""
    if len(raw) < 6:
        return {}
    vendor_id = struct.unpack("!I", raw[:4])[0]
    body = raw[4:]
    out: dict[tuple[int, int], bytes] = {}
    i = 0
    while i < len(body):
        if i + 2 > len(body):
            break
        st = body[i]
        ln = body[i + 1]
        if ln < 2 or i + ln > len(body):
            break
        out[(vendor_id, st)] = body[i + 2: i + ln]
        i += ln
    return out


# ── mock NAS ──────────────────────────────────────────────────────────


class MockNas:
    """Listens on a free 127.0.0.1 port; one request → one ACK reply.

    Captures the request bytes so tests can assert code + attributes.
    """

    def __init__(self, secret: str, *, reply_code: Optional[int] = None):
        self.secret = secret.encode("utf-8")
        self.reply_code = reply_code  # None → auto (ACK matching request)
        self.captured: Optional[bytes] = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(5.0)
        self.port = self.sock.getsockname()[1]
        self._stopped = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        try:
            data, addr = self.sock.recvfrom(4096)
        except socket.timeout:
            return
        self.captured = data
        if self._stopped.is_set():
            return
        # Compose an ACK (or whatever was requested) signed with the
        # request authenticator the production code expects.
        req_code = data[0]
        req_id   = data[1]
        req_auth = data[4:20]
        out_code = self.reply_code if self.reply_code is not None else {
            40: 41,    # Disconnect → Disconnect-ACK
            43: 44,    # CoA → CoA-ACK
        }.get(req_code, 44)
        # No reply attrs.
        length = 20
        header = bytes([out_code, req_id]) + struct.pack("!H", length)
        # Response-Authenticator = MD5(Code|ID|Length|RequestAuthenticator|Attrs|Secret)
        body = header + req_auth + b"" + self.secret
        resp_auth = hashlib.md5(body).digest()
        packet = header + resp_auth
        try:
            self.sock.sendto(packet, addr)
        except OSError:
            pass

    def stop(self) -> None:
        self._stopped.set()
        try:
            self.sock.close()
        except OSError:
            pass
        # join is best-effort; the receiver is in a daemon thread.
        self.thread.join(timeout=1.0)


@pytest.fixture
def mock_nas():
    """Per-test mock NAS — torn down after each test."""
    holder: dict = {}
    def _build(secret: str = "panel-minted-secret", *, reply_code=None) -> MockNas:
        m = MockNas(secret, reply_code=reply_code)
        holder["nas"] = m
        return m
    yield _build
    if "nas" in holder:
        holder["nas"].stop()


# ── packet-shape assertions ──────────────────────────────────────────


def _assert_msg_authenticator_present(attrs: dict, *, secret: str,
                                      raw_packet: bytes) -> None:
    """Verify the Message-Authenticator attribute (type 80) is present
    AND verifies as HMAC-MD5(packet-with-MA-zeroed, secret)."""
    import hmac
    assert 80 in attrs, "Message-Authenticator (type 80) missing — RFC 5176 §3.1"
    ma_sent = attrs[80]
    assert len(ma_sent) == 16, f"Message-Authenticator must be 16 bytes, got {len(ma_sent)}"
    # Reconstruct the packet with the MA field zeroed and verify HMAC-MD5.
    # Find the MA position in raw_packet and zero those 16 bytes.
    needle = bytes([80, 18]) + ma_sent
    idx = raw_packet.find(needle)
    assert idx >= 0
    zeroed = raw_packet[:idx + 2] + bytes(16) + raw_packet[idx + 18:]
    # And the authenticator field (bytes 4..20) must be ZERO for request packets
    # per RFC 5176 §3.1 when computing Message-Authenticator.
    zeroed = zeroed[:4] + bytes(16) + zeroed[20:]
    expected = hmac.new(secret.encode("utf-8"), zeroed, hashlib.md5).digest()
    assert ma_sent == expected, "Message-Authenticator HMAC-MD5 mismatch"


def _session_pppoe(*, nas_ip: str, nas_secret: str, coa_port: int) -> dict:
    return {
        "nas_ip": nas_ip,
        "nas_secret": nas_secret,
        "coa_port": coa_port,
        "session_id": "80000071",
        "framed_ip": "10.20.30.40",
        "calling_station_id": "AA:BB:CC:DD:EE:FF",
        "nasporttype": "Virtual",   # MT writes this for PPPoE
    }


def _session_hotspot(*, nas_ip: str, nas_secret: str, coa_port: int) -> dict:
    return {
        "nas_ip": nas_ip,
        "nas_secret": nas_secret,
        "coa_port": coa_port,
        "session_id": "80FF0042",
        "framed_ip": "192.168.10.55",
        "calling_station_id": "11:22:33:44:55:66",
        "nasporttype": "Ethernet",  # MT writes this for hotspot
    }


# ── action × type matrix ─────────────────────────────────────────────


SECRET = "panel-minted-secret"


def test_set_ip_pppoe_packet_shape(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import change_ip_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = change_ip_live(tenant_id=1, username="ppp_user_42",
                         new_ip="10.20.30.99",
                         session_id=session["session_id"],
                         session_row=session)
    assert out.ok is True
    assert out.code == 44, f"expected CoA-ACK(44), got {out.code}/{out.code_name}"
    assert out.action == "set_ip"
    assert out.session_type == "pppoe"
    # Decode the captured request.
    pkt = nas.captured
    assert pkt is not None and pkt[0] == 43, "request must be CoA-Request (code 43)"
    attrs = _decode_attrs(pkt[20:])
    # User-Name
    assert attrs.get(1) == b"ppp_user_42"
    # Acct-Session-Id
    assert attrs.get(44) == b"80000071"
    # Framed-IP-Address = the NEW ip (the change), not the old match key
    assert attrs.get(8) == bytes([10, 20, 30, 99]), \
        "Framed-IP-Address must carry the NEW ip (action a — set_ip)"
    # Calling-Station-Id (session match key, MT hotspot/PPP both)
    assert attrs.get(31) == b"AA:BB:CC:DD:EE:FF"
    # Message-Authenticator HMAC-MD5(secret) verifies
    _assert_msg_authenticator_present(attrs, secret=SECRET, raw_packet=pkt)


def test_set_ip_hotspot_unsupported(mock_nas):
    """Hotspot must surface unsupported WITHOUT sending a packet."""
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import change_ip_live
    session = _session_hotspot(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = change_ip_live(tenant_id=1, username="hotspot_user",
                         new_ip="192.168.10.77",
                         session_id=session["session_id"],
                         session_row=session)
    assert out.ok is False
    assert out.code_name == "unsupported"
    assert "hotspot" in out.detail.lower()
    # And NO packet should have hit the wire.
    assert nas.captured is None, \
        "set_ip on hotspot MUST NOT send a packet that we know NAKs"


def test_set_speed_pppoe_packet_shape(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import change_speed_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = change_speed_live(tenant_id=1, username="ppp_user_42",
                            rx_kbps=51200, tx_kbps=10240,
                            session_id=session["session_id"],
                            session_row=session)
    assert out.ok is True
    assert out.code == 44
    assert out.action == "set_speed"
    assert out.session_type == "pppoe"
    pkt = nas.captured
    assert pkt is not None and pkt[0] == 43
    attrs = _decode_attrs(pkt[20:])
    # Vendor-Specific must be present with Mikrotik (14988) + Rate-Limit (8)
    vsa_raw = attrs.get(26)
    assert vsa_raw is not None, "Vendor-Specific (26) missing for set_speed"
    vsa = _decode_vsa(vsa_raw)
    assert (14988, 8) in vsa, "Mikrotik-Rate-Limit (14988/8) missing"
    rate = vsa[(14988, 8)].decode("ascii")
    assert rate == "51200k 10240k", f"unexpected rate-limit encoding: {rate!r}"
    # Framed-IP-Address must still be the OLD ip (match key, NOT a change)
    assert attrs.get(8) == bytes([10, 20, 30, 40])


def test_set_speed_hotspot_packet_shape(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import change_speed_live
    session = _session_hotspot(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = change_speed_live(tenant_id=1, username="hotspot_user",
                            rx_kbps=10240, tx_kbps=5120,
                            session_id=session["session_id"],
                            session_row=session)
    assert out.ok is True
    assert out.code == 44
    assert out.session_type == "hotspot"
    pkt = nas.captured
    attrs = _decode_attrs(pkt[20:])
    vsa = _decode_vsa(attrs[26])
    assert vsa[(14988, 8)] == b"10240k 5120k"
    # Calling-Station-Id present (hotspot identifies by MAC primarily)
    assert attrs.get(31) == b"11:22:33:44:55:66"
    # Framed-IP-Address as match key
    assert attrs.get(8) == bytes([192, 168, 10, 55])


def test_disconnect_pppoe_packet_shape(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import disconnect_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = disconnect_live(tenant_id=1, username="ppp_user_42",
                          session_id=session["session_id"],
                          session_row=session)
    assert out.ok is True
    assert out.code == 41, f"expected Disconnect-ACK(41), got {out.code}/{out.code_name}"
    assert out.action == "disconnect"
    pkt = nas.captured
    assert pkt is not None and pkt[0] == 40, "request must be Disconnect-Request (code 40)"
    attrs = _decode_attrs(pkt[20:])
    assert attrs.get(1) == b"ppp_user_42"
    assert attrs.get(44) == b"80000071"
    _assert_msg_authenticator_present(attrs, secret=SECRET, raw_packet=pkt)


def test_disconnect_hotspot_packet_shape(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import disconnect_live
    session = _session_hotspot(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = disconnect_live(tenant_id=1, username="hotspot_user",
                          session_id=session["session_id"],
                          session_row=session)
    assert out.ok is True
    assert out.code == 41
    assert out.session_type == "hotspot"
    pkt = nas.captured
    attrs = _decode_attrs(pkt[20:])
    assert attrs.get(31) == b"11:22:33:44:55:66", "hotspot match key MAC missing"


# ── NAK + timeout paths (no fake success) ────────────────────────────


def test_nak_is_surfaced_verbatim_no_fake_success(mock_nas):
    """When MT replies CoA-NAK(45), the outcome must be ok=False."""
    nas = mock_nas(SECRET, reply_code=45)
    from app.radius.services.live_session_control import change_speed_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = change_speed_live(tenant_id=1, username="u",
                            rx_kbps=1024, tx_kbps=512,
                            session_id="80000071", session_row=session)
    assert out.ok is False
    assert out.code == 45
    assert out.code_name == "CoA-NAK"


def test_timeout_is_surfaced_no_fake_success():
    """No mock NAS listening → CoA must time out with ok=False."""
    # Bind a socket so we KNOW that port number — then close it. The port
    # may immediately come back unbound, the client will timeout.
    with closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM)) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    from app.radius.services.live_session_control import change_speed_live
    session = {
        "nas_ip": "127.0.0.1", "nas_secret": SECRET, "coa_port": free_port,
        "session_id": "x", "framed_ip": "10.0.0.1",
        "calling_station_id": "", "nasporttype": "Virtual",
    }
    out = change_speed_live(tenant_id=1, username="u",
                            rx_kbps=1024, tx_kbps=512,
                            session_id="x", session_row=session)
    assert out.ok is False
    # Linux returns "timeout"; Windows immediately surfaces an ICMP
    # port-unreachable as a socket error. Both are legitimate "no fake
    # success" paths — what matters is the result is False.
    assert out.code_name in ("timeout", "socket_error")


# ── input guards (refuse before the wire) ────────────────────────────


def test_set_ip_rejects_invalid_ipv4(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import change_ip_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    with pytest.raises(ValueError):
        change_ip_live(tenant_id=1, username="u", new_ip="not-an-ip",
                       session_row=session)
    assert nas.captured is None


def test_set_speed_rejects_non_positive_rates(mock_nas):
    nas = mock_nas(SECRET)
    from app.radius.services.live_session_control import change_speed_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    with pytest.raises(ValueError):
        change_speed_live(tenant_id=1, username="u",
                          rx_kbps=0, tx_kbps=100, session_row=session)
    with pytest.raises(ValueError):
        change_speed_live(tenant_id=1, username="u",
                          rx_kbps=100, tx_kbps=-5, session_row=session)
    assert nas.captured is None


# ── support matrix ───────────────────────────────────────────────────


def test_support_matrix_documents_pppoe_vs_hotspot():
    from app.radius.services.live_session_control import SUPPORT_MATRIX, is_supported
    assert SUPPORT_MATRIX["pppoe"]["set_ip"]     is True
    assert SUPPORT_MATRIX["pppoe"]["set_speed"]  is True
    assert SUPPORT_MATRIX["pppoe"]["disconnect"] is True
    assert SUPPORT_MATRIX["hotspot"]["set_ip"]   is False
    assert SUPPORT_MATRIX["hotspot"]["set_speed"]   is True
    assert SUPPORT_MATRIX["hotspot"]["disconnect"]  is True
    assert is_supported("set_ip", "pppoe") is True
    assert is_supported("set_ip", "hotspot") is False
    assert is_supported("set_ip", "what?")   is False


# ── shared secret never logged ───────────────────────────────────────


def test_secret_never_in_outcome_dict(mock_nas, caplog):
    nas = mock_nas(SECRET)
    import logging
    caplog.set_level(logging.DEBUG, logger="app.radius.services.live_session_control")
    caplog.set_level(logging.DEBUG, logger="app.radius.integration.radius_coa")
    from app.radius.services.live_session_control import change_speed_live
    session = _session_pppoe(nas_ip="127.0.0.1", nas_secret=SECRET, coa_port=nas.port)
    out = change_speed_live(tenant_id=1, username="u",
                            rx_kbps=1024, tx_kbps=512,
                            session_id="80000071", session_row=session)
    blob = str(out.as_dict())
    assert SECRET not in blob
    for rec in caplog.records:
        assert SECRET not in rec.getMessage()
