"""مستودع الإشعارات الموحّد — قراءة/كتابة جدول notifications.

العمود الفقري لمركز الإشعارات: إشعارات محلّية (قرب انتهاء الاشتراك/الخدمة)
وإشعارات مدفوعة من لوحة التراخيص عبر الجسر. إزالة التكرار بـ dedup_key
(فهرس فريد جزئي)، وحالة القراءة عبر read_at. كل الدوال tenant-scoped.
"""
from __future__ import annotations

from typing import Optional

from ..connection import db, transaction
from ..helpers import now_iso

# الأنواع/الشدّات المعتمدة (للتحقّق الناعم في الطبقة الأعلى).
TYPES = ("license", "subscription", "service", "support", "billing", "system")
SEVERITIES = ("info", "success", "warning", "critical")


def _row(r) -> dict:
    return {
        "id": int(r["id"]),
        "tenant_id": int(r["tenant_id"]),
        "type": r["type"] or "system",
        "severity": r["severity"] or "info",
        "title": r["title"] or "",
        "body": r["body"] or "",
        "link": r["link"] or "",
        "dedup_key": r["dedup_key"] or "",
        "source": r["source"] or "local",
        "source_ref": r["source_ref"] or "",
        "read_at": r["read_at"] or "",
        "created_at": r["created_at"] or "",
        "is_read": bool(r["read_at"]),
        # MT90 — الصفّ يُبنى بمفاتيح صريحة، فعمودٌ جديد يسقط صامتًا ما لم
        # يُضَف هنا. و`keys()` تحرس ضدّ قاعدةٍ لم تُهاجَر بعد.
        "event_key": (r["event_key"] or "") if "event_key" in r.keys() else "",
    }


def _row_for(r, viewer) -> dict:
    """``_row`` + the viewer's own read state (fix3: per-admin reads)."""
    d = _row(r)
    if viewer is not None and "_viewer_read" in r.keys():
        d["is_read"] = bool(r["_viewer_read"])
    return d


# ── fix3 (F01 F9 / F08 M2): per-admin visibility + read state ─────────────
# ``viewer`` = None → the tenant-wide legacy behaviour (owner / co-owner /
# unbound credential / internal callers). Otherwise a dict built by
# ``services.notifications.viewer_for``:
#   admin_id   — the viewing admin;
#   scope      — None = sees every subscriber, else the owner-scope admin id;
#   users_view — may see subscriber notifications at all;
#   audiences  — non-subscriber groups he may see (network/finance/store…).
def _visible_sql(tenant_id: int, viewer) -> tuple[str, list]:
    if viewer is None:
        return "", []
    aid = int(viewer["admin_id"])
    parts = ["actor_admin_id = ?"]
    vals: list = [aid]
    if viewer.get("users_view"):
        if viewer.get("scope") is None:
            parts.append("subscriber_username <> ''")
        else:
            from ..repos.subscribers_repo import _owner_scope_sql
            clause, cvals = _owner_scope_sql(int(viewer["scope"]))
            parts.append("(subscriber_username <> '' AND subscriber_username IN ("
                         "SELECT username FROM subscribers WHERE tenant_id = ?" + clause + "))")
            vals += [int(tenant_id), *cvals]
    auds = sorted(a for a in (viewer.get("audiences") or ()) if a)
    if auds:
        parts.append("(subscriber_username = '' AND audience IN (%s))"
                     % ",".join("?" for _ in auds))
        vals += auds
    return " AND (" + " OR ".join(parts) + ")", vals


def _read_sql(viewer) -> tuple[str, list]:
    """SQL boolean «read for this viewer» (global read_at, or his own read row)."""
    if viewer is None:
        return "(read_at <> '')", []
    return ("(read_at <> '' OR EXISTS (SELECT 1 FROM panel_notification_reads nr "
            "WHERE nr.notification_id = panel_notifications.id AND nr.admin_id = ?))",
            [int(viewer["admin_id"])])


