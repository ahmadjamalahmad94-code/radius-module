# Router 14 — WireGuard ping diagnosis (10.10.0.1 100% loss)

**Date:** 2026-05-26
**Symptom from session:**

```
/tool ping 10.10.0.1 src-address=10.10.0.15 count=5
→ 100% packet loss, all timeouts

/interface wireguard peers print detail
→ peer1 rx=0 tx=148   (no handshake EVER completed)
```

The router has sent 148 bytes of handshake initiation but received zero
bytes back. The VPS either (a) doesn't have the router's public key
registered, (b) has it bound to a different `allowed-ips`, or (c) is
not receiving the UDP packets at all (firewall / wrong port).

---

## Quick verification ladder (run on VPS — under 60 seconds total)

Run these in order. Stop at the first one that flags red.

### 1. Is the router's public key registered on wg0?

```bash
sudo wg show wg0 | grep -A2 'o5ZzsCcC7iW5OzxLM//V5nUfEe6lLw613PQbdbNn1Gk='
```

**Expected:** a `peer:` block with `allowed ips: 10.10.0.15/32` and a recent
`latest handshake`.

**If empty / not found:** the wizard never pushed peer 14 to wg0 → jump to
*Section A — wizard did not apply peer*.

**If shown but `latest handshake: 0` or never:** keys match but handshake
isn't reaching → jump to *Section B — handshake not reaching VPS*.

### 2. Is the public key on wg0 the one the router is dialling?

The router is dialling endpoint `187.77.70.18:51820` and signing with VPS
peer pubkey `FCCFdzxg2qicb1yx8ALX9vGFmYe/Nur1USL68zOTY30=` (from
the router output). Compare with VPS server pubkey:

```bash
sudo wg show wg0 public-key
```

**Expected:** `FCCFdzxg2qicb1yx8ALX9vGFmYe/Nur1USL68zOTY30=`

**If different:** the wizard's RouterOS script embedded a stale server pubkey.
Jump to *Section C — server pubkey mismatch*.

### 3. Is the kernel actually receiving the handshake packets?

```bash
sudo tcpdump -ni any 'udp port 51820' -c 5
```

Now trigger a handshake from the router (in MikroTik terminal):
`/interface wireguard peers set [find where interface=hr-wg] persistent-keepalive=10s`

**If tcpdump shows zero packets from 187.77.70.18:** the router's traffic
isn't reaching VPS. Could be ISP-side issue or wrong endpoint. Jump to
*Section D*.

**If packets arrive but no response:** the VPS WG kernel module is dropping
them (key mismatch). Re-run Section 1 and Section 2.

### 4. Is UFW / iptables blocking inbound UDP/51820?

```bash
sudo iptables -nvL INPUT | grep 51820
sudo ufw status verbose 2>/dev/null | grep 51820
```

**Expected:** `ACCEPT` rule on UDP/51820, or `ALLOW IN`.

---

## Section A — wizard did not apply peer to wg0 (most common)

**Why this happens:** the v2 wizard's `ServerWireGuardPeerApplyService.apply()`
is gated by four environment flags
([setup_wizard_server_wg.py:80-86](../../app/radius/services/setup_wizard_server_wg.py)):

```
HOBERADIUS_SETUP_WIZARD_LAB_MODE=1
HOBERADIUS_SETUP_WIZARD_SERVER_WG_APPLY=1
HOBERADIUS_SETUP_WIZARD_SERVER_WG_READINESS=1
HOBERADIUS_SETUP_WIZARD_SERVER_WG_REAL_ADAPTER=1
```

If any one is unset, `apply()` returns
`{"status": "blocked", "code": "server_wg_real_apply_flags_disabled"}` —
and the wizard happily proceeds past the gate because the operator was
expected to apply the peer manually.

**Verify on VPS:**

```bash
systemctl show hoberadius.service | grep -E 'Environment=.*HOBERADIUS_SETUP_WIZARD'
# or:
docker exec hoberadius env | grep HOBERADIUS_SETUP_WIZARD
```

**Manual fix — add router 14 to wg0 immediately:**

