"""PDF generation for consultations and lab orders. Pure-Python via reportlab."""
import os
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from io import BytesIO
from typing import List, Optional

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm, mm
from reportlab.lib.colors import HexColor, white, black, Color
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

logger = logging.getLogger(__name__)

ACCENT = HexColor("#4AA391")
ACCENT_DIM = HexColor("#3d8a79")
TEXT = HexColor("#1a202c")
MUTED = HexColor("#5a6673")
BORDER = HexColor("#d8d4cb")
BG_SOFT = HexColor("#f1f0ea")
BG_CARD = HexColor("#ffffff")
BG_DANGER = HexColor("#FEE2E2")
TXT_DANGER = HexColor("#B91C1C")
BG_WARN = HexColor("#FEF3C7")
TXT_WARN = HexColor("#92400E")
BG_OK = HexColor("#D1FAE5")
TXT_OK = HexColor("#047857")

# ---------- Font registration ----------

_FONT_NAME = "AvrisFont"
_FONT_BOLD = "AvrisFont-Bold"
_font_registered = False


def _register_fonts():
    """Register a Cyrillic-capable TTF on first use. Falls back to Helvetica if nothing found."""
    global _font_registered, _FONT_NAME, _FONT_BOLD
    if _font_registered:
        return
    candidates = [
        # macOS
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
        # Linux DejaVu
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("/usr/share/fonts/dejavu/DejaVuSans.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
        # Linux Liberation
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        # Windows
        ("C:\\Windows\\Fonts\\arial.ttf", "C:\\Windows\\Fonts\\arialbd.ttf"),
    ]
    for reg, bold in candidates:
        if os.path.exists(reg):
            try:
                pdfmetrics.registerFont(TTFont(_FONT_NAME, reg))
                if os.path.exists(bold):
                    pdfmetrics.registerFont(TTFont(_FONT_BOLD, bold))
                else:
                    _FONT_BOLD = _FONT_NAME
                _font_registered = True
                logger.info("PDF font registered: %s", reg)
                return
            except Exception as e:
                logger.warning("Failed to register %s: %s", reg, e)
                continue
    # Fallback — Helvetica won't render Cyrillic correctly but PDF will still produce
    _FONT_NAME = "Helvetica"
    _FONT_BOLD = "Helvetica-Bold"
    _font_registered = True
    logger.warning("No TTF found — falling back to Helvetica (Cyrillic may break)")


def _styles():
    _register_fonts()
    base = getSampleStyleSheet()["BodyText"]
    return {
        "h1": ParagraphStyle("h1", parent=base, fontName=_FONT_BOLD, fontSize=22,
                             textColor=TEXT, leading=26, spaceAfter=2),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontName=_FONT_NAME, fontSize=10,
                                   textColor=MUTED, leading=14, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=base, fontName=_FONT_BOLD, fontSize=11,
                             textColor=ACCENT_DIM, leading=14, spaceAfter=4,
                             textTransform="uppercase"),
        "label": ParagraphStyle("label", parent=base, fontName=_FONT_BOLD, fontSize=8,
                                textColor=MUTED, leading=10, spaceAfter=2),
        "value": ParagraphStyle("value", parent=base, fontName=_FONT_NAME, fontSize=10,
                                textColor=TEXT, leading=14, spaceAfter=8),
        "body": ParagraphStyle("body", parent=base, fontName=_FONT_NAME, fontSize=10,
                               textColor=TEXT, leading=14),
        "soap_label": ParagraphStyle("soap_label", parent=base, fontName=_FONT_BOLD, fontSize=10,
                                     textColor=ACCENT_DIM, leading=12, spaceAfter=4),
        "soap_body": ParagraphStyle("soap_body", parent=base, fontName=_FONT_NAME, fontSize=10,
                                    textColor=TEXT, leading=14, spaceAfter=10),
        "footer": ParagraphStyle("footer", parent=base, fontName=_FONT_NAME, fontSize=8,
                                 textColor=MUTED, leading=11, alignment=TA_CENTER),
        "ai": ParagraphStyle("ai", parent=base, fontName=_FONT_NAME, fontSize=10,
                             textColor=TEXT, leading=14, leftIndent=12, rightIndent=12,
                             spaceBefore=6, spaceAfter=6),
        "lang_badge": ParagraphStyle("lang_badge", parent=base, fontName=_FONT_BOLD, fontSize=8,
                                     textColor=white, leading=10, alignment=TA_CENTER),
    }


