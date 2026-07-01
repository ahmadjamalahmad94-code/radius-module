"""لوحة شحن الرصيد (للمالك) — أرصدة/ديون/سلف المدراء والموزّعين + شحن مباشر.

يثبّت العقود التي طلبها المالك:
  1. الحراسة: المالك الرئيسي وحده يرى اللوحة ويشحن — 403 لغيره (خادميًّا، GET+POST).
  2. الشحن يسدّد الدين أولًا ثم يقيّد الباقي في المحفظة — للمدير *وللموزّع*.
  3. الشحن يصيب المشغّل الصحيح فقط (لا يلمس غيره).
  4. الإجماليّات (أرصدة/ديون/سلف) صحيحة عبر المدراء والموزّعين.
  5. نموذج رصيد الموزّع مُعاد استخدامه (محفظة + distributors.debt_balance) لا مُخترَع.
"""
from __future__ import annotations

import os
import sys
import tempfile
from uuid import uuid4

import pytest


# ─────────────────────────── fixtures (NO_SEED) ───────────────────────────
@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_credit_dash_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]
    from app import create_app
    flask_app = create_app()
    with flask_app.app_context():
        from app.radius.db.migrations_runner import run_pending_migrations
        from app.radius.db.repos import admins_repo, tenants_repo
        run_pending_migrations()
        tenants_repo.ensure_default_tenant()
        admins_repo.ensure_default_roles()
    yield flask_app
    for k in list(sys.modules):
        if k.startswith("app."):
            del sys.modules[k]


@pytest.fixture
def client(app):
    return app.test_client()


# ─────────────────────────── helpers ───────────────────────────
def _owner():
    """أول مسؤول = المالك الرئيسي (أصغر id)، uncapped."""
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(
        username=f"owner_{uuid4().hex[:8]}", password="owner-pass",
        full_name="Primary Owner", is_super_admin=True,
    )


def _manager(name="Mgr"):
    from app.radius.db.repos import admins_repo
    return admins_repo.create_admin(
        username=f"mgr_{uuid4().hex[:8]}", password="mgr-pass", full_name=name,
    )


def _super_role_admin():
    """مسؤول غير مالك يحمل دورًا بكل الصلاحيات — يجب أن يبقى 403 على اللوحة."""
    from app.radius.core.constants import ALL_PERMISSIONS
    from app.radius.db.repos import admins_repo
    role = admins_repo.create_role(
        name=f"superrole_{uuid4().hex[:6]}", display_name="Super-like",
        permissions=tuple(ALL_PERMISSIONS),
    )
    return admins_repo.create_admin(
        username=f"adm_{uuid4().hex[:8]}", password="adm-pass",
        full_name="Full-perms Admin", role_id=role.id,
    )


def _fund_manager(manager_id, money):
    from app.radius.services.business_os_finance import WalletService
    w = WalletService().create_wallet(tenant_id=1, owner_type="manager", owner_id=int(manager_id))
    if float(money) > 0:
        WalletService().credit(
            tenant_id=1, wallet_id=int(w["id"]), amount=f"{float(money):.2f}",
            actor_type="admin", actor_id=1, reference_type="test_fund",
        )


def _give_manager_debt(manager_id, money):
    """يصنع دينًا حقيقيًّا للمدير عبر بوّابة الإنفاق (محفظة 0 + سقف دين مُفعَّل)."""
    from app.radius.db.repos import admins_repo
    from app.radius.services.manager_credit import ManagerCreditService
    admins_repo.update_admin(int(manager_id), debt_cap_enabled=True, debt_cap_minor=1_000_00)
    res = ManagerCreditService(tenant_id=1).charge(
        int(manager_id), int(round(float(money) * 100)), kind="generic", own=True)
    assert res["mode"] == "debt"


def _distributor(*, balance=0.0, debt=0.0, credit_limit=0.0, name="Dist"):
    from app.radius.services.operations import get_operations_service
    saved = get_operations_service().create_distributor(
        tenant_id=1, actor="owner",
        data={"name": f"{name}_{uuid4().hex[:6]}", "balance": balance,
              "debt_balance": debt, "credit_limit": credit_limit},
    )
    return saved


