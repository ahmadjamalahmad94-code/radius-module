"""محرّك ترحيل بيانات العملاء — استيراد قاعدة/جدول/PDF من نظام RADIUS/ISP آخر.

يحوّل أيّ مصدر (قاعدة SQLite، تفريغ SQL لـMySQL/Postgres، Excel/CSV، تصدير
MikroTik ‎.rsc، PDF جدوليّ) إلى مجموعة بيانات موحّدة، ثمّ يصنّفها إلى «أقسام»
يفهمها HobeRadius (مدراء/صلاحيات/موزّعون/مشتركون/كروت/باقات)، ويبني خطّة
استيراد (للقراءة فقط) تُظهر ما سيُنشأ/يُدمج/يُتخطّى ولماذا، ثمّ يُنفّذها بمعاملة
ذرّيّة وبشكل idempotent (إعادة التشغيل لا تُكرّر).

الطبقات (نظير cards_import_engine + mt_import، لكنها معمّمة):

    sources   → sniff + introspect   → SourceDataset
    classify  → table → section      → list[SectionMatch]
    mapping   → selection → candidates
    engine    → analyze / build_plan / commit

كل الدوال في sources/classify/mapping/sections خالصة (بلا Flask/DB). الكتابة
الوحيدة تحدث في engine.commit عبر مستودعات HobeRadius القائمة (لا مخزن مُوازٍ).
"""
from __future__ import annotations

# نسخة المحرّك — تُرفَع مع كل تغيير في منطق الكشف/التصنيف، وتظهر في الواجهة
# كشارة بناء كي يتأكّد المالك بصريًّا أن الحاوية تشغّل أحدث كود.
ENGINE_VERSION = "1.0"
ENGINE_BUILD_NOTE = "sources+classify+plan+commit"

__all__ = ["ENGINE_VERSION", "ENGINE_BUILD_NOTE"]
