"""رفع آمن لملفات الهوت سبوت الكبيرة إلى الراوتر.

المشكلة الجذرية: `/file/add contents=<html>` في نداء API واحد يحمل
HTML كبيرًا (شعار/خط base64 مضمّن) فبعض إصدارات RouterOS تقطع الاتصال
(Connection reset by peer) على الحمولة الضخمة — reset «ثابت» لا عابر،
فلا تنفع إعادة المحاولة وحدها. الحلول هنا (تُركّب معًا):

  1) extract_inline_images — تنزع صور data:base64 الكبيرة من الـHTML
     وتعيدها ملفات منفصلة، فيصغر login.html كثيرًا ويمرّ عبر API بأمان.
     الصور binary فتُرفع عبر FTP (الـAPI لا يحمل binary موثوقًا).

  2) ftp_upload — رفع متدفّق مجزّأ عبر FTP (STOR ثنائي بدفعات صغيرة):
     يتجاوز حدّ جملة API الواحدة ويحمل الـbinary، مع تقدّم حسب الأجزاء.
     FTP على RouterOS يستعمل نفس اعتماد مستخدم الراوتر — لا أسرار زائدة.

لا تخزين أسرار جديدة: إعداد FTP يُبنى من نفس عنوان/مستخدم/كلمة مرور
API الموجودة في nas_devices.
"""
from __future__ import annotations

import base64
import ftplib
import io
import re
from dataclasses import dataclass

# حدّ حجم الحمولة الآمن لنداء API الواحد (بايت). فوقه نفضّل FTP مباشرة
# بدل المخاطرة بـ reset. ~50KB هامش متحفّظ يمرّ على كل إصدارات RouterOS.
API_SAFE_BYTES = 50_000

# الحدّ الأدنى لحجم صورة مضمّنة تستحق فصلها لملف (أصغر منه لا يفيد).
MIN_ASSET_BYTES = 2_048

# دفعة الرفع عبر FTP — صغيرة فلا تصطدم حمولة واحدة بحدّ الراوتر.
FTP_BLOCKSIZE = 8_192


_DATA_IMG_RE = re.compile(
    r"data:image/(?P<ext>png|jpe?g|gif|webp|svg\+xml);base64,"
    r"(?P<b64>[A-Za-z0-9+/=]+)"
)

_EXT_MAP = {
    "png": "png", "jpg": "jpg", "jpeg": "jpg",
    "gif": "gif", "webp": "webp", "svg+xml": "svg",
}


@dataclass
class InlineAsset:
    """أصل (صورة) نُزع من HTML ليُرفع ملفًا منفصلًا."""
    filename: str   # اسم نسبي بجانب login.html (مثل hr-asset-1.png)
    data: bytes     # الـbinary المفكوك من base64


def extract_inline_images(html: str, *,
                          min_bytes: int = MIN_ASSET_BYTES,
                          prefix: str = "hr-asset"):
    """ينزع صور `data:...;base64,` الكبيرة من الـHTML ويستبدل كلّ واحدة
    بمسار نسبي لاسم ملف منفصل. يعيد (html_جديد, [InlineAsset...]).

    الصور الصغيرة (< min_bytes) تبقى مضمّنة (لا تستحق ملفًا/رحلة FTP).
    base64 غير الصالح يُترك كما هو (لا نكسر الصفحة)."""
    assets: list[InlineAsset] = []
    counter = {"n": 0}

    def _repl(m: "re.Match[str]") -> str:
        raw = m.group("b64")
        try:
            blob = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001 — base64 معطوب → اتركه مضمّنًا
            return m.group(0)
        if len(blob) < min_bytes:
            return m.group(0)
        ext = _EXT_MAP.get(m.group("ext").lower(), "img")
        counter["n"] += 1
        name = f"{prefix}-{counter['n']}.{ext}"
        assets.append(InlineAsset(filename=name, data=blob))
        return name

    new_html = _DATA_IMG_RE.sub(_repl, html)
    return new_html, assets


def count_chunks(total: int, blocksize: int = FTP_BLOCKSIZE) -> int:
    """عدد دفعات الرفع المتوقّعة لحجم معطى (للعرض في شريط التقدّم)."""
    if total <= 0:
        return 0
    return (total + blocksize - 1) // blocksize


class FtpUploadError(Exception):
    """فشل رفع عبر FTP (اتصال/مصادقة/STOR) — يحمل سببًا واضحًا."""


