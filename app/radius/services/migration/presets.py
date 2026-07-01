"""إعدادات مصادر معروفة (Recognized-source PRESETS) — نموذج البيانات
المُعكَّس هندسيًّا لمنتجات RADIUS/ISP شائعة، مكتوبٌ صراحةً كي يكون التصنيف
**حتميًّا** لتلك المخطّطات لا تخمينًا. الاستدلال الضبابيّ (heuristics) يبقى
مسار الاحتياط للمصادر المجهولة فقط.

كل PRESET يحمل: توقيع كشف (أعمدة/جداول مميِّزة)، والخرائط الحتميّة لكل قسم،
والعلاقات بالمفاتيح الحقيقيّة. الآليّة الفعليّة مبثوثة في classify/mapping؛
هذا الملفّ يوثّق «الفهم» ويُميّز المصدر ليظهر في الواجهة وليُسنِد القرارات.

────────────────────────────────────────────────────────────────────────
PRESET «adv_hotspot» — لوحة إدارة هوتسبوت/RADIUS تجاريّة (منها دمب العميل
adv_dbq…). عُكِس مخطّطها من دمب حقيقيّ (103 جداول):

• المشتركون الحقيقيّون: جدول ``userinfo`` (username=رقم الجوال؛ firstname،
  lastname، email، mobile، address، ``creationby``=معرّف المدير المُنشئ،
  ``id_card``، down_speed/up_speed، total_paid/unpaid_invoices…). عددهم
  الحقيقيّ صغير (مثال العميل: 1589).

• المصادقة + الكروت: جدول ``radcheck`` (EAV: username, attribute, op, value)
  **مع عمود مميِّز ``is_card``**:
    - ``is_card = 0`` → مشترك حقيقيّ.
    - ``is_card = 1`` → **كرت/قسيمة** (رمز مولَّد) — ``id_card`` يربطه بالسلسلة.
  كلمة المرور صفٌّ حيث ``attribute = Cleartext-Password`` (أو المُجزّأة).
  هذا العمود هو الإشارة الحاسمة التي تفصل الكروت عن المشتركين (لا تخمين نمط).
  (مثال العميل: 21489 مستخدمًا = 1589 مشترك + 19900 كرت.)

• ربط الباقة: ``radusergroup`` (username → groupname = اسم الباقة/البروفايل).

• الباقات: ``profiles`` (id، profile_name، price، down_speed، up_speed،
  exp_unit/exp_unit_val، profile_qouta…). أسعار المدير في
  ``prices_profiles_admin`` (id_manager، id_profile، new_price).

• المدراء: جدول ``managers`` (id، ``user_manager``=اسم الدخول، ``pass``،
  ``full_name``، parent، security_group). «انشئ بواسطة» في userinfo رقميّ =
  ``managers.id`` → يُحَلّ إلى ``user_manager`` (لا يُفبرَك مديرٌ اسمه «6»).
  ``a_s_manager`` = حركات ماليّة للمدير (ليست قائمة مدراء).

• الكروت (جداول إضافيّة): ``card_users`` (دفعات توليد: profile، owner،
  num_ser، created_by)، ``list_cards`` (name_card=الرمز، id_card، serial،
  is_used)، ``series_cards`` (السلاسل)، ``converted_cards`` (card_num+mac)،
  ``rep_cards`` (username=رمز، num_ser). الرمز يظهر أيضًا في radcheck
  is_card=1.

الخرائط الحتميّة (المُطبَّقة في classify/mapping):
  subscribers = radcheck[is_card=0] ∪ userinfo  (مفتاح username؛ كلمة من
                radcheck؛ باقة من radusergroup؛ مدير محلول من creationby).
  cards       = radcheck[is_card=1] + جداول الكروت  → قسم «الكروت» فقط.
  managers    = managers (user_manager/full_name)؛ الأرقام تُحَلّ لا تُفبرَك.
  plans       = profiles / prices_profiles_admin.
────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from .sections import norm_key


def _cols(table) -> set[str]:
    return {norm_key(c) for c in table.columns}


def _has(dataset, name: str) -> bool:
    nk = norm_key(name)
    return any(norm_key(t.name) == nk for t in dataset.tables)


def _table(dataset, name: str):
    nk = norm_key(name)
    for t in dataset.tables:
        if norm_key(t.name) == nk:
            return t
    return None


def recognize(dataset) -> str:
    """يُعيد اسم المصدر المعروف («adv_hotspot» / «freeradius» / «mikrotik» /
    «») ليُعرَض في الواجهة. لا يغيّر التصنيف (المبثوث في classify) بل يُسمّيه."""
    rc = _table(dataset, "radcheck")
    # لوحة تجاريّة: radcheck فيه is_card + جدول managers فيه user_manager.
    if rc is not None and _cols(rc) & {"is_card", "iscard"}:
        mg = _table(dataset, "managers")
        if mg is not None and _cols(mg) & {"user_manager"}:
            return "adv_hotspot"
    if rc is not None and _cols(rc) >= {"attribute", "value"}:
        return "freeradius"
    for t in dataset.tables:
        if t.origin == "mikrotik":
            return "mikrotik"
    return ""


_LABELS = {
    "adv_hotspot": "لوحة هوتسبوت/RADIUS تجاريّة (نمط adv) — تصنيف حتميّ "
                   "(is_card يفصل الكروت، والمدراء الرقميّون يُحَلّون)",
    "freeradius": "قاعدة FreeRADIUS (radcheck/radusergroup)",
    "mikrotik": "تصدير MikroTik RouterOS",
}


def label(name: str) -> str:
    return _LABELS.get(name, "")


__all__ = ["recognize", "label"]
