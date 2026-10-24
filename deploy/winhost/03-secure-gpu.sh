#!/bin/bash
# Hardening, then the two kernel settings GPU passthrough needs.
set -euo pipefail

# ---------------------------------------------------------------- SSH
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?KbdInteractiveAuthentication.*/KbdInteractiveAuthentication no/' /etc/ssh/sshd_config
sshd -t && systemctl reload ssh

# ---------------------------------------------------------- fail2ban
export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q fail2ban >/dev/null
cat > /etc/fail2ban/jail.d/hobe.conf <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled = true

[proxmox]
enabled  = true
port     = https,http,8006
filter   = proxmox
logpath  = /var/log/daemon.log
EOF
cat > /etc/fail2ban/filter.d/proxmox.conf <<'EOF'
[Definition]
failregex = pvedaemon\[.*authentication (verification )?failure; rhost=<HOST> user=.* msg=.*
ignoreregex =
journalmatch = _SYSTEMD_UNIT=pvedaemon.service
EOF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

# ------------------------------------------------------- GPU passthrough
# The board has an Intel iGPU, so the host keeps a console and the RTX is
# free to be claimed by vfio from boot - no fighting the nvidia driver.
if ! grep -q "intel_iommu=on" /etc/default/grub; then
  sed -i 's/^GRUB_CMDLINE_LINUX_DEFAULT="\(.*\)"/GRUB_CMDLINE_LINUX_DEFAULT="\1 intel_iommu=on iommu=pt"/' /etc/default/grub
  update-grub
fi
for m in vfio vfio_iommu_type1 vfio_pci; do grep -qx "$m" /etc/modules || echo "$m" >> /etc/modules; done
GPU=$(lspci -nn | grep -i 'NVIDIA' | grep -iE 'VGA|Audio' | grep -oE '\[10de:[0-9a-f]{4}\]' | tr -d '[]' | paste -sd, -)
echo "options vfio-pci ids=$GPU disable_vga=1" > /etc/modprobe.d/vfio.conf
cat > /etc/modprobe.d/blacklist-nvidia.conf <<'EOF'
blacklist nouveau
blacklist nvidia
blacklist nvidiafb
blacklist snd_hda_intel
EOF
update-initramfs -u -k all >/dev/null 2>&1
echo "vfio ids: $GPU"
fail2ban-client status | head -3
