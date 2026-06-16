# MultiMic Studio — Backend (FastAPI)

The recording/sync engine: accounts, sessions, uploads, audio alignment + mixing,
and transcription. This is the priority of the first milestone.

## Requirements

- Python 3.11+
- **FFmpeg** (optional): a system FFmpeg on PATH is preferred, but a bundled binary
  (`imageio-ffmpeg`, installed via requirements) is used automatically if none is found.
  - Windows: `winget install Gyan.FFmpeg` (or `choco install ffmpeg`)
  - macOS: `brew install ffmpeg`

## Setup

```powershell
cd MultiMicStudio/backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Windows PowerShell
pip install -r requirements.txt
Copy-Item .env.example .env            # then edit secrets
```

Run the API:

```powershell
# Use `python -m uvicorn` — the bare `uvicorn` command is often not on PATH in PowerShell.
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open interactive docs at http://localhost:8000/docs
(Host phones can reach it at http://<YOUR-PC-LAN-IP>:8000/docs once on the same Wi-Fi.)

## Default configuration (zero external services)

- **Database:** SQLite file `multimic.db` (set `DATABASE_URL` to PostgreSQL for prod).
- **Storage:** local `./storage` dir, served at `/files/...`.
- **Transcription:** `stub` (returns a placeholder). To enable real transcription:
  ```powershell
  pip install faster-whisper
  # in .env:  TRANSCRIPTION_BACKEND=faster-whisper
  ```

## API overview

| Method | Path                          | Purpose                          |
| ------ | ----------------------------- | -------------------------------- |
| POST   | `/auth/signup`                | Create account, returns JWT      |
| POST   | `/auth/login`                 | Login, returns JWT               |
| GET    | `/auth/me`                    | Current user                     |
| POST   | `/sessions`                   | Create session (returns code)    |
| POST   | `/sessions/join`              | Join by code + speaker name      |
| POST   | `/sessions/{id}/start|stop`   | Host controls recording state    |
| DELETE | `/sessions/{id}`              | Owner deletes session+recordings |
| POST   | `/recordings`                 | Upload one phone's audio file    |
| POST   | `/projects/process/{id}`      | Align + mix + transcribe         |
| GET    | `/projects`                   | Dashboard project list           |
| GET    | `/projects/{session_id}`      | Project detail (audio+transcript)|

## How sync + mix works

`app/audio/processing.py` implements the layered strategy:
1. Detect a clap/beep transient near the start of each file.
2. If no marker, seed from client timestamps and refine via FFT cross-correlation.
3. Place each track on a shared timeline at its offset and sum to one mixed track.

Drift correction for long takes is stubbed (`detect_drift` returns 0) and is the first
planned post-MVP upgrade.

## Production path (no re-architecture)

- Swap `STORAGE_BACKEND=s3` and implement `S3Storage` in `app/storage.py`.
- Replace `dispatch_processing` body in `app/worker/tasks.py` with a Redis/RQ enqueue.
- Replace `init_db()` (create_all) with Alembic migrations.
