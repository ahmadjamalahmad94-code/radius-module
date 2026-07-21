"""MT24 — نسخ احتياطية معزولة لكل شبكة (per-tenant backup/restore).

كل شبكة تنسخ/تستعيد بياناتها فقط — مستقلّة تمامًا عن بقية الشبكات.
النسخة = تصدير كل صفوف الجداول التي تحمل عمود tenant_id حيث tenant_id=X،
إلى ملفّ JSON مضغوط (gzip) في مجلد خاصّ بالشبكة. الاستعادة تحذف صفوف
الشبكة الحالية وتُعيد إدراج صفوف النسخة (بنفس الجهة).

آمن: يكتشف الجداول ديناميكيًّا (PRAGMA)، فلا يكسر عند إضافة جداول جديدة.
لا يمسّ صفوف الجهات الأخرى إطلاقًا (كل الاستعلامات مقيّدة بـtenant_id).
"""
from __future__ import annotations

import gzip
import json
import time
from pathlib import Path
from typing import Optional

from ..db.connection import db, db_path, transaction

_SCHEMA = 1
# جداول تحمل tenant_id لكنها بنية مشتركة لا تخصّ بيانات الشبكة (نستثنيها).
_EXCLUDE = {"tenants", "tenant_memberships", "tenant_settings", "admins",
            "roles", "schema_migrations", "meta",
            # MT32 — مراسلات الشبكة مع المزوّد: ليست بيانات تشغيل، ولا يجوز
            # أن تَمحوها استعادةُ نسخةٍ قديمة (الاستعادة تَحذف ثم تُدرِج).
            "provider_chat_messages",
            # MT36 — طلبات الاشتراك: جدول منصّة لا جدول شبكة. عموده
            # ``tenant_id`` يعني «الشبكة التي وُلدت من الطلب» لا «الشبكة
            # المالكة»، فلولا هذا الاستثناء لالتقطه الكشف التلقائي أدناه
            # (أيّ جدول فيه عمود tenant_id) وسرَّب طلبات الآخرين إلى نسخة
            # كل شبكة، ثم محاها عند أوّل استعادة.
            "signup_requests"}


def _tenant_dir(tenant_id: int) -> Path:
    d = Path(db_path()).parent / "tenant-backups" / str(int(tenant_id))
    d.mkdir(parents=True, exist_ok=True)
    return d


def tenant_tables() -> list[str]:
    """كل جداول القاعدة التي تحمل عمود tenant_id (عدا البنية المشتركة)."""
    out = []
    conn = db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%'").fetchall()
    for r in rows:
        name = r["name"]
        if name in _EXCLUDE:
            continue
        cols = conn.execute(f"PRAGMA table_info('{name}')").fetchall()
        if any(c["name"] == "tenant_id" for c in cols):
            out.append(name)
    return sorted(out)


def export_tenant(tenant_id: int, *, actor: str = "") -> dict:
    """يصدّر بيانات الشبكة إلى ملفّ مضغوط. يعيد معلومات النسخة."""
    tid = int(tenant_id)
    conn = db()
    tables = tenant_tables()
    data: dict = {}
    total = 0
    for t in tables:
        try:
            rows = conn.execute(
                f"SELECT * FROM \"{t}\" WHERE tenant_id = ?", (tid,)).fetchall()
        except Exception:  # noqa: BLE001 — جدول شاذّ لا يكسر النسخة
            continue
        if rows:
            data[t] = [dict(r) for r in rows]
            total += len(rows)
    slug = ""
    try:
        row = conn.execute("SELECT slug FROM tenants WHERE id=?", (tid,)).fetchone()
        slug = row["slug"] if row else ""
    except Exception:  # noqa: BLE001
        pass
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "schema": _SCHEMA, "tenant_id": tid, "tenant_slug": slug,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "actor": actor, "row_count": total, "tables": data,
    }
    fname = f"{slug or 'tenant'}-{stamp}.json.gz"
    path = _tenant_dir(tid) / fname
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    with gzip.open(path, "wb") as f:
        f.write(raw)
    _prune(tid, keep=20)
    return {"name": fname, "size": path.stat().st_size, "rows": total,
            "tables": len(data), "created_at": payload["created_at"]}


