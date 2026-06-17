"""Private-beta upload safety limits.

Pure validation helpers used by the recording-upload route. They read settings at
call time (so live config / tests can adjust limits) and reject bad uploads BEFORE
anything is written to storage or enters processing.

Rejections are logged to the ``multimic.limits`` logger with the session and
participant ids only — never guest tokens, auth tokens, or signing secrets.
"""
from __future__ import annotations

import logging
from io import BytesIO
from typing import BinaryIO

from fastapi import HTTPException

from app.config import get_settings

logger = logging.getLogger("multimic.limits")


def _ext(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[-1].lower()


def validate_filetype(
    filename: str | None, *, session_id: str, participant_id: str
) -> str:
    """Return the lowercased extension if accepted, else raise HTTP 415."""
    settings = get_settings()
    allowed = settings.allowed_audio_extension_set
    ext = _ext(filename)
    if not allowed:  # empty allow-list => accept anything (check disabled)
        return ext
    if ext not in allowed:
        logger.warning(
            "Rejected upload: unsupported type '%s' session=%s participant=%s",
            ext or "(none)",
            session_id,
            participant_id,
        )
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '.{ext or '?'}'. "
                f"Allowed: {', '.join(sorted(allowed))}."
            ),
        )
    return ext


def validate_duration(
    duration_seconds: float | None, *, session_id: str, participant_id: str
) -> None:
    """Reject an over-long recording (client-reported duration) with HTTP 413."""
    settings = get_settings()
    limit = settings.max_recording_seconds
    if duration_seconds is not None and limit > 0 and duration_seconds > limit:
        logger.warning(
            "Rejected upload: too long %.1fs > %.1fs session=%s participant=%s",
            duration_seconds,
            limit,
            session_id,
            participant_id,
        )
        raise HTTPException(
            status_code=413,
            detail=(
                f"Recording is too long ({duration_seconds:.0f}s). "
                f"Maximum is {limit:.0f}s — try a shorter take."
            ),
        )


def read_within_size_limit(
    fileobj: BinaryIO, *, session_id: str, participant_id: str
) -> BytesIO:
    """Read an upload into memory, rejecting oversize (413) or empty (422) files.

    Reads at most ``max_upload_bytes + 1`` so an over-limit file is detected
    without loading the whole thing. Returns a rewound BytesIO ready for storage.
    """
    settings = get_settings()
    limit = settings.max_upload_bytes
    data = fileobj.read(limit + 1) if limit > 0 else fileobj.read()
    if limit > 0 and len(data) > limit:
        logger.warning(
            "Rejected upload: too large > %d bytes session=%s participant=%s",
            limit,
            session_id,
            participant_id,
        )
        raise HTTPException(
            status_code=413,
            detail=(
                f"File is too large. Maximum upload size is "
                f"{max(1, limit // (1024 * 1024))} MB."
            ),
        )
    if len(data) == 0:
        logger.warning(
            "Rejected upload: empty file session=%s participant=%s",
            session_id,
            participant_id,
        )
        raise HTTPException(
            status_code=422,
            detail="The uploaded file is empty or unreadable.",
        )
    return BytesIO(data)
