"""استخراج وفحص المصدر — يحوّل أيّ ملف مرفوع إلى ``SourceDataset`` موحّد.

يكتشف النوع من المحتوى (magic bytes) لا الامتداد فقط، ويتعامل دفاعيًّا مع
الترميزات والملفات الكبيرة والمُشوَّهة. الأنواع المدعومة:

  • ``sqlite``    — ملف قاعدة SQLite (‎.db/.sqlite): يُفتَح للقراءة فقط ويُفحَص
                    عبر ``sqlite_master`` + ``PRAGMA table_info``.
  • ``sql_dump``  — تفريغ SQL (MySQL/MariaDB/Postgres): يُحلّل ``CREATE TABLE``
                    لترتيب الأعمدة و``INSERT INTO`` للصفوف (تسامُح مع backticks
                    وعلامات الاقتباس واللهجات).
  • ``xlsx``      — Excel: كل ورقة = جدول.
  • ``csv``       — CSV/TSV/نص: جدول واحد، مع كشف الفاصل والترويسة.
  • ``pdf``       — PDF جدوليّ عبر pdfplumber (أفضل-جهد، يتراجع بلطف).
  • ``mikrotik``  — تصدير RouterOS ‎.rsc: ‎/ppp secret + /ip hotspot user.

كلّ القيم تُطبَّع إلى نصوص. الدوال خالصة (لا Flask/DB).
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import tempfile

from .model import SourceDataset, SourceTable

# حدود أمان — تمنع ملفًّا خبيثًا/ضخمًا من إغراق الذاكرة.
MAX_ROWS_PER_TABLE = 500_000
MAX_CELL_LEN = 8_000
MAX_COLUMNS = 256

_ENCODING_CHAIN = ("utf-8-sig", "utf-8", "cp1256", "windows-1256", "cp1252", "latin-1")
_SQLITE_MAGIC = b"SQLite format 3\x00"


# ════════════════════════════════════════════════════════════════════
# الواجهة العامّة
# ════════════════════════════════════════════════════════════════════

def sniff_source(file_bytes: bytes, filename: str = "") -> str:
    """يكتشف نوع المصدر من المحتوى أولًا ثمّ الامتداد."""
    if not file_bytes:
        return "unknown"
    head = file_bytes[:64]
    if head.startswith(_SQLITE_MAGIC):
        return "sqlite"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        if b"xl/" in file_bytes[:8192] or b"[Content_Types].xml" in file_bytes[:8192]:
            return "xlsx"
    if head.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
        return "xls-legacy"

    lower = (filename or "").lower()
    if lower.endswith((".db", ".sqlite", ".sqlite3")):
        # امتداد قاعدة لكن بلا توقيع — قد يكون فارغًا أو تالفًا.
        return "sqlite"
    if lower.endswith(".rsc"):
        return "mikrotik"
    if lower.endswith(".sql"):
        return "sql_dump"
    if lower.endswith(".xlsx"):
        return "xlsx"
    if lower.endswith(".pdf"):
        return "pdf"
    if lower.endswith((".csv", ".tsv", ".txt")):
        return "csv"

    # استدلال على المحتوى النصّي.
    sample = _decode_text(file_bytes[:65536])
    upper = sample.upper()
    if "CREATE TABLE" in upper or re.search(r"\bINSERT\s+INTO\b", upper):
        return "sql_dump"
    if re.search(r"/(ppp\s+secret|ip\s+hotspot\s+user|ip\s+hotspot\s+/user)", sample) \
            or re.search(r"^\s*add\s+name=", sample, re.MULTILINE):
        return "mikrotik"
    if any(d in sample for d in (",", ";", "\t", "|")) and "\n" in sample:
        return "csv"
    return "unknown"


def introspect(file_bytes: bytes, filename: str = "") -> SourceDataset:
    """نقطة الدخول — يكتشف النوع ويُرجع مجموعة بيانات موحّدة (للقراءة فقط)."""
    fmt = sniff_source(file_bytes, filename)
    ds = SourceDataset(fmt=fmt)
    try:
        if fmt == "sqlite":
            _from_sqlite(file_bytes, ds)
        elif fmt == "sql_dump":
            _from_sql_dump(file_bytes, ds)
        elif fmt == "xlsx":
            _from_xlsx(file_bytes, ds)
        elif fmt == "csv":
            _from_csv(file_bytes, ds, filename)
        elif fmt == "pdf":
            _from_pdf(file_bytes, ds)
        elif fmt == "mikrotik":
            _from_mikrotik(file_bytes, ds)
        elif fmt == "xls-legacy":
            ds.warnings.append(
                "صيغة .xls القديمة غير مدعومة — احفظ الملف بصيغة .xlsx ثم أعد الرفع.")
        else:
            ds.warnings.append(
                "تعذّر التعرّف على نوع الملف. المدعوم: قاعدة SQLite، تفريغ SQL، "
                "Excel/CSV، PDF جدوليّ، تصدير MikroTik (.rsc).")
    except Exception as exc:  # noqa: BLE001 — لا نُسقط التحليل على عطل مُستخرِج
        ds.warnings.append(f"خطأ أثناء قراءة المصدر: {exc}")
    # تنظيف: أسقط الجداول الفارغة تمامًا (بلا أعمدة وبلا صفوف).
    ds.tables = [t for t in ds.tables if t.columns or t.rows]
    return ds


# ════════════════════════════════════════════════════════════════════
# أدوات مشتركة
# ════════════════════════════════════════════════════════════════════

def _decode_text(file_bytes: bytes) -> str:
    for enc in _ENCODING_CHAIN:
        try:
            return file_bytes.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return file_bytes.decode("utf-8", errors="replace")


def _clip(value) -> str:
    if value is None:
        return ""
    s = str(value)
    if len(s) > MAX_CELL_LEN:
        s = s[:MAX_CELL_LEN]
    return s


def _looks_like_header(cells: list[str]) -> bool:
    """صفّ ترويسة: خلايا غير فارغة، أغلبها غير رقميّ، ومتمايزة."""
    cleaned = [c.strip() for c in cells]
    if not cleaned or not all(cleaned):
        return False
    if len(set(c.lower() for c in cleaned)) != len(cleaned):
        return False
    numeric = sum(1 for c in cleaned if re.fullmatch(r"[-+]?\d+(\.\d+)?", c))
    return numeric <= len(cleaned) * 0.4


def _make_columns(width: int, header: list[str] | None) -> list[str]:
    if header:
        cols, seen = [], {}
        for i in range(width):
            raw = (header[i].strip() if i < len(header) and header[i] else "") or f"col{i+1}"
            key = raw
            if key in seen:
                seen[key] += 1
                key = f"{raw}_{seen[raw]}"
            else:
                seen[key] = 0
            cols.append(key)
        return cols
    return [f"col{i+1}" for i in range(width)]


def _rows_to_table(name: str, grid: list[list[str]], origin: str,
                   *, note: str = "") -> SourceTable:
    """يحوّل شبكة خلايا (مع كشف الترويسة) إلى ``SourceTable``."""
    grid = [[_clip(c) for c in row] for row in grid if any(str(c).strip() for c in row)]
    if not grid:
        return SourceTable(name=name, origin=origin, note=note)
    width = min(max(len(r) for r in grid), MAX_COLUMNS)
    header = grid[0] if _looks_like_header(grid[0]) else None
    columns = _make_columns(width, header)
    data_rows = grid[1:] if header else grid
    rows: list[dict[str, str]] = []
    for r in data_rows[:MAX_ROWS_PER_TABLE]:
        rows.append({columns[i]: (r[i] if i < len(r) else "") for i in range(width)})
    if len(data_rows) > MAX_ROWS_PER_TABLE:
        note = (note + " ").strip() + f"(اقتُصرت إلى {MAX_ROWS_PER_TABLE} صفًّا)"
    return SourceTable(name=name, columns=columns, rows=rows, origin=origin, note=note)


# ════════════════════════════════════════════════════════════════════
# SQLite
# ════════════════════════════════════════════════════════════════════

def _from_sqlite(file_bytes: bytes, ds: SourceDataset) -> None:
    # نكتب البايتات لملف مؤقّت ونفتحه للقراءة فقط (immutable) — أسلم من فتح
    # مُدخَل غير موثوق على القرص الحيّ.
    tmp = tempfile.NamedTemporaryFile(prefix="mig_src_", suffix=".db", delete=False)
    try:
        tmp.write(file_bytes)
        tmp.flush()
        tmp.close()
        uri = f"file:{tmp.name}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        try:
            names = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
            if not names:
                ds.warnings.append("قاعدة SQLite لا تحتوي جداول قابلة للقراءة.")
            for tname in names:
                try:
                    cols = [r["name"] for r in conn.execute(
                        f'PRAGMA table_info("{tname}")').fetchall()][:MAX_COLUMNS]
                    if not cols:
                        # PRAGMA على view قد لا يُرجع أعمدة — استخرجها من أوّل صفّ.
                        cur = conn.execute(f'SELECT * FROM "{tname}" LIMIT 1')
                        cols = [d[0] for d in (cur.description or [])][:MAX_COLUMNS]
                    cur = conn.execute(
                        f'SELECT * FROM "{tname}" LIMIT {MAX_ROWS_PER_TABLE}')
                    rows = []
                    for raw in cur.fetchall():
                        rows.append({c: _clip(raw[c]) for c in cols if c in raw.keys()})
                    ds.tables.append(SourceTable(
                        name=tname, columns=cols, rows=rows, origin="sqlite"))
                except sqlite3.Error as exc:
                    ds.warnings.append(f"تعذّرت قراءة الجدول «{tname}»: {exc}")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        ds.warnings.append(f"ملف ليس قاعدة SQLite صالحة: {exc}")
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


# ════════════════════════════════════════════════════════════════════
# تفريغ SQL (MySQL / MariaDB / Postgres)
# ════════════════════════════════════════════════════════════════════

def _strip_ident(tok: str) -> str:
    tok = tok.strip()
    if len(tok) >= 2 and tok[0] in "`\"[" and tok[-1] in "`\"]":
        tok = tok[1:-1]
    # schema.table → table
    if "." in tok:
        tok = tok.split(".")[-1].strip("`\"[]")
    return tok.strip()


_CREATE_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`"\[]?[\w\.]+[`"\]]?)\s*\((.*?)\)\s*'
    r'(?:ENGINE|DEFAULT|;|AUTO_INCREMENT|COMMENT|/\*|WITHOUT|STRICT)',
    re.IGNORECASE | re.DOTALL)


def _parse_create_columns(body: str) -> list[str]:
    """يستخرج أسماء الأعمدة (بالترتيب) من جسم ``CREATE TABLE`` المتسامح."""
    cols: list[str] = []
    depth = 0
    cur = []
    parts: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur))
    _NON_COL = ("primary", "unique", "key", "constraint", "foreign",
                "index", "check", "fulltext", "spatial")
    for part in parts:
        p = part.strip()
        if not p:
            continue
        first = p.split(None, 1)[0]
        if first.lower().strip("`\"[]") in _NON_COL:
            continue
        cols.append(_strip_ident(first))
        if len(cols) >= MAX_COLUMNS:
            break
    return cols


def _split_sql_values(segment: str) -> list[list[str]]:
    """يحلّل ``(...),(...),(...)`` إلى صفوف قيم (نصوص؛ NULL→'')."""
    rows: list[list[str]] = []
    i, n = 0, len(segment)
    while i < n and len(rows) < MAX_ROWS_PER_TABLE:
        while i < n and segment[i] in " \t\r\n,":
            i += 1
        if i >= n or segment[i] != "(":
            if i < n and segment[i] == ";":
                break
            i += 1
            continue
        i += 1  # تخطّى '('
        values: list[str] = []
        cur: list[str] = []
        in_str = False
        quote = ""
        while i < n:
            ch = segment[i]
            if in_str:
                if ch == "\\" and i + 1 < n:        # هروب على نمط MySQL
                    cur.append(segment[i + 1])
                    i += 2
                    continue
                if ch == quote:
                    if i + 1 < n and segment[i + 1] == quote:  # '' داخل النصّ
                        cur.append(quote)
                        i += 2
                        continue
                    in_str = False
                    i += 1
                    continue
                cur.append(ch)
                i += 1
                continue
            if ch in ("'", '"'):
                in_str = True
                quote = ch
                i += 1
                continue
            if ch == ",":
                values.append("".join(cur).strip())
                cur = []
                i += 1
                continue
            if ch == ")":
                values.append("".join(cur).strip())
                i += 1
                break
            cur.append(ch)
            i += 1
        norm = []
        for v in values:
            vs = v.strip()
            if vs.upper() == "NULL":
                vs = ""
            norm.append(_clip(vs))
        if norm:
            rows.append(norm)
    return rows


def _from_sql_dump(file_bytes: bytes, ds: SourceDataset) -> None:
    text = _decode_text(file_bytes)
    # خرائط ترتيب الأعمدة لكل جدول من CREATE TABLE.
    create_cols: dict[str, list[str]] = {}
    for m in _CREATE_RE.finditer(text):
        tname = _strip_ident(m.group(1))
        cols = _parse_create_columns(m.group(2))
        if tname and cols:
            create_cols[tname.lower()] = cols

    # اجمع صفوف INSERT لكل جدول (قد تتعدّد عبارات INSERT لنفس الجدول).
    insert_re = re.compile(
        r'INSERT\s+(?:IGNORE\s+)?INTO\s+([`"\[]?[\w\.]+[`"\]]?)\s*(\([^)]*\))?\s*VALUES',
        re.IGNORECASE)
    table_rows: dict[str, list[list[str]]] = {}
    table_cols: dict[str, list[str]] = {}
    table_order: list[str] = []

    pos = 0
    for m in insert_re.finditer(text):
        tname = _strip_ident(m.group(1))
        key = tname.lower()
        explicit_cols = None
        if m.group(2):
            explicit_cols = [_strip_ident(c) for c in m.group(2)[1:-1].split(",")]
        # القطعة من بعد VALUES حتى الفاصلة المنقوطة المنطقيّة التالية.
        start = m.end()
        end = text.find(";", start)
        if end == -1:
            end = len(text)
        segment = text[start:end]
        rows = _split_sql_values(segment)
        if not rows:
            continue
        if key not in table_rows:
            table_rows[key] = []
            table_order.append(key)
            table_cols[key] = explicit_cols or create_cols.get(key) or []
        elif explicit_cols and not table_cols.get(key):
            table_cols[key] = explicit_cols
        table_rows[key].extend(rows)
        pos = end

    if not table_rows:
        ds.warnings.append("لم يُعثَر على عبارات INSERT قابلة للقراءة في تفريغ SQL.")
        # ما يزال بإمكاننا عرض بنية الجداول الفارغة (أعمدة فقط).
        for key, cols in create_cols.items():
            ds.tables.append(SourceTable(name=key, columns=cols, rows=[],
                                         origin="sql_dump", note="بنية فقط (بلا صفوف)"))
        return

    for key in table_order:
        rows = table_rows[key][:MAX_ROWS_PER_TABLE]
        cols = table_cols.get(key) or create_cols.get(key) or []
        width = max((len(r) for r in rows), default=len(cols))
        if not cols or len(cols) < width:
            cols = (cols + [f"col{i+1}" for i in range(len(cols), width)])[:width]
        dict_rows = []
        for r in rows:
            dict_rows.append({cols[i]: (r[i] if i < len(r) else "")
                              for i in range(min(width, len(cols)))})
        ds.tables.append(SourceTable(name=key, columns=cols[:width], rows=dict_rows,
                                     origin="sql_dump"))


# ════════════════════════════════════════════════════════════════════
# Excel / CSV / PDF
# ════════════════════════════════════════════════════════════════════

def _from_xlsx(file_bytes: bytes, ds: SourceDataset) -> None:
    try:
        from openpyxl import load_workbook
    except ImportError:
        ds.warnings.append("مكتبة قراءة Excel غير مثبّتة على الخادم (openpyxl).")
        return
    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001
        ds.warnings.append(f"تعذّرت قراءة ملف Excel: {exc}")
        return
    try:
        for ws in wb.worksheets:
            grid: list[list[str]] = []
            for raw in ws.iter_rows(values_only=True):
                grid.append([_xlsx_cell(c) for c in raw])
                if len(grid) > MAX_ROWS_PER_TABLE + 1:
                    break
            t = _rows_to_table(ws.title, grid, "xlsx")
            if t.columns or t.rows:
                ds.tables.append(t)
    finally:
        wb.close()
    if not ds.tables:
        ds.warnings.append("ملف Excel لا يحتوي أوراقًا قابلة للقراءة.")


def _xlsx_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _from_csv(file_bytes: bytes, ds: SourceDataset, filename: str) -> None:
    text = _decode_text(file_bytes).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        ds.warnings.append("الملف فارغ.")
        return
    sample = "\n".join(text.split("\n")[:20])
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delimiter = _best_delimiter(sample)
    grid: list[list[str]] = []
    for raw in csv.reader(io.StringIO(text), delimiter=delimiter):
        grid.append([_clip(c) for c in raw])
    name = os.path.splitext(os.path.basename(filename or "data"))[0] or "data"
    t = _rows_to_table(name, grid, "csv")
    if t.columns or t.rows:
        ds.tables.append(t)
    else:
        ds.warnings.append("تعذّرت قراءة صفوف من ملف CSV.")


def _best_delimiter(sample: str) -> str:
    best = (",", -1.0)
    for d in (",", ";", "\t", "|"):
        try:
            counts = [len(r) for r in csv.reader(io.StringIO(sample), delimiter=d)
                      if any(c.strip() for c in r)]
        except csv.Error:
            continue
        if not counts:
            continue
        avg = sum(counts) / len(counts)
        if avg <= 1.0:
            continue
        var = sum((c - avg) ** 2 for c in counts) / len(counts)
        score = avg - var * 0.5
        if score > best[1]:
            best = (d, score)
    return best[0]


def _from_pdf(file_bytes: bytes, ds: SourceDataset) -> None:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        ds.warnings.append("مكتبة قراءة PDF غير مثبّتة على الخادم (pdfplumber).")
        return
    n_table = 0
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for pi, page in enumerate(pdf.pages):
                try:
                    page_tables = page.extract_tables() or []
                except Exception:  # noqa: BLE001
                    page_tables = []
                for tbl in page_tables:
                    grid = [[_clip(c) for c in (row or [])] for row in (tbl or [])]
                    t = _rows_to_table(f"pdf_table_{pi+1}_{n_table+1}", grid, "pdf")
                    if t.rows:
                        ds.tables.append(t)
                        n_table += 1
    except Exception as exc:  # noqa: BLE001
        ds.warnings.append(f"تعذّر استخراج جداول PDF: {exc}")
    if not ds.tables:
        ds.warnings.append(
            "لم تُستخرَج جداول من PDF — قد لا يكون جدوليًّا. حوّله إلى Excel/CSV "
            "للحصول على نتيجة أدقّ.")


# ════════════════════════════════════════════════════════════════════
# MikroTik RouterOS export (.rsc)
# ════════════════════════════════════════════════════════════════════

_RSC_KV_RE = re.compile(r'([\w\-]+)=("(?:[^"\\]|\\.)*"|\S+)')


def _parse_rsc_kv(line: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _RSC_KV_RE.finditer(line):
        key = m.group(1).strip()
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
            val = val[1:-1].replace('\\"', '"')
        out[key] = _clip(val)
    return out


def _from_mikrotik(file_bytes: bytes, ds: SourceDataset) -> None:
    text = _decode_text(file_bytes)
    # طيّ أسطر المتابعة (سطر ينتهي بـ '\' يُكمَل في التالي).
    text = re.sub(r"\\\s*\n", " ", text)
    current = ""   # القسم الحاليّ من سطر يبدأ بـ '/'
    buckets: dict[str, list[dict[str, str]]] = {}
    order: list[str] = []

    def bucket_for(section: str) -> str | None:
        s = section.lower()
        if "ppp" in s and "secret" in s:
            return "ppp_secrets"
        if "hotspot" in s and "user" in s and "profile" not in s:
            return "hotspot_users"
        if "ppp" in s and "profile" in s:
            return "ppp_profiles"
        if "hotspot" in s and "profile" in s:
            return "hotspot_profiles"
        return None

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/"):
            current = line.lstrip("/").strip()
            continue
        # سطر بيانات: قد يبدأ بـ add/set أو بـ kv مباشرة.
        verb = ""
        rest = line
        first = line.split(None, 1)
        if first and first[0].lower() in ("add", "set"):
            verb = first[0].lower()
            rest = first[1] if len(first) > 1 else ""
        if verb and verb != "add":
            continue
        kv = _parse_rsc_kv(rest)
        if not kv:
            continue
        bkey = bucket_for(current)
        if bkey is None:
            # قد يكون سطر add يحمل قرينة على القسم (مثلًا داخل /ppp secret).
            continue
        buckets.setdefault(bkey, [])
        if bkey not in order:
            order.append(bkey)
        buckets[bkey].append(kv)

    if not buckets:
        ds.warnings.append(
            "لم يُعثَر على ‎/ppp secret أو /ip hotspot user في تصدير MikroTik.")
        return

    for bkey in order:
        recs = buckets[bkey][:MAX_ROWS_PER_TABLE]
        cols: list[str] = []
        for r in recs:
            for k in r.keys():
                if k not in cols:
                    cols.append(k)
        cols = cols[:MAX_COLUMNS]
        rows = [{c: r.get(c, "") for c in cols} for r in recs]
        ds.tables.append(SourceTable(name=bkey, columns=cols, rows=rows,
                                     origin="mikrotik"))


__all__ = ["sniff_source", "introspect", "MAX_ROWS_PER_TABLE"]
