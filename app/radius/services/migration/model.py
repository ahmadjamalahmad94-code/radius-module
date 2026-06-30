"""أنواع بيانات محرّك الترحيل — dataclasses خالصة (بلا Flask/DB).

تمثّل ثلاث مراحل:

  • المصدر بعد الاستخراج/الفحص:  ``SourceColumn`` / ``SourceTable`` / ``SourceDataset``
  • نتيجة التحليل (للقراءة):     ``SectionMatch`` / ``AnalysisResult``
  • خطّة + تقرير الاستيراد:      ``Candidate`` / ``RowPlan`` / ``SectionPlan`` /
                                 ``ImportPlan`` / ``SectionReport`` / ``ImportReport``

كل ``*_public_dict`` يُرجع شكلًا آمنًا للإرسال للمتصفّح (بلا كلمات مرور خام).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# ─── حالات الصفوف في الخطّة ──────────────────────────────────────────
ROW_NEW = "new"           # سيُنشأ (غير موجود)
ROW_MERGE = "merge"       # موجود مسبقًا — سيُحدَّث/يُدمَج
ROW_SKIP = "skip"         # سيُتخطّى (موجود + وضع التخطّي، أو غير صالح)
ROW_INVALID = "invalid"   # غير صالح (مفتاح طبيعيّ ناقص…)


# ════════════════════════════════════════════════════════════════════
# (1) المصدر
# ════════════════════════════════════════════════════════════════════

@dataclass
class SourceColumn:
    name: str
    sample_values: list[str] = field(default_factory=list)
    non_empty: int = 0

    def public_dict(self) -> dict:
        return {"name": self.name,
                "samples": self.sample_values[:5],
                "non_empty": self.non_empty}


@dataclass
class SourceTable:
    """جدول/ورقة/قسم واحد من المصدر، مُطبَّع إلى صفوف dict موحّدة."""
    name: str
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, str]] = field(default_factory=list)
    origin: str = ""          # 'sqlite' | 'sql_dump' | 'xlsx' | 'csv' | 'pdf' | 'mikrotik'
    note: str = ""

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def column_info(self, *, sample: int = 5) -> list[SourceColumn]:
        out: list[SourceColumn] = []
        for c in self.columns:
            vals = [str(r.get(c, "") or "") for r in self.rows]
            non_empty = sum(1 for v in vals if v.strip())
            seen: list[str] = []
            for v in vals:
                v = v.strip()
                if v and v not in seen:
                    seen.append(v)
                if len(seen) >= sample:
                    break
            out.append(SourceColumn(name=c, sample_values=seen, non_empty=non_empty))
        return out

    def public_dict(self, *, sample_rows: int = 5) -> dict:
        return {
            "name": self.name,
            "origin": self.origin,
            "note": self.note,
            "row_count": self.row_count,
            "columns": [c.public_dict() for c in self.column_info()],
            "sample_rows": self.rows[:sample_rows],
        }


@dataclass
class SourceDataset:
    fmt: str = "unknown"      # 'sqlite' | 'sql_dump' | 'xlsx' | 'csv' | 'pdf' | 'mikrotik'
    tables: list[SourceTable] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def table(self, name: str) -> Optional[SourceTable]:
        for t in self.tables:
            if t.name == name:
                return t
        return None

    def public_dict(self) -> dict:
        return {
            "fmt": self.fmt,
            "warnings": self.warnings,
            "tables": [t.public_dict() for t in self.tables],
        }


# ════════════════════════════════════════════════════════════════════
# (2) التحليل / التصنيف
# ════════════════════════════════════════════════════════════════════

@dataclass
class SectionMatch:
    """ربط مُقترَح: قسم HobeRadius ← جدول مصدر، مع خريطة أعمدة وثقة."""
    section: str                         # مفتاح القسم (subscribers, plans, …)
    source_table: str
    confidence: float = 0.0              # 0..1
    column_map: dict[str, str] = field(default_factory=dict)  # target_field → source_column
    recognized_as: str = ""              # 'freeradius' | 'mikrotik' | 'generic' | ''
    note: str = ""
    row_count: int = 0

    def public_dict(self) -> dict:
        return {
            "section": self.section,
            "source_table": self.source_table,
            "confidence": round(self.confidence, 3),
            "column_map": self.column_map,
            "recognized_as": self.recognized_as,
            "note": self.note,
            "row_count": self.row_count,
        }


@dataclass
class AnalysisResult:
    dataset: SourceDataset
    matches: list[SectionMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def matches_for(self, section: str) -> list[SectionMatch]:
        return [m for m in self.matches if m.section == section]

    def public_dict(self) -> dict:
        return {
            "fmt": self.dataset.fmt,
            "dataset": self.dataset.public_dict(),
            "matches": [m.public_dict() for m in self.matches],
            "warnings": list(self.warnings) + list(self.dataset.warnings),
        }


# ════════════════════════════════════════════════════════════════════
# (3) الخطّة + التقرير
# ════════════════════════════════════════════════════════════════════

@dataclass
class Candidate:
    """سجلّ مُطبَّع جاهز للاستيراد ضمن قسم. ``fields`` بأسماء حقول HobeRadius."""
    section: str
    natural_key: str
    fields: dict[str, Any] = field(default_factory=dict)
    source_ref: str = ""                 # مفتاح/معرّف المصدر (لإعادة بناء العلاقات)


@dataclass
class RowPlan:
    natural_key: str
    status: str = ROW_NEW                # new|merge|skip|invalid
    reason: str = ""                     # سبب عربيّ واضح عند skip/invalid
    preview: dict[str, Any] = field(default_factory=dict)   # حقول آمنة للعرض

    def public_dict(self) -> dict:
        return {"key": self.natural_key, "status": self.status,
                "reason": self.reason, "preview": self.preview}


@dataclass
class SectionPlan:
    section: str
    rows: list[RowPlan] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)   # محاذية لـrows
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict:
        c = {ROW_NEW: 0, ROW_MERGE: 0, ROW_SKIP: 0, ROW_INVALID: 0}
        for r in self.rows:
            c[r.status] = c.get(r.status, 0) + 1
        return c

    def public_dict(self, *, sample: int = 8) -> dict:
        return {
            "section": self.section,
            "counts": self.counts(),
            "total": len(self.rows),
            "warnings": self.warnings,
            "sample": [r.public_dict() for r in self.rows[:sample]],
        }


@dataclass
class ImportPlan:
    sections: list[SectionPlan] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def section(self, key: str) -> Optional[SectionPlan]:
        for s in self.sections:
            if s.section == key:
                return s
        return None

    def public_dict(self) -> dict:
        return {
            "sections": [s.public_dict() for s in self.sections],
            "warnings": self.warnings,
        }


@dataclass
class SectionReport:
    section: str
    created: int = 0
    merged: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[dict] = field(default_factory=list)   # {key, action, reason}

    @property
    def total(self) -> int:
        return self.created + self.merged + self.skipped + self.failed

    def public_dict(self) -> dict:
        return {
            "section": self.section,
            "created": self.created, "merged": self.merged,
            "skipped": self.skipped, "failed": self.failed,
            "total": self.total,
            "errors": self.errors[:50],
        }


@dataclass
class ImportReport:
    dry_run: bool = False
    sections: list[SectionReport] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    status: str = "completed"            # completed | failed

    def section(self, key: str) -> SectionReport:
        for s in self.sections:
            if s.section == key:
                return s
        sr = SectionReport(section=key)
        self.sections.append(sr)
        return sr

    @property
    def created(self) -> int:
        return sum(s.created for s in self.sections)

    @property
    def merged(self) -> int:
        return sum(s.merged for s in self.sections)

    def public_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "status": self.status,
            "totals": {
                "created": sum(s.created for s in self.sections),
                "merged": sum(s.merged for s in self.sections),
                "skipped": sum(s.skipped for s in self.sections),
                "failed": sum(s.failed for s in self.sections),
            },
            "sections": [s.public_dict() for s in self.sections],
            "warnings": self.warnings,
        }


__all__ = [
    "ROW_NEW", "ROW_MERGE", "ROW_SKIP", "ROW_INVALID",
    "SourceColumn", "SourceTable", "SourceDataset",
    "SectionMatch", "AnalysisResult",
    "Candidate", "RowPlan", "SectionPlan", "ImportPlan",
    "SectionReport", "ImportReport",
]
