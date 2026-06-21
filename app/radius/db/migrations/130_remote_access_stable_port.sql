-- Remote-access: a STABLE external port per device.
--
-- Until now the external port the operator connects to was picked
-- per-session by next_free_external_port() — deterministic from the
-- device id (40000 + device_id % 20000) but never persisted, so it
-- could drift if a collision walk kicked in, and the operator had no
-- single "fixed" IP:port to memorise / hand out for a given MikroTik.
--
-- This column pins one external port per device. It is allocated the
-- first time a session is opened for the device (collision-free vs
-- every other device's pinned port) and reused for every later open,
-- so «وصول عن بُعد» always reveals the SAME IP:port for that device.
-- 0 = not yet allocated.
ALTER TABLE network_devices
  ADD COLUMN remote_ext_port INTEGER NOT NULL DEFAULT 0;
