# Setup Wizard v3 — Diagnostic Codes Catalog

Every failure surface in the new unified wizard emits a **structured
diagnostic** (not a string). The verification worker, the recovery UI,
and the support-bundle exporter all consume from this catalog.

## Schema

```python
{
    "code": "wg_handshake_never",            # stable machine-readable
    "category": "vpn" | "radius" | "nas" | "ops_room" | "internet" | "service",
    "severity": "blocker" | "warning" | "info",
    "symptom_ar": "...",                      # what the user sees
    "symptom_en": "...",
    "likely_causes_ar": ["...", "..."],
    "auto_fix_available": True | False,
    "auto_fix_command": "...",                # bash | routeros | sql
    "auto_fix_runs_on": "vps" | "router" | "wizard_server",
    "manual_fix_steps_ar": ["خطوة 1", "خطوة 2"],
    "docs_link": "docs/radius/...",
    "next_check_after_fix": "code_to_rerun",
}
```

## Catalog

### VPN category

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `wg_handshake_never` | لم يكتمل أي تصافح WireGuard بعد إقلاع الراوتر | نعم | Triggers Section A fix from [ROUTER_14_PING_DIAGNOSIS.md](ROUTER_14_PING_DIAGNOSIS.md) |
| `wg_handshake_stale` | آخر تصافح قبل أكثر من 5 دقائق | جزئي | Increase `persistent-keepalive` to 25s |
| `wg_pubkey_mismatch_router_side` | الراوتر يستخدم مفتاح خادم قديم | نعم | Regenerate router script |
| `wg_pubkey_mismatch_server_side` | الخادم لا يعرف المفتاح العام للراوتر | نعم | Write `/etc/hoberadius/wg-peers.d/router-{id}.conf` |
| `wg_allowed_ip_conflict` | عنوان VPN محجوز من راوتر آخر | لا | Pick new IP from pool; rerun script |
| `wg_endpoint_unreachable` | منفذ UDP/51820 محجوب من ISP الراوتر | لا | Switch to TCP transport |
| `wg_no_packets_received_vps` | الخادم لا يرى أي حزم من الراوتر | لا | Check VPS firewall: `iptables -nvL INPUT \| grep 51820` |
| `vpn_no_handshake_server_side` | الراوتر يقول "متصل" لكن الخادم لا يرى التصافح | جزئي | Mismatch between router-reported state and server reality. Push peer to wg0 |
| `wg_persistent_keepalive_too_low` | keepalive < 10s يستنزف بطارية الراوتر بدون فائدة | نعم | Set to 25s |

### RADIUS category

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `radius_no_response` | لا يرد خادم RADIUS على اختبار auth | جزئي | NAS not in clients.conf → `nas_not_registered_in_clients_conf` |
| `nas_not_registered_in_clients_conf` | الراوتر غير مسجّل كـ NAS في FreeRADIUS | نعم | Write entry to `/etc/freeradius/3.0/clients.conf`, `systemctl reload freeradius` |
| `radius_secret_mismatch` | المفتاح المشترك مختلف بين الراوتر والخادم | نعم | Re-sync secret from wizard run |
| `radius_packet_invalid_message_authenticator` | الراوتر يرسل بدون Message-Authenticator | نعم | Toggle `require-message-auth=no` on router OR set `require_message_authenticator = yes` on server side |
| `radius_called_id_filter_block` | فلتر called-id يمنع المرور | لا | Manual review |
| `radius_unreachable_via_vpn` | الخادم على 10.10.0.1 لا يستجيب من VPN | جزئي | Run VPN diagnostics first |

