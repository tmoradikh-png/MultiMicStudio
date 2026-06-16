"""Project routes: trigger processing, list projects, get project detail."""
import tempfile
from pathlib import Path

import soundfile as sf
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audio import effects, processing
from app.database import get_db
from app.deps import get_current_user
from app.models import (
    ProcessedProject,
    ProcessingStatus,
    RecordingSession,
    User,
)
from app.schemas import EnhanceRequest, ProjectListItem, ProjectOut
from app.storage import get_storage, key_to_relpath
from app.worker.tasks import dispatch_processing

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("/process/{session_id}", response_model=ProjectOut, status_code=status.HTTP_202_ACCEPTED)
def process_session(
    session_id: str,
    background: BackgroundTasks,
    body: EnhanceRequest | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProcessedProject:
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not the session owner")

    mode = (body.mode if body else None) or effects.DEFAULT_MODE
    if mode not in effects.ENHANCEMENT_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown enhancement mode: {mode}")

    project = session.project
    if project is None:
        project = ProcessedProject(session_id=session_id)
        db.add(project)
    project.processing_status = ProcessingStatus.pending
    project.error = None
    db.commit()
    db.refresh(project)

    # MVP: run in a background thread. Full product: enqueue on Redis/RQ here instead.
    background.add_task(dispatch_processing, session_id, mode)
    return project


@router.post("/{session_id}/enhance", response_model=ProjectOut)
def enhance_project(
    session_id: str,
    body: EnhanceRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProcessedProject:
    """Re-render an enhancement preset from the EXISTING natural stereo mix.

    This never re-aligns or re-mixes, so it cannot introduce timing drift or
    stacked/duplicated audio. The natural stereo mix is always preserved for
    comparison. Selecting the "natural" mode just clears the enhanced render.
    """
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not the session owner")
    project = session.project
    if project is None:
        raise HTTPException(status_code=404, detail="Project not processed yet")

    mode = body.mode
    if mode not in effects.ENHANCEMENT_MODES:
        raise HTTPException(status_code=400, detail=f"Unknown enhancement mode: {mode}")
    if not project.final_audio_stereo_url:
        raise HTTPException(
            status_code=409,
            detail="No natural stereo mix available to enhance. Re-process first.",
        )

    storage = get_storage()
    if mode == effects.DEFAULT_MODE:
        # Natural is the reference mix itself; drop any enhanced render.
        project.final_audio_enhanced_url = None
        project.enhancement_mode = mode
        db.commit()
        db.refresh(project)
        return project

    src = storage.path(key_to_relpath(project.final_audio_stereo_url))
    data, sr = sf.read(src, dtype="float32", always_2d=True)
    enhanced = effects.apply_enhancement(data, sr, mode)
    key = f"projects/{session_id}/final_mix_{mode}.wav"
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / f"final_mix_{mode}.wav")
        processing.write_wav(enhanced, sr, out)
        with open(out, "rb") as fh:
            url = storage.save(key, fh)
    project.final_audio_enhanced_url = url
    project.enhancement_mode = mode
    db.commit()
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectListItem])
def list_projects(
    db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> list[ProjectListItem]:
    sessions = (
        db.query(RecordingSession)
        .filter(RecordingSession.owner_user_id == user.id)
        .order_by(RecordingSession.created_at.desc())
        .all()
    )
    items: list[ProjectListItem] = []
    for s in sessions:
        p = s.project
        items.append(
            ProjectListItem(
                session_id=s.id,
                title=s.title,
                status=s.status,
                project_id=p.id if p else None,
                processing_status=p.processing_status if p else None,
                final_audio_url=p.final_audio_url if p else None,
                created_at=s.created_at,
            )
        )
    return items


@router.get("/{session_id}", response_model=ProjectOut)
def get_project(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProcessedProject:
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not the session owner")
    if session.project is None:
        raise HTTPException(status_code=404, detail="Project not processed yet")
    return session.project
