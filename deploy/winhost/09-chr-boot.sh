#!/bin/bash
# Start each router and give it its address over the serial console - a CHR
# boots with DHCP only, and there is no DHCP server on this bridge.
set -euo pipefail
BASE=300
for n in 1 2 3 4; do
  VMID=$((BASE + n))
  IP=10.20.0.$((20 + n))
  qm status $VMID | grep -q running || qm start $VMID
done
sleep 60
for n in 1 2 3 4; do
  VMID=$((BASE + n))
  IP=10.20.0.$((20 + n))
  qm terminal $VMID >/dev/null 2>&1 &
  python3 - "$VMID" "$IP" <<'PY'
import socket, sys, time
vmid, ip = sys.argv[1], sys.argv[2]
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(f"/var/run/qemu-server/{vmid}.serial0")
s.settimeout(3)

def send(line):
    s.sendall((line + "\r\n").encode())
    time.sleep(1.2)
    try:
        return s.recv(65536).decode(errors="ignore")
    except socket.timeout:
        return ""

send("")
out = send("")
# first boot asks for a login; admin has no password until we set one
if "ogin" in out:
    send("admin")
    send("")
    send("")
    send("n")   # skip the licence prompt if it appears
send(f"/ip address add address={ip}/24 interface=ether1")
send("/ip route add gateway=10.20.0.1")
send("/ip dns set servers=1.1.1.1,8.8.8.8")
send("/ip service disable telnet,ftp,www,api")
send("/ip service set ssh port=22")
print(vmid, ip, "configured")
s.close()
PY
done
sleep 5
for n in 1 2 3 4; do
  IP=10.20.0.$((20 + n))
  printf "%s " "$IP"; ping -c 2 -W 2 "$IP" >/dev/null 2>&1 && echo OK || echo NO
done
