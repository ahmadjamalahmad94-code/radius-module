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

# MT91 — وضع الصوت. المزوّد يُوفّر أصوات الكلام مركزيًّا، ومالك الريديوس قد لا
# يُحبّها فيرجع للنغمة القديمة. القرار على الخادم لا في المتصفّح: هو قرار مالك
# اللوحة لا تفضيلُ جهاز، فيسري على كلّ مدير وكلّ متصفّح دفعةً واحدة.
#
# وأناقة التنفيذ أنّ نقطة الصوت وحدها تُنفّذه: في وضع «النغمة» تردّ 404 —
# وهي أصلًا إشارة «شغّل النغمة» التي يفهمها الجرس. فلا سطر جافاسكربت واحد
# يحتاج تعديلًا، ولا فرعٌ جديد يُختبَر في المتصفّح.
MODE_KEY = "notif.sound_mode"
MODE_VOICE = "voice"       # الأصوات المسجَّلة (الافتراضيّ)
MODE_TONE = "tone"         # النغمة المولَّدة القديمة، وتُتجاهَل كلّ الأصوات


def get_mode(tenant_id: int) -> str:
    try:
        from ..db.repos import tenants_repo
        val = str(tenants_repo.get_setting(tenant_id, MODE_KEY, MODE_VOICE) or "")
    except Exception:  # noqa: BLE001 — تعذّر القراءة ⇒ الافتراضيّ
        return MODE_VOICE
    return MODE_TONE if val.strip().lower() == MODE_TONE else MODE_VOICE


def set_mode(tenant_id: int, mode: str, *, by: int = 0) -> str:
    value = MODE_TONE if str(mode or "").strip().lower() == MODE_TONE else MODE_VOICE
    try:
        from ..db.repos import tenants_repo
        tenants_repo.set_setting(tenant_id, MODE_KEY, value, by=by)
    except Exception:  # noqa: BLE001
        _LOG.exception("set_mode failed")
    return value

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
# MT97 — «راوتر» و«جهاز شبكة» شيئان مختلفان يُراقبهما نظامان مختلفان،
# وكانا يتشاركان صوتًا واحدًا. التسميات هنا تقول بالضبط أيّهما يُطلقه، كي
# لا يحتار المشغّل أمام بندين متشابهين.
#
# `router_offline` مُعرَّفٌ في سجلّ التنبيهات نفسه، فلا يُكرَّر هنا —
# تكراره كان يُظهر بندين متشابهين في الصفحة («انقطاع راوتر» و«راوتر غير
# متصل») لا يُميّزهما أحد.
_EXTRA_EVENTS: tuple[SoundEvent, ...] = (
    SoundEvent("router_up", "راوتر: عاد للاتصال", "network"),
    SoundEvent("device_down", "جهاز شبكة: انقطع (أكسس بوينت وغيره)", "network",
               "من صفحة «تتبّع الأجهزة» — منطقةٌ واحدة تتأثّر"),
    SoundEvent("device_up", "جهاز شبكة: عاد للاتصال", "network"),
    SoundEvent("device_unavailable", "جهاز شبكة: غير متاح لأنّ راوتره ساقط",
               "network", "ليس عطب الجهاز — أصلِح الراوتر أوّلًا"),
    SoundEvent("network_high_latency", "جهاز شبكة: ارتفاع البنج", "network"),
    SoundEvent("health_digest_ok", "الفحص الدوريّ انتهى — كلّ الأجهزة سليمة",
               "system", "طمأنةٌ دوريّة لا إجراء فيها — يُنصح بصوتٍ هادئ"),
    SoundEvent("health_digest_issues", "الفحص الدوريّ انتهى — توجد أجهزة بها مشاكل",
               "system", "هذا وحده يستوجب أن تفتح اللوحة — ميّزه"),

    # ── موارد المايكروتيك (MT99) ──
    # كلّ بندٍ يقول: ما الذي تجاوز، وما أثره العمليّ. العتبات في:
    # الإعدادات ← مراقبة الموارد.
    SoundEvent("res_cpu_high", "المعالج مرتفع (فوق العتبة، افتراضيًّا ٨٥٪)",
               "resources", "الراوتر يبطؤ: تأخّر المصادقة وتقطّع الإدارة"),
    SoundEvent("res_ram_high", "الذاكرة ممتلئة (فوق العتبة، افتراضيًّا ٩٠٪)",
               "resources", "خطر إعادة تشغيلٍ ذاتيّة أو سقوط خدمات"),
    SoundEvent("res_temp_high", "الحرارة مرتفعة (فوق العتبة، افتراضيًّا ٧٠°)",
               "resources", "تهويةٌ سيّئة — يسبق العطب المادّيّ"),
    SoundEvent("res_disk_high", "مساحة القرص شحّت (تحت العتبة، افتراضيًّا ١٠٪)",
               "resources", "يمنع النسخ الاحتياطيّ وتسجيل السجلّات"),
    SoundEvent("res_traffic_high", "حركة المرور تجاوزت العتبة المضبوطة",
               "resources", "تُضبط يدويًّا؛ ٠ = مُعطّلة"),
    SoundEvent("res_recovered", "مورد عاد تحت العتبة (تعافى)",
               "resources", "انتهاء الحالة — طمأنةٌ لا إنذار"),
)


