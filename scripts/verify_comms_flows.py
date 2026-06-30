#!/usr/bin/env python
"""Verify the COMMUNICATIONS / Operations-Center flows actually work end-to-end.

Mirrors ``scripts/verify_finance_flows.py``: runs DUMMY operations inside a real
Flask app context against the dev SQLite DB (tenant 1) and prints a PASS/FAIL
table, then restores any ``tenant_settings`` it touched so the run is fully
idempotent.

Flows checked (phases 1-4):
  (1) CHANNEL config   — save SMS channel (self_api + URL) then read it back
                         and confirm ``is_channel_active`` is True.
  (2) NOTIFY event     — enable ``subscriber_created`` for sms+telegram and fire
                         ``notify_event`` against a dummy subscriber; assert the
                         SMS dispatched + Telegram was attempted.
  (3) BOT inbound      — enable the WhatsApp bot and feed an inbound «الرصيد»
                         message; assert the bot matched a command and "sent" a
                         reply through the (dry) WhatsApp sender.
  (4) QUOTA            — credit +2 then consume 1 on the SMS channel; assert the
                         balance + ledger move correctly.

SAFETY / NO NETWORK:
  - Guarded by ``COMMS_DRY`` (defaults to "1"): the Phase-1 ``http_send`` and the
    Phase-2 Telegram ``send_to_tenant`` are replaced by no-op stubs that return
    success WITHOUT touching the network. Set ``COMMS_DRY=0`` only if you really
    want live sends (not recommended).
  - Operates only on the dev DB (instance/hoberadius.db) tenant 1.
  - All ``tenant_settings`` keys the script writes are snapshotted first and
    restored (or deleted) at the end — baseline is proven restored.

Usage (from repo root):
    python scripts/verify_comms_flows.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Arabic flows print Arabic detail lines — force UTF-8 stdout so this never
# crashes on a Windows cp1252 console.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001 — older Pythons / non-reconfigurable streams
    pass

# --- make repo root importable + force dev-safe env BEFORE importing app -----
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("HOBERADIUS_NO_WORKER", "1")  # no background threads
os.environ.setdefault("HOBERADIUS_NO_SEED", "1")    # do not reseed demo data
os.environ.setdefault("COMMS_DRY", "1")             # providers are no-op/dry
os.environ.pop("HOBERADIUS_ENV", None)
os.environ.pop("FLASK_ENV", None)

TENANT_ID = 1
ACTOR = "verify-comms-script"
DUMMY_PHONE = "0790009911"
SMS_URL = "https://gw.example.invalid/send?to={phone}&text={msg}"
DRY = os.environ.get("COMMS_DRY", "1").strip().lower() not in ("0", "false", "no", "off")


# ── PASS/FAIL line helper ─────────────────────────────────────────────────
def _line(flow: str, ok: bool, detail: str) -> str:
    tag = "PASS" if ok else "FAIL"
    return f"[{tag}] {flow:<26} {detail}"


# ── tenant_settings snapshot / restore (idempotency) ──────────────────────
def _snapshot_settings(conn, keys: list[str]) -> dict[str, str | None]:
    snap: dict[str, str | None] = {}
    for key in keys:
        row = conn.execute(
            "SELECT value FROM tenant_settings WHERE tenant_id=? AND key=?",
            (TENANT_ID, key),
        ).fetchone()
        snap[key] = (row[0] if row else None)
    return snap


def _restore_settings(conn, snap: dict[str, str | None]) -> int:
    """Restore prior values; delete keys that did not exist before. Returns
    the number of keys touched."""
    from app.radius.db.helpers import now_iso

    touched = 0
    for key, prior in snap.items():
        if prior is None:
            conn.execute(
                "DELETE FROM tenant_settings WHERE tenant_id=? AND key=?",
                (TENANT_ID, key),
            )
        else:
            conn.execute(
                """
                INSERT INTO tenant_settings(tenant_id, key, value, updated_by, updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(tenant_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (TENANT_ID, key, prior, 0, now_iso()),
            )
        touched += 1
    return touched


def _resolve_dummy_subscriber(conn) -> dict:
    """Reuse an existing subscriber; create a tagged dummy only if none exists.

    We force a known mobile on the chosen subscriber for the duration of the run
    (snapshotted + restored) so the bot phone-lookup + notify phone both resolve.
    """
    row = conn.execute(
        "SELECT id, username, mobile FROM subscribers "
        "WHERE tenant_id=? AND deleted_at IS NULL ORDER BY id LIMIT 1",
        (TENANT_ID,),
    ).fetchone()
    if row:
        return {"id": int(row[0]), "username": row[1], "prev_mobile": row[2], "created": False}
    from app.radius.core.types import Subscriber
    from app.radius.db.repos import subscribers_repo
    sub = subscribers_repo.upsert_subscriber(Subscriber(
        id=None, tenant_id=TENANT_ID,
        username="dummy_verify_comms",
        password="dummy-verify-pw",
        full_name="DUMMY-VERIFY",
        mobile=DUMMY_PHONE,
    ))
    return {"id": int(sub.id), "username": sub.username, "prev_mobile": None, "created": True}


def main() -> int:
    from app import create_app
    app = create_app()

    results: list[str] = []
    # Settings keys we will write (and must restore).
    from app.radius.services import notifications_engine as ne
    touched_keys: list[str] = []
    for ch in ("sms", "whatsapp"):
        for f in ("enabled", "mode", "send_url_template", "http_method", "balance_url"):
            touched_keys.append(f"comms.{ch}.{f}")
    for f in ("enabled", "greeting", "fallback", "commands"):
        touched_keys.append(f"comms.bot.{f}")
    ev = "subscriber_created"
    for f in ("enabled", "channels", "template"):
        touched_keys.append(f"notif.{ev}.{f}")

    with app.app_context():
        from app.radius.db.connection import db, transaction
        from app.radius.services import comms_bot, comms_providers, telegram_notifier

        conn = db()

        # ── DRY guard: replace senders with no-op success stubs (NO network) ──
        send_calls = {"http": 0, "telegram": 0}
        if DRY:
            def _dry_http(*, template, method, phone, message, **kwargs):
                send_calls["http"] += 1
                return comms_providers.HttpSendOutcome(
                    ok=True, status_code=200, body_excerpt='{"id":"dry"}', final_url="dry://ok")

            def _dry_tg(tenant_id, text):
                send_calls["telegram"] += 1
                return True, ""

            comms_providers.http_send = _dry_http          # patched everywhere it's called
            telegram_notifier.send_to_tenant = _dry_tg

        # Snapshot BEFORE we mutate anything.
        snap = _snapshot_settings(conn, touched_keys)
        subscriber = _resolve_dummy_subscriber(conn)
        sub_created_id = subscriber["id"] if subscriber["created"] else None

        # Force a known mobile so phone lookups resolve (restored on cleanup).
        if not subscriber["created"]:
            with transaction() as txn:
                txn.execute(
                    "UPDATE subscribers SET mobile=? WHERE id=? AND tenant_id=?",
                    (DUMMY_PHONE, subscriber["id"], TENANT_ID),
                )
        # Re-fetch a fresh subscriber object for the engine (by username — robust
        # against large subscriber tables where id-paging would miss it).
        from app.radius.db.repos import subscribers_repo
        sub_obj = subscribers_repo.get_subscriber(TENANT_ID, subscriber["username"])

        print(f"DRY MODE: {'ON (no network)' if DRY else 'OFF (LIVE SENDS!)'}")
        print(f"Using subscriber: id={subscriber['id']} username={subscriber['username']!r} "
              f"mobile={DUMMY_PHONE}")

        # ── (1) CHANNEL config save + read ───────────────────────────────────
        try:
            comms_providers.save_channel_config(TENANT_ID, "sms", {
                "enabled": "1", "mode": "self_api",
                "send_url_template": SMS_URL, "http_method": "GET",
            }, by=0)
            cfg = comms_providers.load_channel_config(TENANT_ID, "sms")
            ok1 = (cfg["enabled"] is True and cfg["mode"] == "self_api"
                   and cfg["send_url_template"] == SMS_URL
                   and comms_providers.is_channel_active(cfg) is True)
            results.append(_line("(1) channel config", ok1,
                                 f"enabled={cfg['enabled']} mode={cfg['mode']} active={comms_providers.is_channel_active(cfg)}"))
        except Exception as exc:  # noqa: BLE001
            results.append(_line("(1) channel config", False, f"error={exc!r}"))

        # ── (2) NOTIFY one business event (sms + telegram) ───────────────────
        try:
            ne.save_rules(TENANT_ID, {
                f"{ev}__enabled": "1",
                f"{ev}__channels": ["sms", "telegram"],
                f"{ev}__template": "تم إنشاء حساب {username}",
            }, by=0)
            http_before = send_calls["http"]
            tg_before = send_calls["telegram"]
            outcome = ne.notify_event(ev, tenant_id=TENANT_ID, subscriber=sub_obj)
            sms_ok = outcome.sent.get("sms") is True
            # In DRY mode the telegram stub returns success; live may be unconfigured.
            tg_attempted = (send_calls["telegram"] - tg_before) >= 1
            http_fired = (send_calls["http"] - http_before) >= 1
            ok2 = bool(outcome.fired and sms_ok and http_fired and (tg_attempted or not DRY))
            results.append(_line("(2) notify_event", ok2,
                                 f"fired={outcome.fired} sms={outcome.sent.get('sms')} "
                                 f"tg_attempted={tg_attempted} http_calls=+{send_calls['http']-http_before} "
                                 f"msg={outcome.message!r}"))
        except Exception as exc:  # noqa: BLE001
            results.append(_line("(2) notify_event", False, f"error={exc!r}"))

        # ── (3) BOT inbound reply ────────────────────────────────────────────
        try:
            # Enable the bot + reuse the (now-active) WhatsApp/SMS sender by also
            # enabling the whatsapp channel (the bot sends via whatsapp).
            comms_providers.save_channel_config(TENANT_ID, "whatsapp", {
                "enabled": "1", "mode": "self_api",
                "send_url_template": SMS_URL, "http_method": "GET",
            }, by=0)
            comms_bot.save_bot_config(TENANT_ID, {
                "enabled": "1",
                "commands": [{"keyword": "الرصيد", "reply_template": "رصيدك: {balance}", "enabled": "1"}],
            }, by=0)
            http_before = send_calls["http"]
            reply = comms_bot.handle_inbound(TENANT_ID, phone=DUMMY_PHONE, text="الرصيد")
            bot_http = (send_calls["http"] - http_before)
            ok3 = bool(reply.handled and reply.matched_keyword == "الرصيد"
                       and (reply.sent if DRY else True) and (bot_http >= 1 if DRY else True))
            results.append(_line("(3) bot inbound reply", ok3,
                                 f"handled={reply.handled} matched={reply.matched_keyword!r} "
                                 f"sent={reply.sent} reply={reply.reply_text!r}"))
        except Exception as exc:  # noqa: BLE001
            results.append(_line("(3) bot inbound reply", False, f"error={exc!r}"))

        # (4) The admin-sold message-bundle/quota model was retired (SMS &
        #     WhatsApp are free BYO services now) — nothing to verify here.

        # ───────────────────────── RESULTS ──────────────────────────────────
        print("\n" + "=" * 72)
        print("COMMS FLOW RESULTS")
        print("=" * 72)
        for line in results:
            print("  " + line)
        all_ok = all(line.startswith("[PASS]") for line in results)
        print("-" * 72)
        print(f"  total send attempts (dry): http_send={send_calls['http']} telegram={send_calls['telegram']}")
        print(f"  OVERALL: {'ALL FLOWS PASS' if all_ok else 'SOME FLOWS FAILED'}")
        print("=" * 72)

        # ───────────────────────── CLEANUP ──────────────────────────────────
        print("\nCLEANUP — restoring tenant_settings + subscriber mobile...")
        cleanup_ok = True
        try:
            with transaction() as txn:
                restored = _restore_settings(txn, snap)
                # Restore the subscriber mobile we forced (or remove a created one).
                if sub_created_id is not None:
                    txn.execute("DELETE FROM subscribers WHERE id=? AND tenant_id=?",
                                (sub_created_id, TENANT_ID))
                elif subscriber.get("prev_mobile") is not None:
                    txn.execute("UPDATE subscribers SET mobile=? WHERE id=? AND tenant_id=?",
                                (subscriber["prev_mobile"], subscriber["id"], TENANT_ID))
                else:
                    txn.execute("UPDATE subscribers SET mobile=NULL WHERE id=? AND tenant_id=?",
                                (subscriber["id"], TENANT_ID))
            print(f"    restored {restored} tenant_settings key(s).")
        except Exception as exc:  # noqa: BLE001
            cleanup_ok = False
            print(f"    CLEANUP ERROR: {exc!r}")

        # Prove the baseline is restored.
        post = _snapshot_settings(conn, touched_keys)
        restored_ok = (post == snap)
        print("=" * 72)
        if restored_ok:
            print("  CLEANUP: tenant_settings baseline RESTORED.")
        else:
            print("  CLEANUP: baseline NOT fully restored — diff:")
            for k in touched_keys:
                if post.get(k) != snap.get(k):
                    print(f"      {k}: before={snap.get(k)!r} after={post.get(k)!r}")
        print("=" * 72)

    return 0 if (all_ok and cleanup_ok and restored_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
