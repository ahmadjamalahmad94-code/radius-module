"""Web UI for card print template operations room."""
from __future__ import annotations

import base64
import binascii
import re
from io import BytesIO
from pathlib import PurePath

from flask import Blueprint, Response, flash, g, jsonify, redirect, render_template, request, session, url_for
from PIL import Image, ImageOps, UnidentifiedImageError

from ..core.errors import RadiusError, RadiusValidationError
from ..services.card_renderer import (
    build_card_render_model,
    card_mm_box,
    normalize_render_engine,
    render_card_svg,
)
from ..services.cards import get_cards_service
from ..services.operations import get_operations_service


def register_print_template_routes(bp: Blueprint) -> None:
    bp.add_url_rule("/print-templates", "print_templates", print_templates, methods=["GET"])
    bp.add_url_rule("/print-templates", "print_templates_create", print_templates_create, methods=["POST"])
    bp.add_url_rule(
        "/print-templates/export",
        "print_templates_export_center",
        print_templates_export_center,
        methods=["GET"],
    )
    # صفحة الإحصائيات المنفصلة: جداول القوالب المحفوظة + سجل التصدير
    # خرجت من الصفحة الرئيسية حتى تبقى غرفتا الطباعة والتصميم نظيفتين.
    bp.add_url_rule(
        "/print-templates/stats",
        "print_templates_stats",
        print_templates_stats,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/preview",
        "print_templates_preview",
        print_templates_preview,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/preview-fragment",
        "print_templates_preview_fragment",
        print_templates_preview_fragment,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/thumbnail.svg",
        "print_templates_thumbnail",
        print_templates_thumbnail,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/designer-svg",
        "print_templates_designer_svg",
        print_templates_designer_svg,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/edit",
        "print_templates_update",
        print_templates_update,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/delete",
        "print_templates_delete",
        print_templates_delete,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/cleanup-fixtures",
        "print_templates_cleanup_fixtures",
        print_templates_cleanup_fixtures,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/set-default",
        "print_templates_set_default",
        print_templates_set_default,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/export.pdf",
        "print_templates_export_pdf",
        print_templates_export_pdf,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/<int:template_id>/export-jobs",
        "print_templates_export_job_start",
        print_templates_export_job_start,
        methods=["POST"],
    )
    bp.add_url_rule(
        "/print-templates/export-jobs/<int:job_id>",
        "print_templates_export_job_status",
        print_templates_export_job_status,
        methods=["GET"],
    )
    bp.add_url_rule(
        "/print-templates/export-jobs/<int:job_id>/download",
        "print_templates_export_job_download",
        print_templates_export_job_download,
        methods=["GET"],
    )


def _tid() -> int:
    return int(getattr(g, "tenant_id", session.get("tenant_id") or 1))


def _actor() -> str:
    return session.get("admin_name") or session.get("admin_user") or "anonymous"


def _int(name: str, default: int = 0) -> int:
    try:
        return int(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float = 0) -> float:
    try:
        return float(request.form.get(name) or default)
    except (TypeError, ValueError):
        return default


# علامة بصرية محايدة تُستخدم بدل سلاسل وهمية مثل «SAMPLE» / «CARD1234»
# في معاينات التصميم/المصغّرات قبل ربطها بدفعة حقيقية. الشرطة الطويلة
# (em-dash) إشارة معروفة لـ«لا قيمة بعد» فلا تظهر كرقم بطاقة حقيقي.
_PLACEHOLDER_CARD_TEXT = "—"


def _first_real_card_sample() -> dict:
    """يلتقط أول بطاقة حقيقية من أي دفعة لهذا المستأجر — تُستخدم في
    المعاينات/المصغّرات بدلًا من سلاسل وهمية. عند انعدامها يعيد قاموسًا
    بعلامة «—» مع `is_real=False` ليستطيع المستدعي إظهار شارة
    «معاينة بدون بطاقات حقيقية بعد»."""
    fallback = {
        "id": "",
        "username": _PLACEHOLDER_CARD_TEXT,
        "password": "********",
        "qr_payload": _PLACEHOLDER_CARD_TEXT,
        "is_real": False,
    }
    try:
        cards = get_cards_service().list_cards(limit=1, offset=0)
    except Exception:  # pragma: no cover — defensive (DB not ready)
        return fallback
    if not cards:
        return fallback
    card = cards[0] if isinstance(cards, list) else cards
    if not isinstance(card, dict):
        try:
            card = dict(card)
        except Exception:  # pragma: no cover — non-iterable row
            return fallback
    username = (card.get("username") or "").strip()
    if not username:
        return fallback
    return {
        "id": card.get("id") or "",
        "username": username,
        "password": "********",
        "qr_payload": username,
        "is_real": True,
    }


def _checked(name: str, default: bool = False) -> bool:
    if name not in request.form:
        return default
    return request.form.get(name) in {"1", "true", "on", "yes"}


_URL_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")


def _normalize_login_url(value: str) -> str:
    """يطبّع رابط الدخول التلقائي قبل حفظه في القالب.

    المستخدم غالبًا يكتب عنوان IP مجرّدًا (مثل 10.10.10.10) بدون
    http:// — نضيف البروتوكول هنا حتى يُخزَّن الرابط في القالب بصيغة
    قياسية، فتتطابق المعاينة الحية مع التصدير حرفيًا. الروابط التي
    تحمل بروتوكولًا بالفعل تمر كما هي.
    """
    value = (value or "").strip()
    if not value:
        return ""
    if not _URL_SCHEME_RE.match(value):
        value = "http://" + value
    return value


_BACKGROUND_INPUT_MAX_BYTES = 8_000_000
_BACKGROUND_MAX_EDGE_PX = 1400
_BACKGROUND_TARGET_BYTES = 420_000


def _image_has_alpha(image: Image.Image) -> bool:
    return image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )


