"""CardsService — توليد الكروت + ربطها بـ adapter كحسابات."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from ..core.constants import (
    AUDIT_ACTION_UPDATE,
    AUDIT_ACTION_BATCH_ARCHIVE,
    AUDIT_ACTION_BATCH_GENERATE,
    AUDIT_ACTION_REVOKE,
    USER_TYPE_CARD,
)
from ..core.errors import RadiusValidationError
from ..core.types import Card, CardBatch, Subscriber
from ..db.repos import cards_repo
from ..integration.adapter import RadiusAdapter
from ..stores.cards_store import CardsStore
from .audit import RadiusAuditService
from .audit_events import roadmap_audit_payload


def _minutes_to_value_unit(minutes: int) -> tuple[int, str]:
    """Canonical minutes → (value, unit) for a card batch time window.

    Mirrors routes/cards.py::_minutes_to_value_unit and the unit_input picker
    base (minutes). Prefers the largest whole unit so «480 min» renders as
    «8 hours», «43200 min» as «30 days».
    """
    m = int(minutes or 0)
    if m > 0 and m % 1440 == 0:
        return m // 1440, "days"
    if m > 0 and m % 60 == 0:
        return m // 60, "hours"
    return max(0, m), "minutes"


# حقول بنية الكروت «المخبوزة» في السجلات المولّدة — مقفلة بعد التوليد ولا
# تُعدَّل أبداً على حزمة قائمة (الكروت مطبوعة/مُسلَّمة؛ تغيير العدد أو طول الكود
# أو نمطه/بادئته يُفسد المطابقة مع البطاقات الفعلية). تُجرَّد دائماً من تعديل
# الحزمة في update_batch، ويَرفضها مسار التعديل خادميًّا عند محاولة تغييرها.
STRUCTURAL_LOCKED_FIELDS = (
    "count",                      # عدد الكروت
    "username_length",            # طول اسم المستخدم (عدد الأرقام)
    "password_length",            # طول كلمة المرور/الكود (عدد الأرقام)
    "password_charset",           # مجموعة محارف الكود
    "password_generation_type",   # نمط توليد الكود
    "username_prefix",            # بادئة اسم المستخدم
    "username_suffix",            # لاحقة اسم المستخدم
    "include_batch_number",       # تضمين رقم الحزمة في الاسم
    "random_generation_enabled",  # نمط التوليد العشوائي
    "starts_with_or_ends_with",   # موضع النص المضاف (بادئة/لاحقة)
    "prefix_or_suffix_value",     # النص المضاف للاسم
)


def _batch_window_seconds(batch) -> int:
    """MT113 — نافذة الحزمة بالثواني، بنفس قاعدة مسار المصادقة.

    تُقرأ من كائن الحزمة بدل صفّ SQL، فتُعاد الصياغة على `_card_window_seconds`
    كي لا تتفرّع قاعدتان تختلفان بمرور الوقت — اختلافُهما يعني وقتًا يُعرض
    غير الوقت الذي يُنفَّذ.
    """
    from .policy_engine import _card_window_seconds
    return _card_window_seconds({
        "time_value": getattr(batch, "time_value", 0),
        "time_unit": getattr(batch, "time_unit", ""),
        "validity_after_first_login_days":
            getattr(batch, "validity_after_first_login_days", 0),
    })


class CardsService:
    def __init__(self, adapter: RadiusAdapter, audit: RadiusAuditService) -> None:
        self._adapter = adapter
        self._audit = audit
        self._store = CardsStore.instance()

    def list_batches(self, *, limit: int = 100, offset: int = 0):
        return self._store.list_batches(limit=limit, offset=offset)

    def list_batch_operations(self, **kw) -> list[dict]:
        return self._store.list_batch_operations(**kw)

    def count_batch_operations(self, **kw) -> int:
        return self._store.count_batch_operations(**kw)

    def batch_operations_totals(self, **kw) -> dict:
        return self._store.batch_operations_totals(**kw)

    def list_cards(self, **kw):
        return self._store.list_cards(**kw)

    def count_cards(self, **kw) -> int:
        """R10.4: عدّ الكروت — للـ pagination في cards_list."""
        return self._store.count_cards(**kw)

    def cards_status_counts(self, **kw) -> dict:
        """عدّادات الحالات (الإجمالي/متاح/مستخدم/منتهي/محظور) لشريط KPI
        في صفحة «كل الكروت» — استعلام تجميعي واحد ضمن نطاق البحث/الدفعة."""
        return self._store.cards_status_counts(**kw)

    def stats(self) -> dict:
        return self._store.stats()

    def batch_operational_summary(self, batch_id: int) -> dict | None:
        return cards_repo.batch_operational_summary(self._store_tenant_id(), batch_id)

    def _store_tenant_id(self) -> int:
        from ..core.tenant import DEFAULT_TENANT_ID
        try:
            from flask import g
            return int(getattr(g, "tenant_id", DEFAULT_TENANT_ID))
        except (ImportError, RuntimeError):
            return DEFAULT_TENANT_ID

    def _reconcile_card_policy(self, card_id: int, *, reason: str) -> None:
        """إنفاذ فوريّ بعد حفظٍ يمسّ بطاقة واحدة: إعادة فحص جلساتها الحيّة
        وطرد المخالف (policy_reconciler). محصّن — لا يكسر الحفظ أبدًا."""
        try:
            tenant_id = self._store_tenant_id()
            card = cards_repo.get_card(tenant_id, card_id)
            username = getattr(card, "username", None) or (
                card.get("username") if isinstance(card, dict) else None)
            if not username:
                return
            from .policy_reconciler import reconcile_active_sessions_against_policy
            reconcile_active_sessions_against_policy(
                tenant_id, usernames=[str(username)], reason=reason)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "policy reconcile after %s failed for card=%s",
                reason, card_id, exc_info=True)

    @staticmethod
    def _int(value, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bool(value) -> bool:
        return value in (True, 1, "1", "true", "yes", "on")

    def _plan_name(self, plan_id) -> str:
        """اسم الباقة المقروء لرقمها — لعرض «الباقة: كان X ← صار Y» في سجل
        التغييرات. غير قاتل: يُعيد «#id» أو '' عند تعذّر الجلب."""
        try:
            if not plan_id:
                return ""
            plan = self._adapter.get_profile(int(plan_id))
            return (getattr(plan, "name", "") or "").strip() or f"#{plan_id}"
        except Exception:  # noqa: BLE001 — never break an update over a label
            return f"#{plan_id}" if plan_id else ""

    def generate_batch(
        self,
        *,
        actor: str,
        plan_id: int,
        count: int,
        # ── خيارات RM-H4 (كلها optional عشان توافق calls قديمة) ──
        username_prefix: str = "",
        username_suffix: str = "",
        username_length: int = 8,
        password_length: int = 6,
        password_charset: str = "digits",
        password_generation_type: str = "medium",
        include_batch_number: bool = False,
        starts_with_or_ends_with: str = "",
        prefix_or_suffix_value: str = "",
        random_generation_enabled: bool = True,
        time_value: int = 0,
        time_unit: str = "days",
        device_count: int = 1,
        device_limit_mode: str = "",
        equal_share_download: bool = False,
        equal_share_upload: bool = False,
        duration_mode: str = "time_unit",
        validity_after_first_login_days: int = 0,
        count_by_seconds: bool = False,
        count_from_first_connect: bool = True,
        on_quota_exhaust: str = "stop",
        auto_renew_after_first_use: bool = False,
        transfer_to_student_status_on_connect: bool = False,
        close_user_session_on_disconnect: bool = False,
        allow_entry_by_previous_card_palestine: bool = False,
        switch_to_mac_on_connect: bool = False,
        lock_to_mac_on_close: bool = False,
        phone_only_login: bool = False,
        price_per_card: float = 0.0,
        price_bulk: float = 0.0,
        total_price: float = 0.0,
        total_quota_mb: int = 0,
        package_name: str = "",
        service_name: str = "",
        manager_id: int = 0,
        distributor_id: int | None = None,
        source_type: str = "generated",
        notes: str = "",
        metadata: str = "{}",
        progress_callback=None,
    ) -> tuple[CardBatch, list[Card]]:
        def progress(phase: str, current: int = 0, total: int | None = None, message: str = "") -> None:
            if progress_callback:
                progress_callback({
                    "phase": phase,
                    "current": int(current or 0),
                    "total": int(total if total is not None else count),
                    "message": message,
                })

        progress("validating", 0, count, "فحص الإعدادات ومنع التكرار")
        if count <= 0 or count > 2000:
            raise RadiusValidationError("count بين 1 و 2000")
        if not plan_id:
            raise RadiusValidationError("plan_id مطلوب")

        plan = self._adapter.get_profile(plan_id)
        progress("preparing", 0, count, "تجهيز الحزمة وربط العرض")
        # ── Offer time INHERITANCE: «مدة الوقت» على العرض (plan.duration_minutes)
        # هو رصيد وقت البطاقة الموحَّد. حين لا يُمرِّر النداء نافذة وقت صريحة
        # (لا time_value ولا validity_after_first_login_days)، نَرِث زمن العرض
        # إلى نافذة الحزمة (time_value/time_unit) صراحةً — فيَقرأه فاحص البطاقة
        # وحساب الرصيد (card_accounting.budget_seconds) كرصيد «من أوّل اتصال».
        # قيمة صريحة من الشاشة/العرض التجاريّ تبقى مُقدَّمة (لا نَدُوسها).
        if (time_value <= 0 and validity_after_first_login_days <= 0
                and int(getattr(plan, "duration_minutes", 0) or 0) > 0):
            time_value, time_unit = _minutes_to_value_unit(int(plan.duration_minutes))
            duration_mode = "time_unit"
        # ── #20: two duration modes, driven purely by count_from_first_connect ──
        #
        # RADIUS attribute mapping (materialised by the auth path — see
        # freeradius_translator.build_subscriber_attrs + policy_engine):
        #
        #   • Mode B  (count_from_first_connect=True): WALL-CLOCK countdown that
        #     begins at FIRST LOGIN. We must NOT stamp a generation-time
        #     expire_at — the countdown hasn't started yet. The expiry is
        #     materialised at first login (policy_engine sets first_used_at;
        #     the validity window = first_used_at + validity_after_first_login
        #     [or time_value/time_unit], emitted to MikroTik as the
        #     "Expiration" check item, i.e. a wall-clock cut-off). So expire
        #     stays None at generation.
        #
        #   • Mode A  (count_from_first_connect=False): USAGE-SECONDS budget that
        #     burns only while the user is ONLINE — NOT a wall-clock date. This
        #     maps to an accumulated session-time budget (Session-Timeout /
        #     Acct usage), never to "Expiration". So we also leave expire=None;
        #     a generation-time wall clock would wrongly expire the card on the
        #     calendar even while it sits unused. count_by_seconds expresses the
        #     unit of that budget.
        #
        # Only when NEITHER first-login nor a usage budget is in play do we fall
        # back to the legacy "valid-until date from the plan" wall clock.
        expire = None
        if count_by_seconds and not count_from_first_connect:
            # Mode A — usage-seconds budget that burns only while ONLINE. This
            # must NOT be a wall-clock date (a calendar expiry would kill the
            # card even while it sits unused). Enforced via accumulated session
            # time, so we leave expire_at unset at generation.
            expire = None
        elif time_value and time_unit:
            # MT112 — بطاقةٌ لها مدّة: **لا ساعةَ حائطٍ عند التوليد**.
            #
            # كان هنا `utcnow() + المدّة` بوصفه «سقف أمان» لأنّ الختم عند أوّل
            # دخول لم يكن مبنيًّا. والأثر التجاريّ: بطاقة «٤ ساعات» تُولَّد
            # الساعة ٧ فتموت الساعة ١١ ولو بقيت في الدرج بلا بيع. المالك رأى
            # ١٢٠٠ بطاقةٍ تعدّ تنازليًّا وهي لم تُلمَس («ناقصين ٩ دقايق»).
            #
            # صار الختم في مسار المصادقة عند أوّل دخول
            # (`policy_engine._update_login_timestamps`)، ويلتقط
            # `card_time_reconcile` ما فات المسار المباشر. فهنا يبقى الحقل
            # فارغًا: البطاقة غير المستعملة لا تنقص أبدًا — وهو ما طلبه المالك
            # نصًّا: «ما ينقص الوقت نهائيًّا إلّا لمّا يسجّل دخول».
            #
            # ملاحظة: الشرط لم يعد يشترط `duration_mode == "time_unit"`. كان
            # اشتراطه يُسقط تركيبةً شائعة (المفتاحان مُطفآن) إلى فرع الخطّة
            # أدناه فتُختم ساعة حائطٍ من التوليد بطريقٍ آخر — نفس العطب بابٌ
            # ثانٍ. أيّ مدّةٍ يكتبها المشغّل تُحسب من أوّل دخول، بلا استثناء.
            expire = None
        elif plan.validity_days:
            expire = datetime.utcnow() + timedelta(days=plan.validity_days)

        # تحويل password_generation_type إلى password_charset لو الـ caller ما حدّد charset مخصص
        if password_generation_type and password_charset == "digits":
            pgt_map = {
                "digits": "digits", "weak": "alpha",
                "medium": "mixed", "strong": "strong",
            }
            password_charset = pgt_map.get(password_generation_type, "mixed")

        # ── RM-QA: H4 fix — apply starts_with_or_ends_with + prefix_or_suffix_value ──
        # هذه الحقول الجديدة في H4 كانت تُحفظ في DB لكن لم تُطبَّق فعلًا على usernames.
        # نطويها فوق username_prefix/username_suffix legacy قبل تمريرها للمولّد.
        if prefix_or_suffix_value:
            if starts_with_or_ends_with == "prefix":
                username_prefix = (prefix_or_suffix_value or "") + (username_prefix or "")
            elif starts_with_or_ends_with == "suffix":
                username_suffix = (username_suffix or "") + (prefix_or_suffix_value or "")

        batch = self._store.create_batch(CardBatch(
            id=None, batch_code="", plan_id=plan_id, count=count,
            package_name=package_name,
            username_prefix=username_prefix, username_suffix=username_suffix,
            username_length=username_length,
            include_batch_number=include_batch_number,
            password_length=password_length, password_charset=password_charset,
            expire_at=expire,
            validity_after_first_login_days=validity_after_first_login_days,
            count_by_seconds=count_by_seconds, count_from_first_connect=count_from_first_connect,
            on_quota_exhaust=on_quota_exhaust,
            switch_to_mac_on_connect=switch_to_mac_on_connect,
            lock_to_mac_on_close=lock_to_mac_on_close, phone_only_login=phone_only_login,
            service_name=service_name, notes=notes, manager_id=manager_id, created_by=actor,
            price_per_card=price_per_card, price_bulk=price_bulk, total_quota_mb=total_quota_mb,
            # RM-H4
            password_generation_type=password_generation_type,
            random_generation_enabled=random_generation_enabled,
            starts_with_or_ends_with=starts_with_or_ends_with,
            prefix_or_suffix_value=prefix_or_suffix_value,
            time_value=time_value, time_unit=time_unit,
            device_count=device_count, device_limit_mode=device_limit_mode,
            duration_mode=duration_mode,
            auto_renew_after_first_use=auto_renew_after_first_use,
            transfer_to_student_status_on_connect=transfer_to_student_status_on_connect,
            close_user_session_on_disconnect=close_user_session_on_disconnect,
            allow_entry_by_previous_card_palestine=allow_entry_by_previous_card_palestine,
            total_price=total_price, metadata=metadata,
            source_type=source_type or "generated",
            distributor_id=distributor_id,
        ))
        progress("batch", 0, count, f"تم إنشاء الحزمة {batch.batch_code}")
        cards = self._store.generate_cards_for_batch(
            batch_id=batch.id, plan_id=plan_id, count_to_make=count,
            username_prefix=username_prefix, username_suffix=username_suffix,
            username_length=username_length,
            password_length=password_length, password_charset=password_charset,
            expire_at=expire,
            progress_callback=lambda made, total: progress("generating", made, total, f"تم توليد {made} من {total} بطاقة"),
        )
        # سجّل كل بطاقة كحساب RADIUS (subscriber من نوع card)
        #
        # MT82 — 🔴 حادثة إنتاج (169.58.71.165، 2026-07-28): هذه الحلقة كانت
        # تستدعي `upsert_account` مباشرةً، فما إن صار راوترٌ حيًّا يكتب المحاسبة
        # في نفس قاعدة SQLite حتى رمى `database is locked` هنا ⇒ **سقط إنشاء
        # الحزمة كلّه** والمشغّل يرى «0 / 120». الكروت كانت قد وُلدت فعلًا،
        # فالعطب في الخطوة الأخيرة وحدها. عالجتُ نفس الصنف في مسار الاستيراد
        # (MT70) وتركتُ هذا المسار — والصنف لا يُعالَج بالتجزئة.
        # الآن كلاهما يمرّ بـ`_sync_cards_to_radius`: إعادةٌ عند القفل، ولا
        # استثناء يُسقط توليدًا مُثبَّتًا، والفشل الجزئيّ يُبلَّغ لا يُبتلع.
        progress("syncing", 0, len(cards), "تجهيز حسابات RADIUS")

        def _sync_progress(done: int, total: int) -> None:
            if done == total or done % 25 == 0:
                progress("syncing", done, total,
                         f"تم تجهيز {done} من {total} حساب")

        synced, sync_failed = self._sync_cards_to_radius(
            cards, plan_id=plan_id, batch_id=batch.id, actor=actor,
            equal_share_download=bool(equal_share_download),
            equal_share_upload=bool(equal_share_upload),
            progress=_sync_progress)
        if sync_failed:
            progress("syncing", synced, len(cards),
                     f"⚠️ {sync_failed} بطاقة بلا حساب مصادقة — أعد المزامنة "
                     "من صفحة الحزمة قبل بيعها")
        self._audit.record(
            actor=actor, action=AUDIT_ACTION_BATCH_GENERATE,
            target_type="card_batch", target_id=str(batch.id),
            payload={"plan_id": plan_id, "count": count, "batch_code": batch.batch_code},
        )
        progress("done", len(cards), len(cards), "اكتمل إنشاء الحزمة")
        return self._store.get_batch(batch.id), cards

    def analyze_import(self, cards: list[dict[str, str]]) -> dict:
        """فحص جاف (read-only) لصفوف الاستيراد: يُصنّفها دون أيّ كتابة.

        يُرجِع تقريراً مفصّلاً: الإجمالي، الصالح للاستيراد (فريد وغير موجود)،
        المكرر داخل الملف، الموجود مسبقاً في النظام، وغير الصالح مع سبب واضح
        لكل مجموعة وعيّنات. تُستخدَم من معاينة «تحليل الملف» ومن الاستيراد
        الفعليّ معاً (مصدر حقيقة واحد) فلا تُنشأ حزمة فارغة/وهمية."""
        seen: set[str] = set()
        valid: list[dict[str, str]] = []
        in_file: list[str] = []
        empty = 0
        nonempty = [(c.get("username") or "").strip() for c in cards]
        existing = cards_repo.existing_card_usernames(self._store_tenant_id(), nonempty)
        in_system: list[str] = []
        for c in cards:
            u = (c.get("username") or "").strip()
            if not u:
                empty += 1
                continue
            if u in seen:
                in_file.append(u)
                continue
            seen.add(u)
            if u in existing:
                in_system.append(u)
                continue
            valid.append({"username": u, "password": (c.get("password") or "").strip()})
        invalid: list[dict] = []
        if empty:
            invalid.append({
                "reason": "empty_username",
                "label": "اسم المستخدم فارغ (حقل مفقود)",
                "count": empty,
                "samples": [],
            })
        return {
            "total": len(cards),
            "valid_rows": valid,
            "valid_count": len(valid),
            "duplicate_in_file": {"count": len(in_file), "samples": in_file[:10]},
            "duplicate_in_system": {"count": len(in_system), "samples": in_system[:10]},
            "invalid": invalid,
        }

    def import_batch(
        self,
        *,
        actor: str,
        plan_id: int,
        cards: list[dict[str, str]],
        source_type: str = "imported",
        package_name: str = "",
        service_name: str = "",
        notes: str = "",
        price_per_card: float = 0.0,
        price_bulk: float = 0.0,
        total_price: float = 0.0,
        sync_to_radius: bool = False,
    ) -> dict:
        """Import explicit card credentials as an operational card batch.

        `source_type=external` is a bookkeeping-only file and never syncs to
        FreeRADIUS/MikroTik. `source_type=imported` may sync only when the caller
        asks for it explicitly.

        يُستورَد فقط الصفّ الصالح (الفريد وغير الموجود مسبقاً). إن لم يبقَ صفٌّ
        صالح لا تُنشأ حزمة إطلاقاً (لا حزمة وهمية بعدد 200 و0 صالح). «السعر
        الإجمالي» محسوبٌ خادميًّا = عدد الصالح × سعر البطاقة (لا يُكتَب يدويًّا).
        """
        source = (source_type or "imported").strip().lower()
        if source not in {"imported", "external"}:
            raise RadiusValidationError("source_type must be imported or external")
        if not cards:
            raise RadiusValidationError("cards list is required")
        if len(cards) > 5000:
            raise RadiusValidationError("import supports up to 5000 cards per batch")
        if not plan_id:
            raise RadiusValidationError("plan_id مطلوب")
        plan = self._adapter.get_profile(plan_id)

        # فحص جاف أوّلاً — نستورد الصالح فقط، ولا نُنشئ حزمة إن كان 0 صالح.
        report = self.analyze_import(cards)
        valid_rows = report["valid_rows"]
        if not valid_rows:
            raise RadiusValidationError(
                "لا توجد بطاقات صالحة للاستيراد — كلّها مكرّرة أو غير صالحة، فلم تُنشأ أيّ حزمة."
            )
        valid_count = len(valid_rows)
        computed_total = round(valid_count * float(price_per_card or 0), 2)

        should_sync = bool(sync_to_radius) and source != "external"
        batch = self._store.create_batch(CardBatch(
            id=None,
            batch_code="",
            plan_id=plan_id,
            count=valid_count,
            package_name=package_name or ("ملف خارجي" if source == "external" else "ملف مستورد"),
            service_name=service_name,
            notes=notes,
            created_by=actor,
            price_per_card=price_per_card,
            price_bulk=price_bulk,
            total_price=computed_total,
            source_type=source,
            original_count=valid_count,
            settlement_count=valid_count,
            metadata='{"imported":true}',
        ))
        inserted_cards, skipped = cards_repo.import_cards(
            tenant_id=self._store_tenant_id(),
            batch_id=int(batch.id),
            plan_id=plan_id,
            rows=valid_rows,
            expire_at=None,
        )
        radius_synced, radius_sync_failed = 0, 0
        if should_sync:
            radius_synced, radius_sync_failed = self._sync_imported_cards(
                inserted_cards, plan_id=plan_id, batch_id=batch.id, actor=actor)

        # «المتخطّى» = ما رفضه الفحص الجافّ (مكرر/غير صالح) + أيّ تعارض متبقٍّ.
        analysis_skipped = (report["total"] - valid_count)
        skipped_total = analysis_skipped + len(skipped)
        self._audit.record(
            actor=actor,
            action="card_batch.import",
            target_type="card_batch",
            target_id=str(batch.id),
            payload={
                "plan_id": plan_id,
                "plan_name": getattr(plan, "name", ""),
                "source_type": source,
                "requested": report["total"],
                "valid": valid_count,
                "inserted": len(inserted_cards),
                "skipped": skipped_total,
                "radius_synced": radius_synced,
            },
        )
        return {
            "batch": self._store.get_batch(int(batch.id)),
            "cards": inserted_cards,
            "skipped": skipped,
            "report": report,
            "inserted_count": len(inserted_cards),
            "skipped_count": skipped_total,
            "radius_synced_count": radius_synced,
            "radius_sync_failed_count": radius_sync_failed,
            "radius_sync_enabled": should_sync,
        }

    # عدد محاولات إعادة المزامنة عند «database is locked» ومهلها (ثوانٍ).
    _SYNC_RETRIES = 5
    _SYNC_BACKOFF = (0.2, 0.5, 1.0, 2.0, 3.0)

    def _sync_cards_to_radius(self, cards, *, plan_id, batch_id, actor,
                              equal_share_download=False,
                              equal_share_upload=False, progress=None):
        """MT82 — المُزامِن المشترك للتوليد والاستيراد معًا.

        الصنف واحد (قفل القاعدة يُسقط الخطوة الأخيرة بعد تثبيت الكروت)، وقد
        عالجتُه في الاستيراد وحده (MT70) فبقي التوليد ينهار: «0 / 120». مكانٌ
        واحد الآن، فلا يُصلَح أحدهما ويُنسى الآخر.
        """
        import logging
        import time

        log = logging.getLogger(__name__)
        done = failed = 0
        total = len(cards)
        for idx, card in enumerate(cards, start=1):
            acc = Subscriber(
                id=None, username=card.username, password=card.password,
                user_type=USER_TYPE_CARD, plan_id=plan_id,
                expire_at=card.expire_at, card_batch_id=batch_id,
                created_by=actor,
                equal_share_download=bool(equal_share_download),
                equal_share_upload=bool(equal_share_upload),
            )
            for attempt in range(self._SYNC_RETRIES):
                try:
                    self._adapter.upsert_account(acc)
                    done += 1
                    break
                except Exception as exc:  # noqa: BLE001 — لا يُسقط توليدًا مُثبَّتًا
                    if ("locked" in str(exc).lower()
                            and attempt < self._SYNC_RETRIES - 1):
                        time.sleep(self._SYNC_BACKOFF[attempt])
                        continue
                    failed += 1
                    log.warning("card sync failed for %r (%s)", card.username, exc)
                    break
            if progress:
                progress(idx, total)
        if failed:
            log.error("card sync: %d/%d بطاقة بلا حساب مصادقة (batch=%s)",
                      failed, total, batch_id)
        return done, failed

    def _sync_imported_cards(self, cards, *, plan_id, batch_id, actor):
        """MT70 — يُنشئ حساب مصادقةٍ لكل كرتٍ مستورَد بلا أن يُسقط الاستيراد.

        كانت الحلقة تستدعي ``upsert_account`` مباشرةً؛ فأيّ استثناء — وأشهره
        ``sqlite3.OperationalError: database is locked`` على الحزم الكبيرة —
        يَصعد إلى المسار **بعد** أن ثُبِّتت الحزمة وكروتها. النتيجة على
        الإنتاج: صفحة 500 بينما البيانات محفوظة، فيُعيد المشغّل الاستيراد،
        و**تبقى كروتٌ بلا حساب مصادقة** (٪٢١ من ٧٥٥٥ كرتًا في حادثة
        2026-07-27). الاستيراد عمليّةٌ مُثبَّتة سلفًا، فالمزامنة الجزئيّة
        خبرٌ يُبلَّغ لا سببٌ للانهيار.

        يُعيد ``(نجح، فشل)`` — والمسار يُظهر الفشل للمشغّل صراحةً.
        """
        import logging
        import time

        log = logging.getLogger(__name__)
        done = failed = 0
        for card in cards:
            acc = Subscriber(
                id=None,
                username=card.username,
                password=card.password,
                user_type=USER_TYPE_CARD,
                plan_id=plan_id,
                expire_at=card.expire_at,
                card_batch_id=batch_id,
                created_by=actor,
            )
            for attempt in range(self._SYNC_RETRIES):
                try:
                    self._adapter.upsert_account(acc)
                    done += 1
                    break
                except Exception as exc:  # noqa: BLE001 — لا يُسقط استيرادًا مُثبَّتًا
                    locked = "locked" in str(exc).lower()
                    if locked and attempt < self._SYNC_RETRIES - 1:
                        time.sleep(self._SYNC_BACKOFF[attempt])
                        continue
                    failed += 1
                    log.warning("card import: RADIUS sync failed for %r (%s)",
                                card.username, exc)
                    break
        if failed:
            log.error("card import: %d/%d بطاقة بلا حساب مصادقة (batch=%s)",
                      failed, done + failed, batch_id)
        return done, failed

    # ─── Print-Only Cards ─────────────────────────────────────────
    #
    # These batches exist purely as a printable label source. The
    # cards never reach FreeRADIUS, never auth, never grant network
    # access. The flag print_only=1 is the single source of truth
    # and lives on both the batch and the individual cards as
    # defence-in-depth.

    def import_print_only_batch(
        self,
        *,
        actor: str,
        package_name: str,
        cards: list[dict[str, str]],
        price_per_card: float = 0.0,
        price_bulk: float = 0.0,
        notes: str = "",
        plan_id: int | None = None,
    ) -> dict:
        """Import explicit card credentials as a print-only batch.

        Same parsing pipeline as ``import_batch`` but:
          * Forces ``source_type='external'`` so no sync ever happens.
          * Marks the resulting batch + cards with print_only=1.
          * Picks the tenant's first plan as a metadata anchor when
            no plan is supplied — the plan is irrelevant because the
            cards never auth, but the schema requires one.
        """
        if not cards:
            raise RadiusValidationError("لا توجد كروت للاستيراد.")
        if len(cards) > 5000:
            raise RadiusValidationError("الحد الأقصى 5000 بطاقة في الدفعة الواحدة.")

        effective_plan_id = plan_id or self._first_plan_id_for_tenant()
        if not effective_plan_id:
            raise RadiusValidationError(
                "لا يوجد plan في النظام — أنشئ باقة واحدة على الأقل قبل استيراد بطاقات الطباعة."
            )

        result = self.import_batch(
            actor=actor,
            plan_id=effective_plan_id,
            cards=cards,
            source_type="external",
            package_name=package_name or "بطاقات طباعة",
            notes=notes,
            price_per_card=price_per_card,
            sync_to_radius=False,
        )

        batch = result["batch"]
        self._mark_batch_print_only(int(batch.id), price_bulk=price_bulk)
        # Re-fetch so the caller sees the updated row.
        result["batch"] = self._store.get_batch(int(batch.id))
        return result

    def _first_plan_id_for_tenant(self) -> int | None:
        """Return the tenant's first plan id, or None if none exist.

        Uses the access_plans table directly (the underlying name in
        002_radius_core.sql) and only takes non-deleted rows.
        """
        from ..db.connection import db
        row = db().execute(
            "SELECT id FROM access_plans WHERE tenant_id = ? "
            "AND COALESCE(deleted_at, '') = '' "
            "ORDER BY id LIMIT 1",
            (self._store_tenant_id(),),
        ).fetchone()
        return int(row["id"]) if row else None

    def _mark_batch_print_only(self, batch_id: int, *, price_bulk: float = 0.0) -> None:
        """Flip print_only=1 on the batch row + every card it owns,
        and stash the wholesale price on the batch."""
        from ..db.connection import db
        conn = db()
        tenant_id = self._store_tenant_id()
        conn.execute(
            "UPDATE card_batches SET print_only = 1, price_bulk = ? "
            "WHERE id = ? AND tenant_id = ?",
            (float(price_bulk or 0.0), batch_id, tenant_id),
        )
        conn.execute(
            "UPDATE cards SET print_only = 1 "
            "WHERE batch_id = ? AND tenant_id = ?",
            (batch_id, tenant_id),
        )
        conn.commit()

    def list_print_only_batches(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        """Return print-only batches for the current tenant, formatted
        the same way the existing list_batch_operations does so the
        template can use the same chip + table macros."""
        from ..db.connection import db
        rows = db().execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM cards c
                      WHERE c.batch_id = b.id AND c.tenant_id = b.tenant_id) AS card_count
            FROM card_batches b
            WHERE b.tenant_id = ?
              AND b.print_only = 1
              AND COALESCE(b.deleted_at, '') = ''
            ORDER BY b.id DESC
            LIMIT ? OFFSET ?
            """,
            (self._store_tenant_id(), int(limit), int(offset)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_print_only_batches(self) -> int:
        from ..db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS c FROM card_batches "
            "WHERE tenant_id = ? AND print_only = 1 "
            "AND COALESCE(deleted_at, '') = ''",
            (self._store_tenant_id(),),
        ).fetchone()
        return int(row["c"] or 0)

    def list_print_only_cards(
        self,
        batch_id: int,
        *,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[dict]:
        """Return raw rows for the print modal. Includes the password
        column because the print template needs to render it onto
        labels — this is the whole point of the section."""
        from ..db.connection import db
        rows = db().execute(
            """
            SELECT id, username, password, batch_id, created_at
            FROM cards
            WHERE tenant_id = ? AND batch_id = ? AND print_only = 1
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (self._store_tenant_id(), int(batch_id), int(limit), int(offset)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_print_only_cards(self, batch_id: int) -> int:
        """Total count of cards in a print-only batch — used for
        pagination on the batch detail page."""
        from ..db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS c FROM cards "
            "WHERE tenant_id = ? AND batch_id = ? AND print_only = 1",
            (self._store_tenant_id(), int(batch_id)),
        ).fetchone()
        return int(row["c"] or 0)

    def delete_print_only_batch(self, *, actor: str, batch_id: int) -> bool:
        """Soft-delete a print-only batch + every card under it.

        Sets deleted_at on the batch row and on every card. The
        batch must already be print_only=1; we never accept a
        non-print-only batch through this entrypoint (defence in
        depth — a normal batch should never be deleted via this
        section's UI).
        """
        from ..db.connection import db
        tenant_id = self._store_tenant_id()
        conn = db()
        # Verify the batch is print-only before touching it.
        row = conn.execute(
            "SELECT id FROM card_batches "
            "WHERE id = ? AND tenant_id = ? AND print_only = 1 "
            "AND COALESCE(deleted_at, '') = ''",
            (int(batch_id), tenant_id),
        ).fetchone()
        if not row:
            return False
        now = datetime.utcnow().isoformat(timespec='seconds')
        conn.execute(
            "UPDATE card_batches SET deleted_at = ?, deleted_by = ?, "
            "delete_reason = 'operator deleted from print section' "
            "WHERE id = ? AND tenant_id = ?",
            (now, actor or 'anonymous', int(batch_id), tenant_id),
        )
        conn.execute(
            "UPDATE cards SET deleted_at = ? "
            "WHERE batch_id = ? AND tenant_id = ?",
            (now, int(batch_id), tenant_id),
        )
        conn.commit()
        self._audit.record(
            actor=actor or 'anonymous',
            action=AUDIT_ACTION_BATCH_ARCHIVE,
            target_type="card_batch",
            target_id=str(batch_id),
            payload={"print_only": True, "soft_delete": True},
        )
        return True

    # ─── Recharge Cards (بطاقات الشحن المسبق) ──────────────────
    #
    # Prepaid wallet top-up vouchers. Each card carries its own
    # monetary value (cards.wallet_value) so a single batch can
    # mix denominations. The customer portal /portal/card/redeem
    # path consumes these directly via redeem_card_to_wallet.

    def generate_recharge_batch(
        self,
        *,
        actor: str,
        package_name: str,
        denominations: list[dict],
        username_length: int = 10,
        password_length: int = 5,
        notes: str = "",
    ) -> dict:
        """Generate a multi-denomination recharge batch.

        denominations: a list of {"value": float, "count": int}
        entries — e.g. [{"value":5,"count":100},{"value":10,"count":50}].
        Each card row gets its own wallet_value from the matching
        entry; the batch row carries the denominations JSON in its
        metadata so the operator UI can render the breakdown.
        """
        import json
        import secrets
        import string
        from datetime import datetime as _dt

        # Validate inputs.
        if not package_name:
            raise RadiusValidationError("اسم الحزمة مطلوب.")
        if not denominations:
            raise RadiusValidationError("لا توجد فئات للتوليد.")
        cleaned: list[dict] = []
        for d in denominations:
            try:
                value = float(d.get("value") or 0)
                count = int(d.get("count") or 0)
            except (TypeError, ValueError) as exc:
                raise RadiusValidationError(
                    f"قيمة أو عدد غير صالح: {d}"
                ) from exc
            if value <= 0 or count <= 0:
                continue
            cleaned.append({"value": value, "count": count})
        if not cleaned:
            raise RadiusValidationError(
                "كل الفئات قيمتها صفر — أدخل فئة واحدة على الأقل."
            )
        total_cards = sum(int(d["count"]) for d in cleaned)
        if total_cards > 5000:
            raise RadiusValidationError("الحد الأقصى 5000 بطاقة في الدفعة.")
        total_value = sum(d["value"] * d["count"] for d in cleaned)

        tenant_id = self._store_tenant_id()
        from ..db.connection import db
        conn = db()

        # ── Create the batch row. We reuse the existing CardBatch
        #    plumbing — source_type=external so no FreeRADIUS sync —
        #    then flip recharge_only after the fact.
        plan_id = self._first_plan_id_for_tenant()
        if not plan_id:
            raise RadiusValidationError(
                "لا يوجد plan في النظام — أنشئ باقة واحدة قبل توليد بطاقات الشحن."
            )

        # ── Generate unique card codes. Digits only so the operator
        #    can write them with a number pad and the customer can
        #    enter them on any keyboard.
        digits = "0123456789"
        def _new_code(length: int) -> str:
            return "".join(secrets.choice(digits) for _ in range(length))

        # ── Create the batch first so we have a real id to attach
        #    cards to. Use the existing store to get the auto code.
        from ..core.types import CardBatch
        batch = self._store.create_batch(CardBatch(
            id=None,
            batch_code="",
            plan_id=plan_id,
            count=total_cards,
            package_name=package_name,
            service_name="",
            notes=notes,
            created_by=actor,
            price_per_card=0.0,
            total_price=total_value,
            source_type="external",
            original_count=total_cards,
            settlement_count=total_cards,
            metadata=json.dumps({
                "recharge_only": True,
                "denominations": cleaned,
            }),
        ))
        batch_id = int(batch.id)

        # ── Mark the batch as recharge_only + stamp total_value.
        conn.execute(
            "UPDATE card_batches "
            "SET recharge_only=1, price_bulk=? "
            "WHERE id=? AND tenant_id=?",
            (float(total_value), batch_id, tenant_id),
        )

        # ── Insert the cards row-by-row. Codes are unique inside
        #    the batch via the existing UNIQUE(tenant_id,username)
        #    index. Retry on collision (rare with 12-char codes
        #    over a 32-symbol alphabet — collisions << 1 in 10^17).
        now = _dt.utcnow().isoformat(timespec="seconds")
        inserted = 0
        for slot in cleaned:
            value = float(slot["value"])
            for _ in range(int(slot["count"])):
                tries = 0
                while True:
                    tries += 1
                    code = _new_code(username_length)
                    pin  = _new_code(password_length)
                    try:
                        conn.execute(
                            """
                            INSERT INTO cards
                              (tenant_id, batch_id, plan_id, username, password,
                               wallet_value, recharge_only,
                               expire_at, used, revoked,
                               created_at)
                            VALUES (?, ?, ?, ?, ?, ?, 1, NULL, 0, 0, ?)
                            """,
                            (tenant_id, batch_id, plan_id, code, pin,
                             value, now),
                        )
                        inserted += 1
                        break
                    except Exception:  # noqa: BLE001
                        if tries > 6:
                            raise
                        continue
        conn.commit()

        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_BATCH_GENERATE,
            target_type="card_batch",
            target_id=str(batch_id),
            payload={
                "recharge_only": True,
                "denominations": cleaned,
                "total_cards": total_cards,
                "total_value": total_value,
            },
        )

        return {
            "batch": self._store.get_batch(batch_id),
            "inserted_count": inserted,
            "total_value": total_value,
        }

    def list_recharge_batches(self, *, limit: int = 100, offset: int = 0) -> list[dict]:
        """Return recharge-only batches for the current tenant."""
        from ..db.connection import db
        rows = db().execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM cards c
                      WHERE c.batch_id=b.id AND c.tenant_id=b.tenant_id) AS card_count,
                   (SELECT COUNT(*) FROM cards c
                      WHERE c.batch_id=b.id AND c.tenant_id=b.tenant_id
                        AND c.used=1) AS used_count
            FROM card_batches b
            WHERE b.tenant_id=?
              AND b.recharge_only=1
              AND COALESCE(b.deleted_at,'')=''
            ORDER BY b.id DESC
            LIMIT ? OFFSET ?
            """,
            (self._store_tenant_id(), int(limit), int(offset)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_recharge_batches(self) -> int:
        from ..db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS c FROM card_batches "
            "WHERE tenant_id=? AND recharge_only=1 "
            "AND COALESCE(deleted_at,'')=''",
            (self._store_tenant_id(),),
        ).fetchone()
        return int(row["c"] or 0)

    def get_recharge_batch(self, batch_id: int) -> dict | None:
        from ..db.connection import db
        row = db().execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM cards c
                      WHERE c.batch_id=b.id AND c.tenant_id=b.tenant_id) AS card_count,
                   (SELECT COUNT(*) FROM cards c
                      WHERE c.batch_id=b.id AND c.tenant_id=b.tenant_id
                        AND c.used=1) AS used_count,
                   (SELECT COALESCE(SUM(c.wallet_value), 0) FROM cards c
                      WHERE c.batch_id=b.id AND c.tenant_id=b.tenant_id) AS total_value
            FROM card_batches b
            WHERE b.id=? AND b.tenant_id=? AND b.recharge_only=1
            """,
            (int(batch_id), self._store_tenant_id()),
        ).fetchone()
        return dict(row) if row else None

    def list_recharge_cards(
        self,
        batch_id: int,
        *,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[dict]:
        from ..db.connection import db
        rows = db().execute(
            """
            SELECT id, username, password, wallet_value,
                   used, first_used_at, created_at, batch_id
            FROM cards
            WHERE tenant_id=? AND batch_id=? AND recharge_only=1
            ORDER BY id
            LIMIT ? OFFSET ?
            """,
            (self._store_tenant_id(), int(batch_id), int(limit), int(offset)),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_recharge_cards(self, batch_id: int) -> int:
        from ..db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS c FROM cards "
            "WHERE tenant_id=? AND batch_id=? AND recharge_only=1",
            (self._store_tenant_id(), int(batch_id)),
        ).fetchone()
        return int(row["c"] or 0)

    def delete_recharge_batch(self, *, actor: str, batch_id: int) -> bool:
        """Soft-delete a recharge batch. Refuses non-recharge batches."""
        from ..db.connection import db
        tenant_id = self._store_tenant_id()
        conn = db()
        row = conn.execute(
            "SELECT id FROM card_batches "
            "WHERE id=? AND tenant_id=? AND recharge_only=1 "
            "AND COALESCE(deleted_at,'')=''",
            (int(batch_id), tenant_id),
        ).fetchone()
        if not row:
            return False
        now = datetime.utcnow().isoformat(timespec='seconds')
        conn.execute(
            "UPDATE card_batches SET deleted_at=?, deleted_by=?, "
            "delete_reason='operator deleted from recharge section' "
            "WHERE id=? AND tenant_id=?",
            (now, actor or 'anonymous', int(batch_id), tenant_id),
        )
        conn.execute(
            "UPDATE cards SET deleted_at=? "
            "WHERE batch_id=? AND tenant_id=?",
            (now, int(batch_id), tenant_id),
        )
        conn.commit()
        self._audit.record(
            actor=actor or 'anonymous',
            action=AUDIT_ACTION_BATCH_ARCHIVE,
            target_type="card_batch",
            target_id=str(batch_id),
            payload={"recharge_only": True, "soft_delete": True},
        )
        return True

    def get_print_only_batch(self, batch_id: int) -> dict | None:
        from ..db.connection import db
        row = db().execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM cards c
                      WHERE c.batch_id = b.id AND c.tenant_id = b.tenant_id) AS card_count
            FROM card_batches b
            WHERE b.id = ? AND b.tenant_id = ? AND b.print_only = 1
            """,
            (int(batch_id), self._store_tenant_id()),
        ).fetchone()
        return dict(row) if row else None

    def last_realign_summary(self) -> dict:
        """آخر مواءمةٍ نفّذها `update_batch` — ليُخبر المسارُ المشغّلَ بعددها."""
        return dict(getattr(self, "_last_realign", None)
                    or {"pending": 0, "started": 0})

    def update_batch(self, *, actor: str, batch_id: int, data: dict) -> CardBatch:
        batch = self._store.get_batch(batch_id)
        if not batch:
            raise RadiusValidationError("دفعة الكروت غير موجودة")

        changes: dict = {}
        text_fields = (
            "package_name",
            "username_prefix",
            "username_suffix",
            "password_charset",
            "starts_with_or_ends_with",
            "prefix_or_suffix_value",
            "time_unit",
            "duration_mode",
            "on_quota_exhaust",
            "service_name",
            "notes",
            "status",
            "password_generation_type",
            "metadata",
            "assigned_to",
        )
        int_fields = (
            "plan_id",
            "count",
            "total_quota_mb",
            "username_length",
            "password_length",
            "validity_after_first_login_days",
            "manager_id",
            "time_value",
            "device_count",
            "distributor_id",
        )
        float_fields = ("price_per_card", "price_bulk", "total_price")
        bool_fields = (
            "include_batch_number",
            "count_by_seconds",
            "count_from_first_connect",
            "switch_to_mac_on_connect",
            "lock_to_mac_on_close",
            "phone_only_login",
            "random_generation_enabled",
            "auto_renew_after_first_use",
            "transfer_to_student_status_on_connect",
            "close_user_session_on_disconnect",
            "allow_entry_by_previous_card_palestine",
        )

        for field in text_fields:
            if field in data:
                changes[field] = str(data.get(field) or "").strip()[:500]
        for field in int_fields:
            if field in data:
                changes[field] = self._int(data.get(field))
        for field in float_fields:
            if field in data:
                changes[field] = self._float(data.get(field))
        for field in bool_fields:
            if field in data:
                changes[field] = int(self._bool(data.get(field)))
        if "expire_at" in data:
            value = str(data.get("expire_at") or "").strip()
            changes["expire_at"] = value or None

        # بنية الكروت مقفلة بعد التوليد: نُجرّدها دائماً فلا تُحفَظ أبداً مهما
        # كان المُدخَل (دفاع مركزيّ لأيّ مستدعٍ). الكروت المولّدة لا تُمَسّ.
        for _locked in STRUCTURAL_LOCKED_FIELDS:
            changes.pop(_locked, None)

        if "plan_id" in changes:
            if changes["plan_id"] <= 0:
                raise RadiusValidationError("الباقة المرتبطة مطلوبة")
            self._adapter.get_profile(changes["plan_id"])
        if "device_count" in changes:
            # 0 = وراثة الافتراض العام للكروت (mig154)؛ 1..50 = حدّ صريح للحزمة.
            changes["device_count"] = max(0, min(changes["device_count"], 50))

        updated = self._store.update_batch(batch_id, changes)
        if not updated:
            raise RadiusValidationError("تعذر تعديل دفعة الكروت")

        # MT113 — تعديل المدّة يجب أن يَسري على البطاقات، وإلّا فهو تعديلُ
        # ورقةٍ لا تعديلُ منتَج: يفتح المشغّل الحزمة ويكتب «٦ ساعات» ويحفظ،
        # فتبقى بطاقاتها الأربع-ساعيّة كما هي — والحزمة **مطبوعة** وموزّعة
        # ولا سبيل لسحبها. (طلب المالك حرفيًّا: «عملت حزمة ٤ ساعات، حبّيت
        # أخلّيها ٦ — والحزمة مطبوعة».)
        realigned = {"pending": 0, "started": 0}
        if any(k in changes for k in
               ("time_value", "time_unit", "validity_after_first_login_days")):
            try:
                realigned = cards_repo.realign_batch_card_windows(
                    self._store_tenant_id(), int(batch_id),
                    window_seconds=_batch_window_seconds(updated),
                )
            except Exception:  # noqa: BLE001 — الحفظ لا يسقط لأجل المواءمة
                import logging
                logging.getLogger(__name__).warning(
                    "realign_batch_card_windows failed for batch=%s",
                    batch_id, exc_info=True)
        self._last_realign = realigned
        # لقطتان مقروءتان before/after → يَظهر «الحقل: كان X ← صار Y» في سجل
        # أحداث المدراء لكلّ حقل من حقول الدفعة تغيّر (اسم الباقة يُحلّ لقيمة مقروءة).
        self._audit.record(
            actor=actor,
            action=AUDIT_ACTION_UPDATE,
            target_type="card_batch",
            target_id=str(batch_id),
            payload={"changed_fields": sorted(changes.keys()),
                     "cards_realigned": realigned},
            before=_batch_snapshot(batch, self._plan_name(batch.plan_id)),
            after=_batch_snapshot(updated, self._plan_name(updated.plan_id)),
        )
        # حفظ الدفعة قد يُضيّق قواعد بطاقاتها (حدّ الأجهزة/الكوتا/الأيام…) —
        # إعادة فحص الجلسات الحيّة لبطاقات الحزمة وطرد المخالف فورًا.
        try:
            from .policy_reconciler import reconcile_active_sessions_against_policy
            reconcile_active_sessions_against_policy(
                self._store_tenant_id(), batch_id=int(batch_id),
                reason="batch_update")
        except Exception:  # noqa: BLE001 — الإنفاذ لا يكسر الحفظ أبدًا
            pass
        return updated

    def revoke_card(self, *, actor: str, card_id: int) -> None:
        self._store.revoke(card_id)
        self._audit.record(actor=actor, action=AUDIT_ACTION_REVOKE,
                           target_type="card", target_id=str(card_id))
        # «أي عملية حفظ يصير إعادة مطابقة»: الإلغاء = البطاقة لم تعد صالحة —
        # اطرد جلستها الحيّة فورًا (لا انتظار لإعادة المصادقة).
        self._reconcile_card_policy(card_id, reason="card_revoke")

    def enable_card(self, *, actor: str, card_id: int) -> dict:
        """Re-enable a previously-disabled card AND restore the time
        snapshot taken at disable. Returns dict with the new expire_at
        and how many seconds were restored (0 if the card was never
        frozen, e.g. disabled before migration 025)."""
        tenant_id = self._store_tenant_id()
        result = cards_repo.thaw_card_time(tenant_id, card_id)
        if result is None:
            raise RadiusValidationError("تعذر تفعيل البطاقة")
        self._audit.record(actor=actor, action="card.enable",
                           target_type="card", target_id=str(card_id),
                           payload={
                               "restored_seconds": result["restored_seconds"],
                               "expire_at_new":    result["expire_at_new"],
                           })
        return result

    def disable_card(self, *, actor: str, card_id: int, reason: str = "") -> dict:
        """Disable a card, FREEZE its remaining time, AND kick every
        currently-active session.

        Three things happen atomically from the operator's POV:

        1. The remaining-seconds snapshot is taken (frozen_remaining_seconds)
           so the real-world clock does not burn the user's quota while
           the card is disabled. Re-enabling restores the same amount
           of time from 'now'.

        2. The card is marked revoked, so the next auth attempt fails
           and FreeRADIUS won't let the device re-join.

        3. CoA-Disconnect is broadcast to every active radacct session
           for this username. Without this, an already-online device
           keeps using the network until its lease/keepalive expires —
           the operator clicked "تعطيل" but the user is still online.

        Returns dict with frozen_remaining_seconds + old expire_at,
        plus `kicked_sessions` (how many CoA-Disconnects were sent).
        """
        tenant_id = self._store_tenant_id()
        result = cards_repo.freeze_card_time(
            tenant_id, card_id, actor=actor, reason=reason,
        )
        if result is None:
            raise RadiusValidationError("تعذر تعطيل البطاقة")

        # Kick any device that's still online. Wrapped in try/except so
        # a transient CoA failure doesn't roll back the freeze — the
        # admin's intent ("disable this card") is the contract; the
        # CoA broadcast is best-effort enforcement.
        kicked = 0
        try:
            card = cards_repo.get_card(tenant_id, card_id)
            # get_card returns a Card dataclass; defensive on shape.
            username = getattr(card, "username", None) or (
                card.get("username") if isinstance(card, dict) else None
            )
            if username:
                self._adapter.disconnect(username)
                kicked = -1  # adapter doesn't return a count; -1 = "best-effort dispatched"
        except Exception:  # noqa: BLE001
            # CoA failure must not prevent the freeze from being recorded.
            import logging
            logging.getLogger(__name__).warning(
                "disable_card: CoA kick failed for card=%s", card_id, exc_info=True,
            )

        result["kicked_sessions"] = kicked
        self._audit.record(actor=actor, action="card.disable",
                           target_type="card", target_id=str(card_id),
                           payload={
                               "reason":                   reason,
                               "frozen_remaining_seconds": result["frozen_remaining_seconds"],
                               "expire_at_old":            result["expire_at_old"],
                               "kicked_sessions":          kicked,
                           })
        return result

    def soft_delete_card(self, *, actor: str, card_id: int, reason: str = "") -> None:
        """Move a card to the recycle bin (deleted_at set). The row stays
        in the DB so /admin/radius/recycle-bin can restore or purge it.
        Replaces the previous hard-delete path as the default 'حذف'
        action — delete_card_permanently still exists for explicit
        purging from the recycle bin."""
        tenant_id = self._store_tenant_id()
        if not cards_repo.soft_delete_card(
            tenant_id, card_id, actor=actor, reason=reason,
        ):
            raise RadiusValidationError("تعذر نقل البطاقة إلى سلة المحذوفات")
        self._audit.record(actor=actor, action="card.soft_delete",
                           target_type="card", target_id=str(card_id),
                           payload={"reason": reason})

    def lock_card_mac(self, *, actor: str, card_id: int, mac: str) -> dict:
        """Lock the card to ONE OR MORE MAC addresses, and immediately
        ENFORCE the lock by disconnecting any active session whose MAC
        is not in the allowed list.

        `mac` may be a single value or a comma/semicolon/newline-
        separated list. All entries are normalised to UPPER + ':'
        separators, de-duplicated, sorted, then re-joined with ','
        for storage. Empty after parsing → ValidationError.

        Returns:
          {
            "macs":   ["AA:BB:..", ...],   # the locked-down list
            "kicked": [session_id, ...],   # sessions we sent CoA-Disconnect to
            "kept":   N,                   # sessions whose MAC matched (untouched)
          }

        Without the kick step, a previously-connected non-matching
        device would happily keep streaming until its lease/keepalive
        timeout — defeating the point of locking.
        """
        raw = (mac or "").replace(";", ",").replace("\n", ",")
        macs = sorted({
            m.strip().upper().replace("-", ":")
            for m in raw.split(",")
            if m.strip()
        })
        if not macs:
            raise RadiusValidationError("MAC مطلوب")
        # Loose validity check — 12 hex chars after stripping separators.
        for m in macs:
            hex_only = m.replace(":", "")
            if len(hex_only) != 12 or any(c not in "0123456789ABCDEF" for c in hex_only):
                raise RadiusValidationError(f"عنوان MAC غير صالح: {m}")
        joined = ",".join(macs)
        tenant_id = self._store_tenant_id()
        if not cards_repo.set_card_locked_mac(
            tenant_id, card_id, joined, actor=actor,
        ):
            raise RadiusValidationError("تعذر تثبيت MAC")

        # ── Enforce ────────────────────────────────────────────────
        # Walk active sessions for this card's username; any session
        # whose callingstationid is NOT in `macs` gets CoA-Disconnected.
        kicked: list[str] = []
        kept = 0
        try:
            card = cards_repo.get_card(tenant_id, card_id)
            username = getattr(card, "username", None) or (
                card.get("username") if isinstance(card, dict) else None
            )
            if username:
                allowed = {m.upper() for m in macs}
                rows = cards_repo.list_card_accounting(tenant_id, username, limit=100)
                offenders: list[str] = []  # acctsessionid values to kick
                for row in rows:
                    if row.get("acctstoptime"):
                        continue  # already ended
                    sess_mac = (row.get("callingstationid") or "").strip().upper()
                    sid = row.get("acctsessionid") or ""
                    if not sid:
                        continue
                    if sess_mac and sess_mac in allowed:
                        kept += 1
                    else:
                        offenders.append(sid)
                if offenders:
                    try:
                        # adapter supports session_ids list (per-session disconnect)
                        self._adapter.disconnect(username, session_ids=offenders)
                        kicked.extend(offenders)
                    except TypeError:
                        # Legacy adapter without session_ids kwarg — broadcast.
                        self._adapter.disconnect(username)
                        kicked.extend(offenders)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "lock_card_mac: enforcement kick failed for card=%s",
                card_id, exc_info=True,
            )

        self._audit.record(actor=actor, action="card.lock_mac",
                           target_type="card", target_id=str(card_id),
                           payload={
                               "macs":          macs,
                               "count":         len(macs),
                               "kicked_count":  len(kicked),
                               "kept_count":    kept,
                           })
        return {"macs": macs, "kicked": kicked, "kept": kept}

    def unlock_card_mac(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.set_card_locked_mac(self._store_tenant_id(), card_id, "", actor=actor):
            raise RadiusValidationError("تعذر إلغاء تثبيت MAC")
        self._audit.record(actor=actor, action="card.unlock_mac",
                           target_type="card", target_id=str(card_id))

    def reset_card_usage(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.reset_card_usage(self._store_tenant_id(), card_id):
            raise RadiusValidationError("تعذر تصفير استخدام البطاقة")
        self._audit.record(actor=actor, action="card.reset_usage",
                           target_type="card", target_id=str(card_id))

    def change_card_password(self, *, actor: str, card_id: int,
                             new_password: str = "",
                             kick: bool = True) -> dict:
        """MT107 — تغيير كلمة مرور بطاقةٍ بعينها، وطرد جلساتها فورًا.

        البطاقات الطويلة (أسبوعيّة/شهريّة) تبقى بيد الزبون أسابيع، فيكفي أن
        يُصوّرها أحدهم لتصير مشاعًا. لم يكن أمام المشغّل إلّا تعطيل البطاقة —
        فيخسر الزبون ما دفع. الآن تُغيَّر الكلمة وتبقى البطاقة ووقتها.

        وتغييرُها بلا طردٍ عبثٌ: المتسلّل متّصلٌ الآن، وجلسته القائمة لا
        تُعاد مصادقتها، فيظلّ يستهلك حتى تنتهي مهلته. لذلك الطرد جزءٌ من
        العمليّة لا خيارٌ تجميليّ (`kick=False` للاختبارات فقط).

        الكلمة تُغيَّر في موضعَين لا واحد:
          1. جدول `cards`   — ما يراه المشغّل ويُطبَع على البطاقة.
          2. حساب RADIUS    — ما يُصادَق به فعلًا (+ دفعٌ للمايكروتيك).
        لو نجح الأوّل وحده لظنّ المشغّل أنّه غيّرها والدخول لا يزال بالقديمة.

        `new_password` فارغة ⇒ تُولَّد بنفس طول ومحارف حزمة البطاقة، فتبقى
        متّسقةً مع أخواتها (ولا تُطبع كلمةٌ بصيغةٍ غريبة عن الحزمة).

        تُعيد: {"password", "username", "kicked", "generated"}
        """
        tenant_id = self._store_tenant_id()
        card = cards_repo.get_card(tenant_id, card_id)
        if not card:
            raise RadiusValidationError("البطاقة غير موجودة")
        username = getattr(card, "username", "") or ""
        if not username:
            raise RadiusValidationError("البطاقة بلا اسم دخول")

        pwd = (new_password or "").strip()
        generated = not pwd
        if generated:
            length, charset = 6, "digits"
            try:
                batch = cards_repo.get_batch(
                    tenant_id, getattr(card, "batch_id", None) or 0,
                )
                if batch:
                    length = int(getattr(batch, "password_length", 0) or 6)
                    charset = getattr(batch, "password_charset", "") or "digits"
            except Exception:  # noqa: BLE001 — حزمةٌ محذوفة لا تمنع التغيير
                pass
            pwd = cards_repo._random_str(max(1, length), charset=charset)
        elif len(pwd) > 64:
            raise RadiusValidationError("كلمة المرور أطول من 64 حرفًا")
        elif any(c.isspace() for c in pwd):
            # مسافةٌ داخل الكلمة لا تُرى عند الطباعة، ثمّ يفشل الدخول بلا سبب ظاهر.
            raise RadiusValidationError("كلمة المرور لا تقبل مسافات")

        if not cards_repo.set_card_password(tenant_id, card_id, pwd):
            raise RadiusValidationError("تعذّر تغيير كلمة مرور البطاقة")

        # ── جانب RADIUS ───────────────────────────────────────────────
        # لو فشل هذا فالجدولان متخالفان — نُعلنه بدل ابتلاعه.
        self._adapter.reset_password(username, pwd)

        # ── الطرد ─────────────────────────────────────────────────────
        kicked = False
        if kick:
            try:
                self._adapter.disconnect(username)
                kicked = True
            except Exception:  # noqa: BLE001 — الكلمة تغيّرت فعلًا؛ لا نتراجع
                import logging
                logging.getLogger(__name__).warning(
                    "change_card_password: kick failed for %r", username,
                    exc_info=True,
                )

        # لا تُسجَّل الكلمة نفسها في التدقيق — السجلّ يُقرأ من الواجهة.
        self._audit.record(actor=actor, action="card.change_password",
                           target_type="card", target_id=str(card_id),
                           payload={
                               "username":  username,
                               "generated": generated,
                               "kicked":    kicked,
                               "length":    len(pwd),
                           })
        return {"password": pwd, "username": username,
                "kicked": kicked, "generated": generated}

    def set_card_speed(self, *, actor: str, card_id: int,
                         down_kbps: int, up_kbps: int,
                         username: str = "") -> dict:
        """Persist a per-card Mikrotik-Rate-Limit override, write it
        through to FreeRADIUS (radreply), and best-effort CoA-push it
        to any live session for `username`.

        Pass down_kbps=0 AND up_kbps=0 to CLEAR the override (falls back
        to the plan default). Mixing 0 + nonzero is rejected because we
        always emit a "up/down k" pair to MT.

        Returns a dict with keys: {down, up, was_override, fr_synced,
        coa_result}. Raises RadiusValidationError on bad input or missing
        card.
        """
        down = int(down_kbps or 0)
        up   = int(up_kbps   or 0)
        if down < 0 or up < 0:
            raise RadiusValidationError("لا تُقبل قيم سالبة للسرعة")
        clearing = (down == 0 and up == 0)
        if not clearing and (down == 0 or up == 0):
            raise RadiusValidationError(
                "يجب تحديد قيمتي التنزيل والرفع معًا (أو تصفير الاثنين لإلغاء التخصيص)."
            )
        tenant_id = self._store_tenant_id()

        # ── 1) DB persist ──
        result = cards_repo.set_card_speed_override(tenant_id, card_id, down, up)
        if result is None:
            raise RadiusValidationError("لم يتم العثور على البطاقة")
        username = (username or result.get("username") or "").strip()

        # ── 2) FreeRADIUS native path: re-sync radreply for this card's
        #    subscriber-mirror so the new override appears in DB rows that
        #    rlm_sql reads at next Access-Request. Best-effort — if no
        #    subscriber mirror exists, the HTTP /api/internal/auth path
        #    (policy_engine._card_to_subscriber) still picks the override
        #    from the cards row directly.
        fr_synced = False
        try:
            from ..db.repos import subscribers_repo, plans_repo
            from . import freeradius_translator
            sub = subscribers_repo.get_subscriber(tenant_id, username) if username else None
            if sub is not None:
                # Stamp the override onto the Subscriber DTO so sync_subscriber
                # writes the same Mikrotik-Rate-Limit row we expect from the
                # policy_engine path. Both paths end up with the same value.
                from dataclasses import replace
                stamped = replace(
                    sub,
                    bandwidth_control_enabled=(not clearing),
                    download_speed_kbps=(down if not clearing else 0),
                    upload_speed_kbps=(up   if not clearing else 0),
                )
                plan = plans_repo.get_plan(tenant_id, sub.plan_id) if sub.plan_id else None
                freeradius_translator.sync_subscriber(stamped, plan)
                fr_synced = True
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "freeradius_translator re-sync failed for card %s: %s", username, e
            )

        # ── 3) Best-effort CoA push so live MT session picks the new
        #    Mikrotik-Rate-Limit without waiting for re-auth. Same shape
        #    as adjust_card_time but a different attribute.
        coa_result = None
        try:
            push_coa = getattr(self._adapter, "push_rate_limit", None)
            if callable(push_coa) and username:
                rate = (f"{up}k/{down}k" if not clearing else "")
                coa_result = push_coa(username=username, new_rate_limit=rate)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "CoA push_rate_limit failed for %s: %s", username, e
            )

        # ── 4) Audit ──
        self._audit.record(
            actor=actor, action="card.set_speed",
            target_type="card", target_id=str(card_id),
            payload={
                "down_kbps":    down,
                "up_kbps":      up,
                "cleared":      clearing,
                "was_override": result["was_override"],
                "fr_synced":    fr_synced,
                "coa_pushed":   bool(coa_result and getattr(coa_result, "ok", False)),
            },
        )
        return {
            **result,
            "fr_synced":  fr_synced,
            "coa_result": coa_result,
        }

    def adjust_card_time(self, *, actor: str, card_id: int,
                          delta_seconds: int, username: str = "") -> dict:
        """Shift the card's expire_at by +/- delta_seconds and (best-effort)
        push a CoA Session-Timeout update to any live MikroTik session so
        the change takes effect without disconnecting the user.

        Returns the repo result dict; raises RadiusValidationError for any
        precondition failure (zero delta / card not found / not activated).

        CoA push is best-effort — if it fails we still keep the DB write,
        log a warning, and return so the caller can flash a helpful note.
        """
        if delta_seconds == 0:
            raise RadiusValidationError("لا يوجد تعديل لتطبيقه")
        tenant_id = self._store_tenant_id()
        result = cards_repo.adjust_card_expire_at(tenant_id, card_id, delta_seconds)
        if result is None:
            raise RadiusValidationError(
                "تعذر تعديل وقت البطاقة — تأكد أنها مفعّلة (لها وقت انتهاء)."
            )

        # ── Best-effort CoA push to update Session-Timeout on live sessions
        # We delegate to the adapter so each adapter implementation can decide
        # how to enumerate active NAS endpoints (MikroTik vs ManualAdapter).
        coa_result = None
        try:
            push_coa = getattr(self._adapter, "push_session_timeout", None)
            if callable(push_coa) and username:
                coa_result = push_coa(
                    username=username,
                    session_timeout=result["remaining_seconds"],
                )
        except Exception as e:  # noqa: BLE001 — never let CoA failure mask the DB write
            import logging
            logging.getLogger(__name__).warning(
                "CoA push_session_timeout failed for %s: %s", username, e
            )
            coa_result = None

        # ── Audit
        self._audit.record(
            actor=actor, action="card.adjust_time",
            target_type="card", target_id=str(card_id),
            payload={
                "delta_seconds":      delta_seconds,
                "expire_at_old":      result["expire_at_old"],
                "expire_at_new":      result["expire_at_new"],
                "remaining_seconds":  result["remaining_seconds"],
                "coa_pushed":         bool(coa_result and getattr(coa_result, "ok", False)),
            },
        )
        return {
            **result,
            "coa_result": coa_result,
        }

    def delete_card_permanently(self, *, actor: str, card_id: int) -> None:
        if not cards_repo.delete_card_permanently(self._store_tenant_id(), card_id):
            raise RadiusValidationError("تعذر حذف البطاقة")
        self._audit.record(actor=actor, action="card.delete_permanent",
                           target_type="card", target_id=str(card_id))

    def disconnect_card(self, *, actor: str, username: str,
                          session_id: str = "",
                          session_ids: list[str] | None = None,
                          reason: str = "manual") -> None:
        """Kick one, many, or all active sessions for the card.

        Selection rules (most-specific wins):
          • session_ids non-empty → kick exactly those.
          • session_id given      → kick that single one (legacy path).
          • neither               → kick every active session ('all').

        Records the result_status + reason so the disconnect appears in the
        unified MikroTik-actions feed with a real outcome (default reason
        «manual» = the admin kick button on the card page).
        """
        ids = list(session_ids) if session_ids else (
            [session_id] if session_id else None
        )
        _payload = {"session_ids": ids or "all",
                    "count": len(ids) if ids else None, "reason": reason,
                    # redaction-safe dedup key vs the router's radacct Acct-Stop
                    # ("session_ids" is masked as it contains "session").
                    "sid": (ids[0] if ids else "")}
        try:
            self._adapter.disconnect(username, session_ids=ids)
        except Exception as e:  # noqa: BLE001 — record the failure, then re-raise
            self._audit.record(actor=actor, action="card.disconnect",
                               target_type="card", target_id=username,
                               result_status="failed", severity="warning",
                               error_message=str(getattr(e, "message", "") or e)[:2000],
                               payload=_payload)
            raise
        self._audit.record(actor=actor, action="card.disconnect",
                           target_type="card", target_id=username,
                           result_status="success", payload=_payload)

    def archive_batch(self, *, actor: str, batch_id: int, reason: str = "") -> bool:
        archived = cards_repo.archive_batch(
            self._store_tenant_id(), batch_id, actor=actor, reason=reason,
        )
        if archived:
            self._audit.record(
                actor=actor,
                action=AUDIT_ACTION_BATCH_ARCHIVE,
                target_type="card_batch",
                target_id=str(batch_id),
                payload=roadmap_audit_payload(
                    domain="card_batches",
                    action=AUDIT_ACTION_BATCH_ARCHIVE,
                    reason=reason,
                ),
            )
        return archived

    def restore_batch(self, *, actor: str, batch_id: int) -> bool:
        restored = cards_repo.restore_batch(self._store_tenant_id(), batch_id, actor=actor)
        if restored:
            self._audit.record(
                actor=actor,
                action="card_batch.restore",
                target_type="card_batch",
                target_id=str(batch_id),
                payload=roadmap_audit_payload(
                    domain="card_batches",
                    action="card_batch.restore",
                ),
            )
        return restored


# حالة الدفعة بالعربيّة لسجل التغييرات (كان X ← صار Y).
_BATCH_STATUS_AR: dict[str, str] = {
    "active": "نشطة", "exhausted": "مُستنفدة", "revoked": "ملغاة",
    "archived": "مؤرشفة", "deleted": "محذوفة",
}


def _batch_snapshot(batch, plan_name: str = "") -> dict:
    """لقطة مقروءة لحقول دفعة الكروت ذات المعنى — تُخزَّن في before/after فيَظهر
    «الحقل: كان X ← صار Y» عند تعديل الدفعة. القيم مقروءة (اسم الباقة + الحالة
    بالعربيّة + المدّة كرقم+وحدة). لا تُدرَج البنية المقفلة بعد التوليد."""
    if batch is None:
        return {}
    g = lambda a, d=None: getattr(batch, a, d)
    st = (g("status", "") or "").strip()
    tv = int(g("time_value", 0) or 0)
    tu = (g("time_unit", "") or "").strip()
    _TU_AR = {"days": "يوم", "hours": "ساعة", "minutes": "دقيقة", "seconds": "ثانية"}
    return {
        "package_name": (g("package_name", "") or "").strip(),
        "plan": plan_name or (f"#{g('plan_id')}" if g("plan_id") else ""),
        "status": _BATCH_STATUS_AR.get(st, st) if st else "",
        "total_quota_mb": int(g("total_quota_mb", 0) or 0),
        "price_per_card": float(g("price_per_card", 0) or 0),
        "price_bulk": float(g("price_bulk", 0) or 0),
        "validity_after_first_login_days": int(g("validity_after_first_login_days", 0) or 0),
        "duration": (f"{tv} {_TU_AR.get(tu, tu)}".strip() if tv else ""),
        "device_count": int(g("device_count", 0) or 0),
        "on_quota_exhaust": (g("on_quota_exhaust", "") or "").strip(),
        "service_name": (g("service_name", "") or "").strip(),
        "notes": (g("notes", "") or "").strip(),
    }


def get_cards_service() -> CardsService:
    from ..integration.factory import get_radius_adapter
    from .audit import get_audit_service
    return CardsService(get_radius_adapter(), audit=get_audit_service())