def _dist_wallet_credit(distributor_id, money):
    from app.radius.services.business_os_finance import WalletService
    w = WalletService().create_wallet(tenant_id=1, owner_type="distributor", owner_id=int(distributor_id))
    if float(money) > 0:
        WalletService().credit(
            tenant_id=1, wallet_id=int(w["id"]), amount=f"{float(money):.2f}",
            actor_type="admin", actor_id=1, reference_type="test_fund",
        )


def _login(client, username, password):
    res = client.post("/admin/radius/login",
                      data={"username": username, "password": password},
                      follow_redirects=False)
    assert res.status_code in {302, 303}, res.status_code


def _svc():
    from app.radius.services.credit_dashboard import CreditDashboardService
    return CreditDashboardService(tenant_id=1)


# ════════════════════ (1) الحراسة — المالك فقط ════════════════════
def test_owner_sees_dashboard(app, client):
    with app.app_context():
        owner = _owner()
    _login(client, owner.username, "owner-pass")
    assert client.get("/admin/radius/credit").status_code == 200


def test_dashboard_renders_with_populated_rows(app, client):
    """يصيّر كل فروع القالب: صف مدير (بدين/سلف/سقف) + صف موزّع + آخر شحن + زر الشحن."""
    with app.app_context():
        owner = _owner()
        mgr = _manager("Populated")
        _fund_manager(mgr.id, 0)
        _give_manager_debt(mgr.id, 12)
        _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="5", actor="owner")
        d = _distributor(debt=8.0, credit_limit=50.0)
        _dist_wallet_credit(d["id"], 30)
    _login(client, owner.username, "owner-pass")
    res = client.get("/admin/radius/credit")
    assert res.status_code == 200
    body = res.get_data(as_text=True)
    assert 'data-testid="credit-managers-table"' in body
    assert 'data-testid="credit-distributors-table"' in body
    assert 'data-testid="credit-recharge-form"' in body
    assert "Populated" in body


def test_non_owner_403_on_dashboard(app, client):
    with app.app_context():
        _owner()                       # المالك #1
        adm = _super_role_admin()      # غير مالك بكل الصلاحيات
    _login(client, adm.username, "adm-pass")
    assert client.get("/admin/radius/credit").status_code == 403


def test_non_owner_403_on_recharge_post(app, client):
    with app.app_context():
        _owner()
        adm = _super_role_admin()
        mgr = _manager()
    _login(client, adm.username, "adm-pass")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tkn"     # يتجاوز فحص CSRF ليصل لحارس RBAC الفعلي
    res = client.post(
        f"/admin/radius/credit/recharge/manager/{mgr.id}",
        data={"_csrf_token": "tkn", "amount": "10", "method": "cash"},
        follow_redirects=False)
    assert res.status_code == 403


def test_owner_recharge_via_web_credits_wallet(app, client):
    """مسار كامل مربوط: المالك يشحن عبر الويب → 302 ناجح + رصيد المحفظة يرتفع."""
    with app.app_context():
        owner = _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
    _login(client, owner.username, "owner-pass")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tkn"
    res = client.post(
        f"/admin/radius/credit/recharge/manager/{mgr.id}",
        data={"_csrf_token": "tkn", "amount": "18.50", "method": "cash"},
        follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.services.manager_credit import ManagerCreditService
        assert ManagerCreditService(tenant_id=1).wallet_balance_minor(mgr.id) == 18_50


# ════════════════════ (2) الشحن يسدّد الدين أولًا — مدير ════════════════════
def test_recharge_manager_settles_debt_first(app):
    with app.app_context():
        _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
        _give_manager_debt(mgr.id, 30)          # دين 30.00، محفظة 0

        from app.radius.services.manager_credit import ManagerCreditService
        credit = ManagerCreditService(tenant_id=1)
        assert credit.current_debt_minor(mgr.id) == 30_00
        assert credit.wallet_balance_minor(mgr.id) == 0

        # شحن 10 < الدين → كله تسديد، لا رصيد.
        r1 = _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="10", actor="owner")
        assert r1["settled_debt"] == "10.00" and r1["credited_wallet"] == "0.00"
        assert credit.current_debt_minor(mgr.id) == 20_00
        assert credit.wallet_balance_minor(mgr.id) == 0

        # شحن 50 > الدين المتبقّي (20) → 20 تسديد + 30 للمحفظة.
        r2 = _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="50", actor="owner")
        assert r2["settled_debt"] == "20.00" and r2["credited_wallet"] == "30.00"
        assert credit.current_debt_minor(mgr.id) == 0
        assert credit.wallet_balance_minor(mgr.id) == 30_00


