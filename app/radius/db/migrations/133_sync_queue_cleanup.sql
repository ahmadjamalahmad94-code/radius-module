-- تنظيف طابور المزامنة القديم (sync_queue) — chore/customer-panel-cleanup يونيو 2026.
--
-- خلفية: ``sync_queue`` طابور router-push قديم. بعد Phase N3 أُسقط جدول
--   ``mikrotik_configs`` (migration 035) وصار النظام مدعومًا بـRADIUS
--   بالكامل، فأصبح ``router_sync.execute_job`` بلا أثر فعليّ
--   (``mikrotik_repo.list_configs`` تُعيد ``[]`` عند غياب الجدول فالـjob
--   يُعدّ منجزًا noop). الصفوف ذات الحالة 'failed'/'retrying' هي بقايا
--   محاولات دفع قديمة قبل هذا التحوّل — «أعطال» وهميّة بلا قيمة تشغيليّة،
--   كانت تَملأ صفحة «طابور المزامنة» (أُزيلت من شريط العميل في هذا التنظيف).
--
-- الإجراء آمن وحياديّ: يحوّل الصفوف العالقة (failed/retrying) فقط إلى
--   'done'. لا يُسقط الجدول (يبقى مستخدمًا: enqueue→worker→done noop)، ولا
--   يلمس صفوف queued/syncing الجارية. idempotent: إعادة التطبيق على قاعدة
--   نظيفة لا تؤثّر (شرط WHERE يصبح فارغًا). يطابق
--   ``sync_queue_repo.mark_stale_resolved`` (نفس المنطق، قابل للاستدعاء/الاختبار).
--
-- ملاحظة: ``COALESCE(NULLIF(completed_at,''), …)`` يَحفظ ختم إنجاز سابق إن
--   وُجد ويَملأ الفارغ بـUTC الآن — حتى لا تظهر الصفوف المُحلّاة بلا تاريخ.
UPDATE sync_queue
   SET status = 'done',
       completed_at = COALESCE(NULLIF(completed_at, ''),
                               strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
       last_error = ''
 WHERE status IN ('failed', 'retrying');
