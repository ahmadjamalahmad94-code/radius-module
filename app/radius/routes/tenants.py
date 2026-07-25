"""routes إدارة الـ tenants — للأدمن super_admin بشكل أساسي."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from ..core.errors import RadiusError
from ..core.system_config import default_currency
from ..core.tenant import (
    TIER_LIMITS, Tenant,
    TENANT_STATUS_ACTIVE, TENANT_STATUS_SUSPENDED, TENANT_STATUS_TRIAL, TENANT_STATUS_CLOSED,
    TENANT_TIER_STARTER, TENANT_TIER_PRO, TENANT_TIER_ENTERPRISE,
)
from ..services.tenants import get_tenants_service


TIER_KEYS = (TENANT_TIER_STARTER, TENANT_TIER_PRO, TENANT_TIER_ENTERPRISE)
STATUS_KEYS = (TENANT_STATUS_ACTIVE, TENANT_STATUS_TRIAL, TENANT_STATUS_SUSPENDED, TENANT_STATUS_CLOSED)


def register_tenants_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/tenants", "tenants_list", tenants_list, methods=["GET"])
    bp.add_url_rule("/tenants/new", "tenants_new", tenants_new, methods=["GET"])
    bp.add_url_rule("/tenants", "tenants_create", tenants_create, methods=["POST"])
    bp.add_url_rule("/tenants/<int:tenant_id>/edit", "tenants_edit", tenants_edit, methods=["GET"])
    bp.add_url_rule("/tenants/<int:tenant_id>", "tenants_update", tenants_update, methods=["POST"])
    # MT8 — أفعال سريعة لإدارة التجارب من القائمة.
    bp.add_url_rule("/tenants/<int:tenant_id>/trial-extend", "tenants_trial_extend",
                    tenants_trial_extend, methods=["POST"])
    bp.add_url_rule("/tenants/<int:tenant_id>/toggle-suspend", "tenants_toggle_suspend",
                    tenants_toggle_suspend, methods=["POST"])
    # MT33 — حذف شبكة نهائيًّا: صفحة تأكيد (GET) ثم التنفيذ (POST).
    bp.add_url_rule("/tenants/<int:tenant_id>/delete", "tenants_delete_confirm",
                    tenants_delete_confirm, methods=["GET"])
    bp.add_url_rule("/tenants/<int:tenant_id>/delete", "tenants_delete",
                    tenants_delete, methods=["POST"])
    # MT18 — لوحة إدارة الاستضافة (هبوط المزوّد/المالك).
    bp.add_url_rule("/provider", "provider_home", provider_home, methods=["GET"])
    # MT41 — صفحة ملفّ شبكة واحدة: استهلاك + فوترة + مراسلات + سجلّ نشاط.
    bp.add_url_rule("/provider/network/<int:tenant_id>", "provider_network_profile",
                    provider_network_profile, methods=["GET"])
    # MT47/MT49 — إدارة فئات الاشتراك الديناميكيّة + تجاوز حدود شبكة.
    bp.add_url_rule("/provider/tiers", "provider_tiers", provider_tiers, methods=["GET"])
    bp.add_url_rule("/provider/tiers/save", "provider_tiers_save",
                    provider_tiers_save, methods=["POST"])
    bp.add_url_rule("/provider/tiers/add", "provider_tiers_add",
                    provider_tiers_add, methods=["POST"])
    bp.add_url_rule("/provider/tiers/<key>/delete", "provider_tiers_delete",
                    provider_tiers_delete, methods=["POST"])
    bp.add_url_rule("/provider/network/<int:tenant_id>/limits",
                    "provider_network_limits", provider_network_limits, methods=["POST"])
    # MT57 — عروض الأسعار المعروضة على صفحة المنصّة (إضافة/تعديل/حذف/ترتيب).
    bp.add_url_rule("/provider/offers", "provider_offers", provider_offers, methods=["GET"])
    bp.add_url_rule("/provider/offers/save", "provider_offers_save",
                    provider_offers_save, methods=["POST"])
    bp.add_url_rule("/provider/offers/add", "provider_offers_add",
                    provider_offers_add, methods=["POST"])
    bp.add_url_rule("/provider/offers/<key>/delete", "provider_offers_delete",
                    provider_offers_delete, methods=["POST"])
    bp.add_url_rule("/provider/offers/<key>/move/<direction>", "provider_offers_move",
                    provider_offers_move, methods=["POST"])
    # MT46 — لوحة شحن الشبكات: رصيد + أيّام مدفوعة/مجانيّة + تمديد.
    bp.add_url_rule("/provider/topup", "provider_topup", provider_topup, methods=["GET"])
    bp.add_url_rule("/provider/topup/<int:tenant_id>/credit", "provider_topup_credit",
                    provider_topup_credit, methods=["POST"])
    bp.add_url_rule("/provider/topup/<int:tenant_id>/paid-days", "provider_topup_paid",
                    provider_topup_paid, methods=["POST"])
    bp.add_url_rule("/provider/topup/<int:tenant_id>/free-days", "provider_topup_free",
                    provider_topup_free, methods=["POST"])
    # MT36 — إغلاق طلب اشتراك وارد من صفحة هبوط المنصّة.
    bp.add_url_rule("/signup-requests/<int:request_id>/dismiss",
                    "signup_request_dismiss", signup_request_dismiss,
                    methods=["POST"])


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _dyn_tiers():
    """MT49 — الفئات الديناميكيّة (قائمة dicts كاملة) لنموذج الإنشاء."""
    try:
        from ..services import tier_config as tc
        return tc.get_tiers()
    except Exception:  # noqa: BLE001
        return []


def _tenant_usage() -> tuple[dict, dict, dict, dict]:
    """MT18 — استهلاك كل جهة (مشتركون/NAS/متصلون + مشغّل) باستعلامات مجمّعة."""
    usage_subs: dict = {}
    usage_nas: dict = {}
    usage_online: dict = {}
    operators: dict = {}
    try:
        from ..db.connection import db
        for r in db().execute(
                "SELECT tenant_id, COUNT(*) AS n FROM subscribers "
                "WHERE deleted_at IS NULL AND COALESCE(user_type,'') != 'card' "
                "GROUP BY tenant_id"):
            usage_subs[int(r["tenant_id"])] = int(r["n"])
        for r in db().execute(
                "SELECT tenant_id, COUNT(*) AS n FROM nas_devices "
                "WHERE deleted_at IS NULL GROUP BY tenant_id"):
            usage_nas[int(r["tenant_id"])] = int(r["n"])
        for r in db().execute(
                "SELECT tenant_id, COUNT(*) AS n FROM radacct "
                "WHERE acctstoptime IS NULL AND COALESCE(username,'') != '' "
                "GROUP BY tenant_id"):
            usage_online[int(r["tenant_id"])] = int(r["n"])
        # أوّل مدير (غير سوبر) لكل جهة — للعرض في لوحة المزوّد.
        for r in db().execute(
                "SELECT m.tenant_id AS tid, a.username AS u FROM tenant_memberships m "
                "JOIN admins a ON a.id = m.admin_id "
                "WHERE COALESCE(a.is_super_admin,0)=0 AND a.enabled=1 "
                "GROUP BY m.tenant_id"):
            operators.setdefault(int(r["tid"]), r["u"])
    except Exception:  # noqa: BLE001 — عدّادات عرضية، لا تكسر الصفحة
        pass
    return usage_subs, usage_nas, usage_online, operators


def tenants_list():
    from datetime import datetime
    # MT39 — القائمة الغنيّة صارت المصدر الوحيد لجدول الشبكات (حُذف جدول
    # «نظرة عامة» المكرّر). نستثني الشبكة ١ هنا كما تَستثنيها اللوحة: هي
    # مساحة المزوّد نفسه لا شبكة عميل — وكان العدّان يَتضاربان (٣ مقابل ٤)
    # بلا تفسيرٍ يراه المستخدم.
    items = [t for t in get_tenants_service().list() if t.id != 1]
    usage_subs, usage_nas, usage_online, _ = _tenant_usage()
    return render_template("radius/tenants_list.html", items=items,
                           tier_limits=TIER_LIMITS, now_utc=datetime.utcnow(),
                           usage_subs=usage_subs, usage_nas=usage_nas,
                           usage_online=usage_online)


def provider_home():
    """MT18 — لوحة إدارة الاستضافة: كل الجهات + فوترتها + صحة النظام + النسخ.
    owner-only (محروسة بـ_PERM_SUPER في blueprint)."""
    from datetime import datetime
    from ..core.tenant import (TENANT_STATUS_ACTIVE, TENANT_STATUS_TRIAL,
                               TENANT_STATUS_SUSPENDED, TENANT_STATUS_CLOSED)
    items = [t for t in get_tenants_service().list() if t.id != 1]  # نستثني مساحة المزوّد
    usage_subs, usage_nas, usage_online, operators = _tenant_usage()
    now = datetime.utcnow()

    # ملخّص KPI
    kpis = {
        "total":     len(items),
        "active":    sum(1 for t in items if t.status == TENANT_STATUS_ACTIVE),
        "trial":     sum(1 for t in items if t.status == TENANT_STATUS_TRIAL),
        "suspended": sum(1 for t in items if t.status == TENANT_STATUS_SUSPENDED),
        "paid":      sum(1 for t in items if (t.billing_mode or "free") == "paid"),
        "subs_total":   sum(usage_subs.get(t.id, 0) for t in items),
        "online_total": sum(usage_online.get(t.id, 0) for t in items),
    }


    # صحة النظام (owner-only)
    try:
        from ..services.dashboard_metrics import get_system_health
        system = get_system_health()
    except Exception:  # noqa: BLE001
        system = {}

    # النسخ الاحتياطي المحلّي
    try:
        from ..services.operations import get_operations_service
        ops = get_operations_service()
        backups = ops.list_local_backups(tenant_id=1)[:5]
        backup_count = len(ops.list_local_backups(tenant_id=1))
    except Exception:  # noqa: BLE001
        backups, backup_count = [], 0

    # MT36 — طلبات الاشتراك الواردة من صفحة هبوط المنصّة. المُعلَّقة فقط
    # تُعرَض هنا؛ المُعالَجة تبقى في القاعدة للأثر. فشل القراءة لا يُسقط
    # اللوحة (قد لا يكون الترحيل 167 طُبّق بعد على نسخةٍ قديمة).
    try:
        from ..db.repos import signup_requests_repo
        signups = signup_requests_repo.list_all(status="pending", limit=50)
    except Exception:  # noqa: BLE001
        signups = []

    # ── MT38 موجة ١: المال + «يحتاج انتباهك» ──────────────────────
    # تُحسَب بعد ``signups`` لأنّها تَدخل في قائمة الانتباه.
    money, attention = _provider_money_and_attention(
        items, usage_subs, usage_nas, now, signups)

    # ── MT42 موجة ٣: نموّ الشبكات + سجلّ نشاط المنصّة ──────────────
    growth = _provider_growth(items, now)
    platform_activity = _provider_platform_activity(items, now)

    return render_template(
        "radius/provider_home.html",
        items=items, now_utc=now, tier_limits=TIER_LIMITS,
        usage_subs=usage_subs, usage_nas=usage_nas, usage_online=usage_online,
        operators=operators, kpis=kpis, system=system,
        backups=backups, backup_count=backup_count, signups=signups,
        money=money, attention=attention,
        growth=growth, platform_activity=platform_activity)


def _provider_growth(items, now):
    """MT42 — عدد الشبكات المُنشأة في كلّ من آخر ٦ أشهر (لرسمٍ صغير).

    نَعتمد ``created_at`` الحقيقيّ؛ الشبكات بلا تاريخ (قديمة قبل العمود)
    تُنسَب لأقدم شهر كي لا تختفي من المجموع، لكن لا تُنفخ شهرًا بعينه.
    """
    from datetime import datetime

    def _month_key(dt):
        return dt.year * 12 + (dt.month - 1)

    cur = _month_key(now)
    labels, buckets = [], {}
    for i in range(5, -1, -1):
        k = cur - i
        y, m = divmod(k, 12)
        labels.append(f"{y}-{m + 1:02d}")
        buckets[k] = 0

    oldest_key = cur - 5
    total = 0
    for t in items:
        total += 1
        c = getattr(t, "created_at", None)
        if isinstance(c, datetime):
            k = _month_key(c)
        else:
            k = None
        if k is None or k < oldest_key:
            k = oldest_key
        if k > cur:
            k = cur
        buckets[k] = buckets.get(k, 0) + 1

    series = [buckets[cur - i] for i in range(5, -1, -1)]
    return {"labels": labels, "series": series, "total": total,
            "this_month": buckets.get(cur, 0), "peak": max(series) if series else 0}


def _provider_platform_activity(items, now):
    """MT42 — أحدث ما جرى على مستوى المنصّة (لا شبكةٍ بعينها): شبكات
    أُنشئت وطلبات اشتراك وردت أو عُولجت. مصدرٌ حقيقيّ بلا اختراع كتابةٍ
    جديدة — نَقرأ ما هو مُسجَّل سلفًا."""
    from datetime import datetime
    events = []

    for t in items:
        c = getattr(t, "created_at", None)
        if isinstance(c, datetime):
            events.append({"icon": "circle-plus", "color": "#22a565",
                           "text": f"أُنشئت شبكة «{t.display_name or t.name}»",
                           "at": c, "url_id": t.id})

    try:
        from ..db.repos import signup_requests_repo
        for s in signup_requests_repo.list_all(limit=30):
            at = s.get("handled_at") or s.get("created_at") or ""
            try:
                atd = datetime.fromisoformat(str(at).replace("Z", "")[:19])
            except Exception:  # noqa: BLE001
                atd = None
            st = s.get("status")
            if st == "pending":
                events.append({"icon": "envelope", "color": "#7c5cff",
                               "text": f"طلب اشتراك: «{s.get('network_name') or '—'}»",
                               "at": atd, "url_id": None})
            elif st == "rejected":
                events.append({"icon": "envelope-circle-check", "color": "#94a3b8",
                               "text": f"أُغلق طلب «{s.get('network_name') or '—'}»",
                               "at": atd, "url_id": None})
    except Exception:  # noqa: BLE001
        pass

    events.sort(key=lambda e: e["at"] or datetime.min, reverse=True)
    return events[:12]


def _provider_money_and_attention(items, usage_subs, usage_nas, now, signups):
    """MT38 — الرقمان اللذان يَسأل عنهما المزوّد أوّلًا: كم يَدخل، ومَن
    يحتاج تدخّلًا اليوم.

    قرارات صريحة:
      • «الإيراد» = مجموع مبالغ الجهات المدفوعة السارية فقط. الجهة التي
        انقضى ``paid_until`` لا تُحتسب إيرادًا — تُحتسب **متأخّرة**، وإلّا
        أظهرنا للمالك دخلًا لم يَقبضه.
      • الجهات بلا ``paid_until`` تُعدّ سارية (اشتراك مفتوح لا متأخّر):
        غياب التاريخ ليس دليل تأخّر.
      • «قاربت حدّها» عند ٨٥٪ فأعلى — قبل الاصطدام لا بعده.
    العملة لا تُجمع عبر عملات مختلفة: نَعرض المجموع بعملة الإعداد العام
    ونُظهر تحذيرًا إن اختلفت عملات الجهات (لم نَخترع صرفًا).
    """
    from datetime import datetime

    def _paid_until(t):
        raw = getattr(t, "paid_until", None)
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "")[:19])
        except Exception:  # noqa: BLE001
            return None

    NEAR = 0.85
    revenue = 0.0
    overdue_amount = 0.0
    overdue, due_soon, expiring, near_limit, idle = [], [], [], [], []

    for t in items:
        paid = (getattr(t, "billing_mode", "free") or "free") == "paid"
        amount = float(getattr(t, "billing_amount", 0) or 0)
        until = _paid_until(t)

        if paid:
            if until and until < now:
                overdue_amount += amount
                overdue.append({"t": t, "days": (now - until).days, "amount": amount})
            else:
                revenue += amount
                if until and (until - now).days <= 7:
                    due_soon.append({"t": t, "days": (until - now).days, "amount": amount})

        # تجربة توشك أن تنتهي
        ends = getattr(t, "trial_ends_at", None)
        if ends and getattr(t, "status", "") == "trial":
            left = (ends - now).days if isinstance(ends, datetime) else None
            if left is not None and left <= 3:
                expiring.append({"t": t, "days": left})

        # قارب حدّه — بسقفٍ معقول فقط.
        # سقف الأجهزة في الفئة الأساسيّة = ١، فجهةٌ تستخدم راوترها الوحيد
        # تكون ١٠٠٪ دائمًا وتُنبّه أبدًا. ذاك وضعٌ طبيعيّ لا حدثٌ يستحقّ
        # انتباهًا — فنَشترط سقفًا ≥ ٢ كي تكون النسبة ذات معنى.
        subs, nas = usage_subs.get(t.id, 0), usage_nas.get(t.id, 0)
        max_subs = int(getattr(t, "max_subscribers", 0) or 0)
        max_nas = int(getattr(t, "max_nas", 0) or 0)
        if max_subs >= 2 and subs / max_subs >= NEAR:
            near_limit.append({"t": t, "what": "المشتركون",
                               "used": subs, "cap": max_subs})
        if max_nas >= 2 and nas / max_nas >= NEAR:
            near_limit.append({"t": t, "what": "الأجهزة", "used": nas, "cap": max_nas})

        # جهة مفعّلة بلا مشترك واحد = تَعثّرت في البداية
        if getattr(t, "status", "") == "active" and subs == 0:
            idle.append({"t": t})

    try:
        from ..services.provider_chat import unread_by_tenant
        unread = {k: v for k, v in (unread_by_tenant() or {}).items() if v}
    except Exception:  # noqa: BLE001
        unread = {}

    currencies = {(getattr(t, "currency", "") or "") for t in items
                  if (getattr(t, "billing_mode", "free") or "free") == "paid"}
    currencies.discard("")

    money = {
        "revenue": revenue,
        "overdue_amount": overdue_amount,
        "overdue_count": len(overdue),
        "paid_count": sum(1 for t in items
                          if (getattr(t, "billing_mode", "free") or "free") == "paid"),
        "mixed_currency": len(currencies) > 1,
    }
    attention = {
        "overdue": overdue, "due_soon": due_soon, "expiring": expiring,
        "near_limit": near_limit, "idle": idle,
        "signups": len(signups or []),
        "unread_threads": len(unread),
        "unread_total": sum(unread.values()),
    }
    attention["total"] = (len(overdue) + len(due_soon) + len(expiring)
                          + len(near_limit) + len(idle)
                          + attention["signups"] + attention["unread_threads"])
    return money, attention


def provider_network_profile(tenant_id: int):
    """MT41 — ملفّ شبكة واحدة للمزوّد: كل ما يخصّها في صفحة واحدة بدل
    القفز بين اللوحة والتعديل والمحادثة. owner-only (خريطة الصلاحيات)."""
    from datetime import datetime
    t = get_tenants_service().get(tenant_id)
    if not t or tenant_id == 1:
        abort(404)
    usage_subs, usage_nas, usage_online, operators = _tenant_usage()
    now = datetime.utcnow()

    # سجلّ نشاط الشبكة (مُقيَّد بـtenant_id في الاستعلام نفسه — عزلٌ مضمون).
    try:
        from ..db.repos import audit_repo
        activity = audit_repo.recent(tenant_id, limit=40)
    except Exception:  # noqa: BLE001
        activity = []

    # ملخّص المحادثة + آخر رسائل.
    try:
        from ..services import provider_chat
        chat_summary = provider_chat.thread_summary(tenant_id)
        recent_msgs = provider_chat.list_messages(tenant_id=tenant_id)[-6:]
        chat_unread = provider_chat.unread_count(tenant_id=tenant_id, side="provider")
    except Exception:  # noqa: BLE001
        chat_summary, recent_msgs, chat_unread = {"total": 0, "last": None}, [], 0

    def _dt(v):
        if isinstance(v, datetime):
            return v
        try:
            return datetime.fromisoformat(str(v).replace("Z", "")[:19])
        except Exception:  # noqa: BLE001
            return None

    paid_until = _dt(getattr(t, "paid_until", None))
    trial_ends = getattr(t, "trial_ends_at", None)
    profile = {
        "subs": usage_subs.get(tenant_id, 0),
        "nas": usage_nas.get(tenant_id, 0),
        "online": usage_online.get(tenant_id, 0),
        "operator": operators.get(tenant_id, ""),
        "paid_until": paid_until,
        "overdue": bool(paid_until and paid_until < now
                        and (getattr(t, "billing_mode", "free") or "free") == "paid"),
        "trial_days_left": ((trial_ends - now).days
                            if (trial_ends and getattr(t, "status", "") == "trial") else None),
    }
    return render_template(
        "radius/provider_network_profile.html",
        t=t, profile=profile, activity=activity, now_utc=now,
        chat_summary=chat_summary, recent_msgs=recent_msgs, chat_unread=chat_unread)


def provider_tiers():
    """MT47/MT49 — إدارة فئات الاشتراك الديناميكيّة (إضافة/تعديل/حذف)."""
    from ..services import tier_config as tc
    return render_template("radius/provider_tiers.html",
                           tiers=tc.get_tiers(), counts=tc.tiers_in_use())


def provider_tiers_save():
    """يَحفظ تعديلات الفئات القائمة (اسم/أيقونة/حدود) دفعةً."""
    from ..services import tier_config as tc
    from ..db.repos import audit_repo
    rows = []
    for t in tc.get_tiers():
        k = t["key"]
        rows.append({
            "key": k,
            "label": request.form.get(f"{k}__label", t["label"]),
            "icon": request.form.get(f"{k}__icon", t["icon"]),
            "max_subscribers": request.form.get(f"{k}__max_subscribers"),
            "max_nas": request.form.get(f"{k}__max_nas"),
            "api_rpm": request.form.get(f"{k}__api_rpm"),
        })
    saved = tc.save_tiers(rows, by=int(session.get("admin_id") or 0))
    try:
        audit_repo.record(tenant_id=1, actor=_actor(), action="tiers_update",
                          target_type="platform", target_id="tiers",
                          payload={"count": len(saved)})
    except Exception:  # noqa: BLE001
        pass
    flash("حُفظت الفئات. تسري على الشبكات الجديدة؛ الشبكات القائمة تُعدَّل "
          "من ملفّاتها.", "success")
    return redirect(url_for("radius.provider_tiers"))


def provider_tiers_add():
    """يُضيف فئةً جديدة — بلا سقفٍ على العدد."""
    from ..services import tier_config as tc
    label = (request.form.get("label") or "").strip()
    if not label:
        flash("أدخل اسم الفئة.", "error")
        return redirect(url_for("radius.provider_tiers"))
    t = tc.add_tier(
        label=label, icon=(request.form.get("icon") or "layer-group").strip(),
        max_subscribers=request.form.get("max_subscribers") or 100,
        max_nas=request.form.get("max_nas") or 1,
        api_rpm=request.form.get("api_rpm") or 10,
        by=int(session.get("admin_id") or 0))
    flash(f"أُضيفت الفئة «{t['label']}».", "success")
    return redirect(url_for("radius.provider_tiers"))


def provider_tiers_delete(key):
    """يَحذف فئةً. الشبكات التي تستخدمها لا تُكسَر (حدودها منسوخة سلفًا)."""
    from ..services import tier_config as tc
    if tc.delete_tier(key, by=int(session.get("admin_id") or 0)):
        flash("حُذفت الفئة. الشبكات التي كانت عليها تحتفظ بحدودها الحاليّة.", "info")
    else:
        flash("تعذّر الحذف — لا بدّ من فئةٍ واحدة على الأقلّ.", "error")
    return redirect(url_for("radius.provider_tiers"))


# ── MT57 — عروض الأسعار (قسم صفحة المنصّة) ──────────────────────────
def _offer_discounts_from_form(prefix: str) -> list[dict]:
    """يقرأ مدد الخصم من الحقول المتوازية ``<prefix>months[]``/``percent[]``.
    الفارغ يُطرح، فحذف مدّةٍ = إفراغ حقلها."""
    months = request.form.getlist(f"{prefix}months[]")
    pcts = request.form.getlist(f"{prefix}percent[]")
    out = []
    for i, m in enumerate(months):
        if not str(m).strip():
            continue
        out.append({"months": m, "percent": (pcts[i] if i < len(pcts) else 0)})
    return out


def provider_offers():
    """MT57 — إدارة عروض الأسعار المعروضة على صفحة المنصّة."""
    from ..services import pricing_offers as po
    offers = po.get_offers()
    return render_template("radius/provider_offers.html", offers=offers,
                           rows_for={o["key"]: po.offer_rows(o) for o in offers},
                           unit_for={o["key"]: po.unit_price(o) for o in offers})


def provider_offers_save():
    """يَحفظ تعديلات العروض القائمة دفعةً (سعر/اتصالات/خصومات/ظهور)."""
    from ..services import pricing_offers as po
    from ..db.repos import audit_repo
    rows = []
    for o in po.get_offers():
        k = o["key"]
        rows.append({
            "key": k,
            "label": request.form.get(f"{k}__label", o["label"]),
            "icon": request.form.get(f"{k}__icon", o["icon"]),
            "concurrent": request.form.get(f"{k}__concurrent"),
            "price_monthly": request.form.get(f"{k}__price_monthly"),
            "currency": request.form.get(f"{k}__currency", o["currency"]),
            "is_free": bool(request.form.get(f"{k}__is_free")),
            "trial_days": request.form.get(f"{k}__trial_days") or 0,
            "highlight": bool(request.form.get(f"{k}__highlight")),
            "visible": bool(request.form.get(f"{k}__visible")),
            "note": request.form.get(f"{k}__note", ""),
            "discounts": _offer_discounts_from_form(f"{k}__"),
        })
    saved = po.save_offers(rows, by=int(session.get("admin_id") or 0))
    try:
        audit_repo.record(tenant_id=1, actor=_actor(), action="offers_update",
                          target_type="platform", target_id="offers",
                          payload={"count": len(saved)})
    except Exception:  # noqa: BLE001
        pass
    flash("حُفظت العروض — تظهر فورًا على صفحة المنصّة.", "success")
    return redirect(url_for("radius.provider_offers"))


def provider_offers_add():
    """يُضيف عرضًا جديدًا — بلا سقفٍ على العدد."""
    from ..services import pricing_offers as po
    label = (request.form.get("label") or "").strip()
    if not label:
        flash("أدخل اسم العرض.", "error")
        return redirect(url_for("radius.provider_offers"))
    o = po.add_offer(
        label=label, icon=(request.form.get("icon") or "tag").strip(),
        concurrent=request.form.get("concurrent") or 50,
        price_monthly=request.form.get("price_monthly") or 0,
        currency=(request.form.get("currency") or "USD").strip(),
        is_free=bool(request.form.get("is_free")),
        trial_days=request.form.get("trial_days") or 0,
        note=(request.form.get("note") or "").strip(),
        by=int(session.get("admin_id") or 0))
    flash(f"أُضيف العرض «{o['label']}». عدّل خصومات المدد بالأسفل.", "success")
    return redirect(url_for("radius.provider_offers"))


def provider_offers_delete(key):
    from ..services import pricing_offers as po
    if po.delete_offer(key, by=int(session.get("admin_id") or 0)):
        flash("حُذف العرض من صفحة المنصّة.", "info")
    else:
        flash("لم أجد هذا العرض.", "error")
    return redirect(url_for("radius.provider_offers"))


def provider_offers_move(key, direction):
    """ترتيب ظهور العروض على صفحة المنصّة."""
    from ..services import pricing_offers as po
    po.move_offer(key, "up" if direction == "up" else "down",
                  by=int(session.get("admin_id") or 0))
    return redirect(url_for("radius.provider_offers"))


def provider_network_limits(tenant_id: int):
    """MT47 — تجاوز حدود شبكةٍ بعينها مباشرةً (يتخطّى فئتها)."""
    t = get_tenants_service().get(tenant_id)
    if not t or tenant_id == 1:
        abort(404)
    def _int(name, cur, cap):
        try:
            return max(1, min(int(request.form.get(name) or cur), cap))
        except (TypeError, ValueError):
            return cur
    changes = {
        "max_subscribers": _int("max_subscribers", t.max_subscribers, 10_000_000),
        "max_nas": _int("max_nas", t.max_nas, 10_000),
        "api_rpm": _int("api_rpm", t.api_rpm, 100_000),
    }
    get_tenants_service().update(actor=_actor(), tenant_id=tenant_id, **changes)
    flash(f"حُدّثت حدود «{t.display_name or t.name}»: "
          f"{changes['max_subscribers']} مشترك · {changes['max_nas']} جهاز.", "success")
    return redirect(url_for("radius.provider_network_profile", tenant_id=tenant_id))


def provider_topup():
    """MT46 — لوحة شحن الشبكات: بحث + كل شبكة برصيدها وحالة اشتراكها
    وأزرار شحن، وسجلّ آخر الحركات. owner-only (خريطة الصلاحيات)."""
    from datetime import datetime
    from ..services import tenant_topup
    now = datetime.utcnow()
    items = []
    for t in (x for x in get_tenants_service().list() if x.id != 1):
        pu = getattr(t, "paid_until", None)
        te = getattr(t, "trial_ends_at", None)
        items.append({
            "t": t,
            "balance": float(getattr(t, "credit_balance", 0) or 0),
            "paid_until": pu,
            "paid_days_left": (pu - now).days if (pu and pu > now) else (
                (pu - now).days if pu else None),
            "trial_days_left": (te - now).days if (te and getattr(t, "status", "") == "trial") else None,
            "overdue": bool(pu and pu < now and (getattr(t, "billing_mode", "free") or "free") == "paid"),
        })
    # المتأخّرون والمنتهون قريبًا أوّلًا (الأحوج للشحن)
    items.sort(key=lambda x: (not x["overdue"],
                              x["paid_days_left"] if x["paid_days_left"] is not None else 9999))
    try:
        history = tenant_topup.recent(limit=30)
    except Exception:  # noqa: BLE001
        history = []
    id2name = {t.id: (t.display_name or t.name) for t in get_tenants_service().list()}
    return render_template("radius/provider_topup.html",
                           items=items, history=history, id2name=id2name, now_utc=now)


def _topup_target(tenant_id: int):
    t = get_tenants_service().get(tenant_id)
    if not t or tenant_id == 1:
        abort(404)
    return t


def provider_topup_credit(tenant_id: int):
    from ..services import tenant_topup
    _topup_target(tenant_id)
    try:
        r = tenant_topup.add_credit(tenant_id=tenant_id,
                                    amount=request.form.get("amount"),
                                    note=(request.form.get("note") or "").strip(),
                                    actor=_actor())
        flash(f"أُضيف رصيد {r['amount']:,.2f}. الرصيد الآن: {r['balance']:,.2f}.", "success")
    except tenant_topup.TopupError as e:
        flash(str(e), "error")
    return redirect(url_for("radius.provider_topup"))


def provider_topup_paid(tenant_id: int):
    from ..services import tenant_topup
    _topup_target(tenant_id)
    try:
        r = tenant_topup.add_paid_days(
            tenant_id=tenant_id, days=request.form.get("days"),
            amount=request.form.get("amount") or 0,
            charge_balance=bool(request.form.get("charge_balance")),
            note=(request.form.get("note") or "").strip(), actor=_actor())
        flash(f"أُضيفت {r['days']} يوم مدفوع — الاشتراك حتى "
              f"{r['paid_until'].strftime('%Y-%m-%d')}.", "success")
    except tenant_topup.TopupError as e:
        flash(str(e), "error")
    return redirect(url_for("radius.provider_topup"))


def provider_topup_free(tenant_id: int):
    from ..services import tenant_topup
    _topup_target(tenant_id)
    try:
        r = tenant_topup.add_free_days(
            tenant_id=tenant_id, days=request.form.get("days"),
            note=(request.form.get("note") or "").strip(), actor=_actor())
        flash(f"أُضيفت {r['days']} يوم مجّانيّ — حتى "
              f"{r['until'].strftime('%Y-%m-%d')}.", "success")
    except tenant_topup.TopupError as e:
        flash(str(e), "error")
    return redirect(url_for("radius.provider_topup"))


def signup_request_dismiss(request_id: int):
    """MT36 — إغلاق طلب اشتراك (رفض/تمّت معالجته يدويًّا).

    القبول الفعليّ = إنشاء شبكة من «شبكة جديدة»؛ هذا المسار للإغلاق فقط
    كي لا تتراكم الطلبات المُنجَزة في اللوحة. لا يَحذف السجلّ — يُبقيه
    للأثر بحالة ``rejected``.
    """
    from ..db.repos import signup_requests_repo
    row = signup_requests_repo.get(request_id)
    if not row:
        abort(404)
    signup_requests_repo.mark(request_id, status="rejected", by=_actor())
    flash(f"أُغلق طلب «{row.get('network_name') or '—'}».", "info")
    return redirect(url_for("radius.provider_home"))


def tenants_trial_extend(tenant_id: int):
    """MT8 — تمديد التجربة: من الأبعد بين الآن ونهايتها الحالية + N أيام."""
    from datetime import datetime, timedelta
    svc = get_tenants_service()
    t = svc.get(tenant_id)
    if not t:
        abort(404)
    try:
        days = max(1, min(90, int(request.form.get("days") or 7)))
    except ValueError:
        days = 7
    base = t.trial_ends_at if (t.trial_ends_at and t.trial_ends_at > datetime.utcnow()) \
        else datetime.utcnow()
    new_end = base + timedelta(days=days)
    svc.update(actor=_actor(), tenant_id=tenant_id,
               status=TENANT_STATUS_TRIAL, trial_ends_at=new_end)
    flash(f"مُدّدت تجربة «{t.display_name or t.name}» حتى {new_end.strftime('%Y-%m-%d')}.",
          "success")
    return redirect(url_for("radius.tenants_list"))


def tenants_toggle_suspend(tenant_id: int):
    """MT8 — تعليق/إعادة تفعيل سريع. إعادة التفعيل تعيدها «تجريبية» إذا
    كانت مدة تجربتها ما تزال سارية، وإلا «مفعَّلة»."""
    from datetime import datetime
    svc = get_tenants_service()
    t = svc.get(tenant_id)
    if not t:
        abort(404)
    if t.status == TENANT_STATUS_SUSPENDED:
        new_status = (TENANT_STATUS_TRIAL
                      if (t.trial_ends_at and t.trial_ends_at > datetime.utcnow())
                      else TENANT_STATUS_ACTIVE)
        msg = "أُعيد تفعيل الجهة"
    else:
        new_status = TENANT_STATUS_SUSPENDED
        msg = "عُلِّقت الجهة — يُرفض اتصال مشتركيها فورًا"
    svc.update(actor=_actor(), tenant_id=tenant_id, status=new_status)
    flash(f"{msg}: «{t.display_name or t.name}».", "success")
    return redirect(url_for("radius.tenants_list"))


def tenants_new():
    # MT38 — تعبئة مسبقة من طلب اشتراك: المالك يضغط «أنشئ» في بطاقة
    # الطلب فتصل بيانات العميل إلى النموذج بدل إعادة كتابتها يدويًّا.
    # القيم من الرابط تُنظَّف بالقصّ فقط — النموذج نفسه هو من يَتحقّق.
    slug = (request.args.get("name") or "").strip()[:60]
    disp = (request.args.get("display_name") or "").strip()[:120]
    from_signup = (request.args.get("from_signup") or "").strip()[:20]
    blank = Tenant(id=None, slug=slug, name=slug, display_name=disp,
                   plan_tier=TENANT_TIER_STARTER, status=TENANT_STATUS_ACTIVE)
    signup = None
    if from_signup.isdigit():
        try:
            from ..db.repos import signup_requests_repo
            signup = signup_requests_repo.get(int(from_signup))
        except Exception:  # noqa: BLE001 — تعذّر جلب الطلب لا يمنع الإنشاء
            signup = None
    return render_template("radius/tenants_form.html",
        tenant=blank, tiers=_dyn_tiers(), statuses=STATUS_KEYS,
        tier_limits=TIER_LIMITS, is_new=True, signup=signup)


def tenants_create():
    t = _form_dto()
    # MT6 — بذر مدير الجهة (غير سوبر) اختياريًا في نفس الخطوة؛ إلزامي
    # عمليًا للجهات التجريبية كي يتسلّم الزبون بيانات دخول جاهزة.
    op_user = (request.form.get("operator_username") or "").strip()
    try:
        if op_user:
            result = get_tenants_service().create_trial(
                actor=_actor(), tenant=t,
                trial_days=int(request.form.get("trial_days") or 7),
                operator_username=op_user,
                operator_password=request.form.get("operator_password") or "",
                operator_full_name=(request.form.get("operator_full_name") or "").strip(),
            )
            saved = result["tenant"]
            ends = result["trial_ends_at"]
            parts = [f"تم إنشاء الجهة «{saved.display_name or saved.name}»"]
            if ends:
                parts.append(f"(التجربة حتى {ends.strftime('%Y-%m-%d')})")
            parts.append(f"— دخول المدير: {result['operator_username']} / "
                         f"{result['operator_password']} (احفظها الآن، لن تظهر مجددًا)")
            parts.append(f"— بوابة المشتركين: /portal/subscriber/login?t={saved.slug}")
            flash(" ".join(parts), "success")
        else:
            if (t.status or "") == TENANT_STATUS_TRIAL:
                raise RadiusError("الجهة التجريبية تحتاج اسم مستخدم لمديرها — "
                                   "املأ حقل «مدير الجهة».")
            saved = get_tenants_service().create(actor=_actor(), tenant=t)
            flash(f"تم إنشاء الجهة «{saved.name}».", "success")
    except (RadiusError, ValueError) as e:
        flash(str(getattr(e, "message", e)), "error")
        return render_template("radius/tenants_form.html",
            tenant=t, tiers=_dyn_tiers(), statuses=STATUS_KEYS,
            tier_limits=TIER_LIMITS, is_new=True), 400
    return redirect(url_for("radius.tenants_list"))


def tenants_edit(tenant_id: int):
    t = get_tenants_service().get(tenant_id)
    if not t:
        abort(404)
    return render_template("radius/tenants_form.html",
        tenant=t, tiers=_dyn_tiers(), statuses=STATUS_KEYS,
        tier_limits=TIER_LIMITS, is_new=False)


def tenants_update(tenant_id: int):
    changes = _form_changes()
    try:
        get_tenants_service().update(actor=_actor(), tenant_id=tenant_id, **changes)
    except RadiusError as e:
        flash(e.message, "error")
        t = get_tenants_service().get(tenant_id)
        return render_template("radius/tenants_form.html",
            tenant=t, tiers=_dyn_tiers(), statuses=STATUS_KEYS,
            tier_limits=TIER_LIMITS, is_new=False), 400
    flash("تم التحديث.", "success")
    return redirect(url_for("radius.tenants_list"))


def _form_dto() -> Tenant:
    def _i(n, d=0):
        try: return int(request.form.get(n) or d)
        except: return d
    return Tenant(
        id=None,
        slug=(request.form.get("slug") or "").strip().lower(),
        name=(request.form.get("name") or "").strip(),
        display_name=(request.form.get("display_name") or "").strip(),
        email=(request.form.get("email") or "").strip(),
        phone=(request.form.get("phone") or "").strip(),
        currency=(request.form.get("currency") or default_currency()).strip(),
        locale=(request.form.get("locale") or "ar").strip(),
        timezone=(request.form.get("timezone") or "Asia/Amman").strip(),
        logo_url=(request.form.get("logo_url") or "").strip(),
        primary_color=(request.form.get("primary_color") or "#2BAACC").strip(),
        status=(request.form.get("status") or TENANT_STATUS_ACTIVE).strip(),
        plan_tier=(request.form.get("plan_tier") or TENANT_TIER_STARTER).strip(),
        max_subscribers=_i("max_subscribers"),
        max_nas=_i("max_nas"),
        api_rpm=_i("api_rpm"),
        # MT18 — الفوترة
        billing_mode=(request.form.get("billing_mode") or "free").strip(),
        billing_amount=_parse_amount(request.form.get("billing_amount")),
        paid_until=_parse_date(request.form.get("paid_until")),
        billing_note=(request.form.get("billing_note") or "").strip(),
    )


def _parse_amount(v) -> float:
    try:
        return max(0.0, float(v or 0))
    except (TypeError, ValueError):
        return 0.0


def _parse_date(v):
    from datetime import datetime
    s = (v or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _form_changes() -> dict:
    keys = ("name", "display_name", "email", "phone", "currency", "locale",
            "timezone", "logo_url", "primary_color", "status", "plan_tier",
            "billing_mode", "billing_note")
    out: dict = {}
    for k in keys:
        v = request.form.get(k)
        if v is not None:
            out[k] = v.strip()
    for k in ("max_subscribers", "max_nas", "api_rpm"):
        v = request.form.get(k)
        if v is not None:
            try: out[k] = int(v)
            except ValueError: pass
    if request.form.get("billing_amount") is not None:
        out["billing_amount"] = _parse_amount(request.form.get("billing_amount"))
    if "paid_until" in request.form:
        out["paid_until"] = _parse_date(request.form.get("paid_until"))
    return out


def tenants_delete_confirm(tenant_id: int):
    """MT33 — صفحة «هل أنت متأكد؟»: تعرض ما سيُحذف بالتفصيل قبل التأكيد."""
    from ..services import tenant_delete
    try:
        info = tenant_delete.preview_tenant_deletion(tenant_id)
    except tenant_delete.TenantDeleteError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.tenants_list"))
    return render_template("radius/tenant_delete_confirm.html", **info)


def tenants_delete(tenant_id: int):
    """MT33 — التنفيذ. يُشترط كتابة الـslug حرفيًّا، وتُؤخذ نسخة أمان أوّلًا."""
    from ..services import tenant_delete
    try:
        out = tenant_delete.delete_tenant(
            tenant_id, confirm_slug=request.form.get("confirm_slug", ""),
            actor=_actor())
    except tenant_delete.TenantDeleteError as e:
        flash(str(e), "error")
        return redirect(url_for("radius.tenants_delete_confirm", tenant_id=tenant_id))
    flash(f"حُذفت الشبكة «{out['name']}» نهائيًّا ({out['rows']} صفًّا، "
          f"{out['admins_deleted']} مديرًا). نسخة الأمان محفوظة: {out['safety_backup']}",
          "success")
    return redirect(url_for("radius.tenants_list"))
