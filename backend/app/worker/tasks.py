"""Project processing pipeline.

MVP dispatches this via FastAPI BackgroundTasks (see routers/projects.py). The full
product swaps `dispatch_processing` to enqueue on Redis/RQ/Celery — the task body
(`process_session`) stays identical.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import soundfile as sf

from app.audio import effects, processing, transcription
from app.database import SessionLocal
from app.models import (
    AIOutput,
    ProcessedProject,
    ProcessingStatus,
    Recording,
    RecordingSession,
    SessionStatus,
    UploadStatus,
)
from app.storage import get_storage, key_to_relpath

logger = logging.getLogger("multimic.processing")


def dispatch_processing(session_id: str, mode: str = effects.DEFAULT_MODE) -> None:
    """Run processing now (MVP). Replace body with a queue enqueue in production."""
    process_session(session_id, mode)


def _render_enhanced(
    storage, session_id: str, stereo_local_path: str, mode: str
) -> str | None:
    """Apply an enhancement preset to a stereo WAV and store it.

    Returns the stored URL, or None for the natural reference (no enhanced file).
    Length is preserved by the effects, so this can never stack/duplicate audio.
    """
    if mode == effects.DEFAULT_MODE or mode not in effects.ENHANCEMENT_MODES:
        return None
    data, sr = sf.read(stereo_local_path, dtype="float32", always_2d=True)
    enhanced = effects.apply_enhancement(data, sr, mode)
    key = f"projects/{session_id}/final_mix_{mode}.wav"
    with tempfile.TemporaryDirectory() as tmp:
        out = str(Path(tmp) / f"final_mix_{mode}.wav")
        processing.write_wav(enhanced, sr, out)
        with open(out, "rb") as fh:
            return storage.save(key, fh)


def _select_input_recordings(db, session_id: str) -> list[Recording]:
    """Pick exactly one recording per participant for the LATEST take of a session.

    Rules (from product feedback):
      * only recordings belonging to THIS session,
      * only successfully uploaded files (ignore pending/failed/old retries),
      * restrict to a single take: prefer the session's current_take_id, else the
        newest take present among uploads. Old takes never bleed into a new mix.
      * within that take, keep the LATEST upload per participant (newest created_at),
      * never treat a generated project output (e.g. final_mix.wav) as an input.
    """
    rows = (
        db.query(Recording)
        .filter(Recording.session_id == session_id)
        .filter(Recording.upload_status == UploadStatus.uploaded)
        .order_by(Recording.created_at.asc())
        .all()
    )

    # Drop project outputs / fileless rows up front.
    valid = [
        r
        for r in rows
        if r.file_url and "projects/" not in r.file_url and "final_mix" not in r.file_url
    ]
    if not valid:
        return []

    session = db.get(RecordingSession, session_id)
    target_take = session.current_take_id if session else None
    if target_take is None:
        # No current take recorded (legacy data): use the newest take id seen,
        # falling back to the legacy "no take" bucket if none are tagged.
        takes = [r for r in valid if r.take_id is not None]
        if takes:
            target_take = max(takes, key=lambda r: r.created_at).take_id

    in_take = [r for r in valid if r.take_id == target_take]
    if not in_take:
        # Defensive: if nothing matches the target take (e.g. all legacy/untagged),
        # fall back to all valid rows so processing still produces output.
        in_take = valid

    best_per_participant: dict[str, Recording] = {}
    for r in in_take:
        prev = best_per_participant.get(r.participant_id)
        if prev is None:
            best_per_participant[r.participant_id] = r
            continue
        # Prefer the most complete capture in this take. If duration metadata is
        # missing or equal, fall back to the newest upload.
        prev_dur = prev.duration_seconds or 0.0
        cur_dur = r.duration_seconds or 0.0
        if cur_dur > prev_dur + 0.05:
            best_per_participant[r.participant_id] = r
        elif abs(cur_dur - prev_dur) <= 0.05 and r.created_at > prev.created_at:
            best_per_participant[r.participant_id] = r

    return list(best_per_participant.values())


def process_session(session_id: str, mode: str = effects.DEFAULT_MODE) -> None:
    db = SessionLocal()
    storage = get_storage()
    project: ProcessedProject | None = None
    if mode not in effects.ENHANCEMENT_MODES:
        mode = effects.DEFAULT_MODE
    try:
        session = db.get(RecordingSession, session_id)
        if session is None:
            return

        project = session.project
        if project is None:
            project = ProcessedProject(session_id=session_id)
            db.add(project)
        project.processing_status = ProcessingStatus.processing
        project.error = None
        session.status = SessionStatus.processing
        db.commit()

        recordings = _select_input_recordings(db, session_id)
        if not recordings:
            raise RuntimeError("No uploaded recordings to process for this session.")

        # Guard against accidental truncated uploads (for example a 2s retry)
        # being mixed with a full take. Producing a mix in that case sounds
        # badly delayed/laggy, so fail fast with an actionable message instead.
        if len(recordings) >= 2:
            ds = [r.duration_seconds or 0.0 for r in recordings]
            min_d, max_d = min(ds), max(ds)
            if min_d > 0.0 and min_d <= 3.0 and (max_d - min_d) >= 1.8:
                raise RuntimeError(
                    "One participant upload is much shorter than the others "
                    f"({min_d:.1f}s vs {max_d:.1f}s). "
                    "Re-record this take, then process again."
                )

        take_id = session.current_take_id or (recordings[0].take_id if recordings else None)
        logger.info(
            "Processing session %s take=%s: %d input recording(s) after dedup",
            session_id,
            take_id,
            len(recordings),
        )
        for r in recordings:
            logger.info(
                "  input recording_id=%s take=%s participant_id=%s file=%s "
                "local_start=%s duration=%.2fs",
                r.id,
                r.take_id,
                r.participant_id,
                r.file_url,
                r.local_start_timestamp,
                r.duration_seconds or 0.0,
            )

        inputs = [
            (r.id, storage.path(key_to_relpath(r.file_url)), r.local_start_timestamp)
            for r in recordings
        ]
        # recording_id -> participant_id, for human-readable warnings + stem naming.
        rec_by_id = {r.id: r.participant_id for r in recordings}

        # Output keys. Stereo + stems are demo extras alongside the mono mix.
        mix_key = f"projects/{session_id}/final_mix.wav"
        stereo_key = f"projects/{session_id}/final_mix_stereo.wav"
        with tempfile.TemporaryDirectory() as tmp:
            local_mix = str(Path(tmp) / "final_mix.wav")
            local_stereo = str(Path(tmp) / "final_mix_stereo.wav")
            stems_dir = str(Path(tmp) / "stems")
            Path(stems_dir).mkdir(parents=True, exist_ok=True)
            diag = processing.align_and_mix(
                inputs, local_mix, stereo_dest_path=local_stereo, stems_dir=stems_dir
            )

            # Diagnostics: offsets + alignment outcome + final duration.
            logger.info(
                "Mixed session %s take=%s: inputs=%d sr=%d method=%s final_duration=%.2fs",
                session_id,
                take_id,
                diag.input_count,
                diag.sample_rate,
                diag.alignment_method,
                diag.final_duration_seconds,
            )
            for rid, info in diag.per_track.items():
                logger.info(
                    "  track recording_id=%s participant=%s marker=%s marker_at=%.0fms "
                    "offset=%.1fms duration=%.2fs",
                    rid,
                    rec_by_id.get(rid),
                    info["marker_found"],
                    info["marker_ms"],
                    info["offset_ms"],
                    info["duration_seconds"],
                )
            # Do not silently produce a bad mix: warn when a marker is missing.
            for rid in diag.markers_missing:
                pid = rec_by_id.get(rid)
                logger.warning(
                    "Sync marker not found for participant %s (recording %s) in "
                    "session %s — alignment fell back to cross-correlation and may "
                    "be less accurate. Ensure a clear clap/beep at the start.",
                    pid,
                    rid,
                    session_id,
                )

            # Transcribe the mixed track.
            transcript = transcription.transcribe(local_mix)

            # Persist mixed audio into storage.
            with open(local_mix, "rb") as fh:
                final_url = storage.save(mix_key, fh)
            stereo_url: str | None = None
            enhanced_url: str | None = None
            if diag.stereo_path:
                with open(diag.stereo_path, "rb") as fh:
                    stereo_url = storage.save(stereo_key, fh)
                # Optional enhancement preset applied ON TOP of the natural stereo.
                # Natural stays available as `stereo_url` for comparison.
                enhanced_url = _render_enhanced(
                    storage, session_id, diag.stereo_path, mode
                )
            stem_urls: dict[str, str] = {}  # participant_id -> url
            for rid, stem_path in diag.stem_paths.items():
                pid = rec_by_id.get(rid, rid)
                stem_key = f"projects/{session_id}/stems/{pid}.wav"
                with open(stem_path, "rb") as fh:
                    stem_urls[pid] = storage.save(stem_key, fh)
            logger.info(
                "Outputs for session %s take=%s: mono=%s stereo=%s stems=%s",
                session_id,
                take_id,
                final_url,
                stereo_url,
                list(stem_urls.values()),
            )

        for r in recordings:
            r.processing_status = ProcessingStatus.done

        project.final_audio_url = final_url
        project.final_audio_stereo_url = stereo_url
        project.final_audio_enhanced_url = enhanced_url
        project.enhancement_mode = mode
        project.transcript_text = transcript
        project.processing_status = ProcessingStatus.done
        # Replace any previous outputs (avoid stacking on re-process).
        project.ai_outputs.clear()
        project.ai_outputs.append(
            AIOutput(output_type="transcript", content=transcript)
        )
        for pid, url in stem_urls.items():
            project.ai_outputs.append(
                AIOutput(output_type="stem", content=url)
            )
        session.status = SessionStatus.ready
        db.commit()
    except Exception as exc:  # noqa: BLE001  (record failure for the dashboard)
        logger.exception("Processing failed for session %s", session_id)
        db.rollback()
        if project is not None:
            project.processing_status = ProcessingStatus.failed
            project.error = str(exc)
        session = db.get(RecordingSession, session_id)
        if session is not None:
            session.status = SessionStatus.failed
        db.commit()
    finally:
        db.close()
