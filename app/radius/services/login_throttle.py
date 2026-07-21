"""كبح تخمين كلمات المرور — MT37.

تأخير تصاعديّ على مستوى **عنوان الشبكة** لا الحساب: بعد ``MAX_FAILS``
إخفاقات يُحجب العنوان ``BASE_BLOCK_MIN`` دقيقة، وتتضاعف المدّة مع كل
جولة حجب حتى ``MAX_BLOCK_MIN``. لم نَقفل الحساب باسمه عمدًا — مهاجمٌ
يعرف اسم المالك يستطيع عندها حبسه خارج لوحته متى شاء.

العدّاد في القاعدة لا في الذاكرة كي يَصمد عبر إعادة تشغيل الخدمة؛
عدّادٌ في الذاكرة يُصفّره المهاجم بإسقاط العملية.

مبدأ التصميم: **لا يُسقط الدخول أبدًا**. أيّ خطأ في القراءة أو الكتابة
يُبتلع ويُعامَل كـ«غير محجوب» — فقفلُ المالك خارج لوحته بسبب عطلٍ في
جدولٍ مساعد أسوأ من تفويت كبحٍ في حالةٍ نادرة.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..db.connection import db, transaction
from ..db.helpers import now_iso

MAX_FAILS = 5           # إخفاقات متتالية قبل أوّل حجب
BASE_BLOCK_MIN = 15     # مدّة الحجب الأولى
MAX_BLOCK_MIN = 240     # سقف المدّة (٤ ساعات)
FAIL_WINDOW_MIN = 60    # إخفاقات أقدم من هذا تُنسى


def _parse(ts: str) -> datetime | None:
    try:
        return datetime.fromisoformat((ts or "").replace("Z", ""))
    except Exception:  # noqa: BLE001
        return None


def scope_for(ip: str) -> str:
    return "ip:" + (ip or "unknown").strip()[:60]


def _row(scope: str) -> dict[str, Any] | None:
    r = db().execute("SELECT * FROM login_throttle WHERE scope = ?", (scope,)).fetchone()
    return dict(r) if r else None


def blocked_seconds(scope: str) -> int:
    """كم ثانية تبقّت من الحجب (0 = غير محجوب). لا ترفع أبدًا."""
    try:
        row = _row(scope)
        if not row:
            return 0
        until = _parse(row.get("blocked_until") or "")
        if not until:
            return 0
        left = (until - datetime.utcnow()).total_seconds()
        return int(left) if left > 0 else 0
    except Exception:  # noqa: BLE001 — كبحٌ معطّل خيرٌ من دخولٍ مُعطَّل
        return 0


def record_failure(scope: str) -> int:
    """يُسجّل إخفاقًا ويُعيد ثواني الحجب إن بلغ الحدّ (وإلّا 0)."""
    try:
        now = datetime.utcnow()
        with transaction() as conn:
            r = conn.execute("SELECT * FROM login_throttle WHERE scope = ?",
                             (scope,)).fetchone()
            row = dict(r) if r else None

            fails = int(row.get("fail_count") or 0) if row else 0
            level = int(row.get("block_level") or 0) if row else 0

            # إخفاقٌ قديم جدًّا = جولة جديدة
            last = _parse(row.get("last_fail_at") or "") if row else None
            if last and (now - last) > timedelta(minutes=FAIL_WINDOW_MIN):
                fails = 0

            fails += 1
            blocked_until = ""
            wait = 0
            if fails >= MAX_FAILS:
                minutes = min(BASE_BLOCK_MIN * (2 ** level), MAX_BLOCK_MIN)
                until = now + timedelta(minutes=minutes)
                blocked_until = until.isoformat(timespec="seconds")
                wait = int(minutes * 60)
                fails = 0        # تُستأنف العدّة بعد فكّ الحجب
                level += 1

            conn.execute(
                """INSERT INTO login_throttle
                     (scope, fail_count, block_level, blocked_until, last_fail_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(scope) DO UPDATE SET
                     fail_count = excluded.fail_count,
                     block_level = excluded.block_level,
                     blocked_until = CASE WHEN excluded.blocked_until <> ''
                                          THEN excluded.blocked_until
                                          ELSE login_throttle.blocked_until END,
                     last_fail_at = excluded.last_fail_at""",
                (scope, fails, level, blocked_until, now_iso()),
            )
            return wait
    except Exception:  # noqa: BLE001
        return 0


def clear(scope: str) -> None:
    """دخولٌ ناجح يمحو أثر النطاق كاملًا (بما فيه درجة التصعيد)."""
    try:
        with transaction() as conn:
            conn.execute("DELETE FROM login_throttle WHERE scope = ?", (scope,))
    except Exception:  # noqa: BLE001
        pass


def humanize(seconds: int) -> str:
    """«١٥ دقيقة» / «ساعتان» — نصٌّ للمستخدم لا للسجلّ."""
    mins = max(1, int(round(seconds / 60)))
    if mins < 60:
        return f"{mins} دقيقة"
    hours = mins // 60
    rest = mins % 60
    return f"{hours} ساعة" + (f" و{rest} دقيقة" if rest else "")
