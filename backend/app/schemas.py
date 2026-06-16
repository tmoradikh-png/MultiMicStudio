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
    speaker_name: str
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
