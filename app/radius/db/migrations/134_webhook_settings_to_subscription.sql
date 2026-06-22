-- توحيد الويبهوك في صفحة «الربط الخارجي» (webhook_subscriptions).
--
-- كان لدى الإعدادات (System Settings) كتلة Webhook مفردة عبر المفتاحين
-- tenant_settings: webhook.target_url + webhook.secret. أُزيلت هذه الكتلة
-- من الواجهة لصالح صفحة /admin/radius/webhooks القائمة على
-- webhook_subscriptions (تدعم أكثر من اشتراك + سجلّ إرسال + توقيع HMAC).
--
-- حفاظًا على عدم فقدان البيانات: أي مستأجر ضبط رابطًا مفردًا سابقًا (قيمة
-- غير فارغة في webhook.target_url) ولا يملك أي اشتراك بعد — نُنشئ له اشتراكًا
-- مكافئًا (نفس الرابط + السرّ) مُفعَّلًا بكل الأحداث (enabled_events_json='[]'
-- = جميع الأحداث في مُرسِل الأحداث). فيبدأ الإرسال فعليًا للمرّة الأولى
-- عبر المسار الموحّد.
--
-- idempotent: الحارس ‎NOT EXISTS‏ يمنع إنشاء اشتراك مكرّر عند إعادة التشغيل
-- أو حين يملك المستأجر اشتراكًا أصلًا. القيمتان المفردتان تبقيان في
-- tenant_settings (غير مقروءتين من الكود) فلا أثر جانبيًا.
INSERT INTO webhook_subscriptions
    (tenant_id, target_url, secret, enabled_events_json, enabled, created_at)
SELECT
    ts.tenant_id,
    TRIM(ts.value),
    COALESCE((SELECT TRIM(sec.value)
                FROM tenant_settings sec
               WHERE sec.tenant_id = ts.tenant_id
                 AND sec.key = 'webhook.secret'), ''),
    '[]',
    1,
    strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
FROM tenant_settings ts
WHERE ts.key = 'webhook.target_url'
  AND TRIM(COALESCE(ts.value, '')) <> ''
  AND NOT EXISTS (
        SELECT 1 FROM webhook_subscriptions ws
         WHERE ws.tenant_id = ts.tenant_id
  );
