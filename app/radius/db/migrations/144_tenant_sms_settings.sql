-- 144_tenant_sms_settings — per-tenant BYO SMS provider credentials.
--
-- SMS is a FREE bring-your-own-provider service: every customer (tenant)
-- connects their OWN external SMS gateway and buys credit from that provider
-- directly. There is no admin-sold message bundle / quota anymore.
--
-- Provider today is TweetSMS (tweetsms.ps legacy api.php). The customer plugs
-- in EITHER an api_key OR a username+password pair, plus an approved sender
-- name (اسم المرسل). Secrets (api_key / password) are stored ENCRYPTED at rest
-- (Fernet derived from FLASK_SECRET, ``enc:`` prefix — same scheme as
-- tenant_telegram_settings.bot_token). Masked everywhere in the UI.
--
-- One row per tenant. Additive only; the runner applies this file exactly once.
CREATE TABLE IF NOT EXISTS tenant_sms_settings (
    tenant_id   INTEGER PRIMARY KEY,
    provider    TEXT    NOT NULL DEFAULT 'tweetsms',
    api_key     TEXT    NOT NULL DEFAULT '',   -- encrypted (enc:…) or ''
    username    TEXT    NOT NULL DEFAULT '',
    password    TEXT    NOT NULL DEFAULT '',   -- encrypted (enc:…) or ''
    sender      TEXT    NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT
);
