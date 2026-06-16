# MultiMic Studio AI

Turn several phones into synchronized wireless microphones, then use AI to clean,
transcribe, summarize, and repurpose the recording into ready-to-publish content.

This repository contains the **MVP** (buildable today) inside an architecture that is
**aligned with the full product** so later features bolt on without rewrites.

## Repository layout

```
MultiMicStudio/
  backend/   FastAPI + PostgreSQL + FFmpeg + Whisper  (recording/sync engine)
  mobile/    React Native (Expo) app for iOS + Android (recorder)
  web/        Next.js dashboard (project list, player, transcript)
  docs/       Product alignment: MVP -> full product roadmap & data model
```

## What the MVP does (milestone definition)

1. User creates an account and a recording session.
2. Other phones join the session with a 6-char code (or QR of that code).
3. Each phone records **local** high-quality audio (one phone per speaker).
4. Each phone uploads its file to the backend after recording.
5. Backend aligns the files (clap/beep peak + waveform cross-correlation).
6. Backend mixes one synchronized audio file and generates a transcript.
7. Web dashboard plays + downloads the mixed audio and shows the transcript.

The first milestone is successful when **3 phones can record a 10-minute session,
upload, auto-align, and produce one mixed file + transcript** without major drift.

## What is intentionally NOT in the MVP

Payments, advanced AI editing, video, live streaming, real-time cloud recording,
team collaboration, branding. See `docs/ROADMAP.md` for how each maps onto the
existing data model and service boundaries.

## Quick start

- Backend: see [backend/README.md](backend/README.md)
- Mobile: see [mobile/README.md](mobile/README.md)
- Web: see [web/README.md](web/README.md)

Bring up the backend first; mobile and web both point at it.
