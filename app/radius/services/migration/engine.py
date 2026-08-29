"""محرّك الترحيل — التنسيق العامّ: analyze / build_plan / commit.

  • ``analyze``     — يفحص المصدر ويصنّفه (للقراءة فقط، بلا DB). نقيّ.
  • ``build_plan``  — يبني خطّة استيراد: لكل قسم مُختار، يصنّف كل صفّ
                      جديد/مدمج/متخطّى/غير-صالح مع سبب عربيّ، ويعاين حلّ
                      العلاقات (مشترك→باقة، موزّع→مدير…). يقرأ DB، لا يكتب.
  • ``commit``      — ينفّذ بترتيب الاعتماد (أدوار→مدراء→موزّعون→باقات→حِزم
                      →مشتركون→كروت). يُنشئ غير الموجود، ويدمج الموجود حسب
                      الوضع، ويحلّ المفاتيح الأجنبيّة عبر خريطة معرّفات
                      (مصدر→جديد). idempotent: إعادة التشغيل تطابق بالمفتاح
                      الطبيعيّ فلا تُكرّر. ``dry_run`` لا يكتب شيئًا.

نموذج الكتابة «أفضل-جهد محاسَب» (نظير mt_import_runner): صفّ فاشل يُسجَّل في
``errors`` ولا يُجهض القسم؛ idempotency هو شبكة الأمان عند فشل جزئيّ (إعادة
التشغيل تُكمل الناقص وتدمج الموجود فتتقارب).

كلمات المرور: المشترك/الكرت تُخزَّن كما هي (PAP يحتاج النصّ الصريح). كلمة
مُجزّأة (hash من FreeRADIUS) لا تصلح لـPAP — تُحفظ في ``metadata`` مع علم
واضح وتُترك كلمة المشترك فارغة (لا نكسر المصادقة صامتةً). كلمة المدير تُجزَّأ
بـwerkzeug عبر ``create_admin``.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import replace
from datetime import datetime
from typing import Any, Optional

from . import classify, mapping, sources
from .model import (
    AnalysisResult, Candidate, ImportPlan, ImportReport, ROW_INVALID, ROW_MERGE,
    ROW_NEW, ROW_SKIP, RowPlan, SectionPlan, SectionReport, SectionMatch,
)
from .sections import (
    COMMIT_ORDER, SEC_BATCHES, SEC_CARDS, SEC_DISTRIBUTORS, SEC_MANAGERS,
    SEC_PLANS, SEC_ROLES, SEC_SUBSCRIBERS, get_section, norm_key,
)

# حاوية الكروت المستورَدة: حزمة واحدة تُجمَّع تحتها حسابات الكروت (إذ
# cards.batch_id غير قابل للـNULL) — مصدر FreeRADIUS لا يملك مفهوم «حزمة».
_IMPORT_BATCH_NAME = "كروت مستورَدة"
_IMPORT_BATCH_KEY = norm_key(_IMPORT_BATCH_NAME)
# باقة احتياطيّة تُنشأ فقط لو استُورِدت كروت من مصدر بلا أيّ باقة (قسائم صرفة).
_IMPORT_PLAN_NAME = "باقة مستورَدة"
_IMPORT_PLAN_KEY = norm_key(_IMPORT_PLAN_NAME)


# ════════════════════════════════════════════════════════════════════
# (1) التحليل — للقراءة فقط، نقيّ
# ════════════════════════════════════════════════════════════════════

def analyze(file_bytes: bytes, filename: str = "", *, progress_cb=None) -> AnalysisResult:
    return _finish_analyze(
        sources.introspect(file_bytes, filename, progress_cb=progress_cb),
        progress_cb)


def analyze_path(path: str, filename: str = "", *, progress_cb=None) -> AnalysisResult:
    """كـ:func:`analyze` لكن يقرأ من القرص بتدفّق — للملفّات الكبيرة/gzip
    (تفريغ SQL بمئات الميغابايت) دون تحميلها كاملةً في الذاكرة.

    ``progress_cb(phase, info)`` يُستدعى دوريًّا لبثّ تفاصيل مرحلة التحليل
    الحيّة (قراءة/فحص بنية+عدّ الجداول والصفوف/تصنيف/العدّ النهائيّ)."""
    return _finish_analyze(
        sources.introspect_path(path, filename, progress_cb=progress_cb),
        progress_cb)


def _finish_analyze(dataset, progress_cb=None) -> AnalysisResult:
    from . import presets
    if progress_cb:
        try:
            progress_cb("classify", {"tables": len(dataset.tables)})
        except Exception:  # noqa: BLE001
            pass
    matches = classify.classify_dataset(dataset)
    res = AnalysisResult(dataset=dataset, matches=matches)
    res.recognized_source = presets.recognize(dataset)
    res.recognized_label = presets.label(res.recognized_source)
    if not matches and dataset.tables:
        res.warnings.append(
            "لم يُتعرَّف تلقائيًّا على أيّ قسم — يمكنك ربط الأعمدة يدويًّا.")
    if progress_cb:
        # عدّ نهائيّ لكل قسم (من صفوف الترشيحات) لعرضه حيًّا.
        counts: dict[str, int] = {}
        for m in matches:
            counts[m.section] = counts.get(m.section, 0) + int(m.row_count or 0)
        try:
            progress_cb("done", {"tables": len(dataset.tables), "counts": counts})
        except Exception:  # noqa: BLE001
            pass
    return res


# ────────────────────────────────────────────────────────────────────
# مساعد: تطبيع «الاختيارات» القادمة من الواجهة إلى قائمة عمليّات استيراد.
# كل عمليّة = (section, source_table, column_map, mode, enabled).
# ────────────────────────────────────────────────────────────────────

def _imports_from(matches: list[SectionMatch],
                  selections: Optional[list[dict]]) -> list[dict]:
    if selections:
        out = []
        for sel in selections:
            if not sel.get("enabled", True):
                continue
            section = sel.get("section")
            if not get_section(section):
                continue
            out.append({
                "section": section,
                "source_table": sel.get("source_table", ""),
                "column_map": sel.get("column_map") or {},
                "mode": (sel.get("mode") or "merge").lower(),
                "recognized_as": sel.get("recognized_as", ""),
            })
        return out
    # بلا اختيارات صريحة → كل ترشيح بثقة كافية مُفعَّل افتراضًا (وضع دمج).
    out = []
    for m in matches:
        out.append({
            "section": m.section, "source_table": m.source_table,
            "column_map": {}, "mode": "merge", "recognized_as": m.recognized_as,
        })
    return out


def _match_for(matches: list[SectionMatch], section: str,
               source_table: str) -> Optional[SectionMatch]:
    for m in matches:
        if m.section == section and (not source_table or m.source_table == source_table):
            return m
    return None


def _candidates_for(dataset, matches, imp) -> list[Candidate]:
    m = _match_for(matches, imp["section"], imp["source_table"])
    if m is None:
        m = SectionMatch(section=imp["section"], source_table=imp["source_table"],
                         recognized_as=imp.get("recognized_as", ""),
                         column_map=imp.get("column_map") or {})
    return mapping.build_candidates(dataset, m,
                                    column_map_override=imp.get("column_map") or None)


# ════════════════════════════════════════════════════════════════════
# (2) الخطّة — تقرأ DB، لا تكتب
# ════════════════════════════════════════════════════════════════════

def build_plan(tenant_id: int, dataset, matches: list[SectionMatch], *,
               selections: Optional[list[dict]] = None) -> ImportPlan:
    imports = _imports_from(matches, selections)
    plan = ImportPlan()

    # المفاتيح الموجودة مسبقًا + ما «سيُنشأ» في هذه العمليّة (لمعاينة العلاقات).
    existing = _load_existing_keys(tenant_id)
    incoming = {k: set() for k in COMMIT_ORDER}
    for imp in imports:
        for c in _candidates_for(dataset, matches, imp):
            if c.natural_key:
                incoming[c.section].add(c.natural_key)
            # المدير يُشتَقّ من «انشئ بواسطة» فيُنشأ تلقائيًّا — عُدّه «قادمًا»
            # كي لا تُحذِّر المعاينة أنه غير مطابق.
            if c.section in (SEC_SUBSCRIBERS,) and c.fields.get("manager"):
                incoming[SEC_MANAGERS].add(norm_key(str(c.fields["manager"])))

    # رتّب العمليّات بترتيب الاعتماد كي تظهر الأقسام منظَّمة.
    imports.sort(key=lambda i: _rank(i["section"]))

    by_section: dict[str, SectionPlan] = {}
    for imp in imports:
        section_key = imp["section"]
        sp = by_section.setdefault(section_key, SectionPlan(section=section_key))
        section = get_section(section_key)
        mode = imp["mode"]
        seen: set[str] = set()
        for c in _candidates_for(dataset, matches, imp):
            row, warn = _classify_row(tenant_id, section, c, mode,
                                      existing, incoming, seen)
            sp.rows.append(row)
            sp.candidates.append(c)
            if warn and warn not in sp.warnings:
                sp.warnings.append(warn)

    plan.sections = [by_section[k] for k in COMMIT_ORDER if k in by_section]
    return plan


def _classify_row(tenant_id, section, c: Candidate, mode, existing, incoming,
                  seen) -> tuple[RowPlan, str]:
    nat = section.natural_key
    preview = _safe_preview(c)
    if not c.natural_key:
        return RowPlan(natural_key="", status=ROW_INVALID,
                       reason=f"بلا {_field_label(section, nat)} — يُستبعَد",
                       preview=preview), ""
    if c.natural_key in seen:
        return RowPlan(natural_key=c.source_ref, status=ROW_SKIP,
                       reason="مكرّر داخل الملف المصدر", preview=preview), ""
    seen.add(c.natural_key)

    warn = ""
    # معاينة حلّ العلاقات.
    for ref_field, target_section in section.relations:
        ref_val = norm_key(str(c.fields.get(ref_field, "")))
        if not ref_val:
            continue
        if ref_val in existing.get(target_section, {}) or \
                ref_val in incoming.get(target_section, set()):
            continue
        warn = (f"بعض القيم في «{_field_label(section, ref_field)}» لا تطابق "
                f"{_section_label(target_section)} موجودًا أو مستورَدًا — سيُربط لاحقًا/يُترك فارغًا")

    exists = c.natural_key in existing.get(section.key, {})
    if exists and mode == "skip":
        return RowPlan(natural_key=c.source_ref, status=ROW_SKIP,
                       reason="موجود مسبقًا (وضع التخطّي)", preview=preview), warn
    if exists:
        return RowPlan(natural_key=c.source_ref, status=ROW_MERGE,
                       reason="موجود مسبقًا — سيُحدَّث", preview=preview), warn
    return RowPlan(natural_key=c.source_ref, status=ROW_NEW, reason="",
                   preview=preview), warn


# ════════════════════════════════════════════════════════════════════
# (3) التنفيذ — يكتب عبر مستودعات HobeRadius
# ════════════════════════════════════════════════════════════════════

class _SkipRow(Exception):
    """صفّ يُتخطّى بسبب واضح (لا فشل) — مثل حزمة بلا باقة معروفة."""
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def commit(tenant_id: int, dataset, matches: list[SectionMatch], *,
           selections: Optional[list[dict]] = None,
           dry_run: bool = False, actor: str = "migration",
           progress_cb=None) -> ImportReport:
    """ينفّذ الاستيراد.

    ``progress_cb(done, total, section, phase, detail)`` يُستدعى دوريًّا (كل
    ~200 صفّ وعند حدود الأقسام) لبثّ تقدّم حيّ صادق — يُبقي الواجهة حيّة على
    آلاف السجلّات ويتجنّب «الزرّ المجمَّد». ``detail`` قاموس يحمل تفصيل ما يجري:
    عدّ القسم الحاليّ (``section_done``/``section_total``) وحصيلة كل قسم
    (منشأ/مدموج/متخطّى/فاشل) حتى اللحظة — فتَعرض الواجهة «إنشاء المشتركين:
    1,240 / 1,589» مع الحصائل المتزايدة. المعاملات الأربعة الأولى موجبة للتوافق
    الخلفيّ."""
    from .sections import section_label
    imports = _imports_from(matches, selections)
    imports.sort(key=lambda i: _rank(i["section"]))
    report = ImportReport(dry_run=dry_run)

    # عدّ إجماليّ للمرشّحين (للتقدّم). قد يعيد بناء المرشّحين مرّتين لكنه رخيص
    # مقابل شريط تقدّم صادق.
    per_import = [_candidates_for(dataset, matches, imp) for imp in imports]
    total = sum(len(c) for c in per_import)
    done = 0
    # إجماليّ كل قسم (قد يتكرّر مفتاح القسم عبر عدّة استيرادات → نجمعه).
    section_totals: dict[str, int] = {}
    for imp, cands in zip(imports, per_import):
        section_totals[imp["section"]] = \
            section_totals.get(imp["section"], 0) + len(cands)
    section_done: dict[str, int] = {}

    def _find_sr(key):
        for s in report.sections:      # دون إنشاء قسم فارغ (لا يُلوّث التقرير)
            if s.section == key:
                return s
        return None

    def _snapshot(current: str) -> dict:
        secs = []
        for k in COMMIT_ORDER:
            if k not in section_totals:
                continue
            sr = _find_sr(k)
            secs.append({
                "section": k, "label": section_label(k),
                "total": section_totals.get(k, 0),
                "done": section_done.get(k, 0),
                "created": sr.created if sr else 0,
                "merged": sr.merged if sr else 0,
                "skipped": sr.skipped if sr else 0,
                "failed": sr.failed if sr else 0,
            })
        return {"section_total": section_totals.get(current, 0),
                "section_done": section_done.get(current, 0),
                "sections": secs}

    def _tick(section_key, force=False):
        if progress_cb and (force or done % 200 == 0):
            try:
                progress_cb(done, total, section_key, "committing",
                            _snapshot(section_key))
            except TypeError:  # متلقٍّ قديم بأربعة معاملات — توافق خلفيّ.
                try:
                    progress_cb(done, total, section_key, "committing")
                except Exception:  # noqa: BLE001
                    pass
            except Exception:  # noqa: BLE001 — التقدّم لا يُجهض الاستيراد
                pass

    # خريطة المعرّفات: قسم → {مفتاح طبيعيّ مُطبَّع → id الهدف}. تُملأ بالموجود
    # ثمّ بما يُنشأ، فتُحلّ العلاقات للأبناء (الأب يُعالَج أولًا بترتيب الاعتماد).
    existing = _load_existing_keys(tenant_id)
    idmap: dict[str, dict[str, int]] = {k: dict(existing.get(k, {})) for k in COMMIT_ORDER}
    pw_flagged = 0

    for imp, cands in zip(imports, per_import):
        section_key = imp["section"]
        section = get_section(section_key)
        sr = report.section(section_key)
        mode = imp["mode"]
        seen: set[str] = set()
        _tick(section_key, force=True)
        try:
            for c in cands:
                done += 1
                section_done[section_key] = section_done.get(section_key, 0) + 1
                _tick(section_key)
                if not c.natural_key:
                    sr.skipped += 1
                    sr.errors.append({"key": c.source_ref or "",
                                      "action": "invalid",
                                      "reason": "بلا مفتاح طبيعيّ"})
                    continue
                if c.natural_key in seen:
                    sr.skipped += 1
                    continue
                seen.add(c.natural_key)
                try:
                    action, flagged = _commit_one(
                        tenant_id, section_key, c, mode, idmap, actor, dry_run)
                    if action == "created":
                        sr.created += 1
                    elif action == "merged":
                        sr.merged += 1
                    else:
                        sr.skipped += 1
                    pw_flagged += 1 if flagged else 0
                except _SkipRow as skip:      # تخطٍّ مُبرَّر — لا فشل.
                    sr.skipped += 1
                    sr.errors.append({"key": c.source_ref or c.natural_key,
                                      "action": "skipped", "reason": skip.reason})
                except Exception as exc:  # noqa: BLE001 — صفّ سيّئ لا يُجهض القسم
                    sr.failed += 1
                    sr.errors.append({"key": c.source_ref or c.natural_key,
                                      "action": "failed", "reason": str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001 — عطل بنيويّ يُجهض القسم فقط
            sr.errors.append({"key": "", "action": "section_failed",
                              "reason": str(exc)[:200]})
        _tick(section_key, force=True)

    # حدِّث عدّاد حاوية الكروت المستورَدة ليطابق العدد الفعليّ (كان 0 عند إنشائها).
    # ضبط عدّاد كل حزمة على العدد الفعليّ لكروتها (كل حزمة حقيقيّة + الحاوية
    # الاحتياطيّة إن وُجدت) — فتَعرض كل حزمة عدد كروتها الصحيح.
    if not dry_run:
        batch_ids = {int(v) for v in idmap.get(SEC_BATCHES, {}).values()
                     if v and v > 0}
        cbid = idmap.get("_meta", {}).get("card_batch_id")
        if cbid and cbid > 0:
            batch_ids.add(int(cbid))
        if batch_ids:
            try:
                from ...db.connection import transaction
                with transaction() as conn:
                    for bid in batch_ids:
                        # اضبط العدّاد على الكروت الفعليّة فقط للحِزم التي
                        # استقبلت كروتًا؛ الحِزم المطبوعة (0 كرت مستورَد) تُبقي
                        # كمّيّتها المُعلَنة (qty) بدل أن تُصفَّر.
                        conn.execute(
                            "UPDATE card_batches SET count=("
                            "SELECT COUNT(*) FROM cards WHERE batch_id=? "
                            "AND deleted_at IS NULL) WHERE tenant_id=? AND id=? "
                            "AND (SELECT COUNT(*) FROM cards WHERE batch_id=? "
                            "AND deleted_at IS NULL) > 0",
                            (bid, tenant_id, bid, bid))
            except Exception:  # noqa: BLE001 — عدّاد تجميليّ، لا يُجهض الالتزام
                pass

    if pw_flagged:
        report.warnings.append(
            f"{pw_flagged} كلمة مرور مُجزّأة (hash) حُفِظت كعلم في البيانات الوصفيّة — "
            "تتطلّب إعادة تعيين كي تعمل المصادقة (لم تُكسَر صامتةً).")
    # نبضة ختاميّة: «إنهاء… ضبط العدّادات» (بعد كتابة كل الأقسام).
    if progress_cb:
        try:
            progress_cb(done, total, "", "finalizing", _snapshot(""))
        except TypeError:
            try:
                progress_cb(done, total, "", "finalizing")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    return report


def _commit_one(tenant_id, section_key, c: Candidate, mode, idmap, actor,
                dry_run) -> tuple[str, bool]:
    """يُرجع (action, password_flagged). action ∈ created|merged|skipped."""
    handler = _COMMITTERS.get(section_key)
    if handler is None:
        return "skipped", False
    return handler(tenant_id, c, mode, idmap, actor, dry_run)


# ── committers لكل قسم ────────────────────────────────────────────────

def _commit_role(tenant_id, c, mode, idmap, actor, dry_run):
    from ...db.repos import admins_repo
    name = str(c.fields.get("name", "")).strip()
    existing = admins_repo.get_role_by_name(name)
    if existing:
        idmap[SEC_ROLES][c.natural_key] = int(existing.id)
        if mode == "skip" or dry_run:
            return "skipped" if mode == "skip" else "merged", False
        perms = _split_list(c.fields.get("permissions", ""))
        if perms:
            admins_repo.update_role(int(existing.id), permissions=tuple(perms))
        return "merged", False
    if dry_run:
        idmap[SEC_ROLES][c.natural_key] = _placeholder(idmap, SEC_ROLES)
        return "created", False
    role = admins_repo.create_role(
        name=name, display_name=str(c.fields.get("display_name", "") or name),
        permissions=tuple(_split_list(c.fields.get("permissions", ""))))
    idmap[SEC_ROLES][c.natural_key] = int(role.id)
    return "created", False


def _commit_manager(tenant_id, c, mode, idmap, actor, dry_run):
    from ...db.repos import admins_repo
    username = str(c.fields.get("username", "")).strip()
    role_id = _resolve(idmap, SEC_ROLES, c.fields.get("role"))
    existing = admins_repo.get_by_username(username)
    if existing:
        idmap[SEC_MANAGERS][c.natural_key] = int(existing.id)
        if mode == "skip" or dry_run:
            return ("skipped" if mode == "skip" else "merged"), False
        changes = {}
        for src, dst in (("full_name", "full_name"), ("email", "email"),
                         ("mobile", "mobile")):
            if c.fields.get(src):
                changes[dst] = c.fields[src]
        if role_id:
            changes["role_id"] = role_id
        if changes:
            admins_repo.update_admin(int(existing.id), **changes)
        return "merged", False
    if dry_run:
        idmap[SEC_MANAGERS][c.natural_key] = _placeholder(idmap, SEC_MANAGERS)
        return "created", False
    pw = str(c.fields.get("password", "") or "")
    if not pw or c.fields.get("password_scheme"):
        pw = secrets.token_urlsafe(9)            # كلمة عشوائيّة (تُعاد لاحقًا)
    admin = admins_repo.create_admin(
        username=username, password=pw,
        full_name=str(c.fields.get("full_name", "") or ""),
        email=str(c.fields.get("email", "") or ""),
        mobile=str(c.fields.get("mobile", "") or ""),
        role_id=role_id, is_super_admin=False)
    idmap[SEC_MANAGERS][c.natural_key] = int(admin.id)
    return "created", False


def _commit_distributor(tenant_id, c, mode, idmap, actor, dry_run):
    from ...db.repos import admins_repo
    from ...db.connection import db, transaction
    from ...db.helpers import now_iso
    name = str(c.fields.get("name", "")).strip()
    # ربط المدير المسؤول إن وُجد.
    admin_id = None
    mgr = str(c.fields.get("manager", "") or "").strip()
    if mgr:
        a = admins_repo.get_by_username(mgr)
        admin_id = int(a.id) if a else None
    row = db().execute(
        "SELECT id FROM distributors WHERE tenant_id=? AND name=?",
        (tenant_id, name)).fetchone()
    if row:
        idmap[SEC_DISTRIBUTORS][c.natural_key] = int(row["id"])
        if mode == "skip" or dry_run:
            return ("skipped" if mode == "skip" else "merged"), False
        with transaction() as conn:
            conn.execute(
                "UPDATE distributors SET display_name=?, email=?, phone=?, "
                "updated_at=? WHERE tenant_id=? AND id=?",
                (str(c.fields.get("display_name", "") or ""),
                 str(c.fields.get("email", "") or ""),
                 str(c.fields.get("phone", "") or ""),
                 now_iso(), tenant_id, int(row["id"])))
        return "merged", False
    if dry_run:
        idmap[SEC_DISTRIBUTORS][c.natural_key] = _placeholder(idmap, SEC_DISTRIBUTORS)
        return "created", False
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO distributors(tenant_id, admin_id, name, display_name, "
            "email, phone, status, balance, credit_limit, created_by, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tenant_id, admin_id, name,
             str(c.fields.get("display_name", "") or ""),
             str(c.fields.get("email", "") or ""),
             str(c.fields.get("phone", "") or ""),
             "active", _to_float(c.fields.get("balance")),
             _to_float(c.fields.get("credit_limit")), actor, now_iso()))
        idmap[SEC_DISTRIBUTORS][c.natural_key] = int(cur.lastrowid)
    return "created", False


def _plan_attrs(c) -> dict:
    """يحوّل حقول الباقة الخام إلى سمات AccessPlan عبر المحلّلات المتسامحة."""
    from . import valueparse as vp
    attrs: dict[str, Any] = {}
    pm = vp.parse_money(c.fields.get("price", ""))
    if pm.ok:
        attrs["price"] = float(pm.value)
    down = vp.parse_speed(c.fields.get("speed_down", "") or c.fields.get("speed", ""))
    if down.ok:
        attrs["speed_down_kbps"] = int(down.value)
    up = vp.parse_speed(c.fields.get("speed_up", ""))
    if up.ok:
        attrs["speed_up_kbps"] = int(up.value)
    q = vp.parse_data_size(c.fields.get("data_quota", ""))
    if q.ok:
        attrs["quota_total_mb"] = int(q.value)
    d = vp.parse_duration(c.fields.get("validity_days", ""))
    if d.ok:
        attrs["validity_days"] = int(d.value.get("days", 0))
    return attrs


def _commit_plan(tenant_id, c, mode, idmap, actor, dry_run):
    from ...db.repos import plans_repo
    from ...core.types import AccessPlan
    name = str(c.fields.get("name", "")).strip()
    attrs = _plan_attrs(c)
    existing_id = idmap[SEC_PLANS].get(c.natural_key)
    if existing_id and existing_id > 0:
        if mode == "skip" or dry_run:
            return ("skipped" if mode == "skip" else "merged"), False
        p = plans_repo.get_plan(tenant_id, existing_id)
        if p is not None:
            plans_repo.upsert_plan(replace(p, **attrs))
        return "merged", False
    if dry_run:
        idmap[SEC_PLANS][c.natural_key] = _placeholder(idmap, SEC_PLANS)
        return "created", False
    plan = AccessPlan(id=None, name=name, tenant_id=tenant_id,
                      description="مستورَد عبر معالج الترحيل", **attrs)
    saved = plans_repo.upsert_plan(plan)
    idmap[SEC_PLANS][c.natural_key] = int(saved.id)
    return "created", False


def _commit_batch(tenant_id, c, mode, idmap, actor, dry_run):
    from ...db.repos import cards_repo, admins_repo
    from ...core.types import CardBatch
    name = str(c.fields.get("name", "")).strip()
    plan_id = _resolve(idmap, SEC_PLANS, c.fields.get("plan")) or 0
    # Accounting mode + validity budget mapped from the source (FIX 2):
    # «طريقة الإحتساب (من أول اتصال)» → count_from_first_connect;
    # «صلاحية الكارت بعد أول اتصال» → time_value/time_unit. Only the adv
    # card_users builder carries them; other sources leave them absent.
    time_value = _to_int(c.fields.get("time_value")) or 0
    time_unit = str(c.fields.get("time_unit") or "days").strip() or "days"
    count_from_first = bool(c.fields.get("count_from_first_connect", True))
    count_by_secs = bool(c.fields.get("count_by_seconds", False))
    # هل يحمل هذا المرشّح بيانات احتساب مصدريّة أصلًا؟ (باني adv فقط يضعها؛
    # مصادر أخرى لا — فلا نَدوس حزمة قائمة بقيَم افتراضيّة).
    _has_accounting = ("count_from_first_connect" in c.fields
                       or c.fields.get("time_value") is not None)
    existing_id = idmap[SEC_BATCHES].get(c.natural_key)
    if existing_id and existing_id > 0:
        # Reconcile: refresh the drifted accounting mode + budget on the
        # existing batch so migrated card durations become correct
        # («البطاقات تنتقل مدتها صح / طريقة الاحتساب صح») — nothing else.
        # ملاحظة: تُحدَّث الميزانية فقط حين يحمل المصدر ميزانية (time_value
        # موجود) — غيابها لا يُصفّر ميزانية قائمة.
        if mode != "skip" and not dry_run and _has_accounting:
            try:
                from ...db.connection import transaction
                sets = ["count_from_first_connect=?", "count_by_seconds=?"]
                vals: list = [int(count_from_first), int(count_by_secs)]
                if c.fields.get("time_value") is not None:
                    sets += ["time_value=?", "time_unit=?"]
                    vals += [time_value, time_unit]
                with transaction() as conn:
                    conn.execute(
                        f"UPDATE card_batches SET {', '.join(sets)} "
                        "WHERE tenant_id=? AND id=?",
                        (*vals, tenant_id, int(existing_id)))
            except Exception:  # noqa: BLE001 — لا تَكسر الاستيراد بسبب حزمة
                pass
        return ("skipped" if mode == "skip" else "merged"), False
    # card_batches.plan_id غير قابل للـNULL وله FK لـaccess_plans — حزمة بلا
    # باقة محلولة تُتخطّى بسبب واضح بدل كسر قيد المفتاح الأجنبيّ.
    if not (plan_id and plan_id > 0):
        raise _SkipRow("الباقة غير معروفة للحزمة — لم تُنشأ الحزمة")
    if dry_run:
        idmap[SEC_BATCHES][c.natural_key] = _placeholder(idmap, SEC_BATCHES)
        return "created", False
    # المدير المُنشئ (created_by) المحلول لاسم دخول → معرّفه + اسمه على الحزمة.
    manager_id = 0
    created_by = actor
    mgr_login = str(c.fields.get("manager", "") or "").strip()
    if mgr_login and not mgr_login.isdigit():
        a = admins_repo.get_by_username(mgr_login)
        if a is not None:
            manager_id = int(a.id)
            created_by = mgr_login
    series = str(c.fields.get("_series", "") or "").strip().strip("-")
    notes = ("مستورَد عبر معالج الترحيل — سلسلة " + series) if series \
        else "مستورَد عبر معالج الترحيل"
    batch = CardBatch(id=None, batch_code="", plan_id=int(plan_id),
                      count=_to_int(c.fields.get("count")) or 0,
                      tenant_id=tenant_id, package_name=name,
                      price_per_card=_to_float(c.fields.get("price")),
                      manager_id=manager_id, created_by=created_by,
                      count_from_first_connect=count_from_first,
                      count_by_seconds=count_by_secs,
                      time_value=time_value, time_unit=time_unit,
                      source_type="imported", notes=notes)
    saved = cards_repo.create_batch(batch)
    idmap[SEC_BATCHES][c.natural_key] = int(saved.id)
    return "created", False


def _ensure_manager(tenant_id, raw_name, idmap, actor, dry_run) -> Optional[int]:
    """يشتقّ مديرًا من قيمة “انشئ بواسطة”/created_by: يُطابق الموجود أو يُنشئ
    مديرًا مبسّطًا (كلمة عشوائيّة، تُعاد لاحقًا) ويربطه. يُخزَّن في idmap.

    **لا يُنشئ مديرًا اسمه رقم**: قيمة رقميّة بحتة = معرّف/رقم مجموعة لم يُحَلّ
    لاسم مدير حقيقيّ (طبقة mapping تحلّه عادةً) — نتجاهله بدل فبركة مدير «6».
    """
    name = str(raw_name or "").strip()
    key = norm_key(name)
    if not key:
        return None
    if name.isdigit():                # معرّف رقميّ غير محلول — لا تُفبرِك مديرًا.
        # طابق مديرًا موجودًا بهذا المفتاح فقط (لو استُورد سابقًا)، وإلّا تجاهل.
        hit = idmap[SEC_MANAGERS].get(key)
        return hit if (hit and hit > 0) else None
    hit = idmap[SEC_MANAGERS].get(key)
    if hit and hit > 0:
        return hit
    if dry_run:
        ph = _placeholder(idmap, SEC_MANAGERS)
        idmap[SEC_MANAGERS][key] = ph
        return ph
    from ...db.repos import admins_repo
    existing = admins_repo.get_by_username(name)
    if existing is not None:
        idmap[SEC_MANAGERS][key] = int(existing.id)
        return int(existing.id)
    admin = admins_repo.create_admin(username=name, password=secrets.token_urlsafe(9),
                                     full_name=name, is_super_admin=False)
    idmap[SEC_MANAGERS][key] = int(admin.id)
    return int(admin.id)


# ════════════════════════════════════════════════════════════════════
# محلّلات حقول المشترك المتقدّمة (ماك / جدول الاتصال / وقت الاستخدام).
# نقيّة، مدفوعة بالسمة — تعمّم على أيّ دمب adv/Hobe-Hub (لا أرقام مبرمَجة).
# ════════════════════════════════════════════════════════════════════

# عنوان MAC: ستّ ثنائيّات سداسيّة مفصولة بـ«:» أو «-».
_MAC_RE = re.compile(r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}")

# أيّام الأسبوع (بادئة ثلاثيّة إنجليزيّة) → رمز HobeRadius.
_DAY_CODES = ("sat", "sun", "mon", "tue", "wed", "thu", "fri")
# قاعدة المالك: ``arr_days`` فارغ ⇒ كل الأيّام عدا الجمعة.
_DEFAULT_ALLOWED_DAYS = ("sat", "sun", "mon", "tue", "wed", "thu")


def _normalize_mac(raw) -> str:
    """توكِن واحد → MAC مُطبَّع (UPPER, «:») أو «»."""
    s = str(raw or "").strip()
    if not s:
        return ""
    m = _MAC_RE.search(s)
    if m:
        return m.group(0).upper().replace("-", ":")
    hexs = re.sub(r"[^0-9A-Fa-f]", "", s)     # 12 خانة بلا فواصل
    if len(hexs) == 12:
        return ":".join(hexs[i:i + 2] for i in range(0, 12, 2)).upper()
    return ""


def _parse_macs(raw) -> list[str]:
    """يستخرج كل عناوين MAC من قيمة خامّة: PHP-serialize
    (``a:1:{i:0;s:17:"AA:BB:…";}``)، أو CSV/JSON، أو عنوان مفرد. يُطبَّع كلّ
    عنوان (UPPER, «:») ويُزال المكرّر مع حفظ الترتيب. سلسلة فارغة (``s:0:""``)
    لا تُنتج عنوانًا."""
    s = str(raw or "").strip()
    if not s:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in _MAC_RE.findall(s):
        n = m.upper().replace("-", ":")
        if n not in seen:
            seen.add(n)
            out.append(n)
    if out:
        return out
    # لا نمط مفصول بـ«:»/«-» — جرّب توكِنات مفصولة (12-خانة خام محتملة).
    for tok in re.split(r"[,;\s|]+", s):
        n = _normalize_mac(tok)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _parse_clock(raw) -> str:
    """«8:00 AM» / «4:00 PM» / «16:00» / «08:00:00» → «HH:MM» (24 ساعة). قيمة
    غير صالحة/فارغة → «»."""
    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*([AaPp][Mm])?$", s)
    if not m:
        return ""
    h, mm, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ap == "am" and h == 12:
        h = 0
    elif ap == "pm" and h != 12:
        h += 12
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return ""
    return f"{h:02d}:{mm:02d}"


def _parse_arr_days(raw) -> Optional[list[str]]:
    """أيّام السماح من ``arr_days``. يفهم صيغة adv «Sat1,Sun1,…,Fri0» (بادئة
    يوم + بِت سماح)، وصيغة CSV لأسماء أيّام (السماح = الحضور)، وأيّ نصّ يحوي
    توكِنات أيّام. يُرجع قائمة رموز (بالترتيب القانونيّ)، أو ``None`` إن لم
    يُعثَر على أيّ يوم (فيُطبِّق المُتّصِل الافتراض)."""
    s = str(raw or "").strip()
    if not s:
        return None
    seen: set[str] = set()
    saw_any = False
    for mt in re.finditer(r"(sat|sun|mon|tue|wed|thu|fri)[a-z]*\s*[:=]?\s*([01])?",
                          s, re.I):
        saw_any = True
        code = mt.group(1).lower()
        bit = mt.group(2)
        if bit is None or bit == "1":     # بِت=1 أو غائب (الحضور=سماح) ⇒ مسموح
            seen.add(code)
    if not saw_any:
        return None
    return [d for d in _DAY_CODES if d in seen]


def _build_connection_schedule(days_raw, by_time_raw, from_raw, to_raw) -> str:
    """يبني ``connection_schedule`` (JSON) من حقول adv الخامّة وفق قاعدة المالك:

      • ``arr_days`` مضبوط ⇒ الأيّام المسموحة بالضبط؛ فارغ ⇒ كل الأيّام عدا
        الجمعة (Sat–Thu).
      • نافذة وقت واحدة (from→to) تنطبق على كل الأيّام المسموحة، وتُفعَّل حين
        ``limit_by_time`` مُشغَّل (أو — لدمب بلا هذا العلم — حين توفّر الطرفان).
      • كل الأيّام مسموحة وبلا نافذة ⇒ «» (بلا قيد)."""
    from ...core import access_schedule as _asch
    days = _parse_arr_days(days_raw)
    if not days:
        days = list(_DEFAULT_ALLOWED_DAYS)
    f, t = _parse_clock(from_raw), _parse_clock(to_raw)
    bt = str(by_time_raw or "").strip().lower()
    if bt in ("", "none"):
        window_on = bool(f and t)         # لا علم صريح → النافذة إن توفّر الطرفان
    else:
        window_on = bt in ("1", "on", "yes", "true", "enabled", "y")
    if not window_on:
        f = t = ""
    if set(days) >= set(_asch.DAYS) and not (f and t):
        return ""                         # كل الأيّام بلا نافذة = بلا قيد
    try:
        return _asch.serialize({"windows": [{"days": days, "from": f, "to": t}]})
    except Exception:  # noqa: BLE001 — جدول تجميليّ لا يُجهض المشترك
        return ""


def _used_seconds(raw) -> int:
    """«HH:MM:SS» / «HH:MM» / عدد → ثوانٍ. فارغ/غير صالح → 0."""
    s = str(raw or "").strip()
    if not s:
        return 0
    if ":" in s:
        try:
            nums = [int(p) for p in s.split(":")]
        except ValueError:
            return 0
        if len(nums) == 3:
            return nums[0] * 3600 + nums[1] * 60 + nums[2]
        if len(nums) == 2:
            return nums[0] * 3600 + nums[1] * 60
        if len(nums) == 1:
            return nums[0]
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _subscriber_meta(c) -> dict:
    """بيانات وصفيّة تُحفَظ ولا هدف مباشر لها في جدول subscribers."""
    from . import valueparse as vp
    meta: dict[str, Any] = {}
    for k in ("contract_no",):
        if c.fields.get(k):
            meta[k] = str(c.fields[k])
    exp = vp.parse_date(c.fields.get("expire_at", ""))
    if exp.ok:
        meta["expire_at_src"] = exp.value.isoformat()
    return meta


def _commit_subscriber(tenant_id, c, mode, idmap, actor, dry_run):
    from ...db.repos import subscribers_repo
    from ...core.types import Subscriber
    from . import valueparse as vp
    username = str(c.fields.get("username", "")).strip()
    plan_id = _resolve(idmap, SEC_PLANS, c.fields.get("plan"))
    manager_id = _ensure_manager(tenant_id, c.fields.get("manager"), idmap, actor, dry_run)
    manager_id = manager_id if (manager_id and manager_id > 0) else None
    password, flagged, pmeta = _resolve_password(c)
    bal = vp.parse_money(c.fields.get("balance", ""))
    remark = str(c.fields.get("notes", "") or "")
    extra_meta = _subscriber_meta(c)

    exp = vp.parse_date(c.fields.get("expire_at", ""))

    # ── الماك (allowed_macs/mac_lock/caller_id) + جدول الاتصال + وقت الاستخدام ──
    from ...core import access_schedule as _asch
    macs = _parse_macs(c.fields.get("mac"))
    macs_csv = ",".join(macs)                     # mac_lock يدعم قائمة (multi-MAC)
    first_mac = macs[0] if macs else ""
    schedule = _build_connection_schedule(
        c.fields.get("sched_days"), c.fields.get("sched_by_time"),
        c.fields.get("sched_from"), c.fields.get("sched_to"))
    used_secs = _used_seconds(c.fields.get("used_time"))

    def _text_changes() -> dict:
        ch: dict[str, Any] = {}
        for src, attr in (("full_name", "full_name"), ("father_name", "father_name"),
                          ("mobile", "mobile"), ("email", "email"),
                          ("static_ip", "static_ip"), ("address", "address")):
            if c.fields.get(src):
                ch[attr] = str(c.fields[src])
        # الماك: caller_id (أوّل عنوان) + mac_lock/allowed_macs (كلّها، مُطبَّعة).
        if first_mac:
            ch["caller_id"] = first_mac
        if macs_csv:
            ch["mac_lock"] = macs_csv
            ch["allowed_macs"] = macs_csv
        if schedule:
            ch["connection_schedule"] = schedule
            ch["working_days"] = _asch.derive_working_days(schedule)
        if used_secs:
            ch["used_seconds"] = used_secs
        if remark:
            ch["remark"] = remark
        if bal.ok:
            ch["balance"] = float(bal.value)
        # expire_at is imported whenever the source carries it — the primary
        # fix for «كله فعّال»: a past expiry makes the subscriber expired.
        if exp.ok:
            ch["expire_at"] = exp.value
        # Reconcile-SAFE status. Down-state evidence (explicit disable/block, an
        # explicit «expired», or a past expiry) is applied — this is what fixes
        # wrongly-active migrated subscribers. But an existing disabled/expired
        # subscriber is NEVER flipped back to enabled without POSITIVE evidence
        # (explicit enable flag or a FUTURE expiry): «المعطّل يظلّ معطّلًا».
        _src_status = c.fields.get("status", "")
        _derived = vp.derive_status(
            _src_status, expire_at=(exp.value if exp.ok else None), now=_now)
        _existing_status = (getattr(existing, "status", "") or "enabled")
        _explicit_enable = vp.status_signal(_src_status) == "enabled"
        _future_expiry = exp.ok and exp.value > _now
        if _derived == "disabled":
            # Explicit block from the source → apply.
            ch["status"] = "disabled"
        elif _existing_status == "disabled":
            # DISABLED IS STICKY: an existing block is never downgraded to
            # «منتهي» by expiry evidence, and reconcile NEVER auto-lifts it —
            # not even on a source 'enabled' flag, because in the source
            # (Hobe Hub) internet_status='enabled' is the default state of
            # every non-blocked row (expiry is derived from exp_time), so it
            # is not evidence of an intentional un-block. Un-blocking is a
            # manual admin action only. «المشترك المعطّل يضل معطّل».
            pass
        elif _derived == "expired":
            # Expiry evidence (explicit or past expire_at) → apply.
            ch["status"] = "expired"
        elif _existing_status == "expired":
            # Un-expire on renewal evidence: explicit enable OR a future expiry.
            if _explicit_enable or _future_expiry:
                ch["status"] = "enabled"
        else:
            ch["status"] = "enabled"
        return ch

    _now = datetime.utcnow()
    existing = subscribers_repo.get_subscriber(tenant_id, username)
    if existing is not None:
        idmap[SEC_SUBSCRIBERS][c.natural_key] = int(existing.id or 0)
        if mode == "skip" or dry_run:
            return ("skipped" if mode == "skip" else "merged"), flagged
        changes = _text_changes()
        if password:
            changes["password"] = password
        if plan_id:
            changes["plan_id"] = plan_id
        if manager_id:
            changes["manager_id"] = manager_id
        subscribers_repo.upsert_subscriber(replace(existing, **changes))
        return "merged", flagged
    if dry_run:
        idmap[SEC_SUBSCRIBERS][c.natural_key] = _placeholder(idmap, SEC_SUBSCRIBERS)
        return "created", flagged
    meta = dict(pmeta)
    if extra_meta:
        meta.setdefault("migration", {}).update(extra_meta)
    s = Subscriber(
        id=None, username=username, password=password, tenant_id=tenant_id,
        plan_id=plan_id, manager_id=manager_id,
        full_name=str(c.fields.get("full_name", "") or ""),
        father_name=str(c.fields.get("father_name", "") or ""),
        mobile=str(c.fields.get("mobile", "") or ""),
        email=str(c.fields.get("email", "") or ""),
        address=str(c.fields.get("address", "") or ""),
        # status derived from the source flag + expiry: a past expire_at
        # imports as 'expired' (not 'enabled') — the «كله فعّال» fix.
        status=vp.derive_status(c.fields.get("status", ""),
                                expire_at=(exp.value if exp.ok else None),
                                now=_now),
        caller_id=first_mac,
        mac_lock=macs_csv or None,
        allowed_macs=macs_csv,
        connection_schedule=schedule,
        working_days=_asch.derive_working_days(schedule) if schedule else "",
        used_seconds=used_secs,
        static_ip=str(c.fields.get("static_ip", "") or ""),
        remark=remark,
        balance=float(bal.value) if bal.ok else 0.0,
        expire_at=exp.value if exp.ok else None,
        metadata=json.dumps(meta, ensure_ascii=False) if meta else "{}")
    saved = subscribers_repo.upsert_subscriber(s)
    idmap[SEC_SUBSCRIBERS][c.natural_key] = int(saved.id or 0)
    _best_effort_router_sync(saved)
    return "created", flagged


def _ensure_import_card_batch(tenant_id, idmap, actor, dry_run):
    """يضمن حزمة «كروت مستورَدة» واحدة (حاوية) لعقد الكروت المستورَدة — إذ
    ``cards.batch_id``/``plan_id`` غير قابلين للـNULL. يُخزَّن في idmap['_meta']."""
    meta = idmap.setdefault("_meta", {})
    if meta.get("card_batch_id"):
        return meta["card_batch_id"], meta.get("card_batch_plan", 0)
    default_plan = next((v for v in idmap.get(SEC_PLANS, {}).values() if v and v > 0), 0)
    if not default_plan:
        # لا باقة في المصدر (قائمة قسائم صرفة) — cards.plan_id غير قابل للـNULL،
        # فننشئ باقة احتياطيّة كي تُستورَد القسائم بدل رفضها.
        if dry_run:
            default_plan = _placeholder(idmap, SEC_PLANS)
        else:
            from ...db.repos import plans_repo
            from ...core.types import AccessPlan
            saved = plans_repo.upsert_plan(AccessPlan(
                id=None, name=_IMPORT_PLAN_NAME, tenant_id=tenant_id))
            default_plan = int(saved.id)
        idmap.setdefault(SEC_PLANS, {})[_IMPORT_PLAN_KEY] = default_plan
    # أعِد استعمال الحاوية الموجودة (إعادة استيراد جزئيّ) بدل تكرارها.
    existing_id = idmap.get(SEC_BATCHES, {}).get(_IMPORT_BATCH_KEY)
    if existing_id and existing_id > 0:
        meta["card_batch_id"] = int(existing_id)
        meta["card_batch_plan"] = default_plan
        return int(existing_id), default_plan
    if dry_run:
        meta["card_batch_id"] = -1
        meta["card_batch_plan"] = default_plan
        return -1, default_plan
    from ...db.repos import cards_repo
    from ...core.types import CardBatch
    b = CardBatch(id=None, batch_code="", plan_id=int(default_plan), count=0,
                  tenant_id=tenant_id, package_name=_IMPORT_BATCH_NAME,
                  created_by=actor,
                  notes="حاوية الكروت المستورَدة عبر معالج الترحيل")
    saved = cards_repo.create_batch(b)
    meta["card_batch_id"] = int(saved.id)
    meta["card_batch_plan"] = default_plan
    idmap.setdefault(SEC_BATCHES, {})[_IMPORT_BATCH_KEY] = int(saved.id)
    return int(saved.id), default_plan


def _commit_card(tenant_id, c, mode, idmap, actor, dry_run):
    """يكتب الكرت في جدول ``cards`` الحقيقيّ (لا subscribers) مرتبطًا بحزمة
    وباقة (كلاهما NOT NULL FK)، فيظهر تحت صفحات الكروت كأيّ كرت."""
    from ...db.connection import db, transaction
    from ...db.helpers import now_iso
    from . import valueparse as vp
    username = str(c.fields.get("username", "")).strip()
    password, flagged, _meta = _resolve_password(c)
    plan_id = _resolve(idmap, SEC_PLANS, c.fields.get("plan"))
    key = c.natural_key
    # الحزمة الحقيقيّة للكرت (من card_users عبر id_card → اسم الحزمة). حاوية
    # احتياطيّة فقط للكروت التي لا سلسلة لها (تُقلَّل للصفر عند توفّر card_users).
    real_batch = _resolve(idmap, SEC_BATCHES, c.fields.get("batch"))

    existing_id = idmap[SEC_CARDS].get(key)
    if existing_id and existing_id > 0:
        if mode == "skip" or dry_run:
            return ("skipped" if mode == "skip" else "merged"), flagged
        sets, vals = [], []
        if password:
            sets.append("password=?")
            vals.append(password)
        if plan_id:
            sets.append("plan_id=?")
            vals.append(int(plan_id))
        # إعادة التشغيل تُصلِح: تنقل الكرت من الحاوية المُحشورة إلى حزمته الحقيقيّة.
        if real_batch:
            sets.append("batch_id=?")
            vals.append(int(real_batch))
        if sets:
            with transaction() as conn:
                conn.execute(f"UPDATE cards SET {', '.join(sets)} "
                             "WHERE tenant_id=? AND id=?",
                             (*vals, tenant_id, existing_id))
        return "merged", flagged

    if real_batch and plan_id:
        batch_id, card_plan = real_batch, plan_id
    else:
        batch_id, batch_plan = _ensure_import_card_batch(
            tenant_id, idmap, actor, dry_run)
        card_plan = plan_id or batch_plan
    if not batch_id or not card_plan:
        raise _SkipRow("لا توجد باقة/حزمة صالحة للكرت — لم يُستورَد")
    if dry_run:
        idmap[SEC_CARDS][key] = _placeholder(idmap, SEC_CARDS)
        return "created", flagged
    exp = vp.parse_date(c.fields.get("expire_at", ""))
    exp_iso = exp.value.isoformat() if exp.ok else None
    with transaction() as conn:
        cur = conn.execute(
            "INSERT INTO cards(tenant_id, batch_id, username, password, plan_id, "
            "used, expire_at, revoked, created_at) VALUES(?,?,?,?,?,0,?,0,?)",
            (tenant_id, int(batch_id), username, password, int(card_plan),
             exp_iso, now_iso()))
        idmap[SEC_CARDS][key] = int(cur.lastrowid)
    return "created", flagged


_COMMITTERS = {
    SEC_ROLES: _commit_role,
    SEC_MANAGERS: _commit_manager,
    SEC_DISTRIBUTORS: _commit_distributor,
    SEC_PLANS: _commit_plan,
    SEC_BATCHES: _commit_batch,
    SEC_SUBSCRIBERS: _commit_subscriber,
    SEC_CARDS: _commit_card,
}


# ════════════════════════════════════════════════════════════════════
# مساعدات
# ════════════════════════════════════════════════════════════════════

def _load_existing_keys(tenant_id: int) -> dict[str, dict[str, int]]:
    """خرائط مفتاح-طبيعيّ-مُطبَّع → id لكل قسم (للمطابقة وحلّ العلاقات)."""
    out: dict[str, dict[str, int]] = {k: {} for k in COMMIT_ORDER}
    try:
        from ...db.repos import plans_repo
        for p in plans_repo.list_plans(tenant_id, limit=10000):
            out[SEC_PLANS][norm_key(p.name)] = int(p.id)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...db.repos import admins_repo
        for r in admins_repo.list_roles():
            out[SEC_ROLES][norm_key(r.name)] = int(r.id)
        for a in admins_repo.list_admins():
            out[SEC_MANAGERS][norm_key(a.username)] = int(a.id)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...db.repos import cards_repo
        for b in cards_repo.list_batches(tenant_id, limit=10000):
            key = norm_key(b.package_name or b.batch_code)
            if key:
                out[SEC_BATCHES][key] = int(b.id)
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...db.connection import db
        for row in db().execute(
                "SELECT id, name FROM distributors WHERE tenant_id=?",
                (tenant_id,)).fetchall():
            out[SEC_DISTRIBUTORS][norm_key(row["name"])] = int(row["id"])
    except Exception:  # noqa: BLE001
        pass
    try:
        from ...db.connection import db
        for row in db().execute(
                "SELECT id, username FROM subscribers "
                "WHERE tenant_id=? AND deleted_at IS NULL", (tenant_id,)).fetchall():
            key = norm_key(row["username"])
            if key:
                out[SEC_SUBSCRIBERS][key] = int(row["id"])
    except Exception:  # noqa: BLE001
        pass
    try:
        # الكروت في جدول cards المستقلّ (لا subscribers) — للـidempotency.
        from ...db.connection import db
        for row in db().execute(
                "SELECT id, username FROM cards WHERE tenant_id=?",
                (tenant_id,)).fetchall():
            key = norm_key(row["username"])
            if key:
                out[SEC_CARDS][key] = int(row["id"])
    except Exception:  # noqa: BLE001
        pass
    return out


def _resolve(idmap, section_key, raw_value) -> Optional[int]:
    key = norm_key(str(raw_value or ""))
    if not key:
        return None
    v = idmap.get(section_key, {}).get(key)
    return v if (v and v > 0) else None


def _resolve_password(c: Candidate) -> tuple[str, bool, dict]:
    """يُرجع (كلمة للتخزين, مُعلَّمة?, metadata). كلمة مُجزّأة → فارغة + علم."""
    pw = str(c.fields.get("password", "") or "")
    scheme = c.fields.get("password_scheme")
    if scheme:
        return "", True, {"migration": {"password_scheme": scheme,
                                        "password_hash": pw,
                                        "needs_reset": True}}
    return pw, False, {}


def _best_effort_router_sync(saved) -> None:
    try:
        from ...integration.router_sync import enqueue_subscriber_upsert
        enqueue_subscriber_upsert(saved)
    except Exception:  # noqa: BLE001 — محفوظ في DB، مزامنة الراوتر تتبع
        pass


# نطاقُ المعرّفات النائبة في dry_run. **موجبٌ عمدًا**: `_resolve` وحرّاسُ
# العلاقات يشترطون معرّفًا موجبًا (`v > 0`)، والنائبُ السالبُ كان يسقُط منها
# جميعًا — فتُعلن المعاينةُ «الباقة غير معروفة للحزمة» لكلّ حزمة، وهي معاينةٌ
# **كاذبة**: التنفيذُ الحقيقيّ ينجح لأنّ معرّفاته حقيقيّةٌ موجبة. (بلاغ
# 2026-08-25: دمب adv بـ125 حزمةً ظهرت كلُّها «متخطّاة» فبدا أنّ 92,435 كرتًا
# ستنحشر في حاويةٍ واحدة.) والنطاقُ عالٍ كي لا يلتبس بمعرّفٍ حقيقيّ، وآمنٌ
# لأنّ dry_run لا يكتب صفًّا واحدًا — انظر حارس `if not dry_run` في `commit`.
_DRY_ID_BASE = 1_000_000_000


def _placeholder(idmap, section_key) -> int:
    """معرّفٌ نائبٌ مؤقّتٌ في dry_run كي تُحلّ علاقات الأبناء («سيُنشأ أب جديد»)."""
    return _DRY_ID_BASE + len(idmap.get(section_key, {})) + 1


def _rank(section_key: str) -> int:
    s = get_section(section_key)
    return s.depends_rank if s else 99


def _safe_preview(c: Candidate) -> dict:
    out = {}
    for k, v in c.fields.items():
        if k in ("password", "password_scheme", "password_hash"):
            if k == "password" and v:
                out["password"] = "•••"          # لا نُسرّب الكلمة للواجهة
            continue
        out[k] = v
    return out


def _split_list(raw) -> list[str]:
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    s = str(raw or "").strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [str(x).strip() for x in arr if str(x).strip()]
        except (ValueError, TypeError):
            pass
    return [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]


def _to_float(v, default: float = 0.0) -> float:
    try:
        s = str(v).strip().replace(",", "")
        return float(s) if s else default
    except (ValueError, TypeError):
        return default


def _to_int(v, default: int = 0) -> int:
    try:
        s = str(v).strip().replace(",", "")
        return int(float(s)) if s else default
    except (ValueError, TypeError):
        return default


def _field_label(section, target: str) -> str:
    labels = {"username": "اسم المستخدم", "name": "الاسم", "plan": "الباقة",
              "role": "الدور", "manager": "المدير", "batch": "الحزمة"}
    return labels.get(target, target)


def _section_label(section_key: str) -> str:
    s = get_section(section_key)
    return s.label_ar if s else section_key


__all__ = ["analyze", "build_plan", "commit"]