### NAS registry category

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `nas_register_failed_db` | تعذّر إضافة الراوتر إلى جدول nas_devices | لا | DB error — check logs |
| `nas_register_freeradius_reload_failed` | تم إضافة الإدخال لكن إعادة تحميل FreeRADIUS فشلت | نعم | `sudo systemctl reload freeradius` ; check `journalctl -u freeradius -n 50` |
| `nas_already_registered_different_router` | عنوان VPN هذا مسجّل لراوتر آخر | لا | IP collision; choose another |
| `nas_connection_mode_vpn_but_no_peer_ip` | NAS وضع VPN لكن لا يوجد vpn_peer_address | نعم | Backfill from `prepared_wireguard_peers` |

### Ops-room category

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `ops_room_router_not_found` | الراوتر مسجّل لكن لا يظهر في غرفة العمليات | نعم | Insert into `mt_operations_routers` |
| `ops_room_api_unreachable` | غرفة العمليات لا تستطيع الوصول للراوتر | جزئي | Depends on connection_mode; if vpn → check wg handshake |
| `ops_room_orphan_record` | إدخال يتيم في mt_operations بدون nas_devices مقابل | نعم | Cleanup orphan |

### Internet (WAN) category

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `wan_dhcp_no_lease` | لا يوجد عنوان من DHCP على واجهة WAN | لا | Check cable, modem, ISP |
| `wan_pppoe_auth_failed` | فشل توثيق PPPoE | لا | Wrong username/password |
| `wan_pppoe_no_service` | لا يوجد خادم PPPoE يرد | لا | ISP-side outage |
| `wan_static_no_gateway` | الإعدادات الثابتة لكن البوابة غير قابلة للوصول | لا | Wrong static config |
| `wan_dns_unreachable` | الـ WAN يعمل لكن DNS لا يستجيب | نعم | Suggest 1.1.1.1, 8.8.8.8 |

### Service (Hotspot/PPPoE) category

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `hotspot_profile_missing` | بروفايل Hotspot غير موجود | نعم | Recreate from template |
| `hotspot_interface_already_bound` | الواجهة مستخدمة لخدمة أخرى | لا | Pick different interface |
| `pppoe_pool_exhausted` | استنفد pool العملاء | لا | Expand pool |

### Wizard internals

| Code | Symptom (AR) | Auto-fix? | Notes |
|---|---|---|---|
| `wizard_state_inconsistent` | حالة الجلسة لا تتطابق مع قاعدة البيانات | نعم | Reset run state from snapshot |
| `wizard_script_signature_changed` | تم تعديل السكربت يدوياً بعد التوليد | لا | Regenerate to get new signature |
| `wizard_run_not_found` | معرّف الجلسة منتهي الصلاحية | لا | Start fresh run |

---

## How v3 components consume this

1. **Auto-verification worker** (`setup_wizard_v3_auto_verify.py`):
   probes VPS + router + RADIUS in parallel, emits `[diagnostic, ...]`
   list. Each diagnostic links to a row above.

2. **Frontend** (`setup_wizard_v3.js`):
   for any non-empty diagnostics list, renders a card per diagnostic with
   `symptom_ar` as title, expandable "أسباب محتملة" list, and a big
   `[ تشغيل الإصلاح التلقائي ]` button if `auto_fix_available=true`.

3. **Support bundle** (`/setup-wizard/runs/{id}/support-bundle`):
   serializes the diagnostic list as JSON + attaches recent `wg show`,
   `freeradius -X` snippets so support can triage offline.

4. **Recovery state machine** (`setup_wizard_recovery_v3.py`):
   each diagnostic has a `next_check_after_fix` pointer that drives
   automatic re-verification once the fix runs.

---

## Adding a new diagnostic code

1. Append to this catalog (keep alphabetical within category).
2. Add Arabic + English copy to
   `radius-module/app/radius/i18n/wizard_diagnostics.json`.
3. Add probe in `setup_wizard_v3_auto_verify.py` that can emit it.
4. If `auto_fix_available=true`: add handler in
   `setup_wizard_v3_auto_fix.py`.
5. Write test:
   `tests/test_setup_wizard_v3_diagnostics.py::test_emits_<code>`.
