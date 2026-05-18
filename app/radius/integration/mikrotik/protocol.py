"""
MikroTik API binary protocol — encode/decode.

ينفّذ ترميز الكلمة (variable-length) ومُحلِّل الجمل.
يعتمد على دالتين فقط لقراءة/كتابة البايتات — مما يسمح باستخدامه
مع socket مباشر أو buffer للاختبار.

المراجع: https://help.mikrotik.com/docs/spaces/ROS/pages/47579160/API
"""
from __future__ import annotations

from typing import Callable, List

from .errors import ProtocolError


# ────────────────────────── طول الكلمة ──────────────────────────


def encode_length(length: int) -> bytes:
    """يُرجع الـ length-prefix كـ bytes حسب الجدول في الوثائق."""
    if length < 0:
        raise ProtocolError(f"length negative: {length}")
    if length < 0x80:
        return bytes([length])
    if length < 0x4000:
        v = length | 0x8000
        return v.to_bytes(2, "big")
    if length < 0x200000:
        v = length | 0xC00000
        return v.to_bytes(3, "big")
    if length < 0x10000000:
        v = length | 0xE0000000
        return v.to_bytes(4, "big")
    # 5-byte form: 0xF0 + 4 raw bytes
    return bytes([0xF0]) + length.to_bytes(4, "big")


def decode_length(read_byte: Callable[[], int]) -> int:
    """
    يستهلك بايتات الـ length-prefix ويُرجع الطول الكامل.
    `read_byte` دالة تُرجع بايت واحد كـ int (0..255) أو ترمي عند EOF.
    """
    b0 = read_byte()
    if b0 < 0x80:
        return b0
    if (b0 & 0xC0) == 0x80:           # 10xxxxxx
        b1 = read_byte()
        return ((b0 & 0x3F) << 8) | b1
    if (b0 & 0xE0) == 0xC0:           # 110xxxxx
        b1, b2 = read_byte(), read_byte()
        return ((b0 & 0x1F) << 16) | (b1 << 8) | b2
    if (b0 & 0xF0) == 0xE0:           # 1110xxxx
        b1, b2, b3 = read_byte(), read_byte(), read_byte()
        return ((b0 & 0x0F) << 24) | (b1 << 16) | (b2 << 8) | b3
    if b0 == 0xF0:                    # 11110000 + 4 bytes
        b1, b2, b3, b4 = read_byte(), read_byte(), read_byte(), read_byte()
        return (b1 << 24) | (b2 << 16) | (b3 << 8) | b4
    # 0xF8..0xFF محجوزة — يجب قطع الاتصال
    raise ProtocolError(f"reserved control byte 0x{b0:02x} — يجب قفل الاتصال")


# ────────────────────────── الكلمات ──────────────────────────


def encode_word(word: str | bytes) -> bytes:
    """يرمّز كلمة واحدة (length-prefix + content)."""
    if isinstance(word, str):
        raw = word.encode("utf-8")
    elif isinstance(word, (bytes, bytearray)):
        raw = bytes(word)
    else:
        raise TypeError(f"word must be str or bytes, got {type(word)}")
    return encode_length(len(raw)) + raw


def encode_sentence(words: list[str | bytes]) -> bytes:
    """يرمّز جملة كاملة + كلمة الصفر النهائية."""
    out = bytearray()
    for w in words:
        out += encode_word(w)
    out += encode_length(0)  # zero-length terminator
    return bytes(out)


def decode_word(read_byte: Callable[[], int]) -> bytes | None:
    """
    يستهلك كلمة واحدة. يُرجع `None` لو كانت كلمة طول صفر (نهاية الجملة).
    وإلا يُرجع الـ raw bytes (لاحقًا يحوّلها المستدعي إلى str).
    """
    length = decode_length(read_byte)
    if length == 0:
        return None
    return bytes(read_byte() for _ in range(length))


def decode_sentence(read_byte: Callable[[], int]) -> list[bytes]:
    """يقرأ جملة كاملة حتى كلمة الصفر. يُرجع قائمة raw bytes لكل كلمة."""
    words: List[bytes] = []
    while True:
        w = decode_word(read_byte)
        if w is None:
            return words
        words.append(w)


# ────────────────────────── helpers ──────────────────────────


def words_to_dict(words: list[bytes]) -> dict:
    """
    يحوّل جملة مستلَمة إلى dict مفيد:
        {
            'reply':  '!re' | '!done' | '!trap' | '!empty' | '!fatal'  (إن وُجد)
            'attrs':  {key: value, ...}   ← من =name=value
            'api':    {key: value, ...}   ← من .name=value (tag…)
        }
    أي كلمة لا تطابق الأنماط تُجمَع في 'extras' كـ list.
    """
    reply: str | None = None
    attrs: dict[str, str] = {}
    api: dict[str, str] = {}
    extras: list[str] = []
    for w in words:
        s = w.decode("utf-8", errors="replace")
        if s.startswith("!"):
            reply = s
        elif s.startswith("="):
            # =name=value — نقسّم عند علامة الـ '=' الثانية
            body = s[1:]
            if "=" in body:
                k, _, v = body.partition("=")
                attrs[k] = v
            else:
                attrs[body] = ""
        elif s.startswith("."):
            body = s[1:]
            if "=" in body:
                k, _, v = body.partition("=")
                api[k] = v
            else:
                api[body] = ""
        else:
            extras.append(s)
    return {"reply": reply, "attrs": attrs, "api": api, "extras": extras}


def build_attr(name: str, value: str = "") -> str:
    """يبني `=name=value`."""
    return f"={name}={value}"


def build_api_attr(name: str, value: str = "") -> str:
    """يبني `.name=value`."""
    return f".{name}={value}"


def build_query(name: str, value: str | None = None, op: str = "=") -> str:
    """
    يبني query word.
    op = '=' | '<' | '>' | '-' (لـ -name) | '#' (لـ #operations مثل |, &, !).
    """
    if op == "-":
        return f"?-{name}"
    if op == "#":
        return f"?#{name}"
    if value is None:
        return f"?{name}"
    if op == "=":
        return f"?={name}={value}"
    return f"?{op}{name}={value}"
