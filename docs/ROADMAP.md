# MVP → Full Product Alignment

This document keeps the MVP honest: everything we build now should be a true subset
of the full product, never a throwaway. Below, each full-product capability is mapped
to the seams already present in the MVP so later work is additive.

## Service boundaries (stable from day one)

| Layer            | MVP implementation                  | Full product (no re-architecture)              |
| ---------------- | ----------------------------------- | ---------------------------------------------- |
| Auth             | Email + JWT                         | + Google/Apple OAuth (add providers)           |
| Storage          | `LocalStorage` behind `Storage` ABC | Swap to `S3Storage` (same interface)           |
| Job processing   | FastAPI BackgroundTasks + worker fn | Swap dispatcher to Redis/RQ/Celery (same task) |
| Audio engine     | align + mix + transcribe            | + denoise, echo, leveling, silence/filler trim |
| AI outputs       | transcript only                     | summary, clips, captions, blog (new producers) |
| Mobile           | record + upload                     | + roles, QR scan, resumable chunk upload       |
| Web              | list + play + transcript            | + transcript editing, exports, collaboration   |

## Data model is full-product-shaped already

The MVP creates and uses these tables (see `backend/app/models.py`). Columns marked
`(future)` are present now so later features need no migration churn:

- `users` — id, email, name, password_hash, `subscription_plan` (future billing)
- `sessions` — id, owner_user_id, title, status, code, timestamps
- `session_participants` — id, session_id, user_id, device_name, speaker_name, `role`
- `recordings` — id, session_id, participant_id, file_url, local_start_timestamp,
  duration_seconds, sample_rate, upload_status, processing_status
- `processed_projects` — id, session_id, final_audio_url, transcript_text,
  `summary_text` (future), processing_status
- `ai_outputs` — id, project_id, output_type, content  *(table exists; MVP writes
  only the transcript; later: summary, chapters, captions, clips, blog, translation)*

## Synchronization strategy (layered, MVP uses layer 1–2)

1. **Sync marker** — host triggers a clap or app beep; backend finds the peak in each
   file and aligns to it. *(MVP)*
2. **Timestamp + waveform** — device start timestamps + cross-correlation refine the
   offset. *(MVP, in `audio/processing.py`)*
3. **Drift correction** — for long takes, detect clock drift and resample/time-stretch.
   *(Hook present: `processing.detect_drift()` returns 0 in MVP.)*

## Roles (recorder app)

MVP records as a generic Speaker Mic. The `role` column + mobile enum already include
`host`, `speaker_mic`, `backup_recorder`, `camera`, so adding role-specific behavior
later is UI-only.

## Privacy requirements honored in MVP

Visible recording indicator, mic permission prompt, consent reminder before start,
owner can delete recordings, secure auth, transport encryption (HTTPS in prod).
