-- 164 — إدارة الراوترات عن بُعد (TR-069 / CWMP عبر GenieACS).
--
-- وحدة تجريبيّة مستقلّة تمامًا: لا تلمس أي جدول قائم (additive only). TR-069
-- لا يمرّ داخل FreeRADIUS إطلاقًا؛ GenieACS خدمة منفصلة، وهذه الجداول هي طبقة
-- التحكّم في HobeRadius (ربط الجهاز بالمشترك، طابور الأوامر، السجلّ، الموديلات).
-- كل جدول tenant-scoped (tenant_id DEFAULT 1) + soft-delete حيث يلزم.

-- ═══ الأجهزة: الهويّة الدائمة + الحالة ═══
CREATE TABLE IF NOT EXISTS tr069_devices (
  id                          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                   INTEGER NOT NULL DEFAULT 1,
  -- الربط
  owner_admin_id              INTEGER,                       -- المالك/المدير المسؤول
  subscriber_id               INTEGER,                       -- المشترك (NULL = غير مربوط)
  radius_username             TEXT NOT NULL DEFAULT '',       -- اسم PPPoE/RADIUS المربوط
  nas_id                      INTEGER,                        -- الراوتر/NAS (اختياريّ)
  -- هويّة GenieACS/CWMP
  acs_device_id               TEXT NOT NULL DEFAULT '',       -- OUI-ProductClass-Serial (من GenieACS)
  oui                         TEXT NOT NULL DEFAULT '',
  product_class               TEXT NOT NULL DEFAULT '',
  serial_number               TEXT NOT NULL DEFAULT '',
  -- معلومات الجهاز (عرض فقط، تُحدَّث من Inform)
  manufacturer                TEXT NOT NULL DEFAULT '',
  model_name                  TEXT NOT NULL DEFAULT '',
  hardware_version            TEXT NOT NULL DEFAULT '',
  software_version            TEXT NOT NULL DEFAULT '',
  wan_mac                     TEXT NOT NULL DEFAULT '',
  lan_mac                     TEXT NOT NULL DEFAULT '',
  wan_ip                      TEXT NOT NULL DEFAULT '',
  lan_ip                      TEXT NOT NULL DEFAULT '',
  pppoe_username_detected     TEXT NOT NULL DEFAULT '',
  data_model                  TEXT NOT NULL DEFAULT '',        -- tr098 | tr181 | ''
  root_object                 TEXT NOT NULL DEFAULT '',        -- InternetGatewayDevice | Device
  -- اعتماد CWMP (مشفَّر Fernet عبر env_settings — لا يُعاد للـ Frontend أبدًا)
  enrollment_token_hash       TEXT NOT NULL DEFAULT '',
  cwmp_username               TEXT NOT NULL DEFAULT '',
  cwmp_password_enc           TEXT NOT NULL DEFAULT '',
  conn_req_username           TEXT NOT NULL DEFAULT '',
  conn_req_password_enc       TEXT NOT NULL DEFAULT '',
  -- الحالة
  status                      TEXT NOT NULL DEFAULT 'pending', -- pending|active|disabled|archived
  provisioning_status         TEXT NOT NULL DEFAULT 'none',    -- none|awaiting_inform|provisioning|provisioned|failed
  is_online                   INTEGER NOT NULL DEFAULT 0,
  is_managed                  INTEGER NOT NULL DEFAULT 1,
  management_disabled_at      TEXT,
  -- زمنيّات وأخطاء
  last_inform_at              TEXT,
  last_boot_at                TEXT,
  last_sync_at                TEXT,
  last_error_at               TEXT,
  last_error_code             TEXT NOT NULL DEFAULT '',
  last_error_message          TEXT NOT NULL DEFAULT '',
  metadata_json               TEXT NOT NULL DEFAULT '{}',
  created_at                  TEXT NOT NULL,
  updated_at                  TEXT,
  deleted_at                  TEXT,
  CHECK (status IN ('pending','active','disabled','archived'))
);
CREATE INDEX IF NOT EXISTS ix_tr069_devices_tenant
  ON tr069_devices (tenant_id, status, id DESC);