def ftp_upload(host: str, user: str, password: str, remote_path: str,
               data: bytes, *, port: int = 21, timeout: float = 30.0,
               on_progress=None, blocksize: int = FTP_BLOCKSIZE,
               _ftp_factory=None) -> int:
    """رفع `data` إلى `remote_path` (نسبي لجذر ملفات الراوتر، مثل
    'hotspot/login.html') عبر FTP بدفعات صغيرة (STOR ثنائي).

    `on_progress(sent, total)` يُستدعى بعد كل دفعة. يعيد عدد البايت
    المرفوعة. يرمي FtpUploadError على أي فشل برسالة عربية واضحة.

    `_ftp_factory` نقطة حقن للاختبار (تُرجع كائن FTP وهميًا)."""
    total = len(data)
    sent = {"n": 0}
    bio = io.BytesIO(data)

    try:
        ftp = (_ftp_factory() if _ftp_factory is not None else ftplib.FTP())
    except Exception as e:  # noqa: BLE001
        raise FtpUploadError(f"تعذّر تهيئة FTP: {e}") from e

    try:
        ftp.connect(host, port, timeout)
        ftp.login(user, password)
        try:
            ftp.set_pasv(True)
        except Exception:  # noqa: BLE001 — بعض الكائنات الوهمية بلا الدالة
            pass

        def _cb(block: bytes) -> None:
            sent["n"] += len(block)
            if on_progress:
                try:
                    on_progress(sent["n"], total)
                except Exception:  # noqa: BLE001
                    pass

        ftp.storbinary("STOR " + remote_path, bio,
                       blocksize=blocksize, callback=_cb)
    except FtpUploadError:
        raise
    except Exception as e:  # noqa: BLE001
        raise FtpUploadError(_short_ftp_reason(e)) from e
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            try:
                ftp.close()
            except Exception:  # noqa: BLE001
                pass
    return sent["n"]


def _short_ftp_reason(e: BaseException) -> str:
    """رسالة عربية مختصرة لفشل FTP الشائع."""
    low = str(e).lower()
    if "refused" in low or "timed out" in low or "timeout" in low:
        return ("تعذّر الوصول إلى خدمة FTP على الراوتر (مرفوض/مهلة) — "
                "فعّل FTP على الراوتر أو افتح المنفذ 21 عبر النفق.")
    if "login" in low or "530" in low or "credentials" in low:
        return ("رُفض اعتماد FTP — تأكّد أن مستخدم API يملك صلاحية ftp "
                "على الراوتر.")
    return "فشل الرفع عبر FTP: " + str(e)


# ─── السحب من اللوحة عبر النفق (/tool fetch) — بديل FTP لا يحتاجه ──────
#
# المشكلة: تشديد التهيئة يُعطّل خدمة FTP على الراوتر (`/ip service disable
# ftp`)، فأي نشر يعتمد FTP ينهار. الحل: الراوتر يسحب الملف من اللوحة عبر
# نفق الإدارة بـ `/tool fetch` (HTTP) إلى مجلد الهوت سبوت. `/tool fetch`
# يستبدل ملف الوجهة إن وُجد، فيحلّ أيضًا «file already exists». لا يحتاج أي
# منفذ/خدمة جديدة على الراوتر — فقط وصول HTTP صادر إلى اللوحة عبر النفق
# (نفس الوصول الذي يستعمله المتجر).

# مسار نقطة التقديم العامّة (اللوحة) التي يسحب منها الراوتر؛ يُلحَق بـ
# base_url ثم الـtoken. يطابق المسار المُسجَّل في بلوبرنت الراديوس.
ROUTER_PULL_PATH = "/admin/radius/hotspot/pull/"


class FetchUploadError(Exception):
    """فشل سحب الراوتر للملف عبر /tool fetch — يحمل سببًا عربيًّا واضحًا."""


def _row_attrs(row):
    """يطبّع صفّ رد API إلى dict السمات (يقبل {'attrs':{...}} أو dict مباشر)."""
    if isinstance(row, dict):
        a = row.get("attrs")
        if isinstance(a, dict):
            return a
        return row
    return {}


def _fetch_status(rows) -> str:
    """آخر status من ردود /tool fetch (downloading/finished/failed/…)."""
    status = ""
    for s in (rows or []):
        a = _row_attrs(s)
        if a.get("status"):
            status = str(a.get("status")).strip().lower()
    return status


