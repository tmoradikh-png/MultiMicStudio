# MultiMic Studio — Private Beta Readiness

**Date:** 2026-06-17 · **Checkpoint:** `v0.6.0-beta-safety-limits`
**Automated verdict:** ✅ **GO** (9/9 automated checks) for a **host-run / LAN beta**.
**External-tester verdict:** ⚠️ **NO-GO until 3 deployment blockers are closed** (see below).

This is a go/no-go report, not a feature build. The automated portion is produced
by [backend/scripts/beta_readiness.py](../backend/scripts/beta_readiness.py); the
manual portion must be signed off on real devices before inviting outside users.

---

## 1. Pass/fail checklist

### Automated (run: `beta_readiness.py`) — all PASS
| # | Check | Result |
|---|---|---|
| 1 | Host creates session | ✅ |
| 1 | Multiple guests join with **no account** | ✅ |
| 1 | Recording start/stop (take lifecycle) | ✅ |
| 1 | Uploads complete | ✅ |
| 1 | Processing completes | ✅ |
| 1 | All 7 outputs appear (raw×2, natural, studio, karaoke, party, mono) | ✅ |
| 1 | Download works (web outputs reachable) | ✅ |
| 2 | 10 sessions back-to-back all reach `done` | ✅ |
| 2 | No duplicate recordings (retry deduped) | ✅ |
| 2 | No old-take bleed (latest take only) | ✅ |
| 2 | No manual DB/storage cleanup needed (isolated, self-cleaning run) | ✅ |
| 3 | Natural stereo is **true 2-channel** | ✅ |
| 3 | Studio Voice cleaner than natural | ✅ |
| 3 | No clipping / no stacking / low drift | ✅ |
| 3 | Sync acceptable | ✅ |
| 3 | **Baseline guard holds (11/11 at/above baseline)** | ✅ |

