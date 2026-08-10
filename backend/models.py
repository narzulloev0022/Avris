import uuid
from datetime import datetime, date
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON, Float, Date, LargeBinary, Uuid, UniqueConstraint
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
    # Государственная или частная. Влияет не на права, а на разговор: в частной
    # клинике решение о покупке принимает владелец, в государственной — бюджет
    # и минздрав, и путь внедрения там совсем другой.
    facility_type = Column(String(16), nullable=True)  # state | private
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
    # SOAP accuracy stats — incremented when a consultation is saved.
    # accurate = doctor saved LLM's SOAP without edits (high alignment),
    # edited  = doctor changed at least one S/O/A/P field before saving.
    # Dashboard shows accurate / (accurate + edited) * 100 as "AI accuracy".
    soap_accurate_count = Column(Integer, nullable=False, default=0)
    soap_edited_count = Column(Integer, nullable=False, default=0)
    # Continuous Learning Pipeline — opt-in (default OFF). When true, the doctor
    # consents to anonymized [audio + corrected transcript] pairs being collected
    # for Hyperion STT fine-tuning. See STT/Continuous-Learning-Pipeline.md.
    stt_consent = Column(Boolean, nullable=False, default=False)

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
    date_of_birth = Column(Date, nullable=True)
    record_number = Column(String, nullable=True)  # № истории болезни / амбулаторной карты
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
    patient_type = Column(String, nullable=False, default="outpatient")  # outpatient|inpatient
    # Стационарный слой (заполняется при patient_type=inpatient) — снимок момента
    # госпитализации, НЕ перезаписывается текущим состоянием (status/diagnoses).
    admission_date = Column(DateTime, nullable=True)
    admission_diagnosis = Column(Text, nullable=True)
    admission_status = Column(String, nullable=True)  # stable|watch|serious|critical на момент поступления
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
    # visit — амбулаторный приём (default), primary — первичный осмотр при
    # поступлении, daily — ежедневный дневник стационара.
    visit_type = Column(String, nullable=False, default="visit")
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
    # Платный AI-разбор для пациента. Кэшируется здесь, а не пересчитывается на
    # каждое открытие карточки: результаты анализа не меняются, а каждый вызов
    # модели — деньги. ``_key`` — отпечаток результатов: перезалили результаты,
    # значит разбор устарел и будет собран заново.
    patient_breakdown = Column(Text, nullable=True)
    patient_breakdown_key = Column(String(64), nullable=True)

    patient = relationship("Patient", back_populates="lab_orders")
    doctor = relationship("User", back_populates="lab_orders")
    files = relationship("LabFile", back_populates="lab_order", cascade="all, delete-orphan")


class Epicrisis(Base):
    """Эпикриз — врачебная сводка истории болезни пациента.

    kind: interim (этапный) | discharge (выписной). body — финальный текст
    после правок врача (Claude даёт только черновик, он не сохраняется).
    Каждое сохранение — новая запись: история версий вместо перезаписи.
    """
    __tablename__ = "epicrises"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String, nullable=False, default="discharge")  # interim|discharge
    body = Column(Text, nullable=False)
    language = Column(String, nullable=False, default="ru")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


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