# Надстрочные цифры (×10⁹/л в лабораторных единицах) отсутствуют в наших TTF и
# печатались квадратом. Переводим их в reportlab-разметку <super> — вставка
# ПОСЛЕ экранирования, иначе теги сами станут текстом.
_SUPERSCRIPT_DIGITS = {
    "⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4",
    "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
}


def _esc(s) -> str:
    """Escape a string for reportlab Paragraph (handles None and HTML chars)."""
    if s is None:
        return ""
    s = str(s)
    s = (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
         .replace("\n", "<br/>"))
    for char, digit in _SUPERSCRIPT_DIGITS.items():
        if char in s:
            s = s.replace(char, f"<super>{digit}</super>")
    return s


def _brand_block(styles, lang: Optional[str] = None):
    """Header with AVRIS title + Hyperion Labs subtitle, optional lang pill."""
    rows = [[
        Paragraph(f'<font name="{_FONT_BOLD}" size="22" color="#1a202c">AVRIS</font>'
                  f'<font color="#4AA391"> ●</font>', styles["h1"]),
    ]]
    if lang:
        # Language pill on the right
        lang_para = Paragraph(
            f'<para alignment="right"><font name="{_FONT_BOLD}" size="9" color="#ffffff" '
            f'backColor="#4AA391"> &nbsp;{lang.upper()}&nbsp; </font></para>',
            styles["body"],
        )
        rows[0].append(lang_para)
        t = Table(rows, colWidths=[14 * cm, 3 * cm])
    else:
        t = Table(rows, colWidths=[17 * cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (-1, 0), (-1, 0), "RIGHT"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _meta_table(items):
    """Two-column key/value rows used for patient/doctor metadata."""
    if not items:
        return Spacer(0, 0)
    styles = _styles()
    data = []
    for label, value in items:
        data.append([
            Paragraph(_esc(label).upper(), styles["label"]),
            Paragraph(_esc(value), styles["value"]),
        ])
    t = Table(data, colWidths=[3.5 * cm, 13.5 * cm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _hr(color=BORDER):
    """Horizontal rule via a thin Table."""
    t = Table([[""]], colWidths=[17 * cm], rowHeights=[1])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), color),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return t


def _footer(styles):
    return Paragraph(
        f"Сгенерировано Avris AI · Hyperion Labs · {datetime.utcnow().year}",
        styles["footer"],
    )


# Часовой пояс, в котором печатаются даты в документах.
#
# В БД все отметки времени — наивный UTC. Печатать их как есть значит
# сдвинуть каждый приём на пять часов назад, а у записей после 19:00 —
# ещё и на день: осмотр вечера 18 июля в выписке становится осмотром 18-го
# утром, а осмотр в 02:00 — вчерашним. Документ, который пациент несёт
# другому врачу или в страховую, обязан показывать местное время приёма.
_DOC_TZ = ZoneInfo(os.getenv("CLINIC_TIMEZONE", "Asia/Dushanbe"))


def _to_local(dt: datetime) -> datetime:
    """Наивный UTC из БД → местное время клиники."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_DOC_TZ)


def _format_dt(dt) -> str:
    if not dt:
        return "—"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        except ValueError:
            return dt
    try:
        return _to_local(dt).strftime("%d.%m.%Y, %H:%M")
    except Exception:
        return str(dt)


# ---------- Public render functions ----------

def render_consultation_pdf(consultation, patient, doctor) -> bytes:
    """Render consultation as PDF, returns bytes."""
    styles = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Консультация #{consultation.id}",
        author="Avris AI",
    )

    story = []

    # Header
    story.append(_brand_block(styles, lang=consultation.language))
    story.append(Paragraph("Hyperion Labs · Голосовая медицинская документация", styles["subtitle"]))
    story.append(_hr(ACCENT))
    story.append(Spacer(0, 0.5 * cm))

    # Title row: Консультация #N + дата
    story.append(Paragraph(f"Консультация #{consultation.id}", styles["h1"]))
    story.append(Paragraph(_format_dt(consultation.created_at), styles["subtitle"]))

    # Doctor + Patient meta
    meta = []
    if doctor:
        meta.append(("Врач", f"{doctor.full_name or ''}{(' · ' + doctor.specialty) if getattr(doctor, 'specialty', None) else ''}"))
    if patient:
        meta.append(("Пациент", patient.full_name or "—"))
        sub_parts = []
        if getattr(patient, "date_of_birth", None):
            try:
                sub_parts.append("д.р. " + patient.date_of_birth.strftime("%d.%m.%Y"))
            except Exception:
                sub_parts.append("д.р. " + str(patient.date_of_birth))
        if patient.age is not None:
            sub_parts.append(f"{patient.age} лет")
        if patient.gender:
            sub_parts.append(patient.gender)
        if patient.ward:
            sub_parts.append(patient.ward)
        if sub_parts:
            meta.append(("", " · ".join(sub_parts)))
        if getattr(patient, "record_number", None):
            meta.append(("№ карты/ИБ", patient.record_number))
        if patient.diagnoses:
            meta.append(("Диагнозы", ", ".join(patient.diagnoses)))
        if patient.allergies:
            meta.append(("Аллергии", ", ".join(patient.allergies)))
    if not patient and consultation.patient_id is None:
        meta.append(("Пациент", "—"))
    story.append(_meta_table(meta))
    story.append(Spacer(0, 0.4 * cm))
    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))

    # SOAP block
    story.append(Paragraph("ЗАПИСЬ ОСМОТРА (SOAP) · Avris AI", styles["h2"]))
    story.append(Spacer(0, 0.2 * cm))
    soap_rows = [
        ("S — Subjective · Жалобы", consultation.soap_s),
        ("O — Objective · Объективно", consultation.soap_o),
        ("A — Assessment · Оценка", consultation.soap_a),
        ("P — Plan · План", consultation.soap_p),
    ]
    for label, text in soap_rows:
        story.append(KeepTogether([
            Paragraph(_esc(label), styles["soap_label"]),
            Paragraph(_esc(text or "—"), styles["soap_body"]),
        ]))

    story.append(Spacer(0, 0.2 * cm))
    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))

    # Transcript
    if consultation.transcript:
        story.append(Paragraph("ТРАНСКРИПТ · Whisper AI", styles["h2"]))
        story.append(Spacer(0, 0.2 * cm))
        # Transcript in a soft-bg box
        tbl = Table(
            [[Paragraph(_esc(consultation.transcript), styles["body"])]],
            colWidths=[17 * cm],
        )
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BG_SOFT),
            ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(tbl)
        story.append(Spacer(0, 0.4 * cm))

    if getattr(consultation, "duration_seconds", None):
        sec = consultation.duration_seconds or 0
        story.append(Paragraph(
            f"<i>Длительность записи: {sec // 60:02d}:{sec % 60:02d}</i>",
            styles["body"],
        ))
        story.append(Spacer(0, 0.3 * cm))

    story.append(Spacer(0, 0.6 * cm))
    story.append(_footer(styles))

    doc.build(story)
    return buf.getvalue()


_EPI_KIND_TITLE = {"discharge": "Выписной эпикриз", "interim": "Этапный эпикриз"}
_EPI_STATUS_RU = {"stable": "стабильное", "watch": "наблюдение",
                  "serious": "тяжёлое", "critical": "критическое"}
_DEPT_RU = {"therapy": "Терапия", "cardiology": "Кардиология", "surgery": "Хирургия",
            "neurology": "Неврология", "pulmonology": "Пульмонология",
            "icu": "ОРИТ", "post_icu": "Пост-реанимация", "other": "Другое"}


def render_epicrisis_pdf(epicrisis, patient, doctor) -> bytes:
    """Render epicrisis as PDF. Тело — плоский текст врача; строки-заголовки
    (ЗАГЛАВНЫМИ, короткие) оформляются как секции."""
    styles = _styles()
    buf = BytesIO()
    title = _EPI_KIND_TITLE.get(epicrisis.kind, "Эпикриз")
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"{title} #{epicrisis.id}",
        author="Avris AI",
    )

    story = []
    story.append(_brand_block(styles, lang=epicrisis.language))
    story.append(Paragraph("Hyperion Labs · Голосовая медицинская документация", styles["subtitle"]))
    story.append(_hr(ACCENT))
    story.append(Spacer(0, 0.5 * cm))

    story.append(Paragraph(title, styles["h1"]))
    story.append(Paragraph(_format_dt(epicrisis.created_at), styles["subtitle"]))

    # Паспортная шапка — с ДР и № карты (официальные поля)
    meta = []
    if doctor:
        meta.append(("Врач", f"{doctor.full_name or ''}{(' · ' + doctor.specialty) if getattr(doctor, 'specialty', None) else ''}"))
    if patient:
        meta.append(("Пациент", patient.full_name or "—"))
        sub_parts = []
        if getattr(patient, "date_of_birth", None):
            try:
                sub_parts.append("д.р. " + patient.date_of_birth.strftime("%d.%m.%Y"))
            except Exception:
                sub_parts.append("д.р. " + str(patient.date_of_birth))
        if patient.age is not None:
            sub_parts.append(f"{patient.age} лет")
        if patient.gender:
            sub_parts.append(patient.gender)
        if sub_parts:
            meta.append(("", " · ".join(sub_parts)))
        if getattr(patient, "record_number", None):
            meta.append(("№ карты/ИБ", patient.record_number))
        loc = " · ".join(x for x in [_DEPT_RU.get(patient.department, patient.department), patient.ward] if x)
        if loc:
            meta.append(("Отделение", loc))
        adm_parts = []
        if getattr(patient, "admission_date", None):
            adm_parts.append(_format_dt(patient.admission_date))
        if getattr(patient, "admission_status", None):
            adm_parts.append(_EPI_STATUS_RU.get(patient.admission_status, patient.admission_status))
        if adm_parts:
            meta.append(("Поступление", " · ".join(adm_parts)))
        if getattr(patient, "admission_diagnosis", None):
            meta.append(("Диагноз при пост.", patient.admission_diagnosis))
    story.append(_meta_table(meta))
    story.append(Spacer(0, 0.4 * cm))
    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))

    # Тело: секционные заголовки → h2, остальное → body-абзацы
    for raw_line in (epicrisis.body or "").split("\n"):
        line = raw_line.strip()
        if not line:
            story.append(Spacer(0, 0.18 * cm))
            continue
        is_header = (len(line) < 70 and line == line.upper()
                     and any(ch.isalpha() for ch in line))
        if is_header:
            story.append(Spacer(0, 0.2 * cm))
            story.append(Paragraph(_esc(line), styles["h2"]))
        else:
            story.append(Paragraph(_esc(raw_line), styles["body"]))

    story.append(Spacer(0, 0.8 * cm))
    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))
    sign_name = (doctor.full_name or "") if doctor else ""
    story.append(Paragraph(
        f"Лечащий врач: {_esc(sign_name)} ____________________",
        styles["body"],
    ))
    story.append(Spacer(0, 0.6 * cm))
    story.append(_footer(styles))

    doc.build(story)
    return buf.getvalue()


def render_lab_order_pdf(order, patient, doctor) -> bytes:
    """Render lab order results as PDF, returns bytes."""
    styles = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title=f"Направление #{order.id}",
        author="Avris AI",
    )

    story = []
    story.append(_brand_block(styles))
    story.append(Paragraph("Hyperion Labs · Lab Connect", styles["subtitle"]))
    story.append(_hr(ACCENT))
    story.append(Spacer(0, 0.5 * cm))

    status_label = "Получено" if order.status == "received" else "Ожидание"
    story.append(Paragraph(
        f"Направление #{order.id} · <font color='{(TXT_OK if order.status=='received' else TXT_WARN).hexval()}'>{status_label}</font>",
        styles["h1"],
    ))
    story.append(Paragraph(_format_dt(order.created_at), styles["subtitle"]))

    meta = []
    if doctor:
        meta.append(("Врач", f"{doctor.full_name or ''}{(' · ' + doctor.specialty) if getattr(doctor, 'specialty', None) else ''}"))
    if patient:
        meta.append(("Пациент", patient.full_name or "—"))
        sub_parts = []
        if getattr(patient, "date_of_birth", None):
            try:
                sub_parts.append("д.р. " + patient.date_of_birth.strftime("%d.%m.%Y"))
            except Exception:
                sub_parts.append("д.р. " + str(patient.date_of_birth))
        if patient.age is not None:
            sub_parts.append(f"{patient.age} лет")
        if patient.ward:
            sub_parts.append(patient.ward)
        if sub_parts:
            meta.append(("", " · ".join(sub_parts)))
        if getattr(patient, "record_number", None):
            meta.append(("№ карты/ИБ", patient.record_number))
    meta.append(("Код QR", order.qr_token))
    story.append(_meta_table(meta))
    story.append(Spacer(0, 0.4 * cm))
    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))

    # Tests / Results table
    if order.status == "received" and order.results:
        story.append(Paragraph("РЕЗУЛЬТАТЫ", styles["h2"]))
        story.append(Spacer(0, 0.2 * cm))
        header_row = [
            Paragraph("<b>Показатель</b>", styles["body"]),
            Paragraph("<b>Значение</b>", styles["body"]),
            Paragraph("<b>Ед.</b>", styles["body"]),
            Paragraph("<b>Норма</b>", styles["body"]),
        ]
        rows = [header_row]
        for key, item in (order.results or {}).items():
            if not isinstance(item, dict):
                item = {"value": item}
            rows.append([
                Paragraph(_esc(key), styles["body"]),
                Paragraph(_esc(item.get("value", "")), styles["body"]),
                Paragraph(_esc(item.get("unit", "")), styles["body"]),
                Paragraph(_esc(item.get("range", "")), styles["body"]),
            ])
        tbl = Table(rows, colWidths=[7 * cm, 4 * cm, 2.5 * cm, 3.5 * cm])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BG_SOFT),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, BORDER),
            ("LINEBELOW", (0, 0), (-1, -1), 0.25, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(tbl)
        story.append(Spacer(0, 0.4 * cm))

        if order.ai_comment:
            story.append(Paragraph("AI-КОММЕНТАРИЙ · Avris AI", styles["h2"]))
            story.append(Spacer(0, 0.1 * cm))
            ai_tbl = Table(
                [[Paragraph(_esc(order.ai_comment), styles["body"])]],
                colWidths=[17 * cm],
            )
            ai_tbl.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), HexColor("#e9f5f2")),
                ("BOX", (0, 0), (-1, -1), 0.5, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]))
            story.append(ai_tbl)
            story.append(Spacer(0, 0.4 * cm))
    else:
        # Pending — show ordered tests
        story.append(Paragraph("НАЗНАЧЕННЫЕ АНАЛИЗЫ", styles["h2"]))
        story.append(Spacer(0, 0.2 * cm))
        tests = list(order.tests or [])
        if tests:
            rows = [[Paragraph(_esc(k), styles["body"])] for k in tests]
            tbl = Table(rows, colWidths=[17 * cm])
            tbl.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]))
            story.append(tbl)
        else:
            story.append(Paragraph("Тесты не выбраны", styles["body"]))
        story.append(Spacer(0, 0.4 * cm))

    story.append(Spacer(0, 0.4 * cm))
    story.append(_footer(styles))

    doc.build(story)
    return buf.getvalue()


# ---------- Patient-facing export ----------

def _format_date_only(d) -> str:
    if not d:
        return "—"
    try:
        return d.strftime("%d.%m.%Y")
    except Exception:
        return str(d)


# В БД пол лежит кодом ('female'/'male') — в PDF он печатался как есть.
_GENDER_RU = {"female": "женский", "male": "мужской", "other": "другой",
              "ж": "женский", "м": "мужской"}


def _gender_ru(value) -> str:
    if not value:
        return ""
    return _GENDER_RU.get(str(value).strip().lower(), str(value))


def render_patient_record_pdf(account, visits, labs) -> bytes:
    """Медкарта пациента одним файлом — платная фича Plus («Экспорт в PDF»).

    Это выгрузка ДЛЯ ПАЦИЕНТА (взять с собой к другому врачу, приложить к
    страховке), а не документ строгой формы: приёмы показываются
    человеческими сводками, которые пациент видит в приложении, а не сырым
    SOAP. Врачебные PDF (консультация/эпикриз) остаются отдельными формами.

    ``visits`` — список кортежей (consultation, doctor_name, summary|None),
    ``labs`` — список (order, doctor_name); порядок задаёт вызывающий.
    """
    styles = _styles()
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=2 * cm, bottomMargin=2 * cm,
        title="Медицинская карта Avris",
        author="Avris AI",
    )

    story = [
        _brand_block(styles),
        Paragraph("Hyperion Labs · Медицинская карта пациента", styles["subtitle"]),
        _hr(ACCENT),
        Spacer(0, 0.5 * cm),
        Paragraph("Медицинская карта", styles["h1"]),
        Paragraph(f"Avris ID: {_esc(account.avris_patient_id)} · выгружено "
                  f"{_format_dt(datetime.utcnow())}", styles["subtitle"]),
    ]

    # ---- Профиль
    meta = [("Пациент", account.full_name or "—")]
    sub = []
    if account.date_of_birth:
        sub.append("д.р. " + _format_date_only(account.date_of_birth))
    if account.gender:
        sub.append(_gender_ru(account.gender))
    if account.blood_type:
        sub.append("группа крови " + account.blood_type)
    if sub:
        meta.append(("", " · ".join(sub)))
    body = []
    if account.height:
        body.append(f"рост {account.height:g} см")
    if account.weight:
        body.append(f"вес {account.weight:g} кг")
    if body:
        meta.append(("Антропометрия", " · ".join(body)))
    if account.allergies:
        meta.append(("Аллергии", ", ".join(account.allergies)))
    if account.chronic_conditions:
        meta.append(("Хронические", ", ".join(account.chronic_conditions)))
    if account.medications:
        meta.append(("Принимает", ", ".join(account.medications)))
    story.append(_meta_table(meta))
    story.append(Spacer(0, 0.4 * cm))
    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))

    # ---- Приёмы
    story.append(Paragraph(f"ПРИЁМЫ · {len(visits)}", styles["h2"]))
    story.append(Spacer(0, 0.2 * cm))
    if not visits:
        story.append(Paragraph("Приёмов пока нет", styles["body"]))
        story.append(Spacer(0, 0.3 * cm))
    for consultation, doctor_name, summary in visits:
        block = [
            Paragraph(f"{_format_dt(consultation.created_at)} · "
                      f"{_esc(doctor_name or 'Врач')}", styles["soap_label"]),
        ]
        if summary is not None and summary.summary:
            block.append(Paragraph(_esc(summary.summary), styles["soap_body"]))
            if summary.prescriptions:
                block.append(Paragraph("Назначения", styles["label"]))
                block.append(Paragraph(_esc(summary.prescriptions), styles["soap_body"]))
        else:
            # Сводка ещё готовится — честно говорим, а не оставляем пустое место.
            block.append(Paragraph("Сводка визита пока не готова", styles["soap_body"]))
        story.append(KeepTogether(block))

    story.append(_hr())
    story.append(Spacer(0, 0.4 * cm))

    # ---- Анализы
    story.append(Paragraph(f"АНАЛИЗЫ · {len(labs)}", styles["h2"]))
    story.append(Spacer(0, 0.2 * cm))
    if not labs:
        story.append(Paragraph("Анализов пока нет", styles["body"]))
    for order, doctor_name in labs:
        block = [
            Paragraph(f"{_format_dt(order.created_at)} · "
                      f"{_esc(', '.join(order.tests or []) or 'Анализ')}", styles["soap_label"]),
        ]
        results = order.results if isinstance(order.results, dict) else None
        if results:
            rows = []
            for name, value in results.items():
                if isinstance(value, dict):
                    shown = " ".join(
                        str(value.get(k, "")).strip() for k in ("value", "unit")
                    ).strip()
                    ref = str(value.get("range", "") or "").strip()
                else:
                    shown, ref = str(value), ""
                rows.append([
                    Paragraph(_esc(name), styles["body"]),
                    Paragraph(_esc(shown), styles["body"]),
                    Paragraph(_esc(ref), styles["body"]),
                ])
            tbl = Table(rows, colWidths=[8 * cm, 5 * cm, 4 * cm])
            tbl.setStyle(TableStyle([
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            block.append(tbl)
        else:
            block.append(Paragraph("Результаты ещё не готовы", styles["soap_body"]))
        story.append(KeepTogether(block))
        story.append(Spacer(0, 0.3 * cm))

    story.append(Spacer(0, 0.4 * cm))
    story.append(Paragraph(
        "<i>Выгрузка из приложения Avris для личного использования. Не заменяет "
        "медицинские документы клиники и не является заключением врача.</i>",
        styles["body"]))
    story.append(Spacer(0, 0.4 * cm))
    story.append(_footer(styles))

    doc.build(story)
    return buf.getvalue()
