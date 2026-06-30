"""nas_liveness — إشارة «قابليّة الوصول الحيّة» لكلّ راوتر (NAS).

سياسة المالك: الحالة الحيّة للراوتر هي **المصدر الوحيد** لـ«المتصلون الآن».
  • الراوتر غير قابل للوصول (لا يمكن تحديث البيانات) → لا بيانات: تُعرَض اللوحة
    فارغة «الراوتر غير متصل» — لا تُعرَض جلسات RADIUS مفتوحة لا يمكن التحقّق منها.
  • عند عودة الراوتر → نُحدّث المجموعة الحيّة فورًا ونُصالح radacct.

هذه الوحدة سجلّ خفيف **في الذاكرة** (كنمط heartbeat) يُحدّثه المُستطلِع الخلفيّ
(mt_reconciler) كلّ دورة، وعند الطلب الفوريّ من صفحة /online. لا يَلمس DB ولا
الشبكة بنفسه — فالعدّاد/الواجهة يقرآنه دون تعليق (fail-fast).

ثلاث حالات لكلّ NAS:
  • REACHABLE   = آخر استطلاع ناجح ضمن نافذة الحداثة (الأحدث نجاح).
  • UNREACHABLE = آخر استطلاع فشل، أو نجاحٌ قديمٌ تجاوز النافذة (بائت = لا تحقّق).
  • UNKNOWN     = لم يُستطلَع قطّ (لا بيانات) — تُترَك للاحتياط (radacct) في طبقة
                  العرض كي لا ينكسر سلوك النشرات التي لا تُشغّل المُستطلِع.

كلّ القيم لكلّ مستأجر+IP الراوتر (المفتاح = IP الذي يُستطلَع به = nas host).
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

_lock = threading.Lock()
# (tenant_id, nas_ip) -> {"last_ok": float|None, "last_fail": float|None, "active": int}
_state: dict[tuple[int, str], dict] = {}

# نافذة اعتبار آخر نجاح «حيًّا» (ثوانٍ). 90 = 3× دورة mt_reconciler (30s) —
# نجاحٌ أقدم من ذلك = لا نتحقّق منه الآن → غير متصل.
_DEFAULT_WINDOW_SEC = 90


def window_sec() -> int:
    raw = (os.environ.get("HOBERADIUS_NAS_LIVENESS_WINDOW_SEC") or "").strip()
    try:
        v = int(raw)
        return v if v > 0 else _DEFAULT_WINDOW_SEC
    except ValueError:
        return _DEFAULT_WINDOW_SEC


def _key(tenant_id: int, nas_ip: str) -> tuple[int, str]:
    return (int(tenant_id), str(nas_ip or "").strip())


def _new_entry() -> dict:
    return {"last_ok": None, "last_fail": None, "active": 0, "last_event": None}


def record_reachable(tenant_id: int, nas_ip: str, *, active_count: int = 0,
                     now: Optional[float] = None) -> None:
    """يُسجّل استطلاعًا ناجحًا للراوتر مع عدد الجلسات الحيّة المرئيّة عليه."""
    if not str(nas_ip or "").strip():
        return
    ts = now if now is not None else time.time()
    with _lock:
        e = _state.setdefault(_key(tenant_id, nas_ip), _new_entry())
        e["last_ok"] = ts
        e["last_event"] = "ok"
        e["active"] = max(0, int(active_count or 0))


def record_unreachable(tenant_id: int, nas_ip: str, *,
                       now: Optional[float] = None) -> None:
    """يُسجّل فشل استطلاع الراوتر (غير قابل للوصول) — يُصفّر عدّه الحيّ."""
    if not str(nas_ip or "").strip():
        return
    ts = now if now is not None else time.time()
    with _lock:
        e = _state.setdefault(_key(tenant_id, nas_ip), _new_entry())
        e["last_fail"] = ts
        e["last_event"] = "fail"
        e["active"] = 0


def _classify(entry: Optional[dict], *, now: float, win: int) -> Optional[bool]:
    """True=reachable، False=unreachable، None=unknown (لم يُستطلَع قطّ).

    نُصنّف بـ«آخر حدث» صراحةً (last_event) لا بمقارنة الطوابع — فطابعا نجاح/فشل
    متطابقان (نفس اللحظة) لا يُسبّبان غموضًا: آخر استدعاء يَغلب. النجاح البائت
    (أقدم من النافذة) = لا يمكن التحقّق الآن → غير متصل."""
    if not entry:
        return None
    ev = entry.get("last_event")
    if ev == "fail":
        return False
    if ev == "ok":
        ok = entry.get("last_ok")
        if ok is not None and (now - ok) <= win:
            return True
        return False  # نجاح بائت تجاوز النافذة → غير متصل
    return None


def is_reachable(tenant_id: int, nas_ip: str, *,
                 now: Optional[float] = None) -> Optional[bool]:
    n = now if now is not None else time.time()
    win = window_sec()
    with _lock:
        return _classify(_state.get(_key(tenant_id, nas_ip)), now=n, win=win)


def active_for(tenant_id: int, nas_ip: str, *,
               now: Optional[float] = None) -> int:
    """عدد الجلسات الحيّة على هذا الراوتر إن كان قابلاً للوصول، وإلّا 0."""
    n = now if now is not None else time.time()
    win = window_sec()
    with _lock:
        e = _state.get(_key(tenant_id, nas_ip))
        if _classify(e, now=n, win=win) is True:
            return int((e or {}).get("active") or 0)
    return 0


def has_data(tenant_id: int) -> bool:
    """هل يوجد أيّ سجلّ liveness لهذا المستأجر؟ (تمييز «المُستطلِع يعمل» عن
    «لا بيانات» — في الأخيرة تَرتدّ طبقة العرض إلى radacct)."""
    tid = int(tenant_id)
    with _lock:
        return any(k[0] == tid for k in _state)


def snapshot(tenant_id: int, *, now: Optional[float] = None) -> dict[str, dict]:
    """خريطة nas_ip → {reachable, active, age_sec} لكلّ ما اسُتطلِع لهذا المستأجر."""
    n = now if now is not None else time.time()
    win = window_sec()
    tid = int(tenant_id)
    out: dict[str, dict] = {}
    with _lock:
        for (t, ip), e in _state.items():
            if t != tid:
                continue
            reachable = _classify(e, now=n, win=win)
            last = e.get("last_ok") or e.get("last_fail")
            out[ip] = {
                "reachable": reachable,
                "active": int(e.get("active") or 0) if reachable else 0,
                "age_sec": round(n - last, 1) if last else None,
            }
    return out


def live_connected_count(tenant_id: int, *, now: Optional[float] = None) -> int:
    """إجمالي الجلسات الحيّة على الراوترات القابلة للوصول فقط (المصدر الحيّ).
    الراوترات غير القابلة للوصول/المجهولة تُسهم بصفر."""
    n = now if now is not None else time.time()
    win = window_sec()
    tid = int(tenant_id)
    total = 0
    with _lock:
        for (t, ip), e in _state.items():
            if t != tid:
                continue
            if _classify(e, now=n, win=win) is True:
                total += int(e.get("active") or 0)
    return total


def reset() -> None:
    """خطّاف اختبار — يمسح السجلّ بين الحالات."""
    with _lock:
        _state.clear()


__all__ = [
    "window_sec", "record_reachable", "record_unreachable",
    "is_reachable", "active_for", "has_data", "snapshot",
    "live_connected_count", "reset",
]
