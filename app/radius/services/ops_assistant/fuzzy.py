"""«هل تقصد…؟» — the deterministic fuzzy fallback of the operations assistant.

When an exact / substring lookup (subscriber, plan, offer, card batch) finds
NOTHING, the executor ranks the records the admin may see by similarity and
returns at most ``MAX_CANDIDATES`` of them as CHOICES with ``"match":"fuzzy"``.

Safety contract (docs/OPS_EXECUTOR.md «Smart search»):

* a fuzzy candidate is NEVER issued to the conversation, so no executable or
  INFO proposal can reference it (``invented_id``); it becomes usable only
  after the admin picks it (the «pick» click → an exact lookup) or names it
  exactly in a new message;
* the scan is tenant- and scope-bound with the SAME SQL predicate as the list
  API (``_subscriber_filter_sql`` / ``batch_scope_clause``) and every
  candidate's details are fetched again through the real /api/v1 GET handlers
  with the admin's credential;
* no free SQL, no model input in SQL except bound parameters.

Matching:

* digits (phone / national id / numeric username): Arabic-Indic → Latin, keep
  digits, drop ``00`` / ``+970`` / ``+972`` and the leading ``0`` (``05x`` =
  ``5x``), then Levenshtein ≤ 2;
* names / usernames: Arabic normalisation (أإآٱ→ا، ة→ه، ى/ئ→ي، ؤ→و, tashkeel
  and tatweel removed, optional «ال»), token similarity ``1 - d/len`` with a
  length-dependent edit bound, plus a cheap consonant skeleton for Latin ↔
  Arabic spellings («fahed» ↔ «فهد»).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

MAX_CANDIDATES = 5
MIN_SCORE = 0.6
MIN_DIGITS = 6            # shorter digit strings match far too much
MAX_DIGIT_EDITS = 2

_NON_DIGIT = re.compile(r"\D+")
_DIGIT_QUERY = re.compile(r"^[\s+()\-.0-9]+$")
# str.translate with a dict is a Python-level lookup per character: a chain
# of str.replace (C) is ~10x faster on a 100k-row bulk string.
_DIGIT_PAIRS = tuple(zip("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
_FOLD_PAIRS = (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ٱ", "ا"), ("ة", "ه"), ("ى", "ي"),
               ("ئ", "ي"), ("ؤ", "و"), ("ی", "ي"), ("ک", "ك"), ("گ", "ك"), ("ڤ", "ف"),
               ("پ", "ب"), ("چ", "ج"))


def _replace_all(text: str, pairs) -> str:
    for a, b in pairs:
        if a in text:
            text = text.replace(a, b)
    return text


_TASHKEEL = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭـ]")
_SEP = re.compile(r"[\s._\-@/\\,'\"()]+")
_ARABIC = re.compile("[؀-ۿ]")


# ─────────────────────────── normalisation ────────────────────────────

def latin(text: Any) -> str:
    return _replace_all(str(text or ""), _DIGIT_PAIRS)


def is_digit_query(query: str) -> bool:
    q = latin(query).strip()
    return bool(q) and bool(_DIGIT_QUERY.match(q)) and len(_NON_DIGIT.sub("", q)) >= MIN_DIGITS


def digit_key(value: Any) -> str:
    """``+970 599-043-336`` / ``00970599043336`` / ``0599043336`` → ``599043336``."""
    d = _NON_DIGIT.sub("", latin(value))
    if d.startswith("00"):
        d = d[2:]
    if len(d) >= 11 and d[:3] in ("970", "972"):
        d = d[3:]
    return d.lstrip("0")


def norm_text(value: Any) -> str:
    s = _replace_all(_TASHKEEL.sub("", latin(value)), _FOLD_PAIRS).casefold()
    return _SEP.sub(" ", s).strip()


def tokens(value: Any) -> list[str]:
    return [t for t in norm_text(value).split(" ") if t]


def _al(token: str) -> str:
    """«الفهد» → «فهد» (the article is optional in a typed name)."""
    return token[2:] if token.startswith("ال") and len(token) > 4 else token


_SKEL_AR = str.maketrans({
    "ب": "b", "ت": "t", "ث": "t", "ج": "j", "ح": "h", "خ": "k", "د": "d", "ذ": "d",
    "ر": "r", "ز": "z", "س": "s", "ش": "s", "ص": "s", "ض": "d", "ط": "t", "ظ": "z",
    "غ": "g", "ف": "f", "ق": "k", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h",
    "ا": None, "و": None, "ي": None, "ع": None, "ء": None})
_SKEL_DIGRAPH = (("kh", "k"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("gh", "G"),
                 ("ph", "f"), ("ch", "s"), ("ou", "u"), ("ee", "i"), ("oo", "u"))
_SKEL_LAT = str.maketrans({
    "q": "k", "c": "k", "g": "j", "G": "g", "v": "f", "p": "b", "x": "k",
    "7": "h", "5": "k", "9": "s", "3": None, "2": None, "8": "g",
    "a": None, "e": None, "i": None, "o": None, "u": None, "y": None, "w": None})


def skeleton(token: str) -> str:
    """Consonant skeleton shared by Arabic and Latin/arabizi spellings
    («فهد», «فاهد», «fahed», «fahd» → ``fhd``). Cheap and lossy on purpose: it
    only ever proposes candidates, it never decides."""
    if _ARABIC.search(token):
        s = token.translate(_SKEL_AR)
    else:
        s = re.sub(r"[0-9]{2,}|[0-9]+$", "", token.lower())   # «fahed99»: not arabizi
        for a, b in _SKEL_DIGRAPH:
            s = s.replace(a, b)
        s = s.translate(_SKEL_LAT)
    s = re.sub(r"[^a-zA-Z]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)          # «fahhd» = «fahd»


# ─────────────────────────── distances ────────────────────────────

def levenshtein(a: str, b: str, k: int) -> int:
    """Edit distance bounded by ``k`` (returns ``k + 1`` when it is larger).
    Banded dynamic programming: O(len · k)."""
    la, lb = len(a), len(b)
    if abs(la - lb) > k:
        return k + 1
    if a == b:
        return 0
    big = k + 1
    prev = [j if j <= k else big for j in range(lb + 1)]
    for i in range(1, la + 1):
        cur = [big] * (lb + 1)
        if i <= k:
            cur[0] = i
        lo, hi = max(1, i - k), min(lb, i + k)
        best = cur[0]
        ca = a[i - 1]
        for j in range(lo, hi + 1):
            v = prev[j - 1] + (ca != b[j - 1])
            if prev[j] + 1 < v:
                v = prev[j] + 1
            if cur[j - 1] + 1 < v:
                v = cur[j - 1] + 1
            cur[j] = v
            if v < best:
                best = v
        if best > k:
            return big
        prev = cur
    return prev[lb] if prev[lb] <= k else big


def _edit_bound(n: int) -> int:
    return 1 if n <= 4 else 2 if n <= 8 else 3


def token_similarity(q: str, t: str) -> float:
    """1.0 = equal after normalisation; 0 = unrelated (beyond the edit bound)."""
    best = 0.0
    for a in {q, _al(q)}:
        for b in {t, _al(t)}:
            if a == b:
                return 1.0
            n = max(len(a), len(b))
            k = _edit_bound(n)
            d = levenshtein(a, b, k)
            if d <= k:
                best = max(best, 1.0 - d / n)
    if best < MIN_SCORE and bool(_ARABIC.search(q)) != bool(_ARABIC.search(t)):
        sq, st = skeleton(q), skeleton(t)
        if len(sq) >= 2 and len(st) >= 2:
            n = max(len(sq), len(st))
            d = levenshtein(sq, st, 1)
            if d <= 1:                       # cross-script: ≈ one edit, never above it
                best = max(best, 0.75 if d == 0 else 0.62)
    return best


# ─────────────────────────── generic ranking ────────────────────────────

@dataclass
class Candidate:
    key: Any                      # username / id
    score: float
    matched: str                  # phone | national_id | username | name | code
    distance: Optional[int] = None
    extra: dict = field(default_factory=dict)


_BULK_SEP = "\x1f"
_SEP_BULK = re.compile("[ \t\r\n._\\-@/\\\\,'\"()]+")


def _norm_bulk(texts: list[str]) -> list[str]:
    """``norm_text`` for many strings in ONE pass of the C-level replace /
    regex (100k names in ~0.1 s instead of ~1 s one by one)."""
    big = _BULK_SEP.join(t.replace(_BULK_SEP, " ") for t in texts)
    big = _replace_all(_TASHKEEL.sub("", latin(big)), _FOLD_PAIRS).casefold()
    return _SEP_BULK.sub(" ", big).split(_BULK_SEP)


def _chunks(q: str, k: int) -> tuple[str, ...]:
    """k edits leave at least one of k+1 chunks of ``q`` intact (pigeonhole)."""
    n = len(q)
    parts = k + 1
    cuts = [round(i * n / parts) for i in range(parts + 1)]
    return tuple(q[cuts[i]:cuts[i + 1]] for i in range(parts) if cuts[i + 1] > cuts[i])


_SKEL_DIGITS = re.compile("[0-9]{2,}|[0-9]+(?=\x1f|$)")
_SKEL_DROP = re.compile("[^a-zA-Z\x1f]")
_SKEL_DUP = re.compile(r"([a-zA-Z])\1+")


def skeletons(toks: list[str]) -> list[str]:
    """``skeleton`` for many tokens in one C-level pass (same rules)."""
    big = _BULK_SEP.join(toks).lower()
    big = _SKEL_DIGITS.sub("", big)
    for a, b in _SKEL_DIGRAPH:
        big = big.replace(a, b)
    big = big.translate(_SKEL_LAT).translate(_SKEL_AR)
    big = _SKEL_DUP.sub(r"\1", _SKEL_DROP.sub("", big))
    return big.split(_BULK_SEP)


def _similar_tokens(q: str, universe: Iterable[str],
                    other_skel: Optional[dict[str, str]] = None) -> dict[str, float]:
    """{token: similarity >= MIN_SCORE} for one typed token over the distinct
    tokens of the scanned rows, with cheap length / substring pre-filters
    before the bounded edit distance. ``other_skel`` = skeletons of the tokens
    written in the OTHER script (Latin vs Arabic), for «fahed» -> «فهد»."""
    out: dict[str, float] = {}
    probes = []
    for v in {q, _al(q)}:
        k = _edit_bound(len(v) + 1)
        probes.append((len(v), k, _chunks(v, k)))
    for t in universe:
        lt = len(t)
        for lv, k, ch in probes:
            if abs(lt - lv) <= k + 2 and any(c in t for c in ch):
                s = token_similarity(q, t)
                if s >= MIN_SCORE:
                    out[t] = s
                break
    sq = skeleton(q)
    if other_skel and len(sq) >= 2:
        ls, sch = len(sq), _chunks(sq, 1)
        for t, st in other_skel.items():
            if t not in out and abs(len(st) - ls) <= 1 and any(c in st for c in sch) \
                    and levenshtein(sq, st, 1) <= 1:
                s = token_similarity(q, t)
                if s >= MIN_SCORE:
                    out[t] = s
    return out


def rank_names(query: str, rows: Iterable[tuple[Any, list[tuple[str, str]]]],
               limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """``rows`` = (key, [(field, text), ...]). Every typed token must find a
    similar token in one field (score = mean of the best similarities).
    Similarity is computed once per DISTINCT token and rows are pre-filtered
    with a C-level set test, so 100k rows stay well under a second."""
    qt = tokens(query)
    if not qt or len("".join(qt)) < 2:
        return []
    keys: list[Any] = []
    texts: list[str] = []
    fnames: list[str] = []
    row_of: list[int] = []
    for key, fields in rows:
        r = len(keys)
        keys.append(key)
        for fname, text in fields:
            texts.append(text or "")
            fnames.append(fname)
            row_of.append(r)
    if not keys:
        return []
    normed = _norm_bulk(texts)
    universe: set[str] = set()
    for t in normed:
        universe.update(t.split())
    ar = {t for t in universe if _ARABIC.search(t)}
    sk_cache: dict[bool, dict[str, str]] = {}

    def other(q: str) -> dict[str, str]:
        q_ar = bool(_ARABIC.search(q))
        if q_ar not in sk_cache:
            pool = sorted(universe - ar) if q_ar else sorted(ar)
            sk_cache[q_ar] = dict(zip(pool, skeletons(pool)))
        return sk_cache[q_ar]

    sims = [_similar_tokens(q, universe, other(q)) for q in qt]
    if any(not s for s in sims):
        return []
    hitsets = [set(s) for s in sims]
    best: dict[int, tuple[float, str]] = {}
    nq = len(qt)
    for i, text in enumerate(normed):
        toks = text.split()
        if not toks or any(h.isdisjoint(toks) for h in hitsets):
            continue
        sc = sum(max(sq.get(t, 0.0) for t in toks) for sq in sims) / nq
        r = row_of[i]
        if sc >= MIN_SCORE and sc > best.get(r, (0.0, ""))[0]:
            best[r] = (sc, fnames[i])
    found = [Candidate(keys[r], round(sc, 3), how) for r, (sc, how) in best.items()]
    found.sort(key=lambda c: -c.score)
    return found[:limit]


_DIGIT_BULK_DROP = re.compile("[^0-9\x1f]")
_DIGIT_BULK_PREFIX = re.compile("(^|\x1f)(?:00)?(?:97[02](?=[0-9]{8}))?0*")


def _digit_keys(values: list[str]) -> list[str]:
    """``digit_key`` for many values in one C-level pass."""
    big = latin(_BULK_SEP.join(v.replace(_BULK_SEP, " ") for v in values))
    big = _DIGIT_BULK_PREFIX.sub(r"\1", _DIGIT_BULK_DROP.sub("", big))
    return big.split(_BULK_SEP)


def rank_digits(query: str, rows: Iterable[tuple[Any, list[tuple[str, str]]]],
                limit: int = MAX_CANDIDATES) -> tuple[list[Candidate], list[Candidate]]:
    """(normalised-exact matches, fuzzy matches <= 2 edits) for a digit query.
    Equal length: substitutions (Hamming, covers a swapped pair); a length
    difference of 1-2: pigeonhole pre-filter + bounded Levenshtein."""
    import operator
    qk = digit_key(query)
    lq = len(qk)
    if lq < MIN_DIGITS - 1:
        return [], []
    keys: list[Any] = []
    values: list[str] = []
    fnames: list[str] = []
    row_of: list[int] = []
    for key, fields in rows:
        r = len(keys)
        keys.append(key)
        for fname, value in fields:
            values.append(str(value or ""))
            fnames.append(fname)
            row_of.append(r)
    dk = _digit_keys(values)
    n = lq
    cuts = (0, n // 3, 2 * n // 3, n)
    chunks = tuple(qk[cuts[i]:cuts[i + 1]] for i in range(3))
    ne = operator.ne
    best: dict[int, tuple[int, int]] = {}          # row -> (distance, flat index)
    for i, vk in enumerate(dk):
        lv = len(vk)
        if lv == lq:
            if vk == qk:
                d = 0
            else:
                d = sum(map(ne, qk, vk))
                if d > MAX_DIGIT_EDITS:
                    continue
        elif lv and abs(lv - lq) <= MAX_DIGIT_EDITS:
            if not any(c in vk for c in chunks):
                continue
            d = levenshtein(qk, vk, MAX_DIGIT_EDITS)
            if d > MAX_DIGIT_EDITS:
                continue
        else:
            continue
        r = row_of[i]
        if r not in best or d < best[r][0]:
            best[r] = (d, i)
    exact: list[Candidate] = []
    fuzzy: list[Candidate] = []
    for r, (d, i) in best.items():
        c = Candidate(keys[r], round(1.0 - d / lq, 3), fnames[i], d, {"value": values[i]})
        (exact if d == 0 else fuzzy).append(c)
    fuzzy.sort(key=lambda c: (c.distance, -c.score))
    return exact[:limit], fuzzy[:limit]


# ─────────────────────────── records ────────────────────────────

def mask_tail(value: Any, keep: int = 4) -> str:
    s = str(value or "")
    return ("•" * max(0, len(s) - keep)) + s[-keep:] if len(s) > keep else s


def subscriber_rows(tenant_id: int, owner_admin_id: Optional[int], *, digits: bool):
    """(username, fields) for every subscriber the admin may list — the SAME
    WHERE as GET /accounts (tenant, not deleted, real subscribers, owner
    scope). Only the columns the match needs are read."""
    from ...db.connection import db
    from ...db.repos.subscribers_repo import _subscriber_filter_sql
    where, vals = _subscriber_filter_sql(int(tenant_id), user_type="subscriber",
                                         owner_admin_id=owner_admin_id)
    if digits:
        sql = (f"SELECT username, mobile, national_id FROM subscribers{where} "
               "AND (COALESCE(mobile,'') != '' OR COALESCE(national_id,'') != '' "
               "OR username GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9]*') ORDER BY id DESC")
        for r in db().execute(sql, vals):
            yield r[0], [("phone", r[1] or ""), ("national_id", r[2] or ""),
                         ("username", r[0] if any(c.isdigit() for c in r[0]) else "")]
    else:
        sql = f"SELECT username, full_name FROM subscribers{where} ORDER BY id DESC"
        for r in db().execute(sql, vals):
            yield r[0], [("username", r[0] or ""), ("name", r[1] or "")]


def subscriber_candidates(tenant_id: int, owner_admin_id: Optional[int], query: str):
    """→ (match, [Candidate]) with match ∈ {"normalized", "fuzzy", None}.
    ``normalized`` = the same number written differently (+970 / spaces /
    Arabic digits): an exact record, safe to issue."""
    q = (query or "").strip()
    if not q:
        return None, []
    if is_digit_query(q):
        exact, fuzzy = rank_digits(q, subscriber_rows(tenant_id, owner_admin_id, digits=True))
        if exact:
            return "normalized", exact
        return ("fuzzy", fuzzy) if fuzzy else (None, [])
    found = rank_names(q, subscriber_rows(tenant_id, owner_admin_id, digits=False))
    return ("fuzzy", found) if found else (None, [])


def catalog_candidates(query: str, items: list[dict], name_key: str = "name",
                       limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Plans / offers (short lists already fetched through the API)."""
    rows = [(i, [("name", str(it.get(name_key) or ""))]) for i, it in enumerate(items)]
    return rank_names(query, rows, limit)


def batch_rows(tenant_id: int, scope: Optional[int]):
    from ...db.connection import db
    sql = ("SELECT b.id, b.package_name, b.batch_code FROM card_batches b "
           "WHERE b.tenant_id = ? AND b.deleted_at IS NULL")
    vals: list[Any] = [int(tenant_id)]
    if scope is not None:
        from ...services.card_batch_scope import batch_scope_clause
        clause, cv = batch_scope_clause(int(scope), alias="b")
        sql += " AND " + clause
        vals += cv
    for r in db().execute(sql + " ORDER BY b.id DESC LIMIT 20000", vals):
        yield int(r[0]), [("name", r[1] or ""), ("code", r[2] or "")]


def batch_candidates(tenant_id: int, scope: Optional[int], query: str) -> list[Candidate]:
    return rank_names(query, batch_rows(tenant_id, scope))


__all__ = ["MAX_CANDIDATES", "Candidate", "digit_key", "is_digit_query", "norm_text", "tokens",
           "skeleton", "levenshtein", "token_similarity", "rank_names",
           "rank_digits", "subscriber_candidates", "catalog_candidates", "batch_candidates",
           "mask_tail"]
