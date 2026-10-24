#!/bin/bash
# Move the Windows guest to the new server, unattended.
#
# 🔑 This script runs ON THE OLD SERVER, started with nohup, because the
# first thing it does is shut down the very machine the operator was working
# from. Nothing here may depend on that machine, or on anyone watching.
#
# The copy is streamed straight into the new host's restore - there is no
# room for a 200GB backup file on this server's 50GB system disk, and a
# stream needs none.
#
# Nothing is deleted here. The old guest stays exactly where it is, shut
# down, until the owner has seen the new one work.
set -uo pipefail
VMID=100
NEW=144.76.201.108
KEY=/root/.ssh/hr-winhost
LOG=/root/win-migrate.log
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -i $KEY root@$NEW"

say() { echo "$(date -u +%H:%M:%S) $*" >> "$LOG"; }
fail() {
  say "FAILED: $*"
  # Never leave the owner without a working desktop. If anything goes wrong
  # after the shutdown, the original guest - untouched - comes straight back.
  qm status $VMID 2>/dev/null | grep -q stopped && qm start $VMID >>"$LOG" 2>&1 && \
    say "the original guest has been started again on this server"
  echo "FAILED" > /root/win-migrate.state
  exit 1
}

echo "RUNNING" > /root/win-migrate.state
: > "$LOG"
say "preflight"
$SSH true || fail "cannot reach the new server"
$SSH "qm status $VMID >/dev/null 2>&1" && fail "VM $VMID already exists on the new server"
SRC_GB=$(lvs --noheadings --units g -o lv_size vg0/vm-$VMID-disk-1 | tr -dc '0-9.')
say "source disk ${SRC_GB}G"

say "shutting the guest down (up to 10 minutes)"
qm shutdown $VMID --timeout 600 >>"$LOG" 2>&1
for i in $(seq 1 60); do
  qm status $VMID | grep -q stopped && break
  sleep 10
done
qm status $VMID | grep -q stopped || { qm stop $VMID >>"$LOG" 2>&1; sleep 10; }
qm status $VMID | grep -q stopped || fail "guest did not stop"
say "guest stopped"

say "streaming to the new server - this is the long part"
vzdump $VMID --stdout --compress zstd --mode stop 2>>"$LOG" \
  | $SSH "qmrestore - $VMID --storage local-lvm" >>"$LOG" 2>&1
RC=${PIPESTATUS[0]}:${PIPESTATUS[1]}
say "transfer finished rc=$RC"
$SSH "qm config $VMID >/dev/null 2>&1" || fail "the new server has no VM $VMID - transfer did not land"

say "shaping the new guest"
$SSH "qm set $VMID --memory 32768 --balloon 0 --cores 8 --onboot 1 --agent 1" >>"$LOG" 2>&1
# its own address comes with it, so the new host carries the same subnet
$SSH "qm set $VMID --net0 virtio,bridge=vmbr1" >>"$LOG" 2>&1
say "work disks"
$SSH "qm set $VMID --scsi1 local-lvm:400,discard=on,iothread=1,ssd=1" >>"$LOG" 2>&1
$SSH "qm set $VMID --scsi2 local-lvm:300,discard=on,iothread=1,ssd=1" >>"$LOG" 2>&1
say "starting"
$SSH "qm start $VMID" >>"$LOG" 2>&1
sleep 90
$SSH "qm status $VMID" >>"$LOG" 2>&1

say "DONE - the old guest is still here, shut down, untouched"
echo "DONE" > /root/win-migrate.state
