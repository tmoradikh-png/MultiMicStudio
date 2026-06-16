"""Speech-to-text transcription.

Two backends:
  * "stub"           -> no model; returns a placeholder so the pipeline runs anywhere.
  * "faster-whisper" -> real transcription if the package + model are installed.

The interface is stable, so swapping in a cloud Whisper API later is a one-file change.
"""
from __future__ import annotations

from app.config import get_settings

settings = get_settings()


def transcribe(audio_path: str) -> str:
    backend = settings.transcription_backend
    if backend == "faster-whisper":
        return _transcribe_faster_whisper(audio_path)
    return _transcribe_stub(audio_path)


def _transcribe_stub(audio_path: str) -> str:
    return (
        "[Transcript placeholder] Real transcription is disabled "
        "(TRANSCRIPTION_BACKEND=stub). Install faster-whisper and set "
        "TRANSCRIPTION_BACKEND=faster-whisper to generate a real transcript."
    )


def _transcribe_faster_whisper(audio_path: str) -> str:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "faster-whisper is not installed. `pip install faster-whisper`."
        ) from exc

    model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
    segments, _info = model.transcribe(audio_path, vad_filter=True)
    return " ".join(seg.text.strip() for seg in segments).strip()
