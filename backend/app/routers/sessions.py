"""Session routes: create, join, list, get, start, stop, delete."""
import secrets
import string
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.auth import generate_guest_token
from app.database import get_db
from app.deps import Principal, get_current_user, get_optional_principal, get_principal
from app.models import (
    RecordingSession,
    SessionParticipant,
    SessionStatus,
    User,
)
from app.schemas import (
    JoinResult,
    SessionCreate,
    SessionJoin,
    SessionOut,
    SessionStatusOut,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])

_CODE_ALPHABET = string.ascii_uppercase + string.digits


def _generate_code(db: Session) -> str:
    # Avoid ambiguous chars; retry on the rare collision.
    alphabet = _CODE_ALPHABET.replace("O", "").replace("0", "").replace("I", "").replace("1", "")
    for _ in range(10):
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        if not db.query(RecordingSession).filter(RecordingSession.code == code).first():
            return code
    raise HTTPException(status_code=500, detail="Could not allocate a session code")


def _get_owned_session(db: Session, session_id: str, user: User) -> RecordingSession:
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not the session owner")
    return session


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RecordingSession:
    session = RecordingSession(
        owner_user_id=user.id, title=payload.title, code=_generate_code(db)
    )
    db.add(session)
    db.flush()
    # The owner is automatically a host participant.
    db.add(
        SessionParticipant(
            session_id=session.id,
            user_id=user.id,
            speaker_name=user.name,
            role="host",
        )
    )
    db.commit()
    db.refresh(session)
    return session


@router.post("/join", response_model=JoinResult)
def join_session(
    payload: SessionJoin,
    db: Session = Depends(get_db),
    principal: Principal | None = Depends(get_optional_principal),
) -> JoinResult:
    """Join a session as an account holder OR as a no-account guest.

    If a valid account token is present the participant is linked to that user.
    Otherwise an anonymous guest participant is created and issued a `guest_token`
    the phone stores and reuses for reconnects / upload retries (so the same device
    always maps to the same participant and never duplicates audio in the mix).

    Reconnect: if the caller presents a guest token that already belongs to THIS
    session, the existing participant is returned unchanged (same token) instead of
    creating a duplicate — this is what makes an app restart safe.
    """
    session = (
        db.query(RecordingSession)
        .filter(RecordingSession.code == payload.code.upper())
        .first()
    )
    if session is None:
        raise HTTPException(status_code=404, detail="Invalid session code")

    # A returning guest whose token is bound to THIS session simply reconnects.
    if (
        principal is not None
        and principal.is_guest
        and principal.participant.session_id == session.id
    ):
        return JoinResult(
            session=session,
            participant=principal.participant,
            guest_token=principal.participant.guest_token,
        )

    if session.status in (SessionStatus.ended, SessionStatus.ready, SessionStatus.processing):
        raise HTTPException(status_code=409, detail="Session is no longer open to join")

    user = principal.user if principal is not None else None
    name = (payload.speaker_name or "").strip()
    if not name:
        name = user.name if user is not None else f"Guest {len(session.participants) + 1}"

    guest_token = None if user is not None else generate_guest_token()
    participant = SessionParticipant(
        session_id=session.id,
        user_id=user.id if user is not None else None,
        guest_token=guest_token,
        speaker_name=name,
        device_name=payload.device_name,
        role=payload.role,
    )
    db.add(participant)
    db.commit()
    db.refresh(session)
    db.refresh(participant)
    return JoinResult(session=session, participant=participant, guest_token=guest_token)


@router.get("", response_model=list[SessionOut])
def list_sessions(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[RecordingSession]:
    return (
        db.query(RecordingSession)
        .filter(RecordingSession.owner_user_id == user.id)
        .order_by(RecordingSession.created_at.desc())
        .all()
    )


@router.get("/{session_id}", response_model=SessionOut)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RecordingSession:
    return _get_owned_session(db, session_id, user)


@router.get("/{session_id}/status", response_model=SessionStatusOut)
def get_session_status(
    session_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> RecordingSession:
    """Lightweight status poll usable by ANY participant (owner, account, or guest).

    Joined phones poll this so they can auto-start/stop when the host does, without
    each phone pressing its own button.
    """
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if principal.is_guest:
        is_member = principal.participant.session_id == session_id
    else:
        user = principal.user
        is_member = session.owner_user_id == user.id or any(
            p.user_id == user.id for p in session.participants
        )
    if not is_member:
        raise HTTPException(status_code=403, detail="Not a participant of this session")
    return session


@router.post("/{session_id}/start", response_model=SessionOut)
def start_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RecordingSession:
    session = _get_owned_session(db, session_id, user)
    # Every Start is a NEW take: mint a fresh id so guests detect a new attempt and
    # the mixer only ever uses recordings from this take (no carry-over/echo).
    session.current_take_id = str(uuid.uuid4())
    session.status = SessionStatus.recording
    session.started_at = datetime.now(timezone.utc)
    session.ended_at = None
    db.commit()
    db.refresh(session)
    return session


@router.post("/{session_id}/stop", response_model=SessionOut)
def stop_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RecordingSession:
    session = _get_owned_session(db, session_id, user)
    session.status = SessionStatus.ended
    session.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    # Privacy requirement: owner can delete recordings/sessions.
    session = _get_owned_session(db, session_id, user)
    db.delete(session)
    db.commit()
