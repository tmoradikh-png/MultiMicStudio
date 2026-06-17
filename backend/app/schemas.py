"""Pydantic schemas (API request/response shapes)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import (
    ParticipantRole,
    ProcessingStatus,
    SessionStatus,
    UploadStatus,
)


# --- Auth ---
class SignupRequest(BaseModel):
    email: EmailStr
    name: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: EmailStr
    name: str
    subscription_plan: str


# --- Sessions ---
class SessionCreate(BaseModel):
    title: str


class SessionJoin(BaseModel):
    code: str
    # Optional display label only. Guests need no account, so a name is not required;
    # the backend assigns "Guest N" when omitted.
    speaker_name: str = ""
    device_name: str = ""
    role: ParticipantRole = ParticipantRole.speaker_mic


class ParticipantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    speaker_name: str
    device_name: str
    role: ParticipantRole
    joined_at: datetime


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    title: str
    code: str
    status: SessionStatus
    current_take_id: str | None = None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    participants: list[ParticipantOut] = []


class SessionStatusOut(BaseModel):
    """Minimal status payload for participant polling (auto-start/stop)."""
    model_config = ConfigDict(from_attributes=True)
    id: str
    status: SessionStatus
    current_take_id: str | None = None
    started_at: datetime | None
    ended_at: datetime | None


class JoinResult(BaseModel):
    session: SessionOut
    participant: ParticipantOut
    # Set only when a no-account guest joins. The phone MUST store this and re-send it
    # (as a Bearer token) for status polling and upload retries. Null for logged-in users.
    guest_token: str | None = None


# --- Recordings ---
class RecordingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    participant_id: str
    take_id: str | None
    file_url: str | None
    duration_seconds: float | None
    sample_rate: int | None
    upload_status: UploadStatus
    processing_status: ProcessingStatus


# --- Projects ---
class AIOutputOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    output_type: str
    content: str
    created_at: datetime


class EnhanceRequest(BaseModel):
    # One of effects.ENHANCEMENT_MODES; validated in the router.
    mode: str = "natural"


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    session_id: str
    final_audio_url: str | None
    final_audio_stereo_url: str | None
    final_audio_enhanced_url: str | None
    enhancement_mode: str | None
    transcript_text: str | None
    summary_text: str | None
    processing_status: ProcessingStatus
    error: str | None
    created_at: datetime
    # Per-participant aligned stems (output_type == "stem") for the demo.
    stems: list[AIOutputOut] = []


class ProjectListItem(BaseModel):
    """Flattened summary for the dashboard list view."""
    session_id: str
    title: str
    status: SessionStatus
    project_id: str | None
    processing_status: ProcessingStatus | None
    final_audio_url: str | None
    created_at: datetime


# --- Outputs + quality badge ---
class OutputItem(BaseModel):
    """One playable/downloadable output role for the dashboard."""
    role: str  # raw_phone_1 | raw_phone_2 | natural_stereo | studio_voice | karaoke | party | mono_downmix
    label: str
    url: str | None
    kind: str  # "raw" | "mix"
    available: bool


class QualitySummaryItem(BaseModel):
    question: str
    answer: str
    detail: str = ""
    good: bool


class QualityBadge(BaseModel):
    ok: bool
    passed: int
    total: int
    failed: int
    baseline_failed: int
    baseline_total: int
    summary: list[QualitySummaryItem] = []


class ProjectOutputs(BaseModel):
    session_id: str
    processing_status: ProcessingStatus
    outputs: list[OutputItem]
    quality: QualityBadge | None = None
