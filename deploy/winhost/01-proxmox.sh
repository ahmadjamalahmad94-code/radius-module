#!/bin/bash
# Proxmox VE 8 on the installed Debian 12, matching the fleet's version.
set -euo pipefail
IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -1)
FQDN=hobe-winhost.hoberadius.local
HOST=hobe-winhost

hostnamectl set-hostname "$HOST"
# Proxmox resolves its own name to a real address at install time; the
# loopback line Debian ships would make it bind 127.0.1.1 and refuse.
sed -i "/$HOST/d" /etc/hosts
grep -q "^$IP" /etc/hosts || echo "$IP $FQDN $HOST" >> /etc/hosts

install -d /etc/apt/keyrings
curl -fsSL -o /etc/apt/keyrings/proxmox-release-bookworm.gpg \
  https://enterprise.proxmox.com/debian/proxmox-release-bookworm.gpg
echo "deb [signed-by=/etc/apt/keyrings/proxmox-release-bookworm.gpg] \
http://download.proxmox.com/debian/pve bookworm pve-no-subscription" \
  > /etc/apt/sources.list.d/pve-no-subscription.list
rm -f /etc/apt/sources.list.d/pve-enterprise.list

export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q postfix open-iscsi chrony
apt-get install -y -q proxmox-default-kernel
echo "kernel installed; reboot needed before proxmox-ve"