def create_returning(tenant_id: int, *, type: str = "system",
                     severity: str = "info", title: str = "", body: str = "",
                     link: str = "", dedup_key: str = "", source: str = "local",
                     source_ref: str = "",
                     event_key: str = "", subscriber_username: str = "",
                     audience: str = "",
                     actor_admin_id: Optional[int] = None) -> tuple[Optional[int], bool]:
    """يُدرج إشعارًا ويُرجع (id, is_new).

    is_new=True فقط حين أُدرج صفّ جديد فعلًا؛ وعند إصابة dedup_key مكرّر
    (INSERT OR IGNORE على الفهرس الفريد الجزئي) يُرجع (id_القائم, False).
    تَستعمله طبقة notify() كي تُطلق الدفع **مرّة واحدة** للإشعار الجديد ولا
    تُكرّره عند إعادة المحاولة بنفس المفتاح. يُرجع (None, False) عند الفشل."""
    sev = severity if severity in SEVERITIES else "info"
    src = source if source in ("local", "bridge") else "local"
    now = now_iso()
    with transaction() as conn:
        cur = conn.execute(
            # MT90 — event_key: مفتاح الحدث الدقيق كي يعرف الجرس أيّ صوتٍ
            # يُشغّل. `type` وحده خشن (subscription/system) فلا يُميّز «إضافة
            # مشترك» من «تعديل بيانات»، والعنوان نصٌّ حرّ لا يصلح مفتاحًا.
            "INSERT OR IGNORE INTO panel_notifications("
            " tenant_id, type, severity, title, body, link, dedup_key,"
            " source, source_ref, event_key, subscriber_username, audience,"
            " actor_admin_id, read_at, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'',?)",
            (tenant_id, type or "system", sev, title, body, link,
             dedup_key or "", src, source_ref or "", event_key or "",
             str(subscriber_username or ""), str(audience or ""),
             int(actor_admin_id) if actor_admin_id else None, now),
        )
        if cur.lastrowid and cur.rowcount:
            return int(cur.lastrowid), True
    # كان مكرّرًا (تُجوهِل) — أعِد id الصفّ القائم بنفس المفتاح (ليس جديدًا).
    if dedup_key:
        row = db().execute(
            "SELECT id FROM panel_notifications WHERE tenant_id=? AND dedup_key=?",
            (tenant_id, dedup_key)).fetchone()
        if row:
            return int(row["id"]), False
    return None, False


def create(tenant_id: int, *, type: str = "system", severity: str = "info",
           title: str = "", body: str = "", link: str = "",
           dedup_key: str = "", source: str = "local",
           source_ref: str = "") -> Optional[int]:
    """يُدرج إشعارًا ويُرجع id الجديد أو القائم (عند تكرار المفتاح)، أو None
    عند الفشل. غلاف رفيع على create_returning للتوافق مع المُتّصِلين القائمين."""
    nid, _new = create_returning(
        tenant_id, type=type, severity=severity, title=title, body=body,
        link=link, dedup_key=dedup_key, source=source, source_ref=source_ref)
    return nid


def list_for(tenant_id: int, *, unread_only: bool = False,
             limit: int = 100, offset: int = 0,
             before_id: Optional[int] = None, viewer=None) -> list[dict]:
    """``before_id`` = ترقيم بالمؤشّر (id < before_id): ثابت حين تصل إشعارات
    جديدة أثناء التصفّح — الإزاحة (offset) كانت تُكرّر عناصر في «تحميل المزيد».
    ``viewer`` (fix3) = ما يراه هذا المدير فقط، وحالة قراءته هو."""
    read_sql, read_vals = _read_sql(viewer)
    vis_sql, vis_vals = _visible_sql(tenant_id, viewer)
    sql = ("SELECT panel_notifications.*, " + read_sql + " AS _viewer_read "
           "FROM panel_notifications WHERE tenant_id=?" + vis_sql)
    vals: list = [*read_vals, tenant_id, *vis_vals]
    if unread_only:
        sql += " AND NOT " + read_sql
        vals += read_vals
    if before_id is not None:
        sql += " AND id < ?"
        vals.append(int(before_id))
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    vals += [int(limit), int(offset)]
    return [_row_for(r, viewer) for r in db().execute(sql, vals).fetchall()]


