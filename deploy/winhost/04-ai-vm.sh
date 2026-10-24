#!/bin/bash
# The GPU guest. Ubuntu 24.04 cloud image, the RTX passed straight through.
set -euo pipefail
VMID=200
NAME=ai-gpu
IMG=/var/lib/vz/template/iso/noble-server-cloudimg-amd64.img

install -d /var/lib/vz/template/iso /var/lib/vz/snippets
[ -f "$IMG" ] || curl -fsSL -o "$IMG" \
  https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img

if ! qm status $VMID >/dev/null 2>&1; then
  qm create $VMID --name $NAME --memory 32768 --balloon 0 --cores 10 \
    --cpu host --machine q35 --bios ovmf --ostype l26 \
    --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single --agent 1
  qm set $VMID --efidisk0 local-lvm:1,efitype=4m,pre-enrolled-keys=0
  qm importdisk $VMID "$IMG" local-lvm >/dev/null
  qm set $VMID --scsi0 local-lvm:vm-$VMID-disk-1,discard=on,iothread=1,ssd=1
  qm disk resize $VMID scsi0 300G
  qm set $VMID --boot order=scsi0 --serial0 socket --vga serial0
  # the card and its audio function travel together, as one IOMMU group
  qm set $VMID --hostpci0 0000:01:00,pcie=1
  qm set $VMID --onboot 1
fi
qm config $VMID | grep -E '^(name|memory|cores|hostpci0|scsi0|net0|onboot)'
