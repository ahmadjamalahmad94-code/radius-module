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
import secrets
from dataclasses import replace
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


# ════════════════════════════════════════════════════════════════════
# (1) التحليل — للقراءة فقط، نقيّ
# ════════════════════════════════════════════════════════════════════

def analyze(file_bytes: bytes, filename: str = "") -> AnalysisResult:
    return _finish_analyze(sources.introspect(file_bytes, filename))


def analyze_path(path: str, filename: str = "") -> AnalysisResult:
    """كـ:func:`analyze` لكن يقرأ من القرص بتدفّق — للملفّات الكبيرة/gzip
    (تفريغ SQL بمئات الميغابايت) دون تحميلها كاملةً في الذاكرة."""
    return _finish_analyze(sources.introspect_path(path, filename))


def _finish_analyze(dataset) -> AnalysisResult:
    matches = classify.classify_dataset(dataset)
    res = AnalysisResult(dataset=dataset, matches=matches)
    if not matches and dataset.tables:
        res.warnings.append(
            "لم يُتعرَّف تلقائيًّا على أيّ قسم — يمكنك ربط الأعمدة يدويًّا.")
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

def commit(tenant_id: int, dataset, matches: list[SectionMatch], *,
           selections: Optional[list[dict]] = None,
           dry_run: bool = False, actor: str = "migration") -> ImportReport:
    imports = _imports_from(matches, selections)
    imports.sort(key=lambda i: _rank(i["section"]))
    report = ImportReport(dry_run=dry_run)

    # خريطة المعرّفات: قسم → {مفتاح طبيعيّ مُطبَّع → id الهدف}. تُملأ بالموجود
    # ثمّ بما يُنشأ، فتُحلّ العلاقات للأبناء (الأب يُعالَج أولًا بترتيب الاعتماد).
    existing = _load_existing_keys(tenant_id)
    idmap: dict[str, dict[str, int]] = {k: dict(existing.get(k, {})) for k in COMMIT_ORDER}
    pw_flagged = 0

    for imp in imports:
        section_key = imp["section"]
        section = get_section(section_key)
        sr = report.section(section_key)
        mode = imp["mode"]
        seen: set[str] = set()
        try:
            for c in _candidates_for(dataset, matches, imp):
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
                except Exception as exc:  # noqa: BLE001 — صفّ سيّئ لا يُجهض القسم
                    sr.failed += 1
                    sr.errors.append({"key": c.source_ref or c.natural_key,
                                      "action": "failed", "reason": str(exc)[:200]})
        except Exception as exc:  # noqa: BLE001 — عطل بنيويّ يُجهض القسم فقط
            sr.errors.append({"key": "", "action": "section_failed",
                              "reason": str(exc)[:200]})

    if pw_flagged:
        report.warnings.append(
            f"{pw_flagged} كلمة مرور مُجزّأة (hash) حُفِظت كعلم في البيانات الوصفيّة — "
            "تتطلّب إعادة تعيين كي تعمل المصادقة (لم تُكسَر صامتةً).")
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
    from ...db.repos import cards_repo
    from ...core.types import CardBatch
    name = str(c.fields.get("name", "")).strip()
    plan_id = _resolve(idmap, SEC_PLANS, c.fields.get("plan")) or 0
    existing_id = idmap[SEC_BATCHES].get(c.natural_key)
    if existing_id and existing_id > 0:
        return ("skipped" if mode == "skip" else "merged"), False
    if dry_run:
        idmap[SEC_BATCHES][c.natural_key] = _placeholder(idmap, SEC_BATCHES)
        return "created", False
    batch = CardBatch(id=None, batch_code="", plan_id=int(plan_id),
                      count=_to_int(c.fields.get("count")) or 0,
                      tenant_id=tenant_id, package_name=name,
                      price_per_card=_to_float(c.fields.get("price")),
                      created_by=actor, notes="مستورَد عبر معالج الترحيل")
    saved = cards_repo.create_batch(batch)
    idmap[SEC_BATCHES][c.natural_key] = int(saved.id)
    return "created", False


