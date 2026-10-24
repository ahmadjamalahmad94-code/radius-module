#!/bin/bash
# Four CHR routers for the VPN service. Each gets its own private address
# and its own forwarded management port; the VPN ports themselves are opened
# per router once we know which protocols the service sells.
set -euo pipefail
VER=${CHR_VER:-7.19.6}
IMG=/var/lib/vz/template/iso/chr-$VER.img
BASE=${BASE:-300}

if [ ! -f "$IMG" ]; then
  curl -fsSL -o /tmp/chr.zip "https://download.mikrotik.com/routeros/$VER/chr-$VER.img.zip"
  unzip -o -q /tmp/chr.zip -d /tmp
  mv "/tmp/chr-$VER.img" "$IMG"
  rm -f /tmp/chr.zip
fi

for n in 1 2 3 4; do
  VMID=$((BASE + n))
  IP=10.20.0.$((20 + n))
  qm status $VMID >/dev/null 2>&1 && { echo "$VMID exists"; continue; }
  qm create $VMID --name "chr-vpn-$n" --memory 1536 --cores 2 --cpu host \
    --ostype l26 --net0 virtio,bridge=vmbr1 --scsihw virtio-scsi-single \
    --serial0 socket --vga serial0 --onboot 1
  qm importdisk $VMID "$IMG" local-lvm >/dev/null
  qm set $VMID --scsi0 local-lvm:vm-$VMID-disk-0,discard=on,ssd=1 >/dev/null
  # 64MB is what the image ships with, and a disk that small cannot hold a
  # RouterOS 7 upgrade - a paid lesson from the fleet.
  qm disk resize $VMID scsi0 4G >/dev/null
  qm set $VMID --boot order=scsi0 >/dev/null
  qm set $VMID --description "VPN service router $n · $IP · mgmt port $((2200 + n))" >/dev/null
  echo "created $VMID chr-vpn-$n -> $IP"
done
qm list | grep -E "chr-vpn|VMID"
