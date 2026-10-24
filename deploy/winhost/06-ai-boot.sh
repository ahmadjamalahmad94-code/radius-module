#!/bin/bash
# cloud-init for the GPU guest, then first boot.
set -euo pipefail
VMID=200
IP=10.20.0.10
KEY=/root/.ssh/authorized_keys

qm set $VMID --ide2 local-lvm:cloudinit
qm set $VMID --ipconfig0 "ip=$IP/24,gw=10.20.0.1"
qm set $VMID --nameserver "1.1.1.1 8.8.8.8"
qm set $VMID --ciuser hobe
qm set $VMID --sshkeys "$KEY"
qm set $VMID --cicustom ""
qm start $VMID
echo "started; waiting for ssh"
for i in $(seq 1 40); do
  if ssh -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
         -o UserKnownHostsFile=/dev/null hobe@$IP true 2>/dev/null; then
    echo "AI VM UP"; break
  fi
  sleep 10
done
ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    hobe@$IP "lspci -nn | grep -i nvidia; nproc; free -g | head -2" 2>/dev/null
