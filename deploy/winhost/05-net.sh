#!/bin/bash
# One public IPv4 is all this server has, so the guests live on a private
# bridge and reach the internet through the host. Services are published by
# forwarding a port, which also keeps every guest off the open internet
# unless we deliberately open a door for it.
set -euo pipefail
BR=vmbr1
NET=10.20.0
grep -q "iface $BR" /etc/network/interfaces || cat >> /etc/network/interfaces <<EOF

auto $BR
iface $BR inet static
	address $NET.1/24
	bridge-ports none
	bridge-stp off
	bridge-fd 0
	post-up   echo 1 > /proc/sys/net/ipv4/ip_forward
	post-up   iptables -t nat -A POSTROUTING -s $NET.0/24 -o enp4s0 -j MASQUERADE
	post-down iptables -t nat -D POSTROUTING -s $NET.0/24 -o enp4s0 -j MASQUERADE
EOF
ifquery --state $BR >/dev/null 2>&1 || ifup $BR
ip -4 -br a show $BR
sysctl -w net.ipv4.ip_forward=1 >/dev/null
grep -q "^net.ipv4.ip_forward" /etc/sysctl.conf || echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