def _best_effort_remove(client, remote_path: str) -> None:
    """يحذف ملف الوجهة إن وُجد قبل السحب (تأمين «الاستبدال») — يبتلع كل خطأ."""
    try:
        rows = client.run("/file/print",
                          attrs={"where": "name=" + remote_path})
    except Exception:  # noqa: BLE001
        return
    for row in (rows or []):
        a = _row_attrs(row)
        if (a.get("name") or "") == remote_path:
            fid = a.get(".id") or a.get("id")
            if fid:
                try:
                    client.run("/file/remove", attrs={".id": fid})
                except Exception:  # noqa: BLE001
                    pass
            break


def _file_present(client, remote_path: str) -> bool:
    """يتحقّق أن الملف ظهر على الراوتر بعد السحب."""
    try:
        rows = client.run("/file/print",
                          attrs={"where": "name=" + remote_path})
    except Exception:  # noqa: BLE001
        return False
    for row in (rows or []):
        if (_row_attrs(row).get("name") or "") == remote_path:
            return True
    return False


def _short_fetch_reason(e: BaseException) -> str:
    low = str(e).lower()
    if "already exists" in low:
        # لا ينبغي أن يحدث (نحذف أولًا + fetch يستبدل) — رسالة احتياطيّة.
        return "الملف موجود على الراوتر وتعذّر استبداله عبر /tool fetch."
    if any(n in low for n in ("refused", "timed out", "timeout", "no route",
                              "could not connect", "failure", "unreachable")):
        return ("تعذّر سحب الملف بـ /tool fetch — لم يصل الراوتر إلى اللوحة "
                "عبر النفق (HTTP). تأكّد أن عنوان خادم الراديوس صحيح وأن النفق "
                "قائم وأن مسار اللوحة مسموح في walled-garden.")
    return "فشل السحب عبر /tool fetch: " + str(e)


def router_fetch_upload(client, remote_path: str, data: bytes, *,
                        base_url: str,
                        stash_fn,
                        content_type: str = "text/plain; charset=utf-8",
                        mode: str = "http",
                        on_progress=None,
                        verify: bool = True,
                        pull_path: str = ROUTER_PULL_PATH) -> int:
    """يجعل الراوتر يسحب `data` إلى `remote_path` عبر `/tool fetch`.

    يخزّن البايتات في مخزن مؤقّت عبر `stash_fn(data, content_type) -> token`،
    يبني رابط اللوحة `base_url + pull_path + token`، يحذف الوجهة إن وُجدت
    (تأمين الاستبدال)، ثم يشغّل `/tool fetch` على الراوتر. لا يعتمد FTP.

    يعيد عدد البايت المرفوعة، أو يرمي FetchUploadError برسالة واضحة.
    `client` أيّ كائن له `.run(path, attrs=...)`."""
    total = len(data)
    if not base_url:
        raise FetchUploadError(
            "لا يوجد عنوان لوحة يصله الراوتر عبر النفق (عنوان خادم الراديوس "
            "غير مضبوط) — تعذّر السحب بـ /tool fetch.")
    token = stash_fn(data, content_type)
    url = base_url.rstrip("/") + pull_path + token

    _best_effort_remove(client, remote_path)

    try:
        rows = client.run("/tool/fetch", attrs={
            "url": url, "mode": mode, "dst-path": remote_path,
        })
    except Exception as e:  # noqa: BLE001 — trap/connect → سبب واضح
        raise FetchUploadError(_short_fetch_reason(e)) from e

    if _fetch_status(rows) == "failed":
        raise FetchUploadError(_short_fetch_reason(Exception("fetch failure")))

    if verify and not _file_present(client, remote_path):
        raise FetchUploadError(
            "اكتمل أمر /tool fetch لكن الملف لم يظهر على الراوتر — تحقّق أن "
            "مجلد الوجهة موجود وأن السحب وصل اللوحة.")

    if on_progress:
        try:
            on_progress(total, total)
        except Exception:  # noqa: BLE001
            pass
    return total


def ftp_config_from_nas(row, *, default_port: int = 21,
                        timeout: float = 30.0) -> dict | None:
    """يبني إعداد FTP من صفّ nas_devices (نفس عنوان/مستخدم/كلمة مرور
    API — لا أسرار زائدة). يعيد None إن نقص اعتماد جوهري.

    `host` يُمرَّر من المستدعي (بعد حلّ عنوان الاتصال/النفق) لأن منطق
    resolve_connection_address يعيش في طبقة الراوت."""
    user = (row.get("api_user") if hasattr(row, "get") else row["api_user"]) or ""
    pw = (row.get("api_password") if hasattr(row, "get")
          else row["api_password"]) or ""
    if not user:
        return None
    return {"user": user, "password": pw, "port": default_port,
            "timeout": timeout}
