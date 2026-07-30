"""MT92 — سحب أصوات الإشعارات من لوحة التراخيص.

قرار المالك: الأصوات تُرفع مرّةً في اللوحة المركزيّة، وكلّ نسخة ريديوس تسحبها
تلقائيًّا وتجعلها افتراضيّها. مالك الريديوس **يختار** (كلامٌ أم نغمة) ولا
**يُغيّر** — فلا رفعَ عنده إطلاقًا.

خطوتان عمدًا:
  ① البيان: مفاتيح + بصمات فقط (كيلوبايتات).
  ② الجلب: بايتات ما تغيّرت بصمته وحده.

بدون ① لكان كلّ سحبٍ دوريّ يجرّ ميغابايتات عبر كلّ نسخةٍ منشورة كلّ ساعة.

ولا شيء هنا يرمي: الصوت زينة. تعذّر الاتصال يعني «لا جديد اليوم»، والنسخ
تُشغّل ما عندها (أو نغمتها) كما كانت.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

_LOG = logging.getLogger(__name__)


def sync_once(tenant_id: int = 1) -> dict[str, Any]:
    """سحبةٌ واحدة. يُرجع تقريرًا يصلح للسجلّ وللعرض. لا يرمي أبدًا."""
    from . import notification_sounds as snd

    report: dict[str, Any] = {
        "ok": False, "reason": "", "checked": 0,
        "updated": 0, "skipped": 0, "failed": 0,
    }
    try:
        from .admin_panel_client import AdminPanelClient
        client = AdminPanelClient()
    except Exception:  # noqa: BLE001
        report["reason"] = "client_unavailable"
        return report

    # MT98 — نُرسل كتالوجنا مع السحب. اللوحة كانت تحمل قائمةً مكتوبةً باليد
    # فتعرض أقلّ ممّا يُطلقه الريديوس (29 مقابل 48)، ونسخةٌ تُضيف أحداثًا
    # (فرع TR-069) لا تظهر أحداثها هناك أبدًا. الريديوس هو من يعرف أحداثه،
    # فليُعلّمها للوحة بدل أن تُخمّنها — بلا نداءٍ إضافيّ ولا صيانةٍ يدويّة.
    manifest = client.get_notification_sounds_manifest(catalog=_catalog_payload())
    if not manifest.get("ok"):
        report["reason"] = str(manifest.get("reason") or "manifest_failed")
        return report

    rows = manifest.get("sounds")
    if not isinstance(rows, list):
        report["reason"] = "bad_manifest"
        return report

    have = snd.status_map(tenant_id)
    local_checksums = _local_checksums(tenant_id)

    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("event_key") or "").strip()
        remote_sum = str(row.get("checksum") or "").strip()
        if not key or not remote_sum:
            continue
        report["checked"] += 1

        # مفتاحٌ لا يعرفه هذا الإصدار: تُتجاهَل بهدوء. اللوحة قد تسبق النسخة
        # بأحداثٍ جديدة، وذلك ليس خطأً — ولا يجوز أن يُسقط بقيّة السحب.
        if not snd.is_valid_key(key):
            report["skipped"] += 1
            continue

        # رفعٌ محلّيّ موجود؟ لا نلمسه (وهو مستحيلٌ اليوم لأنّ الرفع مُقفل،
        # لكنّ الحارس يبقى: قرار العميل يفوز لو فُتح يومًا).
        if (have.get(key) or {}).get("origin") == "local":
            report["skipped"] += 1
            continue

        if local_checksums.get(key) == remote_sum:
            report["skipped"] += 1
            continue

        fetched = client.fetch_notification_sound(key)
        if not fetched.get("ok"):
            report["failed"] += 1
            _LOG.warning("sound fetch failed for %s: %s", key, fetched.get("reason"))
            continue
        try:
            raw = base64.b64decode(str(fetched.get("data_b64") or ""))
        except Exception:  # noqa: BLE001
            report["failed"] += 1
            _LOG.warning("sound payload undecodable for %s", key)
            continue
        if not raw:
            report["failed"] += 1
            continue

        ok, _msg = snd.save_sound(
            tenant_id, key, raw,
            mime=str(fetched.get("mime") or "audio/mpeg"),
            filename=str(fetched.get("filename") or ""),
            origin="central",
            # بصمة اللوحة لا بصمتنا: بها وحدها تُقارَن السحبة التالية.
            checksum=str(fetched.get("checksum") or remote_sum))
        if ok:
            report["updated"] += 1
        else:
            report["failed"] += 1

    report["ok"] = True
    if report["updated"]:
        _LOG.info("notification sounds: %d محدَّثة من اللوحة المركزيّة",
                  report["updated"])
    return report


def _local_checksums(tenant_id: int) -> dict[str, str]:
    """بصمات ما هو مخزَّنٌ محلّيًّا — بها نتجنّب تنزيل ما لم يتغيّر."""
    try:
        from ..db.connection import db
        rows = db().execute(
            "SELECT sound_key, checksum FROM notification_sounds "
            "WHERE tenant_id=?", (tenant_id,)).fetchall()
        return {r["sound_key"]: (r["checksum"] or "") for r in rows}
    except Exception:  # noqa: BLE001
        return {}


def _catalog_payload() -> list[dict]:
    """كتالوج هذه النسخة كما تُعلنه للوحة: مفتاح + تسمية + مجموعة."""
    try:
        from . import notification_sounds as snd
        return [{"key": e.key, "label": e.label, "group": e.group,
                 "group_label": snd.GROUP_LABELS.get(e.group, e.group)}
                for e in snd.EVENTS.values()]
    except Exception:  # noqa: BLE001 — الإعلان زينة، لا يُسقط السحب
        return []
