from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Float, Date, LargeBinary
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    full_name = Column(String, nullable=False)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    patronymic = Column(String, nullable=True)
    date_of_birth = Column(Date, nullable=True)
    phone = Column(String, nullable=True)
    specialty = Column(String, nullable=True)
    hospital_name = Column(String, nullable=True)
    hospital_address = Column(String, nullable=True)
    department = Column(String, nullable=True)
    position = Column(String, nullable=True)
    experience_years = Column(Integer, nullable=True)
    license_number = Column(String, nullable=True)
    avatar_url = Column(String, nullable=True)
    role = Column(String, nullable=False, default="doctor")
    language_pref = Column(String, nullable=False, default="ru")
    theme_pref = Column(String, nullable=False, default="system")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    is_active = Column(Boolean, nullable=False, default=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    profile_completed = Column(Boolean, nullable=False, default=False)
    is_admin = Column(Boolean, nullable=False, default=False)
    is_approved = Column(Boolean, nullable=False, default=False)
    rejection_reason = Column(String, nullable=True)

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
    department = Column(String, nullable=True)  # therapy|cardiology|surgery|neurology|pulmonology|icu|post_icu|other
    status = Column(String, nullable=True)  # stable|watch|serious|critical
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


class NightRound(Base):
    __tablename__ = "night_rounds"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=True, index=True)
    ward = Column(String, nullable=True)
    vitals = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    plan = Column(Text, nullable=True)
    transcript = Column(Text, nullable=True)
    status = Column(String, nullable=True)
    language = Column(String, nullable=False, default="ru")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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
    files = relationship("LabFile", back_populates="lab_order", cascade="all, delete-orphan")


class LabFile(Base):
    __tablename__ = "lab_files"

    id = Column(Integer, primary_key=True, index=True)
    lab_order_id = Column(Integer, ForeignKey("lab_orders.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)
    result_type = Column(String, nullable=False)  # lab|ecg|xray|us|mri|ct|endo|other
    size_bytes = Column(Integer, nullable=False)
    data = Column(LargeBinary, nullable=False)
    uploaded_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    lab_order = relationship("LabOrder", back_populates="files")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String, nullable=False, default="system")  # call|lab_ready|round|system
    title = Column(String, nullable=False)
    message = Column(Text, nullable=True)
    payload = Column(JSON, nullable=True)
    is_read = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
