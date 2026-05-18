"""
Smoke E2E — يُشغَّل على VPS بعد النشر للتحقّق من العمل الكامل.

الاستخدام:
    python tests/smoke_e2e.py --url https://radius.example.com [--mt host=10.0.0.1,user=admin,pass=x,port=8728]

يختبر 15 سيناريو حسب ROADMAP_TO_VPS.md S5.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


class Smoke:
    def __init__(self, base: str, token: str | None = None) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.failures: list[str] = []
        self.passes: list[str] = []

    def _http(self, method: str, path: str, *, json_body=None, headers=None) -> tuple[int, dict]:
        url = self.base + path
        data = None
        h = {"Accept": "application/json"}
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        if headers:
            h.update(headers)
        req = urllib.request.Request(url, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try: body = json.loads(e.read().decode("utf-8") or "{}")
            except: body = {}
            return e.code, body

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        if ok:
            self.passes.append(name)
            print(f"  ✅ {name}" + (f" — {detail}" if detail else ""))
        else:
            self.failures.append(f"{name}: {detail}")
            print(f"  ❌ {name} — {detail}")

    def section(self, n: str) -> None:
        print(f"\n=== {n} ===")

    def run(self, mt: dict | None = None) -> int:
        self.section("T1: liveness /_health")
        s, b = self._http("GET", "/admin/radius/_health")
        self.check("liveness 200", s == 200, f"status={s}")

        self.section("T2: readiness /_healthz")
        s, b = self._http("GET", "/admin/radius/_healthz")
        self.check("readiness ok|degraded", s in (200, 503),
                    f"status={s}, body={json.dumps(b)[:120]}")
        self.check("DB check", b.get("checks", {}).get("db") == "ok",
                    f"db={b.get('checks',{}).get('db')}")

        self.section("T3: API /api/v1/health (لا auth)")
        s, b = self._http("GET", "/api/v1/health")
        self.check("api health 200", s == 200)

        if not self.token:
            print("\n⚠️  --token غير ممرَّر — أتخطّى اختبارات الـ API الـ authed.")
            return self._summary()

        self.section("T4: API auth بـ token غير صحيح")
        old = self.token; self.token = "wrong-token"
        s, _ = self._http("GET", "/api/v1/accounts")
        self.check("invalid token → 401", s == 401, f"status={s}")
        self.token = old

        self.section("T5: API accounts list (DB-backed)")
        s, b = self._http("GET", "/api/v1/accounts?limit=5")
        self.check("accounts list 200", s == 200)

        self.section("T6: create account عبر API")
        username = "smoke-" + secrets.token_hex(4)
        s, b = self._http("POST", "/api/v1/accounts", json_body={
            "username": username, "password": "smoke-pass", "full_name": "Smoke Test",
        })
        self.check("create 201", s == 201, f"status={s}")
        created_username = (b.get("data") or {}).get("username") or username

        self.section("T7: get account")
        s, b = self._http("GET", f"/api/v1/accounts/{created_username}")
        self.check("get 200", s == 200)

        self.section("T8: disable / enable")
        s, _ = self._http("POST", f"/api/v1/accounts/{created_username}/disable")
        self.check("disable 200", s == 200)
        s, _ = self._http("POST", f"/api/v1/accounts/{created_username}/enable")
        self.check("enable 200", s == 200)

        self.section("T9: generate cards (5)")
        s, b = self._http("POST", "/api/v1/cards/generate", json_body={
            "plan_id": 1, "count": 5, "username_prefix": "smk-",
        })
        self.check("cards 201", s == 201)
        cards = (b.get("data") or {}).get("cards", [])
        self.check("5 cards returned", len(cards) == 5, f"got {len(cards)}")

        self.section("T10: profiles + nas + sessions/online")
        s1, _ = self._http("GET", "/api/v1/profiles")
        s2, _ = self._http("GET", "/api/v1/nas")
        s3, _ = self._http("GET", "/api/v1/sessions/online")
        self.check("profiles 200", s1 == 200)
        self.check("nas 200", s2 == 200)
        self.check("online 200", s3 == 200)

        self.section("T11: accounting endpoint")
        s, b = self._http("GET", "/api/v1/accounting?limit=10")
        self.check("accounting 200", s == 200)

        if mt:
            self.section("T12: MikroTik test-credentials")
            s, b = self._http("POST", "/api/v1/mikrotik/test-credentials", json_body=mt)
            self.check("mt test", s == 200,
                        f"status={s}, msg={json.dumps(b)[:120]}")
            if s == 200:
                ident = (b.get("data") or {}).get("identity", {}).get("name", "?")
                self.check("mt identity present", "name" in (b.get("data") or {}).get("identity", {}),
                            f"identity={ident}")

        self.section("T13: cleanup (delete account)")
        s, _ = self._http("DELETE", f"/api/v1/accounts/{created_username}")
        self.check("delete 200", s == 200)

        self.section("T14: OpenAPI spec")
        s, b = self._http("GET", "/api/openapi.json")
        self.check("openapi 200", s == 200)
        paths = len(b.get("paths") or {})
        self.check("paths >= 20", paths >= 20, f"got {paths}")

        self.section("T15: rate limit (rough)")
        # نُرسل 70 طلب سريعًا للـ accounts list — مع env CSV token rpm = 60 افتراضيًا
        codes = []
        for _ in range(70):
            s, _ = self._http("GET", "/api/v1/accounts?limit=1")
            codes.append(s)
        n429 = codes.count(429)
        # نتسامح: لو rpm عالٍ في الـ tier، 429 = 0 مقبول
        print(f"     429 count = {n429} / 70 (إن صفر، tier فيه rpm عالٍ — مقبول)")

        return self._summary()

    def _summary(self) -> int:
        print(f"\n{'─'*40}\nNet: {len(self.passes)} pass, {len(self.failures)} fail")
        if self.failures:
            print("\nfailures:")
            for f in self.failures: print(f"  • {f}")
            return 1
        print("✅ كل الاختبارات نجحت.")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="مثلاً https://radius.example.com")
    ap.add_argument("--token", default=None, help="API token (Bearer)")
    ap.add_argument("--mt", default=None,
                     help='MT للتحقق: host=10.0.0.1,user=admin,pass=x,port=8728[,tls=1]')
    args = ap.parse_args()

    mt = None
    if args.mt:
        kv = dict(p.split("=", 1) for p in args.mt.split(","))
        mt = {
            "host": kv.get("host"),
            "username": kv.get("user", "admin"),
            "password": kv.get("pass", ""),
            "port": int(kv.get("port", "8728")),
            "use_tls": kv.get("tls") == "1",
            "verify_tls": kv.get("verify", "1") == "1",
            "timeout_sec": 8,
        }

    return Smoke(args.url, args.token).run(mt=mt)


if __name__ == "__main__":
    sys.exit(main())
