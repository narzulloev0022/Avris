"""Проверка прав на карту пациента — одна на все операции.

Личность врача проверяется зависимостью `get_current_user`, но этого мало:
дальше в теле запроса приходит `patient_id`, и его никто не сверял с
владельцем. Врач А указывал карту врача Б — запись создавалась, а публичная
ссылка направления показывала лаборанту имя, возраст и палату чужого
пациента. Ссылка открывается без авторизации, её видит любой, кому попал QR.

OWASP называет это API1, Broken Object Level Authorization. Лечится не
заплаткой в одном месте, а общей проверкой перед каждым действием с картой.

Чужая карта отвечает 404, а не 403: 403 подтверждает, что запись с таким
номером существует, и превращает перебор идентификаторов в разведку.
"""
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from models import Patient, User

_NOT_FOUND = "Пациент не найден"


def require_own_patient(db: Session, patient_id: Optional[int], user: User) -> Optional[Patient]:
    """Карта врача по идентификатору из запроса.

    ``patient_id`` пуст — возвращаем None: осмотр без карты это обычный
    случай, врач диктует до того, как завёл пациента. Карта чужая или
    удалённая — 404.
    """
    if patient_id is None:
        return None
    p = db.query(Patient).filter(Patient.id == patient_id).first()
    if not p or not getattr(p, "is_active", True) or p.doctor_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)
    return p
