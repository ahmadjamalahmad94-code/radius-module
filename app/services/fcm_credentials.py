"""تخزين اعتماد Firebase (حساب الخدمة) المرفوع من اللوحة — بلا أي خطوة خادم.

الهدف
-----
يَجعل اعتماد دفع FCM **قابلًا للضبط بالكامل من واجهة اللوحة**: المالك يَرفع
ملفّ ``firebase-adminsdk.json`` من المتصفّح، فيُخزَّن على الخادم ويُلتقط تلقائيًّا
عند الإرسال — دون أي وصول للطرفية أو متغيّرات بيئة.

أين يُخزَّن؟
-----------
الملفّ السرّ يُكتب بجوار قاعدة البيانات الحيّة في مجلّد ``instance/`` (المُتجاهَل
في git عبر ``.gitignore``) باسم ``firebase-adminsdk.json``. هذا المسار يَبقى عبر
إعادة التشغيل، ولا يُرفَع للمستودع أبدًا. تُحفظ نسخة احتياطيّة من المحتوى الخام
**مشفّرة** (Fernet عبر ``env_settings``، جدول ``system_settings``) كي يُعاد توليد
الملفّ لو فُقد المجلّد (مثلًا بعد إعادة بناء حاوية بلا تثبيت الحجم) — تَستردّ
السلسلة نفسها. التشفير مقصود: النسخة الاحتياطيّة لقاعدة البيانات ملفّ SQLite خام
يُنقَل، فلا يَصحّ أن يَحوي المفتاح الخاصّ كنصّ صريح. حقول التعريف العامّة
(project_id/client_email/الوقت) تُحفظ في ``tenant_settings`` كنصّ (ليست سرًّا).

ترتيب التحميل (يُستخدمه ``fcm_push``)
-------------------------------------
    1) الملفّ المرفوع   instance/firebase-adminsdk.json
    2) JSON المُخزَّن في قاعدة البيانات → يُكتب للملفّ ثم يُستخدم
    3) متغيّر البيئة (توافق رجعيّ) — يُعالجه المُتّصِل

الأمان
------
لا يُعاد عرض محتوى الملفّ أبدًا. تُكشف فقط حقول التعريف العامّة (project_id،
client_email مُقنَّعًا) للحالة. المفتاح الخاصّ لا يُرسَل للواجهة إطلاقًا.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional, Tuple

_LOG = logging.getLogger(__name__)

# اسم الملفّ السرّ داخل مجلّد instance/ (بجوار قاعدة البيانات الحيّة).
CRED_FILENAME = "firebase-adminsdk.json"

# مفاتيح tenant_settings (نطاق المستأجر الافتراضي — الاعتماد خادميّ-عامّ).
# حقول تعريف عامّة فقط (ليست سرًّا) — تُعرَض في الحالة.
_K_PROJECT = "push.fcm.project_id"
_K_EMAIL = "push.fcm.client_email"
_K_UPLOADED = "push.fcm.uploaded_at"

# نسخة الاسترداد السرّيّة (المحتوى الخام للملفّ) — تُخزَّن **مشفّرة** في
# system_settings عبر env_settings. مفتاح بنمط env كي يَتوافق مع طبقة الإعدادات.
_K_JSON_SECRET = "HOBERADIUS_FCM_CREDENTIAL_JSON"

# الحقول الإلزاميّة لملفّ حساب خدمة Firebase صالح.
_REQUIRED_FIELDS = ("type", "project_id", "private_key", "client_email")


def _default_tenant_id() -> int:
    """الاعتماد خادميّ-عامّ — نُخزّن تعريفه تحت المستأجر الافتراضي دائمًا."""
    try:
        from app.radius.core.tenant import DEFAULT_TENANT_ID
        return int(DEFAULT_TENANT_ID)
    except Exception:  # noqa: BLE001 — احتياط
        return 1


def _instance_dir() -> Path:
    """مجلّد instance/ الفعليّ (نفس مجلّد قاعدة البيانات الحيّة).

    يُشتقّ من ``db_path`` كي يَبقى الملفّ السرّ دومًا بجوار القاعدة الحيّة
    أينما ضُبط مسارها (env أو الافتراضي)."""
    try:
        from app.radius.db.connection import db_path
        return Path(db_path()).resolve().parent
    except Exception:  # noqa: BLE001 — قبل تهيئة القاعدة
        here = Path(__file__).resolve().parent.parent.parent  # radius-module/
        return here / "instance"


def stored_file_path() -> Path:
    """المسار المُطلَق للملفّ السرّ المرفوع."""
    return _instance_dir() / CRED_FILENAME


# ───────────────────────── التحقّق ─────────────────────────

def validate_service_account(raw: bytes) -> Tuple[bool, Optional[dict], str]:
    """يتحقّق أنّ ``raw`` هو ملفّ حساب خدمة Firebase حقيقيّ.

    يُرجع ``(ok, data، رسالة_خطأ_عربيّة)``. عند الرفض ``data=None``."""
    try:
        text = raw.decode("utf-8")
    except Exception:  # noqa: BLE001
        return False, None, "تعذّر قراءة الملفّ كنصّ UTF-8."
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        return False, None, "الملفّ ليس JSON صالحًا."
    if not isinstance(data, dict):
        return False, None, "بنية الملفّ غير صحيحة (يُتوقَّع كائن JSON)."
    if str(data.get("type") or "").strip() != "service_account":
        return False, None, ("هذا ليس ملفّ حساب خدمة Firebase "
                             "(type ≠ service_account).")
    missing = [f for f in _REQUIRED_FIELDS if not str(data.get(f) or "").strip()]
    if missing:
        return False, None, ("الملفّ ينقصه حقول حساب الخدمة: "
                             + "، ".join(missing) + ".")
    if "PRIVATE KEY" not in str(data.get("private_key") or ""):
        return False, None, "المفتاح الخاصّ في الملفّ غير صالح."
    return True, data, ""


# ───────────────────────── التخزين ─────────────────────────

def _set_setting(key: str, value: str) -> None:
    from app.radius.db.repos import tenants_repo
    tenants_repo.set_setting(_default_tenant_id(), key, value)


def _get_setting(key: str, default: str = "") -> str:
    try:
        from app.radius.db.repos import tenants_repo
        return str(tenants_repo.get_setting(_default_tenant_id(), key, default) or default)
    except Exception:  # noqa: BLE001 — الحالة لا تَكسر صفحة
        return default


def _set_secret_json(raw_text: str, *, by: int = 0) -> None:
    """يُخزّن المحتوى الخام **مشفّرًا** (system_settings عبر env_settings)."""
    from app.radius.core import env_settings
    env_settings.set_value(_K_JSON_SECRET, raw_text, by=by, secret=True)


def _get_secret_json() -> str:
    """يَقرأ نسخة الاسترداد المشفّرة (مفكوكة) أو '' إن لم تُضبط."""
    try:
        from app.radius.core import env_settings
        return str(env_settings.env(_K_JSON_SECRET, "") or "")
    except Exception:  # noqa: BLE001 — لا يَكسر الارتداد للملفّ
        return ""


def _clear_secret_json() -> None:
    try:
        from app.radius.core import env_settings
        env_settings.clear_value(_K_JSON_SECRET)
    except Exception:  # noqa: BLE001
        pass


def store_uploaded(raw: bytes, *, by: int = 0) -> dict:
    """يتحقّق ثم يُخزّن الاعتماد المرفوع (ملفّ instance/ + نسخة قاعدة بيانات).

    يَرمي ``ValueError`` برسالة عربيّة عند ملفّ غير صالح. يُرجع تعريفًا
    آمنًا للعرض ``{project_id, client_email}`` عند النجاح."""
    ok, data, err = validate_service_account(raw)
    if not ok or data is None:
        raise ValueError(err or "ملفّ اعتماد غير صالح.")

    # (1) اكتب الملفّ السرّ ذرّيًّا بجوار القاعدة الحيّة.
    path = stored_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    os.replace(tmp, path)
    try:  # أفضل-جهد: تقييد الصلاحيات (يُتجاهَل على ويندوز).
        os.chmod(path, 0o600)
    except Exception:  # noqa: BLE001
        pass

    # (2) نسخة استرداد + تعريف للعرض في قاعدة البيانات.
    from datetime import datetime, timezone
    try:
        _set_secret_json(raw.decode("utf-8"), by=by)   # مشفّرة
        _set_setting(_K_PROJECT, str(data.get("project_id") or ""))
        _set_setting(_K_EMAIL, str(data.get("client_email") or ""))
        _set_setting(_K_UPLOADED, datetime.now(timezone.utc).isoformat())
    except Exception:  # noqa: BLE001 — الملفّ يكفي؛ النسخة الاحتياطيّة أفضل-جهد
        _LOG.warning("FCM credential DB backup failed (file written)", exc_info=True)

    # (3) أبطِل ذاكرة fcm_push كي يُلتقط الاعتماد الجديد فورًا.
    try:
        from app.services import fcm_push
        fcm_push.reset_for_test()
    except Exception:  # noqa: BLE001
        pass

    return {"project_id": str(data.get("project_id") or ""),
            "client_email": str(data.get("client_email") or "")}


def clear() -> int:
    """يَحذف الاعتماد المُخزَّن (الملفّ + نسخة القاعدة). يُرجع عدد ما حُذف."""
    n = 0
    path = stored_file_path()
    try:
        if path.is_file():
            path.unlink()
            n += 1
    except Exception:  # noqa: BLE001
        pass
    _clear_secret_json()
    try:
        for k in (_K_PROJECT, _K_EMAIL, _K_UPLOADED):
            _set_setting(k, "")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.services import fcm_push
        fcm_push.reset_for_test()
    except Exception:  # noqa: BLE001
        pass
    return n


# ───────────────────────── التحميل ─────────────────────────

def resolve_credential_path() -> str:
    """يُرجع مسار ملفّ اعتماد جاهز للاستخدام، أو '' إن لا اعتماد مرفوع.

    الترتيب: (1) الملفّ المرفوع في instance/ → (2) نسخة JSON في القاعدة
    (تُكتَب للملفّ ثم تُستخدم). متغيّر البيئة يُعالجه المُتّصِل بعد هذا."""
    path = stored_file_path()
    if path.is_file():
        return str(path)
    # نسخة الاسترداد: أعِد توليد الملفّ من القاعدة (مثلًا بعد فقد المجلّد).
    raw = _get_secret_json()
    if raw.strip():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw, encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except Exception:  # noqa: BLE001
                pass
            return str(path)
        except Exception:  # noqa: BLE001
            _LOG.warning("FCM credential restore-from-DB failed", exc_info=True)
    return ""


# ───────────────────────── الحالة (مُقنَّعة) ─────────────────────────

def _mask_email(email: str) -> str:
    """يُقنّع البريد للعرض: يُبقي بداية المعرّف + النطاق كاملًا.

    ``firebase-adminsdk-abc12@hoberadius.iam...`` →
    ``firebase-adminsdk-…@hoberadius.iam...``"""
    email = (email or "").strip()
    if "@" not in email:
        return "—"
    local, _, domain = email.partition("@")
    head = local[:18]
    if len(local) > 18:
        head += "…"
    return f"{head}@{domain}"


def status() -> dict:
    """حالة الاعتماد المُخزَّن للعرض — لا تَكشف السرّ أبدًا.

      • configured    : هل يوجد اعتماد مرفوع/مُخزَّن؟
      • project_id     : معرّف مشروع Firebase (عامّ).
      • client_email   : بريد حساب الخدمة (مُقنَّع).
      • uploaded_at    : وقت آخر رفع (ISO) إن وُجد.
    تُقرأ الحقول العامّة من الملفّ الفعليّ متى أمكن (أوثق مصدر)."""
    out = {"configured": False, "project_id": "", "client_email": "",
           "uploaded_at": _get_setting(_K_UPLOADED, "")}
    path = stored_file_path()
    data: Optional[dict] = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — مَلفّ تالف → نَرتدّ للقاعدة
            data = None
    if isinstance(data, dict) and str(data.get("type") or "") == "service_account":
        out["configured"] = True
        out["project_id"] = str(data.get("project_id") or "")
        out["client_email"] = _mask_email(str(data.get("client_email") or ""))
        return out
    # احتياط: تعريف من القاعدة (نسخة الاسترداد موجودة لكن الملفّ غاب).
    if _get_secret_json().strip():
        out["configured"] = True
        out["project_id"] = _get_setting(_K_PROJECT, "")
        out["client_email"] = _mask_email(_get_setting(_K_EMAIL, ""))
    return out


__all__ = [
    "CRED_FILENAME",
    "stored_file_path",
    "validate_service_account",
    "store_uploaded",
    "clear",
    "resolve_credential_path",
    "status",
]
