"""طلبات الاشتراك من صفحة هبوط المنصّة — MT36.

جدولٌ على مستوى المنصّة لا الشبكة: الزائر مجهول ولا جهة له بعد. لذلك
لا دالّة هنا تأخذ ``tenant_id`` للعزل — العزل الوحيد المعنيّ أنّ القراءة
كلّها محصورة بلوحة المزوّد (المالك)، ويفرضه حارس المسار لا هذا الملف.

التنظيف والتقصير يحدثان هنا لا في المخطَّط، لأنّ المُدخَل عموميّ من
مجهول: نقصّ الطول ونُجرّد الفراغات قبل الكتابة كي لا يُخزَّن نصّ ضخم
يُثقل اللوحة أو يُشوّه العرض.
"""
from __future__ import annotations

from typing import Any, Optional

from ..connection import db, transaction
from ..helpers import now_iso

# حدود طول متحفّظة — مُدخَل عموميّ، والغرض حماية العرض لا التحقّق الدلاليّ.
_MAX = {"network_name": 120, "slug_wanted": 60, "contact_name": 120,
        "phone": 40, "email": 160, "note": 1000, "source_ip": 64}


def _clean(field: str, value: Any) -> str:
    return str(value or "").strip()[:_MAX.get(field, 200)]


def _clean_count(value: Any, cap: int) -> int:
    """عدّادٌ من مُدخَل عموميّ: غير الرقميّ ⇒ 0 (لم يُحدَّد)، والسالب يُقصّ،
    والمبالَغ فيه يُسقَّف — فلا يُفسد رقمٌ عبثيّ عرضَ الطلبات."""
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, min(n, cap))


def _norm_country(v: Any) -> str:
    """MT67 — رمز دولةٍ من الكتالوج فقط ('' لغيره) — لا مُدخَل حرّ."""
    try:
        from ...services.geo_catalog import normalize_country
        return normalize_country(v)
    except Exception:  # noqa: BLE001
        return ""


def create(*, network_name: str, slug_wanted: str = "", contact_name: str = "",
           phone: str = "", email: str = "", note: str = "",
           source_ip: str = "", wanted_concurrent: Any = 0,
           wanted_routers: Any = 0, country: str = "") -> int:
    """يُسجّل طلبًا جديدًا ويُعيد معرّفه.

    ``wanted_concurrent``/``wanted_routers`` سعةٌ يَطلبها العميل (0 = لم
    يُحدَّد) — تُقابَل بعروض الأسعار وحدّ الراوترات عند الموافقة."""
    with transaction() as conn:
        cur = conn.execute(
            """INSERT INTO signup_requests
               (network_name, slug_wanted, contact_name, phone, email, note,
                status, created_at, source_ip, wanted_concurrent, wanted_routers,
                country)
               VALUES (?,?,?,?,?,?,'pending',?,?,?,?,?)""",
            (_clean("network_name", network_name), _clean("slug_wanted", slug_wanted),
             _clean("contact_name", contact_name), _clean("phone", phone),
             _clean("email", email), _clean("note", note),
             now_iso(), _clean("source_ip", source_ip),
             _clean_count(wanted_concurrent, 1_000_000),
             _clean_count(wanted_routers, 10_000),
             _norm_country(country)),               # MT67
        )
        return int(cur.lastrowid or 0)


def pending_count() -> int:
    row = db().execute(
        "SELECT COUNT(*) AS n FROM signup_requests WHERE status='pending'"
    ).fetchone()
    return int(row["n"] or 0) if row else 0


def list_all(*, status: str = "", limit: int = 200) -> list[dict]:
    """أحدث الطلبات أوّلًا. ``status`` فارغة = الكل."""
    sql = "SELECT * FROM signup_requests"
    args: list[Any] = []
    if status:
        sql += " WHERE status = ?"
        args.append(status)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def get(request_id: int) -> Optional[dict]:
    row = db().execute(
        "SELECT * FROM signup_requests WHERE id = ?", (int(request_id),)
    ).fetchone()
    return dict(row) if row else None


def mark(request_id: int, *, status: str, by: str = "", tenant_id: int = 0) -> None:
    """يُغلق الطلب بقبولٍ أو رفض.

    ``tenant_id`` يُملأ عند القبول بالشبكة التي وُلدت من الطلب، فيبقى
    الأثر موصولًا: أيّ شبكة جاءت من أيّ طلب.
    """
    if status not in {"approved", "rejected"}:
        raise ValueError(f"unsupported signup request status: {status!r}")
    with transaction() as conn:
        conn.execute(
            """UPDATE signup_requests
               SET status = ?, handled_at = ?, handled_by = ?, tenant_id = ?
               WHERE id = ?""",
            (status, now_iso(), str(by or "")[:120], int(tenant_id), int(request_id)),
        )