# MT97.1 — تسمياتٌ صريحة لمجموعة الشبكة. سجلّ التنبيهات يقول «راوتر» و«جهاز»
# وهما لا يُميّزان شيئًا للمشغّل: عنده **مايكروتيك** واحد يُشغّل الريديوس،
# وتحته **أجهزة توزيع** يتتبّعها من صفحة «تتبّع الأجهزة». الفرق عمليّ: سقوط
# المايكروتيك يعني انقطاع الشبكة كلّها، وسقوط جهاز توزيع يعني منطقةً واحدة.
# التسمية تقول **ما الذي حدث وما أثره**، لا اسم الحدث فقط. المشغّل يقرأ
# السطر مرّةً ويقرّر أيّ صوتٍ يستحقّ — فالغموض هنا يكلّفه وقتًا كلّ مرّة.
_LABEL_OVERRIDES = {
    # ── المايكروتيك: الراوتر الذي يُشغّل الريديوس ──
    "router_offline": "🔴 المايكروتيك لا يردّ — تنقطع الشبكة كلّها",
    "router_up": "المايكروتيك عاد للاتصال",
    "mikrotik_connection_problem": "اللوحة لا تصل للمايكروتيك (API مقفلة أو كلمة مرور خاطئة)",
    "router_high_traffic": "المايكروتيك: ذروة السرعة تجاوزت الحدّ — الخطّ ممتلئ الآن",
    "router_high_usage": "المايكروتيك: الداتا المستهلكة تجاوزت الحدّ خلال المدّة",
    # ── أجهزة التوزيع: ما تتتبّعه من صفحة «تتبّع الأجهزة» ──
    "device_down": "جهاز توزيع لا يردّ — منطقةٌ واحدة تنقطع",
    "device_up": "جهاز توزيع عاد للاتصال",
    "device_unavailable": "جهاز توزيع غير مفحوص لأنّ المايكروتيك ساقط",
    "network_high_latency": "جهاز توزيع: بنج مرتفع — بطءٌ لا انقطاع",
    "device_health": "تتبّع الأجهزة: تقرير عامّ عن حالة الأجهزة",
    # ── المشتركون: تسمياتٌ من السجلّ كانت مبهمة ──
    "loan_granted": "منح مشترك سلفة وقت (رصيدٌ مؤقّت قبل الدفع)",
    # ── الشبكة ──
    "network_disconnect": "الشبكة: فصل جهاز أو بنج سيّئ",
    "loop_detected": "لوب في الشبكة (حلقة تُغرق المُبدّل)",
}


