"""اختبارات ترميز كلمات MikroTik — منعزلة، بدون شبكة."""
from __future__ import annotations

import io
import pytest

from app.radius.integration.mikrotik.protocol import (
    decode_length,
    decode_sentence,
    decode_word,
    encode_length,
    encode_sentence,
    encode_word,
    words_to_dict,
)
from app.radius.integration.mikrotik.errors import ProtocolError


# ─────────────── length encoding ───────────────

@pytest.mark.parametrize("length, expected", [
    (0,         bytes([0x00])),
    (1,         bytes([0x01])),
    (0x7F,      bytes([0x7F])),
    (0x80,      bytes([0x80, 0x80])),
    (0x3FFF,    bytes([0xBF, 0xFF])),
    (0x4000,    bytes([0xC0, 0x40, 0x00])),
    (0x1FFFFF,  bytes([0xDF, 0xFF, 0xFF])),
    (0x200000,  bytes([0xE0, 0x20, 0x00, 0x00])),
    (0xFFFFFFF, bytes([0xEF, 0xFF, 0xFF, 0xFF])),
    (0x10000000, bytes([0xF0, 0x10, 0x00, 0x00, 0x00])),
])
def test_encode_length(length, expected):
    assert encode_length(length) == expected


def _reader(buf: bytes):
    it = iter(buf)
    def _read():
        try: return next(it)
        except StopIteration:
            raise AssertionError("buffer exhausted")
    return _read


@pytest.mark.parametrize("length", [
    0, 1, 0x7F, 0x80, 0x3FFF, 0x4000, 0x1FFFFF, 0x200000, 0xFFFFFFF, 0x10000000, 0xFFFFFFFF,
])
def test_roundtrip_length(length):
    buf = encode_length(length)
    assert decode_length(_reader(buf)) == length


def test_reserved_byte_raises():
    with pytest.raises(ProtocolError):
        decode_length(_reader(bytes([0xF8])))
    with pytest.raises(ProtocolError):
        decode_length(_reader(bytes([0xFF])))


def test_encode_negative_raises():
    with pytest.raises(ProtocolError):
        encode_length(-1)


# ─────────────── word ───────────────

def test_encode_word_str_utf8():
    w = encode_word("/login")
    # طول 6 + 6 بايتات
    assert w[0] == 6
    assert w[1:] == b"/login"


def test_encode_word_bytes():
    assert encode_word(b"abc") == bytes([3]) + b"abc"


def test_encode_word_arabic():
    text = "اسم"  # 3 chars, UTF-8 = 6 bytes
    raw = text.encode("utf-8")
    w = encode_word(text)
    assert w[0] == len(raw)
    assert w[1:] == raw


def test_decode_word_zero_returns_none():
    assert decode_word(_reader(bytes([0]))) is None


def test_decode_word_normal():
    w = decode_word(_reader(bytes([3]) + b"abc"))
    assert w == b"abc"


# ─────────────── sentence ───────────────

def test_encode_sentence_terminates_with_zero():
    out = encode_sentence(["/login", "=name=admin", "=password="])
    assert out.endswith(bytes([0]))


def test_roundtrip_sentence_simple():
    words_in = ["/login", "=name=admin", "=password=x"]
    raw = encode_sentence(words_in)
    decoded = decode_sentence(_reader(raw))
    assert [w.decode() for w in decoded] == words_in


def test_decode_sentence_empty():
    assert decode_sentence(_reader(bytes([0]))) == []


# ─────────────── words_to_dict ───────────────

def test_words_to_dict_re():
    words = [b"!re", b"=name=admin", b"=disabled=no", b".tag=7"]
    d = words_to_dict(words)
    assert d["reply"] == "!re"
    assert d["attrs"]["name"] == "admin"
    assert d["attrs"]["disabled"] == "no"
    assert d["api"]["tag"] == "7"


def test_words_to_dict_done():
    d = words_to_dict([b"!done"])
    assert d["reply"] == "!done"
    assert d["attrs"] == {}


def test_words_to_dict_attr_with_equals_in_value():
    d = words_to_dict([b"!re", b"=name=foo=bar=baz"])
    assert d["attrs"]["name"] == "foo=bar=baz"


def test_words_to_dict_empty_attr_value():
    d = words_to_dict([b"!re", b"=password="])
    assert d["attrs"]["password"] == ""


# ─────────────── integration mini-scenario ───────────────

def test_encode_then_decode_full_command():
    """يحاكي ما يحدث على الـ wire ذهابًا وإيابًا."""
    sent = encode_sentence([
        "/ip/hotspot/user/add",
        "=name=u1",
        "=password=p1",
        "=profile=default",
        ".tag=42",
    ])
    decoded = [w.decode() for w in decode_sentence(_reader(sent))]
    assert decoded[0] == "/ip/hotspot/user/add"
    assert "=name=u1" in decoded
    assert ".tag=42" in decoded