def list_tenant_backups(tenant_id: int) -> list[dict]:
    d = _tenant_dir(tenant_id)
    out = []
    for p in sorted(d.glob("*.json.gz"), reverse=True):
        st = p.stat()
        out.append({
            "name": p.name, "size": st.st_size,
            "size_human": _human(st.st_size),
            "mtime": time.strftime("%Y-%m-%d %H:%M", time.gmtime(st.st_mtime)),
        })
    return out


def read_backup_bytes(tenant_id: int, name: str) -> Optional[bytes]:
    p = _safe_path(tenant_id, name)
    if not p or not p.exists():
        return None
    return p.read_bytes()


def delete_tenant_backup(tenant_id: int, name: str) -> bool:
    p = _safe_path(tenant_id, name)
    if p and p.exists():
        p.unlink()
        return True
    return False


def restore_tenant(tenant_id: int, name: str, *, actor: str = "") -> dict:
    """يستعيد بيانات الشبكة من نسخة: يحذف صفوف الشبكة الحالية ويُعيد إدراج
    صفوف النسخة. مقيّد بـtenant_id — لا يمسّ الجهات الأخرى.

    الحفاظ على الـids الأصلية: النسخة تُعيد نفس المعرّفات (كي تبقى الروابط
    الداخلية سليمة). آمن لأن الحذف يسبق الإدراج ضمن نفس الجهة فقط.
    """
    tid = int(tenant_id)
    p = _safe_path(tid, name)
    if not p or not p.exists():
        raise FileNotFoundError("النسخة غير موجودة")
    with gzip.open(p, "rb") as f:
        payload = json.loads(f.read().decode("utf-8"))
    if int(payload.get("tenant_id", -1)) != tid:
        raise ValueError("النسخة تخصّ شبكة أخرى — رُفضت الاستعادة")
    tables = payload.get("tables", {})
    live = set(tenant_tables())
    restored = 0
    with transaction() as conn:
        # المفاتيح الأجنبيّة مؤجَّلة حتى الـCOMMIT: الحذف الجماعيّ يكسر روابط
        # داخليّة (cards→card_batches، subscribers→access_plans...) ثم يُعيدها
        # الإدراج في نفس المعاملة. بدونها يفشل أول DELETE بـFOREIGN KEY
        # constraint failed. تُعاد تلقائيًّا عند نهاية المعاملة.
        conn.execute("PRAGMA defer_foreign_keys = ON")
        # نحذف **كل** جداول الجهة الحيّة لا ما ورد في النسخة فقط، وإلّا بقيت
        # صفوفٌ أُنشئت بعد النسخة في جداول كانت فارغة وقتها (استعادة ناقصة).
        for t in live:
            conn.execute(f"DELETE FROM \"{t}\" WHERE tenant_id = ?", (tid,))
        for t, rows in tables.items():
            if t not in live or not rows:
                continue
            cols = list(rows[0].keys())
            ph = ",".join("?" for _ in cols)
            colsql = ",".join(f'"{c}"' for c in cols)
            for r in rows:
                r["tenant_id"] = tid  # ضمان الجهة الصحيحة
                conn.execute(
                    f"INSERT INTO \"{t}\" ({colsql}) VALUES ({ph})",
                    [r.get(c) for c in cols])
                restored += 1
    return {"restored_rows": restored, "tables": len(tables),
            "from": payload.get("created_at")}


# ─────────── helpers ───────────

def _safe_path(tenant_id: int, name: str) -> Optional[Path]:
    name = (name or "").strip()
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    return _tenant_dir(tenant_id) / name


def _prune(tenant_id: int, keep: int) -> None:
    files = sorted(_tenant_dir(tenant_id).glob("*.json.gz"), reverse=True)
    for p in files[keep:]:
        try:
            p.unlink()
        except OSError:
            pass


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
