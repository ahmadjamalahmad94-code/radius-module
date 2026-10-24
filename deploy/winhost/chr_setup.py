"""Drive a fresh CHR through its first-boot prompts over the serial socket.

RouterOS 7.19 refuses to hand over a shell until the blank admin password is
replaced, and it asks twice.

🔴 The trap that cost a rebuild: a bare newline sent while a password prompt
is waiting counts as an empty password, and RouterOS answers "New password is
the same as old one" and starts over - forever, if the script keeps nudging.
So this version never sends anything unprompted. It waits, reads, and answers
only the prompt it can actually see.
"""
import re
import socket
import sys
import time

VMID, IP, PW = sys.argv[1], sys.argv[2], sys.argv[3]
SOCK = f"/var/run/qemu-server/{VMID}.serial0"

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(SOCK)
s.settimeout(1.0)


def read(seconds=2.0):
    end, out = time.time() + seconds, ""
    while time.time() < end:
        try:
            out += s.recv(65536).decode(errors="ignore")
        except socket.timeout:
            pass
    return out


def send(line):
    """Type like a person.

    🔴 The console drops the first characters typed into a prompt it has only
    just printed. The repeat password came out three characters short of the
    first one, every time, and RouterOS called that a mismatch.
    """
    time.sleep(0.5)
    for ch in line:
        s.sendall(ch.encode())
        time.sleep(0.04)
    s.sendall(b"\r")


buf = ""
state = "start"
done = {"newpw": False, "repeat": False}
deadline = time.time() + 120
# The prompt was printed before we attached, so nothing arrives until the
# console is touched. Ctrl-C is the one safe nudge: at a password prompt it
# cancels and reprints, where a newline would have been submitted as an empty
# password - the loop that cost the first two attempts.
s.sendall(b"\x03")
while time.time() < deadline:
    buf += read(1.5)
    tail = buf[-300:].lower()
    if "[admin@" in buf or re.search(r"\]\s*>\s*$", buf.strip()):
        state = "shell"
        break
    if "repeat new password" in tail and not done["repeat"]:
        send(PW); done["repeat"] = True; buf = ""; continue
    if "new password" in tail and not done["newpw"]:
        send(PW); done["newpw"] = True; buf = ""; continue
    if "software license" in tail or "[y/n]" in tail:
        send("n"); buf = ""; continue
    if re.search(r"login\s*:", tail):
        send("admin"); buf = ""; continue
    if re.search(r"password\s*:", tail) and "new" not in tail:
        send(""); buf = ""; continue
    # after both password answers RouterOS prints the banner and the prompt;
    # give it room rather than typing into it
    if done["repeat"]:
        time.sleep(2)

if state != "shell":
    print(f"{VMID} FAILED tail={buf[-200:]!r}")
    sys.exit(1)

for cmd in [
    f"/ip address add address={IP}/24 interface=ether1",
    "/ip route add gateway=10.20.0.1",
    "/ip dns set servers=1.1.1.1,8.8.8.8",
    "/ip service disable telnet,ftp,www,api,api-ssl",
    f"/system identity set name=chr-vpn-{int(VMID) - 300}",
]:
    send(cmd)
    time.sleep(0.8)
read(2)
print(f"{VMID} OK {IP}")
