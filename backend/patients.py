from datetime import datetime
from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from database import get_db
from models import Patient, User
from auth import get_current_user

router = APIRouter(prefix="/api/patients", tags=["patients"])


# ---------- Schemas ----------

class PatientBase(BaseModel):
    full_name: str = Field(min_length=1)
    full_name_en: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    gender_en: Optional[str] = None
    blood_type: Optional[str] = None
    blood_type_en: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    bmi: Optional[str] = None
    initials: Optional[str] = None
    ward: Optional[str] = None
    ward_en: Optional[str] = None
    department: Optional[str] = None
    status: Optional[str] = None
    patient_type: Optional[str] = "outpatient"
    allergies: List[str] = []
    allergies_en: List[str] = []
    diagnoses: List[str] = []
    diagnoses_en: List[str] = []
    current_conditions: List[str] = []
    current_conditions_en: List[str] = []
    medications: List[str] = []
    medications_en: List[str] = []
    history: List[str] = []
    history_en: List[str] = []
    insight: Optional[str] = None
    insight_en: Optional[str] = None
    vitals: Optional[dict[str, Any]] = None
    avris_score: Optional[int] = None


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    full_name: Optional[str] = None
    full_name_en: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None
    gender_en: Optional[str] = None
    blood_type: Optional[str] = None
    blood_type_en: Optional[str] = None
    height: Optional[float] = None
    weight: Optional[float] = None
    bmi: Optional[str] = None
    initials: Optional[str] = None
    ward: Optional[str] = None
    ward_en: Optional[str] = None
    department: Optional[str] = None
    status: Optional[str] = None
    patient_type: Optional[str] = None
    allergies: Optional[List[str]] = None
    allergies_en: Optional[List[str]] = None
    diagnoses: Optional[List[str]] = None
    diagnoses_en: Optional[List[str]] = None
    current_conditions: Optional[List[str]] = None
    current_conditions_en: Optional[List[str]] = None
    medications: Optional[List[str]] = None
    medications_en: Optional[List[str]] = None
    history: Optional[List[str]] = None
    history_en: Optional[List[str]] = None
    insight: Optional[str] = None
    insight_en: Optional[str] = None
    vitals: Optional[dict[str, Any]] = None
    avris_score: Optional[int] = None


