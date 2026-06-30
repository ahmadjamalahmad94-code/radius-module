"""SMS length / segment math — Unicode-aware (GSM-7 vs UCS-2).

Each SMS message costs money, so messages must stay SHORT. The owner's rule:
cap/guide at **60 characters per SMS**. Arabic SMS is encoded in Unicode
(UCS-2), where a single paid segment holds only ~70 characters, so 60 is the
safe single-segment limit (with headroom for variable expansion).

This module computes the REAL cost of a text accurately, not by byte length:

  * Encoding — GSM-7 if every character is in the GSM 7-bit alphabet
    (basic + extension), otherwise UCS-2 (any Arabic/emoji/… forces UCS-2).
  * Length —
      GSM-7 : 1 unit per basic char, 2 units per extension char (^{}\\[~]|€).
      UCS-2 : number of UTF-16 code units (BMP char = 1, astral/emoji = 2).
  * Segments —
      GSM-7 : ≤160 → 1, else ceil(len/153)  (153 = 160 − 7-byte UDH).
      UCS-2 : ≤70  → 1, else ceil(len/67)   (67  = 70  − 3 UCS-2 UDH units).

The JS counter (``static/js/sms_counter.js``) mirrors this exactly so the live
UI and the server agree on the displayed cost.
"""
from __future__ import annotations

from dataclasses import dataclass

# The owner's safe single-SMS guide (below the 70-char UCS-2 segment boundary).
RECOMMENDED_MAX = 60

# Single-segment + per-concatenated-segment capacities.
GSM_SINGLE, GSM_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67

# GSM 03.38 basic alphabet (each char = 1 unit).
_GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
# GSM 03.38 extension table (each char = 2 units: ESC + char).
_GSM_EXTENDED = set("^{}\\[~]|€")


def is_gsm7(text: str) -> bool:
    """True when every character is encodable in the GSM 7-bit alphabet."""
    for ch in str(text or ""):
        if ch not in _GSM_BASIC and ch not in _GSM_EXTENDED:
            return False
    return True


def _gsm_units(text: str) -> int:
    return sum(2 if ch in _GSM_EXTENDED else 1 for ch in text)


def _ucs2_units(text: str) -> int:
    # UTF-16 code units: characters outside the BMP (e.g. most emoji) take two.
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


@dataclass(frozen=True)
class SmsLength:
    encoding: str          # "gsm" | "unicode"
    length: int            # encoded length in units (the SMS-counted length)
    chars: int             # visible character count (len of the string)
    segments: int          # how many paid SMS parts this consumes
    per_segment: int       # capacity of each segment in this encoding/mode
    single_limit: int      # single-segment capacity for this encoding
    recommended_max: int   # the owner's soft guide (60)
    over_recommended: bool # length > recommended_max
    multi_segment: bool    # segments > 1


def analyze(text: str) -> SmsLength:
    """Return the accurate SMS length/segment breakdown for ``text``.

    ``length`` is the *encoded* length (what the carrier counts), which equals
    the visible char count for plain Arabic/Latin but grows for GSM-extension
    or astral characters.
    """
    s = str(text or "")
    chars = len(s)
    if is_gsm7(s):
        encoding = "gsm"
        length = _gsm_units(s)
        single, multi = GSM_SINGLE, GSM_MULTI
    else:
        encoding = "unicode"
        length = _ucs2_units(s)
        single, multi = UCS2_SINGLE, UCS2_MULTI

    if length == 0:
        segments = 0
        per_segment = single
    elif length <= single:
        segments = 1
        per_segment = single
    else:
        per_segment = multi
        segments = -(-length // multi)  # ceil division

    return SmsLength(
        encoding=encoding,
        length=length,
        chars=chars,
        segments=segments,
        per_segment=per_segment,
        single_limit=single,
        recommended_max=RECOMMENDED_MAX,
        over_recommended=length > RECOMMENDED_MAX,
        multi_segment=segments > 1,
    )


def summary_ar(text: str) -> str:
    """A short Arabic one-liner describing the cost — handy for flashes/logs.

    e.g. «٤٢ حرفًا · رسالة واحدة (Unicode)» or
         «٨٥ حرفًا · رسالتان (مقطعان) — تتجاوز الحدّ الموصى به (60)».
    """
    info = analyze(text)
    parts = [f"{info.length} حرفًا", _segments_ar(info.segments)]
    tag = "Unicode" if info.encoding == "unicode" else "GSM"
    line = " · ".join(parts) + f" ({tag})"
    if info.over_recommended:
        line += f" — تتجاوز الحدّ الموصى به ({RECOMMENDED_MAX})"
    return line


def _segments_ar(n: int) -> str:
    if n <= 0:
        return "لا رسائل"
    if n == 1:
        return "رسالة واحدة"
    if n == 2:
        return "رسالتان (مقطعان)"
    return f"{n} رسائل (مقاطع)"
