"""UsersService — المشتركون (Radius Accounts)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Optional, Sequence

from ..core.constants import (
    AUDIT_ACTION_ARCHIVE, AUDIT_ACTION_CREATE, AUDIT_ACTION_DISABLE,
    AUDIT_ACTION_ENABLE, AUDIT_ACTION_RESET_PASSWORD, AUDIT_ACTION_UPDATE,
    STATUS_DISABLED, STATUS_ENABLED, USER_TYPES,
)
from ..core.errors import RadiusConflict, RadiusNotFound, RadiusValidationError
from ..core.numbers import (
    ACTION_AMOUNT_MAX, EXTEND_MAX_MINUTES, EXTEND_TOO_LONG_AR, NonFiniteNumber,
    action_amount, add_minutes_capped, check_expiry, check_extend_minutes,
    finite_float, round_money,
)
from ..core.system_config import default_currency
from ..core.types import Subscriber
from ..db.connection import after_commit, atomic, in_transaction
from ..integration.adapter import RadiusAdapter
from .audit import RadiusAuditService

import re


def _currency(value) -> str:
    """عملة قيدٍ على محفظة المشترك: رمزٌ مدعوم، والفارغ = عملة النظام."""
    from .accounting import normalize_currency
    return normalize_currency(value)


def _charge_amount(charge_mode: str, amount) -> float:
    """مبلغ «مدفوع/دين» مُقرَّبًا لقرشين **قبل** الفحص — 0.004 كان يمرّ «> 0»
    ثم يُسجَّل قيد دين 0.00 ويمنح ساعة مجّانًا — وبسقف العمليّة الواحدة
    (كان «أضف وقت» في الويب يقبل دينًا بقيمة 1e11). المجّانيّ يمرّ كما هو."""
    if charge_mode not in {"paid", "debt"}:
        return amount
    value = round_money(finite_float(amount, field="amount"))
    if value <= 0:
        raise RadiusValidationError("المبلغ يجب أن يكون أكبر من صفر.")
    action_amount(value, field="amount")
    return value


def _require_paid_balance(sub, amount, charge_mode: str) -> None:
    """«مدفوع — نقدًا» في نوافذ الويب/التطبيق = «تُخصم القيمة من رصيد المشترك»
    (قرار المالك: المدفوع يستهلك الرصيد المسبق). رصيدٌ لا يكفي كان يَنزل سالبًا
    بصمت — أي يصير «دينًا» بلا تسمية. الآن يُرفض ويُوجَّه المشغّل للخيار الصحيح."""
    if charge_mode != "paid":
        return
    balance = float(getattr(sub, "balance", 0) or 0)
    if balance + 1e-9 < float(amount or 0):
        raise RadiusValidationError(
            f"رصيد المشترك ({balance:.2f}) لا يكفي لخصم {float(amount):.2f} — "
            "«مدفوع» يُخصم من الرصيد. أضِف رصيدًا أولًا أو اختر «دين».")


ARCHIVED_NAME_MSG = ("الاسم يخص مشتركًا مؤرشفًا — استرجعه من سلة المحذوفات "
                     "أو احذفه نهائيًا")


def _search_term(search) -> str:
    """Arabic-Indic digits → Latin: «٠٥٩٩» found nothing (re-test R01 N10)."""
    from .subscriber_validation import latin_digits
    return latin_digits(search).strip()


def _username_archived(tenant_id: int, username: str, *,
                       exclude_id: Optional[int] = None) -> bool:
    """The name (case/space-insensitive) belongs to an ARCHIVED subscriber."""
    from ..db.connection import db
    name = str(username or "").strip().lower()
    if not name:
        return False
    sql = ("SELECT 1 FROM subscribers WHERE tenant_id = ? AND lower(trim(username)) = ? "
           "AND deleted_at IS NOT NULL")
    args: list = [int(tenant_id or 1), name]
    if exclude_id is not None:
        sql += " AND id <> ?"
        args.append(int(exclude_id))
    return bool(db().execute(sql + " LIMIT 1", args).fetchone())


def _username_in_use(tenant_id: int, username: str, *,
                     exclude_id: Optional[int] = None) -> bool:
    """الاسم محجوزٌ لمشتركٍ قائم أو لبطاقة (نفس فضاء أسماء الدخول)، بمقارنةٍ
    لا تفرّق بين حالة الأحرف ولا المسافات الطرفيّة."""
    from ..db.connection import db
    name = str(username or "").strip().lower()
    if not name:
        return False
    conn = db()
    sql = ("SELECT 1 FROM subscribers WHERE tenant_id = ? AND lower(trim(username)) = ? "
           "AND deleted_at IS NULL")
    args: list = [int(tenant_id or 1), name]
    if exclude_id is not None:
        # a rename may change only the case of its OWN name
        sql += " AND id <> ?"
        args.append(int(exclude_id))
    if conn.execute(sql + " LIMIT 1", args).fetchone():
        return True
    try:
        return bool(conn.execute(
            "SELECT 1 FROM cards WHERE tenant_id = ? AND lower(trim(username)) = ? LIMIT 1",
            (int(tenant_id or 1), name),
        ).fetchone())
    except Exception:  # noqa: BLE001 — نسخة بلا جدول بطاقات
        return False

# Allowed charset for a RADIUS login username on rename: Latin letters, digits
# and the punctuation RADIUS/NAS accept (._-@), 1–64 chars, no spaces. Keeps the
# auth key portable across FreeRADIUS/MikroTik and free of injection-hazard
# characters (queries are parameterised regardless).
_USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


class UsersService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit

    def count(self, *, status: Optional[str] = None, plan_id: Optional[int] = None,
              user_type: Optional[str] = "subscriber",
              search: str = "", expiring_within_days: Optional[int] = None,
              owner_admin_id: Optional[int] = None, usernames_in=None) -> int:
        """إجماليّ المطابقين — لعدد صفحات الترقيم الخادميّ (مستقلّ عن limit)."""
        try:
            return int(self._adapter.count_accounts(
                status=status, user_type=user_type, search=(_search_term(search) or None),
                expiring_within_days=expiring_within_days,
                owner_admin_id=owner_admin_id, plan_id=plan_id,
                usernames_in=usernames_in))
        except Exception:  # noqa: BLE001 — العدّ لا يكسر الصفحة
            return 0

    def list(self, *, status: Optional[str] = None, plan_id: Optional[int] = None,
             user_type: Optional[str] = "subscriber",
             search: str = "", expiring_within_days: Optional[int] = None,
             owner_admin_id: Optional[int] = None, usernames_in=None,
             order_by: str = "id", order_dir: str = "desc",
             limit: int = 500, offset: int = 0) -> Sequence[Subscriber]:
        """قائمة المشتركين.

        R9.0:
          - `user_type='subscriber'` افتراضياً يستبعد سجلّات mirror التي
            يُنشئها card generation (user_type='card'). صفحة "المشتركين"
            تعرض المشتركين الحقيقيين فقط؛ البطاقات لها صفحة منفصلة.
            تمرير `user_type=None` صراحةً يُعيد السلوك القديم (الكل).
            تمرير `user_type='card'` يعرض البطاقات.
          - `search` يُمرَّر إلى SQL pushdown في الـ adapter/repo بدل
            الفلترة بعد LIMIT. مهم مع >1000 سجلّ.
          - `expiring_within_days`: حصْر النتائج على من تنتهي صلاحيتهم
            خلال N أيام (يطابق حساب «ينتهي قريبًا» في الـ Dashboard).
        """
        # plan_id يُدفَع للـSQL (لا فلترة-بعد-الجلب) كي يصحّ الترقيم الخادميّ:
        # كانت الفلترة بعد LIMIT تُرجع أقلّ من page_size عند تفعيل فلتر الباقة.
        items = list(self._adapter.list_accounts(
            status=status, user_type=user_type, search=(_search_term(search) or None),
            expiring_within_days=expiring_within_days,
            owner_admin_id=owner_admin_id, plan_id=plan_id,
            usernames_in=usernames_in,
            order_by=order_by, order_dir=order_dir,
            limit=limit, offset=offset,
        ))
        return items

    def status_counts(self, *, user_type: Optional[str] = "subscriber",
                      search: str = "", plan_id: Optional[int] = None,
                      expiring_within_days: Optional[int] = None,
                      owner_admin_id: Optional[int] = None) -> dict:
        """عدّادات بطاقات KPI لصفحة المشتركين — تجميع DB حقيقي (GROUP BY
        status) فوق كامل الجدول ضمن نطاق الفلتر، مستقلّ عن حدود الصفحة.
        يُصلِح نقص العدّ حين كانت البطاقات تُحسب من القائمة المحمّلة فقط.
        owner_admin_id يَقصُر العدّ على نطاق المدير (نفس عزل القائمة)."""
        return self._adapter.account_status_counts(
            user_type=user_type, search=(_search_term(search) or None),
            plan_id=plan_id, expiring_within_days=expiring_within_days,
            owner_admin_id=owner_admin_id,
        )

    def get(self, username: str) -> Subscriber:
        return self._adapter.get_account(username)

    @atomic  # the «name free?» check and the insert under ONE write lock
    def create(self, *, actor: str, sub: Subscriber) -> Subscriber:
        _validate(sub)
        # نفس قاعدة إعادة التسمية: اسم الدخول مفتاح RADIUS — الإنشاء كان يقبل
        # مسافات/عربيًّا/إيموجي/«/» (والأخير يجعل الحساب غير قابل للوصول عبر
        # /accounts/<u>) بينما تُرفض كلّها في rename. مصدر واحد للويب والـAPI.
        _validate_new_username(sub.username)
        # 🔴 الحفظ «upsert» على الاسم: إنشاءٌ باسمٍ قائم كان **يكتب فوق** المشترك
        # (رصيده ونهايته وباقته وكلمة سرّه) أو يحوّل بطاقةً إلى مشترك. الإنشاء
        # إنشاءٌ فقط — الاسم المحجوز يُرفض (409 في الـAPI، رسالة في الويب).
        if _username_in_use(getattr(sub, "tenant_id", 1) or 1, sub.username):
            raise RadiusConflict("اسم المستخدم مستخدم مسبقًا.")
        # 🔴 اسمُ مشتركٍ مؤرشف: الحفظ «upsert» كان يُحيي الصفّ المؤرشف نفسه
        # (نفس id) فيرث المشتركُ الجديد دفتره الماليّ ويختفي القديم من سلّة
        # المحذوفات (re-test R01 N5). الأسلم: رفضٌ صريح — لا يُمسّ السجلّ
        # القديم ولا أثرُه؛ يسترجعه المشغّل أو يحذفه نهائيًّا ثمّ يُنشئ.
        if _username_archived(getattr(sub, "tenant_id", 1) or 1, sub.username):
            raise RadiusConflict(ARCHIVED_NAME_MSG)
        from .subscriber_validation import validate_subscriber_fields
        validate_subscriber_fields(sub)
        # ⏸ سؤال المالك (F03): «سنة في المرة» عند الإنشاء — مُطفأ حتى يقرّر
        # (core.numbers.CREATE_EXPIRY_ONE_YEAR_RULE). لا أثر وهو مُطفأ.
        from ..core.numbers import check_create_expiry
        check_create_expiry(getattr(sub, "expire_at", None))
        saved = self._adapter.upsert_account(sub)
        if abs(float(saved.balance or 0)) >= 0.005:
            # an opening balance is money too — same ledger row as an edit.
            _record_manual_balance_change(
                actor=actor, before=replace(saved, balance=0.0), saved=saved)
        self._audit.record(actor=actor, action=AUDIT_ACTION_CREATE,
                           target_type="user", target_id=saved.username,
                           payload={"plan_id": saved.plan_id})
        _notify_alert(saved.tenant_id, "subscriber_new", {
            "full_name": saved.full_name or "—", "username": saved.username,
            "plan": _plan_label(saved.tenant_id, saved.plan_id),
            "mobile": saved.mobile or "—", "actor": actor,
        }, dedup_key=saved.username)
        return saved

    @atomic  # read + write under ONE write lock (BEGIN IMMEDIATE, cross-process)
    def update(self, *, actor: str, sub: Subscriber,
               base: Optional[Subscriber] = None,
               clear_expiry: bool = False) -> Subscriber:
        """Save an edited subscriber.

        ``base`` = the row as the caller loaded it before editing. When given,
        only the fields that differ between ``base`` and ``sub`` are applied —
        onto the row re-read here under the write lock — and only those
        columns are written. A renewal, top-up or status change that committed
        after the caller's read therefore survives (re-test R01 N1: 5/24 PATCH
        rounds lost +60 min, 1/25 lost +5 balance, worse with 2 workers).

        ``clear_expiry`` = an explicit «بدون انتهاء» (API ``expire_at: null``,
        app, web checkbox): NULL expiry = never expires. A missing/blank expiry
        otherwise keeps the stored one (see the rule below)."""
        # Fetch the current row UP-FRONT — it serves two purposes and is
        # non-fatal if it fails (brand-new subscriber / lookup error):
        #   1) password preservation (defense in depth, see below);
        #   2) computing the human-readable change diff for the
        #      «تعديل بيانات مشترك» alert (instead of a hardcoded «—»).
        try:
            existing = self._adapter.get_account(sub.username)
        except Exception:  # noqa: BLE001 — lookup failure must not break update
            existing = None
        changed: Optional[set] = None
        if base is not None and existing is not None:
            changed = _changed_fields(base, sub)
            if clear_expiry:
                changed.add("expire_at")
            sub = replace(existing, **{f: (None if (f == "expire_at" and clear_expiry)
                                           else getattr(sub, f))
                                       for f in changed})
        elif clear_expiry:
            sub = replace(sub, expire_at=None)

        # Defense in depth — protect the stored password from being
        # silently wiped by a form submit (or any caller) that didn't
        # carry the password field. RADIUS PAP/CHAP needs the cleartext
        # password; once erased the subscriber can never log in again,
        # and the only fix is asking the operator to remember/reset
        # the password. So: if the incoming DTO has an empty password
        # AND the subscriber already exists with a non-empty one,
        # preserve the existing value. The dedicated reset_password()
        # path is the ONLY way to clear/change a password.
        if not (sub.password or "").strip():
            if existing and (existing.password or "").strip():
                sub = replace(sub, password=existing.password)
                if changed is not None:
                    changed.discard("password")
        # Same defense for the subscription expiry (expire_at). The profile
        # form leaves the date picker blank to mean «keep as-is»; a blank
        # (None) DTO must never NULL the stored expiry — that would silently
        # un-expire / mis-expire the account. Only a concrete date from the
        # picker, the explicit «بدون انتهاء» (clear_expiry), or the dedicated
        # renewal/plan-change/card flows change it.
        if (sub.expire_at is None and existing and existing.expire_at is not None
                and not clear_expiry):
            sub = replace(sub, expire_at=existing.expire_at)
            if changed is not None:
                changed.discard("expire_at")
        _validate(sub)
        from .subscriber_validation import (validate_expiry_jump,
                                            validate_subscriber_fields)
        if existing is None:
            validate_subscriber_fields(sub)
        else:
            fields = changed if changed is not None else _changed_fields(existing, sub)
            validate_subscriber_fields(sub, fields)
            if "expire_at" in fields and sub.expire_at is not None:
                # Owner rule: one edit moves the expiry forward ≤ 1 year.
                validate_expiry_jump(existing.expire_at, sub.expire_at)
        if changed is not None and _supports_partial(self._adapter):
            saved = self._adapter.upsert_account(sub, only_fields=changed)
        else:
            saved = self._adapter.upsert_account(sub)
        # A direct balance write (owner-only) is money: it gets its ledger row
        # like every other balance movement («تعديل رصيد يدوي»).
        if existing is not None:
            _record_manual_balance_change(actor=actor, before=existing, saved=saved)
        # لقطتان مقروءتان قبل/بعد → يَظهر «الحقل: من X إلى Y» في سجل التعديلات
        # (كان يُسجَّل الفعل بلا تفاصيل). قيَم مقروءة: اسم العرض + حالة عربيّة.
        _tid_a = getattr(saved, "tenant_id", None) or 1
        self._audit.record(actor=actor, action=AUDIT_ACTION_UPDATE,
                           target_type="user", target_id=saved.username,
                           before=_sub_snapshot(existing, _tid_a),
                           after=_sub_snapshot(saved, _tid_a))
        _notify_alert(saved.tenant_id, "subscriber_edited", {
            "username": saved.username, "full_name": saved.full_name or "—",
            "changed": _describe_subscriber_changes(existing, saved),
            "actor": actor,
        }, dedup_key=saved.username)
        # «أي عملية حفظ يصير إعادة مطابقة»: أيّ تعديل قد يغيّر قواعد الوصول
        # (الحالة/الجدول/الكوتا/حدّ الأجهزة/قفل MAC/العرض…) يُعيد فحص جلسات
        # هذا المشترك الحيّة ويطرد المخالف فورًا.
        _reconcile_policy(saved.tenant_id, usernames=[saved.username],
                          reason="subscriber_update")
        return saved

    def rename_username(self, *, actor: str, old_username: str,
                        new_username: str, disconnect: bool = True) -> dict:
        """SAFE rename of a subscriber's LOGIN username — the RADIUS auth key.

        The username is stored BY VALUE across auth (radcheck/radreply/
        radusergroup), accounting (radacct/radpostauth), money, per-user rules
        and portal tables. This performs an ATOMIC cascade rename over all of
        them (adapter.rename_account → single DB transaction), re-provisions
        RADIUS under the new name, and — if the subscriber is online — CoA-
        disconnects the live session so they re-authenticate under the new
        name instead of silently continuing under the old one.

        Validates first: non-empty, allowed charset, and unique (not already a
        subscriber/card in the tenant). Rejects with a clear Arabic error
        otherwise. Records a «تعديل: اسم الدخول من X إلى Y» audit event."""
        old_username = (old_username or "").strip()
        new_username = (new_username or "").strip()
        if not new_username:
            raise RadiusValidationError("اسم الدخول الجديد مطلوب.")
        if not _USERNAME_RE.match(new_username):
            raise RadiusValidationError(
                "اسم الدخول يسمح بالأحرف اللاتينية والأرقام والرموز . _ - @ فقط "
                "(بدون مسافات، حتى ٦٤ حرفًا).")
        # Load the current account (raises RadiusNotFound if the old name is
        # unknown) — also the source of tenant_id for the audit + collision scope.
        existing = self._adapter.get_account(old_username)
        if new_username == old_username:
            return {"renamed": False, "old": old_username, "new": old_username,
                    "had_live_session": False, "tables": {}}
        _validate_new_username(new_username)
        # Friendly uniqueness pre-check (the adapter enforces it authoritatively
        # too, inside the same transaction as the cascade). Case- and
        # space-insensitive like create: «R10_001» beside «r10_001» was
        # accepted (re-test R10 N2 / R01 L3). Only the row itself may differ
        # by case (a case-only rename of its own name).
        _tid_scope = getattr(existing, "tenant_id", None) or 1
        if (self._username_taken(new_username, tenant_id=_tid_scope)
                or _username_in_use(_tid_scope, new_username,
                                    exclude_id=getattr(existing, "id", None))):
            raise RadiusValidationError(
                f"اسم الدخول «{new_username}» مستخدَم بالفعل لمشترك أو بطاقة أخرى.")
        # an archived subscriber still owns its name (UNIQUE row) — was a 500.
        if _username_archived(_tid_scope, new_username,
                              exclude_id=getattr(existing, "id", None)):
            raise RadiusConflict(ARCHIVED_NAME_MSG)

        result = self._adapter.rename_account(
            old_username, new_username, disconnect=disconnect)

        # Audit the exact before→after the change-log renders as
        # «اسم الدخول: من X إلى Y» (login_username label added to audit_format).
        self._audit.record(
            actor=actor, action=AUDIT_ACTION_UPDATE, target_type="user",
            target_id=new_username,
            before={"login_username": old_username},
            after={"login_username": new_username},
        )
        return {"renamed": True, "old": old_username, "new": new_username,
                "had_live_session": bool((result or {}).get("had_live_session")),
                "tables": (result or {}).get("tables") or {}}

    def _username_taken(self, username: str, *, tenant_id: int = 1) -> bool:
        """True if `username` already belongs to another subscriber or card in
        the tenant. Uses the storage-level check when available (covers the
        `cards` table too); falls back to an account probe otherwise."""
        try:
            from ..db.repos import subscribers_repo
            return bool(subscribers_repo.username_exists(tenant_id, username))
        except Exception:  # noqa: BLE001 — fall back to a plain account probe
            pass
        try:
            acc = self._adapter.get_account(username)
            return acc is not None
        except Exception:  # noqa: BLE001 — RadiusNotFound → free
            return False

    @atomic
    def change_plan(self, *, actor: str, username: str, plan_id: int,
                    policy: str) -> dict:
        """تغيير عرض المشترك وفق سياسةٍ صريحة (قواعدٌ موثَّقة في
        ``docs/radius/plan_change_policy.md``):

        • الاتجاه (أرخص/أغلى/مساوٍ) **بسعر الدقيقة** لا بالسعر الإجماليّ —
          عرضٌ بلا مدّة يُحسب شهرًا (43200 د) كما في التسعير والدفعات. العرض
          المجّانيّ (أو «بلا عرض») سعرُ دقيقته صفر: الانتقال منه لمدفوعٍ «أغلى».
        • ``lower_*`` للأرخص فقط، ``higher_*`` للأغلى فقط، ``neutral_keep_expiry``
          للمساوي فقط — لا تغييرَ «محايد» يتجاوز فرق السعر.
        • لا تغيير إلى العرض الحاليّ نفسه، ولا إلى عرضٍ معطّل أو مؤرشف.
        • التعويض/الإنقاص يُزيح تاريخ الانتهاء بالفارق فقط (تُحفظ الثواني)؛
          فارقٌ صفريّ لا يمسّ التاريخ.
        """
        if plan_id <= 0:
            raise RadiusValidationError("اختر العرض الجديد.")
        allowed = {
            "lower_compensate",
            "lower_keep_expiry",
            "higher_debt",
            "higher_reduce_days",
            "higher_keep_expiry",
            "neutral_keep_expiry",
        }
        if policy not in allowed:
            raise RadiusValidationError("طريقة تغيير العرض غير معروفة.")

        sub = self._adapter.get_account(username)
        if sub.plan_id and int(sub.plan_id) == int(plan_id):
            raise RadiusValidationError("العرض المختار هو العرض الحاليّ للمشترك — اختر عرضًا آخر.")
        old_plan = None
        if sub.plan_id:
            try:
                old_plan = self._adapter.get_profile(int(sub.plan_id))
            except Exception:  # noqa: BLE001
                # عرضٌ حاليّ مؤرشف ما زال عقدَ المشترك: سعره يحدّد الاتجاه
                # (كان «بلا عرض» ⇒ مجّانيّ ⇒ كلّ انتقالٍ «أرخص» بلا تعويض).
                try:
                    from ..db.repos import plans_repo
                    old_plan = plans_repo.get_plan(int(sub.tenant_id or 1), int(sub.plan_id),
                                                   include_deleted=True)
                except Exception:  # noqa: BLE001
                    old_plan = None
        try:
            new_plan = self._adapter.get_profile(plan_id)
        except RadiusNotFound:
            raise RadiusNotFound("العرض المختار غير موجود أو مؤرشف.")
        if getattr(new_plan, "deleted_at", None):
            raise RadiusNotFound("العرض المختار غير موجود أو مؤرشف.")
        if not bool(getattr(new_plan, "enabled", True)):
            raise RadiusValidationError(
                "العرض المختار معطّل — فعّله من صفحة العروض أوّلًا أو اختر عرضًا آخر.")

        old_price = float(getattr(old_plan, "price", 0) or 0)
        new_price = float(getattr(new_plan, "price", 0) or 0)
        old_rate = plan_rate_per_minute(old_plan)
        new_rate = plan_rate_per_minute(new_plan)
        direction = plan_change_direction(old_plan, new_plan)
        if policy.startswith("lower_") and direction != "lower":
            raise RadiusValidationError(
                "العرض المختار ليس أرخص من الحاليّ (المقارنة بسعر اليوم/الدقيقة) — "
                "اختر أحد خيارات العرض " + _DIRECTION_AR[direction] + ".")
        if policy.startswith("higher_") and direction != "higher":
            raise RadiusValidationError(
                "العرض المختار ليس أغلى من الحاليّ (المقارنة بسعر اليوم/الدقيقة) — "
                "اختر أحد خيارات العرض " + _DIRECTION_AR[direction] + ".")
        if policy == "neutral_keep_expiry" and direction != "neutral":
            raise RadiusValidationError(
                "العرض المختار " + _DIRECTION_AR[direction] + " من الحاليّ — «تغيير العرض فقط» "
                "للعروض المتساوية السعر؛ اختر تعويضًا/إنقاصًا/دينًا أو «بدون تعويض/دين».")

        now = datetime.utcnow()
        remaining = _remaining_minutes(sub.expire_at, now)
        new_expire_at = sub.expire_at
        minute_delta = 0
        debt_amount = 0.0

        if policy in {"lower_compensate", "higher_reduce_days", "higher_debt"} and remaining > 0:
            if policy == "lower_compensate":
                if new_rate <= 0:
                    raise RadiusValidationError(
                        "لا يمكن التعويض بأيامٍ على عرضٍ مجّانيّ — اختر «تغيير العرض بدون تعويض».")
                adjusted = max(remaining, int(round((remaining * old_rate) / new_rate)))
                minute_delta = adjusted - remaining
            elif policy == "higher_reduce_days":
                adjusted = min(remaining, int(round((remaining * old_rate) / new_rate)))
                minute_delta = max(0, adjusted) - remaining
            elif policy == "higher_debt":
                debt_amount = round(max((new_rate - old_rate) * remaining, 0), 2)
            # 🔴 F04 M1 / F08 H2 — سقوف المالك تسري على تغيير العرض أيضًا (قرارٌ
            # موثَّق: **رفض 422 لا قصّ** — القصّ يُسقط من حقّ المشترك بصمت):
            #   • التعويض وقتٌ يُضاف ⇒ ≤ سنة في العمليّة الواحدة (120 ₪ ⇒ 0.01 ₪
            #     كان يعطي 518 مليون دقيقة وانتهاءً سنة 3012)؛
            #   • الانتهاء الناتج ≤ 2100؛
            #   • دين فرق السعر مبلغٌ لا يُحوَّل وقتًا ⇒ ≤ 100,000.
            if minute_delta > EXTEND_MAX_MINUTES:
                raise NonFiniteNumber(
                    f"{EXTEND_TOO_LONG_AR} — التعويض المحسوب لهذا التغيير "
                    f"{_fmt_minutes_ar(minute_delta)}. اختر «تغيير العرض بدون تعويض» "
                    "ثم مدّد يدويًّا على دفعات، أو اختر عرضًا أقرب سعرًا.",
                    details={"field": "policy", "minute_delta": minute_delta})
            if debt_amount > ACTION_AMOUNT_MAX + 1e-9:
                raise NonFiniteNumber(
                    f"دين فرق السعر المحسوب ({debt_amount:.2f}) يتجاوز الحدّ الأقصى "
                    f"للعملية الواحدة ({int(ACTION_AMOUNT_MAX)}). اختر «إنقاص الأيام» "
                    "أو «بدون دين/تعويض».",
                    details={"field": "policy", "debt_amount": debt_amount})
            if minute_delta:
                # إزاحةٌ بالفارق فقط: ثواني النهاية الأصليّة تبقى (كان يُعاد بناؤها
                # من «الآن + المتبقّي بالدقائق» فتضيع حتى 59 ثانية حتى بفارقٍ صفريّ).
                # add_minutes_capped: ما بعد 2100 (أو الفائض) ⇒ 422 لا 500.
                new_expire_at = add_minutes_capped(sub.expire_at, minute_delta)
                if new_expire_at < now:
                    new_expire_at = now
        debt_amount = debt_amount + 0.0

        new_balance = float(sub.balance or 0) - debt_amount
        # الدين يُطرح من رصيد المشترك ⇒ يُقيَّد بعملة الرصيد (عملة النظام) لا
        # بعملة العرض الجديد (كان يُكتب USD على رصيدٍ بالشيكل). لا سعر صرف في
        # النظام؛ النشر أحاديّ العملة وعملة العرض وسمٌ لا يُحوَّل.
        saved = self._adapter.upsert_account(
            replace(
                sub,
                plan_id=plan_id,
                expire_at=new_expire_at,
                balance=new_balance,
            )
        )
        # فترة كوتة جديدة: إضافات الكوتة للعرض السابق لا تُورَّث للجديد (كان
        # 1024+100 يبقى 1124 بعد الترقية إلى 20 GB أو لعرضٍ بلا كوتة).
        from . import quota_period
        if quota_period.start_new_period(saved, reason="plan_change") is not None:
            saved = self._adapter.get_account(username)
        if debt_amount > 0:
            _record_plan_change_debt(
                actor=actor,
                subscriber=saved,
                old_plan_id=sub.plan_id,
                new_plan_id=plan_id,
                amount=debt_amount,
                currency=_currency(""),
                remaining_minutes=remaining,
            )
        self._audit.record(
            actor=actor,
            action="change_plan",
            target_type="user",
            target_id=username,
            payload={
                "old_plan_id": sub.plan_id,
                "new_plan_id": plan_id,
                "policy": policy,
                "old_price": old_price,
                "new_price": new_price,
                "remaining_minutes": remaining,
                "minute_delta": minute_delta,
                "debt_amount": debt_amount,
                "new_expire_at": new_expire_at.isoformat() if new_expire_at else None,
            },
        )
        # إشعار المشترك بتغيير باقته (عبر المحرّك الموحّد؛ {prof} من السياق،
        # {old_prof} إضافيّ). يُسلَّم للقنوات المُفعَّلة في «إشعارات المشتركين».
        _notify_subscriber(saved.tenant_id, "plan_changed", subscriber=saved,
                           context={"old_prof": getattr(old_plan, "name", "") or ""})
        # تغيير الباقة قد يُدخل قواعد أضيق (أيام/كوتا/حدّ أجهزة) — إعادة فحص
        # جلسات هذا المشترك الحيّة فورًا وطرد المخالف.
        _reconcile_policy(saved.tenant_id, usernames=[username],
                          reason="plan_change")
        return {
            "subscriber": saved,
            "old_plan": old_plan,
            "new_plan": new_plan,
            "policy": policy,
            "direction": direction,
            "remaining_minutes": remaining,
            "minute_delta": minute_delta,
            "debt_amount": debt_amount,
        }

    def send_sms(self, *, actor: str, username: str, message: str,
                 channel: str = "sms") -> dict:
        # القناة: «sms» أو «whatsapp» — كلاهما قنوات HTTP مفعّلة في محرك
        # الإشعارات (comms_providers.HTTP_CHANNELS)؛ أي قيمة أخرى مرفوضة.
        ch = (channel or "sms").strip().lower()
        if ch not in {"sms", "whatsapp"}:
            raise RadiusValidationError("قناة الإرسال غير مدعومة.")
        body = (message or "").strip()
        if not body:
            raise RadiusValidationError("نص الرسالة مطلوب.")
        sub = self._adapter.get_account(username)
        if not sub.id:
            raise RadiusValidationError("المشترك غير صالح.")
        if not (sub.mobile or "").strip():
            raise RadiusValidationError("لا يوجد رقم جوال لهذا المشترك.")

        # تعويض {username} بالاسم الفعلي — مفيد في الإرسال الجماعي حيث
        # تُرسل نفس الرسالة لعدة مشتركين (الواجهة تُبقي المتغيّر كما هو).
        body = body.replace("{username}", username)

        from .notification_campaigns import NotificationCampaignError, NotificationCampaignService

        try:
            result = NotificationCampaignService(tenant_id=sub.tenant_id).send_manual(
                audience={"target": "selected_subscribers", "ids": [int(sub.id)], "limit": 1},
                channel=ch,
                message=body,
                actor=actor,
            )
        except NotificationCampaignError as exc:
            raise RadiusValidationError(str(exc)) from exc
        self._audit.record(
            actor=actor,
            action="subscriber.sms_queue",
            target_type="user",
            target_id=username,
            payload={"queued_count": result.get("queued_count", 0), "channel": ch},
        )
        return result

    @atomic
    def reset_daily_quota(self, *, actor: str, username: str,
                          charge_mode: str = "free", amount: float = 0.0,
                          currency: str = "", notes: str = "") -> Subscriber:
        """Refresh the subscriber's daily allowance (zero the used counters).

        Optionally bills the restore like add_quota: free (no charge), paid
        (cash credited to the ledger), or debt (recorded as a debit + the
        amount subtracted from the subscriber balance). Defaults to free, so
        existing callers keep the original no-cost behaviour.
        """
        currency = _currency(currency)
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("طريقة الاستعادة غير معروفة.")
        amount = _charge_amount(charge_mode, amount)

        sub = self._adapter.get_account(username)
        # F04 N-L1: لا كوتة يوميّة ولا حدّ وقتٍ يوميّ ⇒ لا شيء يُستعاد — كان يُحصِّل
        # المبلغ (رصيد −5) على عرضٍ بلا أيّ سقف يوميّ. يُرفض في كلّ الأنماط.
        if not daily_reset_applicable(sub):
            raise NothingToReset(NOTHING_TO_RESET_AR)
        _require_paid_balance(sub, amount, charge_mode)
        changes = {
            "used_seconds": 0,
            "used_bytes_in": 0,
            "used_bytes_out": 0,
            "balance": float(sub.balance or 0),
        }
        if charge_mode in {"paid", "debt"}:
            # Both paid and debt CONSUME the subscriber balance: «paid» pays from
            # the subscriber's prepaid wallet, «debt» lets it go on credit (into
            # the negative). The only difference is the ledger classification and
            # the admin alert — never the money. A paid add-on must therefore
            # debit the balance just like a renewal; it must NOT report «مدفوعة
            # بقيمة X» while leaving the balance untouched (the confirmed bug).
            changes["balance"] = float(sub.balance or 0) - float(amount)
        saved = self._adapter.upsert_account(replace(sub, **changes))
        # يومٌ جديد من الآن للكوتة والوقت اليوميّين (العدّادان أعلاه لا يكتبهما
        # أحدٌ لمشتركٍ حقيقيّ — الاستهلاك يُقرأ من radacct).
        from . import quota_period
        quota_period.reset_daily(saved)
        if charge_mode in {"paid", "debt"}:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="quota_topup" if charge_mode == "paid" else "debt",
                direction="debit",  # balance decreases in BOTH modes → debit
                amount=float(amount),
                currency=currency,
                source_type="subscriber_daily_quota_reset",
                notes=notes or ("استعادة كوتة يومية مدفوعة" if charge_mode == "paid"
                                else "استعادة كوتة يومية على الدين"),
                metadata={"charge_mode": charge_mode},
            )
        self._audit.record(
            actor=actor,
            action="subscriber.daily_quota_reset",
            target_type="user",
            target_id=username,
            payload={
                "previous_used_seconds": sub.used_seconds,
                "previous_used_bytes_in": sub.used_bytes_in,
                "previous_used_bytes_out": sub.used_bytes_out,
                "charge_mode": charge_mode,
                "amount": float(amount) if charge_mode in {"paid", "debt"} else 0,
            },
        )
        # تنبيه إدارة — استعادة/تصفير كوتا (تصفير العدّادات اليومية).
        _detail = "تصفير الاستهلاك اليومي"
        if charge_mode in {"paid", "debt"}:
            _detail += (" — مدفوعة " if charge_mode == "paid" else " — على الدين ") \
                + _fmt_money_ar(amount, currency)
        _notify_alert(saved.tenant_id, "quota_restored", {
            "username": username,
            "detail": _detail,
            "actor": actor,
        }, dedup_key=f"quota_restore:{username}")
        return saved

    @atomic
    def add_quota(self, *, actor: str, username: str, quota_mb: int,
                  quota_target: str = "combined", charge_mode: str = "free",
                  amount: float = 0.0, currency: str = "",
                  notes: str = "", quota_window: str = "auto") -> Subscriber:
        """إضافة كوتة **للسقف الساري** — بالأولويّة: الإجماليّ (للفترة الحاليّة
        فقط) ⇒ الشهريّ (هذا الشهر) ⇒ اليوميّ (اليوم). ``quota_window`` يفرض
        نافذةً بعينها (total/monthly/daily). راجع ``quota_period``."""
        currency = _currency(currency)
        if quota_mb <= 0:
            raise RadiusValidationError("حجم الكوتة يجب أن يكون أكبر من صفر.")
        if quota_target not in {"combined", "download", "upload"}:
            raise RadiusValidationError("نوع الكوتة غير معروف.")
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("طريقة الإضافة غير معروفة.")
        amount = _charge_amount(charge_mode, amount)
        quota_window = (quota_window or "auto").strip().lower()
        if quota_window not in {"auto", "total", "monthly", "daily"}:
            raise RadiusValidationError("نافذة الكوتة غير معروفة (total أو monthly أو daily).")

        sub = self._adapter.get_account(username)
        # 🔴 الإضافة **تُضاف إلى السقف الساري** لا تحلّ محلّه. كان المسار يجمع
        # على تجاوز المشترك (صفرٌ غالبًا) فيصير التجاوزُ 100 ويغلب كوتا الباقة
        # 1024 ⇒ «إضافة» 100 تُنزل السقف إلى 100. والسقف المنفَّذ (policy_engine
        # ._effective_quota_mb) واحدٌ: تجاوز المشترك (إجماليّ، أو تنزيل+رفع)
        # وإلّا ``plan.quota_total_mb``.
        plan = None
        if sub.plan_id:
            try:
                plan = self._adapter.get_profile(int(sub.plan_id))
            except Exception:  # noqa: BLE001 — عرضٌ مؤرشف/محذوف ⇒ بلا كوتا عرض
                plan = None
        from . import quota_period
        sub_combined = int(sub.combined_quota_mb or 0)
        sub_down = int(sub.download_quota_mb or 0)
        sub_up = int(sub.upload_quota_mb or 0)
        plan_total = int(getattr(plan, "quota_total_mb", 0) or 0) if plan else 0
        per_direction = sub_combined <= 0 and (sub_down > 0 or sub_up > 0)
        has_total = sub_combined > 0 or per_direction or plan_total > 0
        wcaps = quota_period.plan_window_caps(plan)
        if quota_window == "auto":
            quota_window = ("total" if has_total else
                            "monthly" if any(wcaps["monthly"].values()) else
                            "daily" if any(wcaps["daily"].values()) else "")
        if not quota_window or (quota_window == "total" and not has_total) or (
                quota_window in wcaps and not any(wcaps[quota_window].values())):
            raise RadiusValidationError(
                "هذا المشترك بلا سقف كوتة (استهلاك غير محدود) — لا يوجد رصيد كوتة "
                "لتُضاف إليه. لتحديد سقف عدّل كوتة المشترك أو باقته.")
        changes = {
            "quota_limit_enabled": True,
            "combined_quota_mb": sub.combined_quota_mb,
            "download_quota_mb": sub.download_quota_mb,
            "upload_quota_mb": sub.upload_quota_mb,
            "balance": float(sub.balance or 0),
        }
        window_label = {"monthly": "الشهريّة", "daily": "اليوميّة"}.get(quota_window, "")
        if quota_window != "total":
            # كوتة الباقة الشهريّة/اليوميّة: الإضافة لهذا الشهر/اليوم فقط، ولا
            # يُنشأ تجاوزٌ دائم على المشترك.
            caps = wcaps[quota_window]
            if not caps.get(quota_target):
                if quota_target == "combined":
                    raise RadiusValidationError(
                        f"كوتة هذا المشترك {window_label} بالاتجاه (تنزيل/رفع) — "
                        "اختر «تنزيل» أو «رفع».")
                raise RadiusValidationError(
                    f"كوتة هذا المشترك {window_label} لا تشمل هذا الاتجاه — "
                    "أضِف إلى «الكوتة الإجماليّة» أو الاتجاه المحدَّد في الباقة.")
            changes = {"balance": float(sub.balance or 0)}
        elif per_direction:
            if quota_target == "download":
                changes["download_quota_mb"] = sub_down + quota_mb
            elif quota_target == "upload":
                changes["upload_quota_mb"] = sub_up + quota_mb
            else:
                # سقفٌ إجماليّ جديد = مجموع الاتجاهين + الإضافة (نفس ما يُنفَّذ).
                changes["combined_quota_mb"] = sub_down + sub_up + quota_mb
        else:
            if quota_target != "combined":
                raise RadiusValidationError(
                    "كوتة هذا المشترك إجماليّة (تنزيل + رفع معًا) — أضِف إلى "
                    "«الكوتة الإجماليّة» لا إلى اتجاهٍ واحد.")
            base_total = sub_combined if sub_combined > 0 else plan_total
            changes["combined_quota_mb"] = base_total + quota_mb
        _require_paid_balance(sub, amount, charge_mode)
        if charge_mode in {"paid", "debt"}:
            # See reset_daily_quota: paid consumes the prepaid balance, debt goes
            # on credit. Both debit the wallet by `amount`; paid must never leave
            # the balance untouched while claiming «مدفوعة».
            changes["balance"] = float(sub.balance or 0) - float(amount)
        saved = self._adapter.upsert_account(replace(sub, **changes))
        # الإضافة تخصّ الفترة/اليوم/الشهر الجاري فقط — تُزال مع الفترة التالية.
        window_total = None
        if quota_window == "total":
            quota_period.record_total_topup(sub, saved, quota_mb)
        else:
            window_total = quota_period.record_window_topup(
                saved, quota_window, quota_target, quota_mb)
        if charge_mode in {"paid", "debt"}:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="quota_topup" if charge_mode == "paid" else "debt",
                direction="debit",  # balance decreases in BOTH modes → debit
                amount=float(amount),
                currency=currency,
                source_type="subscriber_quota_topup",
                notes=notes or ("إضافة كوتة مدفوعة" if charge_mode == "paid" else "إضافة كوتة على الدين"),
                metadata={
                    "quota_mb": quota_mb,
                    "quota_target": quota_target,
                    "quota_window": quota_window,
                    "charge_mode": charge_mode,
                },
            )
        self._audit.record(
            actor=actor,
            action="subscriber.quota_topup",
            target_type="user",
            target_id=username,
            payload={
                "quota_mb": quota_mb,
                "quota_target": quota_target,
                "quota_window": quota_window,
                "charge_mode": charge_mode,
                "amount": amount,
                "currency": currency,
            },
        )
        # تنبيه إدارة — إضافة كوتا (الإجمالي الجديد للهدف المعنيّ).
        _new_total = {
            "combined": saved.combined_quota_mb,
            "download": saved.download_quota_mb,
            "upload": saved.upload_quota_mb,
        }.get(quota_target, saved.combined_quota_mb)
        if window_total is not None:
            _new_total = (int(wcaps[quota_window].get(quota_target) or 0)
                          + int(window_total.get(quota_target) or 0))
        _target_ar = {"combined": "", "download": " (تنزيل)",
                      "upload": " (رفع)"}.get(quota_target, "")
        _notify_alert(saved.tenant_id, "quota_added", {
            "username": username,
            "quota": f"{int(quota_mb)} م.ب{_target_ar}",
            "new_total": f"{int(_new_total or 0)} م.ب",
            "actor": actor,
        }, dedup_key=f"quota:{username}:{quota_mb}:{quota_target}")
        return saved

    @atomic
    def add_cash_balance(self, *, actor: str, username: str, amount: float,
                         currency: str = "", notes: str = "",
                         settled_deduction: float = 0.0) -> Subscriber:
        currency = _currency(currency)
        amount = round_money(finite_float(amount, field="amount"))
        if amount <= 0:
            raise RadiusValidationError("المبلغ يجب أن يكون أكبر من صفر.")
        action_amount(amount, field="amount")
        # Net wallet credit = cash received − the part used to settle open loans.
        # Loans the operator chose to «خصم» are cleared separately (their own
        # settlement ledger), so ONLY the remainder lands in the wallet — the
        # balance therefore reflects the deductions. With no settlements
        # (settled_deduction=0) this is a plain full-amount credit as before.
        settled_deduction = max(float(settled_deduction or 0.0), 0.0)
        credit = round(max(float(amount) - settled_deduction, 0.0), 2)
        sub = self._adapter.get_account(username)
        previous = float(sub.balance or 0)
        saved = self._adapter.upsert_account(
            replace(sub, balance=previous + credit)
        )
        if credit > 0:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="cash_balance",
                direction="credit",
                amount=credit,
                currency=currency,
                source_type="subscriber_cash_balance",
                notes=notes or "إضافة رصيد نقدي",
                metadata={
                    "previous_balance": previous,
                    "new_balance": float(saved.balance or 0),
                    "gross_amount": round(float(amount), 2),
                    "settled_deduction": round(settled_deduction, 2),
                },
            )
        self._audit.record(
            actor=actor,
            action="subscriber.cash_balance_add",
            target_type="user",
            target_id=username,
            payload={
                "amount": round(float(amount), 2),
                "credited": credit,
                "settled_deduction": round(settled_deduction, 2),
                "currency": currency,
            },
        )
        # تنبيه إدارة — إضافة رصيد (يُرسَل فقط حين دخل رصيد فعليّ للمحفظة).
        if credit > 0:
            _notify_alert(saved.tenant_id, "credit_added", {
                "username": username,
                "amount": _fmt_money_ar(credit, currency),
                "new_balance": _fmt_money_ar(saved.balance, currency),
                "actor": actor,
            }, dedup_key=f"credit:{username}:{credit}")
        return saved

    @atomic
    def apply_payment_to_balance(self, *, actor: str, username: str,
                                 amount: float, payment_id: int | None = None) -> float:
        """يسوي جزءًا من دفعة نقدية مع رصيد سالب مسجل كدين.

        يرفع الرصيد باتجاه الصفر دون تجاوزه، ويسجل قيد `debt_settlement`
        موازنًا لقيد الدين الأصلي. تقارير الدخل المبنية على `payment`
        تبقى كما هي. ترجع الدالة المبلغ الذي تم تطبيقه فعليًا.
        """
        if amount is None or float(amount) <= 0:
            return 0.0
        sub = self._adapter.get_account(username)
        previous = float(sub.balance or 0)
        due = max(-previous, 0.0)
        settle = round(min(float(amount), due), 2)
        if settle <= 0:
            return 0.0
        saved = self._adapter.upsert_account(replace(sub, balance=previous + settle))
        _record_subscriber_ledger(
            actor=actor,
            subscriber=saved,
            entry_type="debt_settlement",
            direction="credit",
            amount=settle,
            currency=default_currency(),
            source_type="payment_balance_settlement",
            notes="تسوية دين من دفعة نقدية",
            metadata={
                "previous_balance": previous,
                "new_balance": float(saved.balance or 0),
                # إلغاء الدفعة يعكس هذا التسديد (يعود الدين إلى الرصيد).
                "payment_id": int(payment_id) if payment_id else None,
            },
        )
        self._audit.record(
            actor=actor,
            action="subscriber.debt_settled_from_payment",
            target_type="user",
            target_id=username,
            payload={"amount": settle},
        )
        return settle

    # 🔴 تعطيل/تفعيل = قراءةُ الصفّ ثم كتابتُه كلّه (upsert). خارج معاملةٍ كانت
    # القراءة تسبق قفل الكتابة، فيكتب التعطيلُ نسخةً قديمة فوق تمديدٍ متزامن
    # (1 من 170 جولة فقدت ساعة ودينًا — الرصيد ≠ الدفتر). @atomic = BEGIN
    # IMMEDIATE قبل القراءة كبقيّة الإجراءات؛ الطرد/الإشعار بعد COMMIT.
    @atomic
    def disable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        # status only — a full-row write here undid a concurrent extend (R02).
        _upsert_fields(self._adapter, replace(u, status=STATUS_DISABLED), {"status"})
        self._audit.record(actor=actor, action=AUDIT_ACTION_DISABLE,
                           target_type="user", target_id=username)
        _notify_subscriber(u.tenant_id, "subscriber_disabled", subscriber=u)
        # إنفاذ فوريّ: التعطيل يطرد الجلسة الحيّة الآن (PoD) لا عند إعادة
        # المصادقة فقط — «عملت تعطيل ما أرسل أمر قطع، ظل متصل».
        _reconcile_policy(u.tenant_id, usernames=[username],
                          reason="subscriber_disable")

    @atomic
    def enable(self, *, actor: str, username: str) -> None:
        u = self._adapter.get_account(username)
        _upsert_fields(self._adapter, replace(u, status=STATUS_ENABLED), {"status"})
        self._audit.record(actor=actor, action=AUDIT_ACTION_ENABLE,
                           target_type="user", target_id=username)
        _notify_subscriber(u.tenant_id, "subscriber_reactivated", subscriber=u)

    def reset_password(self, *, actor: str, username: str, new_password: str) -> None:
        if not new_password:
            raise RadiusValidationError("كلمة المرور الجديدة مطلوبة.")
        validate_new_password(new_password)
        # حساب غير موجود → RadiusNotFound (404) بدل «تمّ» كاذب + مزامنة راوتر
        # لمستخدم لا وجود له.
        self._adapter.get_account(username)
        self._adapter.reset_password(username, new_password)
        self._audit.record(actor=actor, action=AUDIT_ACTION_RESET_PASSWORD,
                           target_type="user", target_id=username)

    @atomic
    def extend_time(self, *, actor: str, username: str, minutes: int,
                    charge_mode: str = "free", amount: float = 0.0,
                    currency: str = "", notes: str = "") -> Subscriber:
        if minutes <= 0:
            raise RadiusValidationError("المدّة يجب أن تكون أكبر من صفر.")
        # قرار المالك: أقصى تمديد في العمليّة الواحدة سنة (التكرار مسموح).
        check_extend_minutes(minutes)
        currency = _currency(currency)
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("طريقة الإضافة غير معروفة.")
        amount = _charge_amount(charge_mode, amount)
        u = self._adapter.get_account(username)
        _require_paid_balance(u, amount, charge_mode)
        # 🔴 المرساة: **الأبعدُ** بين نهايته الحاليّة والآن — لا نهايتُه وحدَها.
        #
        # كان `(u.expire_at or now) + delta`. فمشتركٌ **انتهى** أمس تُضاف إليه
        # أربعُ ساعاتٍ فتصير نهايتُه أمسِ + 4س — أي **ما زالت في الماضي**،
        # والشاشة تقول «تم التمديد» وهو لا يستطيع الدخول. مُشاهَدٌ حيًّا
        # (2026-08-30، خادم سمير): تمديدان بـ240 دقيقة أنتجا نهايتين
        # مضتا قبل 10 و18 ساعة — وصاحبُهما ظنّ أنّه مدّد.
        #
        # وهو نفسُ الخطأ الذي أُصلح للبطاقات في `grant_card_time`؛ صار هنا
        # أيضًا:
        #   • حيٌّ    → يُمدَّد من نهايته فلا يُسرق ما تبقّى له.
        #   • منتهٍ  → يُمدَّد من **الآن** فينال المدّة كاملةً فعلًا.
        _now = datetime.utcnow()
        _anchor = max(u.expire_at, _now) if u.expire_at else _now
        # فائضٌ/ما بعد 2100 ⇒ 422 (كان 9999-12-31 + دقيقة ⇒ 500).
        new_exp = add_minutes_capped(_anchor, minutes)
        new_balance = float(u.balance or 0)
        if charge_mode in {"paid", "debt"}:
            # paid pays from the prepaid balance, debt goes on credit — both
            # consume the wallet by `amount`. A paid extension must never report
            # «مدفوعة» while leaving the balance untouched (the confirmed bug).
            new_balance -= float(amount)
        return self._commit_expiry(
            actor=actor, u=u, new_exp=new_exp, charge_mode=charge_mode,
            amount=amount, currency=currency, notes=notes,
            action="extend_time", minutes=minutes,
            duration_label=_fmt_minutes_ar(minutes),
            ledger_note=("إضافة وقت مدفوعة" if charge_mode == "paid"
                         else "إضافة وقت على الدين"),
        )

    @atomic
    def set_expiry(self, *, actor: str, username: str, expire_at: datetime,
                   charge_mode: str = "free", amount: float = 0.0,
                   currency: str = "", notes: str = "") -> Subscriber:
        """تعيينُ لحظةِ الانتهاء **بالضبط** — لا إضافةَ مدّةٍ فوق ما مضى.

        `extend_time` تجيب عن «كم أُضيف؟»؛ وهذه تجيب عن «متى ينتهي؟». وهما
        سؤالان مختلفان: المشغّل الذي يريد نهايةَ الشهر لا يحسب الفارقَ بيده،
        ولا يقبل أن تُدفع النهايةُ ساعاتٍ لأنّ التمديدَ تراكَم على تمديد.

        اللحظةُ الواصلةُ هنا **UTC ساكن** (حوّلها المسارُ عبر `from_local`)،
        وتُكتب كما هي: لا مرساةَ ولا `max(now, …)` — فالتعيينُ صريحٌ بطبيعته،
        وتقصيرُ اشتراكٍ أو إنهاؤه الآن طلبٌ مشروع.

        الفارقُ عن نهايته الفعليّة (الأبعد بين نهايته الحاليّة والآن) يُحسب
        ويُسجّل بوصفه «الدقائق» — كي يقرأ التقريرُ والدفترُ الماليّ الأثرَ
        نفسَه سواءٌ أُضيفت مدّةٌ أم عُيّن تاريخ؛ وقد يكون سالبًا عند التقصير.
        """
        if not isinstance(expire_at, datetime):
            raise RadiusValidationError("تاريخ الانتهاء مطلوب.")
        check_expiry(expire_at)
        if charge_mode not in {"free", "paid", "debt"}:
            raise RadiusValidationError("طريقة الإضافة غير معروفة.")
        amount = _charge_amount(charge_mode, amount)
        currency = _currency(currency)
        u = self._adapter.get_account(username)
        _require_paid_balance(u, amount, charge_mode)
        # Owner rule (2026-09-29): one set-expiry moves the end ≤ 1 year past
        # max(now, current end); never beyond 2100.
        from .subscriber_validation import validate_expiry_jump
        validate_expiry_jump(u.expire_at, expire_at)
        _now = datetime.utcnow()
        _anchor = max(u.expire_at, _now) if u.expire_at else _now
        minutes = int(round((expire_at - _anchor).total_seconds() / 60))
        return self._commit_expiry(
            actor=actor, u=u, new_exp=expire_at, charge_mode=charge_mode,
            amount=amount, currency=currency, notes=notes,
            action="set_expiry", minutes=minutes,
            duration_label=f"حتّى {_fmt_dt_local(expire_at)}",
            ledger_note="تعيين تاريخ الانتهاء",
        )

    def _commit_expiry(self, *, actor: str, u: Subscriber, new_exp: datetime,
                       charge_mode: str, amount: float, currency: str,
                       notes: str, action: str, minutes: int,
                       duration_label: str, ledger_note: str) -> Subscriber:
        """الكتابةُ والدفترُ والتدقيقُ والتنبيه — مشتركةٌ بين التمديد والتعيين.

        المسلكان يختلفان في **حساب** النهاية فقط؛ وما بعدها واحد. فصلُه هنا
        يمنع أن يُصلَح عطبٌ في أحدهما ويبقى في الآخر — وهو ما وقع سابقًا حين
        صُحّحت مرساةُ البطاقات وبقي المشتركون على الخطأ.
        """
        username = u.username
        new_balance = float(u.balance or 0)
        if charge_mode in {"paid", "debt"}:
            # paid pays from the prepaid balance, debt goes on credit — both
            # consume the wallet by `amount`. A paid extension must never report
            # «مدفوعة» while leaving the balance untouched (the confirmed bug).
            new_balance -= float(amount)
        saved = self._adapter.upsert_account(replace(u, expire_at=new_exp, balance=new_balance))
        # تجديد (منتهٍ يعود، أو فترةٌ كاملة) ⇒ فترة كوتة جديدة: الاستهلاك يُعدّ
        # من الآن وإضافات الفترة السابقة تُزال (quota_period).
        from . import quota_period
        quota_period.on_time_added(u, new_expire=new_exp, minutes=minutes, reason=action)
        if charge_mode in {"paid", "debt"}:
            _record_subscriber_ledger(
                actor=actor,
                subscriber=saved,
                entry_type="time_extension" if charge_mode == "paid" else "debt",
                direction="debit",  # balance decreases in BOTH modes → debit
                amount=float(amount),
                currency=currency,
                source_type="subscriber_time_extension",
                notes=notes or ledger_note,
                metadata={"minutes": minutes, "charge_mode": charge_mode},
            )
        # before/after لعرض «تاريخ الانتهاء: كان X ← صار Y» في صفحة تغييرات
        # الباقات. المفتاح «expiry» (لا expire_at) كي لا يُسقطه فلتر «*_at» في
        # reports._change_items.
        _old_exp = _fmt_dt_local(u.expire_at) if u.expire_at else "—"
        _new_exp = _fmt_dt_local(new_exp)
        self._audit.record(actor=actor, action=action,
                           target_type="user", target_id=username,
                           payload={"minutes": minutes, "new_expire_at": new_exp.isoformat(),
                                    "charge_mode": charge_mode,
                                    "amount": float(amount) if charge_mode in {"paid", "debt"} else 0},
                           before={"expiry": _old_exp},
                           after={"expiry": _new_exp})
        # تنبيه إدارة — «إضافة/تمديد وقت» لكلّ الأنماط. 🔴 التمديد على الدين كان
        # يُطلق «سلفة وقت» (F07): ليس سلفة (لا قيد سلفة ولا تسوية لها) بل تمديدٌ
        # بدينٍ على الرصيد — النوع والمبلغ في «النوع».
        _kind = {"paid": "مدفوع", "debt": "على الدين"}.get(charge_mode, "مجاني")
        if charge_mode in {"paid", "debt"}:
            _kind += " — " + _fmt_money_ar(amount, currency)
        _notify_alert(saved.tenant_id, "time_added", {
            "username": username,
            "duration": duration_label,
            "new_expiry": _fmt_dt_local(new_exp),
            "kind": _kind,
            "actor": actor,
        }, dedup_key=f"time_added:{username}:{minutes}")
        return saved

    def delete(self, *, actor: str, username: str) -> None:
        # غير موجود/مؤرشف مسبقًا → RadiusNotFound (404) بدل 200 «archived».
        self._adapter.get_account(username)
        self._adapter.delete_account(username)
        self._audit.record(actor=actor, action=AUDIT_ACTION_ARCHIVE,
                           target_type="user", target_id=username,
                           payload={"mode": "soft_delete"})


# DTO fields an edit can never change through update(): identity and
# bookkeeping columns.
_NOT_EDITABLE = frozenset({"id", "tenant_id", "username", "created_at", "updated_at"})


def _changed_fields(before: Subscriber, after: Subscriber) -> set:
    """DTO field names whose value differs between two snapshots."""
    from dataclasses import fields as _dc_fields
    out = set()
    for f in _dc_fields(Subscriber):
        name = f.name
        if name in _NOT_EDITABLE:
            continue
        if getattr(before, name, None) != getattr(after, name, None):
            out.add(name)
    return out


def _supports_partial(adapter) -> bool:
    import inspect
    try:
        return "only_fields" in inspect.signature(adapter.upsert_account).parameters
    except (TypeError, ValueError):
        return False


def _upsert_fields(adapter, sub: Subscriber, fields: set) -> Subscriber:
    if _supports_partial(adapter):
        return adapter.upsert_account(sub, only_fields=fields)
    return adapter.upsert_account(sub)


def _record_manual_balance_change(*, actor: str, before: Subscriber,
                                  saved: Subscriber) -> None:
    """Ledger row for a direct balance edit (owner PATCH / import tools).

    Before this, ``PATCH {"balance": 475}`` changed the wallet with no ledger
    row and no 360 timeline event (re-test R01 H3)."""
    old = round(float(before.balance or 0), 2)
    new = round(float(saved.balance or 0), 2)
    delta = round(new - old, 2)
    if abs(delta) < 0.005:
        return
    _record_subscriber_ledger(
        actor=actor,
        subscriber=saved,
        entry_type="cash_balance",
        direction="credit" if delta > 0 else "debit",
        amount=abs(delta),
        currency=default_currency(),
        source_type="subscriber_manual_balance",
        notes="تعديل رصيد يدوي",
        metadata={"previous_balance": old, "new_balance": new},
    )


def _validate(sub: Subscriber) -> None:
    if sub.user_type not in USER_TYPES:
        raise RadiusValidationError(
            "نوع الحساب غير معروف (المسموح: subscriber أو trial أو card).")
    if not sub.username:
        raise RadiusValidationError("اسم الدخول مطلوب.")


# أقلّ طولٍ لكلمة مرور مشترك **يُدخلها المشغّل** (إنشاء/تغيير/إعادة تعيين من
# الويب أو الـAPI) — نفس حدّ التطبيق ومستخدمي البطاقات (٤). يُستدعى من نقاط
# الدخول لا من upsert: كلمةٌ قديمة لم تتغيّر (حسابات مُرحَّلة بكلمات أقصر) تبقى
# قابلة للتعديل، والترحيل/الاستيراد ينقل الكلمات كما هي. الحساب «بلا كلمة
# مرور» (كلمة فارغة) لا يمرّ هنا.
MIN_SUBSCRIBER_PASSWORD_LENGTH = 4


def validate_new_password(password, *, previous=None) -> None:
    """422 when an operator-entered subscriber password is shorter than 4.

    ``previous`` = the stored password on an edit: an unchanged (legacy) one
    is accepted as is."""
    if previous is not None and str(password or "") == str(previous or ""):
        return
    pw = str(password or "")
    if pw and len(pw.strip()) < MIN_SUBSCRIBER_PASSWORD_LENGTH:
        raise RadiusValidationError(
            f"كلمة المرور يجب أن تكون {MIN_SUBSCRIBER_PASSWORD_LENGTH} أحرف على الأقل.")


def _validate_new_username(username: str) -> None:
    """A NEW login name (create / rename): 3–64 of ``A-Za-z0-9._@-``. The app
    already asks for 3; the web and API took 1–2 (re-test R01 N11). Existing
    shorter legacy names stay editable — only a new name is checked."""
    from .subscriber_validation import MIN_USERNAME_LENGTH
    if not _USERNAME_RE.match(username or ""):
        raise RadiusValidationError(
            "اسم الدخول يسمح بالأحرف اللاتينية والأرقام والرموز . _ - @ فقط "
            "(بدون مسافات، حتى ٦٤ حرفًا).")
    if len(username) < MIN_USERNAME_LENGTH:
        raise RadiusValidationError(
            f"اسم الدخول {MIN_USERNAME_LENGTH} أحرف على الأقل.")


def _plan_minutes(plan) -> int:
    if not plan:
        return 0
    if int(getattr(plan, "duration_minutes", 0) or 0) > 0:
        return int(plan.duration_minutes)
    if int(getattr(plan, "validity_days", 0) or 0) > 0:
        return int(plan.validity_days) * 24 * 60
    value = int(getattr(plan, "duration_value", 0) or 0)
    unit = str(getattr(plan, "duration_unit", "") or "").lower()
    if value <= 0:
        return 0
    if unit in {"mins", "min", "minute", "minutes"}:
        return value
    if unit in {"hrs", "hr", "hour", "hours"}:
        return value * 60
    if unit in {"days", "day"}:
        return value * 24 * 60
    if unit in {"months", "month"}:
        return value * 30 * 24 * 60
    return 0


def _minute_rate(plan) -> float:
    return plan_rate_per_minute(plan)


# عرضٌ بلا مدّة يُسعَّر شهرًا — نفس ``AccountingService.price_basis`` (الدفعات
# والتمديد والسلف) وسياق إجراءات التطبيق، فلا يقول السياق «30 يومًا» ثم
# يرفض تغيير العرض «يتطلّب سعرًا ومدّة».
PLAN_PERIOD_FALLBACK_MINUTES = 43200
_DIRECTION_AR = {"lower": "الأرخص", "higher": "الأغلى", "neutral": "المساوي"}


def plan_period_minutes(plan) -> int:
    """مدّة فترة العرض بالدقائق (المدّة، وإلّا الصلاحية، وإلّا شهر)."""
    if not plan:
        return 0
    return _plan_minutes(plan) or PLAN_PERIOD_FALLBACK_MINUTES


def plan_rate_per_minute(plan) -> float:
    """سعر الدقيقة للعرض (0 للعرض المجّانيّ أو غيابه)."""
    price = float(getattr(plan, "price", 0) or 0) if plan else 0.0
    if price <= 0:
        return 0.0
    return price / plan_period_minutes(plan)


def plan_change_direction(old_plan, new_plan) -> str:
    """«lower» / «higher» / «neutral» — بسعر الدقيقة (مصدرٌ واحد للويب والـ API)."""
    old_rate = plan_rate_per_minute(old_plan)
    new_rate = plan_rate_per_minute(new_plan)
    if old_rate <= 0 and new_rate <= 0:
        return "neutral"
    if old_rate <= 0:
        return "higher"
    if new_rate <= 0:
        return "lower"
    if abs(new_rate - old_rate) <= 1e-9 * max(old_rate, new_rate):
        return "neutral"
    return "lower" if new_rate < old_rate else "higher"


def _remaining_minutes(expire_at, now: datetime) -> int:
    if not expire_at:
        return 0
    return max(0, int((expire_at - now).total_seconds() // 60))


NOTHING_TO_RESET_AR = ("لا توجد لهذا المشترك كوتة يوميّة ولا حدّ وقتٍ يوميّ — لا شيء "
                       "لاستعادته (ولا يُحصَّل أيّ مبلغ).")


class NothingToReset(RadiusValidationError):
    """«استعادة الكوتة اليوميّة» على مشتركٍ بلا سقفٍ يوميّ (422)."""


def daily_reset_applicable(sub) -> bool:
    """هل لـ«استعادة الكوتة اليوميّة» معنى؟ — كوتة يوميّة في العرض (إجماليّة أو
    باتجاه) أو حدّ وقت اتصالٍ يوميّ (العرض أو تجاوز المشترك). محصّن: خطأ
    القراءة ⇒ True (السلوك السابق، لا نمنع استعادةً مشروعة)."""
    try:
        plan = None
        if getattr(sub, "plan_id", None):
            from ..db.repos import plans_repo
            plan = plans_repo.get_plan(int(getattr(sub, "tenant_id", 1) or 1),
                                       int(sub.plan_id), include_deleted=True)
        from . import quota_period
        if any(quota_period.plan_window_caps(plan)["daily"].values()):
            return True
        from .policy_engine import _effective_time_caps
        return _effective_time_caps(sub, plan)[1] > 0
    except Exception:  # noqa: BLE001
        return True


def _record_plan_change_debt(*, actor: str, subscriber: Subscriber,
                             old_plan_id: int | None, new_plan_id: int,
                             amount: float, currency: str,
                             remaining_minutes: int) -> None:
    _record_subscriber_ledger(
        actor=actor,
        subscriber=subscriber,
        entry_type="debt",
        amount=amount,
        direction="debit",
        currency=currency,
        source_type="subscriber_plan_change",
        notes="دين فرق تغيير العرض",
        metadata={
            "old_plan_id": old_plan_id,
            "new_plan_id": new_plan_id,
            "remaining_minutes": remaining_minutes,
        },
    )


def _record_subscriber_ledger(*, actor: str, subscriber: Subscriber,
                              entry_type: str, amount: float,
                              direction: str, currency: str,
                              source_type: str, notes: str,
                              metadata: dict) -> None:
    from ..db.connection import transaction
    from ..db.repos import accounting_repo

    with transaction() as conn:
        accounting_repo.create_ledger_entry(
            conn,
            tenant_id=subscriber.tenant_id,
            entry_type=entry_type,
            amount=amount,
            direction=direction,
            currency=(currency or default_currency()).upper()[:8],
            subscriber_id=subscriber.id,
            username=subscriber.username,
            operator=actor,
            source_type=source_type,
            notes=notes,
            metadata=metadata,
        )


def get_users_service() -> UsersService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return UsersService(get_radius_adapter(), audit=get_audit_service())


# ── إنفاذ السياسة على الجلسات الحيّة بعد الحفظ — محصّن، لا يكسر الحفظ ──────
def _reconcile_policy(tenant_id, *, usernames=None, reason: str = "save") -> None:
    if in_transaction():
        # PoD/CoA على الشبكة بعد COMMIT — لا تحت قفل الكتابة ولا قبل الحفظ.
        after_commit(lambda: _reconcile_policy(tenant_id, usernames=usernames,
                                               reason=reason))
        return
    try:
        from .policy_reconciler import reconcile_active_sessions_against_policy
        reconcile_active_sessions_against_policy(
            int(tenant_id or 1), usernames=usernames, reason=reason)
    except Exception:  # noqa: BLE001
        pass
    # إعادة تطبيق السرعة الفعّالة على الجلسات الحيّة عبر CoA بعد الحفظ. المُصالِح
    # أعلاه يطرد المخالفين فقط ولا يمسّ السرعة؛ هذا يدفع ``effective_rate_limit``
    # (المقسَّم إن كان «تقسيم السرعة على الأجهزة» مفعّلًا، أو الكامل عند تعطيله،
    # أو أيّ سرعة مخصّصة جديدة) لكلّ جلسات المشترك — فيأخذ التبديل مفعوله فورًا
    # دون انتظار حدث اتصال/فصل. خلفيّ ومحصّن (لا يبطّئ الحفظ ولا يكسره).
    if usernames:
        try:
            import threading

            def _bw_reapply():
                try:
                    from .bandwidth_apply import apply_users_effective
                    apply_users_effective(int(tenant_id or 1), list(usernames))
                except Exception:  # noqa: BLE001
                    pass

            threading.Thread(target=_bw_reapply, name="bw-reapply-save",
                             daemon=True).start()
        except Exception:  # noqa: BLE001
            pass


# ── تنبيهات الإدارة (تلجرام) — محصّنة، لا تكسر العملية أبدًا ──────────────
def _notify_alert(tenant_id, key: str, context: dict, *, dedup_key: str = "") -> None:
    if in_transaction():
        # بعد COMMIT فقط: لا تنبيه بإجراءٍ رجعت معاملته.
        after_commit(lambda: _notify_alert(tenant_id, key, context, dedup_key=dedup_key))
        return
    try:
        from .admin_alerts import dispatch
        dispatch(int(tenant_id or 1), key, context, dedup_key=dedup_key)
    except Exception:  # noqa: BLE001
        pass


def _notify_subscriber(tenant_id, event_key: str, *, subscriber=None,
                       context: dict | None = None) -> None:
    """يُسلّم إشعار حدث للمشترك عبر المحرّك الموحّد notifications_engine (المصدر
    الوحيد لإعدادات/تسليم إشعارات المشترك). محصّن — لا يكسر العملية أبدًا."""
    if in_transaction():
        after_commit(lambda: _notify_subscriber(tenant_id, event_key,
                                                subscriber=subscriber, context=context))
        return
    try:
        from .notifications_engine import notify_event
        notify_event(event_key, tenant_id=int(tenant_id or 1),
                     subscriber=subscriber, context=context or {})
    except Exception:  # noqa: BLE001
        pass


def _plan_label(tenant_id, plan_id) -> str:
    if not plan_id:
        return "—"
    try:
        from ..db.repos import plans_repo
        plan = plans_repo.get_plan(int(tenant_id or 1), int(plan_id))
        return (getattr(plan, "name", "") or "—") if plan else "—"
    except Exception:  # noqa: BLE001
        return "—"


# ── صياغة قيم تنبيهات الإدارة (مقروءة، عربية، آمنة دائمًا) ────────────────
def _fmt_minutes_ar(minutes: int) -> str:
    """يحوّل دقائق إلى مدّة عربية مقروءة («يومان (2880 دقيقة)») للتنبيهات."""
    try:
        m = int(minutes or 0)
    except (TypeError, ValueError):
        return "—"
    if m <= 0:
        return "—"
    days, rem = divmod(m, 24 * 60)
    hours, mins = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} يوم")
    if hours:
        parts.append(f"{hours} ساعة")
    if mins:
        parts.append(f"{mins} دقيقة")
    human = " و".join(parts) if parts else f"{m} دقيقة"
    # نُلحق العدد الخام بالدقائق بين قوسين للوضوح (يطابق أسلوب البوابة).
    return f"{human} ({m} دقيقة)" if (days or hours) else human


def _fmt_money_ar(amount, currency: str = "") -> str:
    """مبلغ منسّق برقمين عشريّين + رمز العملة («50.00 ₪»). آمن دائمًا."""
    try:
        cur = (currency or default_currency() or "").strip()
        return f"{float(amount or 0):.2f} {cur}".strip()
    except Exception:  # noqa: BLE001
        return "—"


def _fmt_dt_local(dt) -> str:
    """تاريخ/وقت بالمنطقة المحلّية للمستأجر («2026-07-01 12:00»). آمن دائمًا."""
    try:
        if not dt:
            return "—"
        from ..core.system_config import to_local
        return to_local(dt).strftime("%Y-%m-%d %H:%M")
    except Exception:  # noqa: BLE001
        try:
            return dt.strftime("%Y-%m-%d %H:%M")
        except Exception:  # noqa: BLE001
            return "—"


# حالة المشترك بالعربيّة (للـ diff المقروء في تنبيه «تعديل بيانات مشترك»).
_STATUS_AR: dict[str, str] = {
    "enabled": "مفعّل", "disabled": "معطّل", "expired": "منتهٍ",
    "suspended": "موقوف", "pending": "بانتظار", "banned": "محظور",
    "active": "نشط",
}


def _pw_fingerprint(password) -> str:
    """بصمة غير قابلة للعكس لكلمة المرور — تُخزَّن في اللقطة بدل القيمة الخام.
    تتغيّر متى تغيّرت كلمة المرور (فيَظهر «تغيّرت») لكنّها لا تكشفها أبدًا؛ والعرض
    نفسه يُقنَّع بـ«••••» في التقرير (reports._MASK_KEYS)."""
    pw = (password or "")
    if not pw:
        return ""
    import hashlib
    return "pw:" + hashlib.sha256(pw.encode("utf-8", "ignore")).hexdigest()[:12]


def _sub_snapshot(sub, tid) -> dict:
    """لقطة مقروءة لحقول المشترك ذات المعنى — تُخزَّن في before/after بالسجلّ
    فيَعرض «الحقل: من X إلى Y». القيم مقروءة (اسم العرض + الحالة بالعربيّة).
    كلمة المرور تُخزَّن كبصمة مُقنَّعة (لا خام) عبر `_pw_fingerprint`."""
    if sub is None:
        return {}
    g = lambda a, d=None: getattr(sub, a, d)
    st = (g("status", "") or "").strip()
    return {
        "full_name": (g("full_name", "") or "").strip(),
        "mobile": (g("mobile", "") or "").strip(),
        "status": _STATUS_AR.get(st, st) if st else "",
        "plan": _plan_label(tid, g("plan_id")),
        "static_ip": (g("static_ip", "") or "").strip(),
        "download_speed_kbps": int(g("download_speed_kbps", 0) or 0),
        "upload_speed_kbps": int(g("upload_speed_kbps", 0) or 0),
        "quota_total_mb": int(g("quota_total_mb", 0) or 0),
        "device_limit": g("device_limit"),
        "mac_lock": (g("mac_lock", "") or "").strip(),
        "connection_days": _days_ar(g("working_days", "")),
        "password": _pw_fingerprint(g("password", "")),
    }


# أيّام الأسبوع بالعربية لجدول الاتصال (working_days CSV → عربي مقروء).
_DAYS_AR = {
    "sat": "السبت", "sun": "الأحد", "mon": "الاثنين", "tue": "الثلاثاء",
    "wed": "الأربعاء", "thu": "الخميس", "fri": "الجمعة",
}


def _days_ar(raw) -> str:
    """CSV أيّام الاتصال (sat,sun,…) → «السبت، الأحد» — لسجل تغيير جدول الاتصال.
    فارغ (بلا جدول) يُعيد '' فلا يُنتج فرقًا زائفًا."""
    csv = (raw or "").strip()
    if not csv:
        return ""
    days = [d.strip().lower() for d in csv.split(",") if d.strip()]
    return "، ".join(_DAYS_AR.get(d, d) for d in days)


def _describe_subscriber_changes(old, new) -> str:
    """يبني وصفًا عربيًّا مقروءًا لما تغيّر فعليًّا بين الحالة القديمة والجديدة
    للمشترك (يُغذّي حقل ``changed`` في تنبيه «تعديل بيانات مشترك»).

    لكلّ حقل ذي معنى نُدرج «القديم → الجديد» فقط إن اختلف، ونصِل بـ«، ». لو لم
    يتغيّر شيء جوهريّ نُرجِع «لا تغييرات جوهرية»؛ ولو تعذّر جلب الحالة القديمة
    (مشترك جديد/خطأ بحث) نُرجِع «—» دفاعيًّا — التنبيه لا يكسر التحديث أبدًا.

    ملاحظة: لا نُقارن الصلاحية (expire_at) هنا لأنّ نموذج التعديل لا يحملها في
    الـ DTO (تُدار عبر مسار التجديد/التمديد المنفصل)، فمقارنتها تُنتج ضجيجًا."""
    if old is None:
        return "—"
    try:
        tid = getattr(new, "tenant_id", None) or getattr(old, "tenant_id", 1)

        def _txt(s, attr):
            return (getattr(s, attr, "") or "").strip() or "—"

        def _plan(s):
            return _plan_label(tid, getattr(s, "plan_id", None))

        def _status(s):
            v = (getattr(s, "status", "") or "").strip()
            return _STATUS_AR.get(v, v) if v else "—"

        def _speed(s):
            d = int(getattr(s, "download_speed_kbps", 0) or 0)
            u = int(getattr(s, "upload_speed_kbps", 0) or 0)
            return f"{d}/{u} kbps" if (d or u) else "—"

        def _quota(s):
            mb = int(getattr(s, "combined_quota_mb", 0) or 0)
            return f"{mb} م.ب" if mb else "—"

        fields = [
            ("الاسم", _txt(old, "full_name"), _txt(new, "full_name")),
            ("الجوال", _txt(old, "mobile"), _txt(new, "mobile")),
            ("الباقة", _plan(old), _plan(new)),
            ("الحالة", _status(old), _status(new)),
            ("السرعة", _speed(old), _speed(new)),
            ("الكوتا", _quota(old), _quota(new)),
        ]
        parts = [f"{label}: {o} → {n}" for (label, o, n) in fields if o != n]

        # كلمة المرور — لا تُطبَع أبدًا، يُذكَر فقط أنها تغيّرت.
        op = getattr(old, "password", "") or ""
        npw = getattr(new, "password", "") or ""
        if npw and op != npw:
            parts.append("كلمة المرور: تم التغيير")

        # كلّ تغيير على سطر مستقلّ ببادئة «• » (يَطلبه المالك للقراءة). عند
        # غياب أيّ تغيير نُرجِع جملة سطر-واحد بلا بادئة (وكذلك «—» الدفاعيّة).
        return "\n".join("• " + p for p in parts) if parts else "لا تغييرات جوهرية"
    except Exception:  # noqa: BLE001 — الوصف لا يكسر التحديث/التنبيه أبدًا
        return "—"