class PatientResponse(PatientBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    doctor_id: int
    is_active: bool
    created_at: datetime


# ---------- Demo seed ----------

DEMO_PATIENTS: List[dict] = [
    {
        "full_name": "Иванова А.М.", "full_name_en": "Ivanova A.M.", "initials": "ИА",
        "age": 63, "gender": "Ж", "gender_en": "F",
        "blood_type": "O(I) Rh+", "blood_type_en": "O+",
        "height": 165, "weight": 78, "bmi": "28.7",
        "ward": "Кардиология A1", "ward_en": "Cardiology A1",
        "diagnoses": ["Гипертония", "Диабет II"], "diagnoses_en": ["Hypertension", "Diabetes II"],
        "current_conditions": ["Гипертонический криз", "Диабет II"], "current_conditions_en": ["Hypertensive crisis", "Diabetes II"],
        "history": ["ИБС", "ХБП I"], "history_en": ["IHD", "CKD stage I"],
        "allergies": ["Метформин"], "allergies_en": ["Metformin"],
        "medications": ["Амлодипин 10мг", "Инсулин 18Е", "Рамиприл 5мг"],
        "medications_en": ["Amlodipine 10mg", "Insulin 18U", "Ramipril 5mg"],
        "insight": "Ночное давление >150, усилить контроль.",
        "insight_en": "Nighttime BP >150, increase monitoring.",
        "vitals": {"АД": [152,148,150,147,151,149,150], "ЧСС": [88,85,92,90,87,89,91], "T°C": [37.5,37.4,37.3,37.4,37.5,37.2,37.3], "SpO₂": [94,92,93,90,94,95,93]},
        "avris_score": 34,
    },
    {
        "full_name": "Омаров Р.Б.", "full_name_en": "Omarov R.B.", "initials": "ОР",
        "age": 56, "gender": "М", "gender_en": "M",
        "blood_type": "A(II) Rh+", "blood_type_en": "A+",
        "height": 178, "weight": 86, "bmi": "27.1",
        "ward": "Пульмонология B3", "ward_en": "Pulmonology B3",
        "diagnoses": ["Пневмония", "Аритмия"], "diagnoses_en": ["Pneumonia", "Arrhythmia"],
        "current_conditions": ["Пневмония", "Аритмия"], "current_conditions_en": ["Pneumonia", "Arrhythmia"],
        "history": ["ХОБЛ"], "history_en": ["COPD"],
        "allergies": [], "allergies_en": [],
        "medications": ["Цефтриаксон 2г", "Метопролол 25мг"],
        "medications_en": ["Ceftriaxone 2g", "Metoprolol 25mg"],
        "insight": "Сатурация >94% в покое.",
        "insight_en": "Saturation >94% at rest.",
        "vitals": {"АД": [132,128,130,128,124,127,129], "ЧСС": [96,92,90,88,87,86,85], "T°C": [38.4,38.1,37.9,37.8,37.6,37.4,37.2], "SpO₂": [92,93,94,94,95,96,95]},
        "avris_score": 51,
    },
    {
        "full_name": "Нурланов К.М.", "full_name_en": "Nurlanov K.M.", "initials": "НК",
        "age": 59, "gender": "М", "gender_en": "M",
        "blood_type": "B(III) Rh-", "blood_type_en": "B-",
        "height": 172, "weight": 90, "bmi": "30.4",
        "ward": "Кардиология C2", "ward_en": "Cardiology C2",
        "diagnoses": ["ИБС", "Гипертония III"], "diagnoses_en": ["CAD", "Hypertension III"],
        "current_conditions": ["ИБС", "АГ III"], "current_conditions_en": ["CAD", "HTN III"],
        "history": ["Стенокардия"], "history_en": ["Angina"],
        "allergies": ["Амоксициллин"], "allergies_en": ["Amoxicillin"],
        "medications": ["Бисопролол 5мг", "Аторвастатин 20мг"],
        "medications_en": ["Bisoprolol 5mg", "Atorvastatin 20mg"],
        "insight": "Приступы снизились.",
        "insight_en": "Episodes decreased.",
        "vitals": {"АД": [145,142,138,136,134,133,132], "ЧСС": [78,80,76,74,75,73,72], "T°C": [36.9,36.8,36.8,36.7,36.7,36.6,36.6], "SpO₂": [96,95,95,96,97,97,96]},
        "avris_score": 62,
    },
    {
        "full_name": "Сатыбалдиев А.Р.", "full_name_en": "Satybaldiev A.R.", "initials": "СА",
        "age": 48, "gender": "М", "gender_en": "M",
        "blood_type": "AB(IV) Rh+", "blood_type_en": "AB+",
        "height": 180, "weight": 82, "bmi": "25.3",
        "ward": "Терапия D1", "ward_en": "Therapy D1",
        "diagnoses": ["Бронхит"], "diagnoses_en": ["Bronchitis"],
        "current_conditions": ["Бронхит"], "current_conditions_en": ["Bronchitis"],
        "history": ["ХОБЛ"], "history_en": ["COPD"],
        "allergies": [], "allergies_en": [],
        "medications": ["Будесонид", "Ипратропий"],
        "medications_en": ["Budesonide", "Ipratropium"],
        "insight": "Хрипы уменьшились.",
        "insight_en": "Wheezing decreased.",
        "vitals": {"АД": [128,126,127,125,124,123,122], "ЧСС": [84,82,81,80,78,79,77], "T°C": [37.2,37.1,37.0,37.0,36.9,36.9,36.8], "SpO₂": [95,95,96,96,97,96,97]},
        "avris_score": 71,
    },
    {
        "full_name": "Бекмуратов Т.Д.", "full_name_en": "Bekmuratov T.D.", "initials": "БТ",
        "age": 61, "gender": "М", "gender_en": "M",
        "blood_type": "A(II) Rh-", "blood_type_en": "A-",
        "height": 168, "weight": 75, "bmi": "26.6",
        "ward": "Пульмонология B1", "ward_en": "Pulmonology B1",
        "diagnoses": ["ХОБЛ"], "diagnoses_en": ["COPD"],
        "current_conditions": ["ХОБЛ"], "current_conditions_en": ["COPD"],
        "history": ["Курение >20 лет"], "history_en": ["Smoking >20 yrs"],
        "allergies": [], "allergies_en": [],
        "medications": ["Сальбутамол", "Тиотропий"],
        "medications_en": ["Salbutamol", "Tiotropium"],
        "insight": "Повторить спирометрию.",
        "insight_en": "Repeat spirometry.",
        "vitals": {"АД": [138,137,135,134,133,132,130], "ЧСС": [88,86,85,84,83,82,82], "T°C": [37.0,37.0,36.9,36.9,36.8,36.8,36.7], "SpO₂": [93,92,93,94,94,93,92]},
        "avris_score": 58,
    },
    {
        "full_name": "Кадырова Е.В.", "full_name_en": "Kadyrova E.V.", "initials": "КЕ",
        "age": 34, "gender": "Ж", "gender_en": "F",
        "blood_type": "B(III) Rh+", "blood_type_en": "B+",
        "height": 170, "weight": 65, "bmi": "22.5",
        "ward": "Хирургия E2", "ward_en": "Surgery E2",
        "diagnoses": ["Послеоп. период"], "diagnoses_en": ["Post-op period"],
        "current_conditions": ["Послеоп. период"], "current_conditions_en": ["Post-op period"],
        "history": ["Аппендэктомия"], "history_en": ["Appendectomy"],
        "allergies": ["Пенициллин"], "allergies_en": ["Penicillin"],
        "medications": ["Цефазолин 1г", "Парацетамол 1г"],
        "medications_en": ["Cefazolin 1g", "Paracetamol 1g"],
        "insight": "Рана сухая, Score растёт.",
        "insight_en": "Wound dry, Score rising.",
        "vitals": {"АД": [118,120,117,116,115,114,113], "ЧСС": [78,76,75,74,74,72,72], "T°C": [37.1,36.9,36.8,36.7,36.6,36.6,36.5], "SpO₂": [98,98,99,99,99,98,98]},
        "avris_score": 87,
    },
]


def seed_demo_patients_for(db: Session, doctor_id: int) -> int:
    """Insert the 6 demo patients linked to the given doctor. Returns count inserted."""
    inserted = 0
    for p in DEMO_PATIENTS:
        row = Patient(doctor_id=doctor_id, **p)
        db.add(row)
        inserted += 1
    db.commit()
    return inserted


# ---------- Endpoints ----------

def _get_owned_patient(db: Session, pid: int, user: User) -> Patient:
    p = db.query(Patient).filter(Patient.id == pid).first()
    if not p or not p.is_active:
        raise HTTPException(status_code=404, detail="Пациент не найден")
    if p.doctor_id != user.id:
        raise HTTPException(status_code=403, detail="Нет доступа к этому пациенту")
    return p


@router.post("/", response_model=PatientResponse, status_code=status.HTTP_201_CREATED)
def create_patient(
    payload: PatientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = Patient(doctor_id=current_user.id, **payload.model_dump())
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("/", response_model=List[PatientResponse])
def list_patients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Patient)
        .filter(Patient.doctor_id == current_user.id, Patient.is_active.is_(True))
        .order_by(Patient.id.asc())
        .all()
    )


@router.get("/{pid}", response_model=PatientResponse)
def get_patient(
    pid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_owned_patient(db, pid, current_user)


@router.put("/{pid}", response_model=PatientResponse)
def update_patient(
    pid: int,
    payload: PatientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = _get_owned_patient(db, pid, current_user)
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


@router.delete("/{pid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_patient(
    pid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    p = _get_owned_patient(db, pid, current_user)
    p.is_active = False
    db.commit()
    return None
