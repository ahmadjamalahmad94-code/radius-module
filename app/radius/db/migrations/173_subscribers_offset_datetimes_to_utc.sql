-- ════════════════════════════════════════════════════════════════════════
-- 173 — تواريخ المشتركين المخزّنة بإزاحة («…+03:00Z») → UTC ساكن «…Z»
--
-- كان `accounts._parse_dt` في الـ API يحذف «Z» فقط، فتاريخٌ بإزاحة مثل
-- 2026-10-01T10:00:00+03:00 يُخزَّن واعيًا ويُكتب «…+03:00Z» — صيغةٌ تُفسد
-- المقارنات النصّيّة في SQL (3 ساعات خطأ) وكانت تُسقط لوحة التحكّم (500).
-- الإصلاح يُطبّع المُدخل؛ هذه الهجرة تُطبّع ما خُزّن قبله. بلا فقد: نفس
-- اللحظة، بتوقيت UTC (strftime في SQLite يحوّل الإزاحة إلى UTC). متكرّرة
-- التطبيق بأمان (لا تطابق الصيغة الصحيحة).
-- ════════════════════════════════════════════════════════════════════════

UPDATE subscribers
   SET expire_at = strftime('%Y-%m-%dT%H:%M:%S', substr(expire_at, 1, length(expire_at) - 1)) || 'Z'
 WHERE expire_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]Z'
   AND strftime('%Y-%m-%dT%H:%M:%S', substr(expire_at, 1, length(expire_at) - 1)) IS NOT NULL;
UPDATE subscribers
   SET expire_at = strftime('%Y-%m-%dT%H:%M:%S', expire_at) || 'Z'
 WHERE expire_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]'
   AND strftime('%Y-%m-%dT%H:%M:%S', expire_at) IS NOT NULL;

UPDATE subscribers
   SET first_login_at = strftime('%Y-%m-%dT%H:%M:%S', substr(first_login_at, 1, length(first_login_at) - 1)) || 'Z'
 WHERE first_login_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]Z'
   AND strftime('%Y-%m-%dT%H:%M:%S', substr(first_login_at, 1, length(first_login_at) - 1)) IS NOT NULL;
UPDATE subscribers
   SET first_login_at = strftime('%Y-%m-%dT%H:%M:%S', first_login_at) || 'Z'
 WHERE first_login_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]'
   AND strftime('%Y-%m-%dT%H:%M:%S', first_login_at) IS NOT NULL;

UPDATE subscribers
   SET last_login_at = strftime('%Y-%m-%dT%H:%M:%S', substr(last_login_at, 1, length(last_login_at) - 1)) || 'Z'
 WHERE last_login_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]Z'
   AND strftime('%Y-%m-%dT%H:%M:%S', substr(last_login_at, 1, length(last_login_at) - 1)) IS NOT NULL;
UPDATE subscribers
   SET last_login_at = strftime('%Y-%m-%dT%H:%M:%S', last_login_at) || 'Z'
 WHERE last_login_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]'
   AND strftime('%Y-%m-%dT%H:%M:%S', last_login_at) IS NOT NULL;

UPDATE subscribers
   SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%S', substr(last_seen_at, 1, length(last_seen_at) - 1)) || 'Z'
 WHERE last_seen_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]Z'
   AND strftime('%Y-%m-%dT%H:%M:%S', substr(last_seen_at, 1, length(last_seen_at) - 1)) IS NOT NULL;
UPDATE subscribers
   SET last_seen_at = strftime('%Y-%m-%dT%H:%M:%S', last_seen_at) || 'Z'
 WHERE last_seen_at GLOB '*T*[+-][0-9][0-9]:[0-9][0-9]'
   AND strftime('%Y-%m-%dT%H:%M:%S', last_seen_at) IS NOT NULL;
