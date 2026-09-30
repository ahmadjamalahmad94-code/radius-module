-- fix3 (F01 F9 / F02 H2 / F08 M2): admin notifications were tenant-wide — every
-- manager saw every subscriber operation of every other manager, and «تعليم
-- الكل كمقروء» by a limited manager marked the whole network's notifications.
--   * subscriber_username — the subscriber the event is about (scope filter);
--   * audience            — the alert group (subscribers/network/routers/
--                           finance/store/security/system); '' = legacy/system;
--   * actor_admin_id      — the admin who performed the operation;
--   * panel_notification_reads — per-admin read state for non-owner admins
--     (the owner keeps the tenant-wide read_at, as before).
ALTER TABLE panel_notifications ADD COLUMN subscriber_username TEXT NOT NULL DEFAULT '';
ALTER TABLE panel_notifications ADD COLUMN audience TEXT NOT NULL DEFAULT '';
ALTER TABLE panel_notifications ADD COLUMN actor_admin_id INTEGER;
CREATE INDEX IF NOT EXISTS ix_panel_notifications_sub ON panel_notifications(tenant_id, subscriber_username);
CREATE TABLE IF NOT EXISTS panel_notification_reads (
    tenant_id INTEGER NOT NULL DEFAULT 1,
    notification_id INTEGER NOT NULL,
    admin_id INTEGER NOT NULL,
    read_at TEXT NOT NULL,
    PRIMARY KEY (notification_id, admin_id)
);
CREATE INDEX IF NOT EXISTS ix_panel_notification_reads_admin ON panel_notification_reads(admin_id, notification_id);
