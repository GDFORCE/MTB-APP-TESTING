"""My Trial Board — Dawn Rounds clinical-trials backend.

Production-grade FastAPI app with:
- JWT auth (access + refresh)
- Role-based access (sponsor / pi / crc / patient)
- Trials, visits, patients, notifications CRUD
- Real-time chat over WebSocket (1-to-1 + group, typing, read receipts)
- MongoDB persistence
"""
from fastapi import FastAPI, APIRouter, BackgroundTasks, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query, UploadFile, File, Form, Request
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument, UpdateOne
from pymongo.errors import DuplicateKeyError
import os, re, json, logging, uuid, asyncio, hashlib, secrets
from pathlib import Path
from pydantic import BaseModel, EmailStr, Field
from typing import Any, List, Optional, Dict, Literal
from datetime import datetime, date, timezone, timedelta
from passlib.context import CryptContext
import jwt

import otp_service
import protocol_extraction as pe
from schedule_schema import apply_temporal_amount, classify_visit_activities, TemporalAmount
import storage as file_storage
import google_places

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# ── Config ───────────────────────────────────────────────────────────────────
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
JWT_SECRET = os.environ.get('JWT_SECRET', 'dawn-rounds-dev-secret-change-me')
JWT_REFRESH_SECRET = os.environ.get('JWT_REFRESH_SECRET', 'dawn-rounds-dev-refresh-secret')
ALGO = 'HS256'
ACCESS_MIN = 15
REFRESH_DAYS = 30

client = AsyncIOMotorClient(MONGO_URL, tz_aware=True)
db = client[DB_NAME]
pwd_ctx = CryptContext(schemes=['bcrypt'], deprecated='auto')

app = FastAPI(title="My Trial Board")
api = APIRouter(prefix='/api')
oauth2 = OAuth2PasswordBearer(tokenUrl='/api/auth/login', auto_error=False)

def now(): return datetime.now(timezone.utc)
def iso(d): return d.isoformat() if isinstance(d, datetime) else d


def normalize_email(value: Optional[str]) -> Optional[str]:
    clean = str(value or '').strip().lower()
    return clean or None


# Calling codes are 1-4 digits and several are shared, so we only need the set of
# valid prefixes to split a submitted number — not the country it belongs to.
CALLING_CODES = frozenset({
    '1', '7', '20', '27', '30', '31', '32', '33', '34', '36', '39', '40', '41', '43', '44',
    '45', '46', '47', '48', '49', '51', '52', '53', '54', '55', '56', '57', '58', '60', '61',
    '62', '63', '64', '65', '66', '81', '82', '84', '86', '90', '91', '92', '93', '94', '95',
    '98', '211', '212', '213', '216', '218', '220', '221', '222', '223', '224', '225', '226',
    '227', '228', '229', '230', '231', '232', '233', '234', '235', '236', '237', '238', '239',
    '240', '241', '242', '243', '244', '245', '246', '248', '249', '250', '251', '252', '253',
    '254', '255', '256', '257', '258', '260', '261', '262', '263', '264', '265', '266', '267',
    '268', '269', '290', '291', '297', '298', '299', '350', '351', '352', '353', '354', '355',
    '356', '357', '358', '359', '370', '371', '372', '373', '374', '375', '376', '377', '378',
    '379', '380', '381', '382', '383', '385', '386', '387', '389', '420', '421', '423', '500',
    '501', '502', '503', '504', '505', '506', '507', '508', '509', '590', '591', '592', '593',
    '594', '595', '596', '597', '598', '599', '670', '672', '673', '674', '675', '676', '677',
    '678', '679', '680', '681', '682', '683', '685', '686', '687', '688', '689', '690', '691',
    '692', '850', '852', '853', '855', '856', '880', '886', '960', '961', '962', '963', '964',
    '965', '966', '967', '968', '970', '971', '972', '973', '974', '975', '976', '977', '992',
    '993', '994', '995', '996', '998',
    '1242', '1246', '1264', '1268', '1284', '1340', '1345', '1441', '1473', '1649', '1664',
    '1670', '1671', '1684', '1721', '1758', '1767', '1784', '1787', '1809', '1868', '1869',
    '1876',
})


def _split_calling_code(digits: str) -> Optional[tuple]:
    """Longest-first match of `digits` against the known calling codes."""
    for size in (4, 3, 2, 1):
        code = digits[:size]
        if code in CALLING_CODES and len(digits) > size:
            return code, digits[size:]
    return None


def normalize_phone(value: Optional[str]) -> Optional[str]:
    """Canonicalize a registration phone to E.164, defaulting to India.

    Numbers arrive from the client already in `+<code><national>` form. A bare
    number with no `+` keeps the historical Indian interpretation so existing
    clients and stored values stay valid.
    """
    if value is None or not str(value).strip():
        return None
    raw = str(value).strip()
    digits = re.sub(r'\D', '', raw)

    if raw.startswith('+') or digits.startswith('00'):
        if digits.startswith('00'):
            digits = digits[2:]
        parts = _split_calling_code(digits)
        if not parts:
            raise HTTPException(400, 'Enter a valid mobile number with its country code')
        code, national = parts
        national = national.lstrip('0') or national
        if code == '91':
            return _normalize_indian_national(national)
        # NANP (+1 and its +1XXX territories) is always 10 digits after the code.
        if code.startswith('1'):
            valid = len(code) - 1 + len(national) == 10
        else:
            # E.164 caps a full number at 15 digits; 4 is the shortest subscriber part.
            valid = 4 <= len(national) and len(code) + len(national) <= 15
        if not valid:
            raise HTTPException(400, 'Enter a valid mobile number for the selected country')
        return f'+{code}{national}'

    if digits.startswith('0') and len(digits) == 11:
        digits = digits[1:]
    return _normalize_indian_national(digits)


def _normalize_indian_national(digits: str) -> str:
    if len(digits) == 12 and digits.startswith('91'):
        digits = digits[2:]
    if not re.fullmatch(r'[6-9]\d{9}', digits):
        raise HTTPException(400, 'Enter a valid 10-digit Indian mobile number')
    return f'+91{digits}'


def normalize_registration_profile(role: str, profile: Optional[Dict]) -> Dict:
    """Validate role-specific dates and replace client-supplied age with truth."""
    normalized = {
        str(key): value.strip() if isinstance(value, str) else value
        for key, value in (profile or {}).items()
    }
    raw_dob = normalized.get('dob')
    if role == 'patient' and not raw_dob:
        raise HTTPException(400, 'Date of birth is required for patient registration')
    if raw_dob:
        try:
            dob = date.fromisoformat(str(raw_dob))
        except (TypeError, ValueError):
            raise HTTPException(400, 'Date of birth must be a real date in YYYY-MM-DD format')
        today = now().date()
        if dob > today:
            raise HTTPException(400, 'Date of birth cannot be in the future')
        age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
        if age < 0 or age > 120:
            raise HTTPException(400, 'Date of birth must produce an age between 0 and 120')
        normalized['dob'] = dob.isoformat()
        normalized['age'] = age
    else:
        normalized.pop('age', None)
    return normalized

# ── Models ───────────────────────────────────────────────────────────────────
Role = Literal['sponsor', 'cro', 'smo', 'site', 'pi', 'crc', 'patient', 'admin']

class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: Role
    phone: Optional[str] = None
    organization: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None

class RegisterStartIn(BaseModel):
    full_name: str
    role: Role
    # Password is optional: in the design flow, OTP is verified BEFORE the password
    # is set. When omitted here, the account is created later via /register/complete.
    password: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    security_question: Optional[str] = None
    security_answer: Optional[str] = None
    # The design collects three security questions (step 3). Each item: {question, answer}.
    security_questions: Optional[List[Dict]] = None
    profile: Optional[Dict] = None   # extra role-specific fields (designation, dob, gender…)

    invite_token: Optional[str] = None

class RegisterAvailabilityIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None

class RegisterVerifyIn(BaseModel):
    registration_id: str
    email_otp: Optional[str] = None
    phone_otp: Optional[str] = None

class RegisterCompleteIn(BaseModel):
    registration_id: str
    password: str

class RegisterSecurityQuestionsIn(BaseModel):
    registration_id: str
    security_questions: List[Dict]

class RegisterResendIn(BaseModel):
    registration_id: str
    channel: Literal['email', 'phone']

class LoginIn(BaseModel):
    # Keep the historical field name for API compatibility, but accept either
    # a registered email address or a mobile number as the login identifier.
    email: str = Field(min_length=1, max_length=254)
    password: str


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class LogoutIn(BaseModel):
    refresh_token: Optional[str] = Field(default=None, min_length=32, max_length=512)

class ForgotIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None


class ForgotVerifyIn(BaseModel):
    recovery_id: str = Field(min_length=1)
    otp: str = Field(min_length=6, max_length=6)


class LoginSupportStartIn(BaseModel):
    email: EmailStr
    subject: str = Field(min_length=3, max_length=120)
    description: str = Field(min_length=10, max_length=2000)


class LoginSupportVerifyIn(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    otp: str = Field(min_length=6, max_length=6)


class ResetIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    recovery_id: Optional[str] = None
    otp: str
    new_password: str

class PasswordResetLinkIn(BaseModel):
    token: str = Field(min_length=20)
    new_password: str = Field(min_length=8)

class ProfileUpdateIn(BaseModel):
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    avatar_file_id: Optional[str] = None   # uploaded avatar (file id from POST /api/files); '' clears it

class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str

class ChangeContactStartIn(BaseModel):
    field: Literal['email', 'phone']
    value: str

class ChangeContactVerifyIn(BaseModel):
    code: str

class TicketIn(BaseModel):
    category: str
    subject: str
    description: Optional[str] = ''

class EmergencyContactIn(BaseModel):
    name: str = Field(min_length=1)
    phone: str = Field(min_length=6)
    email: Optional[EmailStr] = None
    instructions: Optional[str] = ''

class TrialIn(BaseModel):
    title: str
    protocol_id: str
    phase: str
    condition: str
    description: Optional[str] = ''
    sponsor_name: Optional[str] = ''
    drug: Optional[str] = ''
    duration: Optional[str] = ''
    target_enrollment: Optional[int] = None
    recruitment_status: Optional[str] = 'recruiting'
    ctri_number: Optional[str] = ''
    indications: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    side_effects: List[str] = Field(default_factory=list)
    emergency_contact: Optional[EmergencyContactIn] = None
    total_visits: Optional[int] = Field(default=None, ge=0)
    status: Optional[Literal['active', 'completed', 'terminated']] = 'active'

class TrialPatchIn(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1)
    phase: Optional[str] = None
    condition: Optional[str] = None
    description: Optional[str] = None
    drug: Optional[str] = None
    duration: Optional[str] = None
    target_enrollment: Optional[int] = Field(default=None, ge=0)
    recruitment_status: Optional[str] = None
    ctri_number: Optional[str] = None
    indications: Optional[List[str]] = None
    risks: Optional[List[str]] = None
    side_effects: Optional[List[str]] = None
    emergency_contact: Optional[EmergencyContactIn] = None
    total_visits: Optional[int] = Field(default=None, ge=0)
    status: Optional[Literal['active', 'completed', 'terminated']] = None

class SponsorTrialSiteIn(BaseModel):
    """A sponsor-owned site assignment for one trial.

    The site record is persistent and may be reused across trials. Professional
    contact details are intentionally limited to the PI/site team; patient PII
    is never accepted or returned by this contract.
    """
    name: str = Field(min_length=1)
    address: Optional[str] = ''
    city: Optional[str] = ''
    state: Optional[str] = ''
    hospital_type: Optional[Literal['Private', 'Government']] = 'Private'
    department: Optional[str] = ''
    pi_name: str = Field(min_length=1)
    pi_email: EmailStr
    pi_phone: Optional[str] = ''
    target_enrollment: Optional[int] = Field(default=None, ge=0)
    access_type: Literal['full', 'restricted', 'view_only'] = 'full'

class VisitIn(BaseModel):
    trial_id: str
    visit_number: int
    name: str
    day_offset: Optional[int] = None
    day_end: Optional[int] = None
    # Set only when day_offset is a 30-day/365-day approximation of a
    # protocol Month/Year label. Lets per-patient scheduling redo the math
    # with the patient's real baseline date instead of the approximation.
    calendar_offset_value: Optional[float] = None
    calendar_offset_unit: Optional[Literal['month', 'year']] = None
    window_days: Optional[int] = Field(default=None, ge=0)
    # Keep protocol timing semantics instead of flattening every visit to a
    # symmetric, whole-day baseline offset.
    hour_offset: Optional[float] = None
    hour_offset_basis: Optional[Literal['absolute', 'within_day']] = None
    hour_end: Optional[float] = None
    window_before: Optional[int] = Field(default=None, ge=0)
    window_after: Optional[int] = Field(default=None, ge=0)
    arm_label: Optional[str] = ''
    arm: Optional[str] = None
    source_day_label: Optional[str] = ''
    anchor_study_day: Optional[Literal[0, 1]] = None
    includes_day_zero: Optional[bool] = None
    relative_to: Optional[str] = None
    relative_offset_days: Optional[int] = None
    period: Optional[str] = None
    # Which independent Schedule of Assessments (substudy) this visit belongs
    # to, for a protocol that prints more than one (see extract-schedule's
    # schedule_variants). Blank/None for every ordinary, single-schedule
    # trial — matches everyone, exactly like a blank arm_label.
    substudy_label: Optional[str] = None
    activities: List[str] = []
    procedures: List[Dict] = Field(default_factory=list)
    operational_constraints: List[str] = Field(default_factory=list)
    visit_type: Optional[str] = ''
    location: Optional[str] = ''
    checklist: List[str] = []   # "before you come in" patient-prep steps
    clinical_tasks: List[str] = []
    admin_tasks: List[str] = []
    comments: Optional[str] = ''
    extraction_warning: bool = False
    review_status: Literal['pending', 'ok'] = 'ok'
    extracted_from_protocol: bool = False
    field_evidence: List[Dict] = Field(default_factory=list)

class VisitUpdate(BaseModel):
    """Partial edit of an existing visit TEMPLATE (Task 4.1 edit mode). Only the
    fields present are applied; trial_id is immutable here. `visit_number` is
    editable so the editor can keep template order unique after a row is
    deleted/reordered (Finding 1); a change re-points the seq of eligible
    future instances."""
    name: Optional[str] = None
    visit_number: Optional[int] = None
    day_offset: Optional[int] = None
    day_end: Optional[int] = None
    calendar_offset_value: Optional[float] = None
    calendar_offset_unit: Optional[Literal['month', 'year']] = None
    window_days: Optional[int] = Field(default=None, ge=0)
    hour_offset: Optional[float] = None
    hour_offset_basis: Optional[Literal['absolute', 'within_day']] = None
    hour_end: Optional[float] = None
    window_before: Optional[int] = Field(default=None, ge=0)
    window_after: Optional[int] = Field(default=None, ge=0)
    arm_label: Optional[str] = None
    arm: Optional[str] = None
    source_day_label: Optional[str] = None
    anchor_study_day: Optional[Literal[0, 1]] = None
    includes_day_zero: Optional[bool] = None
    relative_to: Optional[str] = None
    relative_offset_days: Optional[int] = None
    period: Optional[str] = None
    substudy_label: Optional[str] = None
    activities: Optional[List[str]] = None
    procedures: Optional[List[Dict]] = None
    operational_constraints: Optional[List[str]] = None
    visit_type: Optional[str] = None
    location: Optional[str] = None
    checklist: Optional[List[str]] = None
    clinical_tasks: Optional[List[str]] = None
    admin_tasks: Optional[List[str]] = None
    comments: Optional[str] = None
    extraction_warning: Optional[bool] = None
    review_status: Optional[Literal['pending', 'ok']] = None
    extracted_from_protocol: Optional[bool] = None
    field_evidence: Optional[List[Dict]] = None

class PatientIn(BaseModel):
    full_name: str
    email: Optional[EmailStr] = None
    phone: Optional[str] = ''
    trial_id: str
    pi_id: Optional[str] = None
    crc_id: Optional[str] = None
    enrolled_date: Optional[str] = None
    subject_id: Optional[str] = None
    dob: Optional[str] = None
    gender: Optional[str] = None
    language: Optional[str] = None
    avatar_initials: Optional[str] = None
    baseline_date: Optional[str] = None   # anchors visit-instance scheduling
    # Which substudy (independent Schedule of Assessments) this patient is
    # enrolled under, for a trial with more than one — see
    # GET /trials/{trial_id}/substudies. None for every ordinary trial;
    # materialize_visit_instances then materializes every template, exactly
    # as it always has.
    substudy_label: Optional[str] = None
    # Which arm/treatment-sequence (see GET /trials/{trial_id}/arms) this
    # patient is enrolled under, for a trial whose visit templates are
    # arm-tagged. None for every ordinary, single-arm/shared trial;
    # materialize_visit_instances then materializes every arm-untagged
    # template, exactly as it always has.
    arm_label: Optional[str] = None


class PatientInvitationIn(PatientIn):
    # Staff-created patient invitations are phone-first. Direct legacy patient
    # enrollment keeps its existing contract, while this flow requires phone.
    phone: str = Field(min_length=1)

def patient_initials(value: Optional[str], full_name: str) -> str:
    """Normalize staff-entered initials, falling back to the first two name parts."""
    supplied = ''.join(
        character for character in str(value or '').upper()
        if character.isalpha()
    )[:4]
    if supplied:
        return supplied
    return ''.join(
        word[0].upper() for word in str(full_name or '').split()[:2]
        if word
    ) or 'P'

class SchedulePreviewIn(BaseModel):
    baseline_date: str
    arm_label: Optional[str] = ''
    substudy_label: Optional[str] = ''

class MessageIn(BaseModel):
    conversation_id: str
    content: str

class ChatAttachmentIn(BaseModel):
    file_id: str
    name: str
    size: int
    content_type: Optional[str] = None
    duration: Optional[int] = None   # seconds, voice notes only

class ChatMessageIn(BaseModel):
    content: str = Field(default='', max_length=5000)
    type: Literal['text', 'image', 'document', 'voice'] = 'text'
    attachment: Optional[ChatAttachmentIn] = None

class ConversationIn(BaseModel):
    participant_ids: List[str]
    title: Optional[str] = None
    is_group: bool = False
    description: Optional[str] = None
    trial_id: Optional[str] = None

class ConversationSettingsIn(BaseModel):
    auto_delete_days: Optional[int] = Field(default=None, ge=0, le=365)
    title: Optional[str] = Field(default=None, min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=280)

class ConversationMembersIn(BaseModel):
    user_ids: List[str] = Field(min_length=1)

class ConversationReportIn(BaseModel):
    reason: Optional[str] = None

class TeamMemberPatchIn(BaseModel):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    designation: Optional[str] = Field(default=None, max_length=120)
    phone: Optional[str] = Field(default=None, max_length=32)
    role: Optional[str] = None

# ── Helpers ──────────────────────────────────────────────────────────────────
def make_token(sub: str, role: str, kind: str = 'access'):
    secret = JWT_SECRET if kind == 'access' else JWT_REFRESH_SECRET
    delta = timedelta(minutes=ACCESS_MIN) if kind == 'access' else timedelta(days=REFRESH_DAYS)
    return jwt.encode({'sub': sub, 'role': role, 'kind': kind,
                       'iat': now(), 'exp': now() + delta}, secret, ALGO)


def _refresh_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


async def issue_refresh_token(
    user_id: str,
    role: str,
    *,
    family_id: Optional[str] = None,
) -> tuple[str, dict]:
    """Issue a one-time opaque refresh token and persist only its SHA-256 hash."""
    raw = secrets.token_urlsafe(48)
    issued_at = now()
    doc = {
        'id': str(uuid.uuid4()),
        'token_hash': _refresh_token_hash(raw),
        'family_id': family_id or str(uuid.uuid4()),
        'user_id': user_id,
        'role': role,
        'status': 'active',
        'created_at': issued_at,
        'expires_at': issued_at + timedelta(days=REFRESH_DAYS),
    }
    await db.refresh_tokens.insert_one(doc)
    return raw, doc


async def revoke_refresh_family(token_doc: dict, reason: str, *, reuse=False):
    revoked_at = now()
    await db.refresh_tokens.update_many(
        {'family_id': token_doc['family_id'], 'status': {'$ne': 'revoked'}},
        {'$set': {'status': 'revoked', 'revoked_at': revoked_at,
                  'revoke_reason': reason}},
    )
    user = await db.users.find_one({'id': token_doc.get('user_id')}, {'_id': 0})
    action = 'auth.refresh_reuse_detected' if reuse else 'auth.session_revoked'
    await write_audit(
        user, action,
        ('Refresh-token reuse detected; revoked the entire session family'
         if reuse else f'Revoked refresh-token family: {reason}'),
        status='failure' if reuse else 'success',
        family_id=token_doc.get('family_id'),
    )

async def current_user(token: Optional[str] = Depends(oauth2)):
    if not token:
        raise HTTPException(401, 'Not authenticated')
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGO])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, 'Token expired')
    except jwt.PyJWTError:
        raise HTTPException(401, 'Invalid token')
    user = await db.users.find_one({'id': payload['sub']}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
    if not user:
        raise HTTPException(401, 'User not found')
    # Admin-suspended accounts are dead sessions (Task 6.1).
    if user.get('status') == 'Suspended':
        raise HTTPException(403, 'Account suspended')
    # Admin force-logout: any token issued BEFORE force_logout_at is invalid.
    # Tokens without an iat claim (pre-6.1) are treated as old → fail-closed.
    flo = user.get('force_logout_at')
    if flo:
        iat = payload.get('iat')
        issued = datetime.fromtimestamp(iat, tz=timezone.utc) if iat else None
        if issued is None or issued < flo:
            raise HTTPException(401, 'Session terminated — please sign in again')
    return user


async def optional_current_user(token: Optional[str] = Depends(oauth2)):
    """Return the signed-in user when supplied, while allowing public lookups."""
    if not token:
        return None
    return await current_user(token)

def require_roles(*allowed):
    async def dep(user=Depends(current_user)):
        if user['role'] not in allowed:
            raise HTTPException(403, 'Insufficient role')
        return user
    return dep

def serialize(d):
    if not d: return d
    d.pop('_id', None)
    d.pop('hashed_password', None)
    d.pop('security_answer_hash', None)
    return d

async def _read_upload_capped(file, max_bytes: int, too_large: str = 'File is too large') -> bytes:
    """Read an UploadFile in 1 MB chunks, aborting with 413 the moment the total
    exceeds max_bytes — so an oversized (or maliciously unbounded) request body
    never fully materializes in memory. Returns the bytes when within the cap."""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, too_large)
        chunks.append(chunk)
    return b''.join(chunks)

# ── Audit trail ──────────────────────────────────────────────────────────────
async def write_audit(user, action, detail, status='success', **ctx):
    """Write a standard audit row for any mutation.

    `user` is the acting user document (or None for anonymous/public actions,
    e.g. a public invitation accept). `action` is dotted `category.verb`
    (e.g. 'visit.patch'); the category is derived from it unless overridden
    via ctx. Extra keyword context (target_id, changes, …) is stored verbatim.
    Returns the audit row id.
    """
    user = user or {}
    doc = {
        'id': str(uuid.uuid4()),
        'user_id': user.get('id'),
        'user_name': user.get('full_name', ''),
        'role': user.get('role', ''),
        'org': user.get('organization', ''),
        'action': action,
        'category': ctx.pop('category', action.split('.', 1)[0]),
        'detail': detail,
        'ip': ctx.pop('ip', ''),
        'device': ctx.pop('device', ''),
        'status': status,
        'created_at': now(),
        **ctx,
    }
    await db.audit_logs.insert_one(doc)
    return doc['id']

# ── Organizations ────────────────────────────────────────────────────────────
ORG_TYPES = ('sponsor', 'cro', 'smo', 'site')

def org_type_for_role(role: str) -> str:
    """sponsor/cro/smo/site users belong to that org type; pi/crc (and anyone
    else) work at a site."""
    return role if role in ORG_TYPES else 'site'

def organization_name_key(name: Optional[str]) -> str:
    return ' '.join((name or '').strip().split()).casefold()


async def find_organization_by_name(name: Optional[str]) -> Optional[dict]:
    key = organization_name_key(name)
    if not key:
        return None
    organization = await db.organizations.find_one(
        {'name_key': key}, {'_id': 0})
    if organization:
        return organization
    normalized = ' '.join((name or '').strip().split())
    return await db.organizations.find_one(
        {'name': {'$regex': f'^{re.escape(normalized)}$', '$options': 'i'}},
        {'_id': 0},
    )


async def find_existing_organization(
    name: Optional[str] = None,
    google_place_id: Optional[str] = None,
) -> Optional[dict]:
    """Match a real-world place identifier first, then the normalized name.

    Place IDs protect against duplicate registrations when Google's canonical
    hospital name differs from the name already stored on MTB. Older records
    without a Place ID continue to use the existing exact normalized-name rule.
    """
    place_id = str(google_place_id or '').strip()
    if place_id and google_places.PLACE_ID_RE.fullmatch(place_id):
        organization = await db.organizations.find_one(
            {'google_place_id': place_id}, {'_id': 0})
        if organization:
            return organization
    return await find_organization_by_name(name)


async def ensure_organization(
    name: Optional[str],
    org_type: str = 'site',
    actor=None,
    details: Optional[Dict] = None,
):
    """Upsert an organization record the first time its name is seen
    and return ``(organization, created)``."""
    name = ' '.join((name or '').strip().split())
    if not name:
        return None, False
    details = details or {}
    google_place_id = str(
        details.get('googlePlaceId') or details.get('google_place_id') or '').strip()
    existing = await find_existing_organization(name, google_place_id)
    if existing:
        return existing, False
    organization = {
        'id': str(uuid.uuid4()), 'name': name,
        'name_key': organization_name_key(name),
        'type': org_type if org_type in ORG_TYPES else 'site',
        'address': (details.get('orgAddress') or details.get('address') or '').strip(),
        'hospital_type': (
            details.get('hospitalType') or details.get('hospital_type') or '').strip(),
        'contact': (details.get('phone') or '').strip(),
        'email': (details.get('email') or '').strip().lower(),
        'website': (details.get('website') or '').strip(),
        'status': 'active', 'created_at': now(),
    }
    if google_places.PLACE_ID_RE.fullmatch(google_place_id):
        organization['google_place_id'] = google_place_id
        organization['address_source'] = 'google_places'
    try:
        await db.organizations.insert_one(organization)
        created = True
    except DuplicateKeyError:
        organization = await find_existing_organization(name, google_place_id)
        created = False
    if created:
        await write_audit(actor, 'organization.create',
                          f'Organization "{name}" auto-created at registration',
                          target_id=organization['id'])
    return serialize({**organization}), created

# ── Auth ─────────────────────────────────────────────────────────────────────
@api.post('/auth/register')
async def register(body: RegisterIn):
    if body.role == 'admin':
        raise HTTPException(403, 'This role cannot self-register')
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    if await db.users.find_one({'email': email}):
        raise HTTPException(400, 'Email already registered')
    if phone and await db.users.find_one({'phone': phone}):
        raise HTTPException(400, 'Phone number already registered')
    uid = str(uuid.uuid4())
    doc = {
        'id': uid,
        'email': email,
        'full_name': body.full_name.strip(),
        'role': body.role,
        'phone': phone or '',
        'organization': (body.organization or '').strip(),
        'hashed_password': pwd_ctx.hash(body.password),
        'security_question': body.security_question or '',
        'security_answer_hash': pwd_ctx.hash(body.security_answer.lower()) if body.security_answer else '',
        'avatar_initials': ''.join([w[0].upper() for w in body.full_name.split()[:2]]) or 'U',
        'created_at': now(),
        'is_online': False,
    }
    await db.users.insert_one(doc)
    organization, created = await ensure_organization(
        body.organization, org_type_for_role(body.role), actor=doc)
    if organization:
        doc['org_admin'] = bool(created and body.role != 'patient')
        await db.users.update_one(
            {'id': uid}, {'$set': {'org_admin': doc['org_admin']}})
    access = make_token(uid, body.role, 'access')
    refresh, _ = await issue_refresh_token(uid, body.role)
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**doc})}

@api.post('/auth/login')
async def login(body: LoginIn):
    identifier = body.email.strip()
    if '@' in identifier:
        user = await db.users.find_one({'email': identifier.lower()})
    else:
        try:
            phone = normalize_phone(identifier)
        except HTTPException:
            phone = None
        user = await db.users.find_one({'phone': phone}) if phone else None
    if not user or not pwd_ctx.verify(body.password, user['hashed_password']):
        raise HTTPException(401, 'Invalid credentials')
    if user.get('status') == 'Suspended':
        raise HTTPException(403, 'Your account has been suspended. Contact support.')
    access = make_token(user['id'], user['role'], 'access')
    refresh, refresh_doc = await issue_refresh_token(user['id'], user['role'])
    await write_audit(
        user, 'auth.login', 'Signed in successfully',
        family_id=refresh_doc['family_id'])
    return {
        'access_token': access,
        'refresh_token': refresh,
        'expires_in': ACCESS_MIN * 60,
        'user': serialize({**user}),
    }

@api.post('/auth/refresh')
async def refresh_token(body: RefreshIn):
    token_hash = _refresh_token_hash(body.refresh_token)
    token_doc = await db.refresh_tokens.find_one({'token_hash': token_hash})
    if not token_doc:
        raise HTTPException(401, 'Invalid refresh token')
    if token_doc.get('status') != 'active':
        if token_doc.get('status') == 'consumed':
            await revoke_refresh_family(token_doc, 'refresh token reused', reuse=True)
        raise HTTPException(401, 'Refresh token is no longer valid')
    if token_doc.get('expires_at') and token_doc['expires_at'] <= now():
        await db.refresh_tokens.update_one(
            {'id': token_doc['id']}, {'$set': {'status': 'expired'}})
        raise HTTPException(401, 'Refresh token expired')

    consumed_at = now()
    consumed = await db.refresh_tokens.find_one_and_update(
        {
            'id': token_doc['id'],
            'status': 'active',
            'expires_at': {'$gt': consumed_at},
        },
        {'$set': {'status': 'consumed', 'consumed_at': consumed_at}},
        return_document=ReturnDocument.AFTER,
    )
    if not consumed:
        latest = await db.refresh_tokens.find_one({'id': token_doc['id']})
        if latest and latest.get('status') == 'consumed':
            await revoke_refresh_family(latest, 'refresh token reused', reuse=True)
        raise HTTPException(401, 'Refresh token is no longer valid')

    user = await db.users.find_one({'id': consumed['user_id']}, {'_id': 0})
    if not user or user.get('status') == 'Suspended':
        await revoke_refresh_family(consumed, 'user unavailable or suspended')
        raise HTTPException(401, 'Session is no longer active')
    force_logout_at = user.get('force_logout_at')
    if force_logout_at and consumed.get('created_at') < force_logout_at:
        await revoke_refresh_family(consumed, 'administrative force logout')
        raise HTTPException(401, 'Session was terminated')

    rotated, replacement = await issue_refresh_token(
        user['id'], user['role'],
        family_id=consumed['family_id'],
    )
    await db.refresh_tokens.update_one(
        {'id': consumed['id']},
        {'$set': {'replaced_by_id': replacement['id']}},
    )
    return {
        'access_token': make_token(user['id'], user['role'], 'access'),
        'refresh_token': rotated,
        'expires_in': ACCESS_MIN * 60,
    }


@api.post('/auth/logout')
async def logout(body: LogoutIn):
    if body.refresh_token:
        token_doc = await db.refresh_tokens.find_one(
            {'token_hash': _refresh_token_hash(body.refresh_token)})
        if token_doc:
            await revoke_refresh_family(token_doc, 'user logout')
    return {'ok': True}

@api.get('/auth/me')
async def me(user=Depends(current_user)):
    return user


@api.get('/master-data/options')
async def master_data_options(
    fieldType: Literal['department', 'designation'] = Query(...),
    user=Depends(optional_current_user),
):
    """Published values plus pending/rejected values private to the caller."""
    global_rows = await db.master_data_values.find(
        {'fieldType': fieldType}, {'_id': 0, 'value': 1},
    ).sort('value', 1).to_list(1000)
    private_rows = []
    if user:
        private_rows = await db.master_data_submissions.find({
            'fieldType': fieldType,
            'submittedById': user['id'],
            'status': {'$in': ['pending', 'rejected']},
        }, {
            '_id': 0, 'id': 1, 'value': 1, 'status': 1, 'rejectReason': 1,
        }).sort('dateSubmitted', -1).to_list(100)
    return {
        'values': [row['value'] for row in global_rows if row.get('value')],
        'private_values': private_rows,
    }

@api.patch('/auth/me')
async def update_me(body: ProfileUpdateIn, user=Depends(current_user)):
    """Update the signed-in user's profile. Name/phone/email are top-level;
    dob/gender/language ride in the `profile` sub-document."""
    updates: Dict = {}
    if body.full_name is not None and body.full_name.strip():
        name = body.full_name.strip()
        updates['full_name'] = name
        updates['avatar_initials'] = ''.join([w[0].upper() for w in name.split()[:2]]) or 'U'
    if body.phone is not None:
        updates['phone'] = normalize_phone(body.phone) or ''
    if body.email is not None:
        email = normalize_email(body.email)
        existing = await db.users.find_one({'email': email, 'id': {'$ne': user['id']}})
        if existing:
            raise HTTPException(400, 'That email is already in use by another account.')
        updates['email'] = email
    # profile sub-fields
    normalized_dob = None
    if body.dob is not None:
        normalized_profile = normalize_registration_profile(
            user.get('role') or '', {'dob': body.dob})
        normalized_dob = normalized_profile.get('dob')
        if 'age' in normalized_profile:
            updates['profile.age'] = normalized_profile['age']
    for key, val in (('dob', normalized_dob), ('gender', body.gender), ('language', body.language)):
        if val is not None:
            updates[f'profile.{key}'] = val
    if body.avatar_file_id is not None:
        updates['avatar_file_id'] = body.avatar_file_id.strip() or None
    if updates:
        await db.users.update_one({'id': user['id']}, {'$set': updates})
    fresh = await db.users.find_one({'id': user['id']}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
    return serialize(fresh)

@api.post('/auth/change-password')
async def change_password(body: ChangePasswordIn, user=Depends(current_user)):
    full = await db.users.find_one({'id': user['id']})
    if not full or not full.get('hashed_password') or not pwd_ctx.verify(body.current_password, full['hashed_password']):
        raise HTTPException(400, 'Your current password is incorrect.')
    if len(body.new_password) < 8:
        raise HTTPException(400, 'New password must be at least 8 characters.')
    await db.users.update_one({'id': user['id']}, {'$set': {'hashed_password': pwd_ctx.hash(body.new_password)}})
    return {'ok': True}

# ── Support tickets ───────────────────────────────────────────────────────────
@api.get('/support/tickets')
async def list_tickets(user=Depends(current_user)):
    return await db.support_tickets.find({'user_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(200)

@api.post('/support/tickets')
async def create_ticket(body: TicketIn, user=Depends(current_user)):
    n = now()
    ticket_id = f"#TKT-{n.strftime('%Y%m%d')}-{str(uuid.uuid4().int)[:4]}"
    doc = {
        'id': str(uuid.uuid4()), 'ticket_id': ticket_id, 'user_id': user['id'],
        'category': body.category, 'subject': body.subject.strip() or 'Support request',
        'description': body.description or '', 'status': 'Open',
        'created_at': n,
    }
    await db.support_tickets.insert_one(doc)
    return serialize({**doc})


async def _deliver_login_support_code(email: str, code: str, user_name: str):
    """Do not let provider behavior disclose whether a pre-login email exists."""
    try:
        await _deliver_otp(
            'email', email, code, user_name=user_name, purpose='login_support')
    except Exception:
        logging.exception('Pre-login support OTP delivery failed')


@api.post('/auth/support/start')
async def start_login_support(
    body: LoginSupportStartIn,
    request: Request,
    background_tasks: BackgroundTasks,
):
    """Start an email-verified support request without requiring a session.

    The response deliberately does not reveal whether the email is registered.
    Unknown addresses receive a synthetic request id and no database record.
    """
    email = normalize_email(body.email)
    subject = body.subject.strip()
    description = body.description.strip()
    if len(subject) < 3 or len(description) < 10:
        raise HTTPException(422, 'Enter a subject and at least 10 characters describing the issue.')

    await _enforce_rate_limit(f'login-support:{email}')
    client_host = request.client.host if request.client else 'unknown'
    await _enforce_rate_limit(f'login-support-ip:{client_host}')
    request_id = str(uuid.uuid4())
    response = {
        'ok': True,
        'message': 'If the email is registered, a verification code has been sent.',
        'request_id': request_id,
        'expires_in': otp_service.OTP_TTL_MIN * 60,
        'resend_cooldown': OTP_RESEND_COOLDOWN_SEC,
    }
    user = await db.users.find_one({'email': email})
    if not user:
        return response

    code = DEV_OTP_CODE if DEV_OTP_MODE and not _channel_configured('email') else otp_service.generate_code()
    n = now()
    await db.prelogin_support_requests.update_many(
        {'user_id': user['id'], 'status': 'pending'},
        {'$set': {'status': 'superseded', 'updated_at': n}},
    )
    await db.prelogin_support_requests.insert_one({
        'id': request_id,
        'user_id': user['id'],
        'registered_email': email,
        'subject': subject,
        'description': description,
        'otp_hash': pwd_ctx.hash(code),
        'otp_sent_at': n,
        'otp_attempts': 0,
        'status': 'pending',
        'created_at': n,
        'expires_at': n + timedelta(minutes=otp_service.OTP_TTL_MIN),
    })
    background_tasks.add_task(
        _deliver_login_support_code, email, code, user.get('full_name') or '')
    return response


@api.post('/auth/support/verify')
async def verify_login_support(body: LoginSupportVerifyIn):
    """Verify the email code and create one admin-visible login support ticket."""
    pending = await db.prelogin_support_requests.find_one({'id': body.request_id})
    if pending and pending.get('status') == 'submitted':
        if not _otp_matches(body.otp, pending.get('otp_hash')):
            raise HTTPException(400, 'Invalid OTP. Please enter the correct OTP.')
        ticket = await db.support_tickets.find_one(
            {'prelogin_request_id': body.request_id}, {'_id': 0})
        if ticket:
            return {'ok': True, 'ticket_id': ticket['ticket_id']}

    sent_at = pending.get('otp_sent_at') if pending else None
    expired = not sent_at or (now() - sent_at).total_seconds() > otp_service.OTP_TTL_MIN * 60
    attempts = int(pending.get('otp_attempts') or 0) if pending else 0
    valid = bool(pending) and pending.get('status') == 'pending' and not expired \
        and attempts < OTP_MAX_VERIFY_ATTEMPTS \
        and _otp_matches(body.otp, pending.get('otp_hash'))
    if not valid:
        if pending and pending.get('status') == 'pending' and not expired \
                and attempts < OTP_MAX_VERIFY_ATTEMPTS:
            await db.prelogin_support_requests.update_one(
                {'id': body.request_id}, {'$inc': {'otp_attempts': 1}})
        if pending and expired:
            raise HTTPException(400, 'Verification code expired. Request a new code.')
        if pending and attempts >= OTP_MAX_VERIFY_ATTEMPTS:
            raise HTTPException(429, 'Too many incorrect attempts. Request a new code.')
        raise HTTPException(400, 'Invalid OTP. Please enter the correct OTP.')

    claimed = await db.prelogin_support_requests.find_one_and_update(
        {'id': body.request_id, 'status': 'pending'},
        {'$set': {'status': 'submitting', 'verified_at': now()}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        ticket = await db.support_tickets.find_one(
            {'prelogin_request_id': body.request_id}, {'_id': 0})
        if ticket:
            return {'ok': True, 'ticket_id': ticket['ticket_id']}
        raise HTTPException(409, 'This support request has already been used.')

    n = now()
    ticket_id = f"#TKT-{n.strftime('%Y%m%d')}-{str(uuid.uuid4().int)[:4]}"
    ticket = {
        'id': str(uuid.uuid4()),
        'ticket_id': ticket_id,
        'user_id': claimed['user_id'],
        'registered_email': claimed['registered_email'],
        'category': 'Login Issue',
        'subject': claimed['subject'],
        'description': claimed['description'],
        'source': 'Pre-login',
        'status': 'Open',
        'prelogin_request_id': body.request_id,
        'created_at': n,
    }
    try:
        await db.support_tickets.insert_one(ticket)
        await db.prelogin_support_requests.update_one(
            {'id': body.request_id},
            {'$set': {'status': 'submitted', 'submitted_at': n}},
        )
    except Exception:
        await db.prelogin_support_requests.update_one(
            {'id': body.request_id, 'status': 'submitting'},
            {'$set': {'status': 'pending'}, '$unset': {'verified_at': ''}},
        )
        raise
    return {'ok': True, 'ticket_id': ticket_id}


@api.post('/auth/forgot')
async def forgot(body: ForgotIn):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    if bool(email) == bool(phone):
        raise HTTPException(400, 'Enter either your registered email or phone number')
    channel = 'email' if email else 'phone'
    target = email or phone
    user = await db.users.find_one({channel: target})
    recovery_id = str(uuid.uuid4())
    response = {
        'ok': True,
        'message': 'If the account exists, a code has been sent',
        'recovery_id': recovery_id,
        'channel': channel,
        'expires_in': otp_service.OTP_TTL_MIN * 60,
        'resend_cooldown': OTP_RESEND_COOLDOWN_SEC,
        'resend_limit': OTP_MAX_RESENDS,
    }
    if not user:
        return {**response, 'resend_count': 0}
    await _enforce_rate_limit(f'forgot:{target}')
    previous_at = user.get('reset_otp_at')
    previous_count = int(user.get('reset_otp_send_count') or 0)
    if previous_at:
        age = (now() - previous_at).total_seconds()
        if age < OTP_RESEND_COOLDOWN_SEC:
            raise HTTPException(429, f'Please wait {OTP_RESEND_COOLDOWN_SEC - int(age)} seconds before requesting another code.')
        if age > 30 * 60:
            previous_count = 0
    if previous_count >= OTP_MAX_RESENDS + 1:
        raise HTTPException(429, 'Maximum resend attempts reached. Please try again later.')
    code = DEV_OTP_CODE if DEV_OTP_MODE and not _channel_configured(channel) else otp_service.generate_code()
    await _deliver_otp(
        channel,
        target,
        code,
        user_name=user.get('full_name') or '',
        purpose='password_recovery',
    )
    await db.users.update_one(
        {'id': user['id']},
        {'$set': {
            'reset_otp_hash': pwd_ctx.hash(code),
            'reset_otp_at': now(),
            'reset_otp_attempts': 0,
            'reset_otp_send_count': previous_count + 1,
            'reset_recovery_id': recovery_id,
            'reset_channel': channel,
        }, '$unset': {'reset_otp': ''}}
    )
    return {**response, 'resend_count': max(previous_count, 0)}


async def _validate_password_recovery_otp(user: Optional[dict], supplied_otp: str) -> dict:
    sent_at = user.get('reset_otp_at') if user else None
    expired = not sent_at or (now() - sent_at).total_seconds() > otp_service.OTP_TTL_MIN * 60
    attempts = int(user.get('reset_otp_attempts') or 0) if user else 0
    valid = bool(user) and not expired and attempts < OTP_MAX_VERIFY_ATTEMPTS and _otp_matches(
        supplied_otp, user.get('reset_otp_hash')
    )
    if valid:
        return user
    if user and not expired and attempts < OTP_MAX_VERIFY_ATTEMPTS:
        await db.users.update_one({'id': user['id']}, {'$inc': {'reset_otp_attempts': 1}})
    if expired:
        raise HTTPException(400, 'Verification code expired. Request a new code.')
    if attempts >= OTP_MAX_VERIFY_ATTEMPTS:
        raise HTTPException(429, 'Too many incorrect attempts. Request a new code.')
    raise HTTPException(400, 'Invalid OTP. Please enter the correct OTP.')


@api.post('/auth/forgot/verify')
async def verify_forgot_password_otp(body: ForgotVerifyIn):
    user = await db.users.find_one({'reset_recovery_id': body.recovery_id})
    user = await _validate_password_recovery_otp(user, body.otp)
    await db.users.update_one(
        {'id': user['id']}, {'$set': {'reset_otp_verified_at': now()}})
    return {'verified': True}


@api.post('/auth/reset')
async def reset(body: ResetIn):
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    if body.recovery_id:
        user = await db.users.find_one({'reset_recovery_id': body.recovery_id})
    elif bool(email) != bool(phone):
        user = await db.users.find_one({'email' if email else 'phone': email or phone})
    else:
        raise HTTPException(400, 'Recovery session is required')
    user = await _validate_password_recovery_otp(user, body.otp)
    await db.users.update_one(
        {'id': user['id']},
        {'$set': {'hashed_password': pwd_ctx.hash(body.new_password)},
         '$unset': {
             'reset_otp': '', 'reset_otp_hash': '', 'reset_otp_at': '',
              'reset_otp_attempts': '', 'reset_otp_send_count': '',
              'reset_recovery_id': '', 'reset_channel': '', 'reset_otp_verified_at': '',
         }}
    )
    await db.refresh_tokens.update_many(
        {'user_id': user['id'], 'status': 'active'},
        {'$set': {'status': 'revoked', 'revoked_at': now(),
                  'revoke_reason': 'password recovered'}},
    )
    return {'ok': True}


@api.post('/auth/password-reset-link')
async def complete_password_reset_link(body: PasswordResetLinkIn):
    """Consume an admin-issued reset/setup link exactly once.

    Only a SHA-256 digest is stored. Claiming the token is atomic, so concurrent
    or replayed submissions cannot both reset the account.
    """
    password = body.new_password
    if not (
        len(password) >= 8
        and re.search(r'[A-Z]', password)
        and re.search(r'[a-z]', password)
        and re.search(r'\d', password)
        and re.search(r'[^A-Za-z0-9]', password)
    ):
        raise HTTPException(
            400,
            'Password must include uppercase, lowercase, number, and special character.')
    token_hash = hashlib.sha256(body.token.encode('utf-8')).hexdigest()
    consumed_at = now()
    token_doc = await db.password_reset_tokens.find_one_and_update(
        {
            'token_hash': token_hash,
            'used_at': None,
            'revoked_at': None,
            'expires_at': {'$gt': consumed_at},
        },
        {'$set': {'used_at': consumed_at}},
        projection={'_id': 0},
        return_document=ReturnDocument.AFTER,
    )
    if not token_doc:
        raise HTTPException(400, 'This password link is invalid, expired, or already used.')
    user = await db.users.find_one({'id': token_doc['user_id']})
    if not user:
        raise HTTPException(400, 'This password link is invalid, expired, or already used.')
    await db.users.update_one(
        {'id': user['id']},
        {
            '$set': {
                'hashed_password': pwd_ctx.hash(password),
                'force_logout_at': consumed_at,
                'password_changed_at': consumed_at,
                'status': 'Active',
            },
            '$unset': {'must_reset_password': ''},
        })
    await db.refresh_tokens.update_many(
        {'user_id': user['id'], 'status': 'active'},
        {'$set': {'status': 'revoked', 'revoked_at': consumed_at,
                  'revoke_reason': 'password reset completed'}},
    )
    await write_audit(
        user,
        'account.password_reset_link_complete',
        'Completed a single-use password setup/reset link',
        target_id=user['id'],
        reset_token_id=token_doc['id'],
    )
    return {'ok': True}

# ── Registration with OTP verification ───────────────────────────────────────
OTP_MAX_VERIFY_ATTEMPTS = 6      # wrong-code attempts before the pending is locked
OTP_MAX_RESENDS = 3              # resend attempts allowed per channel
OTP_RESEND_COOLDOWN_SEC = 30     # min gap between sends for one registration
# After OTP verification, allow time to choose a strong password before the
# temporary registration is removed by MongoDB's TTL index.
REGISTER_COMPLETE_TTL_MIN = 10
OTP_RATE_LIMIT = 5               # sends allowed per identifier (phone/email) …
OTP_RATE_WINDOW_SEC = 3600       # … within this rolling window

# ── DEV-ONLY OTP bypass ───────────────────────────────────────────────────────
# When DEV_OTP_MODE is on, any channel WITHOUT a configured provider (no SMTP /
# no MSG91) is not actually sent, and a single fixed code (DEV_OTP_CODE) is
# accepted for that channel — so the signup flow can be tested end-to-end before
# real providers are wired. A channel that IS configured still sends real codes.
# ⚠️  MUST be OFF in production (leaves signup wide open otherwise).
DEV_OTP_MODE = os.environ.get('DEV_OTP_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')
DEV_OTP_CODE = os.environ.get('DEV_OTP_CODE', '000000').strip()

def _channel_configured(channel: str) -> bool:
    if channel == 'email':
        return bool(os.environ.get('BREVO_API_KEY') and os.environ.get('BREVO_FROM_EMAIL'))
    return bool(os.environ.get('MSG91_AUTHKEY') and os.environ.get('MSG91_TEMPLATE_ID'))

def required_channels(role: str) -> List[str]:
    """Patients verify by phone only; everyone else verifies email AND phone."""
    return ['phone'] if role == 'patient' else ['email', 'phone']

def _otp_matches(supplied: Optional[str], hashed: Optional[str]) -> bool:
    if not supplied:
        return False
    # DEV-ONLY: accept the fixed dev code for any channel (see DEV_OTP_MODE).
    if DEV_OTP_MODE and supplied.strip() == DEV_OTP_CODE:
        return True
    if not hashed:
        return False
    try:
        return pwd_ctx.verify(supplied.strip(), hashed)
    except Exception:
        return False

async def _enforce_rate_limit(identifier: str):
    """Fixed-window per-identifier cap to curb abuse and runaway SMS spend."""
    key = f'otp:{identifier.lower()}'
    n = now()
    doc = await db.otp_throttle.find_one({'_id': key})
    if doc and (n - doc['window_start']).total_seconds() < OTP_RATE_WINDOW_SEC:
        if doc['count'] >= OTP_RATE_LIMIT:
            mins = int((OTP_RATE_WINDOW_SEC - (n - doc['window_start']).total_seconds()) // 60) + 1
            raise HTTPException(429, f'Too many verification requests. Please try again in about {mins} minute(s).')
        await db.otp_throttle.update_one({'_id': key}, {'$inc': {'count': 1}})
    else:
        await db.otp_throttle.replace_one(
            {'_id': key},
            {'_id': key, 'count': 1, 'window_start': n, 'expires_at': n + timedelta(seconds=OTP_RATE_WINDOW_SEC)},
            upsert=True,
        )

async def _deliver_otp(
    channel: str,
    target: str,
    code: str,
    *,
    user_name: str = '',
    purpose: str = 'registration',
):
    """Send a code, mapping provider failures to clean HTTP errors. Blocking
    provider I/O runs in a threadpool so it never stalls the event loop."""
    # DEV-ONLY: don't try to send on a channel with no provider; the fixed dev
    # code will be accepted at verify time instead.
    if DEV_OTP_MODE and not _channel_configured(channel):
        logging.warning('[DEV_OTP_MODE] Skipped real %s OTP to %s — enter dev code "%s" in the app.', channel, target, DEV_OTP_CODE)
        return
    try:
        if channel == 'email':
            await run_in_threadpool(
                otp_service.send_email,
                target,
                code,
                user_name,
                purpose,
            )
        else:
            await run_in_threadpool(otp_service.send_sms, target, code)
    except otp_service.OTPConfigError:
        logging.error('OTP channel %s is not configured', channel)
        raise HTTPException(503, f'{channel.capitalize()} verification is temporarily unavailable.')
    except otp_service.OTPDeliveryError:
        raise HTTPException(502, f'We could not send the {channel} code. Please try again.')

async def _finalize_registration(pending: dict) -> dict:
    """Create the real user from a fully-verified pending registration + issue tokens."""
    uid = str(uuid.uuid4())
    name = pending['full_name']
    organization = None
    organization_created = False
    organization_name = pending.get('organization') or ''
    if organization_name:
        organization, organization_created = await ensure_organization(
            organization_name,
            pending.get('organization_type') or org_type_for_role(pending['role']),
            actor={'id': uid, 'full_name': name, 'role': pending['role']},
            details={
                **(pending.get('profile') or {}),
                'email': pending.get('email') or '',
                'phone': pending.get('phone') or '',
            },
        )
    is_org_registration = bool(
        pending.get('creates_organization') and pending['role'] != 'patient')
    if is_org_registration and not organization_created:
        raise HTTPException(
            409,
            'This organization was registered while you were signing up. '
            'Ask its administrator to invite you.',
        )
    if pending.get('invitation_id') and (not organization or organization_created):
        if organization_created and organization:
            await db.organizations.delete_one({'id': organization['id']})
        raise HTTPException(
            409, 'The organization attached to this invitation is unavailable')

    doc = {
        'id': uid,
        'email': (pending.get('email') or '').lower(),
        'full_name': name,
        'role': pending['role'],
        'phone': pending.get('phone') or '',
        'organization': pending.get('organization') or '',
        'hashed_password': pending['hashed_password'],
        'security_question': pending.get('security_question') or '',
        'security_answer_hash': pending.get('security_answer_hash') or '',
        'security_questions': pending.get('security_questions') or [],
        'profile': pending.get('profile') or {},
        'avatar_initials': ''.join([w[0].upper() for w in name.split()[:2]]) or 'U',
        'email_verified': bool(pending.get('email_verified')),
        'phone_verified': bool(pending.get('phone_verified')),
        'created_at': now(),
        'is_online': False,
        'org_admin': is_org_registration and organization_created,
    }
    if pending.get('site'):
        doc['site'] = pending['site']
    if pending.get('supervising_pi_id'):
        doc['supervising_pi_id'] = pending['supervising_pi_id']
    try:
        await db.users.insert_one(doc)
    except Exception:
        if organization_created and organization:
            await db.organizations.delete_one({'id': organization['id']})
        raise
    access = make_token(uid, doc['role'], 'access')
    refresh, _ = await issue_refresh_token(uid, doc['role'])
    return {'access_token': access, 'refresh_token': refresh, 'user': serialize({**doc})}


async def _complete_registration(pending: dict) -> dict:
    """Create the account and consume its invitation as one lifecycle."""
    invitation = None
    invitation_id = pending.get('invitation_id')
    if invitation_id:
        invitation = await db.invitations.find_one_and_update(
            {
                'id': invitation_id,
                'token': pending.get('invite_token'),
                'status': 'pending',
            },
            {'$set': {
                'status': 'accepting',
                'registration_id': pending['id'],
                'accepting_at': now(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not invitation:
            current = await db.invitations.find_one(
                {'id': invitation_id}, {'_id': 0, 'status': 1})
            status = (current or {}).get('status', 'unavailable')
            raise HTTPException(
                409, f'This invitation is {status} and can no longer be used')

    created_user_id = None
    created_patient_id = None
    created_master_submission_id = None
    try:
        session = await _finalize_registration(pending)
        created_user_id = session['user']['id']
        profile = pending.get('profile') or {}
        if pending.get('role') == 'pi' and profile.get('department_is_custom'):
            submission = {
                'id': str(uuid.uuid4()),
                'fieldType': 'department',
                'value': str(profile.get('department') or '').strip(),
                'submittedBy': pending.get('full_name') or '',
                'submittedById': created_user_id,
                'org': pending.get('organization') or '',
                'dateSubmitted': now(),
                'status': 'pending',
                'actionBy': None,
                'rejectReason': '',
            }
            await db.master_data_submissions.insert_one(submission)
            created_master_submission_id = submission['id']
            review_fields = {
                'department_submission_id': submission['id'],
                'department_review_status': 'pending',
            }
            await db.users.update_one({'id': created_user_id}, {'$set': {
                f'profile.{key}': value for key, value in review_fields.items()
            }})
            session['user'].setdefault('profile', {}).update(review_fields)
        if invitation:
            accepted_details = {
                'full_name': pending.get('full_name') or '',
                'designation': (pending.get('profile') or {}).get('designation', ''),
                'phone': pending.get('phone') or '',
                'role': pending.get('role') or invitation.get('role') or 'patient',
            }
            result = await db.invitations.update_one(
                {
                    'id': invitation['id'],
                    'status': 'accepting',
                    'registration_id': pending['id'],
                },
                {'$set': {
                    'status': 'accepted',
                    'accepted_at': now(),
                    'accepted_user_id': created_user_id,
                    **accepted_details,
                }, '$unset': {'accepting_at': ''}},
            )
            if result.modified_count != 1:
                raise RuntimeError('Invitation acceptance could not be finalized')

            if invitation.get('role') == 'patient' and invitation.get('trial_id'):
                patient_data = invitation.get('patient_data')
                if patient_data:
                    subject_id = patient_data.get('subject_id')
                    if subject_id:
                        duplicate = await db.patients.find_one(
                            {'trial_id': invitation['trial_id'], 'subject_id': subject_id},
                            {'_id': 0, 'id': 1})
                        if duplicate:
                            raise HTTPException(
                                409,
                                f'Subject ID {subject_id} is already enrolled in this trial',
                            )
                    patient_id = str(uuid.uuid4())
                    patient_doc = {
                        **patient_data,
                        'id': patient_id,
                        'trial_id': invitation['trial_id'],
                        'user_id': created_user_id,
                        'full_name': pending.get('full_name') or invitation.get('full_name', ''),
                        'email': pending.get('email') or invitation.get('email', ''),
                        'phone': pending.get('phone') or invitation.get('phone', ''),
                        'created_by': invitation.get('invited_by'),
                        'created_at': now(),
                        'enrolled_date': now().date().isoformat(),
                        'completed_visit_ids': [],
                        'avatar_initials': patient_initials(
                            patient_data.get('avatar_initials'),
                            pending.get('full_name') or invitation.get('full_name', ''),
                        ),
                    }
                    # Invitation values are prefills; preserve any final
                    # profile changes the patient makes while registering.
                    registration_profile = pending.get('profile') or {}
                    for profile_key in ('dob', 'gender', 'language', 'age'):
                        if registration_profile.get(profile_key) not in (None, ''):
                            patient_doc[profile_key] = registration_profile[profile_key]
                    await db.patients.insert_one(patient_doc)
                    created_patient_id = patient_id
                    created_visits = await materialize_visit_instances(patient_doc)
                    await db.invitations.update_one(
                        {'id': invitation['id'], 'status': 'accepted'},
                        {'$set': {'patient_id': patient_id}},
                    )
                    await write_audit(
                        session['user'], 'patient.enroll',
                        f"Accepted invitation and enrolled in trial {invitation['trial_id']} "
                        f"({created_visits} visit instance(s) materialized)",
                        target_id=patient_id, trial_id=invitation['trial_id'],
                    )
                else:
                    # Legacy patient invitations link the account to a record that
                    # was created before the invitation was sent.
                    contacts = []
                    if pending.get('email'):
                        contacts.append({'email': pending['email']})
                    if pending.get('phone'):
                        contacts.append({'phone': pending['phone']})
                    if contacts:
                        await db.patients.update_one(
                            {
                                '$and': [
                                    {'trial_id': invitation['trial_id']},
                                    {'$or': contacts},
                                    {'$or': [
                                        {'user_id': {'$exists': False}},
                                        {'user_id': None},
                                    ]},
                                ],
                            },
                            {'$set': {
                                'user_id': created_user_id,
                                'full_name': pending.get('full_name') or invitation.get('full_name', ''),
                                'phone': pending.get('phone') or invitation.get('phone', ''),
                            }},
                        )
            await write_audit(
                session['user'], 'invitation.accept',
                f"Invitation for {invitation.get('email') or invitation.get('phone')} accepted",
                target_id=invitation['id'])
        await db.pending_registrations.delete_one({'id': pending['id']})
        return session
    except Exception:
        if created_master_submission_id:
            await db.master_data_submissions.delete_one(
                {'id': created_master_submission_id})
        if created_patient_id:
            await db.patients.delete_one({'id': created_patient_id})
        if created_user_id:
            await db.refresh_tokens.delete_many({'user_id': created_user_id})
            await db.users.delete_one({'id': created_user_id})
        if invitation:
            await db.invitations.update_one(
                {
                    'id': invitation['id'],
                    'status': 'accepting',
                    'registration_id': pending['id'],
                },
                {'$set': {'status': 'pending'},
                 '$unset': {'registration_id': '', 'accepting_at': ''}},
            )
        raise


@api.post('/auth/register/check-availability')
async def register_check_availability(body: RegisterAvailabilityIn):
    """Field-level duplicate check for the registration details screen.

    The authoritative checks in ``register_start`` remain in place to protect
    against races between this preview and submission.
    """
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    return {
        'email': ({
            'available': not bool(await db.users.find_one({'email': email}, {'_id': 1})),
        } if email else None),
        'phone': ({
            'available': not bool(await db.users.find_one({'phone': phone}, {'_id': 1})),
        } if phone else None),
    }


@api.post('/auth/register/start')
async def register_start(body: RegisterStartIn):
    invitation = None
    if body.invite_token:
        invitation = await _find_invitation_by_code(body.invite_token)
        if not invitation:
            raise HTTPException(404, 'Invitation not found')
        status = _invitation_status(invitation)
        if status != 'pending':
            raise HTTPException(
                400, f'This invitation is {status} and can no longer be used')

    registration_entity_role = invitation.get('role') if invitation else body.role
    effective_role = registration_entity_role
    if not invitation and registration_entity_role in ('site', 'smo'):
        selected_organization_role = str(
            (body.profile or {}).get('role') or '').strip().lower()
        if selected_organization_role == 'pi':
            effective_role = 'pi'
        elif selected_organization_role in ('research team', 'crc'):
            effective_role = 'crc'
        elif selected_organization_role in ('administrative', 'administrator', 'admin'):
            effective_role = registration_entity_role
        else:
            raise HTTPException(
                400,
                f'Select PI, Research Team, or Administrative for '
                f'{registration_entity_role.upper()} registration',
            )
    if effective_role == 'admin':
        raise HTTPException(403, 'This role cannot self-register')
    # Possession of the emailed invitation code establishes the invited email.
    # Invited users therefore verify only the phone number they enter during
    # registration; normal self-registration keeps its role-based channels.
    channels = ['phone'] if invitation else required_channels(effective_role)
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    organization = (body.organization or '').strip() or None
    if invitation:
        invited_email = (invitation.get('email') or '').lower().strip() or None
        if invited_email and email and invited_email != email:
            raise HTTPException(400, 'Email must match the invitation')
        email = invited_email or email
        organization = (invitation.get('org') or '').strip()

    profile = normalize_registration_profile(effective_role, body.profile)
    if effective_role == 'pi' and profile.get('department_is_custom'):
        custom_department = str(profile.get('department') or '').strip()
        if custom_department.lower() == 'others specify':
            custom_department = ''
        if len(custom_department) < 2 or len(custom_department) > 120:
            raise HTTPException(
                400, 'Specify a department between 2 and 120 characters')
        profile['department'] = custom_department
        profile['department_is_custom'] = True
    else:
        profile.pop('department_is_custom', None)
    if not invitation and registration_entity_role == 'smo':
        raw_hospitals = profile.get('hospitals')
        smo_roles = {
            'pi': 'PI',
            'research team': 'Research Team',
            'crc': 'Research Team',
            'administrative': 'Administrative',
        }
        hospital_types = {'private': 'Private', 'government': 'Government'}
        if not isinstance(raw_hospitals, list) or not raw_hospitals:
            raise HTTPException(
                400, 'Add at least one hospital or clinical trial site managed by the SMO')
        hospitals = []
        for hospital in raw_hospitals:
            if not isinstance(hospital, dict):
                raise HTTPException(400, 'Each SMO hospital entry must be an object')
            name = str(hospital.get('name') or '').strip()
            address = str(hospital.get('address') or '').strip()
            hospital_type = str(hospital.get('type') or '').strip().lower()
            hospital_role = str(hospital.get('role') or '').strip().lower()
            if not name or not address:
                raise HTTPException(400, 'Hospital / site name and location are required')
            if hospital_type not in hospital_types:
                raise HTTPException(400, 'Select a hospital type: Private or Government')
            if hospital_role not in smo_roles:
                raise HTTPException(
                    400, 'Select a hospital role: PI, Research Team, or Administrative')
            normalized_hospital = {
                'name': name,
                'address': address,
                'type': hospital_types[hospital_type],
                'role': smo_roles[hospital_role],
            }
            google_place_id = str(hospital.get('google_place_id') or '').strip()
            if google_places.PLACE_ID_RE.fullmatch(google_place_id):
                normalized_hospital['google_place_id'] = google_place_id
                normalized_hospital['address_source'] = 'google_places'
            hospitals.append(normalized_hospital)
        profile['hospitals'] = hospitals

    if 'email' in channels and not email:
        raise HTTPException(400, 'Email is required for this role')
    if 'phone' in channels and not phone:
        raise HTTPException(400, 'Phone number is required')
    if email and await db.users.find_one({'email': email}):
        raise HTTPException(400, 'Email already registered')
    if phone and await db.users.find_one({'phone': phone}):
        raise HTTPException(400, 'Phone number already registered')

    creates_organization = not invitation and effective_role != 'patient'
    organization_record = None
    if creates_organization:
        if not organization:
            raise HTTPException(
                400, 'Organization name is required when registering an organization')
        google_place_id = str(
            profile.get('googlePlaceId') or profile.get('google_place_id') or '').strip()
        if google_place_id and not google_places.PLACE_ID_RE.fullmatch(google_place_id):
            raise HTTPException(400, 'Invalid Google Place ID')
        organization_record = await find_existing_organization(
            organization, google_place_id)
        if organization_record:
            raise HTTPException(
                409,
                'This organization is already registered. '
                'Ask its administrator to invite you.',
            )
    elif invitation:
        if not organization:
            raise HTTPException(400, 'This invitation is not linked to an organization')
        organization_record = await find_organization_by_name(organization)
        if not organization_record:
            raise HTTPException(
                409, 'The organization attached to this invitation is unavailable')

    # Throttle per identifier before we generate or send anything.
    if phone:
        await _enforce_rate_limit(phone)
    if email:
        await _enforce_rate_limit(email)

    # Drop any earlier in-flight attempt for these identifiers so they can't pile up.
    await db.pending_registrations.delete_many({'$or': [
        *([{'phone': phone}] if phone else []),
        *([{'email': email}] if email else []),
    ]})

    # Hash the three security-question answers (design step 3), storing only hashes.
    sec_qs = []
    for q in (body.security_questions or []):
        question = (q.get('question') or '').strip()
        answer = (q.get('answer') or '').strip().lower()
        if question and answer:
            sec_qs.append({'question': question, 'answer_hash': pwd_ctx.hash(answer)})

    rid = str(uuid.uuid4())
    doc = {
        'id': rid,
        'full_name': body.full_name.strip(),
        'role': effective_role,
        'email': email,
        'phone': phone,
        'organization': organization,
        # Password may be set now (legacy callers) or later via /register/complete.
        'hashed_password': pwd_ctx.hash(body.password) if body.password else None,
        'security_question': body.security_question or (sec_qs[0]['question'] if sec_qs else ''),
        'security_answer_hash': (pwd_ctx.hash(body.security_answer.lower()) if body.security_answer
                                 else (sec_qs[0]['answer_hash'] if sec_qs else '')),
        'security_questions': sec_qs,
        'security_questions_completed': bool(sec_qs),
        'profile': profile,
        'channels': channels,
        'email_verified': bool(invitation and invitation.get('email')),
        'phone_verified': False,
        'attempts': 0,
        'send_count': len(channels),
        'resend_counts': {ch: 0 for ch in channels},
        'last_sent_at': now(),
        'created_at': now(),
        'expires_at': now() + timedelta(minutes=otp_service.OTP_TTL_MIN),
    }
    if creates_organization:
        doc['creates_organization'] = True
        doc['organization_type'] = org_type_for_role(registration_entity_role)
    if invitation:
        doc['invitation_id'] = invitation['id']
        doc['invite_token'] = normalize_invite_code(invitation['token'])
        if invitation.get('supervising_pi_id'):
            doc['supervising_pi_id'] = invitation['supervising_pi_id']
        doc['organization_type'] = (
            organization_record.get('type') or org_type_for_role(effective_role))
        if invitation.get('site'):
            doc['site'] = invitation['site']

    # Generate codes, store ONLY their hashes, then deliver. If a send fails we
    # raise — nothing is persisted, so the user is never told a code is on its way.
    codes = {ch: otp_service.generate_code() for ch in channels}
    for ch in channels:
        doc[f'{ch}_otp_hash'] = pwd_ctx.hash(codes[ch])
    for ch in channels:
        target = email if ch == 'email' else phone
        assert target  # validated present above for every required channel
        await _deliver_otp(
            ch,
            target,
            codes[ch],
            user_name=doc['full_name'],
            purpose='registration',
        )

    await db.pending_registrations.insert_one(doc)
    return {
        'registration_id': rid,
        'channels': channels,
        'email': email,
        'phone': phone,
        'expires_in': otp_service.OTP_TTL_MIN * 60,
        'resend_cooldown': OTP_RESEND_COOLDOWN_SEC,
    }

@api.post('/auth/register/verify')
async def register_verify(body: RegisterVerifyIn):
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if pending['expires_at'] < now():
        await db.pending_registrations.delete_one({'id': pending['id']})
        raise HTTPException(400, 'Your verification code expired. Please restart registration.')
    if pending.get('attempts', 0) >= OTP_MAX_VERIFY_ATTEMPTS:
        await db.pending_registrations.delete_one({'id': pending['id']})
        raise HTTPException(429, 'Too many incorrect attempts. Please restart registration.')

    if not body.email_otp and not body.phone_otp:
        raise HTTPException(400, 'A verification code is required')

    # Channels are verified independently, one call per channel or both
    # together — a code supplied for a channel that's already verified or
    # not required is simply ignored rather than treated as an error, so the
    # phone-verify and email-verify screens can each call this with only
    # their own channel's code.
    updates = {}
    for ch, supplied in (('email', body.email_otp), ('phone', body.phone_otp)):
        if not supplied or ch not in pending['channels'] or pending.get(f'{ch}_verified'):
            continue
        if not _otp_matches(supplied, pending.get(f'{ch}_otp_hash')):
            await db.pending_registrations.update_one({'id': pending['id']}, {'$inc': {'attempts': 1}})
            raise HTTPException(400, f'Incorrect {ch} verification code')
        updates[f'{ch}_verified'] = True

    if updates:
        await db.pending_registrations.update_one({'id': pending['id']}, {'$set': updates})
        pending.update(updates)

    if not all(pending.get(f'{ch}_verified') for ch in pending['channels']):
        return {'verified': False, 'channels': pending['channels'],
                'email_verified': pending.get('email_verified', False),
                'phone_verified': pending.get('phone_verified', False)}

    # All channels verified. In the design flow the password isn't set yet — keep the
    # pending record and let /register/complete create the account. Legacy callers that
    # supplied a password at /start are finalized immediately here.
    if not pending.get('hashed_password'):
        await db.pending_registrations.update_one(
            {'id': pending['id']},
            {'$set': {
                'fully_verified': True,
                'expires_at': now() + timedelta(minutes=REGISTER_COMPLETE_TTL_MIN),
            }},
        )
        return {'verified': True, 'pending_password': True}

    session = await _complete_registration(pending)
    return {'verified': True, **session}

@api.post('/auth/register/complete')
async def register_complete(body: RegisterCompleteIn):
    """Final step of the design flow: set the password on an already-verified pending
    registration and create the account."""
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if pending['expires_at'] < now():
        await db.pending_registrations.delete_one({'id': pending['id']})
        raise HTTPException(400, 'Your password setup session expired. Please restart registration.')
    if not all(pending.get(f'{ch}_verified') for ch in pending['channels']):
        raise HTTPException(400, 'Please verify your contact details before setting a password.')
    if not pending.get('security_questions_completed'):
        raise HTTPException(400, 'Please complete your security questions before setting a password.')
    pending['hashed_password'] = pwd_ctx.hash(body.password)
    session = await _complete_registration(pending)
    return {'verified': True, **session}


@api.post('/auth/register/security-questions')
async def register_security_questions(body: RegisterSecurityQuestionsIn):
    """Save recovery questions after phone/email verification, for every
    registration path (invited or self-registered)."""
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if pending['expires_at'] < now():
        await db.pending_registrations.delete_one({'id': pending['id']})
        raise HTTPException(400, 'Your registration session expired. Please restart registration.')
    if not pending.get('fully_verified'):
        raise HTTPException(400, 'Please verify your phone number before setting security questions.')
    if len(body.security_questions) != 3:
        raise HTTPException(400, 'Please answer all three security questions')

    questions = []
    seen = set()
    for item in body.security_questions:
        question = str(item.get('question') or '').strip()
        answer = str(item.get('answer') or '').strip().lower()
        if not question or not answer:
            raise HTTPException(400, 'Please answer all three security questions')
        normalized_question = question.casefold()
        if normalized_question in seen:
            raise HTTPException(400, 'Please select three different security questions')
        seen.add(normalized_question)
        questions.append({
            'question': question,
            'answer_hash': pwd_ctx.hash(answer),
        })

    await db.pending_registrations.update_one(
        {'id': pending['id']},
        {'$set': {
            'security_question': questions[0]['question'],
            'security_answer_hash': questions[0]['answer_hash'],
            'security_questions': questions,
            'security_questions_completed': True,
        }},
    )
    return {'ok': True}

@api.post('/auth/register/resend')
async def register_resend(body: RegisterResendIn):
    pending = await db.pending_registrations.find_one({'id': body.registration_id})
    if not pending:
        raise HTTPException(404, 'Registration not found or already completed')
    if body.channel not in pending['channels']:
        raise HTTPException(400, 'Channel not used for this registration')

    last_sent = pending.get('last_sent_at')
    if last_sent and (now() - last_sent).total_seconds() < OTP_RESEND_COOLDOWN_SEC:
        wait = OTP_RESEND_COOLDOWN_SEC - int((now() - last_sent).total_seconds())
        raise HTTPException(429, f'Please wait {wait}s before requesting another code.')
    resend_counts = pending.get('resend_counts') or {}
    if int(resend_counts.get(body.channel, 0)) >= OTP_MAX_RESENDS:
        raise HTTPException(429, 'Resend limit reached. Please restart registration.')

    target = pending['email'] if body.channel == 'email' else pending['phone']
    await _enforce_rate_limit(target)

    code = otp_service.generate_code()
    await _deliver_otp(
        body.channel,
        target,
        code,
        user_name=pending.get('full_name') or '',
        purpose='registration',
    )
    await db.pending_registrations.update_one(
        {'id': pending['id']},
        {'$set': {f'{body.channel}_otp_hash': pwd_ctx.hash(code),
                  'last_sent_at': now(),
                  'expires_at': now() + timedelta(minutes=otp_service.OTP_TTL_MIN)},
         '$inc': {'send_count': 1, f'resend_counts.{body.channel}': 1}},
    )
    return {'ok': True, 'resend_cooldown': OTP_RESEND_COOLDOWN_SEC,
            'resend_count': int(resend_counts.get(body.channel, 0)) + 1,
            'resend_limit': OTP_MAX_RESENDS}

# ── Contact change (email / phone) with OTP verification ─────────────────────
# Reuses the registration OTP machinery (_deliver_otp / _otp_matches /
# _enforce_rate_limit / DEV_OTP_MODE). A single pending change per user lives in
# `pending_contact_changes`; a new /start replaces any earlier one, and rows
# auto-expire via the TTL index on `expires_at` (see _ensure_indexes).
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

async def _contact_value_taken(field: str, value: str, user_id: str) -> bool:
    return bool(await db.users.find_one({field: value, 'id': {'$ne': user_id}}))

@api.post('/auth/change-contact/start')
async def change_contact_start(body: ChangeContactStartIn, user=Depends(current_user)):
    field = body.field
    value = (body.value or '').strip()
    if field == 'email':
        value = value.lower()
        if not _EMAIL_RE.match(value):
            raise HTTPException(400, 'Please enter a valid email address.')
    elif not value:
        raise HTTPException(400, 'Please enter a valid phone number.')
    if await _contact_value_taken(field, value, user['id']):
        raise HTTPException(409, f'That {field} is already in use by another account.')

    await _enforce_rate_limit(value)
    # Single pending change per user — a new start supersedes the old one.
    await db.pending_contact_changes.delete_many({'user_id': user['id']})

    code = otp_service.generate_code()
    doc = {
        'id': str(uuid.uuid4()),
        'user_id': user['id'],
        'field': field,
        'value': value,
        'channel': field,   # email -> email channel, phone -> sms channel
        'otp_hash': pwd_ctx.hash(code),
        'attempts': 0,
        'created_at': now(),
        'expires_at': now() + timedelta(minutes=otp_service.OTP_TTL_MIN),
    }
    # Deliver first — if the send fails we raise and persist nothing.
    await _deliver_otp(
        field,
        value,
        code,
        user_name=user.get('full_name') or '',
        purpose='contact_change',
    )
    await db.pending_contact_changes.insert_one(doc)
    return {'field': field, 'value': value, 'channel': field,
            'expires_in': otp_service.OTP_TTL_MIN * 60}

@api.post('/auth/change-contact/verify')
async def change_contact_verify(body: ChangeContactVerifyIn, user=Depends(current_user)):
    pending = await db.pending_contact_changes.find_one({'user_id': user['id']})
    if not pending:
        raise HTTPException(404, 'No pending contact change. Please start again.')
    if pending['expires_at'] < now():
        await db.pending_contact_changes.delete_one({'id': pending['id']})
        raise HTTPException(400, 'Your verification code expired. Please start again.')
    if pending.get('attempts', 0) >= OTP_MAX_VERIFY_ATTEMPTS:
        await db.pending_contact_changes.delete_one({'id': pending['id']})
        raise HTTPException(429, 'Too many incorrect attempts. Please start again.')
    if not _otp_matches(body.code, pending.get('otp_hash')):
        await db.pending_contact_changes.update_one({'id': pending['id']}, {'$inc': {'attempts': 1}})
        raise HTTPException(400, 'Incorrect verification code')

    field, value = pending['field'], pending['value']
    # Re-check uniqueness at commit time (another account may have taken it since).
    if await _contact_value_taken(field, value, user['id']):
        await db.pending_contact_changes.delete_one({'id': pending['id']})
        raise HTTPException(409, f'That {field} is already in use by another account.')

    await db.users.update_one(
        {'id': user['id']},
        {'$set': {field: value, f'{field}_verified': True}})
    await db.pending_contact_changes.delete_one({'id': pending['id']})
    await write_audit(user, 'contact.change', f'Changed {field} to {value}',
                      target_id=user['id'], field=field)
    fresh = await db.users.find_one(
        {'id': user['id']}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
    return {'ok': True, 'field': field, 'value': value, 'user': serialize(fresh)}

# ── Trials ───────────────────────────────────────────────────────────────────
def _protocol_details(doc: dict) -> dict:
    indications = doc.get('indications') or []
    if not indications and doc.get('condition'):
        indications = [part.strip() for part in doc['condition'].split(',')
                       if part.strip()]
    return {
        'ctri_number': doc.get('ctri_number') or '',
        'title': doc.get('title') or '',
        'phase': doc.get('phase') or '',
        'indications': indications,
        'drug': doc.get('drug') or '',
        'duration': doc.get('duration') or '',
        'target_enrollment': doc.get('target_enrollment'),
        'total_visits': doc.get('total_visits'),
        'status': doc.get('status') or 'active',
    }


@api.get('/protocols/lookup/{protocol_id}',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def lookup_protocol(protocol_id: str, user=Depends(current_user)):
    """Resolve a protocol without exposing another sponsor's private studies."""
    key = protocol_id.strip()
    if not key:
        raise HTTPException(400, 'Protocol ID is required')
    registry = await db.protocol_registry.find_one(
        {'protocol_id': {'$regex': f'^{re.escape(key)}$', '$options': 'i'}},
        {'_id': 0})
    if registry:
        return {'found': True, 'protocol_id': registry.get('protocol_id', key),
                'source': 'registry', 'details': _protocol_details(registry)}

    candidates = await db.trials.find(
        {'protocol_id': {'$regex': f'^{re.escape(key)}$', '$options': 'i'}},
        {'_id': 0}).to_list(50)
    for trial in candidates:
        if await _can_access_trial(user, trial):
            details = _protocol_details(trial)
            if details['total_visits'] is None:
                details['total_visits'] = await db.visits.count_documents(
                    {'trial_id': trial['id']})
            return {'found': True, 'protocol_id': trial['protocol_id'],
                    'source': 'organization', 'details': details}
    return {'found': False, 'protocol_id': key, 'source': None, 'details': None}


@api.get('/protocols/lookup',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def lookup_protocol_query(
    protocol_id: str = Query(..., min_length=1),
    user=Depends(current_user),
):
    """Query-parameter form used by the Add Trial UI.

    Keep the original path-parameter endpoint for backwards compatibility;
    both forms deliberately execute the same scoped lookup.
    """
    return await lookup_protocol(protocol_id, user)


@api.post('/protocols/extract-details',
          dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def extract_protocol_details(file: UploadFile = File(...),
                                   user=Depends(current_user)):
    content_type = (file.content_type or '').lower()
    filename = (file.filename or '').lower()
    if content_type != 'application/pdf' and not filename.endswith('.pdf'):
        raise HTTPException(400, 'Upload a PDF protocol document')
    data = await file.read(pe.MAX_PDF_BYTES + 1)
    if not data:
        raise HTTPException(400, 'The uploaded PDF is empty')
    if len(data) > pe.MAX_PDF_BYTES:
        raise HTTPException(413, 'Protocol PDF is too large (maximum 25 MB)')
    try:
        extracted = await pe.get_details_extractor().extract_details(data)
    except pe.ExtractionNotConfigured as exc:
        raise HTTPException(
            503, f'Protocol extraction is not configured on the server: {exc}')
    except pe.ExtractionUnavailable as exc:
        raise HTTPException(503, f'Protocol extraction is temporarily unavailable: {exc}')
    except pe.ExtractionError as exc:
        raise HTTPException(502, f'Could not extract protocol details: {exc}')
    details = extracted.dict()
    details['status'] = (
        details.get('status') if details.get('status') in
        ('active', 'completed', 'terminated') else 'active')
    await write_audit(
        user, 'trial.extract_details',
        f'Extracted creation details from {file.filename or "protocol PDF"}')
    return {'details': details}


@api.post('/protocols/extract',
          dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def extract_protocol_alias(file: UploadFile = File(...),
                                 schedule_option_id: Optional[str] = Form(None),
                                 user=Depends(current_user)):
    """Extract trial details and an audited schedule from one protocol analysis.

    Every schedule is cached briefly and consumed after the trial is
    created, so the Visit Schedule screen does not upload/analyse the same
    PDF again.

    When the protocol prints more than one independent Schedule of
    Assessments (e.g. separate substudies), every one of them is now
    extracted automatically and returned together as ``extractions`` — no
    reviewer pick-then-re-upload round trip. ``schedule_option_id`` is
    still accepted for backward compatibility but is no longer needed.
    """
    content_type = (file.content_type or '').lower()
    filename = (file.filename or '').lower()
    if content_type != 'application/pdf' and not filename.endswith('.pdf'):
        raise HTTPException(400, 'Upload a PDF protocol document')
    data = await file.read(pe.MAX_PDF_BYTES + 1)
    if not data:
        raise HTTPException(400, 'The uploaded PDF is empty')
    if len(data) > pe.MAX_PDF_BYTES:
        raise HTTPException(413, 'Protocol PDF is too large (maximum 25 MB)')
    try:
        extracted_details, results = await pe.extract_protocol_bundle_all(data)
    except pe.ExtractionNotConfigured as exc:
        raise HTTPException(
            503, f'Protocol extraction is not configured on the server: {exc}')
    except pe.ExtractionUnavailable as exc:
        raise HTTPException(503, f'Protocol extraction is temporarily unavailable: {exc}')
    except pe.ExtractionError as exc:
        raise HTTPException(502, f'Could not analyse protocol: {exc}')

    details = extracted_details.model_dump()
    details['status'] = (
        details.get('status') if details.get('status') in
        ('active', 'completed', 'terminated') else 'active')
    created_at = now()
    await db.protocol_extractions.delete_many({'expires_at': {'$lte': created_at}})
    extractions = []
    total_visits = 0
    for option, schedule in results:
        extraction_id = str(uuid.uuid4())
        await db.protocol_extractions.insert_one({
            'id': extraction_id,
            'user_id': user['id'],
            'file_name': file.filename or 'protocol.pdf',
            'details': details,
            'schedule': schedule.model_dump(mode='json'),
            'option_id': option.id if option else '',
            'option_label': option.label if option else '',
            'option_description': option.description if option else '',
            'created_at': created_at,
            'expires_at': created_at + timedelta(hours=2),
        })
        total_visits += len(schedule.visits)
        extractions.append({
            'extraction_id': extraction_id,
            'option_id': option.id if option else '',
            'option_label': option.label if option else '',
            'option_description': option.description if option else '',
            'schedule_visit_count': len(schedule.visits),
            'verification': {
                'status': schedule.verification_status,
                'confidence': schedule.verification_confidence,
                'refinement_count': schedule.verification_iterations,
                'issues': schedule.verification_issues,
                'accuracy': schedule.verification_scores,
            },
        })
    await write_audit(
        user, 'trial.extract_protocol',
        f'Extracted trial details and {total_visits} schedule visit(s) across '
        f'{len(extractions)} schedule(s) from {file.filename or "protocol PDF"}')

    if len(extractions) == 1 and results[0][0] is None:
        # Backward-compatible shape for the overwhelming majority of
        # single-schedule uploads.
        only = extractions[0]
        return {
            'details': details,
            'extraction_id': only['extraction_id'],
            'schedule_visit_count': only['schedule_visit_count'],
            'needs_schedule_selection': False,
            'verification': only['verification'],
        }
    return {
        'details': details,
        'extractions': extractions,
        'needs_schedule_selection': False,
    }


@api.get('/trials')
async def list_trials(user=Depends(current_user)):
    trials = await db.trials.find({}, {'_id': 0}).to_list(500)
    # Every non-platform trial list is tenant/relationship scoped. A crafted
    # client must never turn this convenient list endpoint into a global trial
    # directory.
    scoped = []
    for trial in trials:
        if await _can_access_trial(user, trial):
            scoped.append(trial)
    trials = scoped

    # Batched enrolment counts — one grouped query over db.patients (no per-trial
    # N+1). enrolled_count is an aggregate (not patient PII), fine for sponsors.
    trial_ids = [t['id'] for t in trials]
    counts: Dict[str, int] = {}
    site_names: Dict[str, set] = {}
    patient_rows = []
    if trial_ids:
        patient_rows = await db.patients.find(
            {'trial_id': {'$in': trial_ids}},
            {'_id': 0, 'trial_id': 1, 'pi_id': 1, 'crc_id': 1}
        ).to_list(5000)
        for patient in patient_rows:
            tid = patient['trial_id']
            counts[tid] = counts.get(tid, 0) + 1

    creator_ids = {t.get('created_by') for t in trials if t.get('created_by')}
    staff_ids = {
        staff_id for patient in patient_rows
        for staff_id in (patient.get('pi_id'), patient.get('crc_id'))
        if staff_id
    }
    people_ids = list(creator_ids | staff_ids)
    people_rows = await db.users.find(
        {'id': {'$in': people_ids}} if people_ids else {'id': {'$in': []}},
        {'_id': 0, 'id': 1, 'full_name': 1, 'role': 1, 'organization': 1}
    ).to_list(5000)
    people = {row['id']: row for row in people_rows}
    for patient in patient_rows:
        tid = patient['trial_id']
        for staff_id in (patient.get('pi_id'), patient.get('crc_id')):
            organization = (people.get(staff_id) or {}).get('organization')
            if organization:
                site_names.setdefault(tid, set()).add(organization)

    for t in trials:
        t['enrolled_count'] = counts.get(t['id'], 0)
        # target_enrollment is NOT captured at trial creation (POST /trials is frozen
        # and TrialIn has no target field; the seed doesn't set one either). Surface
        # it only when a trial doc genuinely carries it, else null — never fabricate
        # a target. Keying it explicitly makes the null obvious and consistent.
        t['target_enrollment'] = t.get('target_enrollment')
        creator = people.get(t.get('created_by')) or {}
        t['created_by_name'] = creator.get('full_name') or ''
        t['created_by_role'] = creator.get('role') or ''
        t['site_names'] = sorted(site_names.get(t['id'], set()))
        t['site_count'] = len(t['site_names'])
        # schedule_status (approved/flagged/…) is already on `t` when stored — the
        # find() above returns the full doc, so we neither add nor fabricate it.
    return trials

@api.post('/trials', dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'smo', 'site'))])
async def create_trial(body: TrialIn, user=Depends(current_user)):
    tid = str(uuid.uuid4())
    values = body.model_dump()
    # Sponsor/CRO ownership is derived from the authenticated account, never
    # from caller-controlled JSON.
    if user['role'] in ('sponsor', 'cro'):
        organization = (user.get('organization') or '').strip()
        if not organization:
            raise HTTPException(400, 'Your account is not linked to an organization')
        values['sponsor_name'] = organization
    if user['role'] in ('smo', 'site'):
        organization_name = (user.get('organization') or '').strip()
        if not organization_name:
            raise HTTPException(400, 'Your account is not linked to an organization')
        if not user.get('org_admin'):
            raise HTTPException(
                403, 'Only the organization administrator can create delegated trials')
        organization = await db.organizations.find_one(
            {'name': organization_name}, {'_id': 0})
        if not organization or organization.get('type') not in ('smo', 'site'):
            raise HTTPException(403, 'A valid Site or SMO organization is required')
        if not organization.get('trial_creation_delegated'):
            raise HTTPException(
                403, 'Active platform delegation is required before creating a trial')
        values['owning_organization_id'] = organization['id']
        values['owning_organization_name'] = organization['name']
        values['created_under_delegation_request_id'] = (
            organization.get('trial_creation_delegation_request_id'))
    values['status'] = values.get('status') or 'active'
    doc = {'id': tid, **values, 'created_by': user['id'], 'created_at': now()}
    await db.trials.insert_one(doc)
    await write_audit(user, 'trial.create',
                      f"Created trial {doc.get('protocol_id') or tid}",
                      target_id=tid, trial_id=tid)
    return serialize(doc)

@api.get('/trials/{trial_id}')
async def get_trial(trial_id: str, user=Depends(current_user)):
    t = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not t: raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, t):
        raise HTTPException(403, 'You do not have access to this trial')
    visits = await db.visits.find({'trial_id': trial_id}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    return {**t, 'visits': visits}

@api.patch('/trials/{trial_id}')
async def patch_trial(trial_id: str, body: TrialPatchIn,
                      user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    if user['role'] in ('sponsor', 'cro'):
        if (trial.get('sponsor_name') or '').strip().casefold() != (
                user.get('organization') or '').strip().casefold():
            raise HTTPException(403, 'Only the owning Sponsor/CRO can edit this trial')
    elif trial.get('created_by') != user['id']:
        raise HTTPException(403, 'Only the trial creator can edit this trial')

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(400, 'No trial fields were provided')
    updates.update({
        'updated_at': now(),
        'updated_by': user['id'],
        'updated_by_name': user.get('full_name') or '',
    })
    await db.trials.update_one({'id': trial_id}, {'$set': updates})
    await write_audit(
        user, 'trial.update',
        f"Updated trial {trial.get('protocol_id') or trial_id}",
        target_id=trial_id, trial_id=trial_id,
        changes={key: value for key, value in updates.items()
                 if key not in ('updated_at', 'updated_by', 'updated_by_name')},
    )
    fresh = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    return serialize(fresh)


@api.get('/sponsor/dashboard',
         dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_dashboard(user=Depends(current_user)):
    """One de-identified, organization-scoped payload for the Sponsor/CRO app.

    Patient names/contact fields never leave this endpoint. Site performance is
    derived from aggregate enrolment and visit-instance status only.
    """
    all_trials = await db.trials.find({}, {'_id': 0}).to_list(500)
    trials = [trial for trial in all_trials
              if await _can_access_trial(user, trial)]
    trial_ids = [trial['id'] for trial in trials]
    trial_by_id = {trial['id']: trial for trial in trials}

    patients = []
    if trial_ids:
        patients = await db.patients.find(
            {'trial_id': {'$in': trial_ids}},
            {'_id': 0, 'id': 1, 'trial_id': 1, 'pi_id': 1, 'crc_id': 1,
             'created_by': 1, 'status': 1}).to_list(5000)

    counts: Dict[str, int] = {}
    randomized_counts: Dict[str, int] = {}
    trial_recruitment: Dict[str, dict] = {}
    recruitment = {
        'screened': 0,
        'screen_fail': 0,
        'randomized': 0,
        'active': 0,
        'withdrawn': 0,
        'dropout': 0,
        'follow_up': 0,
        'completed': 0,
    }
    for patient in patients:
        trial_id = patient['trial_id']
        counts[trial_id] = counts.get(trial_id, 0) + 1
        bucket = _recruitment_bucket(patient.get('status'))
        funnel = trial_recruitment.setdefault(trial_id, {
            'screened': 0, 'screen_fail': 0, 'randomized': 0,
            'active': 0, 'withdrawn': 0, 'dropout': 0,
            'follow_up': 0, 'completed': 0,
        })
        funnel['screened'] += 1
        if bucket in funnel and bucket != 'screened':
            funnel[bucket] += 1
        elif bucket not in ('screen_fail', 'withdrawn', 'dropout', 'completed'):
            funnel['active'] += 1
        if bucket in recruitment:
            recruitment[bucket] += 1
        else:
            recruitment['active'] += 1
        if bucket == 'randomized':
            randomized_counts[patient['trial_id']] = (
                randomized_counts.get(patient['trial_id'], 0) + 1)

    trial_cards = []
    for trial in trials:
        enrolled = counts.get(trial['id'], 0)
        target = trial.get('target_enrollment')
        trial_cards.append({
            'id': trial['id'],
            'protocol_id': trial.get('protocol_id') or trial['id'][:8],
            'title': trial.get('title') or 'Untitled trial',
            'phase': trial.get('phase') or '',
            'condition': trial.get('condition') or '',
            'drug': trial.get('drug') or '',
            'status': trial.get('status') or 'active',
            'recruitment_status': trial.get('recruitment_status') or '',
            'enrolled_count': enrolled,
            'randomized_count': randomized_counts.get(trial['id'], 0),
            'recruitment': trial_recruitment.get(trial['id'], {
                'screened': 0, 'screen_fail': 0, 'randomized': 0,
                'active': 0, 'withdrawn': 0, 'dropout': 0,
                'follow_up': 0, 'completed': 0,
            }),
            'target_enrollment': target,
            'site_count': 0,
            'created_by': trial.get('created_by'),
            'created_at': iso(trial.get('created_at')),
        })

    # Resolve each enrolled subject to a site through its assigned PI/CRC. Only
    # staff-facing professional contact data is used; subject PII is excluded.
    staff_ids = {
        staff_id for patient in patients
        for staff_id in (patient.get('pi_id'), patient.get('crc_id'))
        if staff_id
    }
    staff_ids.update(
        trial.get('created_by') for trial in trials if trial.get('created_by'))
    staff_rows = await db.users.find(
        {'id': {'$in': list(staff_ids)}} if staff_ids else {'id': {'$in': []}},
        {'_id': 0, 'id': 1, 'full_name': 1, 'email': 1, 'phone': 1,
         'organization': 1, 'role': 1, 'profile.department': 1}).to_list(2000)
    staff = {row['id']: row for row in staff_rows}
    for card in trial_cards:
        creator = staff.get(card.get('created_by')) or {}
        card['created_by_name'] = creator.get('full_name') or ''
        card['created_by_role'] = creator.get('role') or ''

    site_groups: Dict[str, dict] = {}
    patient_site: Dict[str, str] = {}
    for patient in patients:
        pi = staff.get(patient.get('pi_id')) or {}
        crc = staff.get(patient.get('crc_id')) or {}
        site_name = (pi.get('organization') or crc.get('organization') or
                     'Unassigned site')
        group = site_groups.setdefault(site_name, {
            'patient_ids': [], 'trial_ids': set(), 'pi': pi, 'crc': crc})
        group['patient_ids'].append(patient['id'])
        group['trial_ids'].add(patient['trial_id'])
        if pi and not group.get('pi'):
            group['pi'] = pi
        if crc and not group.get('crc'):
            group['crc'] = crc
        patient_site[patient['id']] = site_name

    # Include organization-network sites even before their first enrolment.
    org_name = (user.get('organization') or '').strip()
    organization = await db.organizations.find_one(
        {'name': org_name}, {'_id': 0}) if org_name else None
    network_sites = []
    if organization:
        network_sites = await db.org_sites.find(
            {'org_id': organization['id']}, {'_id': 0}).sort('name', 1).to_list(500)
        site_pi_ids = [site.get('user_id') for site in network_sites
                       if site.get('user_id')]
        site_pi_emails = [str(site.get('pi_email') or '').lower().strip()
                          for site in network_sites if site.get('pi_email')]
        site_pi_filters = []
        if site_pi_ids:
            site_pi_filters.append({'id': {'$in': site_pi_ids}})
        if site_pi_emails:
            site_pi_filters.append({'email': {'$in': site_pi_emails}})
        site_pi_rows = await db.users.find(
            {'$or': site_pi_filters} if site_pi_filters else {'id': {'$in': []}},
            {'_id': 0, 'id': 1, 'full_name': 1, 'email': 1, 'phone': 1,
             'organization': 1, 'role': 1, 'profile.department': 1},
        ).to_list(1000)
        site_pis_by_id = {row['id']: row for row in site_pi_rows}
        site_pis_by_email = {
            str(row.get('email') or '').lower(): row for row in site_pi_rows
            if row.get('email')
        }
        linked_pi_orgs = sorted({
            str(row.get('organization') or '').strip()
            for row in site_pi_rows if row.get('organization')
        })
        organization_pi_rows = await db.users.find(
            {
                'role': 'pi',
                'organization': {'$in': linked_pi_orgs},
            } if linked_pi_orgs else {'id': {'$in': []}},
            {'_id': 0, 'id': 1, 'full_name': 1, 'email': 1, 'phone': 1,
             'organization': 1, 'role': 1, 'profile.department': 1},
        ).to_list(2000)
        site_pis_by_org: Dict[str, List[dict]] = {}
        for pi_row in organization_pi_rows:
            site_pis_by_org.setdefault(pi_row.get('organization') or '', []).append(pi_row)
        for site in network_sites:
            group = site_groups.setdefault(site.get('name') or 'Unnamed site', {
                'patient_ids': [], 'trial_ids': set(), 'pi': {}, 'crc': {}})
            group['id'] = site.get('id')
            group['address'] = site.get('address') or ''
            group['city'] = site.get('city') or ''
            group['state'] = site.get('state') or ''
            group['hospital_type'] = site.get('hospital_type') or ''
            group['department'] = site.get('department') or ''
            group['access_type'] = site.get('access_type') or 'full'
            group['status'] = site.get('status') or 'active'
            group['trial_targets'] = site.get('trial_targets') or {}
            group['trial_ids'].update(site.get('trial_ids') or [])
            linked_pi = (
                site_pis_by_id.get(site.get('user_id')) or
                site_pis_by_email.get(str(site.get('pi_email') or '').lower())
            )
            if linked_pi:
                group['pi'] = linked_pi
                related_pis = [linked_pi, *site_pis_by_org.get(
                    linked_pi.get('organization') or '', [])]
                deduplicated_pis = []
                seen_pi_ids = set()
                for related_pi in related_pis:
                    identity = related_pi.get('id') or str(
                        related_pi.get('email') or '').strip().lower()
                    if identity and identity not in seen_pi_ids:
                        seen_pi_ids.add(identity)
                        deduplicated_pis.append(related_pi)
                group['pis'] = deduplicated_pis
            elif site.get('pi_name'):
                configured_pi = {
                    'full_name': site.get('pi_name') or '',
                    'email': site.get('pi_email') or '',
                    'phone': site.get('pi_phone') or '',
                    'role': 'pi',
                    'organization': site.get('name') or '',
                    'profile': {'department': site.get('department') or ''},
                }
                group['pi'] = configured_pi
                group['pis'] = [configured_pi]

    instances = []
    patient_ids = [patient['id'] for patient in patients]
    if patient_ids:
        instances = await db.visit_instances.find(
            {'patient_id': {'$in': patient_ids}},
            {'_id': 0, 'patient_id': 1, 'status': 1}).to_list(20000)
    instance_stats: Dict[str, dict] = {}
    for instance in instances:
        site_name = patient_site.get(instance.get('patient_id'))
        if not site_name:
            continue
        stats = instance_stats.setdefault(site_name, {'total': 0, 'completed': 0, 'overdue': 0})
        stats['total'] += 1
        status = (instance.get('status') or '').lower()
        if status == 'completed':
            stats['completed'] += 1
        if status == 'overdue':
            stats['overdue'] += 1

    # Aggregate de-identified dose outcomes in one bounded portfolio query.
    # This keeps the dashboard composite honest without exposing medication or
    # patient details and avoids one adherence query per enrolled subject.
    dose_logs = await db.dose_logs.find(
        {'patient_id': {'$in': patient_ids}} if patient_ids else
        {'patient_id': {'$in': []}},
        {'_id': 0, 'patient_id': 1, 'status': 1},
    ).to_list(50000)
    dose_stats: Dict[str, dict] = {}
    for log in dose_logs:
        stats = dose_stats.setdefault(
            log.get('patient_id'), {'total': 0, 'taken': 0})
        stats['total'] += 1
        if (log.get('status') or '').lower() == 'taken':
            stats['taken'] += 1

    site_cards = []
    for index, (site_name, group) in enumerate(sorted(site_groups.items())):
        linked_trials = [trial_by_id[trial_id] for trial_id in group['trial_ids']
                         if trial_id in trial_by_id]
        linked_statuses = {
            (trial.get('status') or 'active').lower()
            for trial in linked_trials
        }
        stored_status = (group.get('status') or '').lower()
        site_status = (
            'completed' if linked_statuses == {'completed'} else
            'terminated' if linked_statuses == {'terminated'} else
            stored_status if stored_status in {'active', 'completed', 'terminated'} else
            'active'
        )
        enrolled = len(group['patient_ids'])
        group_patient_ids = set(group['patient_ids'])
        site_funnel = {
            'screened': 0, 'screen_fail': 0, 'randomized': 0, 'active': 0,
            'withdrawn': 0, 'dropout': 0, 'follow_up': 0, 'completed': 0,
        }
        for patient in patients:
            if patient.get('id') not in group_patient_ids:
                continue
            bucket = _recruitment_bucket(patient.get('status'))
            site_funnel['screened'] += 1
            if bucket in site_funnel and bucket != 'screened':
                site_funnel[bucket] += 1
            elif bucket not in (
                'screen_fail', 'withdrawn', 'dropout', 'completed',
            ):
                site_funnel['active'] += 1
        trial_targets = group.get('trial_targets') or {}
        target = sum(
            trial_targets.get(trial['id'], trial.get('target_enrollment') or 0)
            for trial in linked_trials
        )
        stats = instance_stats.get(site_name, {'total': 0, 'completed': 0, 'overdue': 0})
        compliance = round((stats['completed'] / stats['total']) * 100) if stats['total'] else 100
        site_dose_total = sum(
            dose_stats.get(patient_id, {}).get('total', 0)
            for patient_id in group['patient_ids'])
        site_dose_taken = sum(
            dose_stats.get(patient_id, {}).get('taken', 0)
            for patient_id in group['patient_ids'])
        adherence = round((site_dose_taken / site_dose_total) * 100) if site_dose_total else 100
        enrollment_pct = round((enrolled / target) * 100) if target else 0
        performance_score = round((
            min(100, enrollment_pct) + compliance + adherence
        ) / 3)
        pi, crc = group.get('pi') or {}, group.get('crc') or {}
        site_pis = group.get('pis') or ([pi] if pi else [])
        site_cards.append({
            'id': group.get('id') or (f"pi-{pi['id']}" if pi.get('id') else f'site-{index + 1}'),
            'name': site_name,
            'hospital': site_name,
            'address': group.get('address') or '',
            'city': group.get('city') or '',
            'state': group.get('state') or '',
            'hospital_type': group.get('hospital_type') or '',
            'department': ((pi.get('profile') or {}).get('department') or
                           group.get('department') or ''),
            'access_type': group.get('access_type') or 'full',
            'status': site_status,
            'pi_name': pi.get('full_name') or '',
            'pi_id': pi.get('id') or '',
            'pi_email': pi.get('email') or '',
            'pi_phone': pi.get('phone') or '',
            'pis': [{
                'id': site_pi.get('id') or '',
                'name': site_pi.get('full_name') or '',
                'email': site_pi.get('email') or '',
                'phone': site_pi.get('phone') or '',
                'department': ((site_pi.get('profile') or {}).get('department') or ''),
            } for site_pi in site_pis if site_pi.get('full_name')],
            'crc_name': crc.get('full_name') or '',
            'enrolled': enrolled,
            'target_enrollment': target or None,
            'enrollment_pct': enrollment_pct,
            'visit_compliance': compliance,
            'adherence_pct': adherence,
            'performance_score': performance_score,
            'overdue_visits': stats['overdue'],
            'recruitment': site_funnel,
            'trials': [{
                'id': trial['id'],
                'protocol_id': trial.get('protocol_id') or trial['id'][:8],
                'title': trial.get('title') or 'Untitled trial',
                'phase': trial.get('phase') or '',
                'condition': trial.get('condition') or '',
                'drug': trial.get('drug') or '',
                'status': trial.get('status') or 'active',
                'recruitment_status': trial.get('recruitment_status') or '',
                'pi_name': pi.get('full_name') or '',
                'department': ((pi.get('profile') or {}).get('department') or
                               group.get('department') or ''),
            } for trial in linked_trials],
        })

    site_count_for_trial: Dict[str, int] = {}
    for site in site_cards:
        for trial in site['trials']:
            site_count_for_trial[trial['id']] = site_count_for_trial.get(trial['id'], 0) + 1
    for card in trial_cards:
        card['site_count'] = site_count_for_trial.get(card['id'], 0)

    notifications = await db.notifications.find(
        {'user_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(5)
    alerts = sum(1 for notification in notifications if not notification.get('read'))
    alerts += sum(site.get('overdue_visits', 0) for site in site_cards)
    active_trials = sum(1 for trial in trials
                        if (trial.get('status') or 'active').lower() == 'active')
    enrolled = len(patients)
    target = sum(int(trial.get('target_enrollment') or 0) for trial in trials)
    enrollment_pct = round((enrolled / target) * 100) if target else 0
    visit_total = sum(stats['total'] for stats in instance_stats.values())
    visits_completed = sum(stats['completed'] for stats in instance_stats.values())
    compliance_pct = round((visits_completed / visit_total) * 100) if visit_total else 100
    dose_total = sum(stats['total'] for stats in dose_stats.values())
    doses_taken = sum(stats['taken'] for stats in dose_stats.values())
    adherence_pct = round((doses_taken / dose_total) * 100) if dose_total else 100
    health_score = round((
        min(100, enrollment_pct) + compliance_pct + adherence_pct
    ) / 3) if trials else 0

    pi_keys = {
        site.get('pi_id') or (site.get('pi_email') or '').lower()
        for site in site_cards
        if site.get('pi_id') or site.get('pi_email')
    }
    return serialize({
        'portfolio': {
            'health_score': health_score,
            'status': (
                'no_portfolio_data' if not trials else
                'on_track' if health_score >= 75 else
                'steady' if health_score >= 60 else
                'needs_attention'
            ),
            'active_trials': active_trials,
            'alerts': alerts,
            'enrolled': enrolled,
            'target': target,
            'enrollment_pct': enrollment_pct,
            'compliance_pct': compliance_pct,
            'adherence_pct': adherence_pct,
            'recruitment': recruitment,
        },
        'totals': {
            'trials': len(trials),
            'sites': len(site_cards),
            'subjects': len(patients),
            'pis': len(pi_keys),
        },
        'trials': trial_cards,
        'sites': site_cards,
        'recent_notifications': notifications,
        'capabilities': {
            'can_add_trial': True,
            'can_add_site': bool(user.get('org_admin')),
            'can_share_schedule': True,
            'can_manage_organization': bool(user.get('org_admin')),
        },
    })


def _recruitment_bucket(status_value: Optional[str]) -> str:
    status_key = (status_value or 'active').strip().lower().replace('-', '_').replace(' ', '_')
    aliases = {
        'screen_failure': 'screen_fail',
        'screenfailed': 'screen_fail',
        'followup': 'follow_up',
        'in_follow_up': 'follow_up',
        'drop_out': 'dropout',
    }
    return aliases.get(status_key, status_key)


async def _sponsor_trial_detail_payload(trial: dict, user: dict) -> dict:
    """Build the sponsor/CRO detail contract from scoped aggregate data.

    Subjects are represented only by study identifiers/initials. Names,
    contact details, DOB and other direct patient identifiers are never added.
    """
    trial_id = trial['id']
    patients = await db.patients.find(
        {'trial_id': trial_id},
        {'_id': 0, 'id': 1, 'subject_id': 1, 'avatar_initials': 1,
         'pi_id': 1, 'crc_id': 1, 'created_by': 1, 'status': 1,
         'enrolled_date': 1, 'created_at': 1},
    ).to_list(5000)
    staff_ids = {
        staff_id for patient in patients
        for staff_id in (patient.get('pi_id'), patient.get('crc_id'), patient.get('created_by'))
        if staff_id
    }
    staff_rows = await db.users.find(
        {'id': {'$in': list(staff_ids)}} if staff_ids else {'id': {'$in': []}},
        {'_id': 0, 'id': 1, 'full_name': 1, 'email': 1, 'phone': 1,
         'organization': 1, 'role': 1, 'profile.designation': 1},
    ).to_list(2000)
    staff = {row['id']: row for row in staff_rows}

    org_name = (user.get('organization') or '').strip()
    organization = await db.organizations.find_one(
        {'name': org_name}, {'_id': 0, 'id': 1}) if org_name else None
    stored_sites = await db.org_sites.find(
        {'org_id': organization['id'], 'trial_ids': trial_id},
        {'_id': 0},
    ).sort('name', 1).to_list(500) if organization else []

    site_groups: Dict[str, dict] = {}
    for site in stored_sites:
        site_groups[site.get('name') or 'Unnamed site'] = {
            **site, 'patients': [], 'staff': [],
        }
    for patient in patients:
        pi = staff.get(patient.get('pi_id')) or {}
        crc = staff.get(patient.get('crc_id')) or {}
        creator = staff.get(patient.get('created_by')) or {}
        site_name = (
            pi.get('organization') or crc.get('organization')
            or creator.get('organization') or 'Unassigned site'
        )
        group = site_groups.setdefault(site_name, {
            'id': f"site-{len(site_groups) + 1}",
            'name': site_name,
            'address': '', 'city': '', 'state': '', 'department': '',
            'hospital_type': '', 'trial_targets': {}, 'patients': [], 'staff': [],
        })
        group['patients'].append(patient)
        for person in (pi, crc):
            if person and person.get('id') and all(
                current.get('id') != person['id'] for current in group['staff']
            ):
                group['staff'].append(person)

    patient_ids = [patient['id'] for patient in patients]
    visit_instances = await db.visit_instances.find(
        {'patient_id': {'$in': patient_ids}} if patient_ids else
        {'patient_id': {'$in': []}},
        {
            '_id': 0, 'id': 1, 'patient_id': 1, 'status': 1,
            'visit_number': 1, 'seq': 1, 'name': 1, 'visit_type': 1,
            'scheduled_date': 1, 'window_start': 1, 'window_end': 1,
        },
    ).to_list(20000)
    completed_visits: Dict[str, int] = {}
    current_visits: Dict[str, dict] = {}
    site_visit_stats: Dict[str, dict] = {}

    def visit_sort_timestamp(value) -> float:
        if not value:
            return float('inf')
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
                str(value).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError):
            return float('inf')

    patient_site: Dict[str, str] = {}
    for site_name, group in site_groups.items():
        for patient in group['patients']:
            patient_site[patient['id']] = site_name
    for instance in visit_instances:
        patient_id = instance.get('patient_id')
        site_name = patient_site.get(patient_id)
        if not site_name:
            continue
        stats = site_visit_stats.setdefault(
            site_name, {'total': 0, 'completed': 0, 'overdue': 0})
        stats['total'] += 1
        instance_status = (instance.get('status') or '').lower()
        if instance_status == 'completed':
            stats['completed'] += 1
            completed_visits[patient_id] = completed_visits.get(patient_id, 0) + 1
        if instance_status == 'overdue':
            stats['overdue'] += 1
        effective_status = _effective_visit_status(instance)
        if effective_status not in VISIT_QUEUE_TERMINAL_STATUSES:
            candidate = {**instance, 'status': effective_status}
            existing = current_visits.get(patient_id)
            candidate_rank = 0 if effective_status == 'overdue' else 1
            existing_rank = 0 if existing and existing.get('status') == 'overdue' else 1
            candidate_date = visit_sort_timestamp(candidate.get('scheduled_date'))
            existing_date = visit_sort_timestamp((existing or {}).get('scheduled_date'))
            if existing is None or (candidate_rank, candidate_date) < (existing_rank, existing_date):
                current_visits[patient_id] = candidate

    funnel_keys = (
        'screened', 'screen_fail', 'randomized', 'active', 'withdrawn',
        'dropout', 'follow_up', 'completed',
    )
    recruitment = {key: 0 for key in funnel_keys}
    subjects = []
    for index, patient in enumerate(patients):
        bucket = _recruitment_bucket(patient.get('status'))
        recruitment['screened'] += 1
        if bucket in recruitment and bucket != 'screened':
            recruitment[bucket] += 1
        elif bucket not in ('screen_fail', 'withdrawn', 'dropout', 'completed'):
            recruitment['active'] += 1
        site_name = patient_site.get(patient['id'], 'Unassigned site')
        current_visit = current_visits.get(patient['id'])
        subjects.append({
            'id': patient['id'],
            'subject_id': patient.get('subject_id') or f"SUBJ-{index + 1:03d}",
            'initials': patient.get('avatar_initials') or '',
            'site': site_name,
            'status': patient.get('status') or 'active',
            'enrolled_at': iso(patient.get('enrolled_date') or patient.get('created_at')),
            'visits_completed': completed_visits.get(patient['id'], 0),
            'current_visit': {
                'id': current_visit.get('id'),
                'visit_number': current_visit.get('visit_number') or current_visit.get('seq'),
                'name': current_visit.get('name') or 'Visit',
                'status': current_visit.get('status') or 'scheduled',
                'visit_type': current_visit.get('visit_type') or '',
                'scheduled_date': iso(current_visit.get('scheduled_date')),
                'window_start': iso(current_visit.get('window_start')),
                'window_end': iso(current_visit.get('window_end')),
            } if current_visit else None,
            'deidentified': True,
        })

    sites = []
    team_by_id: Dict[str, dict] = {}
    for site_name, group in site_groups.items():
        site_patients = group['patients']
        site_funnel = {key: 0 for key in funnel_keys}
        for patient in site_patients:
            bucket = _recruitment_bucket(patient.get('status'))
            site_funnel['screened'] += 1
            if bucket in site_funnel and bucket != 'screened':
                site_funnel[bucket] += 1
            elif bucket not in ('screen_fail', 'withdrawn', 'dropout', 'completed'):
                site_funnel['active'] += 1
        site_staff = group.get('staff') or []
        pi = next((person for person in site_staff if person.get('role') == 'pi'), None)
        crc = next((person for person in site_staff if person.get('role') == 'crc'), None)
        if not pi and group.get('pi_name'):
            pi = {
                'id': f"{group.get('id')}-pi",
                'full_name': group.get('pi_name') or '',
                'email': group.get('pi_email') or '',
                'phone': group.get('pi_phone') or '',
                'role': 'pi',
                'organization': site_name,
            }
        for person in filter(None, (pi, crc)):
            team_by_id[person['id']] = {
                'id': person['id'],
                'name': person.get('full_name') or '',
                'role': person.get('role') or '',
                'organization': person.get('organization') or site_name,
                'designation': (person.get('profile') or {}).get('designation') or '',
                'email': person.get('email') or '',
                'phone': person.get('phone') or '',
            }
        stats = site_visit_stats.get(
            site_name, {'total': 0, 'completed': 0, 'overdue': 0})
        trial_target = (group.get('trial_targets') or {}).get(
            trial_id, trial.get('target_enrollment') or 0)
        sites.append({
            'id': group.get('id'),
            'name': site_name,
            'address': group.get('address') or '',
            'city': group.get('city') or '',
            'state': group.get('state') or '',
            'hospital_type': group.get('hospital_type') or '',
            'department': group.get('department') or '',
            'access_type': group.get('access_type') or 'full',
            'status': group.get('status') or 'active',
            'pi_name': (pi or {}).get('full_name') or '',
            'pi_email': (pi or {}).get('email') or '',
            'pi_phone': (pi or {}).get('phone') or '',
            'crc_name': (crc or {}).get('full_name') or '',
            'enrolled': len(site_patients),
            'target_enrollment': trial_target or None,
            'enrollment_pct': round((len(site_patients) / trial_target) * 100)
            if trial_target else 0,
            'visit_compliance': round((stats['completed'] / stats['total']) * 100)
            if stats['total'] else 0,
            'overdue_visits': stats['overdue'],
            'recruitment': site_funnel,
        })

    creator = await db.users.find_one(
        {'id': trial.get('created_by')},
        {'_id': 0, 'id': 1, 'full_name': 1, 'role': 1},
    ) or {}
    sponsor_contact = {
        'id': user['id'],
        'name': user.get('full_name') or '',
        'role': user.get('role') or '',
        'organization': org_name,
        'designation': (user.get('profile') or {}).get('designation') or '',
        'email': user.get('email') or '',
        'phone': user.get('phone') or '',
    }
    team = [sponsor_contact, *team_by_id.values()]
    visits = await db.visits.find(
        {'trial_id': trial_id}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    documents = await db.files.find(
        {'scope.type': 'trial', 'scope.id': trial_id},
        {'_id': 0, 'key': 0},
    ).sort('created_at', -1).to_list(500)
    for document in documents:
        document['url'] = f"/api/files/{document['id']}"
    version_rows = await db.schedule_versions.find(
        {'trial_id': trial_id},
        {'_id': 0, 'visits': 0},
    ).sort('version', -1).to_list(500)

    return serialize({
        **trial,
        'visits': visits,
        'total_visits': len(visits),
        'site_count': len(sites),
        'enrolled_count': len(patients),
        'created_by_name': creator.get('full_name') or '',
        'created_by_role': creator.get('role') or '',
        'recruitment': recruitment,
        'sites': sites,
        'subjects': subjects,
        'team': team,
        'documents': documents,
        'schedule_version': trial.get('schedule_version') or (
            version_rows[0].get('version') if version_rows else 0),
        'versions': version_rows,
        'capabilities': {
            'can_add_site': bool(user.get('org_admin')),
            'can_manage_schedule': True,
            'can_share': True,
        },
    })


async def _require_sponsor_trial_detail(trial_id: str, user: dict) -> dict:
    """Resolve one trial and enforce its role-appropriate relationship boundary."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    return trial


@api.get('/trials/{trial_id}/recruitment',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def trial_recruitment(trial_id: str, user=Depends(current_user)):
    trial = await _require_sponsor_trial_detail(trial_id, user)
    payload = await _sponsor_trial_detail_payload(trial, user)
    return {
        'trial_id': trial_id,
        'recruitment': payload['recruitment'],
        'sites': [{
            'id': site.get('id'),
            'name': site.get('name'),
            'address': site.get('address') or '',
            'city': site.get('city') or '',
            'state': site.get('state') or '',
            'target_enrollment': site.get('target_enrollment'),
            'enrolled': site.get('enrolled', 0),
            'enrollment_pct': site.get('enrollment_pct', 0),
            'recruitment': site.get('recruitment') or {},
            'department': site.get('department') or '',
            'pi_name': site.get('pi_name') or '',
            'pi_email': site.get('pi_email') or '',
            'pi_phone': site.get('pi_phone') or '',
            'crc_name': site.get('crc_name') or '',
        } for site in payload['sites']],
    }


@api.get('/trials/{trial_id}/subjects',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def trial_subjects(
    trial_id: str,
    site: Optional[str] = None,
    subject_status: Optional[str] = Query(None, alias='status'),
    user=Depends(current_user),
):
    trial = await _require_sponsor_trial_detail(trial_id, user)
    rows = (await _sponsor_trial_detail_payload(trial, user))['subjects']
    if site:
        rows = [row for row in rows if row.get('site') == site]
    if subject_status:
        wanted = _recruitment_bucket(subject_status)
        rows = [row for row in rows
                if _recruitment_bucket(row.get('status')) == wanted]
    return rows


@api.get('/trials/{trial_id}/subjects/{subject_id}/visits',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def trial_subject_visits(
    trial_id: str,
    subject_id: str,
    user=Depends(current_user),
):
    await _require_sponsor_trial_detail(trial_id, user)
    subject = await db.patients.find_one(
        {
            'trial_id': trial_id,
            '$or': [{'id': subject_id}, {'subject_id': subject_id}],
        },
        {'_id': 0, 'id': 1},
    )
    if not subject:
        raise HTTPException(404, 'Subject not found in this trial')
    rows = await db.visit_instances.find(
        {'trial_id': trial_id, 'patient_id': subject['id']},
        {
            '_id': 0, 'id': 1, 'visit_id': 1, 'visit_number': 1, 'name': 1,
            'status': 1, 'scheduled_date': 1, 'window_start': 1,
            'window_end': 1, 'completed_at': 1,
        },
    ).sort('visit_number', 1).to_list(500)
    return rows


@api.get('/trials/{trial_id}/team',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def trial_team(trial_id: str, user=Depends(current_user)):
    trial = await _require_sponsor_trial_detail(trial_id, user)
    return (await _sponsor_trial_detail_payload(trial, user))['team']


@api.get('/trials/{trial_id}/documents',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def trial_documents(trial_id: str, user=Depends(current_user)):
    trial = await _require_sponsor_trial_detail(trial_id, user)
    return (await _sponsor_trial_detail_payload(trial, user))['documents']


@api.get('/trials/{trial_id}/versions',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def trial_versions(trial_id: str, user=Depends(current_user)):
    await _require_sponsor_trial_detail(trial_id, user)
    rows = await db.schedule_versions.find(
        {'trial_id': trial_id},
        {'_id': 0, 'visits': 0},
    ).sort('version', -1).to_list(500)
    return rows


@api.get('/sponsor/trials/{trial_id}',
         dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_trial_detail(trial_id: str, user=Depends(current_user)):
    trial = await _require_sponsor_trial_detail(trial_id, user)
    return await _sponsor_trial_detail_payload(trial, user)


@api.get('/sponsor/pi-lookup',
         dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_pi_lookup(email: EmailStr, user=Depends(current_user)):
    """Resolve an exact PI email for an organization-admin site assignment."""
    if not user.get('org_admin'):
        raise HTTPException(403, 'Only an organization admin can look up PIs')
    pi = await db.users.find_one(
        {'email': str(email).lower(), 'role': 'pi'},
        {'_id': 0, 'id': 1, 'email': 1, 'full_name': 1, 'phone': 1,
         'organization': 1, 'profile.department': 1},
    )
    if not pi:
        return {'found': False}
    return {
        'found': True,
        'pi': {
            'id': pi['id'],
            'email': pi.get('email') or '',
            'full_name': pi.get('full_name') or '',
            'phone': pi.get('phone') or '',
            'organization': pi.get('organization') or '',
            'department': (pi.get('profile') or {}).get('department') or '',
        },
    }


@api.get('/sponsor/share-site-directory',
         dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_share_site_directory(
    trial_id: Optional[str] = None,
    user=Depends(current_user),
):
    """All active Site organizations available to the schedule-share picker.

    Sponsor dashboard sites are deliberately scoped to the caller's existing
    network. Schedule sharing additionally needs a discovery surface so a
    sponsor can find a new registered Site and establish the relationship by
    sharing a trial. Only professional PI identity is returned; patient data is
    never part of this directory contract.
    """
    if trial_id:
        trial = await db.trials.find_one({'id': trial_id}, {'_id': 0, 'id': 1})
        if not trial:
            raise HTTPException(404, 'Trial not found')
        if not await _trial_in_caller_org(user, trial_id):
            raise HTTPException(403, 'You do not have access to this trial')

    sponsor_org_name = (user.get('organization') or '').strip()
    sponsor_org = await db.organizations.find_one(
        {'name': sponsor_org_name}, {'_id': 0, 'id': 1},
    ) if sponsor_org_name else None
    network_sites = await db.org_sites.find(
        {'org_id': sponsor_org['id']} if sponsor_org else {'id': {'$in': []}},
        {'_id': 0},
    ).to_list(2000)

    site_orgs = await db.organizations.find(
        {
            'type': 'site',
            'status': {'$nin': ['merged', 'inactive', 'Inactive', 'suspended', 'Suspended']},
        },
        {'_id': 0, 'id': 1, 'name': 1, 'address': 1, 'city': 1,
         'state': 1, 'status': 1},
    ).sort('name', 1).to_list(2000)
    site_names = [str(row.get('name') or '').strip() for row in site_orgs]
    pi_rows = await db.users.find(
        {
            'role': 'pi',
            'organization': {'$in': site_names},
            'status': {'$nin': ['Inactive', 'inactive', 'Removed', 'Suspended', 'suspended']},
        },
        {'_id': 0, 'id': 1, 'full_name': 1, 'email': 1, 'phone': 1,
         'organization': 1, 'org_admin': 1, 'created_at': 1,
         'profile.department': 1},
    ).to_list(5000)
    pis_by_org: Dict[str, List[dict]] = {}
    for pi in pi_rows:
        key = str(pi.get('organization') or '').strip().casefold()
        if key:
            pis_by_org.setdefault(key, []).append(pi)
    for rows in pis_by_org.values():
        rows.sort(key=lambda row: (
            not bool(row.get('org_admin')),
            str(row.get('created_at') or ''),
            str(row.get('full_name') or row.get('email') or '').casefold(),
        ))

    network_by_org_id = {
        row.get('site_org_id'): row for row in network_sites
        if row.get('site_org_id')
    }
    network_by_name = {
        str(row.get('name') or '').strip().casefold(): row
        for row in network_sites if row.get('name')
    }
    result = []
    for organization in site_orgs:
        org_name = str(organization.get('name') or '').strip()
        network_site = (
            network_by_org_id.get(organization.get('id'))
            or network_by_name.get(org_name.casefold())
        )
        available_pis = pis_by_org.get(org_name.casefold(), [])
        pi = None
        if network_site:
            linked_id = network_site.get('user_id')
            linked_email = str(network_site.get('pi_email') or '').strip().lower()
            pi = next((row for row in available_pis if row.get('id') == linked_id), None)
            if not pi and linked_email:
                pi = next((row for row in available_pis
                           if str(row.get('email') or '').strip().lower() == linked_email), None)
        pi = pi or (available_pis[0] if available_pis else None)
        ordered_pis = (
            ([pi] if pi else [])
            + [row for row in available_pis if not pi or row.get('id') != pi.get('id')]
        )
        trial_ids = (network_site or {}).get('trial_ids') or []
        result.append({
            'id': (network_site or {}).get('id') or f"directory:{organization['id']}",
            'organization_id': organization['id'],
            'name': org_name,
            'address': (network_site or {}).get('address') or organization.get('address') or '',
            'city': (network_site or {}).get('city') or organization.get('city') or '',
            'state': (network_site or {}).get('state') or organization.get('state') or '',
            'status': organization.get('status') or 'active',
            'pi_id': (pi or {}).get('id') or '',
            'pi_name': (pi or {}).get('full_name') or '',
            'pi_email': (pi or {}).get('email') or '',
            'pi_phone': (pi or {}).get('phone') or '',
            'pis': [{
                'id': row.get('id') or '',
                'name': row.get('full_name') or '',
                'email': row.get('email') or '',
                'phone': row.get('phone') or '',
                'department': (row.get('profile') or {}).get('department') or '',
                'is_default': bool(pi and row.get('id') == pi.get('id')),
            } for row in ordered_pis],
            'pi_count': len(available_pis),
            'in_network': bool(network_site),
            'assigned_to_trial': bool(trial_id and trial_id in trial_ids),
            'can_receive_schedule': bool(available_pis),
        })
    return result


@api.get('/sponsor/trials/{trial_id}/subjects',
         dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_trial_subjects(trial_id: str,
                                 site: Optional[str] = None,
                                 subject_status: Optional[str] = Query(
                                     None, alias='status'),
                                 user=Depends(current_user)):
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _trial_in_caller_org(user, trial_id):
        raise HTTPException(403, 'You do not have access to this trial')
    payload = await _sponsor_trial_detail_payload(trial, user)
    rows = payload['subjects']
    if site:
        rows = [row for row in rows if row.get('site') == site]
    if subject_status:
        wanted = _recruitment_bucket(subject_status)
        rows = [
            row for row in rows
            if _recruitment_bucket(row.get('status')) == wanted
        ]
    return rows


@api.post('/sponsor/trials/{trial_id}/sites',
          dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_add_trial_site(trial_id: str, body: SponsorTrialSiteIn,
                                 user=Depends(current_user)):
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _trial_in_caller_org(user, trial_id):
        raise HTTPException(403, 'You do not have access to this trial')
    if not user.get('org_admin'):
        raise HTTPException(403, 'Only an organization admin can add trial sites')
    org_name = (user.get('organization') or '').strip()
    organization = await db.organizations.find_one(
        {'name': org_name}, {'_id': 0})
    if not organization:
        raise HTTPException(400, 'Your organization could not be resolved')

    site_name = body.name.strip()
    registered_pi = await db.users.find_one(
        {'email': str(body.pi_email).lower(), 'role': 'pi'},
        {'_id': 0, 'id': 1, 'email': 1, 'full_name': 1, 'phone': 1,
         'organization': 1, 'profile.department': 1},
    )
    existing = await db.org_sites.find_one(
        {'org_id': organization['id'], 'name': site_name}, {'_id': 0})
    fields = {
        'address': (body.address or '').strip(),
        'city': (body.city or '').strip(),
        'state': (body.state or '').strip(),
        'hospital_type': body.hospital_type or 'Private',
        'department': ((registered_pi or {}).get('profile') or {}).get('department')
                      or (body.department or '').strip(),
        'pi_name': (registered_pi or {}).get('full_name') or body.pi_name.strip(),
        'pi_email': str(body.pi_email).lower(),
        'pi_phone': (registered_pi or {}).get('phone') or (body.pi_phone or '').strip(),
        'access_type': body.access_type,
        'status': 'active',
        'updated_at': now(),
        'updated_by': user['id'],
    }
    if registered_pi:
        fields['user_id'] = registered_pi['id']
    target_path = f'trial_targets.{trial_id}'
    if existing:
        update_set = dict(fields)
        if body.target_enrollment is not None:
            update_set[target_path] = body.target_enrollment
        await db.org_sites.update_one(
            {'id': existing['id']},
            {'$set': update_set, '$addToSet': {'trial_ids': trial_id}},
        )
        site_id = existing['id']
    else:
        site_id = str(uuid.uuid4())
        doc = {
            'id': site_id, 'org_id': organization['id'], 'name': site_name,
            **fields, 'trial_ids': [trial_id],
            'trial_targets': {
                trial_id: body.target_enrollment
            } if body.target_enrollment is not None else {},
            'created_at': now(), 'created_by': user['id'],
        }
        await db.org_sites.insert_one(doc)

    # "Save & Share with PI": persist an invitation alongside the site
    # assignment. Reuse a still-pending invite rather than sending duplicates.
    invitation = await db.invitations.find_one({
        'email': str(body.pi_email).lower(), 'trial_id': trial_id,
        'role': 'pi',
        'status': {'$in': ['pending', 'accepted']} if registered_pi else 'pending',
    }, {'_id': 0})
    if registered_pi and invitation and invitation.get('status') != 'accepted':
        await db.invitations.update_one(
            {'id': invitation['id']},
            {'$set': {
                'status': 'accepted',
                'accepted_user_id': registered_pi['id'],
                'accepted_at': now(),
            }},
        )
        invitation = {**invitation, 'status': 'accepted',
                      'accepted_user_id': registered_pi['id']}
    if not invitation:
        token = uuid.uuid4().hex
        invitation = {
            'id': str(uuid.uuid4()), 'token': token,
            'email': str(body.pi_email).lower(), 'phone': body.pi_phone or '',
            'full_name': body.pi_name.strip(), 'role': 'pi',
            'trial_id': trial_id, 'invited_by': user['id'],
            'org': site_name, 'organization': site_name,
            'inviter_name': user.get('full_name') or '',
            'inviter_organization': site_name,
            'status': 'accepted' if registered_pi else 'pending',
            'accepted_user_id': registered_pi.get('id') if registered_pi else None,
            'accepted_at': now() if registered_pi else None,
            'created_at': now(),
            'expires_at': now() + timedelta(days=INVITE_TTL_DAYS),
            'resend_count': 0,
        }
        await db.invitations.insert_one(invitation)
        if not registered_pi:
            try:
                await run_in_threadpool(
                    otp_service.send_invitation_email,
                    invitation['email'],
                    _invite_link(invitation['token']),
                    invitation['full_name'],
                    invitation['inviter_name'],
                    invitation['inviter_organization'],
                )
            except (otp_service.OTPConfigError, otp_service.OTPDeliveryError):
                await db.invitations.delete_one({'id': invitation['id']})
                raise HTTPException(502, 'The PI invitation email could not be delivered.')
    await write_audit(
        user, 'trial.site_add',
        f'Added {site_name} to {trial.get("protocol_id") or trial_id} and shared with PI',
        target_id=site_id, trial_id=trial_id, org_id=organization['id'])
    stored = await db.org_sites.find_one({'id': site_id}, {'_id': 0})
    return {
        'site': serialize(stored),
        'invitation': {
            'id': invitation['id'],
            'status': invitation.get('status') or 'pending',
            'invite_link': _invite_link(invitation['token']),
            'expires_at': iso(invitation.get('expires_at')),
        },
    }


@api.post('/sponsor/trials/{trial_id}/sites/import',
          dependencies=[Depends(require_roles('sponsor', 'cro'))])
async def sponsor_import_trial_sites(
    trial_id: str,
    file: UploadFile = File(...),
    user=Depends(current_user),
):
    """Import a CSV site roster with explicit per-row success/error results.

    Successful rows use the exact same persistent site-assignment and PI
    invitation workflow as single entry. A malformed row cannot be mistaken
    for an import: it is returned with its 1-based spreadsheet row number and
    validation message.
    """
    import csv
    import io

    trial = await _require_sponsor_trial_detail(trial_id, user)
    if not user.get('org_admin'):
        raise HTTPException(403, 'Only an organization admin can import trial sites')
    filename = (file.filename or '').strip().lower()
    content_type = (file.content_type or '').lower().split(';')[0]
    if not filename.endswith('.csv') and content_type not in (
        'text/csv', 'application/csv', 'application/vnd.ms-excel',
    ):
        raise HTTPException(400, 'Upload a CSV site roster')
    data = await file.read(2 * 1024 * 1024 + 1)
    if not data:
        raise HTTPException(400, 'The uploaded CSV is empty')
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, 'Site roster is too large (maximum 2 MB)')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise HTTPException(400, 'Site roster must be UTF-8 encoded CSV')
    reader = csv.DictReader(io.StringIO(text))
    headers = {
        (header or '').strip().lower() for header in (reader.fieldnames or [])
    }
    required_headers = {'name', 'pi_name', 'pi_email'}
    missing_headers = sorted(required_headers - headers)
    if missing_headers:
        raise HTTPException(
            400, f"Missing required CSV columns: {', '.join(missing_headers)}")

    rows = list(reader)
    if not rows:
        raise HTTPException(400, 'Site roster contains no data rows')
    if len(rows) > 500:
        raise HTTPException(413, 'Site roster may contain at most 500 rows')

    results = []
    for index, raw in enumerate(rows, start=2):
        normalized = {
            (key or '').strip().lower(): (value or '').strip()
            for key, value in raw.items()
        }
        try:
            target_raw = normalized.get('target_enrollment') or ''
            try:
                target = int(target_raw) if target_raw else None
            except ValueError:
                raise ValueError('target_enrollment must be a whole number')
            hospital = normalized.get('hospital_type') or 'Private'
            hospital = hospital[:1].upper() + hospital[1:].lower()
            access_type = (
                normalized.get('access_type') or 'full'
            ).lower().replace(' ', '_')
            body = SponsorTrialSiteIn(
                name=normalized.get('name') or '',
                address=normalized.get('address') or '',
                city=normalized.get('city') or '',
                state=normalized.get('state') or '',
                hospital_type=hospital,
                department=normalized.get('department') or '',
                pi_name=normalized.get('pi_name') or '',
                pi_email=normalized.get('pi_email') or '',
                pi_phone=normalized.get('pi_phone') or '',
                target_enrollment=target,
                access_type=access_type,
            )
            imported = await sponsor_add_trial_site(trial_id, body, user)
            results.append({
                'row': index,
                'status': 'imported',
                'site_id': imported['site']['id'],
                'invitation_id': imported['invitation']['id'],
            })
        except HTTPException as exc:
            results.append({
                'row': index, 'status': 'error', 'error': str(exc.detail)})
        except Exception as exc:
            results.append({
                'row': index, 'status': 'error', 'error': str(exc)})

    imported_count = sum(
        1 for result in results if result['status'] == 'imported')
    await write_audit(
        user, 'trial.site_import',
        f"Imported {imported_count}/{len(results)} sites for "
        f"{trial.get('protocol_id') or trial_id}",
        trial_id=trial_id, imported=imported_count,
        failed=len(results) - imported_count,
    )
    return {
        'total': len(results),
        'imported': imported_count,
        'failed': len(results) - imported_count,
        'results': results,
    }


@api.get('/smo/dashboard', dependencies=[Depends(require_roles('smo', 'site'))])
@api.get('/site/dashboard', dependencies=[Depends(require_roles('smo', 'site'))])
async def smo_dashboard(user=Depends(current_user)):
    """De-identified OPERATIONAL dashboard for SMO/Site organization members.

    Deliberately separate from the org-admin console: this is available to
    every SMO/Site account and exposes network totals, masked subjects and
    visit workload, while member management, ownership transfer and other
    governance remain in the org-admin console (org_admin only).
    """
    org_name = (user.get('organization') or '').strip()
    if not org_name:
        raise HTTPException(400, 'Your account is not linked to an organization')

    all_trials = await db.trials.find({}, {'_id': 0}).to_list(1000)
    trials = [trial for trial in all_trials if await _can_access_trial(user, trial)]
    trial_ids = [trial['id'] for trial in trials]
    patients = await db.patients.find(
        {'trial_id': {'$in': trial_ids}} if trial_ids else {'trial_id': {'$in': []}},
        {'_id': 0, 'id': 1, 'trial_id': 1, 'pi_id': 1, 'crc_id': 1,
         'avatar_initials': 1, 'subject_id': 1, 'status': 1}).to_list(5000)

    staff_ids = {
        staff_id for patient in patients
        for staff_id in (patient.get('pi_id'), patient.get('crc_id'))
        if staff_id
    }
    staff_rows = await db.users.find(
        {'id': {'$in': list(staff_ids)}} if staff_ids else {'id': {'$in': []}},
        {'_id': 0, 'id': 1, 'full_name': 1, 'organization': 1,
         'role': 1}).to_list(2000)
    staff = {row['id']: row for row in staff_rows}

    masked_patients = []
    patient_ids = []
    for patient in patients:
        patient_ids.append(patient['id'])
        pi = staff.get(patient.get('pi_id')) or {}
        crc = staff.get(patient.get('crc_id')) or {}
        masked_patients.append({
            'id': patient['id'],
            'subject_id': patient.get('subject_id') or
                          f"SUBJ-{patient['id'][-3:].upper()}",
            'avatar_initials': patient.get('avatar_initials') or '',
            'trial_id': patient.get('trial_id'),
            'site': pi.get('organization') or crc.get('organization') or '',
            'pi_name': pi.get('full_name') or '',
            'status': patient.get('status') or 'active',
        })

    upcoming = []
    if patient_ids:
        rows = await db.visit_instances.find({
            'patient_id': {'$in': patient_ids},
            'status': {'$in': ['scheduled', 'upcoming', 'overdue']},
        }, {'_id': 0}).sort('scheduled_date', 1).to_list(100)
        patient_map = {patient['id']: patient for patient in masked_patients}
        for row in rows:
            patient = patient_map.get(row.get('patient_id')) or {}
            upcoming.append({
                'id': row.get('id'),
                'type': 'overdue_visit' if row.get('status') == 'overdue' else 'visit_today',
                'title': row.get('name') or 'Scheduled visit',
                'subtitle': ' · '.join(filter(None, [
                    patient.get('subject_id'), patient.get('site')])),
                'due': iso(row.get('scheduled_date')),
                'patient_id': row.get('patient_id'),
                'trial_id': row.get('trial_id') or patient.get('trial_id'),
                'priority': 'high' if row.get('status') == 'overdue' else 'medium',
                'status': row.get('status'),
            })

    organization = await db.organizations.find_one(
        {'name': org_name}, {'_id': 0}) or {}
    network_sites = await db.org_sites.find(
        {'org_id': organization.get('id')},
        {'_id': 0}).sort('name', 1).to_list(500) if organization.get('id') else []
    site_names = {site.get('name') for site in network_sites if site.get('name')}
    site_names.update(patient.get('site') for patient in masked_patients
                      if patient.get('site'))

    sponsors = {trial.get('sponsor_name') for trial in trials
                if trial.get('sponsor_name') and trial.get('sponsor_name') != org_name}
    trial_cards = []
    counts: Dict[str, int] = {}
    for patient in masked_patients:
        counts[patient['trial_id']] = counts.get(patient['trial_id'], 0) + 1
    for trial in trials:
        trial_cards.append({
            **trial,
            'enrolled_count': counts.get(trial['id'], 0),
            'target_enrollment': trial.get('target_enrollment'),
        })

    return serialize({
        'organization': {
            'name': org_name,
            'type': organization.get('type')
                    or ('site' if user.get('role') == 'site' else 'smo'),
            'org_admin': bool(user.get('org_admin')),
        },
        'totals': {
            'trials': len(trials),
            'sites': len(site_names),
            'subjects': len(masked_patients),
            'sponsors': len(sponsors),
        },
        'trials': trial_cards,
        'subjects': masked_patients,
        'tasks': upcoming,
        'sites': network_sites,
    })

async def _persist_schedule_definition(
    trial_id: str,
    schedule: pe.ExtractedSchedule,
    user: dict,
    *,
    source_extraction_id: str,
) -> str:
    """Persist the immutable AI draft without replacing an approved schedule."""
    existing = await db.schedule_definitions.find_one({
        'trial_id': trial_id,
        'source_extraction_id': source_extraction_id,
    }, {'_id': 0, 'id': 1})
    if existing:
        return existing['id']
    definition_id = str(uuid.uuid4())
    document = {
        'id': definition_id,
        'trial_id': trial_id,
        'schema_version': '2.0',
        'status': 'draft_review',
        'classification': (
            schedule.classification.model_dump() if schedule.classification else None),
        'canonical_plan': (
            schedule.canonical_plan.model_dump() if schedule.canonical_plan else None),
        'evidence_facts': [item.model_dump() for item in schedule.evidence_facts],
        'canonical_validation': schedule.canonical_validation,
        'compatibility_visits': [item.model_dump() for item in schedule.visits],
        'verification': {
            'status': schedule.verification_status,
            'confidence': schedule.verification_confidence,
            'issues': schedule.verification_issues,
            'accuracy': schedule.verification_scores,
        },
        'source_extraction_id': source_extraction_id,
        'created_by': user['id'],
        'created_at': now(),
    }
    await db.schedule_definitions.insert_one(document)
    await db.trials.update_one(
        {'id': trial_id},
        {'$set': {'current_schedule_definition_id': definition_id}})
    return definition_id


def _schedule_extraction_payload(
    schedule: pe.ExtractedSchedule,
    *,
    schedule_definition_id: str | None = None,
) -> dict:
    """Convert an extracted schedule to the existing editor response contract."""
    visits = []
    global_warning = schedule.verification_status == 'needs_review'
    for visit in schedule.visits:
        row = visit.model_dump()
        # Undated ET/Unscheduled visits remain visible but require review. An
        # explicitly absolute hour is independently calculable even when its
        # day offset is null (including Hour 0).
        warning = (
            global_warning
            or bool(row.get('extraction_warning'))
            or not _has_calculable_template_time(row)
        )
        # Never turn an unknown day into baseline. The editor can retain the
        # row as manual-review/undated and save that state explicitly.
        row['anchor_study_day'] = schedule.anchor_study_day
        row['includes_day_zero'] = schedule.includes_day_zero
        row['arm_label'] = row.get('arm') or ''
        # Assessments, paperwork and site logistics arrive in one protocol list.
        # Route them to the editor's two columns instead of copying everything
        # into Clinical Tasks and leaving Admin Tasks permanently empty.
        clinical_tasks, admin_tasks = classify_visit_activities(
            row.get('activities') or [])
        row['clinical_tasks'] = clinical_tasks
        row['admin_tasks'] = admin_tasks
        row['comments'] = ''
        row['extraction_warning'] = warning
        row['review_status'] = 'pending' if warning else row.get('review_status', 'ok')
        row['extracted_from_protocol'] = True
        visits.append(row)
    return {
        'schema_version': '2.0',
        'schedule_definition_id': schedule_definition_id,
        'visits': visits,
        'assumptions': schedule.assumptions,
        'schedule_kind': schedule.schedule_kind,
        'anchor_study_day': schedule.anchor_study_day,
        'includes_day_zero': schedule.includes_day_zero,
        'source_notes': schedule.source_notes,
        'classification': (
            schedule.classification.model_dump() if schedule.classification else None),
        'canonical_plan': (
            schedule.canonical_plan.model_dump() if schedule.canonical_plan else None),
        'evidence_facts': [item.model_dump() for item in schedule.evidence_facts],
        'canonical_validation': schedule.canonical_validation,
        'verification': {
            'status': schedule.verification_status,
            'confidence': schedule.verification_confidence,
            'refinement_count': schedule.verification_iterations,
            'issues': schedule.verification_issues,
            'accuracy': schedule.verification_scores,
        },
    }


@api.post('/trials/{trial_id}/extract-schedule',
          dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def extract_schedule(trial_id: str, file: UploadFile = File(...),
                           schedule_option_id: Optional[str] = Form(None),
                           user=Depends(current_user)):
    """AI-assisted: read an uploaded protocol PDF and return its Schedule(s) of
    Assessments as visit templates for the caller to REVIEW and edit before
    saving. Never writes visits — the sponsor confirms via the normal save flow.
    Trial-ownership scoped (same rule as the schedule endpoints).

    When the protocol prints more than one independent Schedule of
    Assessments (e.g. separate substudies), every one of them is now
    extracted automatically and returned together as ``schedule_variants``
    — no reviewer pick-then-re-upload round trip. ``schedule_option_id`` is
    still accepted for backward compatibility but is no longer needed."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if user['role'] in ('sponsor', 'cro'):
        owns = await _trial_in_caller_org(user, trial_id)
    else:  # pi
        owns = await _pi_owns_trial(user, trial)
    if not owns:
        raise HTTPException(403, 'You do not have access to this trial')

    ctype = (file.content_type or '').lower()
    if ctype not in ('application/pdf', 'application/octet-stream', ''):
        raise HTTPException(400, 'Upload a PDF protocol document')
    data = await _read_upload_capped(file, pe.MAX_PDF_BYTES, 'Protocol PDF is too large (max 25 MB)')
    if not data:
        raise HTTPException(400, 'The uploaded file is empty')
    if data[:5] != b'%PDF-':
        raise HTTPException(400, 'The uploaded file does not look like a PDF')

    try:
        results = await pe.get_extractor().extract_all(data)
    except pe.ExtractionNotConfigured as exc:
        raise HTTPException(503, 'Protocol extraction is not configured on the '
                            f'server: {exc}. Set the selected provider API key and restart.')
    except pe.ExtractionUnavailable as e:
        # Provider reachable but refusing work (billing/quota/overload). This is
        # an operations problem, not a problem with the sponsor's document —
        # say so, so nobody wastes time re-uploading a perfectly good protocol.
        raise HTTPException(503, f'Protocol extraction is temporarily unavailable: {e}. '
                                 'Your document was not the problem — please retry later '
                                 'or contact support.')
    except pe.ExtractionError as e:
        raise HTTPException(502, f'Could not extract the schedule: {e}')

    if len(results) == 1 and results[0][0] is None:
        # The ordinary, single-schedule case — unchanged response shape.
        _, schedule = results[0]
        await write_audit(
            user, 'trial.extract_schedule',
            f'Extracted {len(schedule.visits)} visit(s) from protocol PDF for '
            f'{trial.get("protocol_id") or trial_id}', trial_id=trial_id)
        definition_id = await _persist_schedule_definition(
            trial_id, schedule, user,
            source_extraction_id=f'direct:{uuid.uuid4()}',
        )
        return {
            **_schedule_extraction_payload(schedule, schedule_definition_id=definition_id),
            'needs_schedule_selection': False,
        }

    # Multiple independent Schedules of Assessments (e.g. substudies) — every
    # one was extracted already; persist each as its own draft and return
    # them all together for the reviewer to compare/edit/save side by side.
    variants = []
    for option, schedule in results:
        definition_id = await _persist_schedule_definition(
            trial_id, schedule, user,
            source_extraction_id=f'direct:{uuid.uuid4()}:{option.id if option else "0"}',
        )
        variant = {
            **_schedule_extraction_payload(schedule, schedule_definition_id=definition_id),
            'option_id': option.id if option else '',
            'option_label': option.label if option else '',
            'option_description': option.description if option else '',
        }
        variants.append(variant)
    if variants:
        # The trial's canonical schedule pointer must be a deliberate choice,
        # not whichever variant _persist_schedule_definition happened to
        # persist last.
        await db.trials.update_one(
            {'id': trial_id},
            {'$set': {'current_schedule_definition_id': variants[0]['schedule_definition_id']}})
    await write_audit(
        user, 'trial.extract_schedule_variants',
        f'Extracted {len(variants)} independent schedule(s) '
        f'({", ".join(v["option_label"] for v in variants if v["option_label"])}) '
        f'from protocol PDF for {trial.get("protocol_id") or trial_id}',
        trial_id=trial_id)
    return {'needs_schedule_selection': False, 'schedule_variants': variants}


@api.post('/trials/{trial_id}/protocol-extractions/{extraction_id}/consume',
          dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def consume_protocol_extraction(
    trial_id: str,
    extraction_id: str,
    user=Depends(current_user),
):
    """Return the schedule produced during Add Trial without another AI call."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if user['role'] in ('sponsor', 'cro'):
        owns = await _trial_in_caller_org(user, trial_id)
    else:
        owns = await _pi_owns_trial(user, trial)
    if not owns:
        raise HTTPException(403, 'You do not have access to this trial')

    draft = await db.protocol_extractions.find_one({
        'id': extraction_id,
        'user_id': user['id'],
        'expires_at': {'$gt': now()},
    }, {'_id': 0})
    if not draft:
        raise HTTPException(
            404, 'The prepared protocol schedule expired or is unavailable. '
                 'Upload the PDF again to regenerate it.')
    linked_trial = draft.get('trial_id')
    if linked_trial and linked_trial != trial_id:
        raise HTTPException(409, 'This protocol extraction belongs to another trial')

    try:
        schedule = pe.ExtractedSchedule.model_validate(draft.get('schedule') or {})
    except ValueError as exc:
        raise HTTPException(500, 'The prepared schedule could not be read') from exc
    await db.protocol_extractions.update_one(
        {'id': extraction_id},
        {'$set': {'trial_id': trial_id, 'consumed_at': now()}})
    definition_id = await _persist_schedule_definition(
        trial_id, schedule, user, source_extraction_id=extraction_id)
    await write_audit(
        user, 'trial.consume_protocol_extraction',
        f'Reused prepared protocol schedule for '
        f'{trial.get("protocol_id") or trial_id}',
        trial_id=trial_id, target_id=trial_id)
    return {
        **_schedule_extraction_payload(schedule, schedule_definition_id=definition_id),
        'option_id': draft.get('option_id') or '',
        'option_label': draft.get('option_label') or '',
        'option_description': draft.get('option_description') or '',
    }


@api.get('/trials/{trial_id}/schedule-definition',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))])
async def get_schedule_definition(trial_id: str, user=Depends(current_user)):
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    definition_id = trial.get('current_schedule_definition_id')
    if not definition_id:
        raise HTTPException(404, 'No canonical schedule definition exists for this trial')
    definition = await db.schedule_definitions.find_one(
        {'id': definition_id, 'trial_id': trial_id}, {'_id': 0})
    if not definition:
        raise HTTPException(404, 'Canonical schedule definition was not found')
    return serialize(definition)

# ── Visit schedule ──────────────────────────────────────────────────────────
@api.post('/visits')
async def create_visit(body: VisitIn, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    trial = await db.trials.find_one({'id': body.trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    vid = str(uuid.uuid4())
    values = body.model_dump()
    if values.get('arm') and not values.get('arm_label'):
        values['arm_label'] = values['arm']
    values, timing_warning = _normalized_template_timing(values)
    explicitly_acknowledged = (
        values.get('review_status') == 'ok'
        and values.get('extraction_warning') is False
    )
    if timing_warning or (
        not _has_calculable_template_time(values) and not explicitly_acknowledged
    ):
        values['extraction_warning'] = True
        values['review_status'] = 'pending'
        existing = str(values.get('comments') or '').strip()
        reason = timing_warning or 'Visit has no calculable day offset.'
        values['comments'] = ' '.join(part for part in (existing, reason) if part)
    doc = {'id': vid, **values, 'created_at': now()}
    await db.visits.insert_one(doc)
    # Finding 2: a visit ADDED to an in-flight schedule must appear for patients
    # already enrolled (materialize_visit_instances is a per-patient no-op once
    # they have instances, so it would never reach them otherwise).
    await _materialize_new_template_for_enrolled(doc)
    # A newly-added target may resolve existing dependents; a newly-added
    # relative visit may itself become dated. The recomputation re-materializes
    # the already-created instance in place, so no duplicate is created.
    await _recompute_relative_templates(body.trial_id)
    current = await db.visits.find_one({'id': vid}, {'_id': 0})
    return serialize(current or doc)


@api.post('/trials/{trial_id}/schedule-preview')
async def preview_trial_schedule(trial_id: str, body: SchedulePreviewIn,
                                 user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc', 'smo', 'site'))):
    """Calculate the selected trial's real protocol schedule before enrollment.

    This is intentionally read-only: staff must review the same template-based
    dates that will be copied to the patient only after invitation acceptance.
    """
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    try:
        baseline = datetime.fromisoformat(body.baseline_date)
    except ValueError:
        raise HTTPException(400, 'baseline_date must be an ISO 8601 date or datetime')
    if baseline.tzinfo is None:
        baseline = baseline.replace(tzinfo=timezone.utc)
    templates = await db.visits.find({'trial_id': trial_id}, {'_id': 0}) \
                               .sort('visit_number', 1).to_list(500)
    if not templates:
        raise HTTPException(409, 'This trial has no published visit schedule yet')
    preview = _build_schedule_preview(templates, baseline, body.arm_label, body.substudy_label)
    if not preview:
        raise HTTPException(409, 'No visit templates match the selected trial arm/substudy')
    return {
        'baseline_date': iso(baseline),
        'arm_label': body.arm_label or '',
        'substudy_label': body.substudy_label or '',
        'visits': preview,
    }


async def _require_schedule_owner(user: dict, trial: dict):
    """Trial-ownership gate shared by the schedule CRUD endpoints. sponsor/cro
    own via their org (_trial_in_caller_org); pi owns via _pi_owns_trial. Raises
    403 for a foreign trial (fail-closed)."""
    if user['role'] in ('sponsor', 'cro'):
        owns = await _trial_in_caller_org(user, trial['id'])
    else:  # pi
        owns = await _pi_owns_trial(user, trial)
    if not owns:
        raise HTTPException(403, 'You do not have access to this trial')


@api.get('/trials/{trial_id}/visits')
async def list_trial_visits(trial_id: str,
                            user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    """The trial's visit TEMPLATES, sorted by visit_number — the schedule the
    edit screen loads on entry. Trial-ownership scoped (403 for a foreign trial)."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    await _require_schedule_owner(user, trial)
    return await db.visits.find({'trial_id': trial_id}, {'_id': 0}) \
                          .sort('visit_number', 1).to_list(500)


@api.get('/trials/{trial_id}/substudies',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc', 'smo', 'site'))])
async def list_trial_substudies(trial_id: str, user=Depends(current_user)):
    """The distinct substudy_label values saved on this trial's visits, for
    the enrollment substudy picker. Empty for every ordinary,
    single-schedule trial — the picker only ever appears when there's
    something to actually choose between."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    labels = await db.visits.distinct('substudy_label', {'trial_id': trial_id})
    return sorted(label for label in labels if label)


@api.get('/trials/{trial_id}/arms',
         dependencies=[Depends(require_roles('sponsor', 'cro', 'pi', 'crc', 'smo', 'site'))])
async def list_trial_arms(trial_id: str, user=Depends(current_user)):
    """The distinct arm_label values saved on this trial's visits, for the
    enrollment arm picker. Empty for every ordinary, single-arm/shared
    trial — the picker only ever appears when there's something to
    actually choose between."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    labels = await db.visits.distinct('arm_label', {'trial_id': trial_id})
    return sorted(label for label in labels if label)


@api.put('/visits/{visit_id}')
async def update_visit(visit_id: str, body: VisitUpdate,
                       user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    """Update a visit TEMPLATE (name/day_offset/window_days/activities/checklist)
    and re-materialize the trial's future-pending instances. Trial-ownership
    scoped (403 for a foreign trial)."""
    tpl = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    if not tpl:
        raise HTTPException(404, 'Visit template not found')
    trial = await db.trials.find_one({'id': tpl.get('trial_id')}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    await _require_schedule_owner(user, trial)
    # ``exclude_unset`` distinguishes "not edited" from an explicit null. That
    # lets a reviewer safely mark a visit undated instead of retaining a stale
    # offset from an earlier extraction.
    fields = body.model_dump(exclude_unset=True)
    if fields.get('arm') and not fields.get('arm_label'):
        fields['arm_label'] = fields['arm']
    candidate, timing_warning = _normalized_template_timing({**tpl, **fields})
    if candidate.get('day_offset') != tpl.get('day_offset') or 'day_offset' in fields:
        fields['day_offset'] = candidate.get('day_offset')
    if candidate.get('day_end') != tpl.get('day_end') or 'day_end' in fields:
        fields['day_end'] = candidate.get('day_end')
    explicitly_acknowledged = (
        fields.get('review_status') == 'ok'
        and fields.get('extraction_warning') is False
    )
    if timing_warning or (
        not _has_calculable_template_time(candidate) and not explicitly_acknowledged
    ):
        fields['extraction_warning'] = True
        fields['review_status'] = 'pending'
        existing = str(candidate.get('comments') or '').strip()
        reason = timing_warning or 'Visit has no calculable day offset.'
        fields['comments'] = ' '.join(part for part in (existing, reason) if part)
    if not fields:
        raise HTTPException(400, 'Nothing to update')
    await db.visits.update_one({'id': visit_id}, {'$set': fields})
    fresh = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    remat = await _rematerialize_template_change(fresh)
    relative_updates = await _recompute_relative_templates(fresh['trial_id'])
    current = await db.visits.find_one({'id': visit_id}, {'_id': 0}) or fresh
    await write_audit(user, 'visit.update',
                      f"Updated visit template {fresh.get('name', '')} "
                      f"({remat} future instance(s) re-materialized; "
                      f"{relative_updates} relative template(s) recalculated)",
                      target_id=visit_id, trial_id=tpl.get('trial_id'),
                      changes={k: iso(v) for k, v in fields.items()})
    return serialize(current)


@api.delete('/visits/{visit_id}')
async def delete_visit(visit_id: str,
                       user=Depends(require_roles('sponsor', 'cro', 'pi'))):
    """Delete a visit TEMPLATE and remove its future-pending instances (completed
    / missed / past ones are kept as history). Trial-ownership scoped (403)."""
    tpl = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    if not tpl:
        raise HTTPException(404, 'Visit template not found')
    trial = await db.trials.find_one({'id': tpl.get('trial_id')}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    await _require_schedule_owner(user, trial)
    removed = await _rematerialize_template_delete(tpl)
    await db.visits.delete_one({'id': visit_id})
    relative_updates = await _recompute_relative_templates(tpl['trial_id'])
    await write_audit(user, 'visit.delete',
                      f"Deleted visit template {tpl.get('name', '')} "
                      f"({removed} future instance(s) removed; "
                      f"{relative_updates} relative template(s) recalculated)",
                      target_id=visit_id, trial_id=tpl.get('trial_id'))
    return {
        'deleted': True,
        'instances_removed': removed,
        'relative_templates_recalculated': relative_updates,
    }

async def _patient_care_context(patient) -> dict:
    """Site + PI contact for a patient, joined from their assigned PI user.

    Enriches GET /visits/mine so the mobile app renders the real site name and
    PI (name / phone / email for tel:+mailto: links) instead of hardcoding
    "AIIMS Delhi / Dr. Sharma". All keys are always present (empty string when
    the patient has no PI assigned) so the client can rely on the shape."""
    ids = [value for value in (patient.get('pi_id'), patient.get('crc_id')) if value]
    staff_rows = await db.users.find(
        {'id': {'$in': ids}}, {'_id': 0}).to_list(2) if ids else []
    staff = {row['id']: row for row in staff_rows}
    pi = staff.get(patient.get('pi_id')) or {}
    crc = staff.get(patient.get('crc_id')) or {}
    contact = crc or pi
    return {
        'pi_id': pi.get('id') or '',
        'site': pi.get('organization') or crc.get('organization') or '',
        'pi_name': pi.get('full_name') or '',
        'pi_phone': pi.get('phone') or '',
        'pi_email': pi.get('email') or '',
        'crc_id': crc.get('id') or '',
        'crc_name': crc.get('full_name') or '',
        'crc_phone': crc.get('phone') or '',
        'crc_email': crc.get('email') or '',
        'assigned_contact_id': contact.get('id') or '',
        'assigned_contact_name': contact.get('full_name') or '',
        'assigned_contact_role': contact.get('role') or '',
    }

async def _trial_checklist_map(trial_id) -> dict:
    """Map of visit-template id -> its `checklist` prep steps (empty list when
    the template carries none), used to enrich per-patient visit instances."""
    tpls = await db.visits.find({'trial_id': trial_id},
                                {'_id': 0, 'id': 1, 'checklist': 1}).to_list(500)
    return {t['id']: (t.get('checklist') or []) for t in tpls}


def _structured_visit_procedures(template: dict, instance: dict) -> list:
    """Return patient-safe structured procedure rows without inventing copy."""
    source = (
        template.get('procedures')
        or template.get('activities')
        or instance.get('procedures')
        or instance.get('activities')
        or []
    )
    rows = []
    for index, item in enumerate(source):
        if isinstance(item, dict):
            label = str(
                item.get('label') or item.get('name') or item.get('title') or ''
            ).strip()
            description = str(
                item.get('description') or item.get('detail') or ''
            ).strip()
        else:
            label = str(item).strip()
            description = ''
        if label:
            rows.append({
                'id': str(item.get('id') if isinstance(item, dict) and item.get('id')
                          else f'procedure-{index + 1}'),
                'label': label,
                'description': description,
            })
    return rows


async def _patient_visit_detail(patient: dict, visit: dict) -> dict:
    """Enrich an owned visit instance/template for the patient detail screen."""
    template_id = visit.get('visit_template_id') or visit.get('id')
    template = await db.visits.find_one({'id': template_id}, {'_id': 0}) or {}
    trial = await db.trials.find_one(
        {'id': visit.get('trial_id') or patient.get('trial_id')}, {'_id': 0}) or {}
    care = await _patient_care_context(patient)
    completed_by = {}
    if visit.get('completed_by'):
        completed_by = await db.users.find_one(
            {'id': visit['completed_by']}, {'_id': 0}) or {}
    scheduled = visit.get('scheduled_date')
    window_days = visit.get('window_days')
    if window_days is None:
        window_days = template.get('window_days')
    window_width = int(window_days or 0)
    window_start = visit.get('window_start')
    window_end = visit.get('window_end')
    if scheduled and (not window_start or not window_end):
        try:
            parsed = scheduled if isinstance(scheduled, datetime) else datetime.fromisoformat(
                str(scheduled).replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            window_start = window_start or parsed - timedelta(days=window_width)
            window_end = window_end or parsed + timedelta(days=window_width)
        except (TypeError, ValueError):
            pass
    preparation = template.get('checklist') or visit.get('checklist') or []
    return serialize({
        **template,
        **visit,
        **care,
        'protocol_id': trial.get('protocol_id') or '',
        'phase': trial.get('phase') or '',
        'indication': trial.get('condition') or trial.get('indication') or '',
        'visit_type': (
            visit.get('visit_type') or template.get('visit_type')
            or visit.get('type') or template.get('type') or ''
        ),
        'location': (
            visit.get('location') or template.get('location') or care.get('site') or ''
        ),
        'window_start': window_start,
        'window_end': window_end,
        'window_days': window_days,
        'completion_timestamp': visit.get('completed_at'),
        'clinician_id': completed_by.get('id') or '',
        'clinician_name': (
            completed_by.get('full_name') or visit.get('completed_by_name') or ''
        ),
        'clinician_role': completed_by.get('role') or '',
        'procedures': _structured_visit_procedures(template, visit),
        'preparation': preparation,
        'checklist': preparation,
    })

@api.get('/visits/mine')
async def my_visits(user=Depends(current_user)):
    """Return upcoming/completed visits for the logged-in patient.

    Served from the patient's own `visit_instances` (created at enrollment /
    startup migration). Falls back to on-the-fly template computation for
    legacy patient records that were never materialized, keeping the exact
    field names the mobile app already consumes. Each visit is additively
    enriched with site/pi_name/pi_phone/pi_email (joined from the patient's PI)
    and the visit template's `checklist`."""
    if user['role'] != 'patient':
        return []
    patient = await db.patients.find_one({'user_id': user['id']}, {'_id': 0})
    if not patient: return []
    care = await _patient_care_context(patient)
    checklists = await _trial_checklist_map(patient['trial_id'])
    instances = await db.visit_instances.find({'patient_id': patient['id']}, {'_id': 0}) \
                                        .sort('seq', 1).to_list(200)
    if instances:
        return [{**(await _ensure_visit_instance_workflow(inst)), **care,
                 'checklist': checklists.get(inst.get('visit_template_id'), [])}
                for inst in instances]
    visits = await db.visits.find({'trial_id': patient['trial_id']}, {'_id': 0}).sort('visit_number', 1).to_list(200)
    completed = set(patient.get('completed_visit_ids', []))
    result = []
    base_date = _patient_visit_anchor(patient)
    for v in visits:
        try:
            scheduled = _calculate_template_datetime(base_date, v)
            scheduled_end = _calculate_template_end_datetime(base_date, v, scheduled)
            window_start, window_end = _schedule_window(v, scheduled)
            operational_status = 'completed' if v['id'] in completed else 'planned'
            manual_review_reason = ''
        except (TypeError, ValueError) as exc:
            scheduled = scheduled_end = window_start = window_end = None
            operational_status = (
                'completed' if v['id'] in completed else 'manual_review')
            manual_review_reason = str(exc)
        display_status = _effective_visit_status({
            'operational_status': operational_status,
            'scheduled_date': scheduled,
            'window_start': window_start,
            'window_end': window_end,
        })
        result.append({
            **v, **care,
            'patient_id': patient['id'],
            'scheduled_date': iso(scheduled),
            'scheduled_end': iso(scheduled_end),
            'window_start': iso(window_start),
            'window_end': iso(window_end),
            'checklist': v.get('checklist') or [],
            'status': display_status,
            'operational_status': operational_status,
            'manual_review_reason': manual_review_reason,
        })
    return result


@api.get('/visits/mine/{visit_id}')
async def my_visit_detail(visit_id: str, user=Depends(current_user)):
    """Return exactly one visit owned by the logged-in patient.

    A real but foreign visit is a 403; an unknown visit is a 404. This prevents
    the detail UI from downloading the whole schedule and selecting a fallback.
    """
    if user.get('role') != 'patient':
        raise HTTPException(403, 'Patient access required')
    patient = await db.patients.find_one({'user_id': user['id']}, {'_id': 0})
    if not patient:
        raise HTTPException(404, 'Patient record not found')
    visit = await db.visit_instances.find_one({'id': visit_id}, {'_id': 0})
    if visit:
        if visit.get('patient_id') != patient.get('id'):
            raise HTTPException(403, 'You do not have access to this visit')
        visit = await _ensure_visit_instance_workflow(visit)
        return await _patient_visit_detail(patient, visit)

    # Legacy patients may still render template IDs until instances are
    # materialized. Only allow a template from this patient's enrolled trial.
    template = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    if not template:
        raise HTTPException(404, 'Visit not found')
    if template.get('trial_id') != patient.get('trial_id'):
        raise HTTPException(403, 'You do not have access to this visit')
    legacy_rows = await my_visits(user)
    legacy = next((row for row in legacy_rows if row.get('id') == visit_id), None)
    if not legacy:
        raise HTTPException(404, 'Visit not found')
    return await _patient_visit_detail(patient, legacy)

# ── Visit instances (per-patient copies of the trial's visit templates) ─────
# The shared `visits` docs are TEMPLATES. Mutating them per patient would leak
# one patient's completion into every other patient's schedule, so on enrollment
# each patient gets their own `visit_instances` rows, and all per-patient
# updates go through PATCH /visit-instances/{id}.

def _patient_visit_anchor(patient) -> datetime:
    """The date a patient's visit schedule anchors on: their baseline date when
    present, else the enrolment date (legacy / seed patients), else now. Always
    returned tz-aware (UTC) so date math is stable."""
    base = None
    for cand in (patient.get('baseline_date'), patient.get('enrolled_date')):
        if cand:
            try:
                base = datetime.fromisoformat(cand)
                break
            except (TypeError, ValueError):
                continue
    if base is None:
        base = now()
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base


def _schedule_window(template: dict, scheduled: datetime) -> tuple[datetime, datetime]:
    """Return a protocol visit window, preserving asymmetric windows when set."""
    symmetric = int(template.get('window_days') or 0)
    before = template.get('window_before')
    after = template.get('window_after')
    before_days = symmetric if before is None else int(before)
    after_days = symmetric if after is None else int(after)
    if before_days < 0 or after_days < 0:
        raise ValueError('Visit windows cannot be negative')
    return scheduled - timedelta(days=before_days), scheduled + timedelta(days=after_days)


def _normalized_template_timing(template: dict) -> tuple[dict, Optional[str]]:
    """Apply deterministic simple-Day conversion without mutating the caller.

    Historical rows have no complete numbering convention and pass through
    unchanged. Newly extracted rows with an exact ``Day N`` label are corrected
    from that evidence before persistence or date calculation.
    """
    normalized = dict(template)
    try:
        derived = pe.simple_day_label_offset(
            normalized.get('source_day_label'),
            anchor_study_day=normalized.get('anchor_study_day'),
            includes_day_zero=normalized.get('includes_day_zero'),
        )
        derived_range = pe.simple_day_label_range_offsets(
            normalized.get('source_day_label'),
            anchor_study_day=normalized.get('anchor_study_day'),
            includes_day_zero=normalized.get('includes_day_zero'),
        )
    except ValueError as exc:
        normalized['day_offset'] = None
        normalized['day_end'] = None
        return normalized, str(exc)
    if derived_range is not None:
        start, end = derived_range
        previous = (normalized.get('day_offset'), normalized.get('day_end'))
        normalized['day_offset'], normalized['day_end'] = start, end
        if previous[0] not in (None, start) or previous[1] not in (None, end):
            return normalized, (
                f"Corrected day range {previous} to ({start}, {end}) from "
                f"{normalized.get('source_day_label')}")
        return normalized, None
    if derived is None:
        return normalized, None
    previous = normalized.get('day_offset')
    normalized['day_offset'] = derived
    warning = None
    if previous is not None and previous != derived:
        warning = (
            f"Corrected day_offset {previous} to {derived} from "
            f"{normalized.get('source_day_label')}")
    return normalized, warning


def _has_calculable_template_time(template: dict) -> bool:
    """Whether a template can produce a date without guessing baseline."""
    if template.get('day_offset') is not None:
        return True
    hour = template.get('hour_offset')
    if hour is None:
        return False
    return template.get('hour_offset_basis') == 'absolute'


def _calculate_template_datetime(anchor: datetime, template: dict) -> datetime:
    """Calculate from the canonical offset and explicit hour interpretation."""
    normalized, numbering_warning = _normalized_template_timing(template)
    if numbering_warning and normalized.get('day_offset') is None:
        raise ValueError(numbering_warning)
    offset = normalized.get('day_offset')
    hour = normalized.get('hour_offset')
    basis = normalized.get('hour_offset_basis')
    if offset is None and basis != 'absolute':
        raise ValueError('Visit has no calculable day offset')
    # day_offset above is a 30-day/365-day approximation of a protocol
    # Month/Year label. Now that a real patient anchor date exists, exact
    # calendar math (true month lengths, leap years) beats that
    # approximation, so prefer it whenever the visit carries no separate
    # hour-level timing that the approximation path would otherwise apply.
    calendar_value = normalized.get('calendar_offset_value')
    calendar_unit = normalized.get('calendar_offset_unit')
    if calendar_value is not None and calendar_unit in ('month', 'year') and hour is None:
        return apply_temporal_amount(
            anchor, TemporalAmount(value=calendar_value, unit=calendar_unit))
    elapsed = pe.canonical_elapsed_time(
        int(offset) if offset is not None else None,
        normalized.get('hour_offset'),
        normalized.get('hour_offset_basis'),
    )
    return anchor + elapsed


def _calculate_template_end_datetime(
    anchor: datetime, template: dict, scheduled: Optional[datetime],
) -> Optional[datetime]:
    """Calculate an optional multi-day/hour-range end from the same anchor."""
    normalized, numbering_warning = _normalized_template_timing(template)
    if numbering_warning and normalized.get('day_offset') is None:
        raise ValueError(numbering_warning)
    if normalized.get('hour_end') is not None:
        end = anchor + pe.canonical_elapsed_time(
            normalized.get('day_offset'), normalized.get('hour_end'),
            normalized.get('hour_offset_basis'))
    elif normalized.get('day_end') is not None:
        end = anchor + timedelta(days=int(normalized['day_end']))
    else:
        return None
    if scheduled is not None and end < scheduled:
        raise ValueError('Visit end cannot be before its scheduled start')
    return end


def _template_matches_arm(template: dict, arm_label: Optional[str]) -> bool:
    template_arm = str(template.get('arm_label') or template.get('arm') or '').strip().lower()
    selected_arm = str(arm_label or '').strip().lower()
    return not template_arm or template_arm == selected_arm


def _template_matches_substudy(template: dict, substudy_label: Optional[str]) -> bool:
    """Same "blank matches everyone" shape as _template_matches_arm, scoped
    to which independent Schedule of Assessments (substudy) a template
    belongs to. A template with no substudy_label (every ordinary,
    single-schedule trial) matches every patient regardless of their own
    substudy_label."""
    template_substudy = str(template.get('substudy_label') or '').strip().lower()
    selected_substudy = str(substudy_label or '').strip().lower()
    return not template_substudy or template_substudy == selected_substudy


def _build_schedule_preview(
    templates: List[dict], baseline: datetime,
    arm_label: Optional[str] = '', substudy_label: Optional[str] = '',
) -> List[dict]:
    """Deterministically calculate a reviewable schedule without writing data."""
    rows = []
    for template in templates:
        if not _template_matches_arm(template, arm_label):
            continue
        if not _template_matches_substudy(template, substudy_label):
            continue
        try:
            scheduled = _calculate_template_datetime(baseline, template)
            scheduled_end = _calculate_template_end_datetime(
                baseline, template, scheduled)
            window_start, window_end = _schedule_window(template, scheduled)
            status = 'planned'
            warning = ''
        except (TypeError, ValueError) as exc:
            scheduled = scheduled_end = window_start = window_end = None
            status = 'manual_review'
            warning = str(exc)
        rows.append({
            'visit_template_id': template.get('id'),
            'visit_number': template.get('visit_number'),
            'name': template.get('name') or 'Visit',
            'source_day_label': template.get('source_day_label') or '',
            'day_offset': template.get('day_offset'),
            'day_end': template.get('day_end'),
            'calendar_offset_value': template.get('calendar_offset_value'),
            'calendar_offset_unit': template.get('calendar_offset_unit'),
            'hour_offset': template.get('hour_offset'),
            'hour_offset_basis': template.get('hour_offset_basis'),
            'hour_end': template.get('hour_end'),
            'relative_to': template.get('relative_to'),
            'relative_offset_days': template.get('relative_offset_days'),
            'period': template.get('period'),
            'arm_label': template.get('arm_label') or template.get('arm') or '',
            'substudy_label': template.get('substudy_label') or '',
            'scheduled_date': iso(scheduled),
            'scheduled_end': iso(scheduled_end),
            'window_start': iso(window_start),
            'window_end': iso(window_end),
            'status': status,
            'manual_review_reason': warning,
        })
    return rows


def _resolve_relative_template_offsets(templates: List[dict]) -> List[dict]:
    """Recompute persisted relative visits to a fixed point.

    A target edit or rename must never leave a dependent visit carrying a stale
    absolute offset. Resolution is arm/period scoped; ambiguous, missing, and
    circular targets become explicitly undated/manual-review rows.
    """
    rows = [dict(template) for template in templates]
    by_name: Dict[str, List[dict]] = {}
    for row in rows:
        by_name.setdefault(str(row.get('name') or '').strip().lower(), []).append(row)

    pending = []
    for row in rows:
        if row.get('relative_to') and row.get('relative_offset_days') is not None:
            row['_previous_day_offset'] = row.get('day_offset')
            row['_previous_day_end'] = row.get('day_end')
            row['day_offset'] = None
            row['day_end'] = None
            pending.append(row)
        else:
            normalized, _ = _normalized_template_timing(row)
            row.update({
                'day_offset': normalized.get('day_offset'),
                'day_end': normalized.get('day_end'),
            })

    for _ in range(len(pending) + 1):
        progressed = False
        for row in list(pending):
            matches = by_name.get(
                str(row.get('relative_to') or '').strip().lower(), [])
            scoped = [candidate for candidate in matches if
                      (candidate.get('arm_label') or candidate.get('arm') or '') ==
                      (row.get('arm_label') or row.get('arm') or '') and
                      (candidate.get('period') or '') == (row.get('period') or '')]
            target = scoped[0] if len(scoped) == 1 else (
                matches[0] if len(matches) == 1 else None)
            if target is None or target.get('day_offset') is None:
                continue
            new_start = int(target['day_offset']) + int(row['relative_offset_days'])
            old_start = row.pop('_previous_day_offset', None)
            old_end = row.pop('_previous_day_end', None)
            row['day_offset'] = new_start
            if old_start is not None and old_end is not None:
                row['day_end'] = new_start + int(old_end) - int(old_start)
            pending.remove(row)
            progressed = True
        if not pending or not progressed:
            break

    for row in rows:
        if row in pending:
            row['_relative_resolution_warning'] = (
                f"Relative target '{row.get('relative_to')}' is missing, ambiguous, "
                "or undated for this arm/period.")
        row.pop('_previous_day_offset', None)
        row.pop('_previous_day_end', None)
    return rows


async def _recompute_relative_templates(trial_id: str) -> int:
    """Persist and re-materialize relative visits affected by a template edit."""
    templates = await db.visits.find(
        {'trial_id': trial_id}, {'_id': 0}).sort('visit_number', 1).to_list(500)
    resolved = _resolve_relative_template_offsets(templates)
    changed = 0
    originals = {row.get('id'): row for row in templates}
    for row in resolved:
        original = originals.get(row.get('id')) or {}
        warning = row.pop('_relative_resolution_warning', None)
        updates = {}
        for field in ('day_offset', 'day_end'):
            if row.get(field) != original.get(field):
                updates[field] = row.get(field)
        if warning:
            updates.update({
                'extraction_warning': True,
                'review_status': 'pending',
            })
        if not updates:
            continue
        await db.visits.update_one({'id': row['id']}, {'$set': updates})
        fresh = {**original, **updates}
        await _rematerialize_template_change(fresh)
        changed += 1
    return changed


_VISIT_TIMING_FIELDS = (
    'day_offset', 'day_end', 'hour_offset', 'hour_offset_basis', 'hour_end',
    'source_day_label', 'anchor_study_day', 'includes_day_zero', 'relative_to',
    'relative_offset_days', 'period', 'arm_label', 'arm', 'window_before',
    'window_after',
)


def _visit_timing_snapshot(template: dict) -> dict:
    """Copy additive protocol timing evidence onto a patient visit instance."""
    return {field: template.get(field) for field in _VISIT_TIMING_FIELDS
            if field in template}


def _visit_task_snapshot(template: dict, kind: str, tasks: list) -> list:
    """Create deterministic task rows for a per-patient visit instance.

    IDs are derived from template + task kind + original position, so repeated
    materialization/migration produces the same identity while duplicate labels
    remain distinct.
    """
    template_id = str(template.get('id') or template.get('visit_template_id') or '')
    rows = []
    for index, item in enumerate(tasks or []):
        if isinstance(item, dict):
            label = str(item.get('label') or item.get('name') or item.get('title') or '').strip()
        else:
            label = str(item).strip()
        if not label:
            continue
        stable = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f'mtb:visit-task:{template_id}:{kind}:{index}:{label}',
        ))
        rows.append({
            'id': stable,
            'label': label,
            'completed': False,
            'completed_by': None,
            'completed_by_name': None,
            'completed_at': None,
        })
    return rows


def _effective_visit_status(instance: dict) -> str:
    """Return the approved display status without overwriting explicit history."""
    status = instance.get('operational_status') or instance.get('status') or 'planned'
    if status not in ('scheduled', 'upcoming', 'planned'):
        return status
    due = instance.get('window_end') or instance.get('scheduled_date')
    if isinstance(due, str):
        try:
            due = datetime.fromisoformat(due.replace('Z', '+00:00'))
        except ValueError:
            due = None
    if isinstance(due, datetime):
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due < now():
            return 'overdue'
    window_start = instance.get('window_start') or instance.get('scheduled_date')
    if isinstance(window_start, str):
        try:
            window_start = datetime.fromisoformat(window_start.replace('Z', '+00:00'))
        except ValueError:
            window_start = None
    if isinstance(window_start, datetime):
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if window_start <= now():
            return 'due'
    return 'planned'


async def _ensure_visit_instance_workflow(instance: dict) -> dict:
    """Lazily migrate legacy instances to per-visit task/comment snapshots."""
    if not instance:
        return instance
    missing_tasks = 'clinical_tasks' not in instance or 'admin_tasks' not in instance
    missing_comments = 'comments' not in instance
    updates: Dict = {}
    if missing_tasks:
        template = await db.visits.find_one(
            {'id': instance.get('visit_template_id')}, {'_id': 0}) or {}
        if 'clinical_tasks' not in instance:
            updates['clinical_tasks'] = _visit_task_snapshot(
                template or instance, 'clinical', template.get('clinical_tasks') or [])
        if 'admin_tasks' not in instance:
            updates['admin_tasks'] = _visit_task_snapshot(
                template or instance, 'admin', template.get('admin_tasks') or [])
    if missing_comments:
        updates['comments'] = []
    if updates:
        await db.visit_instances.update_one({'id': instance['id']}, {'$set': updates})
        instance = {**instance, **updates}
    return {**instance, 'status': _effective_visit_status(instance)}


async def materialize_visit_instances(patient) -> int:
    """Create one visit_instance per trial visit template for `patient`.

    Idempotent per patient (no-op if any instances already exist). Honors the
    legacy `completed_visit_ids` list so migrated patients keep their history;
    otherwise status derives from the scheduled date vs. now (matching the old
    GET /visits/mine computation). Returns the number of instances created.
    """
    if not patient or not patient.get('id') or not patient.get('trial_id'):
        return 0
    if await db.visit_instances.count_documents({'patient_id': patient['id']}, limit=1):
        return 0
    templates = await db.visits.find({'trial_id': patient['trial_id']}, {'_id': 0}) \
                               .sort('visit_number', 1).to_list(500)
    # A patient enrolled under a specific substudy (a trial with more than
    # one independent Schedule of Assessments) only ever gets that
    # substudy's own visits materialized — never the other substudies'.
    # An untagged template (every ordinary, single-schedule trial) still
    # matches every patient, so this is a no-op everywhere else.
    # A patient enrolled under a specific arm/sequence (a trial whose visit
    # templates are arm-tagged) only ever gets that arm's own visits
    # materialized — never another arm's. An untagged template (every
    # ordinary, single-arm/shared trial) still matches every patient, so
    # this is a no-op everywhere else.
    templates = [t for t in templates
                 if _template_matches_substudy(t, patient.get('substudy_label'))
                 and _template_matches_arm(t, patient.get('arm_label'))]
    if not templates:
        return 0
    base = _patient_visit_anchor(patient)
    completed = set(patient.get('completed_visit_ids') or [])
    docs = []
    for t in templates:
        try:
            sched = _calculate_template_datetime(base, t)
            scheduled_end = _calculate_template_end_datetime(base, t, sched)
            window_start, window_end = _schedule_window(t, sched)
            operational_status = 'planned'
            manual_review_reason = ''
        except (TypeError, ValueError) as exc:
            sched = scheduled_end = window_start = window_end = None
            operational_status = 'manual_review'
            manual_review_reason = str(exc)
        wd = t.get('window_days')
        clinical_tasks = _visit_task_snapshot(t, 'clinical', t.get('clinical_tasks') or [])
        admin_tasks = _visit_task_snapshot(t, 'admin', t.get('admin_tasks') or [])
        docs.append({
            'id': str(uuid.uuid4()),
            'patient_id': patient['id'],
            'trial_id': patient['trial_id'],
            'visit_template_id': t['id'],
            'name': t.get('name', ''),
            'seq': t.get('visit_number'),
            # duplicated template fields the RN app reads from /visits/mine
            'visit_number': t.get('visit_number'),
            'activities': t.get('activities', []),
            'procedures': t.get('procedures', []),
            'visit_type': t.get('visit_type', ''),
            'location': t.get('location', ''),
            'window_days': wd,
            **_visit_timing_snapshot(t),
            'scheduled_date': sched,
            'scheduled_end': scheduled_end,
            'window_start': window_start,
            'window_end': window_end,
            # Never infer completion or a missed visit just because time passed.
            'status': 'completed' if t['id'] in completed else operational_status,
            'operational_status': 'completed' if t['id'] in completed else operational_status,
            'manual_review_reason': manual_review_reason,
            'note': '',
            # Immutable per-patient copies of the approved template tasks.
            # Completion metadata is subsequently updated only on this instance.
            'clinical_tasks': clinical_tasks,
            'admin_tasks': admin_tasks,
            'comments': [],
            'updated_by': None,
            'updated_at': now(),
            'created_at': now(),
        })
    await db.visit_instances.insert_many(docs)
    return len(docs)


async def _materialize_new_template_for_enrolled(template) -> int:
    """Create the single instance of a JUST-ADDED template for every patient
    already enrolled in its trial (Finding 2).

    `materialize_visit_instances` is a per-patient no-op once a patient has any
    instances, so a template added mid-trial would otherwise never reach enrolled
    patients. Here we create only THIS template's instance, future-dated off each
    patient's own baseline/enrolment anchor, with the same status/shape as normal
    materialization. Patients not yet materialized are skipped (they'll pick it up
    at enrollment); an existing instance for this template is never duplicated.
    Returns the number of instances created."""
    if not template or not template.get('id') or not template.get('trial_id'):
        return 0
    n = now()
    wd = template.get('window_days')
    created = 0
    async for patient in db.patients.find({'trial_id': template['trial_id']}, {'_id': 0}):
        # only patients who were already materialized need the retro-fit
        if not await db.visit_instances.count_documents({'patient_id': patient['id']}, limit=1):
            continue
        if await db.visit_instances.count_documents(
                {'patient_id': patient['id'], 'visit_template_id': template['id']}, limit=1):
            continue
        # A template belonging to a different substudy than this patient is
        # enrolled under is never materialized for them — same rule as the
        # initial materialize_visit_instances pass.
        if not _template_matches_substudy(template, patient.get('substudy_label')):
            continue
        # Same rule as the initial materialize_visit_instances pass: a
        # template tagged for a different arm than this patient's own is
        # never retro-fitted onto them.
        if not _template_matches_arm(template, patient.get('arm_label')):
            continue
        base = _patient_visit_anchor(patient)
        try:
            sched = _calculate_template_datetime(base, template)
            scheduled_end = _calculate_template_end_datetime(base, template, sched)
            window_start, window_end = _schedule_window(template, sched)
            operational_status = 'planned'
            manual_review_reason = ''
        except (TypeError, ValueError) as exc:
            sched = scheduled_end = window_start = window_end = None
            operational_status = 'manual_review'
            manual_review_reason = str(exc)
        await db.visit_instances.insert_one({
            'id': str(uuid.uuid4()),
            'patient_id': patient['id'],
            'trial_id': template['trial_id'],
            'visit_template_id': template['id'],
            'name': template.get('name', ''),
            'seq': template.get('visit_number'),
            'visit_number': template.get('visit_number'),
            'activities': template.get('activities', []),
            'procedures': template.get('procedures', []),
            'visit_type': template.get('visit_type', ''),
            'location': template.get('location', ''),
            'window_days': wd,
            **_visit_timing_snapshot(template),
            'scheduled_date': sched,
            'scheduled_end': scheduled_end,
            'window_start': window_start,
            'window_end': window_end,
            'status': operational_status,
            'operational_status': operational_status,
            'manual_review_reason': manual_review_reason,
            'note': '',
            'clinical_tasks': _visit_task_snapshot(
                template, 'clinical', template.get('clinical_tasks') or []),
            'admin_tasks': _visit_task_snapshot(
                template, 'admin', template.get('admin_tasks') or []),
            'comments': [],
            'updated_by': None,
            'updated_at': n,
            'created_at': n,
        })
        created += 1
    return created


def _instance_is_repointable(inst, n) -> bool:
    """Whether a visit_instance may be safely re-materialized when its template
    changes. FAIL-CLOSED: only FUTURE, still-pending instances that no one has
    touched are eligible. Completed / missed / rescheduled / past instances and
    any instance carrying patient activity (a note, or an explicit
    updated_by from a PATCH) are treated as history and left untouched."""
    if inst.get('updated_by'):        # someone patched it (reschedule/complete/…)
        return False
    if inst.get('note'):              # carries patient/staff activity
        return False
    if inst.get('status') not in ('upcoming', 'scheduled', 'planned', 'manual_review'):
        return False
    sched = inst.get('scheduled_date')
    if sched is None:
        return inst.get('status') == 'manual_review'
    if isinstance(sched, str):
        try:
            sched = datetime.fromisoformat(sched)
        except ValueError:
            return False
    if sched.tzinfo is None:
        sched = sched.replace(tzinfo=timezone.utc)
    return sched >= n                 # future only


async def _rematerialize_template_change(template) -> int:
    """Propagate a TEMPLATE edit to the trial's future-pending visit_instances.

    Recomputes name/activities/window/scheduled_date for every eligible instance
    (see `_instance_is_repointable`) off each patient's own visit anchor, so a
    schedule edit flows through to patients who haven't yet had the visit —
    without ever clobbering completed/missed/past/touched history. Returns the
    number of instances updated."""
    n = now()
    updated = 0
    anchors: Dict[str, datetime] = {}
    async for inst in db.visit_instances.find(
            {'visit_template_id': template['id']}, {'_id': 0}):
        if not _instance_is_repointable(inst, n):
            continue
        pid = inst['patient_id']
        if pid not in anchors:
            patient = await db.patients.find_one({'id': pid}, {'_id': 0})
            anchors[pid] = _patient_visit_anchor(patient) if patient else n
        try:
            sched = _calculate_template_datetime(anchors[pid], template)
            scheduled_end = _calculate_template_end_datetime(
                anchors[pid], template, sched)
            window_start, window_end = _schedule_window(template, sched)
            operational_status = 'planned'
            manual_review_reason = ''
        except (TypeError, ValueError) as exc:
            sched = scheduled_end = window_start = window_end = None
            operational_status = 'manual_review'
            manual_review_reason = str(exc)
        wd = template.get('window_days')
        await db.visit_instances.update_one({'id': inst['id']}, {'$set': {
            'name': template.get('name', ''),
            # keep the instance's ordinal consistent with the template when the
            # editor re-numbers a row (Finding 1) — only ever for eligible
            # future/pending instances (completed/past keep their original seq).
            'seq': template.get('visit_number'),
            'visit_number': template.get('visit_number'),
            'activities': template.get('activities', []),
            'procedures': template.get('procedures', []),
            'visit_type': template.get('visit_type', ''),
            'location': template.get('location', ''),
            'window_days': wd,
            **_visit_timing_snapshot(template),
            'scheduled_date': sched,
            'scheduled_end': scheduled_end,
            'window_start': window_start,
            'window_end': window_end,
            'status': operational_status,
            'operational_status': operational_status,
            'manual_review_reason': manual_review_reason,
            'updated_at': n,
        }})
        updated += 1
    return updated


async def _rematerialize_template_delete(template) -> int:
    """Remove the future-pending visit_instances of a DELETED template. Completed
    / missed / past / patient-touched instances are kept as history. Returns the
    number of instances removed."""
    n = now()
    removed = 0
    async for inst in db.visit_instances.find(
            {'visit_template_id': template['id']}, {'_id': 0}):
        if _instance_is_repointable(inst, n):
            await db.visit_instances.delete_one({'id': inst['id']})
            removed += 1
    return removed


async def _migrate_visit_instances():
    """Startup backfill: materialize instances for patients enrolled before the
    visit_instances collection existed. Idempotent + cheap (skips patients that
    already have instances); failures only log so the API still boots."""
    try:
        have = await db.visit_instances.distinct('patient_id')
        total = 0
        async for p in db.patients.find({'id': {'$nin': have}}, {'_id': 0}):
            total += await materialize_visit_instances(p)
        if total:
            logging.info('Visit-instance migration: materialized %d instance(s)', total)
    except Exception as e:
        logging.warning('Visit-instance migration deferred (DB unreachable?): %s', e)

# ── Patients ────────────────────────────────────────────────────────────────
# The non-terminal display statuses _effective_visit_status can return (as
# opposed to 'completed' / 'missed' / 'screen_fail' / etc., which are
# terminal for a given visit instance).
_ACTIONABLE_VISIT_STATUSES = ('overdue', 'due', 'planned')


def _derive_patient_status(instances, start_today):
    """Reduce a patient's visit instances to a single list-level status plus
    the soonest actionable visit (`next_visit`), both computed on read.

    - no instances                       → 'no_visits'
    - a pending visit already past-due    → 'overdue'
    - a pending visit still ahead         → 'active'
    - every instance completed            → 'completed'
    - otherwise (only missed remain)      → 'active'
    `next_visit` is the soonest actionable instance (past-due first, else the
    next upcoming), or None when nothing is actionable. A stored instance
    only ever carries a static 'planned'/'completed' status — its real
    overdue/due/planned state is derived here via _effective_visit_status,
    the same derivation GET /patients/{id}/visits and GET /tasks apply.
    """
    if not instances:
        return 'no_visits', None
    instances = [{**i, 'status': _effective_visit_status(i)} for i in instances]
    actionable = [i for i in instances
                  if i.get('status') in _ACTIONABLE_VISIT_STATUSES
                  and isinstance(i.get('scheduled_date'), datetime)]
    actionable.sort(key=lambda i: i['scheduled_date'])
    next_visit = None
    if actionable:
        nv = actionable[0]
        next_visit = {
            'id': nv['id'],
            'name': nv.get('name', ''),
            'seq': nv.get('seq'),
            'scheduled_date': iso(nv.get('scheduled_date')),
            'status': nv.get('status'),
        }
    overdue = any(i['scheduled_date'] < start_today or i.get('status') == 'overdue'
                  for i in actionable)
    if overdue:
        status = 'overdue'
    elif actionable:
        status = 'active'
    elif all(i.get('status') == 'completed' for i in instances):
        status = 'completed'
    else:
        status = 'active'
    return status, next_visit


# ── Ownership scoping (Task 3.75) ────────────────────────────────────────────
# Single source of truth for "may this caller reach this patient / trial?",
# shared by GET /patients/{id}, GET /patients/{id}/visits,
# PATCH /visit-instances/{id} and POST /schedules/{trial_id}/approve|flag.
# Mirrors the GET /patients list rule (site staff scoped to their own site;
# sponsors to their own org's trials) so a crafted id cannot leak a foreign
# patient. NOTE: the medications/calendar `_staff_scoped_patient` helper is a
# deliberately STRICTER pi_id/crc_id-only check and is intentionally left
# unchanged; here the brief calls for "patients whose site/org matches theirs",
# so same-site colleagues are allowed while cross-site is blocked.

async def _org_of(user_id: Optional[str]) -> str:
    """The organization string of a user id (empty when unknown/unset)."""
    if not user_id:
        return ''
    u = await db.users.find_one({'id': user_id}, {'_id': 0, 'organization': 1})
    return (u.get('organization') or '').strip() if u else ''

async def _patient_site_org(patient: dict) -> str:
    """The site a patient belongs to: the org of its assigned PI, else its CRC,
    else whoever enrolled it (created_by)."""
    for key in ('pi_id', 'crc_id', 'created_by'):
        org = await _org_of(patient.get(key))
        if org:
            return org
    return ''

async def _trial_in_caller_org(user: dict, trial_id: Optional[str]) -> bool:
    """True when a trial belongs to the sponsor/cro caller's organization — they
    created it, or its sponsor_name matches their org."""
    if not trial_id:
        return False
    trial = await db.trials.find_one(
        {'id': trial_id}, {'_id': 0, 'sponsor_name': 1, 'created_by': 1})
    if not trial:
        return False
    if trial.get('created_by') == user['id']:
        return True
    org = (user.get('organization') or '').strip()
    if bool(org) and (trial.get('sponsor_name') or '').strip() == org:
        return True
    if not org:
        return False
    organization = await db.organizations.find_one(
        {'name': org}, {'_id': 0, 'id': 1})
    if not organization:
        return False
    grant = await db.org_trial_access.find_one(
        {'org_id': organization['id'], 'trial_id': trial_id, 'granted': True},
        {'_id': 0, 'trial_id': 1})
    return grant is not None


async def _can_access_trial(user: dict, trial: dict) -> bool:
    """Relationship-scoped trial access shared by list/detail/mutations.

    Sponsor/CRO: owned by or explicitly granted to their organization.
    PI: creator, same trial organization, or assigned to an enrolled subject.
    CRC: creator or assigned to an enrolled subject.
    Patient: enrolled in the trial through their own account.
    """
    role = user.get('role')
    if role in ('sponsor', 'cro'):
        return await _trial_in_caller_org(user, trial.get('id'))
    if role == 'pi':
        if await _pi_owns_trial(user, trial):
            return True
        return await _has_accepted_trial_invitation(user, trial.get('id'))
    if role == 'crc':
        if trial.get('created_by') == user.get('id'):
            return True
        assigned = await db.patients.find_one(
            {'trial_id': trial.get('id'), 'crc_id': user.get('id')},
            {'_id': 0, 'id': 1})
        if assigned:
            return True
        return await _has_accepted_trial_invitation(user, trial.get('id'))
    if role == 'patient':
        enrolled = await db.patients.find_one(
            {'trial_id': trial.get('id'), 'user_id': user.get('id')},
            {'_id': 0, 'id': 1})
        return enrolled is not None
    if role in ('smo', 'site'):
        org = (user.get('organization') or '').strip()
        if not org:
            return False
        if trial.get('created_by') == user.get('id'):
            return True
        organization = await db.organizations.find_one(
            {'name': org}, {'_id': 0, 'id': 1})
        if organization:
            grant = await db.org_trial_access.find_one(
                {'org_id': organization['id'], 'trial_id': trial.get('id'),
                 'granted': True}, {'_id': 0, 'trial_id': 1})
            if grant:
                return True
        member_ids = await db.users.distinct('id', {'organization': org})
        if trial.get('created_by') in member_ids:
            return True
        linked = await db.patients.find_one({
            'trial_id': trial.get('id'),
            '$or': [
                {'pi_id': {'$in': member_ids}},
                {'crc_id': {'$in': member_ids}},
                {'created_by': {'$in': member_ids}},
            ],
        }, {'_id': 0, 'id': 1})
        return linked is not None
    return False


async def _has_accepted_trial_invitation(user: dict, trial_id: Optional[str]) -> bool:
    """Match an accepted invite only on a real contact identifier.

    Empty phone values are common for email registrations and must never make
    unrelated users equivalent.
    """
    contacts = []
    email = (user.get('email') or '').strip().lower()
    phone = (user.get('phone') or '').strip()
    if email:
        contacts.append({'email': email})
    if phone:
        contacts.append({'phone': phone})
    if not trial_id or not contacts:
        return False
    invitation = await db.invitations.find_one({
        'trial_id': trial_id,
        'status': 'accepted',
        '$or': contacts,
    }, {'_id': 0, 'id': 1})
    return invitation is not None

async def _can_access_patient(user: dict, patient: dict) -> bool:
    """Ownership predicate shared by every single-patient staff endpoint.

    pi/crc: the patient must be assigned to them (pi_id / crc_id), enrolled by
    them (created_by), or sit at their own site (same organization).
    sponsor/cro: the patient must be enrolled in a trial belonging to their org.
    Any other role: no access.
    """
    role = user['role']
    if role in ('pi', 'crc'):
        key = 'pi_id' if role == 'pi' else 'crc_id'
        if patient.get(key) == user['id'] or patient.get('created_by') == user['id']:
            return True
        caller_org = (user.get('organization') or '').strip()
        return bool(caller_org) and (await _patient_site_org(patient)) == caller_org
    if role in ('sponsor', 'cro'):
        return await _trial_in_caller_org(user, patient.get('trial_id'))
    return False

async def _require_patient(user: dict, patient_id: Optional[str]) -> dict:
    """Load a patient and enforce the caller's ownership scope. 404 when it does
    not exist at all; 403 when it exists but lies outside the caller's scope."""
    p = await db.patients.find_one({'id': patient_id}, {'_id': 0}) if patient_id else None
    if not p:
        raise HTTPException(404, 'Patient not found')
    if not await _can_access_patient(user, p):
        raise HTTPException(403, 'You do not have access to this patient')
    return p

async def _pi_owns_trial(user: dict, trial: dict) -> bool:
    """Whether a PI may review a trial's visit schedule. FAIL-CLOSED: the PI must
    belong to the trial via one of three legitimate ties —
      1. they created it (`created_by`), or
      2. their org matches the trial's org (`sponsor_name`) — this is the
         pre-enrollment approval path (valid even for an unclaimed trial), or
      3. they are a listed PI on it (own a patient enrolled in it).
    A PI whose org differs from the trial's org, who is not the creator, and who
    has no enrolled patient gets no access — even on an 'unclaimed' trial (no
    prior 'unclaimed -> any PI' allow-path)."""
    if trial.get('created_by') == user['id']:
        return True
    org = (user.get('organization') or '').strip()
    if org and (trial.get('sponsor_name') or '').strip() == org:
        return True
    mine = await db.patients.find_one(
        {'trial_id': trial['id'], 'pi_id': user['id']}, {'_id': 0, 'id': 1})
    return mine is not None


@api.get('/patients')
async def list_patients(user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    if user['role'] == 'pi':
        q = {'pi_id': user['id']}
    elif user['role'] == 'crc':
        q = {'crc_id': user['id']}
    else:
        # sponsor/cro: FAIL-CLOSED — only patients enrolled in a trial owned by
        # the caller's org (created by them, or sponsor_name == their org), the
        # same tie the detail endpoint enforces via _trial_in_caller_org. An
        # empty org / no org trials yields an empty list, never every patient.
        org = (user.get('organization') or '').strip()
        trial_or = [{'created_by': user['id']}]
        if org:
            trial_or.append({'sponsor_name': org})
        trials = await db.trials.find({'$or': trial_or}, {'_id': 0, 'id': 1}).to_list(2000)
        q = {'trial_id': {'$in': [t['id'] for t in trials]}}
    patients = await db.patients.find(q, {'_id': 0}).to_list(500)
    if patients:
        pids = [p['id'] for p in patients]
        insts = await db.visit_instances.find(
            {'patient_id': {'$in': pids}}, {'_id': 0}).sort('seq', 1).to_list(5000)
        by_patient: Dict[str, list] = {}
        for i in insts:
            by_patient.setdefault(i['patient_id'], []).append(i)
        start_today = now().replace(hour=0, minute=0, second=0, microsecond=0)
        for p in patients:
            status, next_visit = _derive_patient_status(by_patient.get(p['id'], []), start_today)
            p['status'] = status
            p['next_visit'] = next_visit
    return patients

@api.post('/patients', dependencies=[Depends(require_roles('pi', 'crc', 'smo', 'site'))])
async def add_patient(body: PatientIn, user=Depends(current_user)):
    trial = await db.trials.find_one({'id': body.trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to enroll patients in this trial')

    caller_org = (user.get('organization') or '').strip()
    if user['role'] in ('smo', 'site'):
        if not user.get('org_admin'):
            raise HTTPException(403, 'Organization-admin access is required to enroll patients')
    pi_id = user['id'] if user['role'] == 'pi' else body.pi_id
    crc_id = user['id'] if user['role'] == 'crc' else body.crc_id

    async def default_trial_staff(role: str, field: str) -> Optional[str]:
        """Prefer the staff already handling this trial, then an active
        organisation colleague. This gives new patients a sensible trial-team
        default without replacing the patient-level assignment."""
        existing = await db.patients.find_one(
            {'trial_id': body.trial_id, field: {'$exists': True, '$ne': None}},
            {'_id': 0, field: 1}, sort=[('created_at', -1)])
        if existing and existing.get(field):
            return existing[field]
        if caller_org:
            colleague = await db.users.find_one(
                {'role': role, 'organization': caller_org,
                 'status': {'$nin': ['Inactive', 'Removed', 'Suspended']}},
                {'_id': 0, 'id': 1}, sort=[('created_at', 1)])
            if colleague:
                return colleague['id']
        return None

    if not pi_id:
        pi_id = await default_trial_staff('pi', 'pi_id')
    if not crc_id:
        crc_id = await default_trial_staff('crc', 'crc_id')
    if user['role'] in ('smo', 'site') and not pi_id:
        raise HTTPException(400, 'Select the PI responsible for this patient')
    for staff_id, expected_role, label in (
        (pi_id, 'pi', 'PI'), (crc_id, 'crc', 'CRC'),
    ):
        if not staff_id:
            continue
        staff = await db.users.find_one(
            {'id': staff_id}, {'_id': 0, 'role': 1, 'organization': 1})
        if not staff or staff.get('role') != expected_role:
            raise HTTPException(400, f'Selected {label} is invalid')
        if caller_org and (staff.get('organization') or '').strip() != caller_org:
            raise HTTPException(403, f'Selected {label} must belong to your site')

    # Server-side duplicate subject-ID guard (scoped to the trial) — the client
    # warns optimistically, but the DB is the source of truth.
    if body.subject_id:
        dup = await db.patients.find_one(
            {'trial_id': body.trial_id, 'subject_id': body.subject_id},
            {'_id': 0, 'id': 1})
        if dup:
            raise HTTPException(409, f'Subject ID {body.subject_id} already exists in this trial')
    pid = str(uuid.uuid4())
    values = body.dict()
    values['pi_id'] = pi_id
    values['crc_id'] = crc_id
    doc = {
        'id': pid, **values,
        'created_by': user['id'],
        'created_at': now(),
        'enrolled_date': body.enrolled_date or now().date().isoformat(),
        'completed_visit_ids': [],
        'avatar_initials': patient_initials(body.avatar_initials, body.full_name),
    }
    await db.patients.insert_one(doc)
    created = await materialize_visit_instances(doc)
    await write_audit(user, 'patient.enroll',
                      f"Enrolled {doc['full_name']} in trial {doc['trial_id']} "
                      f"({created} visit instance(s) materialized)",
                      target_id=pid, trial_id=doc['trial_id'])
    return serialize(doc)


@api.get('/patients/invite/check-availability',
         dependencies=[Depends(require_roles('pi', 'crc', 'smo', 'site'))])
async def check_patient_invitation_availability(
    trial_id: str,
    subject_id: Optional[str] = None,
    email: Optional[str] = None,
    user=Depends(current_user),
):
    """Validate patient invite identifiers before the staff member submits."""
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to enroll patients in this trial')

    result = {'subject_id': None, 'email': None}
    if subject_id and subject_id.strip():
        normalized_subject = subject_id.strip()
        patient_exists = await db.patients.find_one(
            {'trial_id': trial_id, 'subject_id': normalized_subject}, {'_id': 0, 'id': 1})
        invite_exists = await db.invitations.find_one(
            {'trial_id': trial_id, 'status': {'$in': ['pending', 'accepting']},
             'patient_data.subject_id': normalized_subject},
            {'_id': 0, 'id': 1})
        result['subject_id'] = {
            'available': not bool(patient_exists or invite_exists),
            'message': (
                f'{normalized_subject} is already enrolled or has a pending invitation for this trial.'
                if patient_exists or invite_exists else ''
            ),
        }
    if email and email.strip():
        normalized_email = email.strip().lower()
        user_exists = await db.users.find_one({'email': normalized_email}, {'_id': 0, 'id': 1})
        invite_exists = await db.invitations.find_one(
            {'email': normalized_email, 'trial_id': trial_id,
             'status': {'$in': ['pending', 'accepting']}},
            {'_id': 0, 'id': 1})
        result['email'] = {
            'available': not bool(user_exists or invite_exists),
            'message': (
                'This email already belongs to an account or has a pending patient invitation.'
                if user_exists or invite_exists else ''
            ),
        }
    return result


@api.post('/patients/invite', dependencies=[Depends(require_roles('pi', 'crc', 'smo', 'site'))])
async def invite_patient_for_enrollment(body: PatientInvitationIn, user=Depends(current_user)):
    """Invite a patient to register, then enrol them only after acceptance."""
    email = normalize_email(body.email)
    phone = normalize_phone(body.phone)
    if not phone:
        raise HTTPException(400, 'Phone number is required')

    trial = await db.trials.find_one({'id': body.trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to enroll patients in this trial')

    caller_org = (user.get('organization') or '').strip()
    if user['role'] in ('smo', 'site') and not user.get('org_admin'):
        raise HTTPException(403, 'Organization-admin access is required to enroll patients')
    pi_id = user['id'] if user['role'] == 'pi' else body.pi_id
    crc_id = user['id'] if user['role'] == 'crc' else body.crc_id

    async def default_trial_staff(role: str, field: str) -> Optional[str]:
        existing = await db.patients.find_one(
            {'trial_id': body.trial_id, field: {'$exists': True, '$ne': None}},
            {'_id': 0, field: 1}, sort=[('created_at', -1)])
        if existing and existing.get(field):
            return existing[field]
        if caller_org:
            colleague = await db.users.find_one(
                {'role': role, 'organization': caller_org,
                 'status': {'$nin': ['Inactive', 'Removed', 'Suspended']}},
                {'_id': 0, 'id': 1}, sort=[('created_at', 1)])
            if colleague:
                return colleague['id']
        return None

    if not pi_id:
        pi_id = await default_trial_staff('pi', 'pi_id')
    if not crc_id:
        crc_id = await default_trial_staff('crc', 'crc_id')
    if user['role'] in ('smo', 'site') and not pi_id:
        raise HTTPException(400, 'Select the PI responsible for this patient')
    for staff_id, expected_role, label in (
        (pi_id, 'pi', 'PI'),
        (crc_id, 'crc', 'CRC'),
    ):
        if not staff_id:
            continue
        staff = await db.users.find_one(
            {'id': staff_id}, {'_id': 0, 'role': 1, 'organization': 1})
        if not staff or staff.get('role') != expected_role:
            raise HTTPException(400, f'Selected {label} is invalid')
        if caller_org and (staff.get('organization') or '').strip() != caller_org:
            raise HTTPException(403, f'Selected {label} must belong to your site')

    if body.subject_id:
        duplicate_patient = await db.patients.find_one(
            {'trial_id': body.trial_id, 'subject_id': body.subject_id},
            {'_id': 0, 'id': 1})
        duplicate_invite = await db.invitations.find_one(
            {'trial_id': body.trial_id, 'status': 'pending',
             'patient_data.subject_id': body.subject_id},
            {'_id': 0, 'id': 1})
        if duplicate_patient or duplicate_invite:
            raise HTTPException(409, f'Subject ID {body.subject_id} already exists or is awaiting registration in this trial')
    contact_matches = [{'phone': phone}]
    if email:
        contact_matches.append({'email': email})
    existing_invite = await db.invitations.find_one(
        {'$or': contact_matches, 'trial_id': body.trial_id, 'role': 'patient',
         'status': {'$in': ['pending', 'accepting']}},
        {'_id': 0, 'id': 1})
    if existing_invite:
        raise HTTPException(
            409,
            'A pending patient invitation already exists for this phone number or email and trial',
        )

    patient_data = body.dict()
    patient_data['email'] = email or ''
    patient_data['phone'] = phone
    patient_data['pi_id'] = pi_id
    patient_data['crc_id'] = crc_id
    token = new_invite_code()
    invitation = {
        'id': str(uuid.uuid4()), 'token': token,
        'email': email or '', 'phone': phone,
        'full_name': body.full_name, 'designation': '', 'role': 'patient',
        'trial_id': body.trial_id, 'invited_by': user['id'],
        'org': caller_org, 'site': '',
        'inviter_name': user.get('full_name') or '',
        'inviter_organization': caller_org,
        'status': 'pending', 'created_at': now(),
        'expires_at': now() + timedelta(days=INVITE_TTL_DAYS),
        'resend_count': 0, 'patient_data': patient_data,
    }
    await db.invitations.insert_one(invitation)
    if email:
        try:
            await run_in_threadpool(
                otp_service.send_invitation_email,
                email,
                _invite_link(token),
                invitation['full_name'],
                invitation['inviter_name'],
                invitation['inviter_organization'],
            )
        except (otp_service.OTPConfigError, otp_service.OTPDeliveryError):
            await db.invitations.delete_one({'id': invitation['id']})
            raise HTTPException(502, 'The patient invitation email could not be delivered.')
    await write_audit(user, 'patient.invite',
                      f"Invited {body.full_name} to register for trial {body.trial_id}",
                      target_id=invitation['id'], trial_id=body.trial_id)
    return {
        **serialize(invitation),
        'invite_link': _invite_link(token),
        'message': (
            'Patient invitation sent by email.' if email
            else 'Patient invitation created. Share the invitation code with the patient.'
        ),
    }


@api.get('/patients/{patient_id}')
async def get_patient(patient_id: str, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    """Patient detail: the patient record + its trial + its visit instances."""
    p = await _require_patient(user, patient_id)
    trial = await db.trials.find_one({'id': p.get('trial_id')}, {'_id': 0})
    raw_instances = await db.visit_instances.find({'patient_id': patient_id}, {'_id': 0}) \
                                            .sort('seq', 1).to_list(500)
    instances = [await _ensure_visit_instance_workflow(row) for row in raw_instances]
    return {**p, 'trial': trial, 'instances': instances}

@api.get('/patients/{patient_id}/visits')
async def get_patient_visits(patient_id: str, user=Depends(require_roles('sponsor', 'cro', 'pi', 'crc'))):
    await _require_patient(user, patient_id)
    rows = await db.visit_instances.find({'patient_id': patient_id}, {'_id': 0}) \
                                   .sort('seq', 1).to_list(500)
    return [await _ensure_visit_instance_workflow(row) for row in rows]

# ── Organizations directory ─────────────────────────────────────────────────
@api.get('/organizations')
async def list_organizations(type: Optional[str] = None, search: Optional[str] = None,
                             include_platform_contact: bool = False):
    """Public directory of known organizations (used by the register screen).

    Directory searches never include staff contact data. The deprecated
    ``include_platform_contact`` parameter is accepted for client compatibility
    but intentionally ignored; callers must use the exact organization contact
    endpoint after selecting a specific organization.
    """
    q: Dict = {}
    if type:
        q['type'] = type
    if search and search.strip():
        q['name'] = {'$regex': re.escape(search.strip()), '$options': 'i'}
    organizations = await db.organizations.find(q, {'_id': 0}).sort('name', 1).to_list(200)
    return organizations


async def _enforce_places_rate_limit(request: Request):
    """Limit public Places proxy traffic per client without retaining raw IPs."""
    raw_client = request.client.host if request.client else "unknown"
    client_key = hashlib.sha256(raw_client.encode("utf-8")).hexdigest()[:24]
    key = f"places:{client_key}"
    n = now()
    window_seconds = 60
    limit = max(10, int(os.environ.get("GOOGLE_PLACES_REQUESTS_PER_MINUTE", "60")))
    try:
        doc = await db.public_api_throttle.find_one({"_id": key})
        if doc and (n - doc["window_start"]).total_seconds() < window_seconds:
            if doc["count"] >= limit:
                raise HTTPException(429, "Too many address searches. Please wait a moment and try again.")
            await db.public_api_throttle.update_one({"_id": key}, {"$inc": {"count": 1}})
        else:
            await db.public_api_throttle.replace_one(
                {"_id": key},
                {"_id": key, "count": 1, "window_start": n,
                 "expires_at": n + timedelta(seconds=window_seconds)},
                upsert=True,
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Address assistance should keep working during a transient throttle-store
        # issue; Google Cloud's quota remains the hard spending backstop.
        logging.warning("Places rate-limit store unavailable: %s", exc)


@api.get('/public/places/hospitals/autocomplete')
async def hospital_place_autocomplete(
    request: Request,
    input: str = Query(min_length=2, max_length=120),
    session_token: str = Query(min_length=1, max_length=36),
):
    await _enforce_places_rate_limit(request)
    try:
        predictions = await run_in_threadpool(
            google_places.autocomplete_hospitals, input, session_token)
        return {"predictions": predictions}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except google_places.PlacesNotConfigured:
        raise HTTPException(503, "Hospital address search is not configured")
    except google_places.PlacesUpstreamError:
        raise HTTPException(502, "Hospital address search is temporarily unavailable")


@api.get('/public/places/hospitals/{place_id}')
async def hospital_place_details(
    place_id: str,
    request: Request,
    session_token: str = Query(min_length=1, max_length=36),
):
    await _enforce_places_rate_limit(request)
    try:
        return await run_in_threadpool(
            google_places.place_address, place_id, session_token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except google_places.PlacesNotConfigured:
        raise HTTPException(503, "Hospital address search is not configured")
    except google_places.PlacesUpstreamError:
        raise HTTPException(502, "Hospital address search is temporarily unavailable")


@api.get('/public/places/organizations/autocomplete')
async def organization_place_autocomplete(
    request: Request,
    input: str = Query(min_length=2, max_length=120),
    session_token: str = Query(min_length=1, max_length=36),
):
    await _enforce_places_rate_limit(request)
    try:
        predictions = await run_in_threadpool(
            google_places.autocomplete_organizations, input, session_token)
        return {"predictions": predictions}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except google_places.PlacesNotConfigured:
        raise HTTPException(503, "Organization search is not configured")
    except google_places.PlacesUpstreamError:
        raise HTTPException(502, "Organization search is temporarily unavailable")


@api.get('/public/places/organizations/{place_id}')
async def organization_place_details(
    place_id: str,
    request: Request,
    session_token: str = Query(min_length=1, max_length=36),
):
    await _enforce_places_rate_limit(request)
    try:
        return await run_in_threadpool(
            google_places.place_address, place_id, session_token)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except google_places.PlacesNotConfigured:
        raise HTTPException(503, "Organization search is not configured")
    except google_places.PlacesUpstreamError:
        raise HTTPException(502, "Organization search is temporarily unavailable")


async def _public_platform_admin_contact() -> Optional[dict]:
    """Return the configured MTB Platform Administrator contact only.

    Registration duplicate warnings must never disclose PI, CRC, investigator,
    or organization-member details, even when that member is an org admin.
    """
    config = await app_config()
    email = config.get('support_email') or ''
    phone = config.get('support_phone') or ''
    if not email and not phone:
        return None
    return {
        'name': 'MTB Platform Support',
        'designation': 'Platform Administrator',
        'email': email,
        'phone': phone,
    }


@api.get('/organizations/registration-check')
async def organization_registration_check(
    name: str,
    google_place_id: Optional[str] = Query(default=None, max_length=255),
):
    """Authoritative duplicate check used at the end of org registration.

    A Google Place ID is authoritative when supplied; exact normalized name is
    the fallback for manual entry and older organizations. Contact information
    is always the configured MTB Platform Administrator, never organization
    members such as PIs, CRCs, investigators, or organization admins.
    """
    place_id = str(google_place_id or '').strip()
    if place_id and not google_places.PLACE_ID_RE.fullmatch(place_id):
        raise HTTPException(400, 'Invalid Google Place ID')
    organization = await find_existing_organization(name, place_id)
    if not organization:
        return {'exists': False, 'organization': None, 'platform_contact': None}
    public_organization = {
        'id': organization.get('id') or '',
        'name': organization.get('name') or '',
        'type': organization.get('type') or '',
    }
    return {
        'exists': True,
        'organization': public_organization,
        'platform_contact': await _public_platform_admin_contact(),
    }


@api.get('/organizations/{org_id}/platform-contact')
async def organization_platform_contact(org_id: str):
    """Exact-match registration confirmation contract for an existing org."""
    organization = await db.organizations.find_one(
        {'id': org_id, 'status': {'$ne': 'merged'}},
        {'_id': 0, 'id': 1, 'name': 1, 'type': 1, 'status': 1,
         'email': 1, 'contact': 1, 'contact_name': 1},
    )
    if not organization:
        raise HTTPException(404, 'Organization not found')
    return {
        'organization': {
            'id': organization['id'],
            'name': organization.get('name') or '',
            'type': organization.get('type') or '',
        },
        'platform_contact': await _public_platform_admin_contact(),
    }

# ── Notifications ───────────────────────────────────────────────────────────
@api.get('/notifications')
async def my_notifications(user=Depends(current_user)):
    items = await db.notifications.find({'user_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(100)
    return items

@api.get('/notifications/unread-count')
async def unread_notification_count(user=Depends(current_user)):
    count = await db.notifications.count_documents({'user_id': user['id'], 'read': {'$ne': True}})
    return {'count': count}

@api.post('/notifications/read-all')
async def mark_all_notifications_read(user=Depends(current_user)):
    r = await db.notifications.update_many(
        {'user_id': user['id'], 'read': {'$ne': True}}, {'$set': {'read': True}})
    await write_audit(user, 'notifications.read_all',
                      f'Marked {r.modified_count} notification(s) as read')
    return {'ok': True, 'count': r.modified_count}

@api.post('/notifications/{nid}/read')
async def mark_read(nid: str, user=Depends(current_user)):
    await db.notifications.update_one({'id': nid, 'user_id': user['id']}, {'$set': {'read': True}})
    return {'ok': True}

# ── Conversations & Messages ────────────────────────────────────────────────
async def _require_conversation_member(cid: str, user_id: str) -> dict:
    """Load a conversation and fail closed unless the caller participates."""
    conv = await db.conversations.find_one({'id': cid}, {'_id': 0})
    if not conv:
        raise HTTPException(404, 'Conversation not found')
    if user_id not in conv.get('participant_ids', []):
        raise HTTPException(403, 'You are not a participant in this conversation')
    return conv


async def _patient_chat_assignment(patient_user_id: str, staff_user_id: str) -> bool:
    """Patients may chat only with the PI/CRC assigned to their enrollment."""
    patient_user = await db.users.find_one(
        {'id': patient_user_id}, {'_id': 0, 'email': 1})
    if not patient_user:
        return False
    patient = await db.patients.find_one({
        '$and': [
            {'$or': [
                {'user_id': patient_user_id},
                {'user_id': {'$exists': False}, 'email': patient_user.get('email')},
                {'user_id': None, 'email': patient_user.get('email')},
            ]},
            {'$or': [{'pi_id': staff_user_id}, {'crc_id': staff_user_id}]},
        ],
    }, {'_id': 0, 'id': 1})
    return patient is not None


async def _chat_trial_ids(user: dict) -> set:
    """Trials that establish a clinical working relationship for a staff user."""
    ids = set()
    uid = user['id']
    role = user.get('role')
    org = (user.get('organization') or '').strip()

    if role in ('pi', 'crc'):
        key = 'pi_id' if role == 'pi' else 'crc_id'
        ids.update(await db.patients.distinct('trial_id', {key: uid}))

    ids.update(await db.trials.distinct('id', {'created_by': uid}))

    if role in ('sponsor', 'cro') and org:
        ids.update(await db.trials.distinct('id', {'sponsor_name': org}))

    if role in ('smo', 'site') and org:
        member_ids = await db.users.distinct('id', {'organization': org})
        ids.update(await db.trials.distinct('id', {'created_by': {'$in': member_ids}}))
        ids.update(await db.patients.distinct('trial_id', {
            '$or': [
                {'pi_id': {'$in': member_ids}},
                {'crc_id': {'$in': member_ids}},
                {'created_by': {'$in': member_ids}},
            ],
        }))

    invitation_contacts = []
    if (user.get('email') or '').strip():
        invitation_contacts.append({'email': user['email'].strip().lower()})
    if (user.get('phone') or '').strip():
        invitation_contacts.append({'phone': user['phone'].strip()})
    if invitation_contacts:
        invitation_ids = await db.invitations.distinct('trial_id', {
            'status': 'accepted', '$or': invitation_contacts,
        })
        ids.update(tid for tid in invitation_ids if tid)

    if org:
        organization = await db.organizations.find_one(
            {'name': org}, {'_id': 0, 'id': 1})
        if organization:
            ids.update(await db.org_trial_access.distinct('trial_id', {
                'org_id': organization['id'], 'granted': True,
            }))
    return {tid for tid in ids if tid}


async def _can_chat_with(user: dict, other: dict) -> bool:
    """Allow chat only inside an organization or an assigned trial relationship."""
    if user['id'] == other['id'] or other.get('status') == 'Suspended':
        return False

    user_org = (user.get('organization') or '').strip().casefold()
    other_org = (other.get('organization') or '').strip().casefold()
    if user_org and user_org == other_org:
        return True

    if user.get('role') == 'patient':
        return await _patient_chat_assignment(user['id'], other['id'])
    if other.get('role') == 'patient':
        return await _patient_chat_assignment(other['id'], user['id'])

    return bool(await _chat_trial_ids(user) & await _chat_trial_ids(other))


def _cleared_at(conv: dict, uid: str):
    return (conv.get('cleared_at') or {}).get(uid)


def _visible_messages_filter(cid: str, conv: dict, uid: str) -> dict:
    """Message query filter honoring this caller's per-user 'clear messages'
    boundary — messages sent before their last clear stay hidden for them
    only; everyone else's history is untouched."""
    flt: dict = {'conversation_id': cid}
    cleared = _cleared_at(conv, uid)
    if cleared:
        flt['created_at'] = {'$gt': cleared}
    return flt


async def _create_chat_message(cid: str, content: str, sender_id: str,
                               conv: Optional[dict] = None,
                               msg_type: str = 'text',
                               attachment: Optional[dict] = None) -> dict:
    conv = conv or await _require_conversation_member(cid, sender_id)
    clean_content = content.strip()
    if not clean_content and not attachment:
        raise HTTPException(422, 'Message content cannot be blank')
    created_at = now()
    msg = {
        'id': str(uuid.uuid4()), 'conversation_id': cid,
        'sender_id': sender_id, 'content': clean_content,
        'type': msg_type, 'attachment': attachment,
        'created_at': created_at, 'read_by': {sender_id: created_at},
    }
    await db.messages.insert_one(msg)
    preview = clean_content or {
        'image': '📷 Photo', 'document': f"📄 {(attachment or {}).get('name', 'Document')}",
        'voice': '🎤 Voice message',
    }.get(msg_type, clean_content)
    await db.conversations.update_one(
        {'id': cid},
        {'$set': {'last_message': preview, 'updated_at': created_at}})
    return msg


async def _conversation_media_count(cid: str) -> int:
    return await db.messages.count_documents(
        {'conversation_id': cid, 'attachment': {'$ne': None}})


async def _hydrate_conversation_trial(conv: dict) -> dict:
    """Attach {protocol_id, title} for a conversation's linked trial, if any."""
    trial_id = conv.get('trial_id')
    if not trial_id:
        return {}
    trial = await db.trials.find_one(
        {'id': trial_id}, {'_id': 0, 'protocol_id': 1, 'title': 1})
    if not trial:
        return {}
    return {'protocol_id': trial.get('protocol_id'), 'trial_title': trial.get('title')}


@api.get('/conversations')
async def list_conversations(user=Depends(current_user)):
    convs = await db.conversations.find({'participant_ids': user['id']}, {'_id': 0}).sort('updated_at', -1).to_list(200)
    # enrich with other participant info + unread count + caller pin/mute flags
    out = []
    for c in convs:
        unread = await db.messages.count_documents({
            **_visible_messages_filter(c['id'], c, user['id']),
            'sender_id': {'$ne': user['id']}, f'read_by.{user["id"]}': {'$exists': False},
        })
        # last-message attribution for the inbox preview line: who sent it,
        # and whether every other member has read it (drives the ✓✓ tick).
        last = await db.messages.find_one(
            _visible_messages_filter(c['id'], c, user['id']),
            {'_id': 0, 'sender_id': 1, 'read_by': 1},
            sort=[('created_at', -1)])
        last_sender_id = (last or {}).get('sender_id')
        last_recipients = [pid for pid in c.get('participant_ids', []) if pid != last_sender_id]
        last_read = bool(last) and bool(last_recipients) and all(
            pid in ((last or {}).get('read_by') or {}) for pid in last_recipients)
        # other participant for 1-1
        other = None
        participants = None
        if not c.get('is_group'):
            other_id = next((p for p in c['participant_ids'] if p != user['id']), None)
            if other_id:
                other = await db.users.find_one({'id': other_id}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0})
        else:
            rows = await db.users.find(
                {'id': {'$in': c.get('participant_ids', [])}},
                {'_id': 0, 'id': 1, 'full_name': 1, 'role': 1,
                 'organization': 1, 'avatar_initials': 1, 'is_online': 1},
            ).to_list(100)
            participants = rows
        out.append({
            **c, 'unread_count': unread, 'other_participant': other,
            'participants': participants,
            'last_sender_id': last_sender_id, 'last_read': last_read,
            'pinned': user['id'] in (c.get('pinned_by') or []),
            'muted': user['id'] in (c.get('muted_by') or []),
            'archived': user['id'] in (c.get('archived_by') or []),
            'is_admin': c.get('created_by') == user['id'],
        })
    return out


class ConversationFlagsIn(BaseModel):
    pinned: Optional[bool] = None
    muted: Optional[bool] = None
    archived: Optional[bool] = None


@api.post('/conversations/{cid}/flags')
async def set_conversation_flags(cid: str, body: ConversationFlagsIn,
                                 user=Depends(current_user)):
    """Per-user pin/mute/archive stored on the conversation (member-gated)."""
    await _require_conversation_member(cid, user['id'])
    if body.pinned is None and body.muted is None and body.archived is None:
        raise HTTPException(400, 'Provide pinned, muted and/or archived')
    for field, value in (('pinned_by', body.pinned), ('muted_by', body.muted), ('archived_by', body.archived)):
        if value is None:
            continue
        op = {'$addToSet' if value else '$pull': {field: user['id']}}
        await db.conversations.update_one({'id': cid}, op)
    fresh = await db.conversations.find_one({'id': cid}, {'_id': 0})
    return {'ok': True,
            'pinned': user['id'] in (fresh.get('pinned_by') or []),
            'muted': user['id'] in (fresh.get('muted_by') or []),
            'archived': user['id'] in (fresh.get('archived_by') or [])}

@api.post('/conversations')
async def create_conversation(body: ConversationIn, user=Depends(current_user)):
    pids = sorted(set(body.participant_ids + [user['id']]))
    if len(pids) < 2:
        raise HTTPException(400, 'A conversation requires another participant')
    if not body.is_group and len(pids) != 2:
        raise HTTPException(400, 'A direct conversation must have exactly two participants')
    participants = await db.users.find(
        {'id': {'$in': pids}},
        {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0}
    ).to_list(len(pids))
    if len(participants) != len(pids):
        raise HTTPException(404, 'One or more participants were not found')
    for participant in participants:
        if participant['id'] != user['id'] and not await _can_chat_with(user, participant):
            raise HTTPException(
                403,
                'You may only message members of your organization or assigned clinical team')
    if not body.is_group:
        existing = await db.conversations.find_one({'participant_ids': pids, 'is_group': False}, {'_id': 0})
        if existing: return existing
    cid = str(uuid.uuid4())
    doc = {'id': cid, 'participant_ids': pids, 'title': body.title or '', 'is_group': body.is_group,
           'description': body.description or '', 'trial_id': body.trial_id,
           'created_by': user['id'],
           'last_message': '', 'created_at': now(), 'updated_at': now()}
    await db.conversations.insert_one(doc)
    return serialize(doc)


@api.get('/messaging/recipients')
async def list_messaging_recipients(user=Depends(current_user)):
    """Only return people the caller may legally start a conversation with."""
    candidates = await db.users.find(
        {'id': {'$ne': user['id']}, 'status': {'$ne': 'Suspended'}},
        {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0,
         'reset_otp': 0, 'reset_otp_hash': 0},
    ).to_list(500)
    return [
        candidate for candidate in candidates
        if await _can_chat_with(user, candidate)
    ]


@api.get('/messaging/unread-count')
async def messaging_unread_count(user=Depends(current_user)):
    """Total unread messages across every non-archived conversation the caller
    is a member of — powers the Messages tab badge."""
    convs = await db.conversations.find(
        {'participant_ids': user['id']}, {'_id': 0}).to_list(200)
    total = 0
    for c in convs:
        if user['id'] in (c.get('archived_by') or []):
            continue
        total += await db.messages.count_documents({
            **_visible_messages_filter(c['id'], c, user['id']),
            'sender_id': {'$ne': user['id']}, f'read_by.{user["id"]}': {'$exists': False},
        })
    return {'count': total}


@api.get('/conversations/{cid}')
async def get_conversation_detail(cid: str, user=Depends(current_user)):
    """Full dossier for the channel-info screen: roster with role/org/online/
    admin flag, description, linked trial, shared-media count, and this
    caller's notification/archive state."""
    conv = await _require_conversation_member(cid, user['id'])
    rows = await db.users.find(
        {'id': {'$in': conv.get('participant_ids', [])}},
        {'_id': 0, 'id': 1, 'full_name': 1, 'role': 1,
         'organization': 1, 'avatar_initials': 1, 'is_online': 1},
    ).to_list(100)
    for row in rows:
        row['admin'] = row['id'] == conv.get('created_by')
    trial_info = await _hydrate_conversation_trial(conv)
    return {
        **conv, 'participants': rows,
        'media_count': await _conversation_media_count(cid),
        'pinned': user['id'] in (conv.get('pinned_by') or []),
        'muted': user['id'] in (conv.get('muted_by') or []),
        'archived': user['id'] in (conv.get('archived_by') or []),
        'is_admin': conv.get('created_by') == user['id'],
        **trial_info,
    }


@api.post('/conversations/{cid}/members')
async def add_conversation_members(cid: str, body: ConversationMembersIn,
                                   user=Depends(current_user)):
    """Add members to a group conversation (any current member may invite)."""
    conv = await _require_conversation_member(cid, user['id'])
    if not conv.get('is_group'):
        raise HTTPException(400, 'Only group conversations have members to add')
    new_ids = [uid for uid in set(body.user_ids) if uid not in conv.get('participant_ids', [])]
    if not new_ids:
        return serialize(await db.conversations.find_one({'id': cid}, {'_id': 0}))
    candidates = await db.users.find(
        {'id': {'$in': new_ids}}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0}
    ).to_list(len(new_ids))
    if len(candidates) != len(new_ids):
        raise HTTPException(404, 'One or more users were not found')
    for candidate in candidates:
        if not await _can_chat_with(user, candidate):
            raise HTTPException(403, f"You can't add {candidate.get('full_name', 'this user')} to this channel")
    await db.conversations.update_one(
        {'id': cid}, {'$addToSet': {'participant_ids': {'$each': new_ids}}, '$set': {'updated_at': now()}})
    fresh = await db.conversations.find_one({'id': cid}, {'_id': 0})
    for pid in fresh['participant_ids']:
        await manager.send(pid, {'type': 'conversations:changed', 'conversation_id': cid})
    await write_audit(user, 'conversation.add_members', f"Added {len(new_ids)} member(s) to {fresh.get('title') or cid}", target_id=cid)
    return serialize(fresh)


@api.delete('/conversations/{cid}/members/{uid}')
async def remove_conversation_member(cid: str, uid: str, user=Depends(current_user)):
    """Remove a member (self-removal = Leave group; removing someone else
    requires being the channel admin/creator)."""
    conv = await _require_conversation_member(cid, user['id'])
    if not conv.get('is_group'):
        raise HTTPException(400, 'Only group conversations have members to remove')
    if uid != user['id'] and conv.get('created_by') != user['id']:
        raise HTTPException(403, 'Only the channel admin can remove other members')
    if uid not in conv.get('participant_ids', []):
        raise HTTPException(404, 'That person is not in this channel')
    await db.conversations.update_one({'id': cid}, {'$pull': {'participant_ids': uid}, '$set': {'updated_at': now()}})
    fresh = await db.conversations.find_one({'id': cid}, {'_id': 0})
    for pid in fresh.get('participant_ids', []):
        await manager.send(pid, {'type': 'conversations:changed', 'conversation_id': cid})
    action = 'conversation.leave' if uid == user['id'] else 'conversation.remove_member'
    await write_audit(user, action, f"{'Left' if uid == user['id'] else 'Removed a member from'} {fresh.get('title') or cid}", target_id=cid)
    return {'ok': True}


@api.get('/conversations/{cid}/invite-link')
async def get_conversation_invite_link(cid: str, user=Depends(current_user)):
    """Generate (once) and return this group's invite code."""
    conv = await _require_conversation_member(cid, user['id'])
    if not conv.get('is_group'):
        raise HTTPException(400, 'Only group conversations have invite links')
    token = conv.get('invite_token')
    if not token:
        token = uuid.uuid4().hex[:10]
        await db.conversations.update_one({'id': cid}, {'$set': {'invite_token': token}})
    return {'token': token}


@api.post('/conversations/join/{token}')
async def join_conversation_by_invite(token: str, user=Depends(current_user)):
    conv = await db.conversations.find_one({'invite_token': token, 'is_group': True}, {'_id': 0})
    if not conv:
        raise HTTPException(404, 'This invite link is invalid or has expired')
    if user['id'] not in conv.get('participant_ids', []):
        await db.conversations.update_one(
            {'id': conv['id']}, {'$addToSet': {'participant_ids': user['id']}, '$set': {'updated_at': now()}})
        fresh = await db.conversations.find_one({'id': conv['id']}, {'_id': 0})
        for pid in fresh.get('participant_ids', []):
            await manager.send(pid, {'type': 'conversations:changed', 'conversation_id': conv['id']})
        conv = fresh
    return serialize(conv)


@api.get('/conversations/{cid}/files')
async def list_conversation_files(cid: str, user=Depends(current_user)):
    """Shared files & media filmstrip — every attachment ever posted in this
    conversation, newest first."""
    await _require_conversation_member(cid, user['id'])
    rows = await db.messages.find(
        {'conversation_id': cid, 'attachment': {'$ne': None}},
        {'_id': 0, 'id': 1, 'sender_id': 1, 'created_at': 1, 'type': 1, 'attachment': 1},
    ).sort('created_at', -1).to_list(200)
    out = []
    for row in rows:
        att = row.get('attachment') or {}
        out.append({
            'message_id': row['id'], 'sender_id': row['sender_id'], 'created_at': row['created_at'],
            'type': row.get('type'), 'file_id': att.get('file_id'), 'name': att.get('name'),
            'size': att.get('size'), 'content_type': att.get('content_type'),
            'url': f"/api/files/{att.get('file_id')}" if att.get('file_id') else None,
        })
    return serialize(out)


@api.post('/conversations/{cid}/clear')
async def clear_conversation_messages(cid: str, user=Depends(current_user)):
    """'Clear messages' — hides this caller's history up to now; everyone
    else's copy of the conversation is untouched."""
    await _require_conversation_member(cid, user['id'])
    at = now()
    await db.conversations.update_one({'id': cid}, {'$set': {f'cleared_at.{user["id"]}': at}})
    return {'ok': True, 'cleared_at': iso(at)}


@api.post('/conversations/{cid}/report')
async def report_conversation(cid: str, body: ConversationReportIn, user=Depends(current_user)):
    """Report group — files a support ticket platform admins can triage."""
    conv = await _require_conversation_member(cid, user['id'])
    n = now()
    ticket_id = f"#TKT-{n.strftime('%Y%m%d')}-{str(uuid.uuid4().int)[:4]}"
    doc = {
        'id': str(uuid.uuid4()), 'ticket_id': ticket_id, 'user_id': user['id'],
        'category': 'conversation_report',
        'subject': f"Reported: {conv.get('title') or 'Conversation'}",
        'description': (body.reason or '').strip() or 'No reason provided.',
        'status': 'Open', 'created_at': n, 'conversation_id': cid,
    }
    await db.support_tickets.insert_one(doc)
    await write_audit(user, 'conversation.report', f"Reported {conv.get('title') or cid}", target_id=cid)
    return {'ok': True, 'ticket_id': ticket_id}


@api.patch('/conversations/{cid}/settings')
async def update_conversation_settings(cid: str, body: ConversationSettingsIn, user=Depends(current_user)):
    """Channel-wide settings: auto-delete timer, and (group only) rename/
    description edits. Admin/creator only."""
    conv = await _require_conversation_member(cid, user['id'])
    if conv.get('created_by') != user['id']:
        raise HTTPException(403, 'Only the channel admin can change these settings')
    update: dict = {'auto_delete_days': body.auto_delete_days, 'updated_at': now()}
    if body.title is not None:
        if not conv.get('is_group'):
            raise HTTPException(400, 'Only group conversations can be renamed')
        update['title'] = body.title.strip()
    if body.description is not None:
        if not conv.get('is_group'):
            raise HTTPException(400, 'Only group conversations have a description')
        update['description'] = body.description.strip()
    await db.conversations.update_one({'id': cid}, {'$set': update})
    return {'ok': True, **update, 'updated_at': iso(update['updated_at'])}


@api.get('/conversations/{cid}/messages')
async def get_messages(cid: str, user=Depends(current_user)):
    conv = await _require_conversation_member(cid, user['id'])
    msgs = await db.messages.find(
        _visible_messages_filter(cid, conv, user['id']), {'_id': 0}
    ).sort('created_at', 1).to_list(500)
    # mark all as read
    await db.messages.update_many(
        {'conversation_id': cid, 'sender_id': {'$ne': user['id']}},
        {'$set': {f'read_by.{user["id"]}': now()}}
    )
    return msgs


@api.post('/conversations/{cid}/messages')
async def post_message(cid: str, body: ChatMessageIn,
                       user=Depends(current_user)):
    conv = await _require_conversation_member(cid, user['id'])
    attachment = body.attachment.model_dump() if body.attachment else None
    msg = await _create_chat_message(cid, body.content, user['id'], conv,
                                     msg_type=body.type, attachment=attachment)
    out = {**serialize(msg), 'type': 'message'}
    for pid in conv['participant_ids']:
        await manager.send(pid, out)
    return out


@api.post('/conversations/{cid}/read')
async def mark_conversation_read(cid: str, user=Depends(current_user)):
    conv = await _require_conversation_member(cid, user['id'])
    read_at = now()
    result = await db.messages.update_many(
        {'conversation_id': cid, 'sender_id': {'$ne': user['id']},
         f'read_by.{user["id"]}': {'$exists': False}},
        {'$set': {f'read_by.{user["id"]}': read_at}})
    event = {
        'type': 'read', 'conversation_id': cid,
        'user_id': user['id'], 'read_at': iso(read_at),
    }
    for pid in conv['participant_ids']:
        if pid != user['id']:
            await manager.send(pid, event)
    return {'ok': True, 'count': result.modified_count, 'read_at': iso(read_at)}

# ── Users (directory) ───────────────────────────────────────────────────────
@api.get('/users')
async def list_users(user=Depends(current_user)):
    users = await db.users.find({}, {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0, 'reset_otp': 0}).to_list(500)
    return [u for u in users if u['id'] != user['id']]

TEAM_ROLES = ['pi', 'crc', 'sponsor', 'cro', 'smo', 'site']

def _can_manage_team_member(user: dict, member: dict) -> bool:
    return bool(
        user.get('org_admin')
        and user.get('id') != member.get('id')
        and (user.get('organization') or '').strip()
        and (user.get('organization') or '').strip()
            == (member.get('organization') or '').strip()
    )

@api.get('/team')
async def list_team(user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro', 'smo', 'site'))):
    """Org- and trial-scoped clinical team for the caller — NOT the whole user
    directory. A member qualifies when they either share the caller's
    organization or collaborate on a trial the caller is connected to (its
    creator/sponsor, or the PI/CRC of any patient enrolled in it). Patients and
    unrelated accounts are never included."""
    org = (user.get('organization') or '').strip()

    # Trials the caller is connected to: ones they created / sponsor, plus ones
    # they staff as PI/CRC on a patient record.
    trial_ids: set = set()
    trial_or = [{'created_by': user['id']}] + ([{'sponsor_name': org}] if org else [])
    async for t in db.trials.find({'$or': trial_or}, {'_id': 0, 'id': 1}):
        trial_ids.add(t['id'])
    async for p in db.patients.find(
            {'$or': [{'pi_id': user['id']}, {'crc_id': user['id']}]},
            {'_id': 0, 'trial_id': 1}):
        if p.get('trial_id'):
            trial_ids.add(p['trial_id'])

    # Collaborator user-ids on those trials.
    collaborator_ids: set = set()
    # A CRC invited by a PI belongs to that PI's team even before either
    # person is assigned to a patient.
    if user.get('role') == 'pi':
        async for member in db.users.find(
                {'supervising_pi_id': user['id']}, {'_id': 0, 'id': 1}):
            collaborator_ids.add(member['id'])
    elif user.get('supervising_pi_id'):
        collaborator_ids.add(user['supervising_pi_id'])
    if trial_ids:
        tid_list = list(trial_ids)
        async for t in db.trials.find({'id': {'$in': tid_list}}, {'_id': 0, 'created_by': 1}):
            if t.get('created_by'):
                collaborator_ids.add(t['created_by'])
        async for p in db.patients.find({'trial_id': {'$in': tid_list}},
                                        {'_id': 0, 'pi_id': 1, 'crc_id': 1}):
            for k in ('pi_id', 'crc_id'):
                if p.get(k):
                    collaborator_ids.add(p[k])

    ors = []
    if org:
        ors.append({'organization': org})
    if collaborator_ids:
        ors.append({'id': {'$in': list(collaborator_ids)}})
    if not ors:
        return []
    members = await db.users.find(
        {
            'role': {'$in': TEAM_ROLES},
            'status': {'$nin': ['Inactive', 'Removed', 'Suspended']},
            '$or': ors,
        },
        {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0, 'reset_otp': 0}
    ).to_list(500)
    # Supply the compact team directory with the trials each person is involved
    # in, so the mobile client can reveal them on demand without an extra call.
    member_ids = {member['id'] for member in members}
    member_trials: Dict[str, set] = {member_id: set() for member_id in member_ids}
    if member_ids:
        async for trial in db.trials.find(
            {}, {'_id': 0, 'id': 1, 'protocol_id': 1, 'title': 1, 'created_by': 1, 'sponsor_name': 1}
        ):
            trial_id = trial.get('id')
            if not trial_id:
                continue
            creator_id = trial.get('created_by')
            if creator_id in member_trials:
                member_trials[creator_id].add(trial_id)
            sponsor_name = (trial.get('sponsor_name') or '').strip()
            if sponsor_name:
                for member in members:
                    if (member.get('organization') or '').strip() == sponsor_name:
                        member_trials[member['id']].add(trial_id)

        async for patient in db.patients.find(
            {'$or': [{'pi_id': {'$in': list(member_ids)}}, {'crc_id': {'$in': list(member_ids)}}]},
            {'_id': 0, 'trial_id': 1, 'pi_id': 1, 'crc_id': 1},
        ):
            for key in ('pi_id', 'crc_id'):
                member_id = patient.get(key)
                if member_id in member_trials and patient.get('trial_id'):
                    member_trials[member_id].add(patient['trial_id'])

    trial_labels: Dict[str, str] = {}
    assigned_trial_ids = {trial_id for trial_ids in member_trials.values() for trial_id in trial_ids}
    if assigned_trial_ids:
        async for trial in db.trials.find(
            {'id': {'$in': list(assigned_trial_ids)}}, {'_id': 0, 'id': 1, 'protocol_id': 1, 'title': 1}
        ):
            trial_labels[trial['id']] = trial.get('protocol_id') or trial.get('title') or 'Trial'
    return [
        {
            **member,
            'designation': (
                member.get('designation')
                or (member.get('profile') or {}).get('designation')
                or ''
            ),
            'capabilities': {
                'can_edit': _can_manage_team_member(user, member),
                'can_remove': _can_manage_team_member(user, member),
            },
            'trials': [
                {'id': trial_id, 'label': trial_labels.get(trial_id, 'Trial')}
                for trial_id in sorted(member_trials.get(member['id'], set()), key=lambda item: trial_labels.get(item, item))
            ],
        }
        for member in members if member['id'] != user['id']
    ]

# ── WebSocket chat ──────────────────────────────────────────────────────────
async def _manageable_team_member(user: dict, member_id: str) -> dict:
    member = await db.users.find_one(
        {'id': member_id, 'role': {'$in': TEAM_ROLES}},
        {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0},
    )
    if not member:
        raise HTTPException(404, 'Organization member not found')
    scoped = await list_team(user)
    if not any(row.get('id') == member_id for row in scoped):
        raise HTTPException(403, 'This organization member is outside your scope')
    if not _can_manage_team_member(user, member):
        raise HTTPException(403, 'Organization admin permission is required')
    return member

async def _ensure_pi_remains(member: dict, next_role: Optional[str] = None):
    if member.get('role') != 'pi' or next_role == 'pi':
        return
    remaining = await db.users.count_documents({
        'organization': member.get('organization'),
        'role': 'pi',
        'id': {'$ne': member['id']},
        'status': {'$nin': ['Inactive', 'Removed', 'Suspended']},
    })
    if remaining < 1:
        raise HTTPException(409, 'At least one active PI must remain in the organization')

@api.patch('/team/{member_id}')
async def patch_team_member(
    member_id: str,
    body: TeamMemberPatchIn,
    user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro', 'smo', 'site')),
):
    member = await _manageable_team_member(user, member_id)
    values = {key: value for key, value in body.dict().items() if value is not None}
    if not values:
        raise HTTPException(400, 'No organization member changes supplied')
    if 'role' in values:
        values['role'] = values['role'].strip().lower()
        if values['role'] not in TEAM_ROLES:
            raise HTTPException(400, 'Unsupported team role')
        await _ensure_pi_remains(member, values['role'])
    updates = {}
    for field in ('full_name', 'phone', 'role'):
        if field in values:
            updates[field] = values[field].strip()
    if 'designation' in values:
        updates['profile.designation'] = values['designation'].strip()
    updates.update({
        'updated_at': now(),
        'updated_by': user['id'],
        'updated_by_name': user.get('full_name') or '',
    })
    await db.users.update_one({'id': member_id}, {'$set': updates})
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()),
        'user_id': member_id,
        'title': 'Team profile updated',
        'body': f"{user.get('full_name') or 'Your organization admin'} updated your organization member profile.",
        'type': 'team',
        'read': False,
        'created_at': now(),
    })
    await write_audit(
        user,
        'team.member_update',
        f"Updated organization member {member.get('full_name') or member_id}",
        target_id=member_id,
        changes=values,
    )
    fresh = await db.users.find_one(
        {'id': member_id},
        {'_id': 0, 'hashed_password': 0, 'security_answer_hash': 0},
    )
    return serialize({
        **fresh,
        'designation': (fresh.get('profile') or {}).get('designation') or '',
        'capabilities': {'can_edit': True, 'can_remove': True},
    })

@api.delete('/team/{member_id}')
async def remove_team_member(
    member_id: str,
    user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro', 'smo', 'site')),
):
    member = await _manageable_team_member(user, member_id)
    await _ensure_pi_remains(member, None)
    removed_at = now()
    await db.notifications.insert_one({
        'id': str(uuid.uuid4()),
        'user_id': member_id,
        'title': 'Organization access removed',
        'body': f"{user.get('full_name') or 'Your organization admin'} removed your organization access.",
        'type': 'team',
        'read': False,
        'created_at': removed_at,
    })
    await db.users.update_one(
        {'id': member_id},
        {'$set': {
            'status': 'Suspended',
            'removed_from_team_at': removed_at,
            'removed_from_team_by': user['id'],
            'force_logout_at': removed_at,
        }},
    )
    await write_audit(
        user,
        'team.member_remove',
        f"Removed organization member {member.get('full_name') or member_id}",
        target_id=member_id,
    )
    return {'removed': True, 'member_id': member_id}

class WSManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}

    async def connect(self, ws: WebSocket, user_id: str):
        await ws.accept()
        self.connections[user_id] = ws
        await db.users.update_one({'id': user_id}, {'$set': {'is_online': True, 'last_seen': now()}})

    def disconnect(self, user_id: str):
        self.connections.pop(user_id, None)

    async def send(self, user_id: str, payload: dict):
        ws = self.connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(payload, default=str))
            except Exception:
                self.disconnect(user_id)

manager = WSManager()

@app.websocket('/api/ws')
async def ws_endpoint(websocket: WebSocket, token: str = Query(...)):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGO])
        if payload.get('kind') != 'access':
            raise jwt.InvalidTokenError()
        user_id = payload['sub']
    except jwt.PyJWTError:
        await websocket.close(code=1008); return
    user = await db.users.find_one(
        {'id': user_id}, {'_id': 0, 'status': 1})
    if not user or user.get('status') == 'Suspended':
        await websocket.close(code=1008); return

    await manager.connect(websocket, user_id)
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get('type')

            if event == 'message':
                cid = data['conversation_id']
                conv = await db.conversations.find_one(
                    {'id': cid, 'participant_ids': user_id}, {'_id': 0})
                if not conv:
                    continue
                try:
                    msg = await _create_chat_message(
                        cid, str(data.get('content') or ''), user_id, conv)
                except HTTPException:
                    continue
                out = {**serialize(msg), 'type': 'message'}
                for pid in conv['participant_ids']:
                    await manager.send(pid, out)

            elif event == 'typing':
                cid = data['conversation_id']
                conv = await db.conversations.find_one(
                    {'id': cid, 'participant_ids': user_id}, {'_id': 0})
                if not conv:
                    continue
                for pid in conv['participant_ids']:
                    if pid != user_id:
                        await manager.send(pid, {'type': 'typing', 'conversation_id': cid, 'user_id': user_id})

            elif event == 'read':
                cid = data['conversation_id']
                conv = await db.conversations.find_one(
                    {'id': cid, 'participant_ids': user_id}, {'_id': 0})
                if not conv:
                    continue
                await db.messages.update_many(
                    {'conversation_id': cid, 'sender_id': {'$ne': user_id}},
                    {'$set': {f'read_by.{user_id}': now()}}
                )
                for pid in conv['participant_ids']:
                    if pid != user_id:
                        await manager.send(pid, {'type': 'read', 'conversation_id': cid, 'user_id': user_id})
    except WebSocketDisconnect:
        manager.disconnect(user_id)
        await db.users.update_one({'id': user_id}, {'$set': {'is_online': False, 'last_seen': now()}})

# ── Seed demo data ──────────────────────────────────────────────────────────
# Every seeded document is keyed on a stable natural key (email, protocol_id,
# (trial_id, visit_number), (medication_id, date, time), …) and upserted, so
# POST /api/seed can run any number of times without duplicating rows and
# without wiping non-seed user data.
SEED_PASSWORD = 'Password1!'

async def _seed_upsert(coll, key: Dict, insert: Optional[Dict] = None,
                       update: Optional[Dict] = None) -> Dict:
    """Upsert one seed doc keyed on `key`. `insert` fields apply only on first
    creation ($setOnInsert — existing rows are never overwritten); `update`
    fields are refreshed on every run ($set). Returns the stored document."""
    ops: Dict = {'$setOnInsert': {'id': str(uuid.uuid4()), **(insert or {})}}
    if update:
        ops['$set'] = update
    return await coll.find_one_and_update(
        key, ops, upsert=True, return_document=ReturnDocument.AFTER,
        projection={'_id': 0})

@api.post('/seed')
async def seed_demo():
    """Idempotent demo seed (rich data for every role):

    - one account per role incl. `admin` (password Password1!), org_admin
      flags on the sponsor/site accounts
    - 4 organizations, 3 trials with visit templates
    - 8 patients across the trials, visit instances curated into a mix of
      completed / upcoming / missed plus one overdue and visits due today
      (so GET /api/tasks has items for pi@mtb.app / crc@mtb.app)
    - medications + 14 days of dose logs → ~93% adherence for patient@mtb.app
    - notifications of each kind, support tickets in each status, invitations
      in all four lifecycle statuses, sample audit rows
    - admin-module fixtures: master-data submissions, terms versions, system
      alerts, broadcast messages
    """
    n = now()
    today = n.date()
    start_today = n.replace(hour=0, minute=0, second=0, microsecond=0)
    pw = pwd_ctx.hash(SEED_PASSWORD)     # one hash — all demo users share the password
    demo_security_answer = pwd_ctx.hash('delhi')

    # 1) Organizations — one per org type.
    for org_name, otype in [('Pfizer Global', 'sponsor'), ('IQVIA India', 'cro'),
                            ('MedPoint SMO Services', 'smo'), ('AIIMS Delhi', 'site')]:
        await _seed_upsert(db.organizations, {'name': org_name}, insert={
            'type': otype, 'address': 'Sector 12, New Delhi',
            'contact': '+91 11 2658 0000',
            'email': f"contact@{org_name.split()[0].lower()}.example",
            'website': '', 'status': 'active', 'created_at': n, 'seed': True})

    # 2) Users — one per role. org_admin marks the org-console owners.
    demo_users = [
        ('admin@mtb.app',   'admin',   'Meera Nair',        'MTB Health Technologies', {}),
        ('sponsor@mtb.app', 'sponsor', 'Sarah Chen',        'Pfizer Global',           {'org_admin': True}),
        ('cro@mtb.app',     'cro',     'David Okafor',      'IQVIA India',             {}),
        ('smo@mtb.app',     'smo',     'Kavita Rao',        'MedPoint SMO Services',   {}),
        ('site@mtb.app',    'site',    'Vikram Malhotra',   'AIIMS Delhi',             {'org_admin': True}),
        ('pi@mtb.app',      'pi',      'Dr. Rajesh Sharma', 'AIIMS Delhi',             {}),
        ('crc@mtb.app',     'crc',     'Anita Verma',       'AIIMS Delhi',             {}),
        ('patient@mtb.app', 'patient', 'Priya Kumar',       '',                        {}),
    ]
    users: Dict[str, dict] = {}
    for email, role, name, org, extra in demo_users:
        users[role] = await _seed_upsert(db.users, {'email': email}, insert={
            'role': role, 'full_name': name, 'organization': org,
            'phone': '+91 98765 43210', 'hashed_password': pw,
            'avatar_initials': ''.join(w[0].upper() for w in name.replace('Dr. ', '').split()[:2]) or 'U',
            'security_question': 'What is the name of the place you are born?',
            'security_answer_hash': demo_security_answer,
            'created_at': n, 'is_online': False,
        }, update=extra or None)

    # 3) Trials + their visit templates (keyed on trial_id + visit_number).
    #    Every seeded template carries the same "before you come in" checklist
    #    (refreshed via $set so existing seeded rows pick it up on re-seed).
    DEFAULT_VISIT_CHECKLIST = [
        'Fast for 8 hours before your visit',
        'Bring your patient ID card',
        'Wear comfortable clothing',
        'Take your regular medications unless told otherwise',
    ]
    trials_spec = [
        ('Protocol-001', 'A Phase II Trial of MTB-Diab-Rx in Type-2 Diabetes',
         'Phase II', 'Type-2 Diabetes',
         'A randomized, double-blind study of MTB-Diab-Rx vs placebo.',
         [(1, 'Screening', 0, ['Informed consent', 'Medical history', 'Vitals', 'Blood draw']),
          (2, 'Baseline', 7, ['Physical exam', 'ECG', 'Blood draw', 'Study drug dispense']),
          (3, 'Week 2', 14, ['Vitals', 'Adverse-event review']),
          (4, 'Week 4', 28, ['Vitals', 'Blood draw', 'Adverse-event review']),
          (5, 'Week 8', 56, ['Vitals', 'Blood draw', 'Drug accountability']),
          (6, 'Week 12', 84, ['Vitals', 'Blood draw', 'Drug accountability']),
          (7, 'Week 16 · Follow-Up', 112, ['Vitals', 'Blood draw', 'Adherence review']),
          (8, 'Week 20', 140, ['Vitals', 'Blood draw']),
          (9, 'Week 24', 168, ['Vitals', 'Blood draw', 'ECG']),
          (10, 'End of Study', 196, ['Final exam', 'Drug return', 'Final assessment'])]),
        ('Protocol-002', 'A Phase III Study of MTB-HTN-24 in Resistant Hypertension',
         'Phase III', 'Hypertension',
         'A multicentre, open-label study of MTB-HTN-24 in resistant hypertension.',
         [(1, 'Screening', 0, ['Informed consent', 'Vitals', 'ABPM setup']),
          (2, 'Baseline', 7, ['Physical exam', 'Blood draw', 'Study drug dispense']),
          (3, 'Week 4', 28, ['Vitals', 'Adverse-event review']),
          (4, 'Week 8', 56, ['Vitals', 'Blood draw']),
          (5, 'Week 12', 84, ['Vitals', 'Drug accountability']),
          (6, 'End of Study', 112, ['Final exam', 'Drug return'])]),
        ('Protocol-003', 'A Phase I Dose-Escalation Study of MTB-Onc-7',
         'Phase I', 'Solid Tumours',
         'First-in-human dose-escalation and safety study of MTB-Onc-7.',
         [(1, 'Screening', 0, ['Informed consent', 'Tumour imaging', 'Blood draw']),
          (2, 'Cycle 1 Day 1', 3, ['Dosing', 'PK sampling', 'Vitals']),
          (3, 'Cycle 1 Day 8', 10, ['PK sampling', 'Adverse-event review']),
          (4, 'Cycle 2 Day 1', 24, ['Dosing', 'Vitals', 'Blood draw']),
          (5, 'End of Cycle 2', 45, ['Tumour imaging', 'Final assessment'])]),
    ]
    trial_ids: Dict[str, str] = {}
    for protocol, title, phase, condition, desc, visits in trials_spec:
        t = await _seed_upsert(db.trials, {'protocol_id': protocol}, insert={
            'title': title, 'phase': phase, 'condition': condition,
            'description': desc, 'sponsor_name': 'Pfizer Global',
            'created_by': users['sponsor']['id'], 'created_at': n, 'status': 'active'})
        trial_ids[protocol] = t['id']
        for num, vname, off, acts in visits:
            await _seed_upsert(db.visits, {'trial_id': t['id'], 'visit_number': num}, insert={
                'name': vname, 'day_offset': off, 'window_days': 3,
                'activities': acts, 'created_at': n},
                update={'checklist': DEFAULT_VISIT_CHECKLIST})

    # 4) Patients — 8 across the 3 trials. pi/crc ids are re-pointed on every
    #    run so scoping and the tasks queue always resolve to the demo staff.
    staff = {'pi_id': users['pi']['id'], 'crc_id': users['crc']['id']}
    patients_spec = [
        ('Priya Kumar',   'patient@mtb.app',       'Protocol-001', 70, users['patient']['id']),
        ('Ravi Patel',    'ravi.patel@mtb.app',    'Protocol-001', 40, None),
        ('Sunita Iyer',   'sunita.iyer@mtb.app',   'Protocol-001', 40, None),
        ('Arjun Singh',   'arjun.singh@mtb.app',   'Protocol-001', 40, None),
        ('Meera Joshi',   'meera.joshi@mtb.app',   'Protocol-001', 40, None),
        ('Karan Mehta',   'karan.mehta@mtb.app',   'Protocol-002', 30, None),
        ('Fatima Sheikh', 'fatima.sheikh@mtb.app', 'Protocol-002', 10, None),
        ('Rohan Das',     'rohan.das@mtb.app',     'Protocol-003', 3,  None),
    ]
    pids: Dict[str, str] = {}
    for fname, email, protocol, days_ago, linked_user_id in patients_spec:
        p = await _seed_upsert(db.patients, {'email': email}, insert={
            'full_name': fname, 'phone': '+91 98765 00000',
            'trial_id': trial_ids[protocol],
            'enrolled_date': (n - timedelta(days=days_ago)).date().isoformat(),
            'completed_visit_ids': [],
            'avatar_initials': ''.join(w[0].upper() for w in fname.split()[:2]),
            'created_at': n,
        }, update={**staff, 'user_id': linked_user_id})
        pids[email] = p['id']
        await materialize_visit_instances(p)   # no-op if already materialized

    # 5) Curate visit instances into the demo status mix. Deterministic $set
    #    updates keyed on (patient_id, seq) — reruns re-align, never duplicate.
    def _sched(days_from_today: int) -> Dict:
        sd = start_today + timedelta(days=days_from_today, hours=10)
        return {'status': 'upcoming', 'scheduled_date': sd,
                'window_start': sd - timedelta(days=3),
                'window_end': sd + timedelta(days=3)}

    async def _curate(email: str, updates: Dict[int, Dict]):
        await db.visit_instances.bulk_write([
            UpdateOne({'patient_id': pids[email], 'seq': seq}, {'$set': fields})
            for seq, fields in updates.items()])

    done = {'status': 'completed'}
    await _curate('patient@mtb.app', {1: done, 2: done, 3: done,
                                      4: _sched(-2),    # overdue → tasks queue
                                      5: _sched(0)})    # due today
    await _curate('ravi.patel@mtb.app', {1: done, 2: done, 3: done, 4: done})
    await _curate('karan.mehta@mtb.app', {1: done, 2: done})
    await _curate('rohan.das@mtb.app', {1: done, 2: _sched(0)})
    # (sunita/arjun/meera/fatima keep their materialized missed/upcoming mix)

    # 6) Medications + 14 days of dose logs for patient@mtb.app.
    #    3 slots/day × 14 days = 42 expected; 3 non-taken → 39/42 ≈ 93%.
    #    The misses sit 10–11 days back so streak_days stays ≥ 10. start_date
    #    is re-pinned to today-13 on every run to keep the window aligned.
    priya = pids['patient@mtb.app']
    med_start = (today - timedelta(days=13)).isoformat()
    med_common = {'route': 'oral', 'end_date': None, 'active': True,
                  'created_by': users['crc']['id'], 'created_at': n}
    med1 = await _seed_upsert(db.medications, {'patient_id': priya, 'name': 'MTB-Diab-Rx'},
                              insert={'trial_id': trial_ids['Protocol-001'], 'dosage': '500 mg',
                                      'schedule': [{'time': '08:00', 'label': 'Morning'},
                                                   {'time': '20:00', 'label': 'Evening'}],
                                      **med_common},
                              update={'start_date': med_start})
    med2 = await _seed_upsert(db.medications, {'patient_id': priya, 'name': 'Metformin'},
                              insert={'trial_id': trial_ids['Protocol-001'], 'dosage': '850 mg',
                                      'schedule': [{'time': '08:00', 'label': 'Morning'}],
                                      **med_common},
                              update={'start_date': med_start})
    # Keep the demo adherence deterministic: stray meds added to the demo
    # patient outside the seed are deactivated (never deleted).
    await db.medications.update_many(
        {'patient_id': priya, 'name': {'$nin': ['MTB-Diab-Rx', 'Metformin']}, 'active': True},
        {'$set': {'active': False}})

    dose_ops = []
    def _dose(med, day_offset, slot, status_):
        dose_ops.append(UpdateOne(
            {'medication_id': med['id'],
             'date': (today - timedelta(days=day_offset)).isoformat(), 'time': slot},
            {'$set': {'status': status_, 'logged_at': n, 'seed': True},
             '$setOnInsert': {'id': str(uuid.uuid4()), 'patient_id': med['patient_id']}},
            upsert=True))
    # Re-running the seed on a later calendar day mints new dose rows (keyed on
    # today−k). Prune the demo patient's seed-marked rows that fell OUTSIDE the
    # current 14-day window so old dates can't accumulate and drift adherence.
    window_start = (today - timedelta(days=13)).isoformat()
    window_end = today.isoformat()
    await db.dose_logs.delete_many({
        'patient_id': priya, 'seed': True,
        '$or': [{'date': {'$lt': window_start}}, {'date': {'$gt': window_end}}],
    })
    for k in range(14):
        _dose(med1, k, '08:00', 'not_taken' if k == 11 else 'taken')
        _dose(med1, k, '20:00', 'skipped' if k == 10 else 'taken')
        _dose(med2, k, '08:00', 'skipped' if k == 11 else 'taken')
    # A second patient on medication so staff screens have variety.
    med3 = await _seed_upsert(db.medications, {'patient_id': pids['karan.mehta@mtb.app'], 'name': 'Amlodipine'},
                              insert={'trial_id': trial_ids['Protocol-002'], 'dosage': '5 mg',
                                      'schedule': [{'time': '09:00', 'label': 'Morning'}],
                                      **med_common},
                              update={'start_date': (today - timedelta(days=2)).isoformat()})
    for k in range(3):
        _dose(med3, k, '09:00', 'taken')
    await db.dose_logs.bulk_write(dose_ops)

    # 7) Notifications — one of each kind, spread across roles.
    for role, title, body_text, kind in [
        ('patient', 'Visit due today', 'Your Week 8 visit at AIIMS Delhi is scheduled today.', 'reminder'),
        ('patient', 'Message from Dr. Sharma', 'Please fast for 8 hours before your blood draw.', 'message'),
        ('patient', 'Lab results reviewed', 'Your Week 4 results have been reviewed by your care team.', 'result'),
        ('pi',      'Schedule review pending', 'Protocol-002 visit schedule is awaiting your review.', 'schedule'),
        ('crc',     'New patient enrolled', 'Rohan Das was enrolled in Protocol-003.', 'system'),
        ('sponsor', 'Schedule approved · Protocol-001', 'Dr. Rajesh Sharma approved the visit schedule.', 'schedule'),
        ('admin',   'OTP delivery failures', '3 OTP deliveries failed in the last 24 hours.', 'system'),
    ]:
        await _seed_upsert(db.notifications,
                           {'user_id': users[role]['id'], 'title': title, 'seed': True},
                           insert={'body': body_text, 'kind': kind, 'read': False,
                                   'created_at': n - timedelta(hours=2)})

    # 8) Support tickets — one per status (status refreshed each run).
    for role, cat, subject, ticket_status in [
        ('patient', 'Technical', 'App shows a blank screen after login', 'Open'),
        ('crc',     'Account',   'Unable to update phone number', 'In Progress'),
        ('pi',      'General',   'Question about visit-window rules', 'Resolved'),
    ]:
        await _seed_upsert(db.support_tickets,
                           {'user_id': users[role]['id'], 'subject': subject, 'seed': True},
                           insert={'ticket_id': f"#TKT-{n.strftime('%Y%m%d')}-{str(uuid.uuid4().int)[:4]}",
                                   'category': cat,
                                   'description': f'Seeded demo ticket ({ticket_status.lower()}).',
                                   'created_at': n - timedelta(days=1)},
                           update={'status': ticket_status})

    # 9) Invitations — one per lifecycle status. The pending one has its
    #    expiry pushed forward on every run so it stays genuinely pending.
    async def _seed_invite(email, role, inv_status, extra=None, refresh=None):
        await _seed_upsert(db.invitations, {'email': email}, insert={
            'token': uuid.uuid4().hex, 'phone': '', 'full_name': '',
            'role': role, 'trial_id': None, 'invited_by': users['pi']['id'],
            'org': 'AIIMS Delhi', 'site': 'AIIMS Delhi', 'status': inv_status,
            'created_at': n, 'resend_count': 0, 'seed': True, **(extra or {})},
            update=refresh)

    await _seed_invite('invitee.pending@mtb.app', 'crc', 'pending',
                       refresh={'expires_at': n + timedelta(days=INVITE_TTL_DAYS)})
    await _seed_invite('invitee.accepted@mtb.app', 'patient', 'accepted',
                       {'expires_at': n + timedelta(days=INVITE_TTL_DAYS),
                        'accepted_at': n - timedelta(days=1)})
    await _seed_invite('invitee.expired@mtb.app', 'patient', 'pending',
                       {'expires_at': n - timedelta(days=1)})   # reads as expired
    await _seed_invite('invitee.cancelled@mtb.app', 'crc', 'cancelled',
                       {'expires_at': n + timedelta(days=INVITE_TTL_DAYS),
                        'cancelled_at': n - timedelta(days=2)})

    # 10) Sample audit rows across categories (write_audit shape).
    for role, action, detail, audit_status in [
        ('patient', 'login.success', 'Signed in from the mobile app', 'success'),
        ('patient', 'login.failed', 'Wrong password (2 attempts)', 'failure'),
        ('crc',     'visit.patch', 'Marked Baseline visit completed for Ravi Patel', 'success'),
        ('crc',     'patient.enroll', 'Enrolled Rohan Das in Protocol-003', 'success'),
        ('sponsor', 'trial.create', 'Created trial Protocol-002', 'success'),
        ('patient', 'account.update', 'Updated phone number', 'success'),
        (None,      'system.backup', 'Nightly database backup completed', 'success'),
    ]:
        actor = users.get(role) or {}
        await _seed_upsert(db.audit_logs,
                           {'action': action, 'detail': detail, 'seed': True},
                           insert={'user_id': actor.get('id'),
                                   'user_name': actor.get('full_name', ''),
                                   'role': actor.get('role', ''),
                                   'org': actor.get('organization', ''),
                                   'category': action.split('.', 1)[0],
                                   'ip': '103.27.9.44', 'device': 'iPhone 15 · MTB app',
                                   'status': audit_status,
                                   'created_at': n - timedelta(hours=6)})

    # 11) Admin-module fixtures. Field names follow the admin API audit
    #     (docs/superpowers/audits/2026-07-07-admin-api-audit.md §4/5/7/14);
    #     the admin backend task will formalize these collections later.
    # Master-data "Others: specify" submissions (§4)
    for field_type, value, md_status, action_by, reject in [
        ('designation', 'Clinical Research Fellow', 'pending', None, ''),
        ('department', 'Endocrinology Research Wing', 'approved', 'Meera Nair', ''),
        ('designation', 'Wellness Consultant', 'rejected', 'Meera Nair', 'Not a recognised clinical designation'),
    ]:
        await _seed_upsert(db.master_data_submissions,
                           {'fieldType': field_type, 'value': value},
                           insert={'submittedBy': users['crc']['full_name'], 'org': 'AIIMS Delhi',
                                   'dateSubmitted': n - timedelta(days=2), 'status': md_status,
                                   'actionBy': action_by, 'rejectReason': reject, 'seed': True})

    # Terms & privacy versions (§5) — v1.0 of each, active.
    for doc_type in ('ToS', 'Privacy'):
        await _seed_upsert(db.terms_versions, {'type': doc_type, 'version': '1.0'},
                           insert={'status': 'active', 'createdAt': n - timedelta(days=30),
                                   'activatedAt': n - timedelta(days=30), 'acceptedBy': 6,
                                   'content': f'{doc_type} v1.0 — demo content; rendered document at /api/legal.',
                                   'seed': True})

    # System alerts (§7)
    for alert_type, desc, affected, severity, alert_status in [
        ('OTP failure', 'SMS OTP delivery failed 3 times in a row', 'patient@mtb.app', 'high', 'open'),
        ('Invite failure', 'Invitation email bounced', 'invitee.pending@mtb.app', 'medium', 'open'),
        ('Session anomaly', 'Login from a new device and location', 'crc@mtb.app', 'low', 'resolved'),
    ]:
        await _seed_upsert(db.system_alerts, {'type': alert_type, 'description': desc},
                           insert={'affected': affected, 'severity': severity,
                                   'status': alert_status, 'timestamp': n - timedelta(hours=4),
                                   'seed': True})

    # Broadcast messages (§14)
    for msg_type, subject, body_text, target in [
        ('general', 'Welcome to My Trial Board', 'The MTB platform is now live for all study teams.', 'all'),
        ('compliance', 'Annual GCP refresher due', 'Please complete your GCP refresher training by month end.', 'role:pi'),
        ('urgent', 'Planned maintenance tonight', 'The platform will be read-only 02:00–03:00 IST.', 'all'),
    ]:
        await _seed_upsert(db.broadcast_messages, {'subject': subject},
                           insert={'type': msg_type, 'body': body_text, 'target': target,
                                   'allowReplies': msg_type != 'urgent', 'scheduleAt': None,
                                   'status': 'sent', 'sent_at': n - timedelta(days=1),
                                   'created_by': users['admin']['id'], 'seed': True})

    await db.meta.update_one({'key': 'seeded_v2'}, {'$set': {'at': n}}, upsert=True)
    return {'ok': True,
            'users': [{'email': e, 'role': r} for e, r, _n, _o, _x in demo_users],
            'password': SEED_PASSWORD}

# ── Visit mutations (mark complete / reschedule / flag) ────────────────────
class VisitPatch(BaseModel):
    status: Optional[Literal['completed', 'upcoming', 'missed', 'flagged']] = None
    scheduled_date: Optional[str] = None
    note: Optional[str] = None

@api.patch('/visits/{visit_id}')
async def patch_visit(visit_id: str, body: VisitPatch,
                      user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    visit = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    if not visit:
        raise HTTPException(404, 'Visit not found')
    trial = await db.trials.find_one({'id': visit.get('trial_id')}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    upd = {k: v for k, v in body.dict().items() if v is not None}
    if not upd: raise HTTPException(400, 'Nothing to update')
    upd['updated_by'] = user['id']; upd['updated_at'] = now()
    await db.visits.update_one({'id': visit_id}, {'$set': upd})
    v = await db.visits.find_one({'id': visit_id}, {'_id': 0})
    await write_audit(user, 'visit.patch', f'Updated visit {visit_id}: {", ".join(sorted(set(upd) - {"updated_by", "updated_at"}))}',
                      target_id=visit_id, trial_id=trial['id'], changes=upd)
    return v

# ── Visit-instance mutations (per-patient — never touches the template) ─────
class VisitInstancePatch(BaseModel):
    status: Optional[Literal[
        'planned', 'due', 'completed', 'missed', 'cancelled', 'rescheduled',
        'manual_review', 'scheduled', 'upcoming', 'overdue', 'screen_pass',
        'screen_fail', 'withdrawn', 'dropout',
    ]] = None
    scheduled_date: Optional[str] = None
    visit_type: Optional[Literal['Hospital', 'Phone', 'Remote', 'Home']] = None
    note: Optional[str] = Field(default=None, max_length=2000)
    actual_visit_at: Optional[str] = None
    missed_reason: Optional[str] = Field(default=None, max_length=2000)
    cancelled_reason: Optional[str] = Field(default=None, max_length=2000)

class VisitInstanceTaskPatch(BaseModel):
    completed: bool

class VisitInstanceCommentIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)

@api.patch('/visit-instances/{instance_id}')
async def patch_visit_instance(instance_id: str, body: VisitInstancePatch,
                               user=Depends(require_roles('pi', 'crc'))):
    inst = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    if not inst:
        raise HTTPException(404, 'Visit instance not found')
    # Ownership: resolve the instance's patient and apply the patient-scoping
    # rule — a foreign instance is a 403, never a silent write.
    await _require_patient(user, inst.get('patient_id'))
    upd: Dict = {}
    if body.status is not None:
        upd['status'] = body.status
        upd['operational_status'] = body.status
        if body.status == 'completed':
            upd['completed_by'] = user['id']
            upd['completed_by_name'] = user.get('full_name') or ''
            upd['completed_at'] = now()
            upd['actual_visit_at'] = upd['completed_at']
        elif inst.get('status') == 'completed':
            upd['completed_by'] = None
            upd['completed_by_name'] = None
            upd['completed_at'] = None
    if body.actual_visit_at is not None:
        try:
            actual = datetime.fromisoformat(body.actual_visit_at.replace('Z', '+00:00'))
        except ValueError:
            raise HTTPException(400, 'actual_visit_at must be an ISO 8601 date/datetime')
        upd['actual_visit_at'] = actual if actual.tzinfo else actual.replace(tzinfo=timezone.utc)
    if body.missed_reason is not None:
        upd['missed_reason'] = body.missed_reason.strip()
    if body.cancelled_reason is not None:
        upd['cancelled_reason'] = body.cancelled_reason.strip()
    if body.note is not None:
        upd['note'] = body.note
    if body.visit_type is not None:
        upd['visit_type'] = body.visit_type
    if body.scheduled_date is not None:
        try:
            sched = datetime.fromisoformat(body.scheduled_date)
        except ValueError:
            raise HTTPException(400, 'scheduled_date must be an ISO 8601 date/datetime')
        if sched.tzinfo is None:
            sched = sched.replace(tzinfo=timezone.utc)
        upd['scheduled_date'] = sched
        upd['window_start'], upd['window_end'] = _schedule_window(inst, sched)
        upd['rescheduled_from'] = inst.get('scheduled_date')
        upd['status'] = 'planned'
        upd['operational_status'] = 'planned'
    if not upd:
        raise HTTPException(400, 'Nothing to update')
    changed = sorted(upd)
    upd['updated_by'] = user['id']
    upd['updated_at'] = now()
    await db.visit_instances.update_one({'id': instance_id}, {'$set': upd})
    fresh = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    await write_audit(user, 'visit_instance.patch',
                      f"Updated visit instance {instance_id} ({inst.get('name', '')}): {', '.join(changed)}",
                      target_id=instance_id, patient_id=inst.get('patient_id'),
                      trial_id=inst.get('trial_id'),
                      changes={k: iso(v) for k, v in upd.items()})
    return await _ensure_visit_instance_workflow(fresh)


@api.patch('/visit-instances/{instance_id}/tasks/{task_id}')
async def patch_visit_instance_task(
    instance_id: str,
    task_id: str,
    body: VisitInstanceTaskPatch,
    user=Depends(require_roles('pi', 'crc')),
):
    """Complete/reopen one stable task on one patient's visit instance."""
    inst = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    if not inst:
        raise HTTPException(404, 'Visit instance not found')
    await _require_patient(user, inst.get('patient_id'))
    inst = await _ensure_visit_instance_workflow(inst)

    task_kind = None
    task_row = None
    updated_tasks = None
    for field in ('clinical_tasks', 'admin_tasks'):
        rows = [dict(row) for row in (inst.get(field) or [])]
        for index, row in enumerate(rows):
            if row.get('id') == task_id:
                task_kind = field
                task_row = row
                if bool(row.get('completed')) == body.completed:
                    return inst
                rows[index] = {
                    **row,
                    'completed': body.completed,
                    'completed_by': user['id'] if body.completed else None,
                    'completed_by_name': (user.get('full_name') or '') if body.completed else None,
                    'completed_at': now() if body.completed else None,
                }
                updated_tasks = rows
                break
        if task_row:
            break
    if not task_row or not task_kind or updated_tasks is None:
        raise HTTPException(404, 'Visit task not found')

    changed_at = now()
    await db.visit_instances.update_one(
        {'id': instance_id},
        {'$set': {
            task_kind: updated_tasks,
            'updated_by': user['id'],
            'updated_at': changed_at,
        }},
    )
    fresh = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    await write_audit(
        user,
        'visit_instance.task_complete' if body.completed else 'visit_instance.task_reopen',
        f"{'Completed' if body.completed else 'Reopened'} "
        f"{'clinical' if task_kind == 'clinical_tasks' else 'administrative'} "
        f"task {task_row.get('label', '')}",
        target_id=instance_id,
        task_id=task_id,
        patient_id=inst.get('patient_id'),
        trial_id=inst.get('trial_id'),
        changes={'completed': body.completed, 'task_kind': task_kind},
    )
    return await _ensure_visit_instance_workflow(fresh)


@api.post('/visit-instances/{instance_id}/comments')
async def add_visit_instance_comment(
    instance_id: str,
    body: VisitInstanceCommentIn,
    user=Depends(require_roles('pi', 'crc')),
):
    """Append an attributed, immutable clinical/admin visit comment."""
    inst = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    if not inst:
        raise HTTPException(404, 'Visit instance not found')
    await _require_patient(user, inst.get('patient_id'))
    inst = await _ensure_visit_instance_workflow(inst)
    text = body.text.strip()
    if not text:
        raise HTTPException(400, 'Comment cannot be blank')
    created_at = now()
    comment = {
        'id': str(uuid.uuid4()),
        'text': text,
        'created_by': user['id'],
        'created_by_name': user.get('full_name') or '',
        'created_at': created_at,
    }
    await db.visit_instances.update_one(
        {'id': instance_id},
        {
            '$push': {'comments': comment},
            '$set': {'updated_by': user['id'], 'updated_at': created_at},
        },
    )
    fresh = await db.visit_instances.find_one({'id': instance_id}, {'_id': 0})
    await write_audit(
        user,
        'visit_instance.comment_add',
        f"Added a comment to visit instance {instance_id} ({inst.get('name', '')})",
        target_id=instance_id,
        comment_id=comment['id'],
        patient_id=inst.get('patient_id'),
        trial_id=inst.get('trial_id'),
    )
    return await _ensure_visit_instance_workflow(fresh)

# ── Visit-schedule review (PI approves or flags a trial's schedule) ─────────
class ScheduleFlagIn(BaseModel):
    reason: str

class ScheduleDecisionIn(BaseModel):
    notes: Optional[str] = Field(default='', max_length=1000)

class ScheduleRejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    notes: Optional[str] = Field(default='', max_length=1000)

async def _notify_trial_sponsors(trial, title, body_text):
    """Notify the trial's sponsor users: its creator (if sponsor/cro) plus every
    sponsor-role user in the trial's sponsor organization."""
    ids = set()
    creator = await db.users.find_one({'id': trial.get('created_by')}, {'_id': 0, 'id': 1, 'role': 1})
    if creator and creator.get('role') in ('sponsor', 'cro'):
        ids.add(creator['id'])
    sponsor_name = (trial.get('sponsor_name') or '').strip()
    if sponsor_name:
        others = await db.users.find({'role': 'sponsor', 'organization': sponsor_name},
                                     {'_id': 0, 'id': 1}).to_list(200)
        ids.update(u['id'] for u in others)
    for uid in ids:
        await db.notifications.insert_one({
            'id': str(uuid.uuid4()), 'user_id': uid, 'title': title, 'body': body_text,
            'kind': 'schedule', 'trial_id': trial['id'], 'read': False, 'created_at': now(),
        })
    return len(ids)

async def _review_schedule(trial_id: str, user: dict, new_status: str, reason: str = ''):
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    # Ownership: PI-only is enforced by the route; on top of that the PI must
    # belong to this trial (creator, same org, or a listed PI) — fail-closed.
    if not await _pi_owns_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    upd = {'schedule_status': new_status,
           'schedule_reviewed_by': user['id'], 'schedule_reviewed_at': now()}
    if new_status == 'flagged':
        upd['schedule_flag_reason'] = reason
    await db.trials.update_one({'id': trial_id}, {'$set': upd})
    label = trial.get('protocol_id') or trial.get('title') or trial_id
    if new_status == 'approved':
        title = f'Schedule approved · {label}'
        body_text = f"{user['full_name']} approved the visit schedule for {label}."
    else:
        title = f'Schedule flagged · {label}'
        body_text = f"{user['full_name']} flagged the visit schedule for {label}: {reason}"
    notified = await _notify_trial_sponsors(trial, title, body_text)
    await write_audit(user, f'schedule.{"approve" if new_status == "approved" else "flag"}',
                      f'Visit schedule for {label} {new_status}' + (f' — {reason}' if reason else ''),
                      target_id=trial_id, notified=notified)
    return {'ok': True, 'trial_id': trial_id, 'schedule_status': new_status, 'notified': notified}

@api.post('/schedules/{trial_id}/approve')
async def approve_schedule(trial_id: str, user=Depends(require_roles('pi'))):
    return await _review_schedule(trial_id, user, 'approved')

@api.post('/schedules/{trial_id}/flag')
async def flag_schedule(trial_id: str, body: ScheduleFlagIn, user=Depends(require_roles('pi'))):
    return await _review_schedule(trial_id, user, 'flagged', reason=body.reason)

async def _refresh_trial_schedule_status(trial_id: str):
    """Keep the legacy trial-level status useful without losing per-site state.

    A rejected site makes the aggregate flagged. The schedule becomes approved
    only after every assigned site review is approved; otherwise it is pending.
    """
    rows = await db.schedule_reviews.find(
        {'trial_id': trial_id}, {'_id': 0, 'status': 1}).to_list(1000)
    assigned = [row for row in rows if row.get('status') != 'pending_assignment']
    statuses = {row.get('status') for row in assigned}
    if 'rejected' in statuses:
        status = 'flagged'
    elif assigned and statuses == {'approved'}:
        status = 'approved'
    else:
        status = 'pending_review'
    await db.trials.update_one(
        {'id': trial_id},
        {'$set': {'schedule_status': status, 'schedule_status_updated_at': now()}})
    return status

async def _schedule_review_row(row: dict) -> dict:
    trial = await db.trials.find_one({'id': row['trial_id']}, {'_id': 0}) or {}
    share = await db.shares.find_one({'id': row.get('share_id')}, {'_id': 0}) or {}
    version = await db.schedule_versions.find_one(
        {'id': row.get('version_id')}, {'_id': 0}) if row.get('version_id') else None
    visits = (
        row.get('visit_snapshot')
        or (version or {}).get('visits')
        or share.get('visit_snapshot')
    )
    # Legacy shares made before version snapshots existed retain their old
    # behavior, while every new review is pinned to the immutable shared rows.
    if visits is None:
        visits = await db.visits.find(
            {'trial_id': row['trial_id']}, {'_id': 0},
        ).sort('visit_number', 1).to_list(500)
    document = (
        row.get('document') or (version or {}).get('document')
        or share.get('document')
    )
    if not document and row.get('document_id'):
        stored_document = await db.files.find_one(
            {'id': row['document_id']}, {'_id': 0, 'key': 0})
        document = _shared_document_metadata(stored_document)
    return serialize({
        **row,
        'protocol_id': trial.get('protocol_id') or '',
        'trial_title': trial.get('title') or '',
        'phase': trial.get('phase') or '',
        'condition': trial.get('condition') or '',
        'visits': visits,
        'schedule_version': (
            row.get('schedule_version') or (version or {}).get('version')
            or share.get('schedule_version') or 0
        ),
        'version_id': row.get('version_id') or share.get('version_id') or '',
        'changed_visits': (
            row.get('changed_visits')
            or (version or {}).get('changed_visits')
            or share.get('changed_visits')
            or []
        ),
        'document': document,
        'share_token': share.get('token') or '',
        'share_expires_at': share.get('expires_at'),
    })

@api.get('/schedule-reviews')
async def list_schedule_reviews(user=Depends(require_roles('pi'))):
    """PI inbox of schedule packages explicitly shared with this user."""
    rows = await db.schedule_reviews.find(
        {'reviewer_id': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(500)
    return [await _schedule_review_row(row) for row in rows]

@api.get('/schedule-reviews/{review_id}')
async def get_schedule_review(review_id: str, user=Depends(require_roles('pi'))):
    row = await db.schedule_reviews.find_one({'id': review_id}, {'_id': 0})
    if not row:
        raise HTTPException(404, 'Schedule review not found')
    if row.get('reviewer_id') != user['id']:
        raise HTTPException(403, 'This schedule review is assigned to another investigator')
    return await _schedule_review_row(row)

async def _decide_schedule_review(review_id: str, user: dict, status: str,
                                  notes: str = '', reason: str = ''):
    row = await db.schedule_reviews.find_one({'id': review_id}, {'_id': 0})
    if not row:
        raise HTTPException(404, 'Schedule review not found')
    if row.get('reviewer_id') != user['id']:
        raise HTTPException(403, 'This schedule review is assigned to another investigator')
    if row.get('status') != 'pending':
        raise HTTPException(409, f"Schedule was already {row.get('status', 'reviewed')}")
    updates = {
        'status': status,
        'pi_notes': (notes or '').strip(),
        'reviewed_by': user['id'],
        'reviewed_at': now(),
    }
    if status == 'rejected':
        updates['rejection_reason'] = reason.strip()
    await db.schedule_reviews.update_one({'id': review_id}, {'$set': updates})
    aggregate = await _refresh_trial_schedule_status(row['trial_id'])
    trial = await db.trials.find_one({'id': row['trial_id']}, {'_id': 0}) or {}
    label = trial.get('protocol_id') or trial.get('title') or row['trial_id']
    if status == 'approved':
        title = f'Schedule approved · {label}'
        body_text = f"{user['full_name']} approved the schedule for {row.get('site_name') or 'their site'}."
    else:
        title = f'Schedule rejected · {label}'
        body_text = (
            f"{user['full_name']} rejected the schedule for "
            f"{row.get('site_name') or 'their site'}: {reason.strip()}"
        )
    notified = await _notify_trial_sponsors(trial, title, body_text)
    await write_audit(
        user, f'schedule_review.{status}',
        f"{label} schedule {status} for {row.get('site_name') or 'site'}",
        target_id=review_id, trial_id=row['trial_id'], share_id=row.get('share_id'),
        notes=(notes or '').strip(), reason=reason.strip(), notified=notified,
    )
    fresh = await db.schedule_reviews.find_one({'id': review_id}, {'_id': 0})
    return {**await _schedule_review_row(fresh), 'trial_schedule_status': aggregate}

@api.post('/schedule-reviews/{review_id}/approve')
async def approve_schedule_review(review_id: str, body: ScheduleDecisionIn,
                                  user=Depends(require_roles('pi'))):
    return await _decide_schedule_review(review_id, user, 'approved', notes=body.notes or '')

@api.post('/schedule-reviews/{review_id}/reject')
async def reject_schedule_review(review_id: str, body: ScheduleRejectIn,
                                 user=Depends(require_roles('pi'))):
    return await _decide_schedule_review(
        review_id, user, 'rejected', notes=body.notes or '', reason=body.reason)

# ── Tasks queue (pi/crc action items, computed on read) ─────────────────────
VISIT_QUEUE_TERMINAL_STATUSES = [
    'completed', 'missed', 'screen_fail', 'withdrawn', 'dropout',
]

@api.get('/tasks')
async def my_tasks(user=Depends(require_roles('pi', 'crc'))):
    """Action queue for site staff: overdue visit instances, visits due today,
    trials awaiting schedule review, and an unread-messages rollup. Computed
    from existing collections on every read — nothing is stored."""
    q = {'pi_id': user['id']} if user['role'] == 'pi' else {'crc_id': user['id']}
    patients = await db.patients.find(q, {'_id': 0}).to_list(500)
    pmap = {p['id']: p for p in patients}
    tasks = []
    start_today = now().replace(hour=0, minute=0, second=0, microsecond=0)
    end_today = start_today + timedelta(days=1)

    if pmap:
        insts = await db.visit_instances.find({
            'patient_id': {'$in': list(pmap)},
            'status': {'$nin': VISIT_QUEUE_TERMINAL_STATUSES},
        }, {'_id': 0}).sort('scheduled_date', 1).to_list(1000)
        for raw_instance in insts:
            instance = await _ensure_visit_instance_workflow(raw_instance)
            scheduled = instance.get('scheduled_date')
            window_end = instance.get('window_end') or scheduled
            if not isinstance(scheduled, datetime) or not isinstance(window_end, datetime):
                continue
            if scheduled.tzinfo is None:
                scheduled = scheduled.replace(tzinfo=timezone.utc)
            if window_end.tzinfo is None:
                window_end = window_end.replace(tzinfo=timezone.utc)

            if window_end < start_today:
                deadline_state = 'overdue'
                task_type = 'overdue_visit'
                days_overdue = max(1, (start_today.date() - window_end.date()).days)
                due_label = f'{days_overdue} day{"s" if days_overdue != 1 else ""} overdue'
                priority = 'high'
            elif start_today <= window_end < end_today:
                deadline_state = 'window_closes_today'
                task_type = 'window_closes_today'
                days_overdue = 0
                due_label = 'Window closes today'
                priority = 'high'
            elif start_today <= scheduled < end_today:
                deadline_state = 'scheduled_today'
                task_type = 'visit_today'
                days_overdue = 0
                due_label = 'Today'
                priority = 'medium'
            else:
                continue

            patient = pmap.get(instance['patient_id'], {})
            subject_label = patient.get('subject_id') or \
                f"SUBJ-{(instance['patient_id'] or '')[-3:].upper()}"
            common = {
                'subtitle': subject_label,
                'due': iso(window_end if deadline_state != 'scheduled_today' else scheduled),
                'due_label': due_label,
                'deadline_state': deadline_state,
                'days_overdue': days_overdue,
                'patient_id': instance['patient_id'],
                'trial_id': instance.get('trial_id'),
                'visit_instance_id': instance['id'],
                'visit_name': instance.get('name', 'Visit'),
                'priority': priority,
            }

            # CRC users receive administrative actions only. Clinical checklist
            # items remain on Patient Record for the clinical team.
            pending_admin = [
                task for task in (instance.get('admin_tasks') or [])
                if isinstance(task, dict) and not task.get('completed')
            ] if user['role'] == 'crc' else []
            if pending_admin:
                for admin_task in pending_admin:
                    tasks.append({
                        **common,
                        'id': f"admin_task:{instance['id']}:{admin_task['id']}",
                        'type': 'admin_task',
                        'title': admin_task.get('label') or 'Administrative visit task',
                        'workflow_task_id': admin_task['id'],
                        'workflow_task_kind': 'admin_tasks',
                    })
            else:
                tasks.append({
                    **common,
                    'id': f"{task_type}:{instance['id']}",
                    'type': task_type,
                    'title': (
                        f"Overdue: {instance.get('name', 'Visit')}"
                        if deadline_state == 'overdue'
                        else f"Window closes today: {instance.get('name', 'Visit')}"
                        if deadline_state == 'window_closes_today'
                        else f"Today: {instance.get('name', 'Visit')}"
                    ),
                })

    if user['role'] == 'pi':
        pending_reviews = await db.schedule_reviews.find(
            {'reviewer_id': user['id'], 'status': 'pending'}, {'_id': 0}
        ).sort('created_at', 1).to_list(500)
        review_trial_ids = sorted({row['trial_id'] for row in pending_reviews})
        review_trials = await db.trials.find(
            {'id': {'$in': review_trial_ids}} if review_trial_ids else {'id': {'$in': []}},
            {'_id': 0}).to_list(500)
        review_trial_map = {trial['id']: trial for trial in review_trials}
        for review in pending_reviews:
            t = review_trial_map.get(review['trial_id'], {})
            tasks.append({
                'id': f"schedule_review:{review['id']}",
                'type': 'schedule_review',
                'title': f"Review visit schedule · {t.get('protocol_id') or t.get('title', '')}",
                'subtitle': review.get('site_name') or t.get('title', ''),
                'due': None,
                'trial_id': review['trial_id'],
                'schedule_review_id': review['id'],
                'priority': 'medium',
            })

    trial_ids = sorted({p['trial_id'] for p in patients if p.get('trial_id')})
    if user['role'] == 'pi' and not pending_reviews and trial_ids:
        # Compatibility for older trials created before per-site submissions
        # existed. Newly shared schedules always use the assigned queue above.
        pending = await db.trials.find(
            {'id': {'$in': trial_ids}, 'schedule_status': {'$nin': ['approved', 'flagged']}},
            {'_id': 0}).to_list(200)
        for t in pending:
            tasks.append({
                'id': f"schedule_review:{t['id']}",
                'type': 'schedule_review',
                'title': f"Review visit schedule · {t.get('protocol_id') or t.get('title', '')}",
                'subtitle': t.get('title', ''),
                'due': None,
                'trial_id': t['id'],
                'priority': 'medium',
            })

    conv_ids = [c['id'] for c in await db.conversations.find(
        {'participant_ids': user['id']}, {'_id': 0, 'id': 1}).to_list(500)]
    unread = 0
    if conv_ids:
        unread = await db.messages.count_documents({
            'conversation_id': {'$in': conv_ids},
            'sender_id': {'$ne': user['id']},
            f'read_by.{user["id"]}': {'$exists': False},
        })
    if unread:
        tasks.append({
            'id': f"unread_messages:{user['id']}",
            'type': 'unread_messages',
            'title': f'{unread} unread message{"s" if unread != 1 else ""}',
            'subtitle': 'Open chat to reply',
            'due': None,
            'count': unread,
            'priority': 'low',
        })

    rank = {'high': 0, 'medium': 1, 'low': 2}
    tasks.sort(key=lambda t: (rank.get(t['priority'], 3), t['due'] or '~'))
    return tasks

# ── Team calendar (site-wide visit schedule for pi/crc) ─────────────────────
TEAM_CALENDAR_MAX_DAYS = 100

@api.get('/calendar/team')
async def team_calendar(from_: Optional[str] = Query(None, alias='from'),
                        to: Optional[str] = Query(None, alias='to'),
                        user=Depends(require_roles('pi', 'crc'))):
    """Read-only site schedule: visit instances for the caller's OWN patients
    (pi_id / crc_id scoping — same rule as GET /patients) within a bounded
    date range, joined with privacy-safe patient identifiers (initials +
    subject label, never full names) and the trial's protocol / condition.

    ?from=&to= are inclusive YYYY-MM-DD bounds. Both omitted → the current
    UTC month; one omitted → a window extending from the other. The span is
    capped at 100 days so a single call can never sweep the whole collection.
    Read-only, so no audit row is written."""
    f = _parse_ymd(from_, 'from')
    t = _parse_ymd(to, 'to')
    if f is None and t is None:                    # default: current month
        f = now().date().replace(day=1)
        t = (f + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif f is None:                                # only `to` given → window back
        assert t is not None
        f = t - timedelta(days=TEAM_CALENDAR_MAX_DAYS - 1)
    elif t is None:                               # only `from` given → window forward
        t = f + timedelta(days=TEAM_CALENDAR_MAX_DAYS - 1)
    assert f is not None and t is not None        # both resolved above (type-narrowing)
    if t < f:
        raise HTTPException(400, 'to must be on or after from')
    if (t - f).days + 1 > TEAM_CALENDAR_MAX_DAYS:
        raise HTTPException(400, f'range cannot exceed {TEAM_CALENDAR_MAX_DAYS} days')

    key = 'pi_id' if user['role'] == 'pi' else 'crc_id'
    patients = await db.patients.find({key: user['id']}, {'_id': 0}).to_list(500)
    if not patients:
        return []
    pmap = {p['id']: p for p in patients}

    start_dt = datetime(f.year, f.month, f.day, tzinfo=timezone.utc)
    end_dt = datetime(t.year, t.month, t.day, tzinfo=timezone.utc) + timedelta(days=1)
    insts = await db.visit_instances.find({
        'patient_id': {'$in': list(pmap)},
        'scheduled_date': {'$gte': start_dt, '$lt': end_dt},
    }, {'_id': 0}).sort([('scheduled_date', 1), ('seq', 1)]).to_list(2000)

    # Joins: one query per collection, not per row.
    staff_ids = sorted({staff_id for patient in patients
                         for staff_id in (patient.get('pi_id'), patient.get('crc_id')) if staff_id})
    staff_map = {u['id']: u async for u in db.users.find(
        {'id': {'$in': staff_ids}}, {'_id': 0, 'id': 1, 'full_name': 1, 'organization': 1})}
    trial_ids = sorted({p['trial_id'] for p in patients if p.get('trial_id')}
                       | {i['trial_id'] for i in insts if i.get('trial_id')})
    trial_map = {tr['id']: tr async for tr in db.trials.find(
        {'id': {'$in': trial_ids}}, {'_id': 0, 'id': 1, 'protocol_id': 1, 'condition': 1})}

    out = []
    for i in insts:
        p = pmap.get(i['patient_id'], {})
        tr = trial_map.get(i.get('trial_id') or p.get('trial_id'), {})
        assigned_pi = staff_map.get(p.get('pi_id'), {})
        assigned_crc = staff_map.get(p.get('crc_id'), {})
        initials = p.get('avatar_initials') \
            or ''.join(w[0].upper() for w in (p.get('full_name') or '').split()[:2]) or 'P'
        out.append({
            'id': i.get('id'),
            'patient_id': i['patient_id'],
            'trial_id': i.get('trial_id') or p.get('trial_id'),
            'name': i.get('name', ''),
            'seq': i.get('seq'),
            'visit_number': i.get('visit_number'),
            'scheduled_date': iso(i.get('scheduled_date')),
            'window_start': iso(i.get('window_start')),
            'window_end': iso(i.get('window_end')),
            'status': i.get('status'),
            'activities': i.get('activities', []),
            # privacy-safe patient identifiers — initials + short subject code
            'patient_initials': initials,
            'subject_label': f"SUBJ-{(i['patient_id'] or '')[:4].upper()}",
            'protocol_id': tr.get('protocol_id', ''),
            'condition': tr.get('condition', ''),
            'pi_name': assigned_pi.get('full_name', ''),
            'crc_name': assigned_crc.get('full_name', ''),
            'site': assigned_pi.get('organization', ''),
        })
    return out

# ── Reminders (patient medication reminders) ──────────────────────────────
async def _clinical_dashboard_payload(user: dict) -> dict:
    """Normalized, relationship-scoped dashboard for PI and CRC users."""
    role = user['role']
    key = 'pi_id' if role == 'pi' else 'crc_id'
    patients = await db.patients.find(
        {key: user['id']}, {'_id': 0}).to_list(500)
    patient_ids = [patient['id'] for patient in patients]
    trials = await list_trials(user)
    tasks = await my_tasks(user)

    today = now().date()
    upcoming = await team_calendar(
        from_=today.isoformat(),
        to=(today + timedelta(days=6)).isoformat(),
        user=user,
    )
    today_visits = [
        visit for visit in upcoming
        if (visit.get('scheduled_date') or '')[:10] == today.isoformat()
        and visit.get('status') not in ('missed', 'screen_fail', 'withdrawn', 'dropout')
    ]
    start_today = datetime(
        today.year, today.month, today.day, tzinfo=timezone.utc)
    overdue_count = 0
    if patient_ids:
        overdue_count = await db.visit_instances.count_documents({
            'patient_id': {'$in': patient_ids},
            'window_end': {'$lt': start_today},
            'status': {'$nin': VISIT_QUEUE_TERMINAL_STATUSES},
        })
    completed_today = sum(
        1 for visit in today_visits if visit.get('status') == 'completed')
    pending_today = sum(
        1 for visit in today_visits
        if visit.get('status') not in VISIT_QUEUE_TERMINAL_STATUSES)

    sponsor_names = sorted({
        (trial.get('sponsor_name') or '').strip()
        for trial in trials if (trial.get('sponsor_name') or '').strip()
    })
    site_names = {
        name for trial in trials for name in (trial.get('site_names') or [])
        if name
    }
    own_org = (user.get('organization') or '').strip()
    if own_org:
        site_names.add(own_org)

    pi_ids = {patient.get('pi_id') for patient in patients
              if patient.get('pi_id')}
    crc_ids = {patient.get('crc_id') for patient in patients
               if patient.get('crc_id')}
    team_ids = pi_ids | crc_ids | {user['id']}
    team_rows = await db.users.find(
        {'id': {'$in': list(team_ids)}},
        {'_id': 0, 'id': 1, 'full_name': 1, 'role': 1,
         'organization': 1, 'avatar_initials': 1},
    ).sort('full_name', 1).to_list(500)

    patient_counts: Dict[str, int] = {}
    for patient in patients:
        trial_id = patient.get('trial_id')
        if trial_id:
            patient_counts[trial_id] = patient_counts.get(trial_id, 0) + 1
    for trial in trials:
        trial['my_patient_count'] = patient_counts.get(trial['id'], 0)

    return serialize({
        'role': role,
        'generated_at': now(),
        'totals': {
            'trials': len(trials),
            'patients': len(patients),
            'sites': len(site_names),
            'sponsors': len(sponsor_names),
            'team': len(team_rows),
            'pis': sum(1 for member in team_rows
                       if member.get('role') == 'pi'),
            'crcs': sum(1 for member in team_rows
                        if member.get('role') == 'crc'),
        },
        'today': {
            'date': today.isoformat(),
            'total': len(today_visits),
            'completed': completed_today,
            'pending': pending_today,
            'overdue': overdue_count,
        },
        'trials': trials,
        'patients': patients,
        'tasks': tasks,
        'today_visits': today_visits,
        'upcoming_visits': upcoming,
        'team': team_rows,
        'sites': sorted(site_names),
        'sponsors': sponsor_names,
        'capabilities': {
            'can_add_patient': True,
            'can_create_trial': role == 'pi',
            'can_review_schedules': role == 'pi',
            'can_complete_visits': True,
            'can_invite_patients': True,
            'can_view_team_calendar': True,
            'can_manage_organization': bool(user.get('org_admin')),
        },
    })


@api.get('/pi/dashboard', dependencies=[Depends(require_roles('pi'))])
async def pi_dashboard(user=Depends(current_user)):
    return await _clinical_dashboard_payload(user)


@api.get('/crc/dashboard', dependencies=[Depends(require_roles('crc'))])
async def crc_dashboard(user=Depends(current_user)):
    return await _clinical_dashboard_payload(user)


class ReminderIn(BaseModel):
    medication: str; dosage: str; time: str; enabled: bool = True

@api.get('/reminders')
async def list_reminders(user=Depends(current_user)):
    return await db.reminders.find({'user_id': user['id']}, {'_id': 0}).sort('time', 1).to_list(100)

@api.post('/reminders')
async def create_reminder(body: ReminderIn, user=Depends(current_user)):
    doc = {'id': str(uuid.uuid4()), 'user_id': user['id'], **body.dict(), 'created_at': now()}
    await db.reminders.insert_one(doc); return serialize(doc)

@api.patch('/reminders/{rid}')
async def update_reminder(rid: str, body: dict, user=Depends(current_user)):
    await db.reminders.update_one({'id': rid, 'user_id': user['id']}, {'$set': {k: v for k, v in body.items() if k in {'enabled', 'time', 'dosage'}}})
    return {'ok': True}

@api.delete('/reminders/{rid}')
async def delete_reminder(rid: str, user=Depends(current_user)):
    await db.reminders.delete_one({'id': rid, 'user_id': user['id']}); return {'ok': True}

# ── Medications + dose logs + adherence ────────────────────────────────────
DOSE_STATUSES = ('taken', 'skipped', 'not_taken', 'remind_later')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TIME_RE = re.compile(r'^\d{2}:\d{2}$')

class ScheduleSlot(BaseModel):
    time: str                      # "08:00"
    label: Optional[str] = ''      # "Morning" / "Evening"

class MedicationIn(BaseModel):
    patient_id: str
    name: str
    dosage: str
    route: Optional[str] = 'oral'
    schedule: List[ScheduleSlot] = []
    start_date: Optional[str] = None   # "YYYY-MM-DD"; defaults to today (UTC)
    end_date: Optional[str] = None     # "YYYY-MM-DD", inclusive
    active: bool = True

class DoseLogIn(BaseModel):
    date: str                          # "YYYY-MM-DD"
    time: str                          # "HH:MM" — a slot from the med's schedule
    status: Literal['taken', 'skipped', 'not_taken', 'remind_later']

def _parse_ymd(value: Optional[str], field: str) -> Optional[date]:
    if value is None:
        return None
    if not _DATE_RE.match(value):
        raise HTTPException(400, f'{field} must be formatted YYYY-MM-DD')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(400, f'{field} is not a valid calendar date')

async def _own_patient_record(user) -> dict:
    """The `patients` row linked to the signed-in patient user."""
    p = await db.patients.find_one({'user_id': user['id']}, {'_id': 0})
    if not p:
        raise HTTPException(404, 'No patient record linked to this account')
    return p

async def _staff_scoped_patient(user, patient_id: Optional[str]) -> dict:
    """pi/crc access check: the patient must be one of THEIR patients
    (same scoping GET /patients applies — pi_id / crc_id match)."""
    if not patient_id:
        raise HTTPException(400, 'patient_id is required for staff')
    p = await db.patients.find_one({'id': patient_id}, {'_id': 0})
    if not p:
        raise HTTPException(404, 'Patient not found')
    key = 'pi_id' if user['role'] == 'pi' else 'crc_id'
    if p.get(key) != user['id']:
        raise HTTPException(403, 'You do not manage this patient')
    return p

async def _resolve_patient_scope(user, patient_id: Optional[str]) -> dict:
    """Patient → own record (ignores ?patient_id=); pi/crc → their patient."""
    if user['role'] == 'patient':
        return await _own_patient_record(user)
    return await _staff_scoped_patient(user, patient_id)

@api.get('/medications')
async def list_medications(patient_id: Optional[str] = None,
                           user=Depends(require_roles('patient', 'pi', 'crc'))):
    p = await _resolve_patient_scope(user, patient_id)
    return await db.medications.find({'patient_id': p['id']}, {'_id': 0}) \
                               .sort('created_at', 1).to_list(200)

@api.post('/medications')
async def create_medication(body: MedicationIn, user=Depends(require_roles('pi', 'crc'))):
    p = await _staff_scoped_patient(user, body.patient_id)
    start = _parse_ymd(body.start_date, 'start_date') or now().date()
    end = _parse_ymd(body.end_date, 'end_date')
    if end and end < start:
        raise HTTPException(400, 'end_date cannot be before start_date')
    for slot in body.schedule:
        if not _TIME_RE.match(slot.time):
            raise HTTPException(400, 'schedule times must be formatted HH:MM')
    mid = str(uuid.uuid4())
    doc = {
        'id': mid,
        'patient_id': p['id'],
        'trial_id': p.get('trial_id'),
        'name': body.name.strip(),
        'dosage': body.dosage.strip(),
        'route': (body.route or 'oral').strip(),
        'schedule': [{'time': s.time, 'label': s.label or ''} for s in body.schedule],
        'start_date': start.isoformat(),
        'end_date': end.isoformat() if end else None,
        'active': body.active,
        'created_by': user['id'],
        'created_at': now(),
    }
    await db.medications.insert_one(doc)
    await write_audit(user, 'medication.create',
                      f"Prescribed {doc['name']} {doc['dosage']} to {p.get('full_name', p['id'])}",
                      target_id=mid, patient_id=p['id'], trial_id=doc['trial_id'])
    return serialize(doc)

async def _med_for_access(medication_id: str, user) -> dict:
    """Load a medication and enforce access: the patient it belongs to, or
    pi/crc staff who manage that patient."""
    med = await db.medications.find_one({'id': medication_id}, {'_id': 0})
    if not med:
        raise HTTPException(404, 'Medication not found')
    if user['role'] == 'patient':
        p = await _own_patient_record(user)
        if med['patient_id'] != p['id']:
            raise HTTPException(403, 'This medication belongs to another patient')
    else:
        await _staff_scoped_patient(user, med['patient_id'])
    return med

@api.post('/medications/{medication_id}/doses')
async def log_dose(medication_id: str, body: DoseLogIn,
                   user=Depends(require_roles('patient'))):
    """Idempotent upsert keyed on (medication_id, date, time): re-logging the
    same slot replaces its status (same row id), never duplicates."""
    med = await _med_for_access(medication_id, user)
    _parse_ymd(body.date, 'date')
    if not _TIME_RE.match(body.time):
        raise HTTPException(400, 'time must be formatted HH:MM')
    n = now()
    key = {'medication_id': medication_id, 'date': body.date, 'time': body.time}
    await db.dose_logs.update_one(
        key,
        {'$set': {'status': body.status, 'logged_at': n},
         '$setOnInsert': {'id': str(uuid.uuid4()), 'patient_id': med['patient_id'], **key}},
        upsert=True,
    )
    log = await db.dose_logs.find_one(key, {'_id': 0})
    await write_audit(user, 'dose.log',
                      f"Logged {med['name']} {body.date} {body.time} as {body.status}",
                      target_id=log['id'], medication_id=medication_id,
                      patient_id=med['patient_id'])
    return log

@api.get('/medications/{medication_id}/doses')
async def list_doses(medication_id: str,
                     from_: Optional[str] = Query(None, alias='from'),
                     to: Optional[str] = Query(None, alias='to'),
                     user=Depends(require_roles('patient', 'pi', 'crc'))):
    """Dose history for one medication, optionally windowed by ?from=&to=
    (inclusive YYYY-MM-DD bounds — lexicographic compare is safe for ISO dates)."""
    await _med_for_access(medication_id, user)
    q: Dict = {'medication_id': medication_id}
    date_q: Dict = {}
    if _parse_ymd(from_, 'from'):
        date_q['$gte'] = from_
    if _parse_ymd(to, 'to'):
        date_q['$lte'] = to
    if date_q:
        q['date'] = date_q
    return await db.dose_logs.find(q, {'_id': 0}).sort([('date', -1), ('time', 1)]).to_list(1000)

async def compute_adherence(patient_id: str) -> dict:
    """Adherence summary for one patient. THE formula (frontend contract):

    - Expected doses ("total"): for every ACTIVE medication, each calendar day
      D with start_date <= D <= min(today, end_date) contributes
      len(schedule) expected doses (meds with an empty schedule contribute 0;
      a future start_date contributes 0 until it arrives). Days are UTC dates,
      inclusive on both ends — a med started today with 2 slots expects 2 today.
    - "taken": dose_logs with status == 'taken' whose (date, time) fall on an
      expected slot of that med (date within the active window AND time equal
      to one of the med's schedule times). Upsert semantics guarantee at most
      one log per (medication_id, date, time), so taken <= total always.
    - "rate": round(taken / total * 100) as an int; 0 when total == 0
      (e.g. 13/14 -> 93).
    - "streak_days": consecutive fully-adherent days (every expected dose that
      day logged 'taken') counting backwards from today; if today is not yet
      complete the streak ends at yesterday instead (an in-progress day never
      breaks the streak). Days with zero expected doses stop the streak.
      Capped at 365.
    - "last7": exactly 7 entries, oldest first, ending today:
      [{date: "YYYY-MM-DD", taken: int, total: int}].
    """
    today = now().date()
    meds = await db.medications.find({'patient_id': patient_id, 'active': True},
                                     {'_id': 0}).to_list(200)
    windows = []                      # (start, end, slot_times) per scorable med
    for m in meds:
        slots = {s['time'] for s in (m.get('schedule') or []) if s.get('time')}
        if not slots:
            continue
        try:
            start = _parse_ymd(m.get('start_date'), 'start_date')
            end = _parse_ymd(m.get('end_date'), 'end_date') or today
        except HTTPException:
            continue                  # malformed stored dates never break the summary
        if not start or start > today:
            continue
        end = min(end, today)
        if end < start:
            continue
        windows.append((m['id'], start, end, slots))

    def expected_on(d: date) -> int:
        return sum(len(slots) for _, s, e, slots in windows if s <= d <= e)

    total = sum(((e - s).days + 1) * len(slots) for _, s, e, slots in windows)

    logs = await db.dose_logs.find({'patient_id': patient_id, 'status': 'taken'},
                                   {'_id': 0}).to_list(5000)
    slot_map = {mid: (s, e, slots) for mid, s, e, slots in windows}
    taken_by_day: Dict[str, int] = {}
    taken = 0
    for log in logs:
        w = slot_map.get(log.get('medication_id'))
        if not w:
            continue
        s, e, slots = w
        try:
            d = date.fromisoformat(log.get('date') or '')
        except ValueError:
            continue
        if s <= d <= e and log.get('time') in slots:
            taken += 1
            taken_by_day[log['date']] = taken_by_day.get(log['date'], 0) + 1

    def day_stats(d: date):
        return taken_by_day.get(d.isoformat(), 0), expected_on(d)

    rate = round(taken / total * 100) if total else 0

    streak = 0
    t_taken, t_total = day_stats(today)
    cursor = today if (t_total and t_taken >= t_total) else today - timedelta(days=1)
    while streak < 365:
        d_taken, d_total = day_stats(cursor)
        if not d_total or d_taken < d_total:
            break
        streak += 1
        cursor -= timedelta(days=1)

    last7 = []
    for k in range(6, -1, -1):
        d = today - timedelta(days=k)
        d_taken, d_total = day_stats(d)
        last7.append({'date': d.isoformat(), 'taken': d_taken, 'total': d_total})

    return {'rate': rate, 'taken': taken, 'total': total,
            'streak_days': streak, 'last7': last7}

@api.get('/adherence')
async def get_adherence(patient_id: Optional[str] = None,
                        user=Depends(require_roles('patient', 'pi', 'crc'))):
    p = await _resolve_patient_scope(user, patient_id)
    return await compute_adherence(p['id'])

# ── Invitations (invite patient/team via email/SMS) ───────────────────────
INVITE_TTL_DAYS = 3

class InvitationIn(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    role: Optional[Role] = 'patient'
    trial_id: Optional[str] = None
    organization: Optional[str] = None   # defaults to the inviter's org
    site: Optional[str] = None

def normalize_invite_code(value: Optional[str]) -> str:
    """Canonical friendly code while retaining legacy UUID-token support."""
    raw = str(value or '').strip()
    if not raw:
        return ''
    raw = raw.rstrip('/').rsplit('/', 1)[-1].split('?', 1)[0].strip()
    compact = re.sub(r'[^A-Za-z0-9]', '', raw)
    if compact[:3].upper() == 'MTB' and len(compact) in (7, 11):
        suffix = compact[3:].upper()
        return 'MTB-' + '-'.join(
            suffix[index:index + 4] for index in range(0, len(suffix), 4))
    if re.fullmatch(r'[A-Fa-f0-9]{32}', compact):
        return compact.lower()
    return raw

def new_invite_code() -> str:
    suffix = uuid.uuid4().hex[:8].upper()
    return f'MTB-{suffix[:4]}-{suffix[4:]}'

async def _find_invitation_by_code(value: str, projection=None):
    raw = str(value or '').strip()
    normalized = normalize_invite_code(raw)
    candidates = list(dict.fromkeys(
        candidate for candidate in (normalized, raw) if candidate))
    return await db.invitations.find_one(
        {'token': {'$in': candidates}}, projection)

def _invite_link(token: str) -> str:
    base = os.environ.get('PUBLIC_APP_URL', 'https://my-trial-board.app')
    return f"{base}/invite/{normalize_invite_code(token)}"

def _can_manage_invitation(inv: dict, user: dict) -> bool:
    """The inviter — or anyone in the same organization — may manage it."""
    return inv.get('invited_by') == user['id'] or \
        bool(inv.get('org')) and inv.get('org') == user.get('organization')

@api.post('/invitations', dependencies=[Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))])
async def create_invitation(body: InvitationIn, user=Depends(current_user)):
    invite_role = body.role or 'patient'
    if invite_role != 'patient' and not user.get('org_admin'):
        raise HTTPException(
            403, 'Organization Admin access is required to invite members')
    if not body.email and not body.phone:
        raise HTTPException(400, 'Email or phone required')
    token = new_invite_code()
    doc = {
        'id': str(uuid.uuid4()), 'token': token,
        'email': (body.email or '').lower(), 'phone': body.phone or '',
        'full_name': body.full_name or '', 'designation': body.designation or '',
        'role': invite_role,
        'trial_id': body.trial_id, 'invited_by': user['id'],
        'supervising_pi_id': user['id'] if user.get('role') == 'pi' and (body.role or '').lower() == 'crc' else '',
        'org': (body.organization or user.get('organization') or '').strip(),
        'site': (body.site or '').strip(),
        'inviter_name': user.get('full_name') or '',
        'inviter_organization': (
            body.organization or user.get('organization') or '').strip(),
        'status': 'pending', 'created_at': now(),
        'expires_at': now() + timedelta(days=INVITE_TTL_DAYS),
        'resend_count': 0,
    }
    await db.invitations.insert_one(doc)
    await write_audit(user, 'invitation.create',
                      f"Invited {doc['email'] or doc['phone']} as {doc['role']}",
                      target_id=doc['id'])
    # Real email sending is wired via EMAIL_API_KEY env (Resend) — falls back to logging in dev.
    invite_link = _invite_link(token)
    if body.email:
        try:
            await run_in_threadpool(
                otp_service.send_invitation_email,
                body.email,
                invite_link,
                body.full_name or '',
                doc['inviter_name'],
                doc['inviter_organization'],
            )
        except (otp_service.OTPConfigError, otp_service.OTPDeliveryError):
            await db.invitations.delete_one({'id': doc['id']})
            raise HTTPException(502, 'The invitation email could not be delivered.')
    logging.info(f'INVITATION: link={invite_link} email={body.email} phone={body.phone}')
    return {**serialize(doc), 'invite_link': invite_link}

@api.get('/invitations')
async def list_invitations(user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    """Invitations sent by the caller or anyone in their organization."""
    org = (user.get('organization') or '').strip()
    ors = [{'invited_by': user['id']}] + ([{'org': org}] if org else [])
    return await db.invitations.find({'$or': ors}, {'_id': 0}).sort('created_at', -1).to_list(200)

def _invitation_status(inv: dict) -> str:
    """Effective status: a pending invitation past its expiry reads as expired."""
    st = inv.get('status', 'pending')
    exp = inv.get('expires_at')
    if st == 'pending' and exp and exp < now():
        return 'expired'
    return st

@api.get('/invitations/{token}')
async def resolve_invitation(token: str):
    """Public: resolve an invite token for the accept screen."""
    inv = await _find_invitation_by_code(token, {'_id': 0})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    inviter = await db.users.find_one(
        {'id': inv.get('invited_by')},
        {'_id': 0, 'full_name': 1, 'organization': 1},
    )
    # Patient invitations retain the profile entered by the research team.
    # Return only registration-safe fields on this public endpoint.
    patient_data = inv.get('patient_data') or {}
    return {
        'org': inv.get('org', ''), 'site': inv.get('site', ''),
        'role': inv.get('role'), 'inviter': (inviter or {}).get('full_name', ''),
        'admin_name': (inviter or {}).get('full_name', ''),
        'org_name': inv.get('org') or (inviter or {}).get('organization', ''),
        'full_name': inv.get('full_name', ''),
        'designation': inv.get('designation', ''),
        'phone': inv.get('phone', ''),
        'dob': patient_data.get('dob', ''),
        'gender': patient_data.get('gender', ''),
        'language': patient_data.get('language', ''),
        'email': inv.get('email', ''), 'status': _invitation_status(inv),
        'expires_at': iso(inv.get('expires_at')),
    }

class InvitationAcceptIn(BaseModel):
    full_name: Optional[str] = ''
    designation: Optional[str] = ''
    phone: Optional[str] = ''
    role: Optional[Role] = None

@api.post('/invitations/{token}/accept')
async def accept_invitation(token: str, body: Optional[InvitationAcceptIn] = None):
    """Validate an invite and return registration prefills without consuming it."""
    inv = await _find_invitation_by_code(token)
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    st = _invitation_status(inv)
    if st != 'pending':
        raise HTTPException(400, f'This invitation is {st} and can no longer be accepted')
    registration_details = {
        'full_name': (body.full_name if body else inv.get('full_name')) or '',
        'designation': (body.designation if body else inv.get('designation')) or '',
        'phone': (body.phone if body else inv.get('phone')) or '',
        'role': inv.get('role') or 'patient',
    }
    return {
        'ok': True, 'status': 'pending', 'registration_required': True,
        'email': inv.get('email', ''), 'org': inv.get('org', ''),
        **registration_details,
    }

@api.post('/invitations/{invitation_id}/resend')
async def resend_invitation(invitation_id: str, user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    inv = await db.invitations.find_one({'id': invitation_id})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    if not _can_manage_invitation(inv, user):
        raise HTTPException(403, 'You can only manage invitations from your organization')
    if _invitation_status(inv) not in ('pending', 'expired'):
        raise HTTPException(400, 'Only pending invitations can be resent')
    new_exp = now() + timedelta(days=INVITE_TTL_DAYS)
    await db.invitations.update_one(
        {'id': invitation_id},
        {'$set': {'status': 'pending', 'expires_at': new_exp, 'last_sent_at': now()},
         '$inc': {'resend_count': 1}})
    invite_link = _invite_link(inv['token'])
    if inv.get('email'):
        original_inviter = await db.users.find_one(
            {'id': inv.get('invited_by')},
            {'_id': 0, 'full_name': 1, 'organization': 1},
        ) or {}
        try:
            await run_in_threadpool(
                otp_service.send_invitation_email,
                inv['email'],
                invite_link,
                inv.get('full_name', ''),
                inv.get('inviter_name') or original_inviter.get('full_name')
                or user.get('full_name') or '',
                inv.get('inviter_organization')
                or original_inviter.get('organization') or inv.get('org') or '',
            )
        except (otp_service.OTPConfigError, otp_service.OTPDeliveryError):
            raise HTTPException(502, 'The invitation email could not be delivered.')
    logging.info(f"INVITATION RESEND: link={invite_link} email={inv.get('email')} phone={inv.get('phone')}")
    await write_audit(user, 'invitation.resend',
                      f"Invitation for {inv.get('email') or inv.get('phone')} resent",
                      target_id=invitation_id)
    return {'ok': True, 'invite_link': invite_link, 'expires_at': iso(new_exp)}

@api.post('/invitations/{invitation_id}/cancel')
async def cancel_invitation(invitation_id: str, user=Depends(require_roles('pi', 'crc', 'sponsor', 'cro'))):
    inv = await db.invitations.find_one({'id': invitation_id})
    if not inv:
        raise HTTPException(404, 'Invitation not found')
    if not _can_manage_invitation(inv, user):
        raise HTTPException(403, 'You can only manage invitations from your organization')
    if inv.get('status') == 'accepted':
        raise HTTPException(400, 'An accepted invitation cannot be cancelled')
    await db.invitations.update_one({'id': invitation_id}, {'$set': {'status': 'cancelled', 'cancelled_at': now()}})
    await write_audit(user, 'invitation.cancel',
                      f"Invitation for {inv.get('email') or inv.get('phone')} cancelled",
                      target_id=invitation_id)
    return {'ok': True, 'status': 'cancelled'}

# ── Shares + PDF export ────────────────────────────────────────────────────
class ShareSiteIn(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=200)
    reviewer_id: Optional[str] = None
    # Present when the picker row came from the platform-wide Site directory
    # instead of an existing sponsor-network record. The server re-resolves
    # this id and never trusts the client-provided site name/PI relationship.
    organization_id: Optional[str] = None

class ShareIn(BaseModel):
    trial_id: str
    via: Literal['email', 'link', 'pdf', 'in_app'] = 'in_app'
    recipients: List[EmailStr] = []
    sites: List[ShareSiteIn] = []
    message: Optional[str] = Field(default='', max_length=300)
    document_id: Optional[str] = None
    document_name: Optional[str] = Field(default='', max_length=255)
    version_note: Optional[str] = Field(default='', max_length=500)

async def _validate_share_site(user: dict, trial: dict, site: ShareSiteIn):
    """Validate a selected site against live trial/network relationships.

    Client-provided names are display metadata only. A reviewer is accepted
    when they are a PI already attached to this trial, or work at a site in the
    sponsor organization's managed network. A platform-directory Site may also
    be selected; in that case the PI must be an active member of that exact
    Site organization. The relationship is persisted only after every selected
    site has passed validation.
    """
    directory_org = None
    if site.organization_id:
        directory_org = await db.organizations.find_one(
            {
                'id': site.organization_id,
                'type': 'site',
                'status': {'$nin': [
                    'merged', 'inactive', 'Inactive', 'suspended', 'Suspended',
                ]},
            },
            {'_id': 0, 'id': 1, 'name': 1, 'address': 1, 'city': 1,
             'state': 1},
        )
        if not directory_org:
            raise HTTPException(400, f'{site.name} is not an active registered Site')

    reviewer = None
    if site.reviewer_id:
        reviewer = await db.users.find_one(
            {
                'id': site.reviewer_id,
                'role': 'pi',
                'status': {'$nin': [
                    'Inactive', 'inactive', 'Removed', 'Suspended', 'suspended',
                ]},
            },
            {'_id': 0, 'id': 1, 'full_name': 1, 'email': 1, 'organization': 1})
        if not reviewer:
            raise HTTPException(400, f'No PI is available for {site.name}')
        if directory_org:
            reviewer_org = str(reviewer.get('organization') or '').strip().casefold()
            directory_name = str(directory_org.get('name') or '').strip().casefold()
            if not reviewer_org or reviewer_org != directory_name:
                raise HTTPException(
                    403, f'{reviewer["full_name"]} is not a PI at {directory_org["name"]}')
            return reviewer
        assigned = await db.patients.find_one(
            {'trial_id': trial['id'], 'pi_id': reviewer['id']}, {'_id': 0, 'id': 1})
        sponsor_org = (user.get('organization') or '').strip()
        org = await db.organizations.find_one(
            {'name': sponsor_org}, {'_id': 0, 'id': 1}) if sponsor_org else None
        network_site = None
        if org:
            network_site = await db.org_sites.find_one({
                'org_id': org['id'],
                'id': site.id,
            }, {'_id': 0, 'id': 1, 'user_id': 1, 'pi_email': 1})
            if network_site:
                linked_user_id = network_site.get('user_id')
                linked_email = str(network_site.get('pi_email') or '').strip().lower()
                reviewer_email = str(reviewer.get('email') or '').strip().lower()
                reviewer_matches = (
                    (linked_user_id and linked_user_id == reviewer['id']) or
                    (linked_email and reviewer_email and linked_email == reviewer_email)
                )
                if not reviewer_matches:
                    network_site = None
        portfolio_site = False
        if (not assigned and not network_site and
                user.get('role') in ('sponsor', 'cro')):
            reviewer_trial_ids = await db.patients.distinct(
                'trial_id', {'pi_id': reviewer['id']})
            for reviewer_trial_id in reviewer_trial_ids:
                if await _trial_in_caller_org(user, reviewer_trial_id):
                    portfolio_site = True
                    break
        if not assigned and not network_site and not portfolio_site:
            raise HTTPException(403, f'{reviewer["full_name"]} is not linked to this trial network')
    else:
        sponsor_org = (user.get('organization') or '').strip()
        org = await db.organizations.find_one(
            {'name': sponsor_org}, {'_id': 0, 'id': 1}) if sponsor_org else None
        network_site = await db.org_sites.find_one(
            {'org_id': org['id'], 'id': site.id}, {'_id': 0, 'id': 1}) if org else None
        if not network_site:
            raise HTTPException(400, f'{site.name} has no assigned PI')
    return reviewer


async def _link_shared_site_to_network(user: dict, trial: dict,
                                       site: ShareSiteIn, reviewer: dict) -> dict:
    """Attach a successfully selected Site to the sponsor's trial network.

    Existing network rows only gain the shared trial id. Directory discoveries
    create a governed network row keyed to the source Site organization so
    later shares and dashboard loads reuse the same relationship.
    """
    sponsor_org_name = (user.get('organization') or '').strip()
    sponsor_org = await db.organizations.find_one(
        {'name': sponsor_org_name}, {'_id': 0, 'id': 1},
    ) if sponsor_org_name else None
    if not sponsor_org:
        raise HTTPException(400, 'Your organization could not be resolved')

    directory_org = None
    if site.organization_id:
        directory_org = await db.organizations.find_one(
            {'id': site.organization_id, 'type': 'site'},
            {'_id': 0, 'id': 1, 'name': 1, 'address': 1, 'city': 1, 'state': 1},
        )
    existing = await db.org_sites.find_one(
        {'id': site.id, 'org_id': sponsor_org['id']}, {'_id': 0})
    if not existing and directory_org:
        existing = await db.org_sites.find_one({
            'org_id': sponsor_org['id'],
            '$or': [
                {'site_org_id': directory_org['id']},
                {'name': directory_org['name']},
            ],
        }, {'_id': 0})

    official_name = (directory_org or {}).get('name') or site.name.strip()
    fields = {
        'name': official_name,
        'status': 'active',
        'updated_at': now(),
        'updated_by': user['id'],
    }
    # A review recipient is not necessarily the site's permanent/default PI.
    # Preserve an existing primary assignment; only initialise it for a new or
    # previously unassigned network site.
    if not existing or not (existing.get('user_id') or existing.get('pi_email')):
        fields.update({
            'pi_name': reviewer.get('full_name') or '',
            'pi_email': reviewer.get('email') or '',
            'user_id': reviewer['id'],
        })
    if directory_org:
        fields.update({
            'site_org_id': directory_org['id'],
            'address': directory_org.get('address') or '',
            'city': directory_org.get('city') or '',
            'state': directory_org.get('state') or '',
        })
    if existing:
        await db.org_sites.update_one(
            {'id': existing['id'], 'org_id': sponsor_org['id']},
            {'$set': fields, '$addToSet': {
                'trial_ids': trial['id'],
                'pi_ids': reviewer['id'],
            }},
        )
        linked_site = {**existing, **fields, 'trial_ids': sorted(set(
            [*(existing.get('trial_ids') or []), trial['id']]
        )), 'pi_ids': sorted(set(
            [*(existing.get('pi_ids') or []), reviewer['id']]
        ))}
    else:
        # A new row is only valid for an explicitly selected directory Site.
        # Other legacy share paths retain their portfolio validation semantics.
        if not directory_org:
            return {'id': site.id, 'name': site.name}
        linked_site = {
            'id': str(uuid.uuid4()),
            'org_id': sponsor_org['id'],
            **fields,
            'trial_ids': [trial['id']],
            'pi_ids': [reviewer['id']],
            'trial_targets': {},
            'access_type': 'restricted',
            'created_at': now(),
            'created_by': user['id'],
        }
        await db.org_sites.insert_one(linked_site)

    if directory_org:
        # The review itself is reviewer-scoped, but the accepted trial
        # relationship is what also makes the shared trial visible in the PI's
        # normal trial list and enables subsequent patient enrollment.
        reviewer_email = str(reviewer.get('email') or '').strip().lower()
        invitation_contacts = [{'accepted_user_id': reviewer['id']}]
        if reviewer_email:
            invitation_contacts.append({'email': reviewer_email})
        invitation = await db.invitations.find_one({
            'trial_id': trial['id'],
            'role': 'pi',
            '$or': invitation_contacts,
            'status': {'$in': ['pending', 'accepted']},
        }, {'_id': 0})
        accepted_fields = {
            'status': 'accepted',
            'accepted_user_id': reviewer['id'],
            'accepted_at': now(),
        }
        if invitation:
            await db.invitations.update_one(
                {'id': invitation['id']}, {'$set': accepted_fields})
        else:
            await db.invitations.insert_one({
                'id': str(uuid.uuid4()),
                'token': new_invite_code(),
                'email': reviewer_email,
                'phone': '',
                'full_name': reviewer.get('full_name') or '',
                'role': 'pi',
                'trial_id': trial['id'],
                'invited_by': user['id'],
                'org': directory_org['name'],
                'organization': directory_org['name'],
                **accepted_fields,
                'created_at': now(),
                'expires_at': now() + timedelta(days=INVITE_TTL_DAYS),
                'resend_count': 0,
            })
    return linked_site


_SCHEDULE_DIFF_FIELDS = (
    'visit_number', 'name', 'day_offset', 'day_end', 'source_day_label',
    'anchor_study_day', 'includes_day_zero', 'hour_offset',
    'hour_offset_basis', 'hour_end', 'relative_to', 'relative_offset_days',
    'period', 'arm_label', 'arm', 'window_days', 'window_before',
    'window_after', 'activities', 'procedures', 'operational_constraints',
    'checklist', 'clinical_tasks', 'admin_tasks', 'comments',
    'extraction_warning', 'review_status', 'extracted_from_protocol',
    'field_evidence',
)


def _schedule_visit_diff(previous: List[dict], current: List[dict]) -> List[dict]:
    """Return a field-level diff between two immutable visit snapshots."""
    previous_by_id = {row.get('id'): row for row in previous if row.get('id')}
    current_by_id = {row.get('id'): row for row in current if row.get('id')}
    changed = []
    ordered_ids = [
        *[row['id'] for row in current if row.get('id')],
        *[row['id'] for row in previous
          if row.get('id') and row['id'] not in current_by_id],
    ]
    for visit_id in ordered_ids:
        before = previous_by_id.get(visit_id)
        after = current_by_id.get(visit_id)
        if before is None:
            change_type = 'added'
            changed_fields = [
                field for field in _SCHEDULE_DIFF_FIELDS if field in (after or {})
            ]
        elif after is None:
            change_type = 'removed'
            changed_fields = [
                field for field in _SCHEDULE_DIFF_FIELDS if field in before
            ]
        else:
            changed_fields = [
                field for field in _SCHEDULE_DIFF_FIELDS
                if before.get(field) != after.get(field)
            ]
            if not changed_fields:
                continue
            change_type = 'modified'
        display = after or before or {}
        changed.append({
            'id': visit_id,
            'visit_number': display.get('visit_number'),
            'name': display.get('name') or '',
            'change_type': change_type,
            'changed_fields': changed_fields,
            'before': before,
            'after': after,
        })
    return changed


def _shared_document_metadata(document: Optional[dict]) -> Optional[dict]:
    if not document:
        return None
    return {
        'id': document.get('id'),
        'name': document.get('name') or '',
        'content_type': document.get('content_type') or '',
        'size': document.get('size'),
        'url': f"/api/files/{document['id']}" if document.get('id') else '',
        'created_at': document.get('created_at'),
    }


@api.post('/shares', dependencies=[Depends(require_roles('sponsor', 'cro', 'pi'))])
async def create_share(body: ShareIn, user=Depends(current_user)):
    trial = await db.trials.find_one({'id': body.trial_id}, {'_id': 0})
    if not trial:
        raise HTTPException(404, 'Trial not found')
    if not await _can_access_trial(user, trial):
        raise HTTPException(403, 'You do not have access to this trial')
    selected_document = None
    if body.document_id:
        selected_document = await db.files.find_one(
            {'id': body.document_id}, {'_id': 0, 'key': 0})
        if not selected_document:
            raise HTTPException(404, 'Selected document was not found')
        scope = selected_document.get('scope') or {}
        if scope.get('type') != 'trial' or scope.get('id') != body.trial_id:
            raise HTTPException(400, 'Selected document does not belong to this trial')
        if not await _file_access_allowed(user, selected_document):
            raise HTTPException(403, 'You do not have access to the selected document')
    validated_sites = []
    for site in body.sites:
        validated_sites.append((site, await _validate_share_site(user, trial, site)))
    if body.via == 'in_app' and (
        not validated_sites or any(reviewer is None for _, reviewer in validated_sites)
    ):
        raise HTTPException(400, 'Select at least one site with an assigned PI for in-app sharing')

    linked_sites = []
    for site, reviewer in validated_sites:
        network_site = (
            await _link_shared_site_to_network(user, trial, site, reviewer)
            if reviewer else {'id': site.id, 'name': site.name}
        )
        linked_sites.append((site, reviewer, network_site))

    visit_snapshot = await db.visits.find(
        {'trial_id': body.trial_id}, {'_id': 0},
    ).sort('visit_number', 1).to_list(500)
    previous_version = await db.schedule_versions.find_one(
        {'trial_id': body.trial_id},
        {'_id': 0},
        sort=[('version', -1)],
    )
    changed_visits = _schedule_visit_diff(
        (previous_version or {}).get('visits') or [], visit_snapshot)
    versioned_trial = await db.trials.find_one_and_update(
        {'id': body.trial_id},
        {
            '$inc': {'schedule_version': 1},
            '$set': {
                'schedule_modified_at': now(),
                'schedule_modified_by': user['id'],
            },
        },
        projection={'_id': 0, 'schedule_version': 1},
        return_document=ReturnDocument.AFTER,
    )
    schedule_version = (versioned_trial or {}).get('schedule_version', 1)
    version_id = str(uuid.uuid4())
    document_metadata = _shared_document_metadata(selected_document)
    version_doc = {
        'id': version_id,
        'trial_id': body.trial_id,
        'version': schedule_version,
        'visits': visit_snapshot,
        'changed_visits': changed_visits,
        'version_note': (body.version_note or '').strip(),
        'document': document_metadata,
        'document_id': body.document_id or None,
        'document_name': (
            (body.document_name or '').strip()
            or (selected_document or {}).get('name', '')
        ),
        'created_by': user['id'],
        'created_by_name': user.get('full_name') or '',
        'created_at': now(),
    }
    await db.schedule_versions.insert_one(version_doc)

    token = uuid.uuid4().hex
    doc = {'id': str(uuid.uuid4()), 'token': token, 'trial_id': body.trial_id, 'via': body.via,
           'recipients': body.recipients, 'created_by': user['id'], 'created_at': now(),
           'expires_at': now() + timedelta(days=7), 'views': 0,
           'message': (body.message or '').strip(),
           'document_id': body.document_id or None,
           'document_name': ((body.document_name or '').strip()
                             or (selected_document or {}).get('name', '')),
           'version_note': (body.version_note or '').strip(),
           'document': document_metadata,
           'version_id': version_id,
           'schedule_version': schedule_version,
           'visit_snapshot': visit_snapshot,
           'changed_visits': changed_visits,
           'site_ids': [network_site.get('id') or site.id
                        for site, _, network_site in linked_sites]}
    await db.shares.insert_one(doc)
    review_ids = []
    for site, reviewer, network_site in linked_sites:
        rid = str(uuid.uuid4())
        review = {
            'id': rid,
            'share_id': doc['id'],
            'trial_id': body.trial_id,
            'site_id': network_site.get('id') or site.id,
            'site_name': network_site.get('name') or site.name,
            'reviewer_id': reviewer.get('id') if reviewer else None,
            'reviewer_name': reviewer.get('full_name') if reviewer else '',
            'reviewer_email': reviewer.get('email') if reviewer else '',
            'status': 'pending' if reviewer else 'pending_assignment',
            'message': doc['message'],
            'document_id': doc['document_id'],
            'document_name': doc['document_name'] or f"{trial.get('protocol_id') or 'Trial'} Visit Schedule.pdf",
            'document': document_metadata,
            'version_note': doc['version_note'],
            'version_id': version_id,
            'schedule_version': schedule_version,
            'visit_snapshot': visit_snapshot,
            'changed_visits': changed_visits,
            'shared_by': user['id'],
            'shared_by_name': user.get('full_name') or '',
            'shared_by_org': user.get('organization') or '',
            'created_at': now(),
        }
        await db.schedule_reviews.insert_one(review)
        review_ids.append(rid)
        if reviewer:
            await db.notifications.insert_one({
                'id': str(uuid.uuid4()),
                'user_id': reviewer['id'],
                'title': f"Schedule review · {trial.get('protocol_id') or trial.get('title')}",
                'body': doc['message'] or f"{user.get('full_name', 'Sponsor')} shared a visit schedule for review.",
                'kind': 'schedule',
                'trial_id': body.trial_id,
                'schedule_review_id': rid,
                'read': False,
                'created_at': now(),
            })
    if linked_sites:
        await _refresh_trial_schedule_status(body.trial_id)
    await write_audit(user, 'schedule.share',
                      f"Shared schedule for {trial.get('protocol_id') or body.trial_id}",
                      target_id=doc['id'], trial_id=body.trial_id,
                      delivery=body.via, recipient_count=len(body.recipients),
                      site_count=len(linked_sites), review_ids=review_ids,
                      schedule_version=schedule_version,
                      changed_visit_count=len(changed_visits))
    base = os.environ.get('PUBLIC_APP_URL', 'https://my-trial-board.app')
    return {
        **serialize(doc),
        'review_ids': review_ids,
        'share_link': f'{base}/s/{token}',
        'pdf_link': f'/api/shares/{token}/schedule.pdf',
    }

@api.get('/shares/{token}/schedule.pdf')
async def share_pdf(token: str):
    from fastapi.responses import Response as FastResp
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors as rcolors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    import io
    s = await db.shares.find_one({'token': token})
    if not s: raise HTTPException(404, 'Share not found')
    if s.get('expires_at') and s['expires_at'].replace(tzinfo=timezone.utc) < now():
        raise HTTPException(410, 'Share link expired')
    await db.shares.update_one({'token': token}, {'$inc': {'views': 1}})
    trial = await db.trials.find_one({'id': s['trial_id']}, {'_id': 0}) or {}
    visits = s.get('visit_snapshot')
    if visits is None:
        visits = await db.visits.find(
            {'trial_id': s['trial_id']}, {'_id': 0},
        ).sort('visit_number', 1).to_list(200)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Visit Schedule · {trial.get('protocol_id', '')}")
    styles = getSampleStyleSheet()
    story = [
        Paragraph(f"<b>{trial.get('protocol_id', 'Trial')} — Visit Schedule</b>", styles['Title']),
        Paragraph(trial.get('title', ''), styles['Heading3']),
        Paragraph(f"Phase: {trial.get('phase', '')} · Condition: {trial.get('condition', '')}", styles['Normal']),
        Spacer(1, 16),
    ]
    rows = [['#', 'Visit name', 'Protocol timing', 'Window', 'Activities']]
    for v in visits:
        label = str(v.get('source_day_label') or '').strip()
        if not label:
            offset = v.get('day_offset')
            if offset is None:
                label = 'Undated / manual review'
            elif offset == 0:
                label = 'Baseline'
            else:
                label = f"Baseline {offset:+d} days"
            if v.get('hour_offset') is not None:
                hour = float(v['hour_offset'])
                label = f"Hour {hour:g}" if v.get('hour_offset_basis') == 'absolute' else (
                    f"{label}, {hour:+g}h")
        before = v.get('window_before')
        after = v.get('window_after')
        if before is not None or after is not None:
            symmetric = int(v.get('window_days') or 0)
            window_label = (
                f"-{symmetric if before is None else before}/"
                f"+{symmetric if after is None else after}d")
        else:
            window_label = (
                f"±{v['window_days']}d" if v.get('window_days') is not None else '-')
        if v.get('day_end') is not None and not v.get('source_day_label'):
            label += f" to baseline {int(v['day_end']):+d} days"
        if v.get('hour_end') is not None and v.get('source_day_label'):
            label += f" (through Hour {float(v['hour_end']):g})"
        rows.append([v.get('visit_number'), v.get('name') or '', label,
                     window_label,
                     ', '.join((v.get('activities') or [])[:3])])
    t = Table(rows, repeatRows=1, colWidths=[28, 140, 60, 50, 200])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), rcolors.HexColor('#A6213F')),
        ('TEXTCOLOR', (0, 0), (-1, 0), rcolors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('GRID', (0, 0), (-1, -1), 0.5, rcolors.HexColor('#E6D6C5')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [rcolors.HexColor('#FBF2E8'), rcolors.white]),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
    ]))
    story.append(t)
    doc.build(story)
    return FastResp(content=buf.getvalue(), media_type='application/pdf', headers={'Content-Disposition': f'inline; filename="schedule-{token[:8]}.pdf"'})

# ── User preferences ──────────────────────────────────────────────────────
@api.get('/preferences')
async def get_prefs(user=Depends(current_user)):
    p = await db.preferences.find_one({'user_id': user['id']}, {'_id': 0}) or {'user_id': user['id'], 'notifications_email': True, 'notifications_push': True, 'notifications_sms': False, 'language': 'en'}
    return p

@api.patch('/preferences')
async def patch_prefs(body: dict, user=Depends(current_user)):
    allow = {
        'notifications_email', 'notifications_push', 'notifications_sms', 'language',
        # granular patient notification preferences (Profile → Notifications)
        'visit_push', 'visit_sms', 'visit_email', 'visit_remind_days',
        'med_push', 'med_sms', 'trial_updates', 'pi_messages', 'system_notifs',
        # calendar settings (Clinical → Calendar → Settings)
        'calendar_default_view', 'week_start', 'reminders_visits', 'reminders_meds',
        'reminder_hours_before',
    }
    upd = {k: v for k, v in body.items() if k in allow}
    await db.preferences.update_one({'user_id': user['id']}, {'$set': upd, '$setOnInsert': {'user_id': user['id']}}, upsert=True)
    return {'ok': True, **upd}

# ── Push notification token registration (Emergent push - real values at deploy time) ─
@api.post('/push/register')
async def register_push(body: dict, user=Depends(current_user)):
    token = body.get('token')
    if not token: raise HTTPException(400, 'Token required')
    await db.push_tokens.update_one({'user_id': user['id'], 'token': token}, {'$set': {'platform': body.get('platform', 'unknown'), 'updated_at': now()}, '$setOnInsert': {'created_at': now()}}, upsert=True)
    return {'ok': True}

def _deidentify_audit_row(row: dict, patient: Optional[dict]) -> dict:
    """Return a sponsor-safe copy of an audit row.

    A sponsor/CRO may audit its own trials but must NEVER see patient PII. We
    (1) drop any direct PII columns, (2) scrub the patient's identifiers out of
    the free-text `detail` and `user_name` (a patient-actor row carries the
    patient's own name), replacing them with a trial-level subject label, and
    (3) relabel a patient actor's name outright. Fail-safe: even with no patient
    row resolved, direct PII columns and a patient-actor name are still stripped.
    """
    row = dict(row)
    for k in ('full_name', 'email', 'phone', 'dob', 'patient_name'):
        row.pop(k, None)
    label = 'Subject'
    pii = []
    if patient:
        label = (patient.get('subject_id') or patient.get('avatar_initials')
                 or 'Subject')
        row['subject_label'] = label
        pii = [patient.get('full_name'), patient.get('email'),
               patient.get('phone'), patient.get('dob')]
    for field in ('detail', 'user_name'):
        val = row.get(field)
        if isinstance(val, str):
            for pv in pii:
                if pv:
                    val = val.replace(str(pv), label)
            row[field] = val
    if row.get('role') == 'patient':           # actor's own name must not leak
        row['user_name'] = label
    row['deidentified'] = True
    return row


async def _scope_audit_logs(user: dict, rows: list) -> list:
    """Fail-closed row-level scoping of audit entries for the calling role.

    patient  → own actions, or rows whose subject is their own patient record.
    pi/crc   → own action rows + rows referencing a patient/trial they own
               (reuses _can_access_patient / _pi_owns_trial — never cross-site).
    sponsor/cro → rows for a trial in their org, DE-IDENTIFIED.
    admin    → unrestricted.
    any other role → nothing.
    """
    role = user['role']
    if role == 'admin':
        return rows
    if role == 'patient':
        own = await db.patients.find_one({'user_id': user['id']}, {'_id': 0, 'id': 1})
        own_pid = own['id'] if own else None
        return [r for r in rows
                if r.get('user_id') == user['id']
                or (own_pid and r.get('patient_id') == own_pid)]
    if role not in ('pi', 'crc', 'sponsor', 'cro', 'smo', 'site'):
        return []

    pcache: Dict[str, Optional[dict]] = {}
    tcache: Dict[str, bool] = {}

    async def _patient(pid):
        if pid not in pcache:
            pcache[pid] = await db.patients.find_one({'id': pid}, {'_id': 0})
        return pcache[pid]

    async def _trial_ok(tid):
        if tid not in tcache:
            if user.get('org_admin'):
                tcache[tid] = await _trial_in_caller_org(user, tid)
            elif role in ('sponsor', 'cro'):
                trial = await db.trials.find_one({'id': tid}, {'_id': 0, 'created_by': 1})
                tcache[tid] = bool(trial) and trial.get('created_by') == user['id']
            else:                              # pi/crc: PI-ownership tie to trial
                trial = await db.trials.find_one({'id': tid}, {'_id': 0})
                tcache[tid] = bool(trial) and await _pi_owns_trial(user, trial)
        return tcache[tid]

    out = []
    for r in rows:
        pid, tid = r.get('patient_id'), r.get('trial_id')
        patient = await _patient(pid) if pid else None
        # Organisation admins also receive account-level activity from their
        # organisation; non-admins remain restricted to an assigned trial.
        allowed = bool(user.get('org_admin') and (r.get('org') or '').strip()
                       == (user.get('organization') or '').strip())
        if not allowed:
            allowed = r.get('user_id') == user['id'] and bool(tid)
        if not allowed and patient is not None:
            allowed = await _can_access_patient(user, patient)
        if not allowed and tid:
            allowed = await _trial_ok(tid)
        if not allowed:
            continue
        if role in ('sponsor', 'cro'):
            # Resolve the subject for scrubbing even when the writer linked the
            # patient only via target_id (e.g. patient.enroll) — a sponsor's
            # free-text detail must never carry a patient name.
            scrub = patient
            if scrub is None and r.get('target_id'):
                scrub = await _patient(r['target_id'])
            out.append(_deidentify_audit_row(r, scrub))
        else:
            out.append(r)
    return out


@api.get('/audit-logs')
async def list_audit(category: Optional[str] = None,
                     from_: Optional[str] = Query(None, alias='from'),
                     to: Optional[str] = Query(None, alias='to'),
                     user=Depends(current_user)):
    """Role-scoped audit trail. Open to every authenticated role; the returned
    rows are scoped fail-closed per role (see _scope_audit_logs) so no caller
    can read another tenant's / site's activity, and a sponsor's view is
    de-identified. ?category= is an exact match; ?from=&to= are inclusive
    YYYY-MM-DD timestamp bounds (same parse + range guard as the calendar)."""
    f = _parse_ymd(from_, 'from')
    t = _parse_ymd(to, 'to')
    if f is not None and t is not None and t < f:
        raise HTTPException(400, 'to must be on or after from')

    q: Dict = {}
    if category:
        q['category'] = category
    if f is not None or t is not None:
        rng: Dict = {}
        if f is not None:
            rng['$gte'] = datetime(f.year, f.month, f.day, tzinfo=timezone.utc)
        if t is not None:
            rng['$lt'] = datetime(t.year, t.month, t.day,
                                  tzinfo=timezone.utc) + timedelta(days=1)
        q['created_at'] = rng

    rows = await db.audit_logs.find(q, {'_id': 0}).sort('created_at', -1).to_list(500)
    scoped = await _scope_audit_logs(user, rows)
    return scoped[:200]

# ── App content (config / FAQ / legal) — DB-backed, lazily seeded ─────────────
# So the app never ships hardcoded copy: these come from the DB and can be edited
# there. Each getter upserts a sensible default the first time it's requested.
DEFAULT_APP_CONFIG = {
    'key': 'app_config',
    'version': '1.0.0',
    'copyright': f'© {now().year} MTB Health Technologies',
    'support_email': 'support@mytrialboard.app',
    'support_phone': '1800-123-4567',
    'support_hours': 'Mon – Fri, 9:00 AM – 6:00 PM',
}
DEFAULT_FAQ = [
    {'order': 1, 'q': 'How do I view my upcoming visit?', 'a': 'Open My Trial from the dashboard — your next visit is highlighted at the top.'},
    {'order': 2, 'q': 'What if I miss a visit?', 'a': 'Contact your research team immediately via the Chat section in the app.'},
    {'order': 3, 'q': 'How do I contact my research team?', 'a': 'Use the Chat icon to message your PI or CRC directly.'},
    {'order': 4, 'q': 'Can I change my phone number?', 'a': 'Yes — Profile & Settings → Edit Profile. Changing it requires OTP verification.'},
    {'order': 5, 'q': 'How are medication reminders set?', 'a': 'Reminders are set by your research team based on your protocol. Manage channels in Notification Preferences.'},
]
DEFAULT_LEGAL = {
    'terms': {
        'key': 'terms', 'version': '2.1', 'effective_date': '01 Jan 2025',
        'blocks': [
            {'heading': '1. Use of Application', 'body': 'This app helps patients manage clinical-trial visit schedules, medication reminders, and communication with research teams.'},
            {'heading': '2. Privacy', 'body': 'Your personal health information is protected in accordance with applicable privacy laws including HIPAA and GDPR.'},
            {'heading': '3. Data Security', 'body': 'We use industry-standard security. All communications are encrypted using TLS 1.3.'},
            {'heading': '4. Medical Disclaimer', 'body': 'This app is informational only and does not replace professional medical advice. Always consult your healthcare provider.'},
            {'heading': '5. User Responsibilities', 'body': 'You are responsible for keeping your login credentials confidential and for all activity under your account.'},
        ],
    },
    'privacy': {
        'key': 'privacy', 'version': '2.1', 'effective_date': '01 Jan 2025',
        'blocks': [
            {'heading': 'Information We Collect', 'body': 'We collect information you provide including contact details, trial-relevant health information, and usage data.'},
            {'heading': 'How We Use Information', 'body': 'To manage your trial participation, send reminders, and facilitate communication with your research team.'},
            {'heading': 'Data Sharing', 'body': 'Shared only with your designated research team and the trial sponsor as required by your protocol.'},
            {'heading': 'Your Rights', 'body': 'You may access, correct, or request deletion of your personal data at any time via your research team.'},
        ],
    },
}

@api.get('/app/config')
async def app_config():
    doc = await db.app_content.find_one({'key': 'app_config'}, {'_id': 0})
    if not doc:
        await db.app_content.update_one({'key': 'app_config'}, {'$setOnInsert': DEFAULT_APP_CONFIG}, upsert=True)
        doc = {k: v for k, v in DEFAULT_APP_CONFIG.items() if k != 'key'}
    doc.pop('key', None)
    return doc


@api.get('/support/contact')
async def support_contact():
    """Public contact data used by pre-auth Help & Support navigation."""
    config = await app_config()
    return {
        'name': 'MTB Platform Support',
        'email': config.get('support_email') or '',
        'phone': config.get('support_phone') or '',
        'hours': config.get('support_hours') or '',
        'channels': {
            'email': bool(config.get('support_email')),
            'phone': bool(config.get('support_phone')),
        },
    }


# ── Role-scoped report exports (approved profile → Reports section) ─────────
# Non-admin report generation. Every dataset is scoped through the caller's
# real trial relationships (_can_access_trial) and Sponsor/CRO exports stay
# de-identified (pseudo subject labels, never patient names/contacts).
ROLE_REPORT_ROLES = ('sponsor', 'cro', 'smo', 'site', 'pi', 'crc')

class RoleReportIn(BaseModel):
    type: Literal['enrolment-summary', 'visit-compliance', 'patient-status']
    format: Literal['pdf', 'xlsx'] = 'pdf'
    trial_ids: List[str] = []
    date_from: Optional[date] = None
    date_to: Optional[date] = None

async def _role_report_scope(user, trial_ids: Optional[List[str]] = None) -> list:
    trials = await db.trials.find({}, {'_id': 0}).to_list(500)
    scoped = [t for t in trials if await _can_access_trial(user, t)]
    if not trial_ids:
        return scoped
    allowed_ids = {trial['id'] for trial in scoped}
    requested_ids = set(trial_ids)
    if not requested_ids.issubset(allowed_ids):
        raise HTTPException(403, 'One or more selected trials are outside your access scope')
    return [trial for trial in scoped if trial['id'] in requested_ids]

def _report_date_in_range(value: Any, date_from: Optional[date], date_to: Optional[date]) -> bool:
    """Compare stored ISO/date/datetime values against an optional inclusive range."""
    if not date_from and not date_to:
        return True
    if isinstance(value, datetime):
        value = value.date()
    elif isinstance(value, str):
        try:
            value = date.fromisoformat(value[:10])
        except ValueError:
            return False
    if not isinstance(value, date):
        return False
    return (date_from is None or value >= date_from) and (date_to is None or value <= date_to)

async def _role_report_rows(user, rtype: str, trial_ids: Optional[List[str]] = None,
                            date_from: Optional[date] = None, date_to: Optional[date] = None):
    trials = await _role_report_scope(user, trial_ids)
    trial_ids = [t['id'] for t in trials]
    by_trial = {t['id']: t for t in trials}
    patients = await db.patients.find(
        {'trial_id': {'$in': trial_ids}}, {'_id': 0}).to_list(10000) if trial_ids else []
    if date_from or date_to:
        patients = [patient for patient in patients if _report_date_in_range(
            patient.get('enrolled_date'), date_from, date_to)]
    deidentified = user['role'] in ('sponsor', 'cro')

    if rtype == 'enrolment-summary':
        headers = ['protocol', 'title', 'trial_status', 'enrolled',
                   'target_enrollment', 'patient_statuses']
        rows = []
        for t in trials:
            enrolled = [p for p in patients if p.get('trial_id') == t['id']]
            buckets: Dict[str, int] = {}
            for p in enrolled:
                key = (p.get('status') or '(not set)').strip() or '(not set)'
                buckets[key] = buckets.get(key, 0) + 1
            breakdown = ', '.join(f'{k}: {v}' for k, v in sorted(buckets.items())) or '—'
            rows.append([t.get('protocol_id') or t['id'], t.get('title') or '',
                         t.get('status') or '', len(enrolled),
                         t.get('target_enrollment') if t.get('target_enrollment') is not None else '',
                         breakdown])
        return headers, rows

    if rtype == 'visit-compliance':
        headers = ['protocol', 'title', 'visits_total', 'completed',
                   'scheduled_upcoming', 'overdue', 'missed']
        today = now().date().isoformat()
        instances = await db.visit_instances.find(
            {'trial_id': {'$in': trial_ids}},
            {'_id': 0, 'trial_id': 1, 'status': 1, 'scheduled_date': 1},
        ).to_list(20000) if trial_ids else []
        if date_from or date_to:
            instances = [instance for instance in instances if _report_date_in_range(
                instance.get('scheduled_date'), date_from, date_to)]
        per: Dict[str, Dict[str, int]] = {}
        for inst in instances:
            tid = inst.get('trial_id')
            if tid not in by_trial:
                continue
            counts = per.setdefault(tid, {'total': 0, 'completed': 0,
                                          'upcoming': 0, 'overdue': 0, 'missed': 0})
            counts['total'] += 1
            status = (inst.get('status') or 'scheduled').lower()
            scheduled = str(inst.get('scheduled_date') or '')[:10]
            if status in ('completed', 'screen_pass', 'screen_fail'):
                counts['completed'] += 1
            elif status == 'missed':
                counts['missed'] += 1
            elif status in ('scheduled', 'upcoming'):
                if scheduled and scheduled < today:
                    counts['overdue'] += 1
                else:
                    counts['upcoming'] += 1
        rows = []
        for t in trials:
            counts = per.get(t['id'], {'total': 0, 'completed': 0,
                                       'upcoming': 0, 'overdue': 0, 'missed': 0})
            rows.append([t.get('protocol_id') or t['id'], t.get('title') or '',
                         counts['total'], counts['completed'], counts['upcoming'],
                         counts['overdue'], counts['missed']])
        return headers, rows

    # patient-status
    headers = ['subject', 'trial', 'status', 'enrolled_date']
    rows = []
    for p in patients:
        trial = by_trial.get(p.get('trial_id')) or {}
        label = (f"SUBJ-{(p.get('id') or '')[-3:]} ({p.get('avatar_initials', '')})"
                 if deidentified else (p.get('full_name') or p.get('subject_id') or p.get('id')))
        rows.append([label, trial.get('protocol_id') or trial.get('title') or '',
                     p.get('status') or '(not set)', p.get('enrolled_date') or ''])
    rows.sort(key=lambda r: (str(r[1]), str(r[0])))
    return headers, rows

@api.post('/reports/generate')
async def role_generate_report(body: RoleReportIn,
                               user=Depends(require_roles(*ROLE_REPORT_ROLES))):
    import admin_routes as _admin  # deferred: admin_routes imports server at startup
    if body.date_from and body.date_to and body.date_from > body.date_to:
        raise HTTPException(422, 'Start date must be on or before end date')
    headers, rows = await _role_report_rows(
        user, body.type, body.trial_ids, body.date_from, body.date_to)
    n = now()
    title = f"{body.type.replace('-', ' ').title()} report"
    data, content_type, extension = _admin._report_bytes(title, headers, rows, body.format)
    key = f'role-reports/{uuid.uuid4()}.{extension}'
    await file_storage.get_storage().save(key, data, content_type)
    doc = {
        'id': str(uuid.uuid4()), 'type': body.type,
        'name': f"{body.type}-{n.strftime('%Y%m%d-%H%M%S')}.{extension}",
        'format': extension, 'content_type': content_type,
        'key': key, 'size': len(data), 'rows': len(rows),
        'created_by': user['id'], 'created_by_name': user.get('full_name', ''),
        'role': user['role'], 'deidentified': user['role'] in ('sponsor', 'cro'),
        'trial_ids': body.trial_ids,
        'date_from': body.date_from.isoformat() if body.date_from else None,
        'date_to': body.date_to.isoformat() if body.date_to else None,
        'created_at': n,
    }
    await db.role_reports.insert_one(doc)
    await write_audit(user, 'report.generate',
                      f"Generated {body.type} {extension.upper()} report ({len(rows)} rows)",
                      target_id=doc['id'])
    return {**serialize(doc), 'download_url': f"/api/reports/{doc['id']}/download"}

@api.get('/reports/options')
async def role_report_options(user=Depends(require_roles(*ROLE_REPORT_ROLES))):
    """Trials the current user may include in a report filter."""
    trials = await _role_report_scope(user)
    return [
        {'id': trial['id'], 'label': trial.get('protocol_id') or trial.get('title') or 'Untitled trial'}
        for trial in sorted(trials, key=lambda row: (row.get('protocol_id') or row.get('title') or '').lower())
    ]

@api.get('/reports/recent')
async def role_recent_reports(user=Depends(require_roles(*ROLE_REPORT_ROLES))):
    rows = await db.role_reports.find(
        {'created_by': user['id']}, {'_id': 0}).sort('created_at', -1).to_list(20)
    for r in rows:
        r['download_url'] = f"/api/reports/{r['id']}/download"
    return rows

@api.get('/reports/{report_id}/download')
async def role_download_report(report_id: str,
                               user=Depends(require_roles(*ROLE_REPORT_ROLES))):
    rep = await db.role_reports.find_one({'id': report_id}, {'_id': 0})
    if not rep:
        raise HTTPException(404, 'Report not found')
    if rep.get('created_by') != user['id']:
        raise HTTPException(403, 'You can only download your own reports')
    from fastapi.responses import Response as FastResp
    try:
        data, _ct = await file_storage.get_storage().open(rep['key'])
    except FileNotFoundError:
        raise HTTPException(404, 'Report file is missing')
    await write_audit(user, 'report.download',
                      f"Downloaded report {rep.get('name')}", target_id=report_id)
    return FastResp(content=data, media_type=rep.get('content_type') or 'application/pdf',
                    headers={'Content-Disposition':
                             f"attachment; filename=\"{rep.get('name', 'report')}\""})


@api.get('/faq')
async def get_faq():
    items = await db.faq.find({}, {'_id': 0}).sort('order', 1).to_list(100)
    if not items:
        await db.faq.insert_many([dict(x) for x in DEFAULT_FAQ])
        items = [{k: v for k, v in x.items()} for x in DEFAULT_FAQ]
    return [{'q': i['q'], 'a': i['a']} for i in items]

@api.get('/legal/{doc_type}')
async def get_legal(doc_type: str):
    if doc_type not in ('terms', 'privacy'):
        raise HTTPException(404, 'Unknown document')
    doc = await db.app_content.find_one({'key': doc_type}, {'_id': 0})
    if not doc:
        default = DEFAULT_LEGAL[doc_type]
        await db.app_content.update_one({'key': doc_type}, {'$setOnInsert': default}, upsert=True)
        doc = default
    return {'version': doc['version'], 'effective_date': doc['effective_date'], 'blocks': doc['blocks']}

@api.post('/legal/accept')
async def accept_legal(user=Depends(current_user)):
    n = now()
    await db.users.update_one({'id': user['id']}, {'$set': {'terms_accepted_at': n}})
    return {'accepted_at': iso(n)}

# ── File uploads (storage abstraction — Task 5.1) ────────────────────────────
# Uploaded files may carry PHI, so download is scope-checked (never a public
# link on the local backend) and delete is owner/admin-only. Storage backend is
# pluggable (local disk now, S3-ready) via storage.get_storage().
FILE_MAX_BYTES = 10 * 1024 * 1024   # 10 MB
# extension -> (allowed content-types, magic-byte prefixes). Both the extension
# AND the declared content-type must be allowed, and the bytes must match the
# type's magic (defence-in-depth against a spoofed content-type / extension).
_ALLOWED_UPLOADS = {
    'pdf':  ({'application/pdf', 'application/octet-stream'}, (b'%PDF-',)),
    'png':  ({'image/png', 'application/octet-stream'}, (b'\x89PNG\r\n\x1a\n',)),
    'jpg':  ({'image/jpeg', 'application/octet-stream'}, (b'\xff\xd8\xff',)),
    'jpeg': ({'image/jpeg', 'application/octet-stream'}, (b'\xff\xd8\xff',)),
    'docx': ({'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
              'application/zip', 'application/octet-stream'}, (b'PK\x03\x04',)),
    # Voice-message recordings (Expo Audio records to .m4a on iOS/Android). ISO-BMFF
    # containers put their 'ftyp' box at offset 4, not offset 0, so this is checked
    # separately below rather than via the generic startswith(magics) scan.
    'm4a':  ({'audio/m4a', 'audio/mp4', 'audio/x-m4a', 'application/octet-stream'}, ()),
}
_FILE_SCOPE_TYPES = ('user', 'trial', 'ticket', 'conversation')


async def _caller_in_trial(user: dict, trial_id: Optional[str]) -> bool:
    """Whether the caller legitimately belongs to a trial (for trial-scoped file
    access). sponsor/cro: their org owns it. pi: _pi_owns_trial. crc: they are a
    listed CRC on an enrolled patient, created it, or share the trial's org.
    Fail-closed for everyone else."""
    if not trial_id:
        return False
    role = user['role']
    if role in ('sponsor', 'cro'):
        return await _trial_in_caller_org(user, trial_id)
    trial = await db.trials.find_one({'id': trial_id}, {'_id': 0})
    if not trial:
        return False
    if role == 'pi':
        return await _pi_owns_trial(user, trial)
    if role == 'crc':
        if trial.get('created_by') == user['id']:
            return True
        org = (user.get('organization') or '').strip()
        if org and (trial.get('sponsor_name') or '').strip() == org:
            return True
        mine = await db.patients.find_one(
            {'trial_id': trial_id, 'crc_id': user['id']}, {'_id': 0, 'id': 1})
        return mine is not None
    return False


async def _file_access_allowed(user: dict, doc: dict) -> bool:
    """Scope gate for GET /api/files/{id}. Owner and admin always pass; otherwise
    the caller must satisfy the file's scope. Fail-closed (unknown scope → deny)."""
    if user['role'] == 'admin' or doc.get('owner_id') == user['id']:
        return True
    scope = doc.get('scope') or {}
    stype, sid = scope.get('type'), scope.get('id')
    if stype == 'user':
        return sid == user['id']
    if stype == 'trial':
        return await _caller_in_trial(user, sid)
    if stype == 'ticket':
        return False   # only owner/admin (handled above); no broad ticket access
    if stype == 'conversation':
        conv = await db.conversations.find_one({'id': sid}, {'_id': 0, 'participant_ids': 1})
        return bool(conv) and user['id'] in (conv.get('participant_ids') or [])
    return False


@api.post('/files')
async def upload_file(file: UploadFile = File(...),
                      scope_type: str = Form('user'),
                      scope_id: Optional[str] = Form(None),
                      user=Depends(current_user)):
    """Upload a file (any authenticated role). 10 MB cap; pdf/png/jpg/docx only
    (validated by extension AND content-type AND magic bytes). The blob is stored
    under a uuid key via the configured storage backend and indexed in `files`
    with a scope (default {type:'user', id: caller}). Returns
    {id, name, size, content_type, url} — url is the presigned link (S3) or the
    authenticated API GET path (local)."""
    name = (file.filename or '').strip() or 'file'
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    spec = _ALLOWED_UPLOADS.get(ext)
    if not spec:
        raise HTTPException(400, 'Unsupported file type (allowed: pdf, png, jpg, docx, m4a)')
    allowed_cts, magics = spec
    ctype = (file.content_type or '').lower().split(';')[0].strip()
    if ctype and ctype not in allowed_cts:
        raise HTTPException(400, 'Content-type does not match the file extension')

    data = await _read_upload_capped(file, FILE_MAX_BYTES, 'File is too large (max 10 MB)')
    if not data:
        raise HTTPException(400, 'The uploaded file is empty')
    magic_ok = (len(data) > 8 and data[4:8] == b'ftyp') if ext == 'm4a' else any(data.startswith(m) for m in magics)
    if not magic_ok:
        raise HTTPException(400, 'File contents do not match the declared type')

    stype = (scope_type or 'user').strip().lower()
    if stype not in _FILE_SCOPE_TYPES:
        raise HTTPException(400, 'Invalid scope type')
    # Default scope is {type:'user', id: caller}; a scope id is required for
    # trial/ticket/conversation scopes and defaults to the caller for a user scope.
    sid = (scope_id or '').strip() or user['id']
    scope = {'type': stype, 'id': sid}
    if stype == 'trial':
        trial = await db.trials.find_one({'id': sid}, {'_id': 0, 'id': 1})
        if not trial:
            raise HTTPException(404, 'Trial not found')
        if user['role'] != 'admin' and not await _caller_in_trial(user, sid):
            raise HTTPException(403, 'You do not have access to upload files to this trial')
    elif stype == 'conversation':
        await _require_conversation_member(sid, user['id'])
    elif stype == 'user' and sid != user['id'] and user['role'] != 'admin':
        raise HTTPException(403, 'You cannot upload files for another user')

    # Prefer the declared content-type; fall back to a canonical one per ext.
    stored_ct = ctype or next(iter(allowed_cts - {'application/octet-stream'}), 'application/octet-stream')
    key = str(uuid.uuid4())
    st = file_storage.get_storage()
    await st.save(key, data, stored_ct)
    doc = {
        'id': str(uuid.uuid4()), 'key': key, 'owner_id': user['id'],
        'scope': scope, 'name': name, 'content_type': stored_ct,
        'size': len(data), 'created_at': now(),
    }
    await db.files.insert_one(doc)
    await write_audit(user, 'file.upload',
                      f'Uploaded {name} ({len(data)} bytes, scope {stype})',
                      target_id=doc['id'])
    url = st.url(key) or f"/api/files/{doc['id']}"
    return {'id': doc['id'], 'name': name, 'size': len(data),
            'content_type': stored_ct, 'url': url}


@api.get('/files')
async def list_files(scope_type: str = Query(...),
                     scope_id: str = Query(...),
                     user=Depends(current_user)):
    """List metadata for files in one authorized scope.

    The response never exposes storage keys. Trial lists use the same access
    check as downloads and return persistent document metadata for trial
    summary/version-history screens.
    """
    stype = (scope_type or '').strip().lower()
    sid = (scope_id or '').strip()
    if stype not in _FILE_SCOPE_TYPES or not sid:
        raise HTTPException(400, 'Valid scope_type and scope_id are required')
    probe = {'owner_id': '', 'scope': {'type': stype, 'id': sid}}
    if not await _file_access_allowed(user, probe):
        raise HTTPException(403, 'You do not have access to this file scope')
    rows = await db.files.find(
        {'scope.type': stype, 'scope.id': sid}, {'_id': 0, 'key': 0}
    ).sort('created_at', -1).to_list(500)
    for row in rows:
        row['url'] = f"/api/files/{row['id']}"
    return serialize(rows)


@api.get('/files/{file_id}')
async def download_file(file_id: str, user=Depends(current_user)):
    """Scope-checked download. Missing → 404; foreign scope → 403. Streams the
    bytes (local) or redirects to the presigned URL (S3)."""
    from fastapi.responses import Response as FastResp, RedirectResponse
    doc = await db.files.find_one({'id': file_id}, {'_id': 0})
    if not doc:
        raise HTTPException(404, 'File not found')
    if not await _file_access_allowed(user, doc):
        raise HTTPException(403, 'You do not have access to this file')
    st = file_storage.get_storage()
    presigned = st.url(doc['key'])
    if presigned:
        return RedirectResponse(presigned, status_code=307)
    try:
        data, _ct = await st.open(doc['key'])
    except FileNotFoundError:
        raise HTTPException(404, 'File blob is missing')
    name = doc.get('name', 'file').replace('"', '')
    return FastResp(
        content=data, media_type=doc.get('content_type', 'application/octet-stream'),
        headers={'Content-Disposition': f'inline; filename="{name}"'})


@api.delete('/files/{file_id}')
async def delete_file(file_id: str, user=Depends(current_user)):
    """Delete a file blob + its db doc. Owner or admin only (else 403)."""
    doc = await db.files.find_one({'id': file_id}, {'_id': 0})
    if not doc:
        raise HTTPException(404, 'File not found')
    if user['role'] != 'admin' and doc.get('owner_id') != user['id']:
        raise HTTPException(403, 'Only the owner or an admin can delete this file')
    try:
        await file_storage.get_storage().delete(doc['key'])
    except Exception as e:
        logging.warning('File blob delete failed for %s: %s', doc['key'], e)
    await db.files.delete_one({'id': file_id})
    await write_audit(user, 'file.delete', f"Deleted {doc.get('name', file_id)}",
                      target_id=file_id)
    return {'ok': True, 'id': file_id}


@api.get('/')
async def root(): return {'app': 'My Trial Board', 'status': 'ok'}

app.include_router(api)

# Replacement schedule APIs are isolated from the legacy Mongo visit scheduler.
# A PostgreSQL connection is opened only when an /api/uctsm endpoint is called.
from app.api.uctsm import create_uctsm_router       # noqa: E402

async def current_uctsm_user(user=Depends(current_user)):
    """Attach the authoritative organization UUID used for SQL tenant scoping."""
    organization = await find_organization_by_name(user.get('organization'))
    if not organization or not organization.get('id'):
        raise HTTPException(403, 'No organization context is available')
    return {**user, 'organization_id': organization['id']}

app.include_router(create_uctsm_router(current_uctsm_user))

@app.middleware('http')
async def disable_legacy_schedule_authoring(request: Request, call_next):
    """Fail closed after cutover instead of silently invoking visit-gap logic."""
    if os.getenv('UCTSM_AUTHORITATIVE', '').strip().lower() in {'1', 'true', 'yes'}:
        path = request.url.path
        method = request.method.upper()
        legacy_authoring = (
            path.endswith('/extract-schedule')
            or path.endswith('/schedule-preview')
            or path.endswith('/schedule-definition')
            or path.startswith('/api/schedules/')
            or path.startswith('/api/schedule-reviews')
            or (path.startswith('/api/visits') and method in {'POST', 'PUT', 'PATCH', 'DELETE'})
        )
        if legacy_authoring and not path.startswith('/api/uctsm/'):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=410,
                content={'detail': 'Legacy visit-gap scheduling is disabled; use /api/uctsm.'},
            )
    return await call_next(request)

# ── Admin + org-admin routers (Task 6.1) ─────────────────────────────────────
# Imported at the bottom on purpose: admin_routes/org_routes import helpers
# (db, write_audit, require_roles, …) back from this module, so they can only
# be imported once those names exist. Both routers carry their own /api/…
# prefixes and their own role gates (admin-only / org-admin-only).
import admin_routes                              # noqa: E402
import org_routes                                # noqa: E402
app.include_router(admin_routes.router)
app.include_router(org_routes.router)
app.include_router(org_routes.trial_access_router)
app.add_middleware(CORSMiddleware, allow_credentials=True, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

async def _ensure_indexes():
    """Create indexes in the background so a slow/unreachable DB never blocks startup."""
    try:
        # Auto-expire abandoned/unverified registrations + throttle windows via TTL.
        await db.pending_registrations.create_index('expires_at', expireAfterSeconds=0)
        await db.pending_contact_changes.create_index('expires_at', expireAfterSeconds=0)
        await db.prelogin_support_requests.create_index('expires_at', expireAfterSeconds=0)
        await db.prelogin_support_requests.create_index('id', unique=True)
        await db.otp_throttle.create_index('expires_at', expireAfterSeconds=0)
        await db.public_api_throttle.create_index('expires_at', expireAfterSeconds=0)
        await db.refresh_tokens.create_index('token_hash', unique=True)
        await db.refresh_tokens.create_index('family_id')
        await db.refresh_tokens.create_index('expires_at', expireAfterSeconds=0)
        # Enforce unique emails at the DB layer (defence-in-depth vs. concurrent signups).
        await db.users.create_index(
            'email', unique=True,
            partialFilterExpression={'email': {'$type': 'string', '$gt': ''}},
        )
        # Per-patient visit instances are always fetched by patient.
        await db.visit_instances.create_index('patient_id')
        # Medications are fetched per patient; the dose upsert key is also
        # unique at the DB layer (defence-in-depth vs. concurrent logging).
        await db.medications.create_index('patient_id')
        await db.dose_logs.create_index(
            [('medication_id', 1), ('date', 1), ('time', 1)], unique=True)
        # Adherence (GET /api/adherence) scans dose_logs by patient.
        await db.dose_logs.create_index('patient_id')
        await db.organizations.create_index(
            'name_key', unique=True,
            partialFilterExpression={'name_key': {'$type': 'string', '$gt': ''}},
        )
        await db.organizations.create_index(
            'google_place_id', unique=True,
            partialFilterExpression={
                'google_place_id': {'$type': 'string', '$gt': ''}},
        )
    except Exception as e:
        logging.warning('Index setup deferred (DB unreachable or existing duplicates?): %s', e)

async def _ensure_admin_seed():
    """Guarantee the platform-admin account exists (admins cannot self-register,
    so a fresh database would otherwise have no way into the admin portal)."""
    try:
        await db.users.update_one(
            {'email': 'admin@mtb.app'},
            {'$setOnInsert': {
                'id': str(uuid.uuid4()), 'role': 'admin', 'full_name': 'Meera Nair',
                'organization': 'MTB Health Technologies', 'phone': '+91 98765 43210',
                'hashed_password': pwd_ctx.hash(SEED_PASSWORD),
                'security_question': '', 'security_answer_hash': '',
                'avatar_initials': 'MN', 'created_at': now(), 'is_online': False,
            }},
            upsert=True)
    except Exception as e:
        logging.warning('Admin seed deferred (DB unreachable?): %s', e)

async def _migrate_organization_ownership():
    """Give pre-existing organizations an owner without changing dashboards."""
    try:
        organizations = await db.organizations.find(
            {'status': {'$ne': 'merged'}}, {'_id': 0}).to_list(5000)
        for organization in organizations:
            name = (organization.get('name') or '').strip()
            if not name:
                continue
            existing_admin = await db.users.find_one({
                'organization': name,
                'org_admin': True,
                'role': {'$ne': 'patient'},
                'status': {'$nin': ['Suspended', 'Deactivated']},
            })
            if existing_admin:
                continue
            owner = await db.users.find_one(
                {
                    'organization': name,
                    'role': {'$nin': ['patient', 'admin']},
                    'status': {'$nin': ['Suspended', 'Deactivated']},
                },
                {'_id': 0},
                sort=[('created_at', 1)],
            )
            if not owner:
                continue
            result = await db.users.update_one(
                {'id': owner['id'], 'org_admin': {'$ne': True}},
                {'$set': {'org_admin': True}},
            )
            if result.modified_count:
                await write_audit(
                    owner, 'organization.owner_backfill',
                    f'Assigned original registrant as administrator of "{name}"',
                    target_id=organization['id'], org_id=organization['id'])
    except Exception as e:
        logging.warning(
            'Organization ownership migration deferred (DB unreachable?): %s', e)

@app.on_event('startup')
async def startup():
    if DEV_OTP_MODE:
        logging.warning('⚠️  DEV_OTP_MODE is ON — fixed OTP "%s" accepted for unconfigured channels. NEVER enable in production.', DEV_OTP_CODE)
    # Fire-and-forget: don't await, so the API serves immediately even if Atlas is down.
    asyncio.create_task(_ensure_indexes())
    # Backfill visit_instances for pre-existing patients (idempotent; logs on failure).
    asyncio.create_task(_migrate_visit_instances())
    # Make sure the platform-admin login exists (idempotent).
    asyncio.create_task(_ensure_admin_seed())
    # Existing owners keep their normal dashboard and gain the admin-entry card.
    asyncio.create_task(_migrate_organization_ownership())
    # Deliver due scheduled broadcasts exactly once (idempotent claim + fan-out).
    asyncio.create_task(admin_routes.broadcast_worker_loop())

@app.on_event('shutdown')
async def shutdown(): client.close()
