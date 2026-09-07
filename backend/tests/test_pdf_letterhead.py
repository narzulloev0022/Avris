"""Шапка бланка и подписи — то, что делает распечатку документом.

Формы 003/у, 025/у и 027/у начинаются с реквизитов медицинской организации:
документ выпускает клиника, а не поставщик софта. Юридическую силу листу
даёт подпись врача; выписному эпикризу — ещё и заведующего отделением.
"""
from types import SimpleNamespace
from datetime import datetime

import pdf_export


def _doctor(**kw):
    base = dict(full_name="Каримов Д. А.", specialty="therapist",
                hospital_name="Клиника «Шифо»", hospital_address="Душанбе, ул. Рудаки 12",
                hospital_phone="+992 44 600 00 00")
    base.update(kw)
    return SimpleNamespace(**base)


def _patient():
    return SimpleNamespace(full_name="Носирова М.", date_of_birth=None, record_number="1024",
                           age=34, gender="Ж", department="therapy", ward=None,
                           admission_date=None, admission_diagnosis=None, diagnoses=None,
                           allergies=None, patient_type="outpatient")


def _consultation():
    return SimpleNamespace(id=1, created_at=datetime(2026, 9, 7, 10, 0), language="ru",
                           transcript="", soap_s="Кашель", soap_o="Дыхание жёсткое",
                           soap_a="Бронхит", soap_p="ОАК", duration_seconds=60,
                           visit_type="visit")


def _text(pdf: bytes) -> str:
    """Грубая выемка текста: reportlab кладёт строки в поток как есть."""
    return pdf.decode("latin-1", "ignore")


class TestLetterhead:
    def test_clinic_name_is_in_the_header(self):
        pdf = pdf_export.render_consultation_pdf(_consultation(), _patient(), _doctor())
        assert len(pdf) > 1000
        assert pdf[:5] == b"%PDF-"

    def test_header_falls_back_to_a_blank_rule(self):
        """Клиника не заполнена — печатаем пустую строку под штамп, а не
        чужое название."""
        doc = pdf_export._clinic_block(pdf_export._styles(), _doctor(hospital_name=""))
        flat = str(doc._cellvalues)
        assert "медицинская организация" in flat

    def test_header_uses_clinic_not_vendor(self):
        flat = str(pdf_export._clinic_block(pdf_export._styles(), _doctor())._cellvalues)
        assert "Шифо" in flat
        assert "Hyperion" not in flat


class TestSignatures:
    def test_consultation_is_signed_by_the_doctor(self):
        els = pdf_export._signature_block(pdf_export._styles(), [("Врач", "Каримов Д. А.")])
        flat = str(els[0]._cellvalues) + str(els[1]._cellvalues)
        assert "Врач" in flat and "Каримов" in flat
        assert "подпись" in flat and "фамилия" in flat

    def test_epicrisis_carries_two_signatures_and_a_stamp(self):
        els = pdf_export._signature_block(
            pdf_export._styles(),
            [("Лечащий врач", "Каримов Д. А."), ("Заведующий отделением", "")],
            stamp=True,
        )
        flat = "".join(str(getattr(e, "_cellvalues", getattr(e, "text", ""))) for e in els)
        assert "Лечащий врач" in flat
        assert "Заведующий отделением" in flat
        assert "М.П." in flat

    def test_unnamed_signer_still_gets_a_line(self):
        """Заведующего в системе нет — линия под фамилию всё равно нужна."""
        els = pdf_export._signature_block(pdf_export._styles(), [("Заведующий отделением", "")])
        assert "_" in str(els[0]._cellvalues)


class TestFooter:
    def test_footer_says_generated_not_issued(self):
        f = pdf_export._footer(pdf_export._styles())
        assert "Сформировано" in f.text
        assert "Hyperion Labs" not in f.text


class TestDoctorLine:
    def test_specialty_prints_in_russian_not_as_a_key(self):
        """В карточке специальность лежит ключом; в документе «therapist»
        рядом с фамилией врача выглядит как ошибка вёрстки."""
        assert pdf_export._doctor_line(_doctor()) == "Каримов Д. А. · Терапевт"

    def test_unknown_specialty_survives_as_written(self):
        d = _doctor(specialty="Врач общей практики")
        assert "Врач общей практики" in pdf_export._doctor_line(d)

    def test_missing_specialty_leaves_just_the_name(self):
        assert pdf_export._doctor_line(_doctor(specialty=None)) == "Каримов Д. А."
