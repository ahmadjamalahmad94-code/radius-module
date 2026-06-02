-- Loop detection probes (smart alerts, phase 3).
--
-- Method (operator-chosen, matches the MikroTik DHCP-Client trick): a passive
-- DHCP client is placed on a chosen access interface with add-default-route=no
-- + use-peer-dns/ntp=no. That port should never see a DHCP server, so it stays
-- "searching". If a LOOP folds the port back into a segment that has a DHCP
-- server (typically the router's own LAN), the probe gets a lease (status=
-- bound) — that lease is the loop signature, and its IP points at the segment.
--
-- The router pushes each probe's reading to /api/v1/routers/<id>/loop/ingest;
-- the server upserts the row + raises auto.router.loop when a probe is bound.
-- One row per (tenant, router, interface).

CREATE TABLE IF NOT EXISTS router_loop_probes (
    tenant_id       INTEGER NOT NULL DEFAULT 1,
    router_id       INTEGER NOT NULL,
    interface       TEXT    NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 1,
    last_status     TEXT    NOT NULL DEFAULT '',   -- searching | bound | stopped
    last_lease_ip   TEXT    NOT NULL DEFAULT '',   -- leased address (the "loop IP")
    last_server_ip  TEXT    NOT NULL DEFAULT '',   -- DHCP server / gateway that answered
    last_reading_at TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (tenant_id, router_id, interface)
);

CREATE INDEX IF NOT EXISTS ix_router_loop_probes_router
    ON router_loop_probes (tenant_id, router_id);
