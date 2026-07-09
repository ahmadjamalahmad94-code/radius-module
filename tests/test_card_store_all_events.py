"""اختبارات: كل نوع حركة في متجر البطاقات يُنتج حدثًا صحيحًا في audit_log.

الاختبارات مُعزَّلة: كل اختبار ينشئ قاعدة بيانات مؤقتة خاصة به
بلا حالة مشتركة مع الاختبارات الأخرى (نمط test_isolation_per_file).
"""
from __future__ import annotations

import json
import pytest

# ── مساعدات الاختبار ──────────────────────────────────────────────────────────

def _make_app(tmp_path):
    """ينشئ تطبيق Flask مؤقتًا مع DB فارغة ومزوّد audit."""
    import sys, os, importlib
    # نحتاج app حقيقية لتشغيل request context
    os.environ.setdefault("RADIUS_DB_PATH", str(tmp_path / "radius.db"))
    os.environ.setdefault("SECRET_KEY", "test-secret-store-events")
    os.environ.setdefault("STORE_KEY", "test-store-key")
    # استيراد create_app بشكل lazy لتفادي SPOF
    from app import create_app
    app = create_app({"TESTING": True,
                      "WTF_CSRF_ENABLED": False,
                      "DATABASE": str(tmp_path / "radius.db")})
    return app


def _audit_rows(conn, action=None):
    """يجلب صفوف audit_log المطابقة للفعل أو كلها."""
    sql = "SELECT * FROM audit_log"
    params = []
    if action:
        sql += " WHERE action = ?"
        params.append(action)
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ── اختبارات الوحدة لـ _emit_store_event (بلا قاعدة بيانات) ─────────────────

class TestEmitStoreEventSilent:
    """_emit_store_event لا يرفع استثناء أبدًا حتى لو فشل audit."""

    def test_no_exception_without_app_context(self):
        """_emit_store_event لا يكسر الاستدعاء خارج سياق Flask."""
        from app.api.v1.store import _emit_store_event
        # لا يجب أن يرفع أي استثناء — الفشل صامت
        _emit_store_event("store.test", card_user_id=1, mobile="0599000001")


# ── اختبارات التكامل: كل حدث يُكتب في audit_log ─────────────────────────────