class TrainingPair(Base):
    """Continuous Learning Pipeline — Data Collector.

    One [audio + corrected transcript] pair captured from a consultation, used
    to fine-tune the Hyperion STT model on Tajik/Russian medical speech. Rows
    are only written with the doctor's explicit consent (opt-in). PHI is stripped
    before audio is persisted to S3. See STT/Continuous-Learning-Pipeline.md.

    Uuid renders as native UUID on Postgres (prod) and CHAR(32) on SQLite (dev).
    """
    __tablename__ = "training_pairs"

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(Uuid(as_uuid=True), nullable=False, index=True)
    audio_s3_path = Column(Text, nullable=True)
    raw_transcript = Column(Text, nullable=True)
    corrected_transcript = Column(Text, nullable=True)
    language = Column(String(10), nullable=True)
    specialty = Column(Text, nullable=True)
    consent = Column(Boolean, nullable=False, default=False)
    phi_cleaned = Column(Boolean, nullable=False, default=False)
    quality_score = Column(Float, nullable=True)
    trained = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AuthCode(Base):
    """Server-side auth state: email-OTP (verify/reset) and OAuth state tokens.

    Replaces the old in-memory dicts in auth.py so codes survive restarts and
    work across multiple instances. One row per (purpose, key):
      purpose = "verify" | "reset"  -> key is the user's email, code_hash is
                sha256 of the 6-digit OTP, resend_after holds the cooldown;
      purpose = "oauth"             -> key is the state token, code_hash empty.
    Expired rows are lazily purged on every store operation.
    """
    __tablename__ = "auth_codes"

    id = Column(Integer, primary_key=True, index=True)
    purpose = Column(String(16), nullable=False)
    key = Column(String(255), nullable=False)
    code_hash = Column(String(64), nullable=False, default="")
    attempts = Column(Integer, nullable=False, default=0)
    expires_at = Column(DateTime, nullable=False)
    resend_after = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("purpose", "key", name="uq_auth_codes_purpose_key"),
    )


class RefreshToken(Base):
    """Server-side registry of issued refresh tokens (one row per jti claim).

    A refresh JWT is accepted only while its jti row exists and is not
    revoked — that's what lets logout, rotation and password reset kill a
    session before the 7-day JWT expiry. Long-expired rows are lazily purged
    whenever a new token is issued.
    """
    __tablename__ = "refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String(36), unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    revoked = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AuditLog(Base):
    """Append-only trail of who did what — the regulatory backbone for medical
    data (who created/changed/read-out which record and when). Rows carry NO
    PHI: meta holds field names, statuses and routes, never values.
    user_id is NULL for public token-authenticated actions (lab portal)."""
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    action = Column(String(32), nullable=False)    # create|update|delete|login|approve|reject|results|upload|reset
    entity = Column(String(32), nullable=False, index=True)  # patient|consultation|lab_order|night_round|user|db
    entity_id = Column(String(64), nullable=True)
    meta = Column(JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)


