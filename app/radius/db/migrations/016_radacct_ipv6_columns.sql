-- 016_radacct_ipv6_columns — يضيف الأعمدة الثلاثة التي يكتبها
-- FreeRADIUS 3.2.x accounting_start_query لكن لم تكن في radacct
-- (المُعرَّف في 006_logs.sql):
--
--   framedipv6prefix
--   framedinterfaceid
--   delegatedipv6prefix
--
-- بدون هذه الأعمدة، كل Acct-Start INSERT يفشل بـ
-- "no such column: framedipv6prefix"، فلا تُنشأ rows في radacct عبر
-- FreeRADIUS sql module. R1 (commit f72e3d4) جعل الـ section غير حاجزة
-- كي يُرسَل Accounting-Response — لكن radacct لا يزال غير ممتلئ.
--
-- هذه الـ migration تُكمل الـ schema. لا تغيير على business logic.
-- idempotency: مضمونة عبر migration runner (_migrations table يتتبّع
-- ما تمّ تطبيقه باسم الـ file). لا نحتاج IF NOT EXISTS لأن executescript
-- لن يُعاد تشغيله بعد أن يُسجَّل.

ALTER TABLE radacct ADD COLUMN framedipv6prefix    TEXT DEFAULT '';
ALTER TABLE radacct ADD COLUMN framedinterfaceid   TEXT DEFAULT '';
ALTER TABLE radacct ADD COLUMN delegatedipv6prefix TEXT DEFAULT '';