def _ensure_manager(tenant_id, raw_name, idmap, actor, dry_run) -> Optional[int]:
    """يشتقّ مديرًا من قيمة “انشئ بواسطة”/created_by: يُطابق الموجود أو يُنشئ
    مديرًا مبسّطًا (كلمة عشوائيّة، تُعاد لاحقًا) ويربطه. يُخزَّن في idmap."""
    name = str(raw_name or "").strip()
    key = norm_key(name)
    if not key:
        return None
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

    def _text_changes() -> dict:
        ch: dict[str, Any] = {}
        for src, attr in (("full_name", "full_name"), ("father_name", "father_name"),
                          ("mobile", "mobile"), ("email", "email"),
                          ("mac", "caller_id"), ("static_ip", "static_ip"),
                          ("address", "address")):
            if c.fields.get(src):
                ch[attr] = str(c.fields[src])
        if remark:
            ch["remark"] = remark
        if c.fields.get("status"):
            ch["status"] = vp.parse_status(str(c.fields["status"]))
        if bal.ok:
            ch["balance"] = float(bal.value)
        return ch

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
        status=vp.parse_status(str(c.fields.get("status", "") or "enabled")),
        caller_id=str(c.fields.get("mac", "") or ""),
        static_ip=str(c.fields.get("static_ip", "") or ""),
        remark=remark,
        balance=float(bal.value) if bal.ok else 0.0,
        metadata=json.dumps(meta, ensure_ascii=False) if meta else "{}")
    saved = subscribers_repo.upsert_subscriber(s)
    idmap[SEC_SUBSCRIBERS][c.natural_key] = int(saved.id or 0)
    _best_effort_router_sync(saved)
    return "created", flagged


def _commit_card(tenant_id, c, mode, idmap, actor, dry_run):
    # الكرت = مشترك من نوع card مرتبط بحزمة. نعيد استعمال مسار المشترك مع
    # user_type=card و card_batch_id محلولًا.
    from ...db.repos import subscribers_repo
    from ...core.types import Subscriber
    username = str(c.fields.get("username", "")).strip()
    plan_id = _resolve(idmap, SEC_PLANS, c.fields.get("plan"))
    batch_id = _resolve(idmap, SEC_BATCHES, c.fields.get("batch"))
    password, flagged, meta = _resolve_password(c)
    existing = subscribers_repo.get_subscriber(tenant_id, username)
    if existing is not None:
        if mode == "skip" or dry_run:
            return ("skipped" if mode == "skip" else "merged"), flagged
        changes: dict[str, Any] = {}
        if password:
            changes["password"] = password
        if plan_id:
            changes["plan_id"] = plan_id
        if batch_id:
            changes["card_batch_id"] = batch_id
        subscribers_repo.upsert_subscriber(replace(existing, **changes))
        return "merged", flagged
    if dry_run:
        return "created", flagged
    s = Subscriber(id=None, username=username, password=password,
                   tenant_id=tenant_id, plan_id=plan_id, user_type="card",
                   card_batch_id=batch_id,
                   metadata=json.dumps(meta, ensure_ascii=False) if meta else "{}")
    subscribers_repo.upsert_subscriber(s)
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
        # المشتركون والكروت يتشاركان جدول subscribers (المفتاح username).
        from ...db.connection import db
        for row in db().execute(
                "SELECT id, username FROM subscribers "
                "WHERE tenant_id=? AND deleted_at IS NULL", (tenant_id,)).fetchall():
            key = norm_key(row["username"])
            if key:
                out[SEC_SUBSCRIBERS][key] = int(row["id"])
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


def _placeholder(idmap, section_key) -> int:
    # معرّف سالب مؤقّت في dry_run كي تُحلّ علاقات الأبناء («سيُنشأ أب جديد»).
    return -(len(idmap.get(section_key, {})) + 1)


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
