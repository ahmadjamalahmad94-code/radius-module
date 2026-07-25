"""Operational ISP foundations: distributors, schedules, printing, backups."""
from __future__ import annotations

import re
import sqlite3
import os
from ..core import env_settings
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

from ..core.errors import RadiusNotFound, RadiusValidationError
from ..core.system_config import default_currency
from ..db.connection import close_thread_conn, db, db_path
from ..db.repos import cards_repo, operations_repo, plans_repo, subscribers_repo
from .audit import RadiusAuditService
from . import backup_compression as _bkz

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_SERVICE_SCOPES = {"hotspot", "broadband", "both"}
_SESSION_FROZEN_STATUSES = {"disabled", "suspended", "frozen", "banned"}
_PRINT_ORIENTATIONS = {"portrait", "landscape"}
_PRINT_EXPORT_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="print-export")
_PRINT_EXPORT_LOCK = threading.Lock()
_PRINT_PRESETS: dict[str, dict[str, Any]] = {
    "modern": {
        "label": "حديث",
        "gradient_start": "#0f172a",
        "gradient_end": "#22a7bd",
        "accent_color": "#f59e0b",
        "text_color": "#ffffff",
        "surface_color": "#e8f7fb",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة إنترنت",
        "footer_text": "احتفظ ببيانات الدخول حتى انتهاء الصلاحية",
    },
    "dark": {
        "label": "داكن احترافي",
        "gradient_start": "#111827",
        "gradient_end": "#334155",
        "accent_color": "#38bdf8",
        "text_color": "#ffffff",
        "surface_color": "#dbeafe",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "قسيمة هوتسبوت",
        "footer_text": "الدعم الفني متوفر من نقطة البيع",
    },
    "gold": {
        "label": "ذهبي",
        "gradient_start": "#3b2f1c",
        "gradient_end": "#b7791f",
        "accent_color": "#facc15",
        "text_color": "#fff7ed",
        "surface_color": "#fff7d6",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة مميزة",
        "footer_text": "سرعة ثابتة وتجربة أفضل",
    },
    "minimal": {
        "label": "بسيط",
        "gradient_start": "#ffffff",
        "gradient_end": "#f8fafc",
        "accent_color": "#0ea5e9",
        "text_color": "#0f172a",
        "surface_color": "#eff6ff",
        "qr_style": "clean",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة دخول",
        "footer_text": "استخدم اسم المستخدم وكلمة المرور مرة واحدة",
    },
    "telecom": {
        "label": "اتصالات",
        "gradient_start": "#083344",
        "gradient_end": "#0891b2",
        "accent_color": "#67e8f9",
        "text_color": "#ecfeff",
        "surface_color": "#cffafe",
        "qr_style": "rounded",
        "brand_name": "HobeRadius",
        "card_title": "دخول واي فاي",
        "footer_text": "امسح رمز QR أو أدخل البيانات يدويًا",
    },
    "neon": {
        "label": "نيون",
        "gradient_start": "#240046",
        "gradient_end": "#00b4d8",
        "accent_color": "#c8ff00",
        "text_color": "#ffffff",
        "surface_color": "#e0f2fe",
        "qr_style": "boxed",
        "brand_name": "HobeRadius",
        "card_title": "بطاقة سرعة",
        "footer_text": "سلّم هذه البطاقة للعميل بعد الدفع",
    },
    "aurora": {
        "label": "شفق",
        "gradient_start": "#172554",
        "gradient_end": "#14b8a6",
        "accent_color": "#f472b6",
        "text_color": "#ffffff",
        "surface_color": "#ecfeff",
        "qr_style": "rounded",
        "brand_name": "HobeRadius",
        "card_title": "دخول واي فاي ذكي",
        "footer_text": "امسح الرمز واتصل واستمتع بخدمة مستقرة",
    },
    "fiber": {
        "label": "فايبر احترافي",
        "gradient_start": "#020617",
        "gradient_end": "#2563eb",
        "accent_color": "#38bdf8",
        "text_color": "#ffffff",
        "surface_color": "#dbeafe",
        "qr_style": "boxed",
        "brand_name": "HobeRadius Fiber",
        "card_title": "دخول فايبر",
        "footer_text": "بطاقة دخول بسرعة عالية",
    },
    "sunset": {
        "label": "غروب",
        "gradient_start": "#7c2d12",
        "gradient_end": "#db2777",
        "accent_color": "#fde047",
        "text_color": "#fff7ed",
        "surface_color": "#ffedd5",
        "qr_style": "rounded",
        "brand_name": "HobeRadius",
        "card_title": "قسيمة ذهبية",
        "footer_text": "احتفظ بهذه البطاقة حتى انتهاء الاشتراك",
    },
    "matrix": {
        "label": "شبكة",
        "gradient_start": "#022c22",
        "gradient_end": "#0f172a",
        "accent_color": "#22c55e",
        "text_color": "#dcfce7",
        "surface_color": "#d1fae5",
        "qr_style": "clean",
        "brand_name": "HobeRadius",
        "card_title": "رمز دخول",
        "footer_text": "رمز دخول آمن لبوابة الهوتسبوت",
    },
}
# مكتبة «القوالب الجاهزة» الموسّعة (قوالب حسب نوع المنشأة والنمط) —
# تُدمج هنا فتظهر تلقائيًّا في المعرض وتمرّ بنفس مسارات المعاينة/التصدير.
# الوحدة بيانات نقيّة لا تستورد operations فلا دورة استيراد.
from .card_template_gallery import GALLERY_PRESETS as _GALLERY_PRESETS  # noqa: E402

for _gk, _gv in _GALLERY_PRESETS.items():
    _PRINT_PRESETS.setdefault(_gk, _gv)

_PRINT_BOOL_FIELDS = {
    "show_username",
    "show_password",
    "show_price",
    "show_hotspot",
    "show_validity",
    "show_serial",
    "show_guides",
    "show_brand",
    "show_title",
}


def _int_field(data: dict, key: str, *, minimum: int = 0, default: int = 0) -> int:
    raw = data.get(key, default)
    if raw in (None, ""):
        raw = default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{key} must be integer")
    if value < minimum:
        raise RadiusValidationError(f"{key} must be >= {minimum}")
    return value


def _float_field(data: dict, key: str, *, minimum: float = 0.0,
                 default: float = 0.0) -> float:
    raw = data.get(key, default)
    if raw in (None, ""):
        raw = default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise RadiusValidationError(f"{key} must be numeric")
    if value < minimum:
        raise RadiusValidationError(f"{key} must be >= {minimum:g}")
    return value


def _optional_int_field(
    data: dict,
    key: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
    default: int = 0,
) -> int:
    value = _int_field(data, key, minimum=minimum, default=default)
    if maximum is not None and value > maximum:
        raise RadiusValidationError(f"{key} must be <= {maximum}")
    return value


def _optional_float_field(
    data: dict,
    key: str,
    *,
    minimum: float = 0.0,
    maximum: float | None = None,
    default: float = 0.0,
) -> float:
    value = _float_field(data, key, minimum=minimum, default=default)
    if maximum is not None and value > maximum:
        raise RadiusValidationError(f"{key} must be <= {maximum:g}")
    return value


def _safe_hex(value: Any, default: str) -> str:
    raw = str(value or default).strip()
    if re.fullmatch(r"#?[0-9a-fA-F]{6}", raw):
        return raw if raw.startswith("#") else f"#{raw}"
    return default


def _normalize_autologin_url(value: str) -> str:
    """يطبّع رابط دخول الهوت سبوت قبل تخزينه داخل layout_json.

    المستخدم يكتب غالبًا عنوان IP مجرّدًا (10.10.10.10) — نضيف
    http:// حتى يُخزَّن الرابط بصيغة قياسية واحدة يعتمد عليها مولّد
    QR في كل مسارات التصيير. الفارغ يبقى فارغًا (سلوك القوالب القديمة).
    """
    value = (value or "").strip()
    if not value:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        value = "http://" + value
    return value


