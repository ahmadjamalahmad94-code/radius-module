"""MT90 — أصوات الإشعارات المخصّصة: كتالوجٌ + تخزينٌ + حلٌّ تسلسليّ.

المالك يريد صوتًا مسجَّلًا (كلامًا) بدل النغمة، **ولكلّ حدثٍ صوتَه**: «مشترك
جديد» غير «راوتر غير متصل» غير «عاد الراوتر». ومصدر الأصوات مركزيّ: تُرفع
مرّةً في اللوحة وتُسحب لكلّ النسخ تلقائيًّا، مع إبقاء رفعٍ محلّيّ يتقدّم عليها.

الحلّ تسلسليّ من الأدقّ إلى الأعمّ — وهذا ما يجعل النظام مفيدًا من أوّل يوم
حتى قبل أن تُرفَع أصواتٌ لكلّ حدث:

    مفتاح الحدث  →  type:<النوع>  →  __global__  →  النغمة المولَّدة

وفي كلّ درجة: المحلّيّ يتقدّم على المركزيّ. أي أنّ صوتًا مركزيًّا لحدثٍ بعينه
يهزم صوتًا محلّيًّا عامًّا (الأدقّ يفوز)، لكنّ محلّيًّا للحدث نفسه يهزم المركزيّ.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Optional

from ..db.connection import db, transaction
from ..db.helpers import now_iso

_LOG = logging.getLogger(__name__)

GLOBAL_KEY = "__global__"
MAX_BYTES = 3 * 1024 * 1024          # ٣ ميغابايت — تسجيلٌ صوتيّ قصير لا أغنية
_ALLOWED_MIME_PREFIX = "audio/"
_OCTET = "application/octet-stream"   # بعض المتصفّحات تُرسل التسجيل هكذا


@dataclass(frozen=True)
class SoundEvent:
    """حدثٌ يُسمَع في اللوحة. `key` هو ما يُخزَّن على الإشعار ويُطلَب به الصوت."""

    key: str
    label: str
    group: str
    hint: str = ""


# ── الكتالوج ──────────────────────────────────────────────────────────
#
# مُشتقٌّ من سجلّ `admin_alerts.ALERTS` (٣٥ حدثًا) لا مكتوبٌ يدويًّا: قائمةٌ
# يدويّة تنحرف عن الواقع مع أوّل حدثٍ يُضاف، فتَظهر في الصفحة أحداثٌ لا
# تُطلَق أبدًا وتغيب أحداثٌ تُطلَق فلا تجد لها صوتًا. المصدر واحد.
#
# ويُضاف إليه ما تُطلقه مراقبة الأجهزة وحدها (عودة الراوتر، الفحص الدوريّ)
# لأنّها تكتب الجرس مباشرةً لا عبر سجلّ التنبيهات.
_EXTRA_EVENTS: tuple[SoundEvent, ...] = (
    SoundEvent("router_down", "انقطاع راوتر", "network",
               "يُنصح بصوتٍ مميّز — هذا ما يوقظك"),
    SoundEvent("router_up", "عودة راوتر للاتصال", "network"),
    SoundEvent("network_high_latency", "ارتفاع زمن الاستجابة", "network"),
    SoundEvent("system_health", "الفحص الدوريّ للأجهزة", "system"),
)


def _build_events() -> tuple[SoundEvent, ...]:
    """السجلّ أوّلًا ثمّ إضافات مراقبة الأجهزة، بلا تكرار وبترتيبٍ ثابت."""
    out: list[SoundEvent] = []
    seen: set[str] = set()
    try:
        from .admin_alerts import ALERTS
        for spec in ALERTS:
            key = str(getattr(spec, "key", "") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(SoundEvent(
                key=key,
                label=str(getattr(spec, "label", "") or key),
                group=str(getattr(spec, "group", "") or "system"),
            ))
    except Exception:  # noqa: BLE001 — الصفحة تعمل ولو تعذّر السجلّ
        _LOG.exception("sound catalog: ALERTS unavailable")
    for ev in _EXTRA_EVENTS:
        if ev.key not in seen:
            seen.add(ev.key)
            out.append(ev)
    return tuple(out)


_EVENTS: tuple[SoundEvent, ...] = _build_events()

EVENTS: dict[str, SoundEvent] = {e.key: e for e in _EVENTS}
EVENT_KEYS: tuple[str, ...] = tuple(EVENTS.keys())

GROUP_LABELS = {
    "subscribers": "المشتركون",
    "network": "الشبكة والأجهزة",
    "finance": "المال والتحصيل",
    "store": "المتجر",
    "security": "الأمان",
    "system": "النظام",
    "billing": "الفوترة",
}

# الأنواع الخشنة الموجودة في panel_notifications.type — كارتدادٍ وسيط بين
# مفتاح الحدث والصوت العامّ، فالإشعارات القديمة (بلا مفتاح) تُسمَع أيضًا.
TYPE_KEYS = ("subscription", "system", "service", "billing", "support", "license")


def type_sound_key(ntype: str) -> str:
    return f"type:{(ntype or 'system').strip().lower()}"


def is_valid_key(key: str) -> bool:
    k = (key or "").strip()
    return bool(k) and (k == GLOBAL_KEY or k in EVENTS
                        or k.startswith("type:"))


# ── التخزين ───────────────────────────────────────────────────────────

# MT90.1 — قاعدة SQLite واحدة يكتبها ثلاثة (اللوحة + FreeRADIUS + العمّال)،
# فكتابةٌ عابرة قد تصطدم بـ`database is locked`. ظهر فعلًا في أوّل رفعٍ على
# خادم الإنتاج. نفس علاج مزامنة الكروت (MT82): إعادةٌ قصيرة ثمّ رسالةٌ صريحة
# — لا فشلٌ صامت ولا استثناءٌ يصعد للواجهة.
_WRITE_RETRIES = 4
_WRITE_BACKOFF = (0.15, 0.4, 0.9)


def _write_with_retry(fn):
    """ينفّذ كتابةً مع إعادةٍ عند قفل القاعدة. يُرجع (نجح، رسالةٌ عند الفشل)."""
    import time
    last = ""
    for attempt in range(_WRITE_RETRIES):
        try:
            fn()
            return True, ""
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
            if "locked" in last.lower() and attempt < _WRITE_RETRIES - 1:
                time.sleep(_WRITE_BACKOFF[attempt])
                continue
            break
    _LOG.warning("notification sound write failed: %s", last)
    if "locked" in last.lower():
        return False, "القاعدة مشغولة الآن — أعد المحاولة بعد لحظات."
    return False, "تعذّر الحفظ."


def _checksum(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:32]


def save_sound(tenant_id: int, sound_key: str, raw: bytes, *,
               mime: str = "audio/mpeg", filename: str = "",
               origin: str = "local") -> tuple[bool, str]:
    """يحفظ صوتًا لمفتاح. يُرجع (نجح، رسالة عربيّة). لا يرمي أبدًا."""
    key = (sound_key or "").strip()
    if not is_valid_key(key):
        return False, "مفتاح إشعارٍ غير معروف."
    if not raw:
        return False, "الملفّ فارغ."
    if len(raw) > MAX_BYTES:
        return False, f"الملفّ كبير جدًّا (الحدّ {MAX_BYTES // (1024 * 1024)} ميغابايت)."
    m = (mime or "").strip() or "audio/mpeg"
    if not m.startswith(_ALLOWED_MIME_PREFIX) and m != _OCTET:
        return False, "الملفّ ليس صوتيًّا."
    if origin not in ("local", "central"):
        origin = "local"
    b64 = base64.b64encode(raw).decode("ascii")
    csum = _checksum(raw)
    outcome: dict = {}

    def _do():
        with transaction() as cn:
            cur = cn.execute(
                "SELECT id, origin, checksum FROM notification_sounds "
                "WHERE tenant_id=? AND sound_key=?", (tenant_id, key)).fetchone()
            if cur is not None:
                # السحب المركزيّ لا يدوس رفعًا محلّيًّا — قرار العميل يفوز.
                if origin == "central" and (cur["origin"] or "") == "local":
                    outcome["done"] = (False, "يوجد صوتٌ محلّيّ لهذا الحدث — "
                                              "المركزيّ لا يستبدله.")
                    return
                if (cur["checksum"] or "") == csum and (cur["origin"] or "") == origin:
                    outcome["done"] = (True, "الصوت نفسه — لا تغيير.")
                    return
                cn.execute(
                    "UPDATE notification_sounds SET mime=?, filename=?, data_b64=?, "
                    "origin=?, checksum=?, updated_at=? WHERE id=?",
                    (m, (filename or "")[:120], b64, origin, csum, now_iso(),
                     int(cur["id"])))
            else:
                cn.execute(
                    "INSERT INTO notification_sounds (tenant_id, sound_key, mime, "
                    "filename, data_b64, origin, checksum, updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (tenant_id, key, m, (filename or "")[:120], b64, origin,
                     csum, now_iso()))
        outcome["done"] = (True, "حُفظ الصوت.")

    ok, err = _write_with_retry(_do)
    if not ok:
        return False, err
    return outcome.get("done", (True, "حُفظ الصوت."))


def clear_sound(tenant_id: int, sound_key: str) -> tuple[bool, str]:
    key = (sound_key or "").strip()
    if not is_valid_key(key):
        return False, "مفتاح إشعارٍ غير معروف."
    def _do():
        with transaction() as cn:
            cn.execute("DELETE FROM notification_sounds WHERE tenant_id=? "
                       "AND sound_key=?", (tenant_id, key))

    ok, err = _write_with_retry(_do)
    if not ok:
        return False, err
    return True, "أُعيدت النغمة الافتراضيّة لهذا الحدث."


def _row(tenant_id: int, key: str) -> Optional[dict]:
    try:
        r = db().execute(
            "SELECT mime, data_b64 FROM notification_sounds "
            "WHERE tenant_id=? AND sound_key=? AND data_b64<>''",
            (tenant_id, key)).fetchone()
        return dict(r) if r else None
    except Exception:  # noqa: BLE001
        return None


def resolve(tenant_id: int, event_key: str = "",
            ntype: str = "") -> Optional[tuple[str, bytes]]:
    """(mime, bytes) للصوت الأنسب، أو None فتُشغَّل النغمة المولَّدة.

    الترتيب هو جوهر الفائدة: صوتٌ واحد عامّ يجعل كلّ الإشعارات مسموعةً فورًا،
    ثمّ يُخصّص المالك ما يشاء حدثًا حدثًا دون أن يُترك الباقي صامتًا.
    """
    for key in (event_key or "", type_sound_key(ntype) if ntype else "", GLOBAL_KEY):
        if not key:
            continue
        row = _row(tenant_id, key)
        if row:
            try:
                return (row["mime"] or "audio/mpeg"),\
                    base64.b64decode(row["data_b64"])
            except Exception:  # noqa: BLE001 — صفٌّ تالف لا يُسكت البقيّة
                _LOG.warning("corrupt sound row for %s", key)
                continue
    return None


def status_map(tenant_id: int) -> dict[str, dict[str, Any]]:
    """{sound_key: {origin, filename, updated_at, bytes}} لكلّ ما هو مخزَّن."""
    out: dict[str, dict[str, Any]] = {}
    try:
        rows = db().execute(
            "SELECT sound_key, origin, filename, updated_at, "
            "LENGTH(data_b64) AS blen FROM notification_sounds "
            "WHERE tenant_id=? AND data_b64<>''", (tenant_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        out[r["sound_key"]] = {
            "origin": r["origin"] or "local",
            "filename": r["filename"] or "",
            "updated_at": r["updated_at"] or "",
            # base64 يتضخّم ٤/٣ — نُرجع الحجم الحقيقيّ تقريبًا لا المخزَّن
            "bytes": int((r["blen"] or 0) * 3 / 4),
        }
    return out


def any_sound_configured(tenant_id: int) -> bool:
    """يقرأه القالب: بلا أيّ صوت لا داعي لأن يطلب JS شيئًا من الخادم."""
    try:
        r = db().execute(
            "SELECT 1 FROM notification_sounds WHERE tenant_id=? AND data_b64<>'' "
            "LIMIT 1", (tenant_id,)).fetchone()
        return r is not None
    except Exception:  # noqa: BLE001
        return False


def catalog(tenant_id: int) -> list[dict[str, Any]]:
    """الكتالوج مُجمَّعًا للعرض، وبحالة كلّ حدث."""
    have = status_map(tenant_id)
    groups: dict[str, dict[str, Any]] = {}
    for ev in _EVENTS:
        g = groups.setdefault(ev.group, {
            "key": ev.group,
            "label": GROUP_LABELS.get(ev.group, ev.group),
            "events": [],
        })
        g["events"].append({
            "key": ev.key, "label": ev.label, "hint": ev.hint,
            "sound": have.get(ev.key),
        })
    return list(groups.values())
