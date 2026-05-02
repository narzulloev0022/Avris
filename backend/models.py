from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Float
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    specialty = Column(String, nullable=True)
    role = Column(String, nullable=False, default="doctor")
    language_pref = Column(String, nullable=False, default="ru")
    theme_pref = Column(String, nullable=False, default="dark")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)

    patients = relationship("Patient", back_populates="doctor", cascade="all, delete-orphan")
    consultations = relationship("Consultation", back_populates="doctor", cascade="all, delete-orphan")
    lab_orders = relationship("LabOrder", back_populates="doctor", cascade="all, delete-orphan")


class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    full_name = Column(String, nullable=False)
    full_name_en = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    gender = Column(String, nullable=True)
    gender_en = Column(String, nullable=True)
    blood_type = Column(String, nullable=True)
    blood_type_en = Column(String, nullable=True)
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    bmi = Column(String, nullable=True)
    initials = Column(String, nullable=True)
    ward = Column(String, nullable=True)
    ward_en = Column(String, nullable=True)
    allergies = Column(JSON, nullable=False, default=list)
    allergies_en = Column(JSON, nullable=False, default=list)
    diagnoses = Column(JSON, nullable=False, default=list)
    diagnoses_en = Column(JSON, nullable=False, default=list)
    current_conditions = Column(JSON, nullable=False, default=list)
    current_conditions_en = Column(JSON, nullable=False, default=list)
    medications = Column(JSON, nullable=False, default=list)
    medications_en = Column(JSON, nullable=False, default=list)
    history = Column(JSON, nullable=False, default=list)
    history_en = Column(JSON, nullable=False, default=list)
    insight = Column(Text, nullable=True)
    insight_en = Column(Text, nullable=True)
    vitals = Column(JSON, nullable=True)
    avris_score = Column(Integer, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    doctor = relationship("User", back_populates="patients")
    consultations = relationship("Consultation", back_populates="patient", cascade="all, delete-orphan")
    lab_orders = relationship("LabOrder", back_populates="patient", cascade="all, delete-orphan")


class Consultation(Base):
    __tablename__ = "consultations"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    transcript = Column(Text, nullable=True)
    soap_s = Column(Text, nullable=True)
    soap_o = Column(Text, nullable=True)
    soap_a = Column(Text, nullable=True)
    soap_p = Column(Text, nullable=True)
    language = Column(String, nullable=False, default="ru")
    duration_seconds = Column(Integer, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    patient = relationship("Patient", back_populates="consultations")
    doctor = relationship("User", back_populates="consultations")


class LabOrder(Base):
    __tablename__ = "lab_orders"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    qr_token = Column(String, unique=True, nullable=False, index=True)
    tests = Column(JSON, nullable=False, default=list)
    status = Column(String, nullable=False, default="pending")
    results = Column(JSON, nullable=True)
    ai_comment = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    received_at = Column(DateTime, nullable=True)

    patient = relationship("Patient", back_populates="lab_orders")
    doctor = relationship("User", back_populates="lab_orders")
