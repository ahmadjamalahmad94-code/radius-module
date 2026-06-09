# -*- coding: utf-8 -*-
"""Reset admin password to 'admin' directly in instance/hoberadius.db."""
import hashlib
import os
import secrets
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "instance", "hoberadius.db")

def hash_password(plain: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(plain.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return salt.hex() + "$" + key.hex()

def main() -> None:
    new_pass = sys.argv[1] if len(sys.argv) > 1 else "admin"
    con = sqlite3.connect(DB)
    cur = con.execute("UPDATE admins SET password_hash = ?, enabled = 1 WHERE username = 'admin'",
                      (hash_password(new_pass),))
    con.commit()
    if cur.rowcount:
        print(f"OK: password for 'admin' reset to '{new_pass}' (enabled).")
    else:
        print("ERROR: admin user not found!")
    # sanity check
    ph = con.execute("SELECT password_hash FROM admins WHERE username='admin'").fetchone()[0]
    salt_hex, key_hex = ph.split("$", 1)
    k = hashlib.scrypt(new_pass.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=16384, r=8, p=1, dklen=32)
    print("verify:", "MATCH" if k.hex() == key_hex else "MISMATCH")
    con.close()

if __name__ == "__main__":
    main()
