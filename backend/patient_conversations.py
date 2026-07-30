"""История разговоров пациента с AI.

Диалог жил только на клиенте: закрыл приложение — разговора нет. Для
медицинского продукта это потеря, а не мелочь: пациент подробно описал
симптомы, а вернуться к этому не может, и следующий разговор начинается с
чистого листа, хотя половина уже была рассказана.

Всё здесь self-scoped, как и остальной пациентский контур: id разговора
проверяется по владельцу токена, чужой отдаёт 404 (не 403 — существование
чужих разговоров не подтверждаем).
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audit import audit
from database import get_db
from models import PatientAccount, PatientConversation, PatientMessage
from patient_auth import get_current_patient

router = APIRouter(prefix="/api/patient/conversations", tags=["patient"])

KIND_ASSISTANT = "assistant"
KIND_INTAKE = "intake"
_KINDS = {KIND_ASSISTANT, KIND_INTAKE}

# Заголовок — первые слова пациента, как в чат-интерфейсах.
TITLE_MAX = 60


class MessageOut(BaseModel):
    role: str
    text: str
    created_at: datetime


class ConversationOut(BaseModel):
    id: int
    kind: str
    title: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ConversationDetailOut(ConversationOut):
    messages: List[MessageOut] = []


def make_title(text: str) -> str:
    """Заголовок из первой реплики: обрезаем по слову, чтобы в списке не
    висели обрубки вроде «Болит голова со вче»."""
    clean = " ".join((text or "").split())
    if len(clean) <= TITLE_MAX:
        return clean
    cut = clean[:TITLE_MAX].rsplit(" ", 1)[0]
    return (cut or clean[:TITLE_MAX]).rstrip(",.;:") + "…"


def owned_conversation(db: Session, cid: int, account: PatientAccount) -> PatientConversation:
    convo = (db.query(PatientConversation)
             .filter(PatientConversation.id == cid,
                     PatientConversation.patient_account_id == account.id)
             .first())
    if convo is None:
        raise HTTPException(status_code=404, detail="Разговор не найден")
    return convo


def append_turn(db: Session, convo: PatientConversation, role: str, text: str) -> None:
    """Дописать реплику и подтянуть updated_at — список сортируется по нему,
    как в любом мессенджере."""
    db.add(PatientMessage(conversation_id=convo.id, role=role, text=text))
    convo.updated_at = datetime.utcnow()
    if not convo.title and role == "user":
        convo.title = make_title(text)


def start_conversation(db: Session, account: PatientAccount, kind: str) -> PatientConversation:
    convo = PatientConversation(patient_account_id=account.id, kind=kind)
    db.add(convo)
    db.flush()
    return convo


@router.get("", response_model=List[ConversationOut])
def list_conversations(
    kind: Optional[str] = None,
    account: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Список разговоров, свежие сверху. ``kind`` разделяет вопросы о здоровье
    и подготовки к приёму — это разные истории, мешать их в одном списке
    значит прятать одно за другим."""
    q = db.query(PatientConversation).filter(
        PatientConversation.patient_account_id == account.id)
    if kind:
        if kind not in _KINDS:
            raise HTTPException(status_code=422, detail="Неизвестный тип разговора")
        q = q.filter(PatientConversation.kind == kind)
    rows = q.order_by(PatientConversation.updated_at.desc()).limit(100).all()
    return [ConversationOut(id=c.id, kind=c.kind, title=c.title,
                            created_at=c.created_at, updated_at=c.updated_at) for c in rows]


@router.get("/{cid}", response_model=ConversationDetailOut)
def get_conversation(
    cid: int,
    account: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    convo = owned_conversation(db, cid, account)
    return ConversationDetailOut(
        id=convo.id, kind=convo.kind, title=convo.title,
        created_at=convo.created_at, updated_at=convo.updated_at,
        messages=[MessageOut(role=m.role, text=m.text, created_at=m.created_at)
                  for m in convo.messages],
    )


@router.delete("/{cid}", status_code=204)
def delete_conversation(
    cid: int,
    account: PatientAccount = Depends(get_current_patient),
    db: Session = Depends(get_db),
):
    """Пациент вправе стереть свой разговор — это его слова о своём здоровье.
    Сообщения уходят каскадом."""
    convo = owned_conversation(db, cid, account)
    db.delete(convo)
    db.commit()
    audit(db, action="delete", entity="patient_conversation", user_id=None,
          entity_id=cid, meta={"door": "patient"})
    return Response(status_code=204)
