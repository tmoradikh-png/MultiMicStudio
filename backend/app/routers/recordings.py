"""Recording upload routes.

A participant registers a recording, then uploads the audio file. The endpoint accepts
the full file in one request for the MVP; the contract (register -> upload) already
matches a future resumable/chunked uploader without changing the client flow.
"""
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import (
    Recording,
    RecordingSession,
    SessionParticipant,
    UploadStatus,
    User,
)
from app.schemas import RecordingOut
from app.storage import get_storage

router = APIRouter(prefix="/recordings", tags=["recordings"])


@router.post("", response_model=RecordingOut, status_code=status.HTTP_201_CREATED)
def upload_recording(
    session_id: str = Form(...),
    participant_id: str = Form(...),
    take_id: str | None = Form(None),
    local_start_timestamp: float | None = Form(None),
    sample_rate: int | None = Form(None),
    duration_seconds: float | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Recording:
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    participant = db.get(SessionParticipant, participant_id)
    if participant is None or participant.session_id != session_id:
        raise HTTPException(status_code=404, detail="Participant not in this session")
    # A participant may only upload for their own join (or the owner for any).
    if participant.user_id != user.id and session.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not allowed to upload here")

    # Tie the upload to a take: trust the client value, else fall back to the
    # session's current take so a mix never mixes audio across attempts.
    effective_take_id = take_id or session.current_take_id

    recording = Recording(
        session_id=session_id,
        participant_id=participant_id,
        take_id=effective_take_id,
        local_start_timestamp=local_start_timestamp,
        sample_rate=sample_rate,
        duration_seconds=duration_seconds,
        upload_status=UploadStatus.uploading,
    )
    db.add(recording)
    db.flush()

    storage = get_storage()
    suffix = (file.filename or "audio").split(".")[-1].lower()
    key = f"recordings/{session_id}/{recording.id}.{suffix}"
    try:
        file_url = storage.save(key, file.file)
    except Exception as exc:  # noqa: BLE001
        recording.upload_status = UploadStatus.failed
        db.commit()
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    recording.file_url = file_url
    recording.upload_status = UploadStatus.uploaded
    db.commit()
    db.refresh(recording)
    return recording


@router.get("/by-session/{session_id}", response_model=list[RecordingOut])
def list_session_recordings(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Recording]:
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return (
        db.query(Recording)
        .filter(Recording.session_id == session_id)
        .order_by(Recording.created_at.asc())
        .all()
    )