Also green from the full backend smoke suite (`smoke_test.py`): single-phone dedup,
alignment accuracy, no-marker alignment, multi-take isolation, stereo+stems,
enhancement presets, guest no-account flow, **limits + cleanup** (#8).

### Manual (must be done on real hardware before external testers) — NOT yet run
| # | Check | Status |
|---|---|---|
| 4 | App restart during a session resumes (ResumeBanner) | ⬜ code-verified, needs device |
| 4 | Upload retry on flaky network | ⬜ code-verified, needs device |
| 4 | Backend unreachable → friendly message | ⬜ code-verified, needs device |
| 4 | Invalid / expired session → clear message | ⬜ code-verified, needs device |
| 4 | Too-large file → friendly 413 message | ⬜ code-verified, needs device |
| 4 | Unsupported file type → friendly 415 message | ⬜ code-verified, needs device |
| 5 | Two **different physical phones** in one session | ⬜ |
| 5 | Two guests in the same session | ⬜ |
| 5 | Longer 2–3 minute recording | ⬜ |
| 5 | Noisy-room test | ⬜ |
| 3 | Bench report on a **real voice** recording | ⬜ (synthetic fixture passes baseline; confirm with real audio) |

> The mobile error/recovery paths (#4) are implemented and unit-consistent
> (`describeError` maps 0/401/403/404/409/413/415/422; ResumeBanner restores an
> active session). They are marked manual only because they need a real device +
> real network to exercise end-to-end.

---

## 2. Remaining blockers (for EXTERNAL testers)

1. **Mobile API base is hardcoded to a LAN IP.**
   [mobile/src/config.ts](../mobile/src/config.ts) = `http://192.168.3.19:8000`.
   Outside testers can't reach that. → make it configurable (env / build-time) and
   point at a hosted HTTPS backend.
2. **Backend is not deployed.** It runs locally via uvicorn. → host it (the #7
   hosting seam is ready: set `DATABASE_URL`=Postgres, `STORAGE_BACKEND=s3`,
   `FILE_URL_SIGNING=true`, `PUBLIC_BASE_URL`=https domain, change `JWT_SECRET`).
3. **No HTTPS / TLS termination yet.** Mobile + web should talk to the backend
   over `https://`. → terminate TLS at the host/reverse proxy.

Closing these three moves the external-tester verdict to GO. None require audio
changes.

---

## 3. Known limitations (acceptable for private beta)

- **Live Mode is disabled** (`LIVE_MODE_ENABLED=false`) — design only, see
  [ARCHITECTURE_HOSTING_AND_LIVE_MODE.md](ARCHITECTURE_HOSTING_AND_LIVE_MODE.md).
- **Transcription is a stub** (`TRANSCRIPTION_BACKEND=stub`); real transcripts need
  `faster-whisper`.
- **Processing runs in-process** (FastAPI BackgroundTasks), not a queue. Fine for a
  small beta; no concurrency cap. The `dispatch_processing()` seam is ready for
  Redis/RQ later.
- **Defaults are SQLite + local disk.** Durable beta should use Postgres + S3.
- **Signup is open** and there is **no rate limiting / abuse protection** yet.
- **Limits are private-beta sized:** 25 MB upload, 5-min recording, 4-hour session
  expiry, 24-hour temp cleanup (all configurable).
- The automated quality check uses a **synthetic speech-like fixture**; it proves
  the pipeline + baseline guard work, but a real-voice bench pass should be
  confirmed once on a real recording.

---

## 4. Exact run commands (Windows / PowerShell)

### Backend (API)
```powershell
cd MultiMicStudio\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env        # then edit secrets/hosting values
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Web dashboard
```powershell
cd MultiMicStudio\web
npm install
Copy-Item .env.local.example .env.local   # set NEXT_PUBLIC_API_URL if not localhost
npm run dev                                # http://localhost:3000
```

### Mobile (Expo)
```powershell
cd MultiMicStudio\mobile
npm install
# set the API base first: edit src/config.ts -> API_BASE_URL (host's IP/URL)
npx expo start
```

### Verification / ops
```powershell
cd MultiMicStudio\backend
.\.venv\Scripts\python.exe smoke_test.py            # full backend smoke suite
.\.venv\Scripts\python.exe scripts\beta_readiness.py # go/no-go runner (this report)
.\.venv\Scripts\python.exe scripts\cleanup.py        # run the cleanup policy (cron-friendly)
```

---

## 5. Environment variables (backend `.env`)

See [backend/.env.example](../backend/.env.example) for the full template.

| Group | Keys |
|---|---|
| Core | `DATABASE_URL`, `JWT_SECRET` (**change!**), `JWT_EXPIRE_MINUTES`, `CORS_ORIGINS` |
| Hosting | `PUBLIC_BASE_URL`, `STORAGE_BACKEND`, `STORAGE_LOCAL_DIR` |
| Secure files | `FILE_URL_SIGNING`, `FILE_URL_TTL_SECONDS`, `FILE_SIGNING_SECRET` |
| Object storage | `S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_PUBLIC_BASE_URL` |
| Limits (#8) | `MAX_UPLOAD_BYTES`, `MAX_RECORDING_SECONDS`, `ALLOWED_AUDIO_EXTENSIONS`, `SESSION_EXPIRY_MINUTES`, `CLEANUP_TEMP_MAX_AGE_HOURS` |
| Other | `TRANSCRIPTION_BACKEND`, `WHISPER_MODEL`, `LIVE_MODE_ENABLED` (keep `false`) |

Web: `NEXT_PUBLIC_API_URL`. Mobile: `API_BASE_URL` in `src/config.ts`.

---

## 6. Current tags / checkpoints

| Tag | What it freezes |
|---|---|
| `v0.1.0-audio-baseline` | Frozen audio quality + bench baseline |
| `v0.2.0-guest-join` | No-account guest join flow |
| `v0.3.0-web-outputs` | Web outputs dashboard + quality badge |
| `v0.4.0-mobile-flow` | Mobile flow polish + upload retry + recovery |
| `v0.5.0-hosting-prep` | Signed URLs + S3 seam + Live Mode design |
| `v0.6.0-beta-limits` / `v0.6.0-beta-safety-limits` | Private-beta safety limits (#8) |

Recovery: `git checkout v0.1.0-audio-baseline` restores frozen audio;
`git checkout v0.6.0-beta-safety-limits` restores the full pre-#9 product.

---

## 7. Recommended next step

A small **"go-live prep"** task (no new product features):

1. Make the mobile `API_BASE_URL` configurable and point web + mobile at a hosted
   backend.
2. Deploy the backend with `DATABASE_URL`=Postgres, `STORAGE_BACKEND=s3`,
   `FILE_URL_SIGNING=true`, `PUBLIC_BASE_URL`=https domain, fresh `JWT_SECRET`,
   behind HTTPS.
3. Run one **real two-phone session** end-to-end and a **real-voice bench** to sign
   off checklist sections 4 and 5.

After that, the product is GO for an invited private beta. Keep Live Mode disabled
until a deliberate L0 spike is scheduled.