```bash
# Option 1: persistent (recommended) — drop a peer fragment
sudo mkdir -p /etc/hoberadius/wg-peers.d
sudo tee /etc/hoberadius/wg-peers.d/router-14.conf >/dev/null <<'EOF'
# HOBERADIUS_ROUTER:14 HOBERADIUS_SETUP:16:server-peer
PublicKey = o5ZzsCcC7iW5OzxLM//V5nUfEe6lLw613PQbdbNn1Gk=
AllowedIPs = 10.10.0.15/32
PersistentKeepalive = 25
EOF
# wg-reload.path will fire wg-reload.service which runs `wg set`
```

```bash
# Option 2: one-shot (won't survive reboot unless wg-quick re-reads)
sudo wg set wg0 peer o5ZzsCcC7iW5OzxLM//V5nUfEe6lLw613PQbdbNn1Gk= \
    allowed-ips 10.10.0.15/32 \
    persistent-keepalive 25
```

**Then trigger handshake from router (MikroTik terminal):**

```routeros
/interface wireguard peers set [find where interface=hr-wg] persistent-keepalive=10s
:delay 5s
/tool ping 10.10.0.1 src-address=10.10.0.15 count=3
```

---

## Section B — handshake not reaching VPS (key matches but no answer)

Run on VPS:

```bash
sudo journalctl -u wg-quick@wg0 -n 50 --no-pager
sudo dmesg | grep -i wireguard | tail -20
```

Look for "Invalid handshake initiation" or
"Packet has invalid mac1" — both mean the peer is dialling with the wrong
server pubkey. Fix by regenerating the router script via the wizard
(which embeds the live VPS pubkey).

---

## Section C — server pubkey mismatch

The router's `peer1` is configured with `public-key=FCCF...30=` as the
*server* it dials. If `sudo wg show wg0 public-key` returns a *different*
key, the router was given a stale value at script-generation time.

**Fix:** regenerate the wizard script and re-run on router. The pubkey
that lands in the script comes from
`ServerWireGuardPeerPlanner.plan()` → `config_preview` which reads from
`router_provisioning_registry.server_vpn_ip` plus the live wg0 pubkey.

If wg0 has been rebuilt since router 14's script was generated, the keys
won't match. Confirm: compare router script's embedded VPS pubkey against
`sudo wg show wg0 public-key`.

---

## Section D — packets not reaching VPS at all

Most likely:
- ISP blocks outbound UDP on port 51820 for the router's WAN
- Router NAT'd behind CGNAT and the endpoint address used (187.77.70.18)
  is a stale value
- VPS provider's external firewall blocks UDP/51820 inbound

**Quick test from another network with `wg`:**

```bash
echo -n | nc -u -w 2 187.77.70.18 51820  # any output means listening
```

If router behind CGNAT, you need a different transport — wireguard-go
over TCP, or move to a different protocol.

---

## Why the wizard did not catch this

`SetupWizardVerificationEngine` runs *only* when the operator clicks
"verify VPN" and pastes router output. The current paste-back check
([setup_wizard_v2.js:friendlyWizardError](../../app/static/js/setup_wizard_v2.js))
maps backend codes to Arabic strings, but it never reaches into VPS-side
`wg show` to confirm the peer is actually bound. The check reads only the
router's side. Router says "I added peer, sent 148 bytes" → wizard sees
"peer present locally" → marks step green.

The v3 wizard MUST run a server-side `wg show wg0` check before
declaring VPN healthy. This is captured as diagnostic code
`vpn_no_handshake_server_side` in the v3 catalog
([WIZARD_DIAGNOSTICS.md](WIZARD_DIAGNOSTICS.md)).

---

## After fix — verify on router

```routeros
/interface wireguard peers print where interface=hr-wg
# expect: rx > 0, latest-handshake within last 30s

/tool ping 10.10.0.1 src-address=10.10.0.15 count=5
# expect: 0% loss
```

Then on VPS:

```bash
sudo wg show wg0 | grep -A4 'peer: o5ZzsCcC'
# expect: latest handshake: a few seconds ago
#         transfer: X received, Y sent  (both > 0)
```

If both green: VPN is up. Next bottleneck for the wizard's RADIUS step
is `radtest hr-api-0014 ... 10.10.0.1 0 <secret>` — that needs the
router added as a NAS in `clients.conf` (separate flow today — see
[SETUP_WIZARD_V3_DESIGN.md](SETUP_WIZARD_V3_DESIGN.md) section
"Auto NAS registration").