def test_recharge_manager_no_debt_all_to_wallet(app):
    with app.app_context():
        _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
        r = _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="25", actor="owner")
        assert r["settled_debt"] == "0.00" and r["credited_wallet"] == "25.00"
        from app.radius.services.manager_credit import ManagerCreditService
        assert ManagerCreditService(tenant_id=1).wallet_balance_minor(mgr.id) == 25_00


# ════════════════════ (2) الشحن يسدّد الدين أولًا — موزّع ════════════════════
def test_recharge_distributor_settles_debt_first(app):
    with app.app_context():
        _owner()
        dist = _distributor(debt=40.0, credit_limit=100.0)
        did = int(dist["id"])
        from app.radius.db.repos import operations_repo
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        ops = ManagerDistributorOpsService(tenant_id=1)

        def _wallet_bal():
            return int(ops.wallet_for(entity_type="distributor", entity_id=did).get("balance_minor") or 0)

        assert float(operations_repo.get_distributor(1, did)["debt_balance"]) == 40.0
        assert _wallet_bal() == 0

        # شحن 15 < الدين → كله تسديد، الرصيد (المحفظة) لا يتغيّر.
        r1 = _svc().recharge(entity_type="distributor", entity_id=did, amount="15", actor="owner")
        assert r1["settled_debt"] == "15.00" and r1["credited_wallet"] == "0.00"
        assert float(operations_repo.get_distributor(1, did)["debt_balance"]) == 25.0
        assert _wallet_bal() == 0

        # شحن 100 > الدين المتبقّي (25) → 25 تسديد + 75 للمحفظة.
        r2 = _svc().recharge(entity_type="distributor", entity_id=did, amount="100", actor="owner")
        assert r2["settled_debt"] == "25.00" and r2["credited_wallet"] == "75.00"
        assert float(operations_repo.get_distributor(1, did)["debt_balance"]) == 0.0
        assert _wallet_bal() == 75_00


def test_distributor_debt_settle_does_not_double_bump_balance(app):
    """نموذج رصيد الموزّع مُعاد استخدامه: تسديد الدين يخفض debt_balance فقط
    دون رفع distributors.balance (الرصيد القابل للصرف يعيش في المحفظة)."""
    with app.app_context():
        _owner()
        dist = _distributor(balance=0.0, debt=20.0)
        did = int(dist["id"])
        from app.radius.db.repos import operations_repo
        _svc().recharge(entity_type="distributor", entity_id=did, amount="20", actor="owner")
        row = operations_repo.get_distributor(1, did)
        assert float(row["debt_balance"]) == 0.0     # الدين سُدّد
        assert float(row["balance"]) == 0.0          # لم يُضاعف الرصيد القديم


# ════════════════════ (3) يصيب المشغّل الصحيح فقط ════════════════════
def test_recharge_targets_only_named_principal(app):
    with app.app_context():
        _owner()
        a = _manager("A"); b = _manager("B")
        _fund_manager(a.id, 0); _fund_manager(b.id, 0)
        _svc().recharge(entity_type="manager", entity_id=a.id, amount="40", actor="owner")
        from app.radius.services.manager_credit import ManagerCreditService
        credit = ManagerCreditService(tenant_id=1)
        assert credit.wallet_balance_minor(a.id) == 40_00
        assert credit.wallet_balance_minor(b.id) == 0