CREATE INDEX IF NOT EXISTS ix_tr069_devices_sub
  ON tr069_devices (tenant_id, subscriber_id);
CREATE INDEX IF NOT EXISTS ix_tr069_devices_owner
  ON tr069_devices (tenant_id, owner_admin_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tr069_devices_acs
  ON tr069_devices (tenant_id, acs_device_id)
  WHERE acs_device_id != '' AND deleted_at IS NULL;

-- ═══ طابور الأوامر بحالاته ═══
CREATE TABLE IF NOT EXISTS tr069_device_actions (
  id                     INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id              INTEGER NOT NULL DEFAULT 1,
  owner_admin_id         INTEGER,
  device_id              INTEGER NOT NULL,
  requested_by           TEXT NOT NULL DEFAULT '',             -- الفاعل (اسم/معرّف)
  action_type            TEXT NOT NULL,                        -- reboot|refresh|change_wifi|change_pppoe|factory_reset|firmware_upgrade|...
  request_payload        TEXT NOT NULL DEFAULT '{}',           -- بعد _redact
  safe_summary           TEXT NOT NULL DEFAULT '',             -- ملخّص خالٍ من الأسرار للعرض
  acs_task_id            TEXT NOT NULL DEFAULT '',
  status                 TEXT NOT NULL DEFAULT 'pending',      -- pending|queued|sent|waiting_for_inform|acknowledged|completed|failed|expired|cancelled
  queued_at              TEXT,
  sent_at                TEXT,
  acknowledged_at        TEXT,
  completed_at           TEXT,
  failed_at              TEXT,
  expires_at             TEXT,
  retry_count            INTEGER NOT NULL DEFAULT 0,
  max_retries            INTEGER NOT NULL DEFAULT 3,
  error_code             TEXT NOT NULL DEFAULT '',
  error_message          TEXT NOT NULL DEFAULT '',
  result_payload         TEXT NOT NULL DEFAULT '{}',
  correlation_id         TEXT NOT NULL DEFAULT '',
  idempotency_key        TEXT NOT NULL DEFAULT '',
  created_at             TEXT NOT NULL,
  updated_at             TEXT,
  FOREIGN KEY (device_id) REFERENCES tr069_devices(id) ON DELETE CASCADE,
  CHECK (status IN ('pending','queued','sent','waiting_for_inform','acknowledged',
                    'completed','failed','expired','cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_tr069_actions_device
  ON tr069_device_actions (tenant_id, device_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_tr069_actions_status
  ON tr069_device_actions (status, id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tr069_actions_idem
  ON tr069_device_actions (device_id, idempotency_key)
  WHERE idempotency_key != '';

-- ═══ لقطة الحالة الحاليّة (صفّ واحد لكل جهاز) ═══
CREATE TABLE IF NOT EXISTS tr069_device_snapshots (
  device_id           INTEGER PRIMARY KEY,
  tenant_id           INTEGER NOT NULL DEFAULT 1,
  uptime_seconds      INTEGER NOT NULL DEFAULT 0,
  wan_status          TEXT NOT NULL DEFAULT '',
  wifi_json           TEXT NOT NULL DEFAULT '{}',              -- SSID/enabled/band...
  connected_hosts     INTEGER,                                 -- NULL = غير مدعوم
  firmware_json       TEXT NOT NULL DEFAULT '{}',
  dsl_json            TEXT NOT NULL DEFAULT '{}',
  raw_json            TEXT NOT NULL DEFAULT '{}',              -- لقطة خام مختصرة
  captured_at         TEXT NOT NULL,
  FOREIGN KEY (device_id) REFERENCES tr069_devices(id) ON DELETE CASCADE
);

-- ═══ ملفّات تعريف الموديلات (خريطة المسارات لطبقة التجريد) ═══
CREATE TABLE IF NOT EXISTS tr069_model_profiles (
  id                       INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id                INTEGER NOT NULL DEFAULT 1,
  manufacturer             TEXT NOT NULL DEFAULT '',
  oui                      TEXT NOT NULL DEFAULT '',
  product_class            TEXT NOT NULL DEFAULT '',
  model_pattern            TEXT NOT NULL DEFAULT '',
  data_model               TEXT NOT NULL DEFAULT '',           -- tr098|tr181
  paths_json               TEXT NOT NULL DEFAULT '{}',         -- {wifi_ssid,wifi_password,wifi_enable,pppoe_username,pppoe_password,wan_ip,wan_mac,connected_hosts,firmware}
  reboot_supported         INTEGER NOT NULL DEFAULT 1,
  factory_reset_supported  INTEGER NOT NULL DEFAULT 0,
  firmware_supported       INTEGER NOT NULL DEFAULT 0,
  notes                    TEXT NOT NULL DEFAULT '',
  verified_at              TEXT,
  created_at               TEXT NOT NULL,
  updated_at               TEXT
);
CREATE INDEX IF NOT EXISTS ix_tr069_model_profiles_match
  ON tr069_model_profiles (tenant_id, oui, product_class);

-- ═══ سجلّ ربط الجهاز بالمشترك عبر الزمن ═══
CREATE TABLE IF NOT EXISTS tr069_device_assignments (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id       INTEGER NOT NULL DEFAULT 1,
  device_id       INTEGER NOT NULL,
  subscriber_id   INTEGER,
  radius_username TEXT NOT NULL DEFAULT '',
  assigned_by     TEXT NOT NULL DEFAULT '',
  method          TEXT NOT NULL DEFAULT '',                    -- enrollment|pppoe|mac|manual
  unassigned_at   TEXT,
  created_at      TEXT NOT NULL,
  FOREIGN KEY (device_id) REFERENCES tr069_devices(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_tr069_assignments_device
  ON tr069_device_assignments (tenant_id, device_id, id DESC);

-- ═══ صور الـ Firmware المعتمدة ═══
CREATE TABLE IF NOT EXISTS tr069_firmware_images (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id         INTEGER NOT NULL DEFAULT 1,
  manufacturer      TEXT NOT NULL DEFAULT '',
  model_pattern     TEXT NOT NULL DEFAULT '',
  hardware_version  TEXT NOT NULL DEFAULT '',
  firmware_version  TEXT NOT NULL DEFAULT '',
  file_path         TEXT NOT NULL DEFAULT '',
  sha256            TEXT NOT NULL DEFAULT '',
  file_size         INTEGER NOT NULL DEFAULT 0,
  release_notes     TEXT NOT NULL DEFAULT '',
  approval_state    TEXT NOT NULL DEFAULT 'pending',           -- pending|approved|rejected
  rollout_state     TEXT NOT NULL DEFAULT 'draft',             -- draft|canary|active|halted
  created_at        TEXT NOT NULL,
  updated_at        TEXT,
  CHECK (approval_state IN ('pending','approved','rejected'))
);
CREATE INDEX IF NOT EXISTS ix_tr069_firmware_model
  ON tr069_firmware_images (tenant_id, manufacturer, model_pattern);

-- ═══ أحداث الأجهزة (Inform/Boot/Fault/Online/Offline/...) ═══
CREATE TABLE IF NOT EXISTS tr069_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id     INTEGER NOT NULL DEFAULT 1,
  device_id     INTEGER,
  acs_device_id TEXT NOT NULL DEFAULT '',
  event_type    TEXT NOT NULL DEFAULT '',                      -- inform|boot|periodic|value_change|connection_request|factory_reset|transfer_complete|fault|online|offline
  detail_json   TEXT NOT NULL DEFAULT '{}',
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tr069_events_device
  ON tr069_events (tenant_id, device_id, id DESC);
