"""اختبارات خدمة شات المتجر — StoreChatService.

شات خفيف بين الزبون والمدير: نص + إرفاق صورة، بلا مال. تغطّي:
الإرسال والترتيب التصاعدي وترقيم after_id، رفض الرسالة الفارغة،
رفض المرسِل غير الصالح، أعلام القراءة وعدّاد غير المقروء، رابط الصورة،
وصندوق وارد المدير (list_threads) باسم الزبون وعدّاد غير المقروء.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest


@pytest.fixture
def app(monkeypatch):
    tmp = tempfile.mkdtemp(prefix="hr_store_chat_")
    monkeypatch.setenv("HOBERADIUS_DB_PATH", os.path.join(tmp, "test.db"))
    monkeypatch.setenv("HOBERADIUS_NO_WORKER", "1")
    monkeypatch.setenv("HOBERADIUS_NO_SEED", "1")
    monkeypatch.delenv("HOBERADIUS_ENV", raising=False)
    monkeypatch.delenv("FLASK_ENV", raising=False)
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]
    from app import create_app

    created = create_app()
    yield created
    for key in list(sys.modules):
        if key.startswith("app."):
            del sys.modules[key]


def _make_card_user(display_name="زبون شات", mobile="0590000077", password="pw1234"):
    from app.radius.services.card_users_marketplace import (
        CardUsersMarketplaceService,
    )

    svc = CardUsersMarketplaceService(tenant_id=1)
    user = svc.create_card_user(
        display_name=display_name, mobile=mobile, password=password
    )
    return int(user["id"])


def _chat():
    from app.radius.services.store_chat import StoreChatService

    return StoreChatService(tenant_id=1)


# ───────────────────────── الإرسال والترتيب وترقيم after_id ─────────────────────────


def test_post_and_list_thread_ascending(app):
    with app.app_context():
        cuid = _make_card_user()
        chat = _chat()
        m1 = chat.post_message(card_user_id=cuid, sender="customer", body="مرحبا")
        m2 = chat.post_message(
            card_user_id=cuid, sender="admin", body="أهلًا بك", admin_actor="مدير"
        )
        thread = chat.list_thread(card_user_id=cuid)
        items = thread["items"]
        assert len(items) == 2
        assert [it["id"] for it in items] == sorted(it["id"] for it in items)
        assert items[0]["body"] == "مرحبا"
        assert items[0]["sender"] == "customer"
        assert items[1]["body"] == "أهلًا بك"
        assert items[1]["sender"] == "admin"
        assert thread["last_id"] == m2["id"]

        # ترقيم after_id يعيد الأحدث فقط
        newer = chat.list_thread(card_user_id=cuid, after_id=m1["id"])
        assert [it["id"] for it in newer["items"]] == [m2["id"]]
        assert newer["last_id"] == m2["id"]

        # لا جديد بعد آخر رسالة
        none_newer = chat.list_thread(card_user_id=cuid, after_id=m2["id"])
        assert none_newer["items"] == []
        assert none_newer["last_id"] == m2["id"]


# ───────────────────────── التحقق ─────────────────────────


def test_post_without_body_or_image_raises(app):
    with app.app_context():
        cuid = _make_card_user(mobile="0590000078")
        chat = _chat()
        from app.radius.services.store_chat import StoreChatError

        with pytest.raises(StoreChatError):
            chat.post_message(card_user_id=cuid, sender="customer", body="   ")


def test_invalid_sender_raises(app):
    with app.app_context():
        cuid = _make_card_user(mobile="0590000079")
        chat = _chat()
        from app.radius.services.store_chat import StoreChatError

        with pytest.raises(StoreChatError):
            chat.post_message(card_user_id=cuid, sender="robot", body="مرحبا")


# ───────────────────────── أعلام القراءة وعدّاد غير المقروء ─────────────────────────


def test_read_flags_and_unread_count(app):
    with app.app_context():
        cuid = _make_card_user(mobile="0590000080")
        chat = _chat()

        # رسالة الزبون تبدأ غير مقروءة للمدير
        cust = chat.post_message(card_user_id=cuid, sender="customer", body="عندي مشكلة")
        assert cust["read_by_admin"] == 0
        assert cust["read_by_customer"] == 1

        # المدير يقرأ → تنقلب رسالة الزبون مقروءة
        flipped = chat.mark_read(card_user_id=cuid, reader="admin")
        assert flipped == 1
        after = chat.list_thread(card_user_id=cuid)["items"]
        assert after[0]["read_by_admin"] == 1

        # رد المدير غير مقروء للزبون
        chat.post_message(card_user_id=cuid, sender="admin", body="رد", admin_actor="مدير")
        assert chat.unread_for_customer(card_user_id=cuid) == 1

        # الزبون يقرأ → يصفر العدّاد
        flipped_c = chat.mark_read(card_user_id=cuid, reader="customer")
        assert flipped_c == 1
        assert chat.unread_for_customer(card_user_id=cuid) == 0


def test_invalid_reader_raises(app):
    with app.app_context():
        cuid = _make_card_user(mobile="0590000081")
        chat = _chat()
        from app.radius.services.store_chat import StoreChatError

        with pytest.raises(StoreChatError):
            chat.mark_read(card_user_id=cuid, reader="nobody")


# ───────────────────────── رابط الصورة ─────────────────────────


def test_image_path_produces_image_url(app):
    with app.app_context():
        cuid = _make_card_user(mobile="0590000082")
        chat = _chat()
        msg = chat.post_message(
            card_user_id=cuid,
            sender="customer",
            body="",
            image_path="uploads/store/receipt.png",
        )
        assert msg["image_url"] == "/static/uploads/store/receipt.png"

        # مسار يبدأ بـstatic/ لا يُضاعَف
        msg2 = chat.post_message(
            card_user_id=cuid,
            sender="customer",
            image_path="static/uploads/x.png",
        )
        assert msg2["image_url"] == "/static/uploads/x.png"

        # بلا صورة → رابط فارغ
        msg3 = chat.post_message(card_user_id=cuid, sender="customer", body="نص فقط")
        assert msg3["image_url"] == ""


# ───────────────────────── صندوق وارد المدير ─────────────────────────


def test_list_threads_for_admin_inbox(app):
    with app.app_context():
        cuid = _make_card_user(display_name="عميل المتجر", mobile="0590000083")
        chat = _chat()
        chat.post_message(card_user_id=cuid, sender="customer", body="رسالة 1")
        chat.post_message(card_user_id=cuid, sender="customer", body="رسالة 2")
        chat.post_message(card_user_id=cuid, sender="admin", body="رد", admin_actor="مدير")

        threads = chat.list_threads()
        assert len(threads) == 1
        t = threads[0]
        assert t["card_user_id"] == cuid
        assert t["display_name"] == "عميل المتجر"
        assert t["mobile"] == "0590000083"
        # رسالتان من الزبون غير مقروءتين للمدير
        assert t["unread_admin_count"] == 2
        assert t["total_count"] == 3
        assert t["last_body"] == "رد"

        # بعد قراءة المدير يصفر عدّاد غير المقروء
        chat.mark_read(card_user_id=cuid, reader="admin")
        t2 = chat.list_threads()[0]
        assert t2["unread_admin_count"] == 0