# ════════════════════ (4) الإجماليّات صحيحة ════════════════════
def test_overview_totals_across_managers_and_distributors(app):
    with app.app_context():
        _owner()
        m1 = _manager("M1"); m2 = _manager("M2")
        _fund_manager(m1.id, 100); _fund_manager(m2.id, 0)
        _give_manager_debt(m2.id, 30)
        d1 = _distributor(debt=15.0); d2 = _distributor(debt=5.0)
        _dist_wallet_credit(d1["id"], 200)

        data = _svc().overview()
        # المدراء: الأرصدة تشمل المالك (0) + 100 + 0 = 100، الدين 30.
        assert data["manager_totals"]["balance"] == "100.00"
        assert data["manager_totals"]["debt"] == "30.00"
        # الموزّعون: رصيد محفظة d1 = 200، دين 15+5 = 20.
        assert data["distributor_totals"]["balance"] == "200.00"
        assert data["distributor_totals"]["debt"] == "20.00"
        # شامل.
        assert data["grand_totals"]["balance"] == "300.00"
        assert data["grand_totals"]["debt"] == "50.00"

        # الصفوف تحمل المشغّلين، والموزّع بلا مفهوم سلف.
        assert any(r["id"] == m1.id for r in data["managers"])
        assert all(r["has_loans"] is False for r in data["distributors"])


def test_overview_records_last_recharge(app):
    with app.app_context():
        _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
        _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="12", actor="owner")
        data = _svc().overview()
        row = next(r for r in data["managers"] if r["id"] == mgr.id)
        assert row["last_recharge"] is not None
        assert row["last_recharge"]["amount"] == "12.00"


# ════════════════════ (5) رفض المدخلات غير الصالحة ════════════════════
def test_recharge_rejects_non_positive(app):
    with app.app_context():
        _owner()
        mgr = _manager()
        from app.radius.services.credit_dashboard import CreditDashboardError
        with pytest.raises(CreditDashboardError):
            _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="0", actor="owner")
        with pytest.raises(CreditDashboardError):
            _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="-5", actor="owner")


# ════════════════════ (6) حالة الدفع: مدفوع مقابل دين ════════════════════
def test_paid_recharge_credits_wallet_no_new_debt(app):
    """«تم الدفع»: يُضاف للمحفظة ولا يُنشئ ديناً جديداً (السلوك الافتراضي)."""
    with app.app_context():
        _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
        from app.radius.services.manager_credit import ManagerCreditService
        credit = ManagerCreditService(tenant_id=1)

        r = _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="40",
                            payment_status="paid", actor="owner")
        assert r["payment_status"] == "paid"
        assert r["credited_wallet"] == "40.00"
        assert r["debt_recorded"] == "0.00"
        assert credit.wallet_balance_minor(mgr.id) == 40_00
        assert credit.current_debt_minor(mgr.id) == 0     # لا دين جديد

    # الافتراضي (بلا payment_status) = مدفوع.
    with app.app_context():
        m2 = _manager("Def")
        _fund_manager(m2.id, 0)
        r2 = _svc().recharge(entity_type="manager", entity_id=m2.id, amount="10", actor="owner")
        assert r2["payment_status"] == "paid"


def test_debt_recharge_manager_increases_outstanding_debt(app):
    """«دين»: يرفع «الدين المستحق» للمدير بالقيمة كاملةً + يُضيف رصيداً قابلاً
    للصرف + يظهر في دفتر manager_credit_ledger وفي أعمدة اللوحة والإجماليّات."""
    with app.app_context():
        _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
        from app.radius.services.manager_credit import ManagerCreditService
        credit = ManagerCreditService(tenant_id=1)
        assert credit.current_debt_minor(mgr.id) == 0

        r = _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="60",
                            payment_status="debt", actor="owner")
        assert r["payment_status"] == "debt"
        assert r["debt_recorded"] == "60.00"
        assert r["settled_debt"] == "0.00"
        assert r["credited_wallet"] == "60.00"           # الرصيد قابل للصرف

        # الدين ارتفع بالقيمة كاملةً، والرصيد أُضيف للاستخدام.
        assert credit.current_debt_minor(mgr.id) == 60_00
        assert credit.wallet_balance_minor(mgr.id) == 60_00

        # يظهر في دفتر الدين (قيد KIND_DEBT).
        from app.radius.db.connection import db
        row = db().execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(amount_minor),0) AS s "
            "FROM manager_credit_ledger WHERE tenant_id=1 AND manager_id=? AND kind='debt'",
            (mgr.id,),
        ).fetchone()
        assert int(row["c"]) == 1 and int(row["s"]) == 60_00

        # اللوحة تعكسه فوراً: عمود «الدين المستحق» + إجمالي الديون.
        data = _svc().overview()
        mrow = next(r for r in data["managers"] if r["id"] == mgr.id)
        assert mrow["debt"] == "60.00"
        assert data["manager_totals"]["debt"] == "60.00"