def _save_optimized_jpeg(image: Image.Image) -> tuple[bytes, int]:
    rgb = image.convert("RGB")
    best = b""
    best_quality = 84
    for quality in (84, 78, 72):
        out = BytesIO()
        rgb.save(
            out,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=True,
            subsampling=2,
        )
        candidate = out.getvalue()
        best = candidate
        best_quality = quality
        if len(candidate) <= _BACKGROUND_TARGET_BYTES:
            break
    return best, best_quality


def _save_optimized_png(image: Image.Image) -> bytes:
    rgba = image.convert("RGBA")
    out = BytesIO()
    rgba.save(out, format="PNG", optimize=True, compress_level=9)
    return out.getvalue()


def _optimize_background_image(raw: bytes, filename: str, mime: str) -> dict:
    if len(raw) > _BACKGROUND_INPUT_MAX_BYTES:
        raise RadiusError("حجم صورة الخلفية كبير جدًا. ارفع صورة حتى 8MB وسيقوم النظام بضغطها تلقائيًا.")
    try:
        image = Image.open(BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise RadiusError("تعذّر قراءة صورة الخلفية. استخدم PNG أو JPG أو WEBP.") from exc

    image = ImageOps.exif_transpose(image)
    width, height = image.size
    if width <= 0 or height <= 0 or (width * height) > 36_000_000:
        raise RadiusError("أبعاد صورة الخلفية كبيرة جدًا. استخدم صورة أصغر من 36MP.")

    if max(image.size) > _BACKGROUND_MAX_EDGE_PX:
        image.thumbnail((_BACKGROUND_MAX_EDGE_PX, _BACKGROUND_MAX_EDGE_PX), Image.Resampling.LANCZOS)

    original_mime = (mime or "").lower()
    if _image_has_alpha(image):
        optimized = _save_optimized_png(image)
        output_mime = "image/png"
        quality = None
    else:
        optimized, quality = _save_optimized_jpeg(image)
        output_mime = "image/jpeg"

    encoded = base64.b64encode(optimized).decode("ascii")
    new_width, new_height = image.size
    return {
        "background_image_data_url": f"data:{output_mime};base64,{encoded}",
        "background_image_name": PurePath(filename).name[:120],
        "background_image_mime": output_mime,
        "background_image_original_mime": original_mime,
        "background_image_original_bytes": len(raw),
        "background_image_optimized_bytes": len(optimized),
        "background_image_width": new_width,
        "background_image_height": new_height,
        "background_image_quality": quality,
        "background_image_optimized": True,
    }


def _background_from_data_url() -> dict:
    data_url = (request.form.get("background_image_data_url") or "").strip()
    if not data_url.startswith("data:image/") or ";base64," not in data_url:
        return {}
    mime_part, encoded = data_url.split(";base64,", 1)
    mime = mime_part.removeprefix("data:").lower()
    if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return {}
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RadiusError("تعذّر حفظ صورة الخلفية من المعاينة. اختر الصورة مرة أخرى.") from exc
    if not raw:
        return {}
    filename = request.form.get("background_image_name") or "card-background"
    return _optimize_background_image(raw, filename, mime)


def _uploaded_background(*, allow_data_url: bool = True) -> dict:
    upload = request.files.get("background_image")
    if upload and upload.filename:
        raw = upload.read()
        if raw:
            mime = (upload.mimetype or "").lower()
            if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
                raise RadiusError("نوع الصورة غير مدعوم. استخدم PNG أو JPG أو WEBP.")
            return _optimize_background_image(raw, upload.filename, mime)
    # Drag/drop and live-preview flows keep the bitmap in a hidden data URL.
    # Persist that exact preview image too, otherwise the user sees it in the
    # designer but loses it after saving/exporting.
    return _background_from_data_url() if allow_data_url else {}


def _logo_from_data_url() -> dict:
    """Optimize the logo from its hidden data-URL field, mapped to logo_* keys.

    Mirrors `_background_from_data_url` but namespaces the output so a logo
    never collides with the background image. Returns {} when no logo is set.
    """
    data_url = (request.form.get("logo_image_data_url") or "").strip()
    if not data_url.startswith("data:image/") or ";base64," not in data_url:
        return {}
    mime_part, encoded = data_url.split(";base64,", 1)
    mime = mime_part.removeprefix("data:").lower()
    if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
        return {}
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RadiusError("تعذّر حفظ الشعار من المعاينة. اختر الصورة مرة أخرى.") from exc
    if not raw:
        return {}
    filename = request.form.get("logo_image_name") or "card-logo"
    optimized = _optimize_background_image(raw, filename, mime)
    return {
        "logo_image_data_url": optimized["background_image_data_url"],
        "logo_image_name": optimized["background_image_name"],
        "logo_image_mime": optimized["background_image_mime"],
    }


def _uploaded_logo(*, allow_data_url: bool = True) -> dict:
    upload = request.files.get("logo_image")
    if upload and upload.filename:
        raw = upload.read()
        if raw:
            mime = (upload.mimetype or "").lower()
            if mime not in {"image/png", "image/jpeg", "image/jpg", "image/webp"}:
                raise RadiusError("نوع الشعار غير مدعوم. استخدم PNG أو JPG أو WEBP.")
            optimized = _optimize_background_image(raw, upload.filename, mime)
            return {
                "logo_image_data_url": optimized["background_image_data_url"],
                "logo_image_name": optimized["background_image_name"],
                "logo_image_mime": optimized["background_image_mime"],
            }
    return _logo_from_data_url() if allow_data_url else {}


def _payload(*, allow_data_url_background: bool = True) -> dict:
    raw_layout = {
        "render_engine": request.form.get("render_engine"),
        "card_orientation": request.form.get("card_orientation"),
        "text_direction": request.form.get("text_direction"),
        "credential_label_language": request.form.get("credential_label_language"),
    }
    render_engine = normalize_render_engine(raw_layout.get("render_engine"), raw_layout)
    engine_language, engine_orientation = render_engine.split("_", 1)
    text_direction = "rtl" if engine_language == "ar" else "ltr"
    label_language = "arabic" if engine_language == "ar" else "english"
    requested_background_style = (request.form.get("background_style") or "").strip().lower()
    has_form_background_image = (request.form.get("background_image_data_url") or "").strip().startswith("data:image/")
    if requested_background_style in {"image", "stored_image", "photo", "upload", "uploaded"}:
        background_style = "image"
    elif requested_background_style in {"preset", "system", "graphics", "generated"}:
        background_style = "preset"
    elif requested_background_style == "gradient":
        background_style = "image" if has_form_background_image else "preset"
    else:
        background_style = "image" if has_form_background_image else "preset"

    layout = {
        "preview_mode": "visual_design_room",
        "card_width_mm": _float("card_width_mm", 85),
        "card_height_mm": _float("card_height_mm", 54),
        "render_engine": render_engine,
        "card_orientation": engine_orientation,
        "design_preset": request.form.get("design_preset") or "modern",
        "gradient_start": request.form.get("gradient_start") or "#0f172a",
        "gradient_end": request.form.get("gradient_end") or "#22a7bd",
        "accent_color": request.form.get("accent_color") or "#f59e0b",
        "text_color": request.form.get("text_color") or request.form.get("color") or "#ffffff",
        "surface_color": request.form.get("surface_color") or "#e8f7fb",
        # Data-strip / pill transparency (0..1). Matches the renderer's
        # surface_opacity default so untouched templates are unchanged.
        "surface_opacity": _float("surface_opacity", 0.95),
        "credential_text_color": request.form.get("credential_text_color") or "#0f172a",
        "credential_label_color": request.form.get("credential_label_color") or "#64748b",
        "username_surface_color": request.form.get("username_surface_color") or request.form.get("surface_color") or "#e8f7fb",
        "password_surface_color": request.form.get("password_surface_color") or request.form.get("surface_color") or "#e8f7fb",
        "username_font_size": _float("username_font_size"),
        "password_font_size": _float("password_font_size"),
        "credential_label_font_size": _float("credential_label_font_size"),
        # وحدة مقاسات الخط: 'pt' = نقاط طباعية حقيقية (المصمم الجديد
        # يرسلها دائمًا)؛ الفارغ = وحدات كانفس (قوالب/نماذج قديمة).
        "font_size_unit": (
            "pt" if (request.form.get("font_size_unit") or "").strip().lower() == "pt"
            else ""),
        "qr_color": request.form.get("qr_color") or "#0f172a",
        "qr_background_color": request.form.get("qr_background_color") or "#ffffff",
        "qr_size_pct": _float("qr_size_pct"),
        "pattern_style": request.form.get("pattern_style") or "signal",
        # Decorative line/grid/signal/circle colour. Default white keeps the
        # legacy look for templates that never set it.
        "pattern_color": request.form.get("pattern_color") or "#ffffff",
        "image_opacity": _float("image_opacity", 0.82),
        "qr_style": request.form.get("qr_style") or "boxed",
        "text_direction": text_direction,
        "credential_label_language": label_language,
        "brand_name": request.form.get("brand_name") or "HobeRadius",
        "card_title": request.form.get("card_title") or "بطاقة إنترنت",
        "footer_text": request.form.get("footer_text") or "",
        "hotspot_address": request.form.get("hotspot_address") or "",
        # رابط الدخول التلقائي للهوت سبوت — عند تعبئته يصبح رمز QR
        # رابط /login?username=&password= فيدخل الزبون فور المسح.
        # فارغ = سلوك القوالب القديمة بلا تغيير. يُطبَّع هنا (إضافة
        # http:// لعناوين IP المجرّدة) حتى يُحفَظ في القالب بصيغة قياسية.
        "hotspot_login_url": _normalize_login_url(request.form.get("hotspot_login_url") or ""),
        "price_text": request.form.get("price_text") or "",
        "validity_text": request.form.get("validity_text") or "",
        "instructions_text": request.form.get("instructions_text") or "",
        "background_style": background_style,
        # ملاءمة صورة الخلفية: cover (قصّ، الافتراضي) / contain / stretch.
        "image_fit": (
            (request.form.get("image_fit") or "cover").strip().lower()
            if (request.form.get("image_fit") or "cover").strip().lower()
            in {"cover", "contain", "stretch"} else "cover"),
        # «خلفية من صورة» داخل تصميم النظام — علم صريح حتى لا تظهر صورة
        # قديمة محفوظة فجأة في قوالب رجعت لوضع النظام.
        "preset_background_image": (
            (request.form.get("preset_background_image") or "").strip().lower()
            in {"1", "true", "yes", "on"}),
        "bleed_marks": _checked("bleed_marks"),
        "show_title": _checked("show_title", True),
        "show_username": _checked("show_username", True),
        "show_password": _checked("show_password", True),
        "show_qr": _checked("show_qr", True),
        "show_price": _checked("show_price"),
        "show_hotspot": _checked("show_hotspot", True),
        "show_validity": _checked("show_validity", True),
        "show_serial": _checked("show_serial", True),
        "show_guides": _checked("show_guides"),
        "show_brand": _checked("show_brand", True),
        "credential_background_enabled": _checked("credential_background_enabled", True),
        "username_surface_enabled": _checked("username_surface_enabled", True),
        "password_surface_enabled": _checked("password_surface_enabled", True),
    }
    # Decorative pattern transparency (0..1) is only forwarded when the
    # designer actually exposes the control, so templates without it keep
    # the renderer's legacy per-pattern alpha instead of a forced overlay.
    if (request.form.get("pattern_opacity") or "").strip() != "":
        layout["pattern_opacity"] = _float("pattern_opacity", 1.0)
    uploaded_background = _uploaded_background(allow_data_url=allow_data_url_background)
    if uploaded_background:
        layout.update(uploaded_background)
        # رفع صورة في وضع «تصميم من النظام» مع علم الخلفية المخصّصة لا
        # يقلب الوضع إلى image — الصورة تصبح طبقة خلف رسوم النظام.
        if not (layout["background_style"] == "preset"
                and layout["preset_background_image"]):
            layout["background_style"] = "image"
    elif layout["background_style"] == "image" and not has_form_background_image:
        layout["background_style"] = "preset"

    # Optional logo (fully additive — absent logo leaves the layout and the
    # rendered card unchanged). The designer drops the chosen bitmap into a
    # hidden `logo_image_data_url` field (same pattern as the background
    # picker) plus optional position (mm) and size (% of card width).
    uploaded_logo = _uploaded_logo(allow_data_url=allow_data_url_background)
    if uploaded_logo:
        layout.update(uploaded_logo)
    logo_x = _float("logo_x")
    logo_y = _float("logo_y")
    logo_size = _float("logo_size_pct")
    if logo_x:
        layout["logo_x"] = logo_x
    if logo_y:
        layout["logo_y"] = logo_y
    if logo_size:
        layout["logo_size_pct"] = logo_size
    return {
        "name": request.form.get("name"),
        # Sheet placement is now an export-only setting. Keep legacy DB
        # columns populated with safe defaults, but do not read them from the
        # template form.
        "orientation": "portrait",
        "cards_per_row": 2,
        "cards_per_column": 5,
        "page_size": "A4",
        "show_qr": _checked("show_qr", True),
        "username_x": _float("username_x"),
        "username_y": _float("username_y"),
        "password_x": _float("password_x"),
        "password_y": _float("password_y"),
        "qr_x": _float("qr_x"),
        "qr_y": _float("qr_y"),
        "font_size": _int("font_size", 12),
        "color": request.form.get("color") or layout["text_color"],
        "layout": layout,
    }


def _print_settings_from_request() -> dict:
    keys = (
        "print_page_size",
        "print_orientation",
        "print_columns",
        "print_rows",
        "print_margin_mm",
        "print_margin_top_mm",
        "print_margin_right_mm",
        "print_margin_bottom_mm",
        "print_margin_left_mm",
        "print_row_gap_mm",
        "print_column_gap_mm",
        "print_fit_mode",
    )
    return {
        key: (request.values.get(key) or "").strip()
        for key in keys
        if request.values.get(key) not in (None, "")
    }


def _export_layout_overrides_from_request() -> dict:
    layout_overrides = {
        key: (request.values.get(key) or "").strip()
        for key in (
            "brand_name",
            "card_title",
            "footer_text",
            "hotspot_address",
            # رابط الدخول التلقائي: يُمرَّر كاستبدال أيضًا حتى يستطيع
            # مشغّل غرفة الطباعة تفعيل QR الدخول التلقائي وقت التصدير
            # دون الرجوع لتعديل القالب المحفوظ.
            "hotspot_login_url",
            "price_text",
            "validity_text",
        )
    }
    overrides = {key: value for key, value in layout_overrides.items() if value}
    if overrides.get("hotspot_login_url"):
        overrides["hotspot_login_url"] = _normalize_login_url(overrides["hotspot_login_url"])
    return overrides


def _batch_id_from_request() -> int | None:
    batch_id_raw = request.values.get("batch_id") or ""
    try:
        return int(batch_id_raw) if batch_id_raw else None
    except ValueError as exc:
        raise RadiusValidationError("معرّف الحزمة غير صحيح.") from exc


def _yes_no(value: object) -> str:
    return "نعم" if bool(value) else "لا"


def _preview_rows(preview: dict | None) -> list[dict]:
    if not isinstance(preview, dict):
        return []
    data = preview.get("preview")
    if not isinstance(data, dict):
        return []

    card = data.get("card") if isinstance(data.get("card"), dict) else {}
    design = data.get("design") if isinstance(data.get("design"), dict) else {}
    sample = data.get("sample") if isinstance(data.get("sample"), dict) else {}
    capabilities = data.get("capabilities") if isinstance(data.get("capabilities"), dict) else {}

    enabled_caps = [
        label
        for key, label in (
            ("sample_pdf", "PDF عينة"),
            ("batch_pdf", "PDF دفعة"),
            ("csv", "CSV"),
            ("excel", "Excel"),
            ("png", "PNG"),
        )
        if capabilities.get(key)
    ]

    return [
        {
            "label": "محرك المعاينة",
            "value": "معاينة البطاقة المرئية"
            if data.get("renderer") == "visual_card_preview"
            else str(data.get("renderer") or "غير محدد"),
        },
        {
            "label": "عدد البطاقات في الصفحة",
            "value": str(data.get("cards_per_page") or "غير محدد"),
        },
        {"label": "دعم QR", "value": _yes_no(data.get("qr_supported"))},
        {
            "label": "مقاس البطاقة",
            "value": (
                f"{card.get('width_mm') or '؟'} × {card.get('height_mm') or '؟'} مم"
            ),
        },
        {"label": "اسم الشبكة/العلامة", "value": str(design.get("brand_name") or "—")},
        {"label": "عنوان البطاقة", "value": str(design.get("card_title") or "—")},
        {"label": "نمط التصميم", "value": str(design.get("preset") or "—")},
        {"label": "عينة اسم المستخدم", "value": str(sample.get("username") or "—")},
        {"label": "مخرجات متاحة", "value": "، ".join(enabled_caps) if enabled_caps else "لا توجد"},
    ]


def _effective_field_layout(form_state: dict | None) -> dict:
    """القيم «الفعلية» لمكان وحجم اليوزر/الباس/QR كما يرسمها المحرك فعلًا.

    حقول المصمم (المكان أفقيًا/عموديًا، حجم الخط، حجم QR) كانت تبدأ بصفر
    لأن 0 يعني «الافتراضي» — بينما العنصر مرسوم فعليًا في مكانه الافتراضي.
    هنا نبني نفس نموذج العرض الذي يستخدمه المحرك الموحّد ونستخرج منه
    الإحداثيات الحقيقية (مم) والأحجام الحقيقية، فتُعبّأ الحقول من البداية
    بقيم مطابقة تمامًا لما يظهر على البطاقة.

    التحويل عكس `card_renderer._resolve_positions` نفسه:
    mm = (الإحداثي على الكانفس / عرض الكانفس) × card_width_mm — لذا إعادة
    إرسال هذه القيم للمحرك تعيد إنتاج نفس الموضع حرفيًا (لا انجراف).
    """
    fs = form_state or {}
    layout = dict(fs.get("layout") or {})
    template_for_render = {
        "id": 0,
        "username_x": fs.get("username_x") or 0,
        "username_y": fs.get("username_y") or 0,
        "password_x": fs.get("password_x") or 0,
        "password_y": fs.get("password_y") or 0,
        "qr_x": fs.get("qr_x") or 0,
        "qr_y": fs.get("qr_y") or 0,
        "layout_json": layout,
    }
    # تُستخدم القيمة في حسابات هندسية بحتة (موقع العناصر)؛ النص لا يظهر
    # في أي معاينة مرئية للمشغّل. نُبقيها على "SAMPLE" تحديدًا لأن
    # المحرك يحتسب أبعاد المستطيل من طول النص، وهذا الـsample بطول 6
    # محارف يُنتج موقعًا عند 0mm (الـfallback إلى _DEFAULT_POSITIONS).
    # تغييره يكسر شرط «الحقول الفارغة في المصمّم = 0mm» الذي يضمن
    # تطابق المعاينة مع PDF (راجع test_designer_form_defaults_match_…).
    sample = {"id": "", "username": "SAMPLE", "password": "********"}
    try:
        model = build_card_render_model(template_for_render, sample)
    except Exception:
        return {}
    canvas_w = float(model["canvas"]["width"]) or 1.0
    canvas_h = float(model["canvas"]["height"]) or 1.0
    # نفس صندوق mm الموجَّه الذي يقسم عليه المحرك (card_mm_box) — وإلا
    # انعكست القيم المعبأة بأبعاد معاكسة على المحركات العمودية.
    card_w_mm, card_h_mm = card_mm_box(layout, (canvas_w, canvas_h))

    def _round_step(value: float, step: float) -> float:
        return round(round(value / step) * step, 1)

    effective: dict[str, float] = {}
    by_id = {str(el.get("id") or ""): el for el in model.get("elements") or []}
    for element_id, prefix in (("user", "username"), ("pass", "password"), ("qr", "qr")):
        el = by_id.get(element_id)
        if not el:
            continue
        effective[f"{prefix}_x"] = _round_step((float(el["x"]) / canvas_w) * card_w_mm, 0.1)
        effective[f"{prefix}_y"] = _round_step((float(el["y"]) / canvas_h) * card_h_mm, 0.1)
    user_el = by_id.get("user")
    if user_el:
        effective["username_font_size"] = _round_step(float(user_el.get("value_font_size") or 0), 0.5)
        effective["credential_label_font_size"] = _round_step(float(user_el.get("label_font_size") or 0), 0.5)
    pass_el = by_id.get("pass")
    if pass_el:
        effective["password_font_size"] = _round_step(float(pass_el.get("value_font_size") or 0), 0.5)
    qr_el = by_id.get("qr")
    if qr_el:
        effective["qr_size_pct"] = _round_step((float(qr_el.get("size") or 0) / canvas_w) * 100.0, 0.5)
    return effective


def _page_context(
    *,
    preview: dict | None = None,
    form_state: dict | None = None,
    form_error: str | None = None,
    edit_template_id: int | None = None,
) -> dict:
    ops = get_operations_service()
    tenant_id = _tid()
    templates = ops.list_print_templates(tenant_id=tenant_id, limit=500)
    selected_edit_id = edit_template_id
    if selected_edit_id is None:
        try:
            selected_edit_id = int(request.args.get("edit_template") or 0) or None
        except (TypeError, ValueError):
            selected_edit_id = None
    if form_state is None and selected_edit_id:
        edit_template = next(
            (dict(row) for row in templates if int(row.get("id") or 0) == int(selected_edit_id)),
            None,
        )
        if edit_template:
            form_state = {**edit_template, "layout": edit_template.get("layout_json") or {}}

    # Real per-template preview thumbnails are served lazily, one small SVG
    # per template via `print_templates_thumbnail` (rendered with the SAME
    # unified renderer the PDF export uses). The picker loads them on scroll
    # (loading="lazy"), so a tenant with 100s of templates stays light.
    templates = [dict(t) for t in templates]

    return {
        "templates": templates,
        "batches": get_cards_service().list_batch_operations(limit=200, offset=0),
        "jobs": ops.list_print_jobs(tenant_id=tenant_id, limit=30),
        "presets": ops.list_print_template_presets(),
        "preview": preview,
        "preview_rows": _preview_rows(preview),
        "form_state": form_state or {},
        # القيم الفعلية لمكان/حجم اليوزر والباس وQR كما يرسمها المحرك —
        # تُستخدم لتعبئة حقول «الحقول» من البداية بدل الأصفار (راجع
        # _effective_field_layout). تُحسب لقالب التعديل أو للافتراضي.
        "effective_fields": _effective_field_layout(form_state),
        "form_error": form_error,
        "edit_template_id": selected_edit_id if form_state else None,
        # Commit 4: id of the tenant's default template (or None) so the
        # export room can auto-pick it on page load and the UI can star
        # the matching row.
        "default_template_id": ops.get_default_print_template_id(tenant_id=tenant_id),
    }


def print_templates_thumbnail(template_id: int):
    """Lazy per-template preview thumbnail: the saved template's REAL card
    rendered as SVG by the same engine the PDF export uses. Served one-by-one
    so a tenant with hundreds of templates loads them only as they scroll
    into view (loading="lazy")."""
    from flask import Response, abort
    ops = get_operations_service()
    tenant_id = _tid()
    templates = ops.list_print_templates(tenant_id=tenant_id, limit=1000)
    tpl = next(
        (dict(row) for row in templates if int(row.get("id") or 0) == int(template_id)),
        None,
    )
    if not tpl:
        abort(404)
    try:
        from ..services.card_renderer import build_card_render_model, render_card_svg
        # المصغّرة تظهر في «معرض القوالب» — قبل ربط القالب بدفعة حقيقية،
        # نلتقط أول بطاقة حقيقية للمستأجر إن وُجدت؛ وإلا «—» محايد.
        sample = _first_real_card_sample()
        layout = tpl.get("layout_json") or {}
        model = build_card_render_model({**tpl, "layout_json": layout}, sample)
        # embed_fonts=True: المصغّرة تُعرض داخل <img> حيث لا تصل خطوط
        # الصفحة (Almarai) إلى الـSVG — يجب تضمين الخط داخل الملف نفسه
        # كـdata: URI وإلا سقط النص إلى Tahoma/خط النظام.
        svg = render_card_svg(model, mask_password=True, embed_fonts=True)
    except Exception:
        abort(404)
    resp = Response(svg, mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "private, max-age=600"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def print_templates():
    return render_template("radius/print_templates.html", **_page_context())


def print_templates_stats():
    """صفحة الإحصائيات المنفصلة لقوالب الطباعة.

    تعرض كل الجداول التي كانت تزاحم غرفتي الطباعة والتصميم في الصفحة
    الرئيسية: جدول القوالب المحفوظة (مع إجراءاتها الأصلية) + سجل
    عمليات التصدير، إضافة إلى مؤشرات سريعة محسوبة من نفس البيانات.
    لا منطق جديد هنا — نفس مصادر البيانات المستخدمة في الصفحة الرئيسية.
    """
    ops = get_operations_service()
    tenant_id = _tid()
    templates = [dict(t) for t in ops.list_print_templates(tenant_id=tenant_id, limit=500)]
    jobs = ops.list_print_jobs(tenant_id=tenant_id, limit=200)
    # مؤشرات بسيطة محسوبة من السجل نفسه (لا استعلامات إضافية)
    success_jobs = sum(1 for j in jobs if (j.get("status") or "") == "success")
    failed_jobs = sum(1 for j in jobs if (j.get("status") or "") == "failed")
    exported_cards = sum(int(j.get("card_count") or 0) for j in jobs if (j.get("status") or "") == "success")
    return render_template(
        "radius/print_templates_stats.html",
        templates=templates,
        jobs=jobs,
        default_template_id=ops.get_default_print_template_id(tenant_id=tenant_id),
        stats={
            "templates": len(templates),
            "jobs": len(jobs),
            "success_jobs": success_jobs,
            "failed_jobs": failed_jobs,
            "exported_cards": exported_cards,
        },
    )


def print_templates_export_center():
    # The standalone export center has been merged into /print-templates
    # (Commit 3: single 3-column page). Keep the legacy URL alive as a
    # 302 to the #export anchor so bookmarks and links from
    # cards_batches keep working.
    return redirect(url_for("radius.print_templates") + "#export")


def print_templates_create():
    payload = None
    try:
        payload = _payload()
        get_operations_service().create_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            data=payload,
        )
        flash("تم حفظ قالب التصميم. يمكنك الآن تصدير PDF عينة أو ربطه بحزمة بطاقات فعلية.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
        return render_template(
            "radius/print_templates.html",
            **_page_context(form_state=payload or {}, form_error=exc.message),
        ), 400
    return redirect(url_for("radius.print_templates"))


def print_templates_update(template_id: int):
    payload = None
    try:
        payload = _payload()
        get_operations_service().update_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            template_id=template_id,
            data=payload,
        )
        flash("تم تحديث قالب التصميم.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
        return render_template(
            "radius/print_templates.html",
            **_page_context(
                form_state=payload or {},
                form_error=exc.message,
                edit_template_id=template_id,
            ),
        ), 400
    return redirect(url_for("radius.print_templates", edit_template=template_id) + "#designer")


def print_templates_preview(template_id: int):
    try:
        # العيّنة: اسم المستخدم المُدخل يدويًا له الأولوية، ثم أول بطاقة
        # حقيقية للمستأجر إن وُجدت، وإلا «—» بدلًا من سلاسل وهمية ثابتة.
        _typed = (request.form.get("sample_username") or "").strip()
        _real = _first_real_card_sample()
        _username = _typed or _real["username"]
        preview = get_operations_service().render_print_template_preview(
            tenant_id=_tid(),
            template_id=template_id,
            sample={
                "username": _username,
                "has_password": True,
                "qr_payload": _username,
            },
        )
        return render_template("radius/print_templates.html", **_page_context(preview=preview))
    except RadiusError as exc:
        flash(exc.message, "error")
    return redirect(url_for("radius.print_templates"))


_PREVIEW_FRAGMENT_OVERRIDE_KEYS = (
    "brand_name",
    "card_title",
    "footer_text",
    "hotspot_address",
    # رابط الدخول التلقائي: تمريره للمعاينة الحية في غرفة الطباعة حتى
    # يعكس رمز QR في المعاينة نفس ما سيخرج في ملف PDF المصدَّر.
    "hotspot_login_url",
    "price_text",
    "validity_text",
)


def print_templates_designer_svg():
    """Live designer preview as inline SVG.

    Posted to from the form editor's `input` listener: takes the
    full current form state, builds the same render model the PDF
    export uses, and returns a single `<svg>` element. The browser
    drops it into the designer canvas via `innerHTML`.

    This replaces the legacy hand-written `.pr-card-preview` HTML
    that drifted from the PDF — same SVG renderer, no second layout.

    The bg-image picker fills a hidden `background_image_data_url`
    field directly so the SVG preview can show the bitmap without
    re-uploading the file on every keystroke.
    """
    layout = _payload(allow_data_url_background=False)["layout"]
    bg_data_url = (request.form.get("background_image_data_url") or "").strip()
    if layout.get("background_style") == "image" and bg_data_url.startswith("data:image/"):
        layout = {**layout, "background_image_data_url": bg_data_url, "background_style": "image"}
    elif layout.get("preset_background_image") and bg_data_url.startswith("data:image/"):
        # «خلفية من صورة» داخل تصميم النظام: مرّر الصورة للمعاينة الحية
        # مع بقاء الوضع preset — تُرسم خلف رسوم النظام.
        layout = {**layout, "background_image_data_url": bg_data_url}
    # Same trick for the logo: the live preview posts the chosen bitmap in a
    # hidden field, so inject it straight into the layout (the optimizer is
    # skipped on the per-keystroke path) so the logo shows without saving.
    logo_data_url = (request.form.get("logo_image_data_url") or "").strip()
    if logo_data_url.startswith("data:image/"):
        layout = {**layout, "logo_image_data_url": logo_data_url}
    # The renderer's `_resolve_positions` reads top-level
    # `username_x` / `username_y` / `password_x` / `password_y` /
    # `qr_x` / `qr_y` from the template row, not the layout dict —
    # mirror them at top level so a freshly-dragged pill survives the
    # round-trip without saving the template first.
    template_for_render = {
        "id": 0,
        "username_x": _float("username_x", 0),
        "username_y": _float("username_y", 0),
        "password_x": _float("password_x", 0),
        "password_y": _float("password_y", 0),
        "qr_x": _float("qr_x", 0),
        "qr_y": _float("qr_y", 0),
        "layout_json": layout,
    }
    # المصمم الحي: إن لم يكتب المشغّل اسمًا، التقط أول بطاقة حقيقية
    # للمستأجر إن وُجدت، وإلا «—» محايد بدل سلسلة وهمية ثابتة.
    _typed = (request.form.get("sample_username") or "").strip()
    _real = _first_real_card_sample()
    sample = {
        "id": "",
        "username": _typed or _real["username"],
        # The SVG adapter masks the password automatically — we still
        # pass a non-empty placeholder so the PASS pill renders.
        "password": "********",
    }
    from ..services.card_renderer import (
        build_card_render_model,
        render_card_svg,
    )

    model = build_card_render_model(template_for_render, sample)
    svg = render_card_svg(model, mask_password=True)
    response = Response(svg, mimetype="image/svg+xml")
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def print_templates_preview_fragment(template_id: int):
    """Live mini-preview for the export center: renders up to 4 real cards
    from the chosen batch using the chosen template's layout. Returns an
    HTML fragment, not a full page — the export UI swaps it into a
    placeholder via fetch(). Password is never included; the fragment
    masks it to ••••••••, same as the live designer canvas.
    """
    ops = get_operations_service()
    template = None
    try:
        templates = ops.list_print_templates(tenant_id=_tid(), limit=10_000)
        for row in templates:
            if int(row.get("id") or 0) == int(template_id):
                template = row
                break
    except Exception:  # pragma: no cover — defensive
        template = None

    batch = None
    cards: list = []
    error: str | None = None

    batch_id_raw = request.args.get("batch_id") or ""
    try:
        batch_id = int(batch_id_raw) if batch_id_raw else None
    except ValueError:
        batch_id = None
        error = "معرّف الحزمة غير صحيح."

    if template is None:
        error = error or "القالب غير موجود."
    elif batch_id is not None:
        try:
            cards_service = get_cards_service()
            batch_obj = cards_service._store.get_batch(batch_id)
            if batch_obj is None:
                error = "الحزمة غير موجودة."
            else:
                # CardBatch is a dataclass; the template only reads a few
                # attributes so we expose it as a dict for simpler Jinja.
                batch = {
                    "id": getattr(batch_obj, "id", batch_id),
                    "batch_name": getattr(batch_obj, "batch_name", "") or "",
                    "count_to_make": getattr(batch_obj, "count_to_make", 0) or 0,
                    "created_count": getattr(batch_obj, "created_count", 0) or 0,
                }
                cards = cards_service.list_cards(batch_id=batch_id, limit=4, offset=0)
        except RadiusError as exc:
            error = exc.message
        except Exception as exc:  # pragma: no cover — defensive
            error = str(exc) or "تعذّر جلب بطاقات الحزمة."

    overrides = {
        key: (request.args.get(key) or "").strip()
        for key in _PREVIEW_FRAGMENT_OVERRIDE_KEYS
    }
    overrides = {k: v for k, v in overrides.items() if v}
    if overrides.get("hotspot_login_url"):
        # نفس تطبيع التصدير: 10.10.10.10 ← http://10.10.10.10
        overrides["hotspot_login_url"] = _normalize_login_url(overrides["hotspot_login_url"])

    # Build SVG strings via the unified renderer — single source of truth
    # shared with the PDF adapter. The route does this server-side so the
    # partial stays markup-only.
    card_svgs: list[dict] = []
    if template is not None and error is None:
        if cards:
            for card in cards:
                model = build_card_render_model(template, card, overrides=overrides)
                card_svgs.append({
                    "card": card,
                    "svg": render_card_svg(model, mask_password=True),
                })
        else:
            # No batch chosen yet — show a single sample card so the user
            # can still see what the template looks like with real layout.
            model = build_card_render_model(template, None, overrides=overrides)
            card_svgs.append({
                "card": None,
                "svg": render_card_svg(model, mask_password=True),
            })

    return render_template(
        "radius/_print_template_preview_fragment.html",
        template=template,
        batch=batch,
        cards=cards,
        card_svgs=card_svgs,
        overrides=overrides,
        error=error,
    )


def print_templates_delete(template_id: int):
    try:
        get_operations_service().delete_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            template_id=template_id,
        )
        flash("تم حذف القالب.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
    next_url = request.form.get("next") or url_for("radius.print_templates")
    return redirect(next_url)


def print_templates_set_default(template_id: int):
    try:
        get_operations_service().set_default_print_template(
            tenant_id=_tid(),
            actor=_actor(),
            template_id=template_id,
        )
        flash("تم اعتماد هذا القالب كقالب افتراضي.", "success")
    except RadiusError as exc:
        flash(exc.message, "error")
    next_url = request.form.get("next") or (
        url_for("radius.print_templates") + "#export"
    )
    return redirect(next_url)


def print_templates_cleanup_fixtures():
    try:
        purged = get_operations_service().purge_test_fixture_print_templates(
            tenant_id=_tid(),
            actor=_actor(),
        )
    except RadiusError as exc:
        flash(exc.message, "error")
        return redirect(url_for("radius.print_templates"))
    if purged:
        flash(
            f"تم تنظيف {len(purged)} قالب اختبار من القائمة.",
            "success",
        )
    else:
        flash("لا توجد قوالب اختبار للتنظيف.", "info")
    next_url = request.form.get("next") or url_for("radius.print_templates")
    return redirect(next_url)


def print_templates_export_pdf(template_id: int):
    try:
        batch_id = _batch_id_from_request()
        # عند تصدير PDF بلا دفعة: لا نستخدم سلسلة وهمية ثابتة. الإدخال
        # اليدوي له الأولوية، ثم أول بطاقة حقيقية، وإلا «—» محايد.
        _typed_u = (request.args.get("sample_username") or "").strip()
        _typed_p = (request.args.get("sample_password") or "").strip()
        _real = _first_real_card_sample()
        _u = _typed_u or _real["username"]
        payload = get_operations_service().export_print_template_pdf(
            tenant_id=_tid(),
            template_id=template_id,
            sample={
                "username": _u,
                "password": _typed_p or "********",
                "qr_payload": _u,
            },
            batch_id=batch_id,
            layout_overrides=_export_layout_overrides_from_request(),
            print_settings=_print_settings_from_request(),
            scope=(request.values.get("scope") or "all"),
            actor=_actor(),
        )
    except RadiusError as exc:
        flash(exc.message, "error")
        return redirect(url_for("radius.print_templates"))
    suffix = f"batch-{batch_id}" if batch_id else f"template-{template_id}"
    return Response(
        payload,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="cards-{suffix}.pdf"'},
    )


def print_templates_export_job_start(template_id: int):
    try:
        batch_id = _batch_id_from_request()
        # نفس قاعدة export_pdf: لا سلاسل وهمية. المُدخل اليدوي ثم أول
        # بطاقة حقيقية للمستأجر إن وُجدت، وإلا «—» محايد.
        _typed_u = (request.values.get("sample_username") or "").strip()
        _typed_p = (request.values.get("sample_password") or "").strip()
        _real = _first_real_card_sample()
        _u = _typed_u or _real["username"]
        job = get_operations_service().start_print_template_export_job(
            tenant_id=_tid(),
            template_id=template_id,
            sample={
                "username": _u,
                "password": _typed_p or "********",
                "qr_payload": _u,
            },
            batch_id=batch_id,
            layout_overrides=_export_layout_overrides_from_request(),
            print_settings=_print_settings_from_request(),
            scope=(request.values.get("scope") or "all"),
            actor=_actor(),
        )
    except RadiusError as exc:
        return jsonify({"ok": False, "error": {"message": exc.message, "code": exc.code}}), exc.http_status
    return jsonify({
        "ok": True,
        "job": _job_payload(job),
        "status_url": url_for("radius.print_templates_export_job_status", job_id=job["id"]),
        "download_url": url_for("radius.print_templates_export_job_download", job_id=job["id"]),
    }), 202


def _job_payload(job: dict) -> dict:
    metadata = job.get("metadata_json") if isinstance(job.get("metadata_json"), dict) else {}
    download_ready = bool(metadata.get("download_ready")) and job.get("status") == "success"
    status = job.get("status")
    if status == "success" and not download_ready:
        # The renderer can finish PDF bytes before the async wrapper writes the
        # downloadable file. Keep polling in a finalizing state until the file
        # path is actually persisted.
        status = "finalizing"
    return {
        "id": job.get("id"),
        "status": status,
        "template_id": job.get("template_id"),
        "batch_id": job.get("batch_id"),
        "card_count": job.get("card_count") or 0,
        "file_name": job.get("file_name") or "",
        "message": job.get("message") or "",
        "progress": int(metadata.get("progress") or (100 if job.get("status") in {"success", "failed"} else 0)),
        "stage": metadata.get("stage") or job.get("status"),
        "stage_label": metadata.get("stage_label") or job.get("message") or "",
        "rendered_cards": int(metadata.get("rendered_cards") or 0),
        "total_cards": int(metadata.get("total_cards") or job.get("card_count") or 0),
        "download_ready": download_ready,
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
    }


def print_templates_export_job_status(job_id: int):
    try:
        job = get_operations_service().get_print_job(tenant_id=_tid(), job_id=job_id)
    except RadiusError as exc:
        return jsonify({"ok": False, "error": {"message": exc.message, "code": exc.code}}), exc.http_status
    return jsonify({
        "ok": True,
        "job": _job_payload(job),
        "download_url": url_for("radius.print_templates_export_job_download", job_id=job_id),
    })


def print_templates_export_job_download(job_id: int):
    try:
        payload, file_name = get_operations_service().get_print_job_file(
            tenant_id=_tid(),
            job_id=job_id,
        )
    except RadiusError as exc:
        return jsonify({"ok": False, "error": {"message": exc.message, "code": exc.code}}), exc.http_status
    return Response(
        payload,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )
