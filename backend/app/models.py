"""SQLAlchemy models.

The schema mirrors the full-product data model from the product spec. The MVP only
*writes* a subset of columns/tables, but the shape is final so later features need no
disruptive migrations. Columns commented `(future)` are reserved for later phases.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStatus(str, enum.Enum):
    created = "created"
    recording = "recording"
    ended = "ended"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class ParticipantRole(str, enum.Enum):
    host = "host"
    speaker_mic = "speaker_mic"
    backup_recorder = "backup_recorder"
    camera = "camera"


class UploadStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    uploaded = "uploaded"
    failed = "failed"


class ProcessingStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255))
    subscription_plan: Mapped[str] = mapped_column(String(50), default="free")  # (future billing)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    sessions: Mapped[list[RecordingSession]] = relationship(back_populates="owner")


class RecordingSession(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    # Short human-friendly join code (also encoded into a QR on the client).
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    status: Mapped[SessionStatus] = mapped_column(
        Enum(SessionStatus), default=SessionStatus.created
    )
    # Identifies the CURRENT recording attempt ("take"). A fresh uuid is minted
    # every time the host presses Start, so multiple takes in one login/session are
    # kept separate (guests detect a new take, the mixer only uses the latest one).
    current_take_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    owner: Mapped[User] = relationship(back_populates="sessions")
    participants: Mapped[list[SessionParticipant]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    recordings: Mapped[list[Recording]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    project: Mapped[ProcessedProject | None] = relationship(
        back_populates="session", uselist=False, cascade="all, delete-orphan"
    )


class SessionParticipant(Base):
    __tablename__ = "session_participants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # Anonymous device identity for guests who join WITHOUT an account. The phone
    # stores this token and re-sends it on reconnect / upload retry, so the same
    # physical device always maps to the SAME participant — which is what stops a
    # retried upload from adding a duplicate, stacked copy to the final mix.
    # NULL for account-based participants (e.g. the host).
    guest_token: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    device_name: Mapped[str] = mapped_column(String(255), default="")
    speaker_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[ParticipantRole] = mapped_column(
        Enum(ParticipantRole), default=ParticipantRole.speaker_mic
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped[RecordingSession] = relationship(back_populates="participants")
    recordings: Mapped[list[Recording]] = relationship(back_populates="participant")


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"))
    participant_id: Mapped[str] = mapped_column(ForeignKey("session_participants.id"))
    # Which recording attempt ("take") this file belongs to. The mixer groups by
    # (take_id, participant_id) so old takes never bleed into a new mix.
    take_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    file_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Client-reported epoch ms when local recording started (used for coarse sync).
    local_start_timestamp: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upload_status: Mapped[UploadStatus] = mapped_column(
        Enum(UploadStatus), default=UploadStatus.pending
    )
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.pending
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped[RecordingSession] = relationship(back_populates="recordings")
    participant: Mapped[SessionParticipant] = relationship(back_populates="recordings")


class ProcessedProject(Base):
    __tablename__ = "processed_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), unique=True)
    final_audio_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Optional stereo render with left/right speaker placement (demo quality).
    final_audio_stereo_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    # Optional enhanced render (a preset applied on top of the natural stereo mix).
    final_audio_enhanced_url: Mapped[str | None] = mapped_column(
        String(1024), nullable=True
    )
    enhancement_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # (future AI)
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        Enum(ProcessingStatus), default=ProcessingStatus.pending
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    session: Mapped[RecordingSession] = relationship(back_populates="project")
    ai_outputs: Mapped[list[AIOutput]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    @property
    def stems(self) -> list[AIOutput]:
        """Per-participant aligned stems, surfaced separately from the transcript."""
        return [o for o in self.ai_outputs if o.output_type == "stem"]


class AIOutput(Base):
    """Generic AI artifact store. MVP writes only the transcript; later phases add
    summary, chapters, captions, clips, blog post, translation, etc."""

    __tablename__ = "ai_outputs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("processed_projects.id"))
    output_type: Mapped[str] = mapped_column(String(50))  # "transcript", "summary", ...
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    project: Mapped[ProcessedProject] = relationship(back_populates="ai_outputs")