def test_debt_recharge_does_not_settle_prior_debt(app):
    """«دين» لا يسدّد ديناً سابقاً — يضيف فوقه (خلافاً لـ«مدفوع»)."""
    with app.app_context():
        _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
        _give_manager_debt(mgr.id, 20)      # دين سابق 20
        from app.radius.services.manager_credit import ManagerCreditService
        credit = ManagerCreditService(tenant_id=1)
        assert credit.current_debt_minor(mgr.id) == 20_00

        _svc().recharge(entity_type="manager", entity_id=mgr.id, amount="30",
                        payment_status="debt", actor="owner")
        # الدين صار 20 + 30 = 50 (لم يُسدَّد السابق).
        assert credit.current_debt_minor(mgr.id) == 50_00
        assert credit.wallet_balance_minor(mgr.id) == 30_00


def test_debt_recharge_distributor_increases_debt_balance(app):
    """«دين» للموزّع: يرفع distributors.debt_balance + قيد debit في دفتره +
    يُضيف رصيداً للمحفظة — ويظهر في اللوحة والإجماليّات."""
    with app.app_context():
        _owner()
        dist = _distributor(debt=0.0)
        did = int(dist["id"])
        from app.radius.db.repos import operations_repo
        from app.radius.services.manager_distributor_ops import ManagerDistributorOpsService
        ops = ManagerDistributorOpsService(tenant_id=1)

        def _wallet_bal():
            return int(ops.wallet_for(entity_type="distributor", entity_id=did).get("balance_minor") or 0)

        r = _svc().recharge(entity_type="distributor", entity_id=did, amount="35",
                            payment_status="debt", actor="owner")
        assert r["payment_status"] == "debt"
        assert r["debt_recorded"] == "35.00" and r["credited_wallet"] == "35.00"
        assert float(operations_repo.get_distributor(1, did)["debt_balance"]) == 35.0
        assert _wallet_bal() == 35_00

        # قيد debit في دفتر الموزّع المدقَّق.
        from app.radius.db.connection import db
        led = db().execute(
            "SELECT COUNT(*) AS c FROM distributor_ledger_entries "
            "WHERE tenant_id=1 AND distributor_id=? AND direction='debit' AND entry_type='on_account_credit'",
            (did,),
        ).fetchone()
        assert int(led["c"]) == 1

        # اللوحة والإجماليّات.
        data = _svc().overview()
        drow = next(x for x in data["distributors"] if x["id"] == did)
        assert drow["debt"] == "35.00"
        assert data["distributor_totals"]["debt"] == "35.00"


def test_debt_recharge_via_web_owner_only(app, client):
    """مسار كامل مربوط عبر الويب: المالك يمنح ديناً → 302 + الدين يرتفع."""
    with app.app_context():
        owner = _owner()
        mgr = _manager()
        _fund_manager(mgr.id, 0)
    _login(client, owner.username, "owner-pass")
    with client.session_transaction() as sess:
        sess["_csrf_token"] = "tkn"
    res = client.post(
        f"/admin/radius/credit/recharge/manager/{mgr.id}",
        data={"_csrf_token": "tkn", "amount": "22", "method": "manual",
              "payment_status": "debt"},
        follow_redirects=False)
    assert res.status_code in {302, 303}
    with app.app_context():
        from app.radius.services.manager_credit import ManagerCreditService
        credit = ManagerCreditService(tenant_id=1)
        assert credit.current_debt_minor(mgr.id) == 22_00
        assert credit.wallet_balance_minor(mgr.id) == 22_00


def test_modal_has_payment_status_selector(app, client):
    """القالب يعرض محدّد «حالة الدفع» بخياريه مدفوع/دين."""
    with app.app_context():
        owner = _owner()
    _login(client, owner.username, "owner-pass")
    body = client.get("/admin/radius/credit").get_data(as_text=True)
    assert 'name="payment_status"' in body
    assert 'value="paid"' in body and 'value="debt"' in body