@pytest.fixture()
def store_client(tmp_path):
    """عميل اختبار Flask مع DB مهيّأة."""
    pytest.importorskip("app")
    app = _make_app(tmp_path)
    with app.test_client() as client:
        with app.app_context():
            # أنشئ الجداول الضرورية
            from app.radius.db.connection import db
            conn = db()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER DEFAULT 1,
                    actor TEXT,
                    action TEXT,
                    target_type TEXT,
                    target_id TEXT,
                    payload_json TEXT,
                    severity TEXT DEFAULT 'info',
                    result_status TEXT DEFAULT '',
                    error_message TEXT DEFAULT '',
                    ip_address TEXT,
                    user_agent TEXT,
                    router_id INTEGER,
                    before_json TEXT,
                    after_json TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS card_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER DEFAULT 1,
                    display_name TEXT,
                    mobile TEXT UNIQUE,
                    password_hash TEXT,
                    status TEXT DEFAULT 'active',
                    wallet_balance_minor INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS system_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                INSERT OR IGNORE INTO system_config (key, value)
                VALUES ('billing.currency', 'ILS');
            """)
            conn.commit()
            yield client, conn


def _store_headers(token):
    return {"Authorization": f"Bearer {token}",
            "Content-Type": "application/json"}


def _make_token(app, card_user_id=1, tenant_id=1):
    with app.app_context():
        from app.radius.services.store_token import issue_store_token
        return issue_store_token(card_user_id=card_user_id, tenant_id=tenant_id)


# ── emit مباشر: يتجاوز HTTP ويختبر أن السجل يُكتب صحيحًا ──────────────────

class TestStoreEventEmissionDirect:
    """يختبر _emit_store_event مباشرةً داخل سياق Flask."""

    def _emit_and_check(self, tmp_path, action, payload, *, cuid=1, mobile="0599111111"):
        pytest.importorskip("app")
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import db
            conn = db()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER DEFAULT 1,
                    actor TEXT, action TEXT, target_type TEXT,
                    target_id TEXT, payload_json TEXT, severity TEXT DEFAULT 'info',
                    result_status TEXT DEFAULT '', error_message TEXT DEFAULT '',
                    ip_address TEXT, user_agent TEXT, router_id INTEGER,
                    before_json TEXT, after_json TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)
            conn.commit()
            from app.api.v1.store import _emit_store_event
            with app.test_request_context("/api/v1/store/test",
                                          environ_base={"REMOTE_ADDR": "192.168.1.1"}):
                _emit_store_event(action, card_user_id=cuid,
                                  mobile=mobile, payload=payload)
            rows = _audit_rows(conn, action)
            assert len(rows) == 1, f"يجب أن يكون هناك صف واحد في audit_log للفعل {action}"
            return rows[0]

    def test_register_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.register",
                                 {"display_name": "أحمد علي"})
        assert r["action"] == "store.register"
        assert r["target_type"] == "card_user"
        pl = json.loads(r["payload_json"] or "{}")
        assert pl.get("display_name") == "أحمد علي"

    def test_register_failed_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.register_failed", {},
                                 mobile="0599222222")
        assert r["action"] == "store.register_failed"
        assert r["severity"] in ("warning", "error")

    def test_logout_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.logout", {})
        assert r["action"] == "store.logout"
        assert r["target_type"] == "card_user"

    def test_purchase_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.purchase",
                                 {"package_id": 5, "package_name": "باقة شهرية",
                                  "amount": "15.00", "card_username": "user123"})
        assert r["action"] == "store.purchase"
        pl = json.loads(r["payload_json"] or "{}")
        assert pl.get("package_name") == "باقة شهرية"
        # لا كلمات مرور في الحمولة
        assert "password" not in str(r["payload_json"])

    def test_purchase_failed_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.purchase_failed",
                                 {"package_id": 5})
        assert r["action"] == "store.purchase_failed"
        assert r["target_type"] == "card_user"

    def test_card_redeem_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.card_redeem",
                                 {"card_number": "CARD-001", "amount": "10.00"})
        assert r["action"] == "store.card_redeem"
        pl = json.loads(r["payload_json"] or "{}")
        assert pl.get("card_number") == "CARD-001"

    def test_card_redeem_failed_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.card_redeem_failed",
                                 {"card_number": "CARD-XXX"})
        assert r["action"] == "store.card_redeem_failed"

    def test_deposit_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.deposit",
                                 {"amount": "50.00", "method": "bank",
                                  "request_id": "42"})
        assert r["action"] == "store.deposit"
        pl = json.loads(r["payload_json"] or "{}")
        assert pl.get("method") == "bank"

    def test_deposit_failed_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.deposit_failed",
                                 {"amount": "0"})
        assert r["action"] == "store.deposit_failed"

    def test_withdrawal_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.withdrawal",
                                 {"amount": "20.00", "payee_name": "محمد خالد"})
        assert r["action"] == "store.withdrawal"
        pl = json.loads(r["payload_json"] or "{}")
        assert pl.get("payee_name") == "محمد خالد"

    def test_withdrawal_failed_event(self, tmp_path):
        r = self._emit_and_check(tmp_path, "store.withdrawal_failed",
                                 {"amount": "999"})
        assert r["action"] == "store.withdrawal_failed"

    def test_no_plaintext_password_in_any_event(self, tmp_path):
        """لا يجوز أبدًا تسجيل كلمة المرور في أي حدث متجر."""
        pytest.importorskip("app")
        app = _make_app(tmp_path)
        with app.app_context():
            from app.radius.db.connection import db
            conn = db()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id INTEGER DEFAULT 1,
                    actor TEXT, action TEXT, target_type TEXT,
                    target_id TEXT, payload_json TEXT, severity TEXT DEFAULT 'info',
                    result_status TEXT DEFAULT '', error_message TEXT DEFAULT '',
                    ip_address TEXT, user_agent TEXT, router_id INTEGER,
                    before_json TEXT, after_json TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                );
            """)
            conn.commit()
            from app.api.v1.store import _emit_store_event
            with app.test_request_context("/api/v1/store/test"):
                _emit_store_event("store.purchase", card_user_id=1,
                                  mobile="0599111111",
                                  payload={"card_username": "user1"})
            rows = conn.execute("SELECT * FROM audit_log").fetchall()
            for row in rows:
                payload_text = str(row["payload_json"] or "")
                assert "password" not in payload_text.lower(), \
                    f"كلمة المرور مُسرَّبة في audit_log: {payload_text}"


