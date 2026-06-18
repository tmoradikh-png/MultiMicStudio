"""Project routes: trigger processing, list projects, get project detail."""
import tempfile
from pathlib import Path

import soundfile as sf
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audio import effects, processing, quality
from app.database import get_db
from app.deps import get_current_user
from app.models import (
    ProcessedProject,
    ProcessingStatus,
    RecordingSession,
    SessionParticipant,
    User,
)
from app.schemas import (
    EnhanceRequest,
    OutputItem,
    ProjectListItem,
    ProjectOut,
    ProjectOutputs,
    QualityBadge,
)
from app.storage import get_storage, key_to_relpath
from app.worker.tasks import _select_input_recordings, dispatch_processing

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


def _ensure_enhanced(storage, session_id: str, stereo_url: str, mode: str) -> str:
    """Return the URL for an enhanced preset, rendering + caching it if missing.

    Deterministic key per (session, mode); rendered once from the natural stereo
    mix using the exact app preset code. Length-preserving, so it can never
    stack/duplicate audio. Does NOT change the natural mix or audio logic.
    """
    key = f"projects/{session_id}/final_mix_{mode}.wav"
    if Path(storage.path(key)).exists():
        return storage.public_url(key)
    src = storage.path(key_to_relpath(stereo_url))
    data, sr = sf.read(src, dtype="float32", always_2d=True)
    enhanced = effects.apply_enhancement(data, sr, mode)
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / f"final_mix_{mode}.wav")
        processing.write_wav(enhanced, sr, out)
        with open(out, "rb") as fh:
            return storage.save(key, fh)


def _present(storage, url: str | None) -> str | None:
    """Convert a stored file URL into the link the client should fetch.

    With the MVP defaults this returns the URL unchanged. On a hosted backend it
    yields a signed/time-limited link (local signing) or a presigned object URL
    (S3) — so the dashboard download/share links stay secure with no UI changes.
    """
    if not url:
        return None
    return storage.signed_url(key_to_relpath(url))



@router.get("/{session_id}/outputs", response_model=ProjectOutputs)
def get_project_outputs(
    session_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectOutputs:
    """All output roles (players + downloads) for one session, plus a quality badge.

    Surfaces every role in one place for the dashboard: the two raw phones, the
    natural stereo mix, the studio_voice / karaoke / party presets (rendered on
    demand and cached), and the mono down-mix. The quality badge reuses the QA
    bench checks so it matches the bench report. Read-only; never re-mixes.
    """
    session = db.get(RecordingSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Not the session owner")
    project = session.project
    if project is None:
        raise HTTPException(status_code=404, detail="Project not processed yet")

    storage = get_storage()
    outputs: list[OutputItem] = []

    # Raw phones — the deduped per-participant inputs for the latest take.
    recs = _select_input_recordings(db, session_id)
    part_names = {
        p.id: p.speaker_name
        for p in db.query(SessionParticipant)
        .filter(SessionParticipant.session_id == session_id)
        .all()
    }
    for i, r in enumerate(recs[:2], start=1):
        name = part_names.get(r.participant_id, f"Phone {i}")
        outputs.append(
            OutputItem(
                role=f"raw_phone_{i}",
                label=f"Raw — {name}",
                url=_present(storage, r.file_url),
                kind="raw",
                available=bool(r.file_url),
            )
        )

    stereo_url = project.final_audio_stereo_url
    outputs.append(
        OutputItem(
            role="natural_stereo",
            label="Natural stereo",
            url=_present(storage, stereo_url),
            kind="mix",
            available=bool(stereo_url),
        )
    )

    # Enhanced presets — rendered on demand from the natural stereo mix.
    studio_url = None
    if stereo_url:
        for mode, label in (
            ("studio_voice", "Studio Voice"),
            ("karaoke", "Singing / Karaoke"),
            ("party", "Party / Room"),
        ):
            try:
                url = _ensure_enhanced(storage, session_id, stereo_url, mode)
            except Exception:  # noqa: BLE001
                url = None
            if mode == "studio_voice":
                studio_url = url
            outputs.append(
                OutputItem(
                    role=mode,
                    label=label,
                    url=_present(storage, url),
                    kind="mix",
                    available=bool(url),
                )
            )
    else:
        for mode, label in (
            ("studio_voice", "Studio Voice"),
            ("karaoke", "Singing / Karaoke"),
            ("party", "Party / Room"),
        ):
            outputs.append(
                OutputItem(role=mode, label=label, url=None, kind="mix", available=False)
            )

    mono_url = project.final_audio_url
    outputs.append(
        OutputItem(
            role="mono_downmix",
            label="Mono down-mix",
            url=_present(storage, mono_url),
            kind="mix",
            available=bool(mono_url),
        )
    )

    # Quality badge (best-effort; reuses the QA bench checks).
    badge = None
    try:
        raw_paths = [
            storage.path(key_to_relpath(r.file_url)) for r in recs if r.file_url
        ]
        natural_path = (
            storage.path(key_to_relpath(stereo_url)) if stereo_url else None
        )
        studio_path = (
            storage.path(key_to_relpath(studio_url)) if studio_url else None
        )
        result = quality.evaluate(natural_path, raw_paths, studio_path)
        if result is not None:
            badge = QualityBadge(**result)
    except Exception:  # noqa: BLE001 — badge is advisory; never fail the response
        badge = None

    return ProjectOutputs(
        session_id=session_id,
        processing_status=project.processing_status,
        outputs=outputs,
        quality=badge,
    )

