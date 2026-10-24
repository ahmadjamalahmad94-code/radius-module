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
# A failed attempt leaves its half-built guest behind on purpose, for reading.
# It also blocks the next attempt, so it has to be removed there by hand -
# which is the point: nobody deletes a guest without having looked at it first.
$SSH "qm status $VMID >/dev/null 2>&1" && \
  fail "VM $VMID already exists on the new server - read it there, then 'qm stop $VMID; qm destroy $VMID --purge' on the new server before retrying"
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
# The far side unpacks the stream itself. qmrestore reads the compression from
# the archive's file name, and a pipe has none - handed a zstd stream it feeds
# the compressed bytes straight to vma extract, which dies on the first header
# while the sender keeps pushing into a pipe nobody drains. Decompressing there
# rather than here keeps the wire small and gives qmrestore the plain VMA.
vzdump $VMID --stdout --compress zstd --mode stop 2>>"$LOG" \
  | $SSH "zstd -d | qmrestore - $VMID --storage local-lvm" >>"$LOG" 2>&1
RC=${PIPESTATUS[0]}:${PIPESTATUS[1]}
say "transfer finished rc=$RC"
[ "$RC" = "0:0" ] || fail "transfer failed rc=$RC - nothing on the new server can be trusted, see the log"
# A restore that dies partway says so and leaves the config behind - "VM state
# is NOT cleaned up" - so a config that answers proves only that something was
# started, never that it finished. The system disk is the only evidence.
$SSH "qm config $VMID 2>/dev/null | grep -q '^scsi0:'" \
  || fail "VM $VMID on the new server has no system disk - the transfer did not land"

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