# ── اختبار مُزخرف: _decorate_card_store_rows يُنتج الملصقات الصحيحة ──────────

class TestDecorateCardStoreRows:
    """يختبر أن _decorate_card_store_rows تُضيف action_label + result_label صحيحًا."""

    def _decorate(self, rows):
        # نحتاج سياق Flask لـ _tid()
        pytest.importorskip("app")
        import os, tempfile
        db_path = tempfile.mktemp(suffix=".db")
        os.environ["RADIUS_DB_PATH"] = db_path
        from app import create_app
        app = create_app({"TESTING": True, "DATABASE": db_path})
        with app.app_context():
            from app.radius.db.connection import db
            conn = db()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS card_users (
                    id INTEGER PRIMARY KEY, tenant_id INTEGER DEFAULT 1,
                    display_name TEXT, mobile TEXT
                );
                INSERT INTO card_users (id, tenant_id, display_name, mobile)
                VALUES (1, 1, 'أحمد', '0599000001');
            """)
            conn.commit()
            from app.radius.routes.reports import _decorate_card_store_rows
            with app.test_request_context("/?tenant_id=1"):
                return _decorate_card_store_rows(rows)

    def _row(self, action, **kw):
        return {"id": 1, "action": action, "actor": "0599000001",
                "target_id": "1", "target_type": "card_user",
                "payload_json": json.dumps(kw.get("payload", {})),
                "error_message": kw.get("error_message", ""),
                "created_at": "2026-07-09 10:00:00",
                "ip_address": "10.0.0.1",
                "actor_label": "0599000001",
                "target_type_label": "مستخدم بطاقة",
                "detail_display": "",
                "action_label": action,
                "target_display": "",
                "changes": []}

    def test_auth_login_ok(self):
        rows = self._decorate([self._row("auth_login")])
        assert rows[0]["login_ok"] is True
        assert rows[0]["result_label"] == "دخول ناجح"

    def test_auth_login_failed(self):
        rows = self._decorate([self._row("auth_login_failed",
                                         error_message="bad_password")])
        assert rows[0]["login_ok"] is False

    def test_store_register(self):
        rows = self._decorate([self._row("store.register",
                                          payload={"display_name": "علي"})])
        assert rows[0]["result_label"] == "تسجيل"
        assert rows[0]["login_ok"] is True

    def test_store_logout(self):
        rows = self._decorate([self._row("store.logout")])
        assert rows[0]["result_label"] == "خروج"
        assert rows[0]["login_ok"] is True

    def test_store_purchase(self):
        rows = self._decorate([self._row("store.purchase",
                                          payload={"package_name": "باقة", "amount": "15"})])
        assert rows[0]["result_label"] == "شراء"
        assert "العرض: باقة" in rows[0]["detail_display"]

    def test_store_purchase_failed(self):
        rows = self._decorate([self._row("store.purchase_failed",
                                          error_message="رصيد غير كافٍ")])
        assert rows[0]["login_ok"] is False

    def test_store_card_redeem(self):
        rows = self._decorate([self._row("store.card_redeem",
                                          payload={"card_number": "C001", "amount": "10"})])
        assert rows[0]["result_label"] == "شحن بطاقة"

    def test_store_deposit(self):
        rows = self._decorate([self._row("store.deposit",
                                          payload={"amount": "50", "method": "bank"})])
        assert rows[0]["result_label"] == "إيداع"
        assert "المبلغ: 50" in rows[0]["detail_display"]

    def test_store_withdrawal(self):
        rows = self._decorate([self._row("store.withdrawal",
                                          payload={"amount": "20", "payee_name": "خالد"})])
        assert rows[0]["result_label"] == "سحب"
        assert "المستفيد: خالد" in rows[0]["detail_display"]

    def test_store_identity_resolved_by_id(self):
        rows = self._decorate([self._row("store.login")])
        assert "أحمد" in rows[0]["store_identity"]