# MT100 — أحداثٌ مُعرَّفةٌ في السجلّ **بلا مُطلِقٍ في الكود**. جردٌ آليّ
# (بحثٌ عن كلّ استدعاءٍ لكلّ مفتاح، مستثنيًا ملفّات التعريف والاختبارات،
# مع تتبّع الاستدعاءات غير المباشرة عبر متغيّر) أعطى ٤٤ حيًّا من ٥٢.
#
# إظهارها يَعِد بما لا يتحقّق: يرفع المشغّل تسجيلًا وينتظر فلا يُسمَع شيءٌ
# أبدًا، فيظنّ نظام الأصوات معطوبًا. إخفاؤها أصدق من عرضها.
#
#   card_batch_low · backup_stale · audit_failure · service_request_approved
#   mikrotik_connection_problem · network_disconnect
#       ← لا مُطلِق إطلاقًا؛ تعريفٌ بقالبٍ جميل ولا شيء يستدعيه.
#
#   router_high_traffic · router_high_usage
#       ← الكشف **يعمل** في `smart_alerts` لكنّه يكتب في «مركز التنبيهات»
#         (alerts_repo) لا في جرس الإشعارات، فلا يمرّ بنظام الأصوات أصلًا.
#         وصلُها بالجرس قرارٌ منفصل (تُصبح إشعارًا لا تنبيهًا).
#
# لإحياء أيّها: استدعِ `admin_alerts.dispatch(tid, "<key>", {...})` من موضع
# الكشف، ثمّ احذفه من هنا — يعود للصفحة تلقائيًّا.
_NO_TRIGGER = frozenset({
    "card_batch_low", "backup_stale", "audit_failure",
    "service_request_approved", "mikrotik_connection_problem",
    "network_disconnect", "router_high_traffic", "router_high_usage",
})


def _build_events() -> tuple[SoundEvent, ...]:
    """السجلّ أوّلًا ثمّ إضافات مراقبة الأجهزة، بلا تكرار وبترتيبٍ ثابت."""
    out: list[SoundEvent] = []
    seen: set[str] = set()
    try:
        from .admin_alerts import ALERTS
        for spec in ALERTS:
            key = str(getattr(spec, "key", "") or "")
            if not key or key in seen or key in _NO_TRIGGER:
                continue
            seen.add(key)
            out.append(SoundEvent(
                key=key,
                label=_LABEL_OVERRIDES.get(
                    key, str(getattr(spec, "label", "") or key)),
                group=str(getattr(spec, "group", "") or "system"),
            ))
    except Exception:  # noqa: BLE001 — الصفحة تعمل ولو تعذّر السجلّ
        _LOG.exception("sound catalog: ALERTS unavailable")
    for ev in _EXTRA_EVENTS:
        if ev.key not in seen:
            seen.add(ev.key)
            out.append(SoundEvent(
                key=ev.key,
                label=_LABEL_OVERRIDES.get(ev.key, ev.label),
                group=ev.group, hint=ev.hint))
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
    "resources": "موارد المايكروتيك (معالج · ذاكرة · حرارة · قرص)",
    "system": "النظام",
    "billing": "الفوترة",
    # فرع TR-069 يُضيف مجموعة `routers` (أجهزة الزبائن عبر GenieACS).
    # الكتالوج مُشتقٌّ من السجلّ الحيّ، فمجموعةٌ بلا تسمية هنا تظهر في
    # الصفحة بمفتاحها الإنجليزيّ — وهو ما أمسكه الاختبار عند الدمج.
    "routers": "أجهزة الزبائن (TR-069)",
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
               origin: str = "local", checksum: str = "") -> tuple[bool, str]:
    """يحفظ صوتًا لمفتاح. يُرجع (نجح، رسالة عربيّة). لا يرمي أبدًا.

    ``checksum`` — بصمة المصدر حين يأتي الصوت من اللوحة المركزيّة. تُخزَّن
    كما هي لا كما نحسبها: المستودعان منفصلان، ولو اختلفت خوارزميّة البصمة
    يومًا لظلّ السحب يُنزّل الملفّ نفسه كلّ ساعة إلى الأبد. فارغةٌ ⇒ نحسبها
    (مسار الرفع المحلّيّ، حيث لا مصدر خارجيًّا يُقارَن به).
    """
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
    csum = (checksum or "").strip() or _checksum(raw)
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
    # وضع «النغمة»: لا صوت مهما كان المرفوع — قرار مالك اللوحة يعلو الكلّ.
    if get_mode(tenant_id) == MODE_TONE:
        return None
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