def _boolish(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _template_layout(data: dict) -> dict:
    layout = data.get("layout") if isinstance(data.get("layout"), dict) else {}
    merged = {**layout, **data}
    preset_name = str(merged.get("design_preset") or "modern").strip()
    if preset_name not in _PRINT_PRESETS:
        preset_name = "modern"
    preset = _PRINT_PRESETS[preset_name]

    def _text(key: str, default: str = "", max_len: int = 140) -> str:
        return str(merged.get(key) or default).strip()[:max_len]

    image_data_url = _text("background_image_data_url", "", 2_100_000)
    raw_background_style = _text("background_style", "", 30).lower()
    if raw_background_style in {"image", "stored_image", "photo", "upload", "uploaded"}:
        background_style = "image"
    elif raw_background_style in {"preset", "system", "graphics", "generated"}:
        background_style = "preset"
    elif raw_background_style == "gradient":
        background_style = "image" if image_data_url.startswith("data:image/") else "preset"
    else:
        background_style = "image" if image_data_url.startswith("data:image/") else "preset"
    if background_style == "image" and not image_data_url.startswith("data:image/"):
        background_style = "preset"

    normalized = {
        **layout,
        "preview_mode": "visual_design_room",
        "design_preset": preset_name,
        "card_width_mm": _float_field(merged, "card_width_mm", minimum=1, default=85),
        "card_height_mm": _float_field(merged, "card_height_mm", minimum=1, default=54),
        "card_orientation": _text("card_orientation", "horizontal", 20),
        "gradient_start": _safe_hex(merged.get("gradient_start"), preset["gradient_start"]),
        "gradient_end": _safe_hex(merged.get("gradient_end"), preset["gradient_end"]),
        "accent_color": _safe_hex(merged.get("accent_color"), preset["accent_color"]),
        "text_color": _safe_hex(merged.get("text_color") or merged.get("color"), preset["text_color"]),
        "surface_color": _safe_hex(merged.get("surface_color"), preset["surface_color"]),
        # Data-strip / pill transparency (0..1). Default 0.95 matches the
        # renderer so existing templates are visually unchanged.
        "surface_opacity": max(0, min(1, _float_field(merged, "surface_opacity", minimum=0, default=0.95))),
        "credential_text_color": _safe_hex(merged.get("credential_text_color"), "#0f172a"),
        "credential_label_color": _safe_hex(merged.get("credential_label_color"), "#64748b"),
        "username_surface_color": _safe_hex(merged.get("username_surface_color"), _safe_hex(merged.get("surface_color"), preset["surface_color"])),
        "password_surface_color": _safe_hex(merged.get("password_surface_color"), _safe_hex(merged.get("surface_color"), preset["surface_color"])),
        "username_font_size": _optional_float_field(merged, "username_font_size", minimum=0, maximum=120, default=0),
        "password_font_size": _optional_float_field(merged, "password_font_size", minimum=0, maximum=120, default=0),
        "credential_label_font_size": _optional_float_field(merged, "credential_label_font_size", minimum=0, maximum=80, default=0),
        "qr_color": _safe_hex(merged.get("qr_color"), "#0f172a"),
        "qr_background_color": _safe_hex(merged.get("qr_background_color"), "#ffffff"),
        "qr_size_pct": _optional_float_field(merged, "qr_size_pct", minimum=0, maximum=48, default=0),
        "pattern_style": _text("pattern_style", str(preset.get("pattern_style", "signal")), 30),
        # Decorative line/grid/signal/circle colour. Default white keeps the
        # legacy look for templates that never set it.
        "pattern_color": _safe_hex(merged.get("pattern_color"), "#ffffff"),
        "image_opacity": max(0, min(1, _float_field(merged, "image_opacity", minimum=0, default=0.82))),
        "qr_style": _text("qr_style", preset["qr_style"], 30),
        "brand_name": _text("brand_name", preset["brand_name"], 80),
        "card_title": _text("card_title", preset["card_title"], 80),
        "footer_text": _text("footer_text", preset["footer_text"], 180),
        "hotspot_address": _text("hotspot_address", "hotspot.local", 120),
        # رابط الدخول التلقائي للهوت سبوت (DNS) — فارغ افتراضيًا حتى
        # لا يتغير سلوك القوالب القديمة. يُطبَّع عند الحفظ: عنوان IP
        # مجرّد (10.10.10.10) يُحفَظ http://10.10.10.10 حتى يقرأه كل
        # مسارات التصيير (معاينة غرفة الطباعة / PDF العينة / مهام
        # التصدير) بنفس الصيغة التي تعرضها معاينة غرفة التصميم.
        "hotspot_login_url": _normalize_autologin_url(_text("hotspot_login_url", "", 200)),
        "price_text": _text("price_text", "", 60),
        "validity_text": _text("validity_text", "", 60),
        "instructions_text": _text(
            "instructions_text",
            "استخدم اسم المستخدم وكلمة المرور أو رمز QR لتسجيل الدخول.",
            180,
        ),
        "background_style": background_style,
        # «خلفية من صورة» داخل تصميم النظام (علم صريح — انظر card_renderer).
        "preset_background_image": _boolish(
            merged.get("preset_background_image"), False),
        "background_image_data_url": image_data_url,
        "background_image_name": _text("background_image_name", "", 140),
        "background_image_mime": _text("background_image_mime", "", 60),
        "bleed_marks": _boolish(merged.get("bleed_marks"), False),
        # رَمز قِطاعي + علامة مائيّة (يونيو 2026):
        #   icon              — مفتاح motif (coffee/medical/wifi/...). يأتي
        #                       من الـpreset؛ يَسقط لـvertical→motif لو غاب.
        #   brand_icon_enabled — هل تَرسم الرَمز الصَغير بِجانب الـbrand؟
        #                       *مَوقوف افتراضيًّا* (تَنقيح المالك، يونيو 2026):
        #                       «دفش ومبالغ فيه» — العَلامة المائيّة وحدها
        #                       تُوصل بَصمة القِطاع بأناقة، بلا icon بارز.
        #                       يَبقى toggle اختياريّ في المُصمِّم لمن يُريده.
        #   watermark_enabled — هل تَرسم الخَلفيّة النَمطيّة القِطاعيّة؟
        #   watermark_opacity — شَفافيّتها (0..0.40)، افتراضي 0.30 =
        #                       النَمط مَرئيّ بِوضوح كامل كَخَلفيّة دون
        #                       مُنازَعَة للـpills/QR (opaque فَوقه).
        #                       الرِحلة: 0.04 single → 0.06 seamless v1 →
        #                       0.15 seamless v2 → 0.30 (تَنقيح المالك:
        #                       «خَلّيه واضح، 30٪»). clamp 0.40 يَترك
        #                       هَامش لمَن يُريد بارزًا أكثر دون فَوضى.
        "icon": _text("icon", str(preset.get("icon", "wifi")), 30),
        "brand_icon_enabled": _boolish(
            merged.get("brand_icon_enabled"), False),
        "watermark_enabled": _boolish(merged.get("watermark_enabled"), True),
        "watermark_opacity": max(0.0, min(0.40, _float_field(
            merged, "watermark_opacity", minimum=0, default=0.30))),
    }
    defaults = {
        "show_username": True,
        "show_password": True,
        "show_price": False,
        "show_hotspot": True,
        "show_validity": True,
        "show_serial": True,
        "show_guides": False,
        "show_brand": True,
        "show_title": True,
        "credential_background_enabled": True,
        "username_surface_enabled": True,
        "password_surface_enabled": True,
    }
    for key, default in defaults.items():
        normalized[key] = _boolish(merged.get(key), default)
    # Decorative pattern transparency (0..1). Only persisted when the
    # incoming form/layout actually carries it, so templates that predate
    # the control keep the renderer's legacy per-pattern alpha instead of a
    # forced opaque overlay. None ⇒ "use legacy" in card_renderer.
    raw_pattern_opacity = merged.get("pattern_opacity")
    if raw_pattern_opacity is not None and str(raw_pattern_opacity).strip() != "":
        normalized["pattern_opacity"] = max(
            0, min(1, _float_field(merged, "pattern_opacity", minimum=0, default=1.0))
        )
    if normalized["card_orientation"] == "vertical" and normalized["card_width_mm"] > normalized["card_height_mm"]:
        normalized["card_width_mm"], normalized["card_height_mm"] = (
            normalized["card_height_mm"],
            normalized["card_width_mm"],
        )
    return normalized


def _print_sheet_settings(settings: Optional[dict]) -> dict:
    """Normalize page imposition settings for export only.

    These values describe how completed card snapshots are placed on a
    printable sheet. They are intentionally separate from template design
    fields, so changing rows, columns, gaps, or page margins never mutates the
    saved card design.
    """
    raw = settings or {}
    page_size = str(raw.get("print_page_size") or raw.get("page_size") or "A4").strip()
    if page_size.lower() not in {"a4", "letter"}:
        raise RadiusValidationError("print_page_size must be A4 or Letter")
    orientation = str(
        raw.get("print_orientation") or raw.get("orientation") or "portrait"
    ).strip().lower()
    if orientation not in _PRINT_ORIENTATIONS:
        raise RadiusValidationError("print_orientation must be portrait or landscape")
    margin_default = _optional_float_field(
        raw, "print_margin_mm", minimum=0, maximum=80, default=10
    )
    return {
        "page_size": "Letter" if page_size.lower() == "letter" else "A4",
        "orientation": orientation,
        "columns": _optional_int_field(
            raw, "print_columns", minimum=1, maximum=12, default=2
        ),
        "rows": _optional_int_field(
            raw, "print_rows", minimum=1, maximum=20, default=5
        ),
        "margin_top_mm": _optional_float_field(
            raw, "print_margin_top_mm", minimum=0, maximum=80, default=margin_default
        ),
        "margin_right_mm": _optional_float_field(
            raw, "print_margin_right_mm", minimum=0, maximum=80, default=margin_default
        ),
        "margin_bottom_mm": _optional_float_field(
            raw, "print_margin_bottom_mm", minimum=0, maximum=80, default=margin_default
        ),
        "margin_left_mm": _optional_float_field(
            raw, "print_margin_left_mm", minimum=0, maximum=80, default=margin_default
        ),
        "row_gap_mm": _optional_float_field(
            raw, "print_row_gap_mm", minimum=0, maximum=60, default=4
        ),
        "column_gap_mm": _optional_float_field(
            raw, "print_column_gap_mm", minimum=0, maximum=60, default=4
        ),
    }


def _strict_print_geometry(*, page_width: float, page_height: float,
                           canvas_width: float, canvas_height: float,
                           sheet: dict, unit: float) -> dict:
    """Calculate strict visible-card placement for a print sheet.

    Rows, columns, gaps, and margins describe the finished card edge, not a
    larger slot that later centers the card. This keeps row gaps and page
    margins visually identical to the operator's print settings while the
    card itself remains a single uniformly-scaled snapshot.
    """
    rows = int(sheet["rows"])
    cols = int(sheet["columns"])
    margin_top = float(sheet["margin_top_mm"]) * unit
    margin_right = float(sheet["margin_right_mm"]) * unit
    margin_bottom = float(sheet["margin_bottom_mm"]) * unit
    margin_left = float(sheet["margin_left_mm"]) * unit
    row_gap = float(sheet["row_gap_mm"]) * unit
    column_gap = float(sheet["column_gap_mm"]) * unit

    available_width = page_width - margin_left - margin_right - (column_gap * (cols - 1))
    available_height = page_height - margin_top - margin_bottom - (row_gap * (rows - 1))
    if available_width <= 0 or available_height <= 0:
        raise RadiusValidationError("print settings leave no printable area")

    aspect = float(canvas_width) / max(float(canvas_height), 1.0)
    max_card_width = available_width / cols
    max_card_height = available_height / rows
    if max_card_width <= 0 or max_card_height <= 0:
        raise RadiusValidationError("print settings leave no card area")

    if (max_card_width / max_card_height) > aspect:
        card_height = max_card_height
        card_width = card_height * aspect
        fit_limited_by = "height"
    else:
        card_width = max_card_width
        card_height = card_width / aspect
        fit_limited_by = "width"

    positions = []
    for row in range(rows):
        for col in range(cols):
            positions.append({
                "row": row,
                "col": col,
                "x": margin_left + col * (card_width + column_gap),
                "y": page_height - margin_top - card_height - row * (card_height + row_gap),
            })

    return {
        "rows": rows,
        "columns": cols,
        "card_width": card_width,
        "card_height": card_height,
        "positions": positions,
        "cards_per_page": rows * cols,
        "fit_limited_by": fit_limited_by,
        "margins": {
            "top": margin_top,
            "right": margin_right,
            "bottom": margin_bottom,
            "left": margin_left,
        },
        "gaps": {
            "row": row_gap,
            "column": column_gap,
        },
    }


def _print_presets_list() -> list[dict]:
    return [
        {"key": key, "label": value["label"], "layout": {**value, "design_preset": key}}
        for key, value in _PRINT_PRESETS.items()
    ]


def validate_service_scope(value: str) -> str:
    scope = (value or "both").strip().lower()
    if scope not in _SERVICE_SCOPES:
        raise RadiusValidationError(
            "service_scope must be one of hotspot, broadband, both"
        )
    return scope


def _validate_time(value: str, field: str) -> str:
    raw = (value or "").strip()
    if not _TIME_RE.match(raw):
        raise RadiusValidationError("يجب إدخال الوقت بصيغة ساعة:دقيقة (HH:MM)")
    hour, minute = [int(part) for part in raw.split(":", 1)]
    if hour > 23 or minute > 59:
        raise RadiusValidationError("الوقت المُدخل غير صالح")
    return raw


def _rate_limit_from_schedule(schedule: dict | None) -> str:
    schedule = schedule or {}
    up = int(schedule.get("speed_up_kbps") or 0)
    down = int(schedule.get("speed_down_kbps") or 0)
    return f"{up}k/{down}k"


def classify_online_state(*, account_status: str = "",
                          expire_at: Any = None,
                          is_online: bool = True) -> dict:
    """Normalize UI-facing live states without mutating RADIUS sessions."""
    if not is_online:
        return {"state": "disconnected", "state_label": "disconnected", "state_color": "gray"}
    status = (account_status or "").strip().lower()
    if status in _SESSION_FROZEN_STATUSES:
        return {"state": "frozen", "state_label": "frozen", "state_color": "blue"}
    if status == "expired":
        return {"state": "expired", "state_label": "expired", "state_color": "orange"}
    if expire_at is not None:
        from datetime import datetime
        if hasattr(expire_at, "replace"):
            if expire_at < datetime.utcnow():
                return {"state": "expired", "state_label": "expired", "state_color": "orange"}
    if status in {"enabled", "active", ""}:
        return {"state": "online", "state_label": "online", "state_color": "green"}
    return {"state": "active", "state_label": status or "active", "state_color": "cyan"}


class OperationsService:
    def __init__(self, audit: RadiusAuditService) -> None:
        self._audit = audit

    def create_distributor(self, *, tenant_id: int, actor: str, data: dict) -> dict:
        name = (data.get("name") or data.get("username") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        normalized = {
            "name": name,
            "display_name": (data.get("display_name") or name).strip(),
            "email": (data.get("email") or "").strip(),
            "phone": (data.get("phone") or "").strip(),
            "status": (data.get("status") or "active").strip().lower(),
            "permissions": data.get("permissions") or [],
            "scope": data.get("scope") or {},
            "balance": _float_field(data, "balance", default=0),
            "credit_limit": _float_field(data, "credit_limit", default=0),
            "debt_balance": _float_field(data, "debt_balance", default=0),
            "notes": (data.get("notes") or "")[:500],
            "metadata": data.get("metadata") or {},
            # المالك (المدير الذي يتبع له الموزع). يُحدَّد خادميًّا في الراوت:
            # محدود → نفسه (مقفل)، سوبر → المدير المختار. None = بلا مالك.
            "admin_id": (int(data["admin_id"]) if data.get("admin_id") else None),
        }
        try:
            saved = operations_repo.create_distributor(tenant_id, normalized, actor=actor)
        except sqlite3.IntegrityError:
            raise RadiusValidationError("distributor name already exists")
        self._audit.record(
            actor=actor,
            action="distributor.create",
            target_type="distributor",
            target_id=str(saved.get("id")),
            payload={"name": saved.get("name")},
        )
        return saved

    def update_distributor(self, *, tenant_id: int, distributor_id: int,
                           actor: str, data: dict) -> dict:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        name = (data.get("name") or data.get("username") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        normalized = {
            "name": name,
            "display_name": (data.get("display_name") or name).strip(),
            "email": (data.get("email") or "").strip(),
            "phone": (data.get("phone") or "").strip(),
            "status": (data.get("status") or "active").strip().lower(),
            "permissions": data.get("permissions") or [],
            "scope": data.get("scope") or {},
            "balance": _float_field(data, "balance", default=0),
            "credit_limit": _float_field(data, "credit_limit", default=0),
            "debt_balance": _float_field(data, "debt_balance", default=0),
            "notes": (data.get("notes") or "")[:500],
            # None → يُبقي المالك كما هو (COALESCE في الـrepo). يُمرَّر فقط حين
            # يُعاد إسناده (السوبر) أو يُثبَّت على المُنشئ المحدود.
            "admin_id": (int(data["admin_id"]) if data.get("admin_id") else None),
        }
        try:
            saved = operations_repo.update_distributor(tenant_id, distributor_id, normalized)
        except sqlite3.IntegrityError:
            raise RadiusValidationError("distributor name already exists")
        self._audit.record(
            actor=actor,
            action="distributor.update",
            target_type="distributor",
            target_id=str(distributor_id),
            payload={"name": saved.get("name")},
        )
        return saved

    def list_distributors(self, *, tenant_id: int, status: Optional[str] = None,
                          admin_id: Optional[int] = None,
                          limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_distributors(
            tenant_id, status=status, admin_id=admin_id, limit=limit, offset=offset
        )

    def get_distributor(self, *, tenant_id: int, distributor_id: int) -> dict:
        distributor = operations_repo.get_distributor(tenant_id, distributor_id)
        if not distributor:
            raise RadiusNotFound("distributor not found")
        return distributor

    def assign_batch(self, *, tenant_id: int, distributor_id: int, batch_id: int,
                     actor: str, notes: str = "") -> dict:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        if not cards_repo.get_batch(tenant_id, batch_id):
            raise RadiusNotFound("batch not found")
        assignment = operations_repo.assign_batch(
            tenant_id, distributor_id=distributor_id, batch_id=batch_id,
            actor=actor, notes=notes[:300],
        )
        self._audit.record(
            actor=actor,
            action="card_batch.assign_distributor",
            target_type="card_batch",
            target_id=str(batch_id),
            payload={"distributor_id": distributor_id},
        )
        return assignment

    def list_distributor_batches(self, *, tenant_id: int, distributor_id: int,
                                 limit: int = 200, offset: int = 0) -> list[dict]:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        return operations_repo.list_assigned_batches(
            tenant_id, distributor_id, limit=limit, offset=offset
        )

    def distributor_summary(self, *, tenant_id: int, distributor_id: int) -> dict:
        summary = operations_repo.distributor_summary(tenant_id, distributor_id)
        if not summary:
            raise RadiusNotFound("distributor not found")
        return summary

    def settle_distributor(self, *, tenant_id: int, distributor_id: int,
                           actor: str, data: dict) -> dict:
        self.get_distributor(tenant_id=tenant_id, distributor_id=distributor_id)
        amount = _float_field(data, "amount", minimum=0.01)
        direction = (data.get("direction") or "credit").strip().lower()
        if direction not in {"credit", "debit"}:
            raise RadiusValidationError("direction must be credit or debit")
        entry_type = (data.get("entry_type") or "settlement").strip().lower()
        entry = operations_repo.post_distributor_ledger(
            tenant_id,
            distributor_id,
            entry_type=entry_type,
            direction=direction,
            amount=amount,
            currency=(data.get("currency") or default_currency()).strip().upper(),
            actor=actor,
            notes=(data.get("notes") or "")[:500],
            related_type=(data.get("related_type") or "").strip(),
            related_id=data.get("related_id"),
            metadata=data.get("metadata") or {},
        )
        self._audit.record(
            actor=actor,
            action="distributor.ledger_post",
            target_type="distributor",
            target_id=str(distributor_id),
            payload={"entry_id": entry.get("id"), "amount": amount, "direction": direction},
        )
        return entry

    def create_bandwidth_schedule(self, *, tenant_id: int, actor: str,
                                  data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        target_type = (data.get("target_type") or "plan").strip().lower()
        if target_type not in {"plan", "subscriber", "card_batch", "subscriber_group"}:
            raise RadiusValidationError(
                "target_type must be plan, subscriber, card_batch, or subscriber_group")

        plan_id = _int_field(data, "plan_id", minimum=0, default=0) or None
        subscriber_username = ""
        card_batch_id = None
        subscriber_group_id = None
        if target_type == "plan":
            if not plan_id:
                raise RadiusValidationError("plan_id is required")
            if not plans_repo.get_plan(tenant_id, plan_id):
                raise RadiusNotFound("plan not found")
        elif target_type == "subscriber":
            from ..db.repos import subscribers_repo
            subscriber_username = (data.get("subscriber_username") or data.get("username") or "").strip()
            if not subscriber_username:
                raise RadiusValidationError("subscriber_username is required")
            sub = subscribers_repo.get_subscriber(tenant_id, subscriber_username)
            if not sub:
                raise RadiusNotFound("subscriber not found")
            plan_id = sub.plan_id or plan_id
            if not plan_id:
                raise RadiusValidationError("subscriber has no plan_id; set plan_id first")
        elif target_type == "card_batch":
            from ..db.repos import cards_repo
            card_batch_id = _int_field(data, "card_batch_id", minimum=1)
            batch = cards_repo.get_batch(tenant_id, card_batch_id, include_deleted=True)
            if not batch:
                raise RadiusNotFound("card batch not found")
            plan_id = batch.plan_id or plan_id
        else:  # subscriber_group
            from ..db.repos import subscriber_groups_repo
            subscriber_group_id = _int_field(data, "subscriber_group_id", minimum=1)
            grp = subscriber_groups_repo.get(tenant_id, subscriber_group_id)
            if not grp:
                raise RadiusNotFound("subscriber group not found")
            plan_id = grp.get("default_plan_id") or plan_id
        normalized = {
            "plan_id": plan_id,
            "target_type": target_type,
            "subscriber_username": subscriber_username,
            "card_batch_id": card_batch_id,
            "subscriber_group_id": subscriber_group_id,
            "priority": _int_field(data, "priority", minimum=1, default=100),
            "name": name,
            "starts_at_time": _validate_time(data.get("starts_at_time"), "starts_at_time"),
            "ends_at_time": _validate_time(data.get("ends_at_time"), "ends_at_time"),
            "days_csv": (data.get("days_csv") or "").strip(),
            "speed_down_kbps": _int_field(data, "speed_down_kbps"),
            "speed_up_kbps": _int_field(data, "speed_up_kbps"),
            "cir_down_kbps": _int_field(data, "cir_down_kbps"),
            "cir_up_kbps": _int_field(data, "cir_up_kbps"),
            "restore_mode": (data.get("restore_mode") or "profile_default").strip(),
            "enabled": bool(data.get("enabled", True)),
            "notes": (data.get("notes") or "")[:500],
            "metadata": data.get("metadata") or {},
        }
        if not (normalized["speed_down_kbps"] or normalized["speed_up_kbps"]) \
                and normalized["restore_mode"] != "disconnect":
            raise RadiusValidationError(
                "أدخلي سرعة التنزيل أو سرعة الرفع (واحدة على الأقل). "
                "إذا كان الغرض من القاعدة فصل الجلسة فقط، اختاري «فصل الجلسة» في «بعد الانتهاء»."
            )
        saved = operations_repo.create_bandwidth_schedule(
            tenant_id, normalized, actor=actor
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.create",
            target_type="bandwidth_schedule",
            target_id=str(saved.get("id")),
            payload={
                "plan_id": plan_id,
                "target_type": target_type,
                "subscriber_username": subscriber_username,
                "card_batch_id": card_batch_id,
                "name": name,
            },
        )
        return saved

    def list_bandwidth_schedules(self, *, tenant_id: int,
                                 plan_id: int | None = None,
                                 target_type: str | None = None,
                                 subscriber_username: str | None = None,
                                 card_batch_id: int | None = None,
                                 subscriber_group_id: int | None = None,
                                 limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_bandwidth_schedules(
            tenant_id,
            plan_id=plan_id,
            target_type=target_type,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            subscriber_group_id=subscriber_group_id,
            limit=limit,
            offset=offset,
        )

    def get_bandwidth_schedule(self, *, tenant_id: int, schedule_id: int) -> dict | None:
        return operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)

    def update_bandwidth_schedule(self, *, tenant_id: int, actor: str,
                                  schedule_id: int, data: dict) -> dict:
        current = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not current:
            raise RadiusNotFound("schedule not found")
        normalized = {
            "name": (data.get("name") or current.get("name") or "قاعدة سرعة").strip(),
            "starts_at_time": _validate_time(data.get("starts_at_time"), "starts_at_time"),
            "ends_at_time": _validate_time(data.get("ends_at_time"), "ends_at_time"),
            "days_csv": (data.get("days_csv") or "").strip(),
            "speed_down_kbps": _int_field(data, "speed_down_kbps"),
            "speed_up_kbps": _int_field(data, "speed_up_kbps"),
            "cir_down_kbps": _int_field(data, "cir_down_kbps"),
            "cir_up_kbps": _int_field(data, "cir_up_kbps"),
            "restore_mode": (data.get("restore_mode") or "profile_default").strip(),
            "priority": _int_field(data, "priority", minimum=1, default=100),
            "enabled": bool(data.get("enabled", True)),
            "notes": (data.get("notes") or "")[:500],
        }
        if not (normalized["speed_down_kbps"] or normalized["speed_up_kbps"]) \
                and normalized["restore_mode"] != "disconnect":
            raise RadiusValidationError(
                "أدخلي سرعة التنزيل أو سرعة الرفع (واحدة على الأقل). "
                "إذا كان الغرض من القاعدة فصل الجلسة فقط، اختاري «فصل الجلسة» في «بعد الانتهاء»."
            )
        saved = operations_repo.update_bandwidth_schedule(tenant_id, schedule_id, normalized)
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.update",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"name": saved.get("name"), "enabled": saved.get("enabled")},
        )
        return saved

    def set_bandwidth_schedule_enabled(self, *, tenant_id: int, actor: str,
                                       schedule_id: int, enabled: bool) -> dict:
        saved = operations_repo.set_bandwidth_schedule_enabled(tenant_id, schedule_id, enabled)
        if not saved:
            raise RadiusNotFound("schedule not found")
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.enable" if enabled else "bandwidth_schedule.disable",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"enabled": enabled},
        )
        return saved

    def set_bandwidth_schedules_enabled_for_target(
        self,
        *,
        tenant_id: int,
        actor: str,
        target_type: str,
        enabled: bool,
        plan_id: int | None = None,
        subscriber_username: str = "",
        card_batch_id: int | None = None,
        subscriber_group_id: int | None = None,
    ) -> int:
        count = operations_repo.set_bandwidth_schedules_enabled_for_target(
            tenant_id,
            target_type=target_type,
            enabled=enabled,
            plan_id=plan_id,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            subscriber_group_id=subscriber_group_id,
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.bulk_enable" if enabled else "bandwidth_schedule.bulk_disable",
            target_type=target_type,
            target_id=str(plan_id or subscriber_username or card_batch_id or subscriber_group_id or ""),
            payload={"enabled": enabled, "count": count},
        )
        return count

    def delete_bandwidth_schedule(self, *, tenant_id: int, actor: str,
                                  schedule_id: int) -> bool:
        current = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not current:
            raise RadiusNotFound("schedule not found")
        deleted = operations_repo.delete_bandwidth_schedule(tenant_id, schedule_id)
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.delete",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={"name": current.get("name"), "deleted": deleted},
        )
        return deleted

    def apply_bandwidth_schedule(self, *, tenant_id: int, schedule_id: int,
                                 actor: str, live: bool = False) -> dict:
        # ملاحظة (HOLD، قرار المالك معلَّق — تدقيق يونيو 2026): لا يوجد عامل
        # خلفيّ يُطبّق الجداول آليًّا بالوقت على الجلسات النشطة؛ التطبيق الحيّ
        # هنا يدويّ (live=True عبر «تطبيق الآن») أو passive عند إعادة المصادقة
        # (policy_engine._build_accept_attrs). لو قرّر المالك الجدولة الآليّة،
        # فالخُطّاف: عامل دوريّ (مثل temp_speed_expiry_worker) يَمسح الجداول
        # المفعَّلة كل دقيقة، يَحسب الداخلة/الخارجة من نافذتها الآن، ويستدعي هذه
        # الدالة بـlive=True لها. يبقى خلف HOBERADIUS_ENABLE_LIVE_SPEED_APPLY.
        schedule = operations_repo.get_bandwidth_schedule(tenant_id, schedule_id)
        if not schedule:
            raise RadiusNotFound("schedule not found")
        rate = _rate_limit_from_schedule(schedule)
        from .bandwidth_rate import live_apply_enabled
        live_enabled = live_apply_enabled()
        if not live or not live_enabled:
            message = (
                "Live RADIUS apply is disabled; dry-run only."
                if live and not live_enabled
                else "Validated schedule. Real-time RADIUS apply was not requested."
            )
            log = operations_repo.log_bandwidth_schedule(
                tenant_id, schedule_id, action="dry_run_apply", status="planned",
                message=message,
            )
            self._audit.record(
                actor=actor,
                action="bandwidth_schedule.apply_planned",
                target_type="bandwidth_schedule",
                target_id=str(schedule_id),
                payload={"log_id": log.get("id"), "live_requested": bool(live)},
            )
            return {
                "schedule": schedule,
                "log": log,
                "rate_limit": rate,
                "applied_to_radius": False,
                "dry_run": True,
                "live_requested": bool(live),
                "live_enabled": live_enabled,
            }

        usernames = operations_repo.usernames_for_bandwidth_schedule(
            tenant_id,
            schedule,
            limit=1000,
        )
        results: list[dict] = []
        applied = 0
        from ..integration import radius_coa
        for username in usernames:
            coa = radius_coa.change_user_rate(
                tenant_id,
                username,
                new_rate_limit=rate,
            )
            if coa.ok:
                applied += 1
            results.append({
                "username": username,
                "ok": bool(coa.ok),
                "code": coa.code_name,
                "message": coa.reply_message,
            })
        status = "applied" if applied else "no_active_sessions"
        if not usernames:
            status = "no_targets"
        log = operations_repo.log_bandwidth_schedule(
            tenant_id,
            schedule_id,
            action="live_apply",
            status=status,
            message=f"Applied {applied}/{len(usernames)} active sessions.",
        )
        self._audit.record(
            actor=actor,
            action="bandwidth_schedule.apply_live",
            target_type="bandwidth_schedule",
            target_id=str(schedule_id),
            payload={
                "log_id": log.get("id"),
                "rate_limit": rate,
                "target_count": len(usernames),
                "applied_count": applied,
            },
        )
        return {
            "schedule": schedule,
            "log": log,
            "rate_limit": rate,
            "applied_to_radius": applied > 0,
            "dry_run": False,
            "live_requested": True,
            "live_enabled": True,
            "target_count": len(usernames),
            "applied_count": applied,
            "results": results,
        }

    def resolve_effective_bandwidth_schedule(
        self,
        *,
        tenant_id: int,
        subscriber_username: str = "",
        card_batch_id: int | None = None,
        plan_id: int | None = None,
    ) -> dict:
        rule = operations_repo.resolve_effective_bandwidth_schedule(
            tenant_id,
            subscriber_username=subscriber_username,
            card_batch_id=card_batch_id,
            plan_id=plan_id,
        )
        return {
            "effective_rule": rule,
            "has_rule": bool(rule),
            "rate_limit": _rate_limit_from_schedule(rule) if rule else "",
            "source": (rule or {}).get("target_type") or "none",
            "precedence": ["subscriber", "card_batch", "plan"],
            "input": {
                "subscriber_username": subscriber_username,
                "card_batch_id": card_batch_id,
                "plan_id": plan_id,
            },
        }

    def create_print_template(self, *, tenant_id: int, actor: str, data: dict) -> dict:
        name = (data.get("name") or "").strip()
        if not name:
            raise RadiusValidationError("name is required")
        orientation = (data.get("orientation") or "portrait").strip().lower()
        if orientation not in _PRINT_ORIENTATIONS:
            raise RadiusValidationError("orientation must be portrait or landscape")
        layout = _template_layout(data)
        normalized = {
            "name": name,
            "orientation": orientation,
            "cards_per_row": _int_field(data, "cards_per_row", minimum=1, default=2),
            "cards_per_column": _int_field(data, "cards_per_column", minimum=1, default=5),
            "page_size": (data.get("page_size") or "A4").strip(),
            "show_qr": bool(data.get("show_qr", True)),
            "username_x": _float_field(data, "username_x", default=0),
            "username_y": _float_field(data, "username_y", default=0),
            "password_x": _float_field(data, "password_x", default=0),
            "password_y": _float_field(data, "password_y", default=0),
            "qr_x": _float_field(data, "qr_x", default=0),
            "qr_y": _float_field(data, "qr_y", default=0),
            "font_size": _int_field(data, "font_size", minimum=6, default=12),
            "color": _safe_hex(data.get("color") or layout.get("text_color"), "#1f2937"),
            "layout": layout,
        }
        try:
            saved = operations_repo.create_print_template(
                tenant_id, normalized, actor=actor
            )
        except sqlite3.IntegrityError:
            raise RadiusValidationError("print template name already exists")
        self._audit.record(
            actor=actor,
            action="card_print_template.create",
            target_type="card_print_template",
            target_id=str(saved.get("id")),
            payload={"name": name},
        )
        return saved

    def update_print_template(self, *, tenant_id: int, actor: str,
                              template_id: int, data: dict) -> dict:
        current = operations_repo.get_print_template(tenant_id, template_id)
        if not current:
            raise RadiusNotFound("print template not found")
        merged = {**current, **data}
        if isinstance(current.get("layout_json"), dict):
            merged["layout"] = {**current["layout_json"], **(data.get("layout") or {})}
        if "name" in data and not str(data.get("name") or "").strip():
            raise RadiusValidationError("name is required")
        orientation = str(merged.get("orientation") or "portrait").strip().lower()
        if orientation not in _PRINT_ORIENTATIONS:
            raise RadiusValidationError("orientation must be portrait or landscape")
        layout = _template_layout(merged)
        normalized = {
            "name": str(merged.get("name") or "").strip(),
            "orientation": orientation,
            "cards_per_row": _int_field(merged, "cards_per_row", minimum=1, default=2),
            "cards_per_column": _int_field(merged, "cards_per_column", minimum=1, default=5),
            "page_size": str(merged.get("page_size") or "A4").strip(),
            "show_qr": _boolish(merged.get("show_qr"), True),
            "username_x": _float_field(merged, "username_x", default=0),
            "username_y": _float_field(merged, "username_y", default=0),
            "password_x": _float_field(merged, "password_x", default=0),
            "password_y": _float_field(merged, "password_y", default=0),
            "qr_x": _float_field(merged, "qr_x", default=0),
            "qr_y": _float_field(merged, "qr_y", default=0),
            "font_size": _int_field(merged, "font_size", minimum=6, default=12),
            "color": _safe_hex(merged.get("color") or layout.get("text_color"), "#1f2937"),
            "layout": layout,
        }
        try:
            saved = operations_repo.update_print_template(
                tenant_id, template_id, normalized, actor=actor
            )
        except sqlite3.IntegrityError:
            raise RadiusValidationError("print template name already exists")
        self._audit.record(
            actor=actor,
            action="card_print_template.update",
            target_type="card_print_template",
            target_id=str(template_id),
            payload={"name": saved.get("name")},
        )
        return saved

    def list_print_templates(self, *, tenant_id: int,
                             limit: int = 200, offset: int = 0) -> list[dict]:
        return operations_repo.list_print_templates(tenant_id, limit=limit, offset=offset)

    def delete_print_template(self, *, tenant_id: int, actor: str,
                              template_id: int) -> bool:
        current = operations_repo.get_print_template(tenant_id, template_id)
        if not current:
            raise RadiusNotFound("print template not found")
        ok = operations_repo.delete_print_template(tenant_id, template_id)
        if ok:
            self._audit.record(
                actor=actor,
                action="card_print_template.delete",
                target_type="card_print_template",
                target_id=str(template_id),
                payload={"name": current.get("name")},
            )
        return ok

    # Names like "Print UI ab12cd34", "ops_room_ab12cd34", "template_ab12cd"
    # are emitted by the integration test suite and end up in the dev DB
    # when developers run the full test pass against their working copy.
    # Exposed as a single regex so the route + the test for the route stay
    # honest about what it deletes.
    PURGEABLE_TEMPLATE_PATTERN = re.compile(
        r"^(?:Print UI |ops_room_|template_)[A-Fa-f0-9]{4,}\s*$"
    )

    def purge_test_fixture_print_templates(
        self, *, tenant_id: int, actor: str
    ) -> list[dict]:
        rows = operations_repo.list_print_templates(tenant_id, limit=10_000, offset=0)
        purged: list[dict] = []
        for row in rows:
            name = str(row.get("name") or "")
            if not self.PURGEABLE_TEMPLATE_PATTERN.match(name):
                continue
            if operations_repo.delete_print_template(tenant_id, int(row["id"])):
                purged.append({"id": row["id"], "name": name})
        if purged:
            self._audit.record(
                actor=actor,
                action="card_print_template.purge_fixtures",
                target_type="card_print_template",
                target_id="*",
                payload={"count": len(purged), "names": [p["name"] for p in purged]},
            )
        return purged

    def list_print_template_presets(self) -> list[dict]:
        return _print_presets_list()

    def set_default_print_template(
        self, *, tenant_id: int, actor: str, template_id: int
    ) -> dict:
        """Mark exactly one print template as the tenant default.

        We deliberately do NOT add a new DB column for this — the flag is
        stored inside the existing `layout_json` payload as `is_default`,
        which keeps the schema unchanged and naturally hydrates back out
        through `list_print_templates`. The service enforces uniqueness
        by clearing the flag on every other template in the same tenant
        before flipping the chosen one on.
        """
        target = operations_repo.get_print_template(tenant_id, template_id)
        if not target:
            raise RadiusNotFound("print template not found")
        for row in operations_repo.list_print_templates(tenant_id, limit=10_000):
            layout = dict(row.get("layout_json") or {})
            wants_on = int(row["id"]) == int(template_id)
            had = bool(layout.get("is_default"))
            if had == wants_on:
                continue
            layout["is_default"] = wants_on
            # update_print_template re-validates the row through the same
            # normaliser used by create — by passing only `layout` we keep
            # the rest of the columns untouched.
            self.update_print_template(
                tenant_id=tenant_id,
                actor=actor,
                template_id=int(row["id"]),
                data={"layout": layout},
            )
        self._audit.record(
            actor=actor,
            action="card_print_template.set_default",
            target_type="card_print_template",
            target_id=str(template_id),
            payload={"name": target.get("name")},
        )
        return operations_repo.get_print_template(tenant_id, template_id) or {}

    def get_default_print_template_id(self, *, tenant_id: int) -> int | None:
        """Returns the id of the tenant's default print template, if any."""
        for row in operations_repo.list_print_templates(tenant_id, limit=10_000):
            layout = row.get("layout_json") or {}
            if layout.get("is_default"):
                return int(row["id"])
        return None

    def list_print_jobs(self, *, tenant_id: int,
                        limit: int = 50, offset: int = 0) -> list[dict]:
        return operations_repo.list_print_jobs(tenant_id, limit=limit, offset=offset)

    def render_print_template_preview(self, *, tenant_id: int, template_id: int,
                                      sample: Optional[dict] = None) -> dict:
        template = operations_repo.get_print_template(tenant_id, template_id)
        if not template:
            raise RadiusNotFound("print template not found")
        layout = template.get("layout_json")
        if not isinstance(layout, dict):
            layout = template.get("layout") if isinstance(template.get("layout"), dict) else {}
        layout = _template_layout({**template, "layout": layout})
        width_mm = max(float(layout.get("card_width_mm") or 85), 1.0)
        height_mm = max(float(layout.get("card_height_mm") or 54), 1.0)

        def _placement(prefix: str) -> dict:
            x_mm = float(template.get(f"{prefix}_x") or 0)
            y_mm = float(template.get(f"{prefix}_y") or 0)
            return {
                "x_mm": x_mm,
                "y_mm": y_mm,
                "x_percent": max(0, min(100, round((x_mm / width_mm) * 100, 2))),
                "y_percent": max(0, min(100, round((y_mm / height_mm) * 100, 2))),
            }

        raw_sample = sample if isinstance(sample, dict) else {}
        # لا قيمة وهمية تظهر كاسم بطاقة حقيقي: العلامة المحايدة «—» تكفي
        # كمعاينة قبل اختيار دفعة. المسار الذي يستدعي هذه الدالة يمرّر
        # اسم بطاقة حقيقية إن وُجدت (راجع print_templates._first_real_card_sample).
        sample_username = str(raw_sample.get("username") or "—")
        sample_payload = {
            "username": sample_username,
            "has_password": bool(raw_sample.get("has_password", True)),
            "qr_payload": str(raw_sample.get("qr_payload") or sample_username),
            "price": str(raw_sample.get("price") or layout.get("price_text") or ""),
            "validity": str(raw_sample.get("validity") or layout.get("validity_text") or ""),
        }
        return {
            "template": template,
            "preview": {
                "renderer": "visual_card_preview",
                "cards_per_page": int(template.get("cards_per_row") or 0)
                                  * int(template.get("cards_per_column") or 0),
                "qr_supported": bool(template.get("show_qr")),
                "card": {
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "font_size": int(template.get("font_size") or 12),
                    "color": template.get("color") or "#1f2937",
                    "layout": layout,
                },
                "design": {
                    "preset": layout.get("design_preset"),
                    "brand_name": layout.get("brand_name"),
                    "card_title": layout.get("card_title"),
                    "gradient_start": layout.get("gradient_start"),
                    "gradient_end": layout.get("gradient_end"),
                    "accent_color": layout.get("accent_color"),
                    "text_color": layout.get("text_color"),
                    "surface_color": layout.get("surface_color"),
                    "qr_style": layout.get("qr_style"),
                    "footer_text": layout.get("footer_text"),
                    "hotspot_address": layout.get("hotspot_address"),
                    "price_text": layout.get("price_text"),
                    "validity_text": layout.get("validity_text"),
                },
                "placements": {
                    "username": _placement("username"),
                    "password": _placement("password"),
                    "qr": _placement("qr"),
                },
                "sample": sample_payload,
                "capabilities": {
                    "sample_pdf": True,
                    "batch_pdf": True,
                    "csv": True,
                    "excel": False,
                    "png": False,
                },
            },
            "export_generated": False,
        }

    def export_print_template_pdf(self, *, tenant_id: int, template_id: int,
                                  sample: Optional[dict] = None,
                                  batch_id: int | None = None,
                                  layout_overrides: Optional[dict] = None,
                                  print_settings: Optional[dict] = None,
                                  scope: str = "all",
                                  actor: str = "system",
                                  job_id: int | None = None) -> bytes:
        template = operations_repo.get_print_template(tenant_id, template_id)
        if not template:
            raise RadiusNotFound("print template not found")

        from io import BytesIO
        from reportlab.lib.pagesizes import A4, letter, landscape, portrait
        from reportlab.lib.units import mm
        from reportlab.pdfgen import canvas

        from .card_renderer import (
            build_card_render_model,
            render_card_pdf,
            place_card_form_uniform,
            model_uses_uploaded_background,
            draw_uploaded_background_uniform,
        )

        # Allowed override keys from the export-center "tweak these
        # texts" fields. We pass them through to the unified renderer
        # rather than mutating layout_json here — same path the web
        # preview uses.
        allowed_override_keys = {
            "brand_name",
            "card_title",
            "footer_text",
            "hotspot_address",
            # رابط الدخول التلقائي من غرفة الطباعة: يجب أن يصل إلى مولّد
            # QR في المحرك الموحّد وإلا عملت معاينة غرفة التصميم وفشل
            # التصدير (نفس الخلل الذي أبلغ عنه المستخدم مع 10.10.10.10).
            "hotspot_login_url",
            "price_text",
            "validity_text",
        }
        overrides = {
            key: str(value).strip()
            for key, value in (layout_overrides or {}).items()
            if key in allowed_override_keys
            and value is not None
            and str(value).strip()
        }

        # Page geometry belongs to the export operation, not to the card
        # template. The saved template defines only the finished card snapshot;
        # these settings decide how many completed snapshots fit on paper.
        sheet = _print_sheet_settings(print_settings)
        page_size = str(sheet["page_size"]).strip().lower()
        base_size = letter if page_size == "letter" else A4
        orientation = str(sheet["orientation"]).lower()
        pagesize = landscape(base_size) if orientation == "landscape" else portrait(base_size)
        page_width, page_height = pagesize

        # Resolve which cards to render.
        sample_payload = sample or {}
        export_type = "sample_pdf"
        batch = None
        cards: list[dict]
        if batch_id:
            batch = cards_repo.get_batch(tenant_id, batch_id, include_deleted=True)
            if not batch:
                raise RadiusNotFound("card batch not found")
            # scope="unused" → only cards that were NEVER opened (used=0) and
            # not revoked; "all" (default) → every card in the batch.
            _unused_only = str(scope or "all").strip().lower() == "unused"
            raw_cards = cards_repo.list_cards(
                tenant_id, batch_id=batch_id,
                used=(False if _unused_only else None),
                revoked=(False if _unused_only else None),
                limit=20000, offset=0,
            )
            # Username + password MUST be carried through to the renderer
            # — the unified model guarantees they appear in the PDF.
            cards = [
                {
                    "id": c.id,
                    "username": c.username,
                    "password": c.password,
                    "serial": str(c.id or ""),
                }
                for c in raw_cards
            ]
            export_type = "batch_pdf"
        else:
            cards = sample_payload.get("cards") if isinstance(sample_payload.get("cards"), list) else []
            if not cards:
                cards = [{
                    "id": "",
                    "username": sample_payload.get("username") or "—",
                    "password": sample_payload.get("password") or "********",
                    "serial": "",
                }]
        if not cards:
            if batch_id and str(scope or "").strip().lower() == "unused":
                raise RadiusValidationError(
                    "لا توجد كروت غير مستخدمة في هذه الحزمة للطباعة.")
            raise RadiusValidationError("selected batch has no cards")

        first_model = build_card_render_model(
            template,
            cards[0] if isinstance(cards[0], dict) else {},
            overrides=overrides,
        )
        geometry = _strict_print_geometry(
            page_width=page_width,
            page_height=page_height,
            canvas_width=float(first_model["canvas"]["width"]),
            canvas_height=float(first_model["canvas"]["height"]),
            sheet=sheet,
            unit=mm,
        )
        cols = int(geometry["columns"])
        cards_per_page = int(geometry["cards_per_page"])
        dynamic_element_ids = {"user", "pass", "qr", "meta"}

        output = BytesIO()
        pdf = canvas.Canvas(output, pagesize=pagesize)
        # Chrome/Edge PDF viewer renders UTF-16 Arabic metadata titles
        # as disconnected/reordered glyphs in the toolbar even when the
        # card body itself renders correctly. Keep document metadata
        # ASCII-only; the Arabic template name still appears in the UI
        # and inside the rendered card where the Arabic image path is
        # used.
        pdf.setTitle(f"HobeRadius card export template {template_id}")
        pdf.setAuthor("HobeRadius")

        file_name = f"cards-template-{template_id}.pdf"
        if batch_id:
            file_name = f"cards-batch-{batch_id}-template-{template_id}.pdf"
        job_metadata = {
            "template_name": template.get("name"),
            "batch_code": getattr(batch, "batch_code", "") if batch else "",
            "progress": 8,
            "stage": "started",
            "stage_label": "بدأ تجهيز ملف PDF",
        }
        if job_id:
            job = operations_repo.update_print_job(
                tenant_id,
                job_id,
                status="started",
                card_count=len(cards),
                file_name=file_name,
                message="بدأ إنشاء ملف PDF.",
                metadata=job_metadata,
            )
        else:
            job = operations_repo.create_print_job(
                tenant_id,
                template_id=template_id,
                batch_id=batch_id,
                export_type=export_type,
                status="started",
                card_count=len(cards),
                file_name=file_name,
                metadata=job_metadata,
                actor=actor,
            )
        try:
            static_form_name = f"card_{template_id}_static"
            uploaded_background_engine = model_uses_uploaded_background(first_model)
            render_card_pdf(
                pdf,
                first_model,
                form_name=static_form_name,
                expose_password=False,
                include_background=not uploaded_background_engine,
                exclude_ids=dynamic_element_ids,
            )
            progress_every = max(1, min(250, len(cards) // 20 or 1))
            for idx, card in enumerate(cards):
                if idx and idx % cards_per_page == 0:
                    pdf.showPage()
                slot = idx % cards_per_page
                placement = geometry["positions"][slot]
                # Build the render model for this card via the SAME
                # builder the live preview uses.
                if idx == 0:
                    model = first_model
                else:
                    model = build_card_render_model(
                        template,
                        card if isinstance(card, dict) else {},
                        overrides=overrides,
                    )
                dynamic_form_name = f"card_{template_id}_{idx}_dynamic"
                # Render the card into a named form at canvas coords …
                render_card_pdf(
                    pdf,
                    model,
                    form_name=dynamic_form_name,
                    expose_password=True,
                    include_background=False,
                    include_ids=dynamic_element_ids,
                )
                # … then place that form into the sheet slot with
                # UNIFORM scale. cards_per_row/column only affect the
                # slot — never the contents of the form.
                if uploaded_background_engine:
                    draw_uploaded_background_uniform(
                        pdf,
                        model,
                        slot_x=float(placement["x"]),
                        slot_y=float(placement["y"]),
                        slot_width=float(geometry["card_width"]),
                        slot_height=float(geometry["card_height"]),
                    )
                place_card_form_uniform(
                    pdf, model, form_name=static_form_name,
                    slot_x=float(placement["x"]), slot_y=float(placement["y"]),
                    slot_width=float(geometry["card_width"]),
                    slot_height=float(geometry["card_height"]),
                )
                place_card_form_uniform(
                    pdf, model, form_name=dynamic_form_name,
                    slot_x=float(placement["x"]), slot_y=float(placement["y"]),
                    slot_width=float(geometry["card_width"]),
                    slot_height=float(geometry["card_height"]),
                )
                if idx == 0 or (idx + 1) % progress_every == 0 or (idx + 1) == len(cards):
                    progress = 12 + int(((idx + 1) / len(cards)) * 72)
                    operations_repo.update_print_job(
                        tenant_id,
                        int(job.get("id") or 0),
                        status="rendering",
                        card_count=len(cards),
                        file_name=file_name,
                        message=f"Rendered {idx + 1} of {len(cards)} cards.",
                        metadata={
                            "progress": min(progress, 88),
                            "stage": "rendering",
                            "stage_label": "رسم البطاقات داخل ملف PDF",
                            "rendered_cards": idx + 1,
                            "total_cards": len(cards),
                            "cards_per_page": cards_per_page,
                        },
                    )

            pdf.showPage()
            operations_repo.update_print_job(
                tenant_id,
                int(job.get("id") or 0),
                status="finalizing",
                card_count=len(cards),
                file_name=file_name,
                message="Finalizing PDF bytes.",
                metadata={
                    "progress": 92,
                    "stage": "finalizing",
                    "stage_label": "إغلاق الملف وتجهيز التنزيل",
                    "total_cards": len(cards),
                },
            )
            pdf.save()
            payload = output.getvalue()
            operations_repo.finish_print_job(
                tenant_id,
                int(job.get("id") or 0),
                status="success",
                card_count=len(cards),
                file_name=file_name,
                message=f"Generated {len(cards)} card(s).",
                metadata={
                    "template_name": template.get("name"),
                    "batch_id": batch_id,
                    "cards_per_page": cards_per_page,
                    "print_settings": {
                        **sheet,
                        "cards_per_page": cards_per_page,
                        "card_width_pt": geometry["card_width"],
                        "card_height_pt": geometry["card_height"],
                        "fit_limited_by": geometry["fit_limited_by"],
                    },
                    "render_mode": "unified_renderer_static_form_plus_dynamic_layer",
                    "bytes": len(payload),
                    "progress": 100,
                    "stage": "completed",
                    "stage_label": "اكتمل ملف PDF",
                },
            )
            self._audit.record(
                actor=actor,
                action="card_print_template.export_pdf",
                target_type="card_print_template",
                target_id=str(template_id),
                payload={"batch_id": batch_id, "card_count": len(cards), "job_id": job.get("id")},
            )
            return payload
        except Exception as exc:
            operations_repo.finish_print_job(
                tenant_id,
                int(job.get("id") or 0),
                status="failed",
                card_count=len(cards),
                file_name=file_name,
                message=str(exc),
                metadata={"template_name": template.get("name"), "batch_id": batch_id},
            )
            raise

    def start_print_template_export_job(
        self,
        *,
        tenant_id: int,
        template_id: int,
        sample: Optional[dict] = None,
        batch_id: int | None = None,
        layout_overrides: Optional[dict] = None,
        print_settings: Optional[dict] = None,
        scope: str = "all",
        actor: str = "system",
    ) -> dict:
        template = operations_repo.get_print_template(tenant_id, template_id)
        if not template:
            raise RadiusNotFound("print template not found")
        batch = None
        card_count = 1
        export_type = "sample_pdf_async"
        if batch_id:
            batch = cards_repo.get_batch(tenant_id, batch_id, include_deleted=True)
            if not batch:
                raise RadiusNotFound("card batch not found")
            # Count cheaply from the existing batch list helper; this is only
            # metadata for progress UX. The renderer resolves the actual cards.
            card_count = int(getattr(batch, "total_cards", 0) or getattr(batch, "generated", 0) or 0)
            export_type = "batch_pdf_async"
        file_name = f"cards-template-{template_id}.pdf"
        if batch_id:
            file_name = f"cards-batch-{batch_id}-template-{template_id}.pdf"
        job = operations_repo.create_print_job(
            tenant_id,
            template_id=template_id,
            batch_id=batch_id,
            export_type=export_type,
            status="queued",
            card_count=card_count,
            file_name=file_name,
            message="Queued PDF export job.",
            metadata={
                "experimental_async": True,
                "progress": 2,
                "stage": "queued",
                "stage_label": "تم وضع المهمة في الطابور",
                "template_name": template.get("name"),
                "batch_code": getattr(batch, "batch_code", "") if batch else "",
                "download_ready": False,
            },
            actor=actor,
        )
        _PRINT_EXPORT_EXECUTOR.submit(
            self._run_print_template_export_job,
            tenant_id,
            int(job["id"]),
            template_id,
            sample or {},
            batch_id,
            layout_overrides or {},
            print_settings or {},
            actor,
            scope or "all",
        )
        return job

    def _print_export_dir(self, tenant_id: int) -> Path:
        target = Path(db_path()).parent / "print_exports" / str(int(tenant_id))
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _run_print_template_export_job(
        self,
        tenant_id: int,
        job_id: int,
        template_id: int,
        sample: dict,
        batch_id: int | None,
        layout_overrides: dict,
        print_settings: dict,
        actor: str,
        scope: str = "all",
    ) -> None:
        try:
            with _PRINT_EXPORT_LOCK:
                operations_repo.update_print_job(
                    tenant_id,
                    job_id,
                    status="started",
                    message="Worker started PDF generation.",
                    metadata={
                        "progress": 5,
                        "stage": "worker_started",
                        "stage_label": "بدأ عامل التصدير",
                    },
                )
                payload = self.export_print_template_pdf(
                    tenant_id=tenant_id,
                    template_id=template_id,
                    sample=sample,
                    batch_id=batch_id,
                    layout_overrides=layout_overrides,
                    print_settings=print_settings,
                    scope=scope,
                    actor=actor,
                    job_id=job_id,
                )
                suffix = f"batch-{batch_id}" if batch_id else f"template-{template_id}"
                file_name = f"cards-{suffix}-job-{job_id}.pdf"
                file_path = self._print_export_dir(tenant_id) / file_name
                file_path.write_bytes(payload)
                job = operations_repo.get_print_job(tenant_id, job_id) or {}
                metadata = job.get("metadata_json") if isinstance(job.get("metadata_json"), dict) else {}
                metadata.update({
                    "download_ready": True,
                    "download_path": str(file_path),
                    "bytes": len(payload),
                    "progress": 100,
                    "stage": "completed",
                    "stage_label": "اكتمل ملف PDF وأصبح جاهزًا للتنزيل",
                })
                operations_repo.finish_print_job(
                    tenant_id,
                    job_id,
                    status="success",
                    card_count=int(job.get("card_count") or 0),
                    file_name=file_name,
                    message="PDF export completed.",
                    metadata=metadata,
                )
        except Exception as exc:
            operations_repo.finish_print_job(
                tenant_id,
                job_id,
                status="failed",
                card_count=0,
                file_name="",
                message=str(exc),
                metadata={
                    "experimental_async": True,
                    "download_ready": False,
                    "progress": 100,
                    "stage": "failed",
                    "stage_label": "فشل تجهيز ملف PDF",
                },
            )
        finally:
            close_thread_conn()

    def get_print_job(self, *, tenant_id: int, job_id: int) -> dict:
        job = operations_repo.get_print_job(tenant_id, job_id)
        if not job:
            raise RadiusNotFound("print job not found")
        return job

    def get_print_job_file(self, *, tenant_id: int, job_id: int) -> tuple[bytes, str]:
        job = self.get_print_job(tenant_id=tenant_id, job_id=job_id)
        if job.get("status") != "success":
            raise RadiusValidationError("print job is not ready")
        metadata = job.get("metadata_json") if isinstance(job.get("metadata_json"), dict) else {}
        raw_path = metadata.get("download_path")
        if not raw_path:
            raise RadiusNotFound("print job file not found")
        base_dir = self._print_export_dir(tenant_id).resolve()
        file_path = Path(str(raw_path)).resolve()
        if base_dir not in file_path.parents and file_path != base_dir:
            raise RadiusValidationError("invalid print job file path")
        if not file_path.exists():
            raise RadiusNotFound("print job file not found")
        return file_path.read_bytes(), str(job.get("file_name") or file_path.name)

    def backup_status(self, *, tenant_id: int) -> dict:
        payload = operations_repo.backup_status(tenant_id)
        payload["google_drive"] = self._google_drive_backup_status(tenant_id)
        return payload

    def _google_drive_backup_status(self, tenant_id: int) -> dict:
        try:
            from . import google_drive as gd
            raw = gd.status(tenant_id)
        except Exception as exc:  # noqa: BLE001
            return {
                "configured": False,
                "connected": False,
                "pending": False,
                "status": "unavailable",
                "email": "",
                "folder_name": "HobeRadius Backups",
                "last_upload_at": "",
                "last_error": str(exc)[:200],
                "message_ar": "تعذرت قراءة حالة جوجل درايف من إعدادات الخادم.",
            }
        configured = bool(raw.get("configured"))
        connected = bool(raw.get("connected"))
        pending = bool(raw.get("pending"))
        if connected:
            status = "connected"
            message = "جوجل درايف مربوط وسيتم استخدامه عند تشغيل النسخ المناسبة."
        elif pending:
            status = "pending"
            message = "طلب ربط جوجل درايف بانتظار إكمال التحقق من المستخدم."
        elif configured:
            status = "configured_not_connected"
            message = "إعدادات جوجل درايف موجودة، لكن الحساب غير مربوط بعد."
        else:
            status = "not_configured"
            message = "جوجل درايف غير مفعل حاليًا من إعدادات الخادم."
        return {
            "configured": configured,
            "connected": connected,
            "pending": pending,
            "status": status,
            "email": str(raw.get("email") or ""),
            "folder_name": str(raw.get("folder_name") or "HobeRadius Backups"),
            "last_upload_at": str(raw.get("last_upload_at") or ""),
            "last_error": str(raw.get("last_error") or ""),
            "message_ar": message,
        }

    # ── Lean ("core") backups ─────────────────────────────────────────────
    # A routine backup keeps the FULL schema (so a restore is complete) but
    # drops the rows of high-volume telemetry/accounting/event-log tables and
    # the in-DB BLOB assets, then VACUUMs the copy. Core business data
    # (subscribers, cards, batches, wallets, plans, settings, roles, admins,
    # ledgers, …) is always kept. This is what makes a normal backup a few MB
    # instead of the whole multi-hundred-MB database.
    LEAN_BACKUP_EXCLUDE_TABLES = (
        # RADIUS accounting / auth logs
        "radacct", "radpostauth",
        # event + audit streams
        "business_events", "audit_log",
        # notifications / delivery logs
        "message_deliveries", "message_notifications", "panel_notifications",
        # device-health / monitoring event + alert logs
        "network_device_health_checks", "network_device_checks",
        "network_device_monitor_events", "network_device_monitor_alerts",
        "network_device_alerts",
        # router telemetry time-series
        "router_resource_samples", "router_metric_samples",
        "router_loop_probes", "router_loop_checks",
        # hotspot analytics beacons
        "hotspot_analytics_events",
        # lifecycle action log
        "lifecycle_events",
        # operations logs
        "bandwidth_schedule_logs", "backup_run_logs",
        # sensitive short-lived log
        "login_attempt_passwords",
        # license bridge attempt/event logs + the snapshot cache (the single
        # biggest table on a long-running install — a full payload blob appended
        # every sync cycle; safe to empty since it is a rebuildable cache).
        "license_admin_bridge_snapshots",
        "license_admin_heartbeat_attempts", "license_admin_usage_report_attempts",
        "license_admin_backup_upload_attempts", "license_admin_bridge_events",
        "setup_wizard_recovery_events", "payment_webhook_events",
        # schema-wide sweep — other append-only telemetry/audit/log tables now
        # under retention (see log_retention._RULES). Emptied in the lean copy,
        # kept in a full archive. price_snapshots is intentionally NOT here —
        # it is low-volume financial provenance worth keeping in routine backups.
        "service_audit_log", "router_lifecycle_events", "setup_wizard_runs",
        "setup_wizard_router_snapshots", "network_policy_snapshots",
        "mac_clone_events", "hotspot_card_sms_attempts",
        "payment_service_apply_attempts", "mikrotik_import_logs",
        "webhook_deliveries", "financial_report_snapshots",
        # in-DB BLOB assets (large binaries) — kept only in a full archive
        "router_backups", "hotspot_assets",
    )

    def _lean_backup_default(self) -> bool:
        raw = str(env_settings.env("HOBERADIUS_BACKUP_LEAN_DEFAULT") or "1").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _backup_compress_default(self) -> bool:
        """Routine backups are gzip-compressed by default (…​.sqlite3.gz).

        This shrinks a lean SQLite backup by ~another order of magnitude and
        keeps panel/Drive uploads well under their size caps. Set
        HOBERADIUS_BACKUP_GZIP=0 to fall back to raw uncompressed .sqlite3
        (e.g. if an operator needs to open the file directly without gunzip)."""
        raw = str(env_settings.env("HOBERADIUS_BACKUP_GZIP") or "1").strip().lower()
        return raw not in ("0", "false", "no", "off")

    def _backup_gzip_level(self) -> int:
        try:
            n = int(str(env_settings.env("HOBERADIUS_BACKUP_GZIP_LEVEL") or "6").strip())
        except (TypeError, ValueError):
            n = 6
        return max(1, min(9, n))

    def _strip_backup_to_lean(self, target: Path) -> dict:
        """On a freshly-copied backup file, delete the rows of the high-volume /
        log / BLOB tables and VACUUM so the routine backup stays small. The
        schema is preserved (tables remain, just emptied) so a restore is
        complete. Returns the list of tables actually cleared."""
        cleared: list[str] = []
        conn = sqlite3.connect(str(target))
        try:
            existing = {
                str(r[0]) for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            for t in self.LEAN_BACKUP_EXCLUDE_TABLES:
                if t in existing:
                    conn.execute(f"DELETE FROM {t}")
                    cleared.append(t)
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()
        return {"cleared": cleared}

    def run_local_backup(self, *, tenant_id: int, actor: str, lean: bool | None = None) -> dict:
        source = Path(db_path())
        job = operations_repo.ensure_backup_job(tenant_id, actor=actor)
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        from datetime import datetime
        lean = self._lean_backup_default() if lean is None else bool(lean)
        compress = self._backup_compress_default()
        stamp = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        # A real SQLite file must exist first: the native backup() API writes to
        # a SQLite connection, and the lean strip runs DELETE + VACUUM on it.
        # When compressing, this is a transient work file that is streamed into
        # the final .sqlite3.gz and then removed, so only the compressed artifact
        # (plus, briefly, the raw copy) ever touches disk.
        work = backup_dir / f"hoberadius-{stamp}.sqlite3"
        target = work
        compressed = False
        try:
            # Best-effort: flush WAL so the copy reflects all committed rows.
            try:
                from ..db.connection import checkpoint_wal
                checkpoint_wal()
            except Exception:  # noqa: BLE001
                pass
            dest = sqlite3.connect(str(work))
            try:
                db().backup(dest)
            finally:
                dest.close()
            if lean:
                # Lean strip must never break a backup — fall back to the full
                # copy if anything goes wrong (a valid, if larger, backup).
                try:
                    self._strip_backup_to_lean(work)
                except Exception:  # noqa: BLE001
                    pass
            raw_ok = work.exists() and work.stat().st_size > 0
            if raw_ok and compress:
                # Stream the finished SQLite file out through gzip. Compression
                # must never break a backup: on any failure we keep the valid
                # (larger) raw .sqlite3 copy instead.
                gz = backup_dir / f"hoberadius-{stamp}.sqlite3.gz"
                try:
                    _bkz.gzip_compress_file(work, gz, level=self._backup_gzip_level())
                    if _bkz.gzip_sqlite_header_ok(gz):
                        try:
                            work.unlink()
                        except OSError:
                            pass
                        target = gz
                        compressed = True
                    else:
                        try:
                            gz.unlink()
                        except OSError:
                            pass
                except Exception:  # noqa: BLE001
                    try:
                        gz.unlink()
                    except OSError:
                        pass
            verified = target.exists() and target.stat().st_size > 0
            status = "success" if verified else "failed"
            kind_ar = "نسخة أساسية (بيانات العمل)" if lean else "أرشيف كامل (يشمل السجلّات)"
            comp_ar = " مضغوطة gzip" if compressed else ""
            message = (
                f"تم إنشاء نسخة SQLite محلية والتحقق منها — {kind_ar}{comp_ar}."
                if verified else "لم يتم إنشاء ملف النسخة الاحتياطية."
            )
        except sqlite3.Error as exc:
            verified = False
            status = "failed"
            message = f"SQLite backup failed: {exc}"
        log = operations_repo.record_backup_run(
            tenant_id,
            job_id=job.get("id"),
            status=status,
            path=str(target) if target.exists() else "",
            message=message,
        )
        self._audit.record(
            actor=actor,
            action="backup.local_run",
            target_type="backup_job",
            target_id=str(job.get("id")),
            payload={"status": status, "verified": verified},
        )
        try:
            self.prune_local_backups()
            self.prune_local_backups_by_count(tenant_id=tenant_id)
        except Exception:  # noqa: BLE001 — retention must never break a backup run
            pass
        # محاولة غير إلزامية: ارفع النسخة إلى جوجل درايف إذا كان مربوطًا محليًا.
        # الرفع لا يكسر النسخة أبدًا، لكن نتيجته (نجاح/فشل) تُسجَّل وتُعاد بدل
        # ابتلاعها بصمت — فلو فشل الرفع لجوجل درايف يظهر السبب للمالك (لا فشل
        # صامت). gd.upload_backup يحفظ آخر خطأ في إعدادات المستأجر أيضًا.
        drive: dict | None = None
        if verified:
            try:
                from . import google_drive as gd
                if gd.status(tenant_id).get("connected"):
                    res = gd.upload_backup(tenant_id, str(target), target.name) or {}
                    if res.get("ok"):
                        drive = {"ok": True, "file_id": res.get("file_id")}
                    else:
                        drive = {"ok": False, "error": str(res.get("error") or "drive_upload_failed")}
            except Exception as exc:  # noqa: BLE001 — Drive push must never break a backup
                try:
                    from . import google_drive as gd
                    gd._set(tenant_id, gd.K_LAST_ERROR, str(exc)[:300])
                except Exception:  # noqa: BLE001
                    pass
                drive = {"ok": False, "error": str(exc)}
        out = {"job": operations_repo.ensure_backup_job(tenant_id), "run": log, "verified": verified}
        if drive is not None:
            out["drive"] = drive
        return out

    # ── Local backup files: listing / retention / download / restore ──
    # Time-based window (days). Env-overridable; 0 disables time pruning.
    LOCAL_BACKUP_RETENTION_DAYS = max(0, int(env_settings.env("HOBERADIUS_BACKUP_RETENTION_DAYS") or 30))

    def _backup_dir(self) -> Path:
        backup_dir = Path(db_path()).parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir

    def prune_local_backups(self, *, days: int | None = None) -> list[str]:
        """Delete local backup files older than the retention window (default 30d).

        Returns the names of the files that were removed. Safe/quiet on errors.
        """
        import time as _time

        retention = int(days if days is not None else self.LOCAL_BACKUP_RETENTION_DAYS)
        if retention <= 0:
            return []
        cutoff = _time.time() - retention * 86400
        removed: list[str] = []
        for path in _bkz.iter_backup_files(self._backup_dir()):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed.append(path.name)
            except OSError:
                continue
        if removed:
            self._audit.record(
                actor="system", action="backup.local_pruned", target_type="backup_retention",
                target_id=str(retention), payload={"removed": removed, "retention_days": retention},
            )
        return removed

    # ── Count-based retention (cap how many backups are kept) ──
    # Default cap on retained local backups. Lowered from 60 (~2 GB) to keep
    # instance/backups/ small by default; override with HOBERADIUS_BACKUP_MAX_COUNT.
    # A per-tenant DB setting / license-contract value still takes precedence
    # (see backup_max_count + _contract_backup_max_count below).
    BACKUP_MAX_COUNT_DEFAULT = max(1, int(env_settings.env("HOBERADIUS_BACKUP_MAX_COUNT") or 10))

    def _contract_backup_max_count(self, *, tenant_id: int) -> int | None:
        """Backup cap defined on the license panel (per edition/fees), delivered
        in the runtime contract as limits.backups.max_count. Authoritative."""
        try:
            from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore, SNAPSHOT_CAPACITY
            snap = LicenseAdminSnapshotStore().latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY)
            if snap and isinstance(snap.get("payload_json"), dict):
                pj = snap["payload_json"]
                limits = (pj.get("contract") or {}).get("limits") or pj.get("limits") or {}
                raw = (limits.get("backups") or {}).get("max_count")
                n = int(raw)
                if n > 0:
                    return min(1000, n)
        except Exception:  # noqa: BLE001
            pass
        return None

    def backup_max_count(self, *, tenant_id: int) -> int:
        # The license panel is authoritative (set by edition/fees).
        contract_cap = self._contract_backup_max_count(tenant_id=tenant_id)
        if contract_cap:
            return contract_cap
        from app.radius.db.repos import tenants_repo
        try:
            raw = tenants_repo.get_setting(int(tenant_id), "backup_max_count", str(self.BACKUP_MAX_COUNT_DEFAULT))
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            n = self.BACKUP_MAX_COUNT_DEFAULT
        return max(1, min(1000, n))

    def backup_max_count_from_panel(self, *, tenant_id: int) -> bool:
        """True when the cap is dictated by the license contract (read-only on radius)."""
        return self._contract_backup_max_count(tenant_id=tenant_id) is not None

    def set_backup_max_count(self, *, tenant_id: int, value: int) -> int:
        from app.radius.db.repos import tenants_repo
        n = max(1, min(1000, int(value)))
        tenants_repo.set_setting(int(tenant_id), "backup_max_count", str(n))
        return n

    def prune_local_backups_by_count(self, *, tenant_id: int, max_count: int | None = None) -> list[str]:
        """Keep only the newest `max_count` regular backups; delete the oldest
        beyond that. Pre-restore snapshots are never auto-deleted."""
        cap = int(max_count if max_count is not None else self.backup_max_count(tenant_id=tenant_id))
        if cap <= 0:
            return []
        files = [
            p for p in _bkz.iter_backup_files(self._backup_dir())
            if p.is_file() and not p.name.startswith("pre-restore-")
        ]
        if len(files) <= cap:
            return []
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)  # newest first
        removed: list[str] = []
        for path in files[cap:]:
            try:
                path.unlink()
                removed.append(path.name)
            except OSError:
                continue
        if removed:
            self._audit.record(
                actor="system", action="backup.local_count_pruned", target_type="backup_retention",
                target_id=str(cap), payload={"removed": removed, "max_count": cap},
            )
        return removed

    def delete_local_backup(self, *, tenant_id: int, actor: str, name: str) -> dict:
        """Delete a single local backup file (validated). Audited."""
        path = self.resolve_local_backup_path(name=name)
        if not path:
            return {"ok": False, "message": "ملف النسخة غير موجود أو غير صالح."}
        try:
            path.unlink()
        except OSError as exc:
            return {"ok": False, "message": f"تعذّر حذف الملف: {exc}"}
        self._audit.record(
            actor=actor, action="backup.local_deleted", target_type="backup_file",
            target_id=name, payload={"name": name},
        )
        return {"ok": True, "message": f"تم حذف النسخة {name}."}

    # ── Unified "full backup": local → panel → Drive (one action) ──
    def panel_backup_enabled(self, *, tenant_id: int) -> bool:
        """Is the paid `backups` service active in the latest license contract?"""
        try:
            from app.radius.services.admin_panel_client import LicenseAdminSnapshotStore, SNAPSHOT_CAPACITY
            snap = LicenseAdminSnapshotStore().latest(tenant_id=tenant_id, snapshot_type=SNAPSHOT_CAPACITY)
            services = {}
            if snap and isinstance(snap.get("payload_json"), dict):
                pj = snap["payload_json"]
                services = (pj.get("contract") or {}).get("services") or pj.get("services") or {}
            return bool((services.get("backups") or {}).get("enabled"))
        except Exception:  # noqa: BLE001
            return False

    def run_full_backup(self, *, tenant_id: int, actor: str, lean: bool | None = None) -> dict:
        """Run local backup, then upload to the panel (if paid), and report
        جوجل درايف. يرجع خطوات الفحص بدون تنفيذ عمليات كتابة.

        ``lean`` controls whether the local copy is a lean core backup
        (default, small) or a full archive including the log/telemetry tables."""
        steps: list[dict] = []
        local = self.run_local_backup(tenant_id=tenant_id, actor=actor, lean=lean)
        local_ok = bool(local.get("verified"))
        steps.append({
            "key": "local", "label": "نسخة محلية",
            "status": "success" if local_ok else "failed",
            "message": "تم إنشاء النسخة المحلية والتحقق منها." if local_ok
                       else (local.get("run", {}).get("message") or "تعذّر إنشاء النسخة المحلية."),
        })

        panel_ok = False
        if not self.panel_backup_enabled(tenant_id=tenant_id):
            steps.append({"key": "panel", "label": "لوحة التراخيص", "status": "skipped",
                          "message": "الخدمة غير مفعّلة (خدمة مدفوعة)."})
        elif not local_ok:
            steps.append({"key": "panel", "label": "لوحة التراخيص", "status": "skipped",
                          "message": "تم التخطّي بسبب فشل النسخة المحلية."})
        else:
            try:
                from .license_admin_backup_upload import BackupUploadService
                up = BackupUploadService().upload_latest_backup(
                    tenant_id=tenant_id, dry_run=False, include_content=True)
                if up.get("ok") and not up.get("dry_run"):
                    panel_ok = True
                    content = bool((up.get("payload") or {}).get("content_included"))
                    steps.append({"key": "panel", "label": "لوحة التراخيص", "status": "success",
                                  "message": "تم الرفع إلى ملفك في لوحة التراخيص بالملف الكامل." if content
                                             else "تم تسجيل البيانات الوصفية على ملفك."})
                else:
                    from .license_admin_backup_upload import friendly_panel_backup_error
                    steps.append({"key": "panel", "label": "لوحة التراخيص", "status": "failed",
                                  "message": friendly_panel_backup_error(up)})
            except Exception as exc:  # noqa: BLE001
                steps.append({"key": "panel", "label": "لوحة التراخيص", "status": "failed",
                              "message": f"تعذّر رفع النسخة إلى لوحة التراخيص: {exc}"})

        # درايف: مساران ممكنان — (1) ربط محلي مباشر من الريدياس (device flow):
        # run_local_backup يرفع فعليًا ويعيد نتيجته في local["drive"]؛ نعرضها
        # بصدق (نجاح/فشل مع السبب، لا فشل صامت). (2) ربط عبر بوابة العميل على
        # لوحة التراخيص: اللوحة تُحوّل النسخة المرفوعة إلى درايف في الخلفية.
        drive_local = (local or {}).get("drive") if isinstance(local, dict) else None
        if isinstance(drive_local, dict):
            if drive_local.get("ok"):
                steps.append({"key": "drive", "label": "جوجل درايف", "status": "success",
                              "message": "تم رفع النسخة إلى جوجل درايف المربوط."})
            else:
                steps.append({"key": "drive", "label": "جوجل درايف", "status": "failed",
                              "message": f"تعذّر الرفع إلى جوجل درايف: {drive_local.get('error') or 'خطأ غير محدّد'}"})
        else:
            # لا ربط محلي → اعتمد مسار اللوحة (read the PANEL's Drive status).
            connected = False
            try:
                from .admin_panel_client import AdminPanelClient
                r = AdminPanelClient().fetch_google_drive_status()
                if r.get("ok"):
                    connected = bool((r.get("response") or {}).get("connected"))
            except Exception:  # noqa: BLE001
                connected = False
            if not connected:
                steps.append({"key": "drive", "label": "جوجل درايف", "status": "skipped",
                              "message": "غير مربوط — اربط جوجل درايف من بوابة العميل."})
            elif panel_ok:
                steps.append({"key": "drive", "label": "جوجل درايف", "status": "success",
                              "message": "وصلت النسخة للوحة وستُحوَّل إلى درايفك المربوط في الخلفية."})
            else:
                steps.append({"key": "drive", "label": "جوجل درايف", "status": "skipped",
                              "message": "يتطلّب نجاح الرفع إلى اللوحة أولًا."})
        return {"ok": local_ok, "steps": steps}

    def import_uploaded_backup(self, *, tenant_id: int, actor: str, fileobj, filename: str) -> dict:
        """Save a user-uploaded backup file into the local backups dir (validated)."""
        from datetime import datetime

        backup_dir = self._backup_dir()
        stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        # Save to a temp name first, then sniff the magic bytes to decide whether
        # this is a raw SQLite backup or a gzip-compressed one (.sqlite3.gz) and
        # give it the honest final extension. Both are accepted.
        staging = backup_dir / f"uploaded-{stamp}.part"
        try:
            fileobj.save(str(staging))
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": f"تعذّر حفظ الملف المرفوع: {exc}"}
        try:
            with open(staging, "rb") as fh:
                header = fh.read(16)
        except OSError as exc:
            try:
                staging.unlink()
            except OSError:
                pass
            return {"ok": False, "message": f"تعذّرت قراءة الملف: {exc}"}
        if header.startswith(_bkz.GZIP_MAGIC):
            # A gzip backup is valid only if it inflates to a SQLite database.
            if not _bkz.gzip_sqlite_header_ok(staging):
                try:
                    staging.unlink()
                except OSError:
                    pass
                return {"ok": False, "message": "الملف المضغوط ليس نسخة قاعدة بيانات SQLite صالحة."}
            safe = f"uploaded-{stamp}.sqlite3.gz"
        elif header.startswith(b"SQLite format 3"):
            safe = f"uploaded-{stamp}.sqlite3"
        else:
            try:
                staging.unlink()
            except OSError:
                pass
            return {"ok": False, "message": "الملف ليس قاعدة بيانات SQLite صالحة."}
        target = backup_dir / safe
        try:
            staging.replace(target)
        except OSError as exc:
            try:
                staging.unlink()
            except OSError:
                pass
            return {"ok": False, "message": f"تعذّر حفظ الملف المرفوع: {exc}"}
        try:
            job = operations_repo.ensure_backup_job(tenant_id, actor=actor)
            operations_repo.record_backup_run(
                tenant_id, job_id=job.get("id"), status="success",
                path=str(target), message="نسخة مرفوعة من جهاز المستخدم.")
        except Exception:  # noqa: BLE001
            pass
        self._audit.record(actor=actor, action="backup.uploaded_import", target_type="backup_file",
                           target_id=safe, payload={"name": safe, "original": str(filename)[:160]})
        try:
            self.prune_local_backups_by_count(tenant_id=tenant_id)
        except Exception:  # noqa: BLE001
            pass
        return {"ok": True, "name": safe, "message": "تم رفع النسخة من جهازك وحفظها محليًا."}

    # ── Scheduling (auto backups) ──
    BACKUP_SCHEDULE_INTERVALS = {"6h": 6 * 3600, "12h": 12 * 3600, "daily": 86400, "weekly": 7 * 86400}

    def get_backup_schedule(self, *, tenant_id: int) -> dict:
        from app.radius.db.repos import tenants_repo
        enabled = str(tenants_repo.get_setting(tenant_id, "backup_schedule_enabled", "0")).strip().lower() in {"1", "true", "yes", "on"}
        interval = str(tenants_repo.get_setting(tenant_id, "backup_schedule_interval", "daily")).strip() or "daily"
        if interval not in self.BACKUP_SCHEDULE_INTERVALS:
            interval = "daily"
        last_run = str(tenants_repo.get_setting(tenant_id, "backup_schedule_last_run", "")).strip()
        return {"enabled": enabled, "interval": interval,
                "interval_seconds": self.BACKUP_SCHEDULE_INTERVALS[interval], "last_run": last_run}

    def set_backup_schedule(self, *, tenant_id: int, enabled: bool, interval: str) -> dict:
        from app.radius.db.repos import tenants_repo
        interval = interval if interval in self.BACKUP_SCHEDULE_INTERVALS else "daily"
        tenants_repo.set_setting(tenant_id, "backup_schedule_enabled", "1" if enabled else "0")
        tenants_repo.set_setting(tenant_id, "backup_schedule_interval", interval)
        return self.get_backup_schedule(tenant_id=tenant_id)

    def mark_schedule_ran(self, *, tenant_id: int) -> None:
        from app.radius.db.repos import tenants_repo
        from datetime import datetime
        tenants_repo.set_setting(tenant_id, "backup_schedule_last_run", datetime.utcnow().isoformat() + "Z")

    def schedule_due(self, *, tenant_id: int) -> bool:
        from datetime import datetime
        sched = self.get_backup_schedule(tenant_id=tenant_id)
        if not sched["enabled"]:
            return False
        last = sched["last_run"]
        if not last:
            return True
        try:
            prev = datetime.fromisoformat(last.replace("Z", ""))
        except ValueError:
            return True
        return (datetime.utcnow() - prev).total_seconds() >= sched["interval_seconds"]

    def list_local_backups(self, *, tenant_id: int = 1) -> list[dict]:
        """Return the local backup files (newest first) for the UI."""
        from datetime import datetime

        backup_dir = self._backup_dir()
        items: list[dict] = []
        for path in _bkz.iter_backup_files(backup_dir):
            if not path.is_file():
                continue
            stat = path.stat()
            items.append({
                "name": path.name,
                "size": int(stat.st_size),
                "size_mb": round(stat.st_size / 1048576, 2),
                "modified_at": datetime.utcfromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "is_snapshot": path.name.startswith("pre-restore-"),
                "compressed": _bkz.is_gzip_name(path.name),
            })
        items.sort(key=lambda x: x["modified_at"], reverse=True)
        return items

    def resolve_local_backup_path(self, *, name: str) -> Path | None:
        """Validate a backup file name and resolve it inside the backups dir."""
        cleaned = os.path.basename(str(name or "").strip())
        if not _bkz.is_backup_name(cleaned):
            return None
        base = self._backup_dir().resolve()
        path = (base / cleaned).resolve()
        if base not in path.parents or not path.exists() or not path.is_file():
            return None
        return path

    # Tables surfaced in the per-backup content summary (read-only counts).
    BACKUP_SUMMARY_TABLES = [
        ("subscribers", "المشتركون"),
        ("cards", "الكروت"),
        ("access_plans", "الباقات"),
        ("card_batches", "دفعات الكروت"),
        ("vouchers", "القسائم"),
        ("subscriber_recharges", "عمليات التعبئة"),
    ]

    def summarize_local_backup(self, *, name: str) -> dict:
        """Open a local backup file read-only and count rows in known tables.

        Returns {"ok": bool, "items": [{key,label,count}]}. Never writes and
        never raises — a missing/corrupt file yields ok=False.
        """
        path = self.resolve_local_backup_path(name=name)
        if not path:
            return {"ok": False, "items": []}
        items: list[dict] = []
        # A gzip-compressed backup is transparently inflated to a temporary
        # plain SQLite file (deleted in the finally) so the read-only counting
        # path below is identical for both .sqlite3 and .sqlite3.gz.
        tmp: Path | None = None
        try:
            if _bkz.is_gzip_file(path):
                tmp = _bkz.decompress_to_temp(path, dir=path.parent)
                open_path = tmp
            else:
                open_path = path
            con = sqlite3.connect(f"file:{open_path.as_posix()}?mode=ro", uri=True, timeout=3)
            try:
                cur = con.cursor()
                cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
                present = {str(r[0]) for r in cur.fetchall()}
                for table, label in self.BACKUP_SUMMARY_TABLES:
                    if table not in present:
                        continue
                    try:
                        cur.execute('SELECT COUNT(*) FROM "%s"' % table)  # noqa: S608 — fixed allow-list
                        items.append({"key": table, "label": label, "count": int(cur.fetchone()[0])})
                    except sqlite3.Error:
                        continue
            finally:
                con.close()
        except (sqlite3.Error, OSError, EOFError):
            return {"ok": False, "items": []}
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return {"ok": True, "items": items}

    def restore_local_backup(self, *, tenant_id: int, actor: str, name: str) -> dict:
        """Restore the live DB from a local backup file. Heavily gated.

        Safety: requires HOBERADIUS_LOCAL_RESTORE_ENABLED, always takes a
        pre-restore snapshot of the current DB first, validates the file, and
        audits every step. Never runs automatically — only on explicit POST.
        """
        from datetime import datetime

        if str(env_settings.env("HOBERADIUS_LOCAL_RESTORE_DISABLED", "")).strip().lower() in {"1", "true", "yes", "on"}:
            return {"ok": False, "code": "restore_disabled",
                    "message": "الاستعادة داخل التطبيق معطّلة على هذا الخادم."}

        source = self.resolve_local_backup_path(name=name)
        if not source:
            return {"ok": False, "code": "not_found", "message": "ملف النسخة غير موجود أو غير صالح."}

        # 1) Pre-restore snapshot of the CURRENT database (rollback safety net).
        snapshot_dir = self._backup_dir()
        snapshot = snapshot_dir / f"pre-restore-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.sqlite3"
        try:
            with sqlite3.connect(str(snapshot)) as dest:
                db().backup(dest)
            snapshot_ok = snapshot.exists() and snapshot.stat().st_size > 0
        except sqlite3.Error as exc:
            self._audit.record(
                actor=actor, action="backup.restore_aborted", target_type="backup_file",
                target_id=name, severity="error", result_status="failed",
                error_message=f"snapshot failed: {exc}",
            )
            return {"ok": False, "code": "snapshot_failed",
                    "message": f"تعذّر أخذ نسخة احترازية قبل الاستعادة: {exc}"}
        if not snapshot_ok:
            return {"ok": False, "code": "snapshot_failed",
                    "message": "تعذّر أخذ نسخة احترازية قبل الاستعادة."}

        # 2) Materialize the source. Compressed backups (.sqlite3.gz) are sniffed
        #    by their gzip magic bytes (1f 8b) — not just the extension — and
        #    inflated on the fly to a temporary plain SQLite file. Legacy
        #    uncompressed .sqlite3 backups are used directly. Either way the
        #    same SQLite backup() swap below applies them to the live DB.
        candidate = source
        tmp: Path | None = None
        if _bkz.is_gzip_file(source):
            try:
                candidate = _bkz.decompress_to_temp(source, dir=snapshot_dir)
                tmp = candidate
            except Exception as exc:  # noqa: BLE001
                self._audit.record(
                    actor=actor, action="backup.restore_failed", target_type="backup_file",
                    target_id=name, severity="critical", result_status="failed",
                    error_message=f"decompress failed: {exc}",
                    payload={"snapshot": snapshot.name},
                )
                return {"ok": False, "code": "decompress_failed",
                        "message": f"تعذّر فكّ ضغط النسخة. النسخة الاحترازية محفوظة: {snapshot.name}. الخطأ: {exc}"}

        # 3) Restore: copy the chosen backup INTO the live database.
        try:
            # Re-verify the candidate is a real SQLite database before it
            # overwrites the live one — a truncated/corrupt file must never
            # replace production.
            with sqlite3.connect(str(candidate)) as probe:
                probe.execute("PRAGMA schema_version;").fetchone()
            src_conn = sqlite3.connect(str(candidate))
            try:
                src_conn.backup(db())
            finally:
                src_conn.close()
        except sqlite3.Error as exc:
            self._audit.record(
                actor=actor, action="backup.restore_failed", target_type="backup_file",
                target_id=name, severity="critical", result_status="failed",
                error_message=str(exc),
                payload={"snapshot": snapshot.name},
            )
            return {"ok": False, "code": "restore_failed",
                    "message": f"فشلت الاستعادة. النسخة الاحترازية محفوظة: {snapshot.name}. الخطأ: {exc}"}
        finally:
            if tmp is not None:
                try:
                    tmp.unlink()
                except OSError:
                    pass

        self._audit.record(
            actor=actor, action="backup.restore_applied", target_type="backup_file",
            target_id=name, severity="critical", result_status="success",
            payload={"restored_from": name, "pre_restore_snapshot": snapshot.name},
        )
        return {"ok": True, "restored_from": name, "snapshot": snapshot.name,
                "message": f"تمت الاستعادة من «{name}». تم حفظ نسخة احترازية: {snapshot.name}."}


def get_operations_service() -> OperationsService:
    from .audit import get_audit_service
    return OperationsService(get_audit_service())
