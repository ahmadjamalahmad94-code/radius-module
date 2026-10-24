#!/bin/bash
# Recover the failed migration and start it again. Runs ON THE OLD SERVER.
#
# The first attempt moved 25MB and stalled: qmrestore reads an archive's
# compression from its file name, a pipe has none, so it fed the zstd stream
# straight to vma extract. It died on the header and left a config behind -
# which the migration script read as success.
#
#   prep  clears that wreck, patches the script, and proves the fix on the
#         small CHR template first. Touches nothing the Windows guest needs.
#   go    runs the real migration. This stops the trading bots.
set -uo pipefail
NEW=144.76.201.108
KEY=/root/.ssh/hr-winhost
SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -i $KEY root@$NEW"
VMID=100
TESTID=999
TESTSRC=101
SCRIPT=/root/win-migrate.sh

die() { echo; echo "STOP: $*"; exit 1; }
ok()  { echo "  ok - $*"; }

[ -r "$KEY" ]                  || die "no key at $KEY - run this on the OLD server"
qm config $VMID >/dev/null 2>&1 || die "no VM $VMID here - run this on the OLD server"
[ -r "$SCRIPT" ]               || die "no $SCRIPT here"

case "${1:-}" in
prep)
  echo "1/4  the new server"
  $SSH true || die "cannot reach $NEW"
  ok "reachable"

  echo "2/4  the half-built guest left on the new server"
  if $SSH "qm status $VMID >/dev/null 2>&1"; then
    # Never delete a guest that holds a system disk, whatever we expect to find.
    if $SSH "qm config $VMID 2>/dev/null | grep -q '^scsi0:'"; then
      die "VM $VMID on $NEW HAS a system disk - leaving it alone, go and look at it"
    fi
    echo "  no system disk in it - removing"
    $SSH "qm stop $VMID >/dev/null 2>&1; sleep 3; qm destroy $VMID --purge" \
      || die "could not remove VM $VMID from $NEW"
    ok "removed"
  else
    ok "nothing there"
  fi

  echo "3/4  patching $SCRIPT"
  cp -n "$SCRIPT" "$SCRIPT.orig" 2>/dev/null
  grep -q 'zstd -d | qmrestore' "$SCRIPT" \
    || sed -i 's#"qmrestore - #"zstd -d | qmrestore - #' "$SCRIPT"
  grep -q 'transfer failed rc=' "$SCRIPT" \
    || sed -i '/transfer finished rc=/a [ "$RC" = "0:0" ] || fail "transfer failed rc=$RC"' "$SCRIPT"
  grep -q 'zstd -d | qmrestore' "$SCRIPT" || die "the patch did not apply - send me: grep -n qmrestore $SCRIPT"
  ok "patched (original kept at $SCRIPT.orig)"

  echo "4/4  proving it on VM $TESTSRC - small, about a minute"
  $SSH "qm destroy $TESTID --purge >/dev/null 2>&1"
  vzdump $TESTSRC --stdout --compress zstd --mode stop 2>/tmp/hr-test.err \
    | $SSH "zstd -d | qmrestore - $TESTID --storage local-lvm" >/tmp/hr-test.out 2>&1
  RC="${PIPESTATUS[0]}:${PIPESTATUS[1]}"
  LANDED=no
  $SSH "qm config $TESTID 2>/dev/null | grep -qE '^(scsi|virtio|sata|ide)[0-9]+:'" && LANDED=yes
  $SSH "qm destroy $TESTID --purge >/dev/null 2>&1"
  if [ "$RC" = "0:0" ] && [ "$LANDED" = yes ]; then
    ok "the test guest landed with its disk - the fix holds"
    echo
    echo "READY.  Next:   bash $0 go"
  else
    echo "  rc=$RC  disk-landed=$LANDED"
    tail -n 6 /tmp/hr-test.out /tmp/hr-test.err 2>/dev/null
    die "the test did not land - send me the lines above and do NOT run 'go'"
  fi
  ;;

go)
  grep -q 'zstd -d | qmrestore' "$SCRIPT" || die "run 'bash $0 prep' first"
  $SSH "qm status $VMID >/dev/null 2>&1" && die "VM $VMID still exists on $NEW - run 'bash $0 prep' first"
  echo "This shuts VM $VMID down. The trading bots stop until it runs again."
  echo "Starting in 15 seconds - Ctrl+C to abort."
  sleep 15
  QM_ALLOW_100=1 nohup bash "$SCRIPT" </dev/null >/dev/null 2>&1 &
  echo
  echo "started, pid $!"
  echo "watch:   tail -f /root/win-migrate.log"
  echo "verdict: cat /root/win-migrate.state"
  ;;

*)
  echo "usage:  bash $0 prep     # clean up, patch, prove the fix"
  echo "        bash $0 go       # run the migration"
  ;;
esac
