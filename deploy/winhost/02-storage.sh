#!/bin/bash
# VM storage: a thin pool on the mirror, plus the autoextend guard the other
# host never had - a thin pool that fills silently corrupts every guest on it.
set -euo pipefail
FREE=$(vgs --noheadings --units g -o vg_free vg0 | tr -dc '0-9.')
POOL=$(awk -v f="$FREE" 'BEGIN{printf "%d", f*0.97}')
lvs vg0/data >/dev/null 2>&1 || lvcreate --type thin-pool -L "${POOL}G" -n data vg0
sed -i 's/^\(\s*\)#\?\s*thin_pool_autoextend_threshold = .*/\1thin_pool_autoextend_threshold = 80/' /etc/lvm/lvm.conf
sed -i 's/^\(\s*\)#\?\s*thin_pool_autoextend_percent = .*/\1thin_pool_autoextend_percent = 10/' /etc/lvm/lvm.conf
pvesm status | grep -q '^local-lvm' || \
  pvesm add lvmthin local-lvm --vgname vg0 --thinpool data --content images,rootdir
pvesm set local --content iso,vztmpl,backup,snippets
lvs
pvesm status