class AssistantUsage(Base):
    """Дневной счётчик AI-ассистента пациента — серверный кап защиты затрат.

    Клиентский paywall держит тарифную границу (Free = 3/день), этот счётчик —
    жёсткий предохранитель от абьюза независимо от тарифа. day = YYYY-MM-DD UTC.
    """
    __tablename__ = "assistant_usage"
    __table_args__ = (UniqueConstraint("patient_account_id", "day", name="uq_assistant_usage_day"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(String(10), nullable=False)
    count = Column(Integer, nullable=False, default=0)


class PatientAccount(Base):
    """The platform's first GLOBAL patient identity — "the second door".

    Owned by the patient (phone/email + OTP login from the mobile app), not by
    a doctor. Existing doctor-scoped ``patients`` rows stay untouched; the two
    worlds meet through ``patient_links``. ``avris_patient_id`` is the
    human-facing ID shown as QR/code at the reception desk.

    ``consent_doctors_at`` — timestamp of the in-app onboarding consent that
    allows network doctors to see the profile on linking. NULL = no consent;
    linking must refuse. Regulatory anchor, do not repurpose.
    """
    __tablename__ = "patient_accounts"

    id = Column(Integer, primary_key=True, index=True)
    avris_patient_id = Column(String(16), unique=True, index=True, nullable=False)
    phone = Column(String(32), unique=True, index=True, nullable=True)
    email = Column(String(255), unique=True, index=True, nullable=True)
    full_name = Column(String(120), nullable=True)
    date_of_birth = Column(Date, nullable=True)
    gender = Column(String(16), nullable=True)
    height = Column(Float, nullable=True)
    weight = Column(Float, nullable=True)
    blood_type = Column(String(16), nullable=True)
    chronic_conditions = Column(JSON, nullable=False, default=list)
    allergies = Column(JSON, nullable=False, default=list)
    medications = Column(JSON, nullable=False, default=list)
    consent_doctors_at = Column(DateTime, nullable=True)
    # Which consent text/version the patient agreed to (recorded together with
    # consent_doctors_at at onboarding). A bare timestamp isn't legally
    # defensible on its own — you must be able to show WHAT was consented to.
    consent_version = Column(String(32), nullable=True)
    language_pref = Column(String(4), nullable=False, default="ru")
    # Подписка: тариф — единственный источник правды о правах пациента
    # (клиентский paywall лишь рисует замки). ``subscription_expires_at`` NULL
    # у free и у бессрочно выданных вручную; истёкший платный тариф считается
    # free без фонового джоба — см. patient_subscription.resolve_tier().
    subscription_tier = Column(String(16), nullable=False, default="free")
    subscription_expires_at = Column(DateTime, nullable=True)
    # appstore | googleplay | manual — откуда пришло право (для сверки с IAP).
    subscription_source = Column(String(32), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    links = relationship("PatientLink", back_populates="account", cascade="all, delete-orphan")
    avatar = relationship("PatientAvatar", back_populates="account", uselist=False,
                          cascade="all, delete-orphan")

    @property
    def avatar_updated_at(self):
        """Когда фото менялось в последний раз; None — фото нет.

        Клиенту нужен именно этот штамп, а не флаг «есть/нет»: по нему он
        обновляет свой кэш, когда пациент сменил фото на другом устройстве.
        """
        return self.avatar.updated_at if self.avatar else None


class PatientAvatar(Base):
    """Фото профиля пациента.

    Отдельная таблица, а не колонка в ``patient_accounts``: аккаунт читается
    почти в каждом запросе, и тащить с ним сотни килобайт картинки — плата
    за то, что нужно одному экрану. Бинарь в БД повторяет ``LabFile``: своего
    файлового хранилища у платформы пока нет, а класть медданные в чужое
    облако мы не будем.
    """
    __tablename__ = "patient_avatars"

    patient_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                        primary_key=True)
    content_type = Column(String(64), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    data = Column(LargeBinary, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    account = relationship("PatientAccount", back_populates="avatar")


class PatientLink(Base):
    """Bridge between a patient's own account and a doctor's ``patients`` row.

    Created when the doctor links the patient in the cabinet (QR/code scan,
    or name search with manual confirmation — ``method`` records which).
    One row per (account, doctor-record) pair — re-linking is idempotent at
    the DB level. Every creation is audit-logged by the API layer.
    """
    __tablename__ = "patient_links"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    method = Column(String(8), nullable=False, default="qr")  # qr|name
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    # Отзыв согласия гасит связь, но не стирает её. Раньше строка удалялась, и
    # повторная привязка заводила врачу ВТОРУЮ карточку того же человека:
    # история приёмов расщеплялась надвое. Теперь связь оживает на прежней
    # карточке. Все читающие пути обязаны фильтровать по revoked_at IS NULL.
    revoked_at = Column(DateTime, nullable=True, index=True)

    account = relationship("PatientAccount", back_populates="links")

    __table_args__ = (
        UniqueConstraint("patient_account_id", "patient_id", name="uq_patient_links_account_patient"),
    )


class PatientRefreshToken(Base):
    """Refresh-token registry for the patient door — mirrors ``refresh_tokens``
    but points at ``patient_accounts``. Kept as a separate table (not a column
    on refresh_tokens) so the two identity spaces can never be confused by an
    integer id collision between users.id and patient_accounts.id."""
    __tablename__ = "patient_refresh_tokens"

    id = Column(Integer, primary_key=True, index=True)
    jti = Column(String(36), unique=True, index=True, nullable=False)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    revoked = Column(Boolean, nullable=False, default=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatientLinkCode(Base):
    """Short-lived 6-digit code the patient shows at the reception desk.

    One-time and TTL-bound (~5 min): the DB row — not the QR payload — is the
    source of truth, so a screenshot of an old QR is worthless. Issued only
    when onboarding consent is present; consumed (used_at) on a successful
    link. Expired rows are lazily purged on every issue."""
    __tablename__ = "patient_link_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(6), unique=True, index=True, nullable=False)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class LinkThrottle(Base):
    """Per-doctor brute-force guard for patient-link code entry.

    A wrong or dead 6-digit code (404/410) from preview/confirm increments
    ``consecutive_failures``; after MAX_LINK_FAILURES in a row this doctor's
    linking is locked for LINK_LOCKOUT_MINUTES. A successful resolve resets
    the counter. One row per doctor. See patient_links.py for the constants.
    """
    __tablename__ = "link_throttle"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    consecutive_failures = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class VisitSummary(Base):
    """Patient-readable retelling of a consultation's SOAP note.

    Generated by Claude right when the doctor saves the consultation (never
    live on stage — the cached row is what the app shows), one per
    consultation. ``language`` follows the patient's language_pref.
    """
    __tablename__ = "visit_summaries"

    id = Column(Integer, primary_key=True, index=True)
    consultation_id = Column(Integer, ForeignKey("consultations.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    summary = Column(Text, nullable=False)
    # Human-readable rendering of the treatment plan (SOAP "P"), produced by the
    # SAME LLM pass as the summary — never raw SOAP. NULL = no prescriptions
    # in this visit. Distinct from PatientAccount.medications ("known meds" the
    # patient self-reported at onboarding), which is never mixed in here.
    prescriptions = Column(Text, nullable=True)
    language = Column(String(4), nullable=False, default="ru")
    model = Column(String(64), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatientPreVisitNote(Base):
    """A short free-text note the patient writes BEFORE a visit — "what I want
    to raise with the doctor this time" — surfaced to the doctor ONCE, at the
    moment they confirm the QR link.

    One ACTIVE (unseen) note per account: the patient-side POST create-or-updates
    it in place. When a doctor first sees it at confirm_link, ``seen_at`` and
    ``seen_by_doctor_id`` are stamped and it is never shown again; the next POST
    then begins a fresh note (old seen rows remain as an immutable trail and do
    NOT block writing a new one). Doctor visibility rides entirely on the
    existing confirm_link consent + PatientLink gate — there is no standalone
    doctor read endpoint, so consent is enforced once, not duplicated here.
    """
    __tablename__ = "patient_previsit_notes"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"), nullable=False, index=True)
    # 1000: потолок с запасом. Заметка, собранная интервью, идёт
    # структурой в несколько строк, и упереться в лимит на полуслове
    # хуже, чем хранить лишние байты.
    note_text = Column(String(1000), nullable=False)
    seen_by_doctor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WaitlistEntry(Base):
    """Public waitlist signup from the marketing page (/waitlist).

    Deliberately minimal and PHI-free: an email, a self-declared role and the
    UI language at signup (so launch emails go out in the right language).
    Duplicate emails are collapsed at the API level, not by a DB error."""
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    full_name = Column(String(120), nullable=True)
    phone = Column(String(32), nullable=True)
    role = Column(String(16), nullable=False, default="doctor")   # doctor|clinic|investor
    lang = Column(String(4), nullable=False, default="ru")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatientConversation(Base):
    """Разговор пациента с AI — ассистент или пред-визитное интервью.

    Раньше диалог жил только на клиенте: закрыл приложение — разговора нет.
    Для медицинского продукта это потеря, а не мелочь: пациент описал симптомы
    и не может к ним вернуться, а следующий разговор начинается с чистого
    листа, хотя половина уже была рассказана.

    ``kind`` разделяет два потока: assistant — вопросы о здоровье, intake —
    подготовка к приёму, из которой рождается заметка врачу.
    """
    __tablename__ = "patient_conversations"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="assistant")  # assistant | intake
    # Заголовок для списка — первые слова пациента, как в чат-интерфейсах.
    title = Column(String(120), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    messages = relationship("PatientMessage", back_populates="conversation",
                            cascade="all, delete-orphan", order_by="PatientMessage.id")


class PatientMessage(Base):
    """Реплика в разговоре. Содержит слова пациента о своём здоровье, то есть
    PHI — живёт под тем же скоупом владельца, что и остальная медкарта."""
    __tablename__ = "patient_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("patient_conversations.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    role = Column(String(16), nullable=False)  # user | assistant
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    conversation = relationship("PatientConversation", back_populates="messages")


class IntakeUsage(Base):
    """Дневной счётчик пред-визитных интервью.

    Отдельная таблица, а не строка в ``assistant_usage`` с префиксом в ключе
    дня: колонка ``day`` там VARCHAR(10) под «YYYY-MM-DD», и «intake:2026-07-30»
    в неё не влезает. SQLite длину не проверяет и молча стерпел бы, а Postgres
    в проде упал бы на первом же интервью.
    """
    __tablename__ = "intake_usage"
    __table_args__ = (UniqueConstraint("patient_account_id", "day", name="uq_intake_usage_day"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    day = Column(String(10), nullable=False)
    count = Column(Integer, nullable=False, default=0)


class NutritionEntry(Base):
    """Приём пищи в дневнике питания (тариф Pro).

    Само фото НЕ храним: оно уходит в модель и забывается. Снимок еды — это
    место, время и обстоятельства жизни человека; хранить его ради трёх цифр
    калорийности не за чем. В базе остаётся только разбор.
    """
    __tablename__ = "nutrition_entries"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    eaten_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    title = Column(String(200), nullable=False)
    # [{"name": "Плов", "grams": 250, "kcal": 520}, ...] — как разобрала модель
    items = Column(JSON, nullable=False, default=list)
    kcal = Column(Integer, nullable=False, default=0)
    protein_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    carbs_g = Column(Float, nullable=True)
    # photo | manual — по фото цифры оценочные, вручную их уточняет человек
    source = Column(String(16), nullable=False, default="photo")
    # low | medium | high — насколько модель уверена в оценке порции
    confidence = Column(String(8), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class NutritionUsage(Base):
    """Дневной счётчик разборов фото. Отдельная таблица — по той же причине,
    что и intake_usage: ключ дня фиксированной длины."""
    __tablename__ = "nutrition_usage"
    __table_args__ = (UniqueConstraint("patient_account_id", "day", name="uq_nutrition_usage_day"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    day = Column(String(10), nullable=False)
    count = Column(Integer, nullable=False, default=0)


class VisitInsight(Base):
    """Продольный разбор истории визитов (тариф Plus).

    Отличается от VisitSummary принципиально: сводка пересказывает ОДИН приём
    и бесплатна, а инсайт смотрит на историю целиком — что повторяется от
    визита к визиту, что уже назначали, о чём спросить врача в следующий раз.
    Пересказ одного приёма продать нельзя, картину за полгода — можно.

    ``source_key`` — отпечаток исходников (визиты + анализы). Совпал — отдаём
    сохранённое: история не изменилась, платить за тот же ответ второй раз
    пациент не должен.
    """
    __tablename__ = "visit_insights"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    source_key = Column(String(64), nullable=False)
    # Абзац: что происходило и что меняется со временем.
    picture = Column(Text, nullable=False)
    # ["…", …] — на что обратить внимание; НЕ диагнозы и НЕ назначения.
    watch = Column(JSON, nullable=False, default=list)
    # ["…", …] — вопросы, которые стоит задать врачу на следующем приёме.
    questions = Column(JSON, nullable=False, default=list)
    # Сколько визитов легло в основу — пациент видит, на чём построен разбор.
    visits_used = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class GlucoseReading(Base):
    """Измерение сахара крови (тариф Pro, «Диабет-контроль»).

    Единицы — ммоль/л: так меряют в Таджикистане и СНГ. Пересчёт в мг/дл не
    храним, чтобы не появилось двух источников правды об одном числе.

    ``context`` важнее, чем кажется: 9 ммоль/л натощак и 9 через два часа
    после еды — разные события, и без пометки статистика врёт.
    """
    __tablename__ = "glucose_readings"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    taken_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    mmol = Column(Float, nullable=False)
    # fasting | before_meal | after_meal | bedtime | random
    context = Column(String(16), nullable=False, default="random")
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class HealthAlert(Base):
    """Находка мониторинга — то, на что стоит обратить внимание врачу.

    Не диагноз и не назначение: правило смотрит на данные, которые пациент
    сам внёс (сахар, анализы), и говорит «покажите это врачу». Никаких
    выводов о причине и тем более о лечении здесь не появляется.

    ``dedup_key`` держит одно и то же наблюдение единственным: без него
    каждая проверка плодила бы копию одной тревоги, и приложение превратилось
    бы в источник шума, который перестают читать — включая тот единственный
    раз, когда читать было нужно.
    """
    __tablename__ = "health_alerts"
    __table_args__ = (UniqueConstraint("patient_account_id", "dedup_key",
                                       name="uq_health_alert_dedup"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    # glucose_low | glucose_high_streak | glucose_silent | lab_out_of_range
    kind = Column(String(32), nullable=False)
    # info | attention | urgent — влияет только на подачу, не на смысл
    severity = Column(String(16), nullable=False, default="info")
    dedup_key = Column(String(120), nullable=False)
    # Числа для подстановки в текст на клиенте: {"mmol": 3.4, "test": "Гемоглобин"}
    details = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    acknowledged_at = Column(DateTime, nullable=True)
    # Когда о находке сообщили вне приложения (письмо). NULL — ещё не
    # сообщали. Отдельно от created_at: находка живёт в базе всегда, а
    # позвать человека можно ровно один раз.
    notified_at = Column(DateTime, nullable=True)


class PatientGoals(Base):
    """Дневные цели пациента: шаги, вода, калории.

    Цель ставит человек, а не приложение. Это не формальность: как только
    знаменатель придумываем мы, «осталось 300 ккал» превращается в требование
    от программы, которая о человеке ничего не знает. Своя цель — наоборот,
    единственное, что делает счётчик осмысленным: без неё «8412 шагов» это
    ни много ни мало.

    NULL — цель не поставлена, и остаток мы не показываем.
    """
    __tablename__ = "patient_goals"

    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                primary_key=True)
    steps = Column(Integer, nullable=True)
    water_glasses = Column(Integer, nullable=True)
    kcal = Column(Integer, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class WaterIntake(Base):
    """Стаканы воды за день.

    Храним счётчик на день, а не отдельные записи: человеку важно «сколько
    сегодня», а не в котором часу был третий стакан. Одна строка на день —
    и тап по стакану остаётся мгновенным.
    """
    __tablename__ = "water_intake"
    __table_args__ = (UniqueConstraint("patient_account_id", "day", name="uq_water_day"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    glasses = Column(Integer, nullable=False, default=0)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatientCheckin(Base):
    """Как пациент чувствует себя сегодня — его собственными словами.

    Единственное на главной, что человек делает сам и регулярно: всё
    остальное там либо чтение, либо реакция на событие. Между приёмами врач
    не знает о пациенте ничего — а «третий день слабость» это ровно то, что
    он спрашивает первым.

    Это не измерение и не оценка, которую ставим мы: шкала субъективная и
    принадлежит человеку. Поэтому никаких выводов из неё сервер не делает —
    ни находок мониторинга, ни средних «баллов самочувствия».

    Одна отметка в день: дневник, который можно вести десять раз в сутки,
    перестают вести совсем. Повторная отметка меняет сегодняшнюю.
    """
    __tablename__ = "patient_checkins"
    __table_args__ = (UniqueConstraint("patient_account_id", "day",
                                       name="uq_patient_checkin_day"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    day = Column(Date, nullable=False, index=True)
    # 1 плохо · 2 так себе · 3 нормально · 4 хорошо · 5 отлично
    level = Column(Integer, nullable=False)
    note = Column(String(280), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class PatientDevice(Base):
    """Носимое устройство пациента — источник измерений.

    Вендор-нейтрально с первого дня: свой браслет Avris, чужой трекер и
    выгрузка из Apple Health / Health Connect пишут в одну таблицу через один
    протокол. Иначе каждый новый источник тянул бы за собой свою модель, свои
    эндпоинты и свою правду о том, что такое «пульс».

    ``secret_hash`` — хэш токена устройства. Браслет живёт своей жизнью и шлёт
    измерения сам, без входа пациента; давать ему пациентский токен нельзя —
    им можно прочитать всю медкарту. Токен устройства умеет ровно одно:
    писать измерения в свою же строку.
    """
    __tablename__ = "patient_devices"
    __table_args__ = (UniqueConstraint("patient_account_id", "external_id",
                                       name="uq_patient_device_external"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    # avris_band | apple_health | health_connect | other
    vendor = Column(String(32), nullable=False, default="other")
    model = Column(String(64), nullable=True)
    # Имя, которое видит пациент. По умолчанию — модель.
    name = Column(String(64), nullable=True)
    # Серийный номер или UUID экземпляра: по нему устройство узнаётся при
    # повторной привязке и не заводит второй строки.
    external_id = Column(String(128), nullable=False, index=True)
    secret_hash = Column(String(64), nullable=False)
    paired_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_sync_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)

    account = relationship("PatientAccount")


class DevicePairCode(Base):
    """Одноразовый код привязки устройства.

    Тот же приём, что и с кодом для врача: код короткоживущий, источник
    правды — строка в базе, а не то, что показано на экране. Устройство
    предъявляет код один раз и получает собственный токен.
    """
    __tablename__ = "device_pair_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(8), unique=True, index=True, nullable=False)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class DeviceMeasurement(Base):
    """Одно измерение от устройства.

    Одна таблица на все виды измерений, а не колонка на каждый: браслеты
    отличаются набором датчиков, и новый показатель не должен требовать
    миграции. Что считать допустимым значением — решает реестр METRICS в
    patient_devices.py, а не эта таблица.

    Уникальность (устройство, вид, время) — чтобы повторная синхронизация не
    плодила копии: браслет, потерявший связь, дошлёт тот же час заново.
    """
    __tablename__ = "device_measurements"
    __table_args__ = (UniqueConstraint("device_id", "kind", "taken_at",
                                       name="uq_device_measurement"),)

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("patient_devices.id", ondelete="CASCADE"),
                       nullable=False, index=True)
    kind = Column(String(32), nullable=False, index=True)
    value = Column(Float, nullable=False)
    unit = Column(String(16), nullable=False)
    taken_at = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MonitoringDigest(Base):
    """Одна спокойная фраза обо всех активных находках сразу.

    Правило говорит сухо и по одной штуке за раз: «Низкий сахар — 2.8
    ммоль/л». На главной это читается как тревога, а цифра там бесполезна —
    сделать с ней в этот момент нечего, кроме как испугаться. Модель
    пересказывает весь набор находок одной фразой на языке пациента.

    ``source_key`` — отпечаток набора активных находок. Набор не менялся —
    фразу не пересобираем: каждый вызов модели стоит денег, а находки за
    шесть часов меняются в лучшем случае раз.
    """
    __tablename__ = "monitoring_digests"

    patient_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                        primary_key=True)
    source_key = Column(String(64), nullable=False)
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class MonitoringRun(Base):
    """Когда мониторинг последний раз смотрел данные этого пациента.

    Нужна не для отчётности: пациент должен видеть, что проверка идёт, и
    когда она была. «Мы следим» без даты последней проверки — обещание,
    которое нечем подтвердить.
    """
    __tablename__ = "monitoring_runs"

    id = Column(Integer, primary_key=True, index=True)
    patient_account_id = Column(Integer, ForeignKey("patient_accounts.id", ondelete="CASCADE"),
                                nullable=False, unique=True, index=True)
    checked_at = Column(DateTime, nullable=False, default=datetime.utcnow)
