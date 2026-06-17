"""Cleanup policy + session-expiry helpers (private beta).

What cleanup DOES:
  * close stale OPEN sessions (inactive past ``session_expiry_minutes``) — marked
    ``ended`` so guests can't keep uploading, but kept in the owner's list,
  * delete failed-upload recordings older than ``cleanup_temp_max_age_hours``
    (their file + row) — these never entered a successful project,
  * delete fully-abandoned, empty sessions (no project, no uploaded audio) that
    are older than the expiry window.

What cleanup NEVER does:
  * touch a session that has a ``ProcessedProject`` (completed/attempted work),
  * delete any final/enhanced project output. Successful outputs are kept for the
    owner. (Private-beta retention: keep outputs, clean only temp/intermediate.)

Run via ``scripts/cleanup.py`` (cron-friendly) or call ``run_cleanup(db)``.
Logs to ``multimic.cleanup`` — no tokens or secrets are logged.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Recording,
    RecordingSession,
    SessionStatus,
    UploadStatus,
)
from app.storage import get_storage, key_to_relpath

logger = logging.getLogger("multimic.cleanup")

_OPEN_STATES = (SessionStatus.created, SessionStatus.recording)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(dt: datetime | None) -> datetime | None:
    """Normalise a (possibly tz-aware) timestamp to naive UTC for comparison.

    SQLite returns naive datetimes; model defaults are tz-aware. Strip tz so both
    sides compare consistently.
    """
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def session_is_expired(
    session: RecordingSession, now: datetime | None = None
) -> bool:
    """True if an OPEN session has been inactive past the expiry window.

    Only open sessions (created/recording) can "expire". Ended/ready/processing/
    failed sessions are handled by their own status (already closed / completed).
    """
    settings = get_settings()
    mins = settings.session_expiry_minutes
    if mins <= 0 or session.status not in _OPEN_STATES:
        return False
    now = now or _naive_utc_now()
    ref = _naive(session.started_at or session.created_at)
    if ref is None:
        return False
    return (now - ref) > timedelta(minutes=mins)


def run_cleanup(
    db: Session, storage=None, now: datetime | None = None
) -> dict[str, int]:
    """Apply the cleanup policy. Returns a summary of actions taken."""
    settings = get_settings()
    storage = storage or get_storage()
    now = now or _naive_utc_now()
    expiry = (
        timedelta(minutes=settings.session_expiry_minutes)
        if settings.session_expiry_minutes > 0
        else None
    )
    temp_age = timedelta(hours=settings.cleanup_temp_max_age_hours)
    summary = {
        "sessions_expired": 0,
        "failed_recordings_deleted": 0,
        "sessions_deleted": 0,
    }

    # 1. Close stale OPEN sessions (keep them in the owner's list; just lock joins).
    if expiry is not None:
        for s in db.query(RecordingSession).filter(
            RecordingSession.status.in_(_OPEN_STATES)
        ):
            ref = _naive(s.started_at or s.created_at)
            if ref is not None and (now - ref) > expiry:
                s.status = SessionStatus.ended
                s.ended_at = now
                summary["sessions_expired"] += 1
                logger.info(
                    "Cleanup: expired stale session %s (inactive since %s)", s.id, ref
                )

    # 2. Delete failed-upload recordings older than the temp window (file + row).
    for r in db.query(Recording).filter(
        Recording.upload_status == UploadStatus.failed
    ):
        created = _naive(r.created_at)
        if created is None or (now - created) <= temp_age:
            continue
        if r.file_url:
            try:
                storage.delete(key_to_relpath(r.file_url))
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Cleanup: could not delete file for failed recording %s", r.id
                )
        db.delete(r)
        summary["failed_recordings_deleted"] += 1
        logger.info("Cleanup: removed failed-upload recording %s", r.id)

    # 3. Delete fully-abandoned, empty sessions older than the expiry window.
    #    A session is kept if it has a project (completed work) OR any uploaded
    #    audio — so completed outputs are never removed.
    if expiry is not None:
        for s in db.query(RecordingSession).filter(
            RecordingSession.status.in_(
                (SessionStatus.created, SessionStatus.recording, SessionStatus.ended)
            )
        ):
            if s.project is not None:
                continue
            if any(rec.upload_status == UploadStatus.uploaded for rec in s.recordings):
                continue
            ref = _naive(s.ended_at or s.started_at or s.created_at)
            if ref is not None and (now - ref) > expiry:
                logger.info("Cleanup: deleting abandoned empty session %s", s.id)
                db.delete(s)
                summary["sessions_deleted"] += 1

    db.commit()
    logger.info("Cleanup summary: %s", summary)
    return summary
