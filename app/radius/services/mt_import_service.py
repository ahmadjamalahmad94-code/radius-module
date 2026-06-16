"""mt_import_service — تحويل سجلّات الراوتر الخام إلى معاينة استيراد.

الجزء الثالث من ميزة «استيراد المشتركين من المايكروتيك». مهمّتها: أخذ
السجلّات الخام التي جلبتها :mod:`mt_import_fetch` وبناء **معاينة** قابلة
للعرض قبل أي كتابة لقاعدة البيانات — تطبيق خرائط الحقول، ربط البروفايل
بالخطّة، وتصنيف كل صفّ (جديد/مكرّر/غير صالح). لا كتابة هنا إطلاقًا؛ القراءة
فقط (موجودون مسبقًا + الخطط). الكتابة الفعليّة في الزيادة 4.

خرائط الحقول:

  هوتسبوت (/ip/hotspot/user):
    name → username | password → password | profile → profile (→ خطّة)
    mac-address → mac | comment → comment | disabled → معطّل

  نطاق عريض (/ppp/secret):
    name → username | password → password | profile → profile (→ خطّة)
    caller-id → mac | remote-address → static_ip | comment → comment

ربط البروفايل بالخطّة: نطابق اسم بروفايل الراوتر باسم خطّة موجودة (غير
حسّاس لحالة الأحرف/المسافات). عند عدم التطابق نَسِم الصفّ «بروفايل غير
مربوط» (plan_id=None) — يُترك قرار الإنشاء الآمن/التخطّي للزيادة 4.

تصنيف الصفّ مقابل الموجودين:
  • new        — لا مشترك بنفس username.
  • duplicate  — يوجد مشترك بنفس username (المعالجة حسب وضع التكرار لاحقًا).
  • invalid    — لا username صالح (يُستبعَد من الاستيراد).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from .mt_import_fetch import IMPORT_BROADBAND, IMPORT_HOTSPOT, _norm_import_type

# تصنيف الصفّ.
ROW_NEW = "new"
ROW_DUPLICATE = "duplicate"
ROW_INVALID = "invalid"

# حالة ربط الخطّة.
PLAN_MATCHED = "matched"
PLAN_UNMAPPED = "unmapped"

# نوع الخدمة المُشتقّ من نوع الاستيراد (يطابق Subscriber.service_type).
_SERVICE_TYPE = {
    IMPORT_HOTSPOT: "Hotspot",
    IMPORT_BROADBAND: "PPPoE",
}


@dataclass
class Candidate:
    """مرشّح استيراد مُطبَّع من سجلّ راوتر واحد (بعد خرائط الحقول)."""
    username: str = ""
    password: str = ""
    profile: str = ""
    service_type: str = ""
    mac: str = ""
    static_ip: str = ""
    comment: str = ""
    disabled: bool = False
    plan_id: Optional[int] = None
    plan_name: str = ""
    plan_status: str = PLAN_UNMAPPED
    raw_id: str = ""

    def public_dict(self) -> dict:
        """تمثيل آمن للعرض — بلا كلمة المرور (لا تُسرَّب للواجهة)."""
        return {
            "username": self.username,
            "profile": self.profile,
            "service_type": self.service_type,
            "mac": self.mac,
            "static_ip": self.static_ip,
            "comment": self.comment,
            "disabled": self.disabled,
            "plan_id": self.plan_id,
            "plan_name": self.plan_name,
            "plan_status": self.plan_status,
            "has_password": bool(self.password),
        }


@dataclass
class PreviewRow:
    candidate: Candidate
    status: str = ROW_NEW           # new|duplicate|invalid
    note: str = ""

    def public_dict(self) -> dict:
        d = self.candidate.public_dict()
        d["status"] = self.status
        d["note"] = self.note
        return d


@dataclass
class ImportPreview:
    import_type: str = ""
    transport: str = ""
    rows: list[PreviewRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ── عدّادات مشتقّة ──
    @property
    def total(self) -> int:
        return len(self.rows)

    @property
    def counts(self) -> dict:
        c = {ROW_NEW: 0, ROW_DUPLICATE: 0, ROW_INVALID: 0}
        for r in self.rows:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    @property
    def unmapped_profiles(self) -> list[str]:
        seen: list[str] = []
        for r in self.rows:
            cand = r.candidate
            if (cand.plan_status == PLAN_UNMAPPED and cand.profile
                    and cand.profile not in seen):
                seen.append(cand.profile)
        return seen

    def public_dict(self) -> dict:
        return {
            "import_type": self.import_type,
            "transport": self.transport,
            "total": self.total,
            "counts": self.counts,
            "unmapped_profiles": self.unmapped_profiles,
            "warnings": list(self.warnings),
            "rows": [r.public_dict() for r in self.rows],
        }


# ── خرائط الحقول ─────────────────────────────────────────────────────

def _s(rec: Mapping[str, Any], *keys: str) -> str:
    """أول قيمة نصّية غير فارغة من مفاتيح مرشّحة."""
    for k in keys:
        v = rec.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def _is_disabled(rec: Mapping[str, Any]) -> bool:
    """علم التعطيل — من `_disabled` المُطبَّع (بعد الجلب) أو `disabled` الخام
    (نصّ RouterOS «true»/«false») كي يعمل البناء على السجلّ الخام أو المُطبَّع."""
    if "_disabled" in rec:
        return bool(rec.get("_disabled"))
    return str(rec.get("disabled") or "").strip().lower() in ("true", "yes", "1")


def build_candidate(record: Mapping[str, Any], import_type: str) -> Candidate:
    """يطبّق خرائط الحقول على سجلّ راوتر خام → Candidate (بلا ربط خطّة بعد)."""
    itype = _norm_import_type(import_type)
    cand = Candidate(
        username=_s(record, "name", "user", "username"),
        password=_s(record, "password"),
        profile=_s(record, "profile"),
        service_type=_SERVICE_TYPE[itype],
        comment=_s(record, "comment"),
        disabled=_is_disabled(record),
        raw_id=_s(record, "_id", ".id", "id"),
    )
    if itype == IMPORT_HOTSPOT:
        cand.mac = _s(record, "mac-address", "mac_address")
    else:  # broadband / ppp secret
        cand.mac = _s(record, "caller-id", "caller_id")
        cand.static_ip = _s(record, "remote-address", "remote_address")
    return cand


# ── ربط البروفايل بالخطّة ────────────────────────────────────────────

def _plan_index(tenant_id: int) -> dict[str, tuple[int, str]]:
    """خريطة اسم-خطّة مُطبَّع → (plan_id, الاسم الأصلي). تُحمَّل مرّة للمعاينة."""
    from ..db.repos import plans_repo
    idx: dict[str, tuple[int, str]] = {}
    for p in plans_repo.list_plans(tenant_id, limit=1000):
        key = _norm_name(p.name)
        if key and key not in idx:
            idx[key] = (int(p.id), p.name)
    return idx


def _norm_name(name: str) -> str:
    return " ".join(str(name or "").strip().lower().split())


def map_profile_to_plan(profile: str, plan_index: Mapping[str, tuple[int, str]]
                        ) -> tuple[Optional[int], str, str]:
    """يطابق بروفايل الراوتر بخطّة موجودة. يُعيد (plan_id, plan_name, status)."""
    key = _norm_name(profile)
    if key and key in plan_index:
        pid, pname = plan_index[key]
        return pid, pname, PLAN_MATCHED
    return None, "", PLAN_UNMAPPED


# ── بناء المعاينة ────────────────────────────────────────────────────

def build_preview(tenant_id: int, import_type: str,
                  records: list[Mapping[str, Any]], *,
                  transport: str = "") -> ImportPreview:
    """يبني معاينة استيراد كاملة (للقراءة فقط) من السجلّات الخام.

    يحمّل فهرس الخطط مرّة، ويستعلم وجود كل username مرّة — لا كتابة."""
    itype = _norm_import_type(import_type)
    preview = ImportPreview(import_type=itype, transport=transport)
    plan_index = _plan_index(tenant_id)

    from ..db.repos import subscribers_repo

    seen_usernames: set[str] = set()
    for rec in records or []:
        cand = build_candidate(rec, itype)
        pid, pname, pstatus = map_profile_to_plan(cand.profile, plan_index)
        cand.plan_id, cand.plan_name, cand.plan_status = pid, pname, pstatus

        if not cand.username:
            preview.rows.append(PreviewRow(cand, status=ROW_INVALID,
                                           note="بلا اسم مستخدم — يُستبعَد"))
            continue

        # تكرار داخل الدفعة نفسها (نفس الاسم مرّتين في الراوتر).
        if cand.username in seen_usernames:
            preview.rows.append(PreviewRow(cand, status=ROW_DUPLICATE,
                                           note="مكرّر داخل الدفعة"))
            continue
        seen_usernames.add(cand.username)

        existing = subscribers_repo.get_subscriber(tenant_id, cand.username)
        if existing is not None:
            preview.rows.append(PreviewRow(cand, status=ROW_DUPLICATE,
                                           note="موجود مسبقًا"))
        else:
            note = "" if pstatus == PLAN_MATCHED else "بروفايل غير مربوط بخطّة"
            preview.rows.append(PreviewRow(cand, status=ROW_NEW, note=note))

    if preview.unmapped_profiles:
        preview.warnings.append(
            "بروفايلات بلا خطّة مطابقة: " + "، ".join(preview.unmapped_profiles))
    return preview


__all__ = [
    "ROW_NEW", "ROW_DUPLICATE", "ROW_INVALID",
    "PLAN_MATCHED", "PLAN_UNMAPPED",
    "Candidate", "PreviewRow", "ImportPreview",
    "build_candidate", "map_profile_to_plan", "build_preview",
]
