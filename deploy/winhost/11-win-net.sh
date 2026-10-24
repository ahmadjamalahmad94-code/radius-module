#!/bin/bash
# The Windows guest carries its own static address (10.10.10.20) inside the
# image, so the new host offers it the same subnet rather than asking the
# operator to re-address a machine full of working software. Both subnets
# live on the one guest bridge.
set -euo pipefail
grep -q "10.10.10.1/24" /etc/network/interfaces || sed -i \
  '/^\tpost-up   echo 1 > \/proc\/sys\/net\/ipv4\/ip_forward$/a\\tpost-up   ip addr add 10.10.10.1/24 dev vmbr1\n\tpost-up   iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o enp4s0 -j MASQUERADE\n\tpost-up   iptables -t nat -A PREROUTING -i enp4s0 -p tcp --dport 3391 -j DNAT --to-destination 10.10.10.20:3389' \
  /etc/network/interfaces
ip addr show vmbr1 | grep -q "10.10.10.1/24" || ip addr add 10.10.10.1/24 dev vmbr1
iptables -t nat -C POSTROUTING -s 10.10.10.0/24 -o enp4s0 -j MASQUERADE 2>/dev/null || \
  iptables -t nat -A POSTROUTING -s 10.10.10.0/24 -o enp4s0 -j MASQUERADE
iptables -t nat -C PREROUTING -i enp4s0 -p tcp --dport 3391 -j DNAT --to-destination 10.10.10.20:3389 2>/dev/null || \
  iptables -t nat -A PREROUTING -i enp4s0 -p tcp --dport 3391 -j DNAT --to-destination 10.10.10.20:3389
ip -4 -br a show vmbr1
iptables -t nat -S | grep -E "3391|10.10.10"
