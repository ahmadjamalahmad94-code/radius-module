"""store_chat_reminder_worker — تذكير دوري بمحادثات شات المتجر غير المُجابة.

تذكير زمنيّ (لا حدثيّ): إن بقيت رسالة زبون في شات المتجر بلا **ردّ** ولا حالة
**«مُعالَجة»** أطول من العتبة، نُرسل تنبيه `store_chat_unanswered` للمدير مرّة
(فلا يُنسى زبون). «مُجاب» = إمّا آخر رسالة من المدير (ردّ) أو حالة الخيط
resolved (المدير عالجها بلا ردّ نصّي).

العتبة = إعداد `alerts.store_chat.unanswered_reminder_minutes` (افتراضي 60).
إزالة التكرار: نُخزّن `reminded_at` لكل خيط (store_chat_threads)؛ لا نُذكّر
مجددًا إلا برسالة زبون أحدث من آخر تذكير (دور انتظار جديد) — فلا تكرار كل دقّة.

النمط يتبع loop_probe_poller: `poll_once()` خالص قابل للاختبار + خيط daemon
محصور بـHOBERADIUS_NO_WORKER. الرابط العميق للردّ يُبنى عند ضبط عنوان عام
(HOBERADIUS_PUBLIC_BASE_URL أو إعداد system.public_base_url) إذ يدور خارج طلب.

متغيّرات البيئة:
  HOBERADIUS_STORE_CHAT_REMINDER_INTERVAL_SEC (افتراضي 300، الحد الأدنى 60)
  HOBERADIUS_STORE_CHAT_REMINDER_ENABLED      (افتراضي 1 → مُفعَّل)
  HOBERADIUS_PUBLIC_BASE_URL                  (اختياري — لبناء رابط الردّ)
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta

from .heartbeat import beat

_LOG = logging.getLogger(__name__)
_NAME = "store_chat_reminder_worker"

_started = False
_started_lock = threading.Lock()

_DEFAULT_INTERVAL = 300
_MIN_INTERVAL = 60
_DEFAULT_THRESHOLD_MIN = 60
SK_THRESHOLD = "alerts.store_chat.unanswered_reminder_minutes"

_flask_app = None  # يُحقَن عند البدء لبناء سياق الطلب (للرابط)


def _interval_sec() -> int:
    raw = os.environ.get("HOBERADIUS_STORE_CHAT_REMINDER_INTERVAL_SEC", "")
    try:
        return max(int(raw), _MIN_INTERVAL)
    except ValueError:
        return _DEFAULT_INTERVAL


def _enabled() -> bool:
    raw = (os.environ.get("HOBERADIUS_STORE_CHAT_REMINDER_ENABLED") or "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _all_tenants() -> list[int]:
    from app.radius.db.connection import db
    try:
        return [r["id"] for r in db().execute(
            "SELECT id FROM tenants WHERE status = 'active'").fetchall()]
    except Exception:  # noqa: BLE001
        return [1]


def _threshold_minutes(tenant_id: int) -> int:
    try:
        from app.radius.db.repos import tenants_repo
        raw = tenants_repo.get_setting(int(tenant_id), SK_THRESHOLD,
                                       str(_DEFAULT_THRESHOLD_MIN))
        return max(1, int(str(raw).strip()))
    except Exception:  # noqa: BLE001
        return _DEFAULT_THRESHOLD_MIN


def _parse_iso(ts) -> datetime | None:
    s = str(ts or "").strip().replace("Z", "").replace("T", " ")
    s = s.split(".")[0][:19]
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _humanize_since(delta: timedelta) -> str:
    """فارق زمني → نصّ عربي مختصر «س ساعة وд دقيقة»/«د دقيقة»/«ي يوم»."""
    mins = max(0, int(delta.total_seconds() // 60))
    days, rem = divmod(mins, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days} يوم" + (f" و{hours} ساعة" if hours else "")
    if hours:
        return f"{hours} ساعة" + (f" و{minutes} دقيقة" if minutes else "")
    return f"{minutes} دقيقة"


def _unanswered_threads(tenant_id: int) -> list[tuple[int, str, str]]:
    """خيوط آخر رسالة فيها من الزبون (غير مُجابة بردّ). يُعيد
    [(card_user_id, last_at, name)]. الحالة/التذكير يُفحَصان في poll_once."""
    from app.radius.db.connection import db
    rows = db().execute(
        """
        SELECT t.cu_id AS cu_id, t.last_at AS last_at,
               cu.display_name AS display_name, cu.mobile AS mobile
        FROM (
            SELECT m.card_user_id AS cu_id,
              (SELECT sender FROM store_chat_messages x
                 WHERE x.tenant_id=m.tenant_id AND x.card_user_id=m.card_user_id
                 ORDER BY x.id DESC LIMIT 1) AS last_sender,
              (SELECT created_at FROM store_chat_messages x
                 WHERE x.tenant_id=m.tenant_id AND x.card_user_id=m.card_user_id
                 ORDER BY x.id DESC LIMIT 1) AS last_at
            FROM store_chat_messages m
            WHERE m.tenant_id=?
            GROUP BY m.card_user_id
        ) t
        LEFT JOIN card_users cu ON cu.tenant_id=? AND cu.id=t.cu_id
        WHERE t.last_sender='customer'
        """,
        (int(tenant_id), int(tenant_id)),
    ).fetchall()
    out: list[tuple[int, str, str]] = []
    for r in rows:
        cu = int(r["cu_id"])
        name = str(r["display_name"] or r["mobile"] or f"#{cu}")
        out.append((cu, str(r["last_at"] or ""), name))
    return out


def poll_once(now: datetime | None = None) -> dict:
    """دورة فحص واحدة لكل المستأجرين. خالصة وقابلة للاختبار (مرّر now ثابتًا).
    لا ترفع استثناءً للمُستدعي."""
    from app.radius.services.admin_alerts import dispatch
    from app.radius.services.store_chat import StoreChatService

    now = now or datetime.utcnow()
    stats = {"tenants": 0, "scanned": 0, "reminded": 0}
    for tid in _all_tenants():
        stats["tenants"] += 1
        threshold = _threshold_minutes(tid)
        try:
            threads = _unanswered_threads(tid)
        except Exception:  # noqa: BLE001
            _LOG.exception("store_chat_reminder: scan failed tenant=%s", tid)
            continue
        svc = StoreChatService(tenant_id=tid)
        for cu_id, last_at, name in threads:
            stats["scanned"] += 1
            meta = svc.get_thread_meta(card_user_id=cu_id)
            if (meta.get("status") or "open") == "resolved":
                continue                      # المدير عالجها بلا ردّ → مُجابة
            last_dt = _parse_iso(last_at)
            if not last_dt:
                continue
            if (now - last_dt) < timedelta(minutes=threshold):
                continue                      # لم تتجاوز العتبة بعد
            reminded_dt = _parse_iso(meta.get("reminded_at"))
            if reminded_dt and reminded_dt >= last_dt:
                continue                      # ذُكِّر بالفعل عن هذه الرسالة
            # تأهّل للتذكير — set g.tenant_id ليبني الرابط بمضيف الطلب (إن وُجد).
            try:
                from flask import g, has_request_context
                if has_request_context():
                    g.tenant_id = int(tid)
            except Exception:  # noqa: BLE001
                pass
            dispatch(int(tid), "store_chat_unanswered", {
                "name": name,
                "since": _humanize_since(now - last_dt),
                "card_user_id": int(cu_id),
            }, dedup_key=f"store_chat_unanswered:{int(cu_id)}:{int(last_dt.timestamp())}")
            svc.mark_reminded(card_user_id=cu_id, when=now.isoformat() + "Z")
            stats["reminded"] += 1
    return stats


def _public_base() -> str:
    base = (os.environ.get("HOBERADIUS_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base
    try:
        from app.radius.db.repos import tenants_repo
        return str(tenants_repo.get_setting(1, "system.public_base_url", "")
                   or "").strip().rstrip("/")
    except Exception:  # noqa: BLE001
        return ""


def _run_loop(*, interval_sec: int) -> None:
    _LOG.info("store_chat_reminder_worker started — interval=%ds", interval_sec)
    while True:
        stats = {}
        try:
            base = _public_base()
            if base and _flask_app is not None:
                # سياق طلب بعنوان عام → يُبنى رابط الردّ المطلق في التنبيه.
                with _flask_app.test_request_context("/", base_url=base):
                    stats = poll_once()
            else:
                stats = poll_once()
        except Exception:  # noqa: BLE001
            _LOG.exception("store_chat_reminder tick failed")
        beat(_NAME, info={"interval_sec": interval_sec,
                          "last_reminded": stats.get("reminded", 0)})
        time.sleep(interval_sec)


def start_store_chat_reminder_worker(flask_app=None) -> None:
    global _started, _flask_app
    with _started_lock:
        if _started:
            return
        if not _enabled():
            _LOG.info("store_chat_reminder_worker disabled by env")
            return
        _flask_app = flask_app
        t = threading.Thread(
            target=_run_loop, kwargs={"interval_sec": _interval_sec()},
            daemon=True, name="hr-store-chat-reminder")
        t.start()
        _started = True


__all__ = ["poll_once", "start_store_chat_reminder_worker"]