def count_for(tenant_id: int, *, viewer=None) -> int:
    """كل إشعارات المدير (المقروءة وغيرها) — عدّاد «الكل» في المركز."""
    vis_sql, vis_vals = _visible_sql(tenant_id, viewer)
    row = db().execute("SELECT COUNT(*) AS c FROM panel_notifications WHERE tenant_id=?"
                       + vis_sql, [tenant_id, *vis_vals]).fetchone()
    return int(row["c"] if row else 0)


def recent(tenant_id: int, limit: int = 6, *, viewer=None) -> list[dict]:
    """أحدث الإشعارات لقائمة الجرس المنسدلة."""
    return list_for(tenant_id, limit=max(1, int(limit)), viewer=viewer)


def unread_count(tenant_id: int, *, viewer=None) -> int:
    read_sql, read_vals = _read_sql(viewer)
    vis_sql, vis_vals = _visible_sql(tenant_id, viewer)
    row = db().execute(
        "SELECT COUNT(*) AS c FROM panel_notifications WHERE tenant_id=?" + vis_sql
        + " AND NOT " + read_sql,
        [tenant_id, *vis_vals, *read_vals]).fetchone()
    return int(row["c"] if row else 0)


def get(tenant_id: int, notif_id: int, *, viewer=None) -> Optional[dict]:
    """``viewer`` given → None when the notification is not visible to him."""
    read_sql, read_vals = _read_sql(viewer)
    vis_sql, vis_vals = _visible_sql(tenant_id, viewer)
    row = db().execute(
        "SELECT panel_notifications.*, " + read_sql + " AS _viewer_read "
        "FROM panel_notifications WHERE tenant_id=? AND id=?" + vis_sql,
        [*read_vals, tenant_id, int(notif_id), *vis_vals]).fetchone()
    return _row_for(row, viewer) if row else None


def mark_read(tenant_id: int, notif_id: int, *, viewer=None) -> bool:
    """``viewer`` None → tenant-wide read_at (owner). A viewer marks only a
    notification he can see, and only for himself."""
    if viewer is not None:
        if get(tenant_id, notif_id, viewer=viewer) is None:
            return False
        with transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO panel_notification_reads("
                " tenant_id, notification_id, admin_id, read_at) VALUES(?,?,?,?)",
                (tenant_id, int(notif_id), int(viewer["admin_id"]), now_iso()))
            return bool(cur.rowcount)
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE panel_notifications SET read_at=? "
            "WHERE tenant_id=? AND id=? AND read_at=''",
            (now_iso(), tenant_id, int(notif_id)))
        return bool(cur.rowcount)


def mark_all_read(tenant_id: int, *, viewer=None) -> int:
    """``viewer`` None → every unread row of the tenant (owner). A viewer marks
    only what HE can see, for himself (F08 M2)."""
    if viewer is not None:
        read_sql, read_vals = _read_sql(viewer)
        vis_sql, vis_vals = _visible_sql(tenant_id, viewer)
        with transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO panel_notification_reads("
                " tenant_id, notification_id, admin_id, read_at) "
                "SELECT ?, id, ?, ? FROM panel_notifications WHERE tenant_id=?"
                + vis_sql + " AND NOT " + read_sql,
                [tenant_id, int(viewer["admin_id"]), now_iso(), tenant_id,
                 *vis_vals, *read_vals])
            return int(cur.rowcount or 0)
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE panel_notifications SET read_at=? WHERE tenant_id=? AND read_at=''",
            (now_iso(), tenant_id))
        return int(cur.rowcount or 0)


def has_unread_of_type(tenant_id: int, type: str, *,
                       source: Optional[str] = None) -> bool:
    """هل يوجد إشعار غير مقروء بنوعٍ معيّن (واختياريًّا مصدرٍ معيّن)؟
    تستعمله خدمة العدّ التنازلي لتفادي تكرار ما أرسلته لوحة التراخيص."""
    sql = ("SELECT 1 FROM panel_notifications WHERE tenant_id=? AND type=? "
           "AND read_at=''")
    vals: list = [tenant_id, type]
    if source:
        sql += " AND source=?"
        vals.append(source)
    sql += " LIMIT 1"
    return db().execute(sql, vals).fetchone() is not None
