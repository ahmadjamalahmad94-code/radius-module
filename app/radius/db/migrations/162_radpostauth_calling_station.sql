-- 162_radpostauth_calling_station — store the attempted client MAC
-- (Calling-Station-Id) on every auth attempt.
--
-- The login-states / failed-logins reports read radpostauth, but it had no
-- MAC column, so the «الجهاز (MAC)» cell was empty («—») — you could see a
-- «MAC غير مطابق» rejection without knowing WHICH device tried. FreeRADIUS
-- already forwards Calling-Station-Id to Flask; policy_engine._log_attempt
-- now persists it here so the report shows the exact attempted MAC.
ALTER TABLE radpostauth ADD COLUMN calling_station TEXT NOT NULL DEFAULT '';
