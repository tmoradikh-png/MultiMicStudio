# ✅ WORKING STATE — MultiMic Studio (snapshot 2026-06-17)

**This snapshot works.** All backend smoke tests pass and the audio quality
feedback (rounds 1–8) has been applied and verified. Use this as a known-good
restore point before continuing with the next step.

> Backup archive: `backups/MultiMicStudio_WORKING_2026-06-17.zip`
> (heavy, regenerable folders are intentionally excluded — see "Restoring" below).

---

## What is verified working

| Area | Status | Proof |
|------|--------|-------|
| Multi-phone sync (clap/beep + cross-correlation) | ✅ | smoke: ALIGNMENT 0.0 ms, NO-MARKER 0.0 ms |
| Single-phone passthrough + dedup of retry uploads | ✅ | smoke: SINGLE-PHONE + DEDUP |
| Multi-take isolation (per-take identity) | ✅ | smoke: MULTI-TAKE ISOLATION |
| Natural **stereo** mix + per-speaker stems | ✅ | smoke: STEREO + STEMS |
| Enhancement presets (natural/studio_voice/karaoke/party) | ✅ | smoke: ENHANCEMENT PRESETS |
| **Natural mix cleanup** (L/R balance, headroom, lower noise) | ✅ | balance -4.1→-0.5 dB, peak -1.5 dBFS |
| **Studio Voice noise fix** (denoise-first, no SNR loss) | ✅ | SNR 14→57 dB, floor -50→-74 dB |
| Objective audio test bench (CLI + GUI) | ✅ | `scripts/audio_bench*.py`, HTML report |

Run all checks yourself (from `backend/`):

```powershell
.\.venv\Scripts\python.exe smoke_test.py
```

Expected: every line ends in `PASSED`.

---

## The 3 parts

```
MultiMicStudio/
  backend/   FastAPI + SQLite/Postgres + FFmpeg  (recording/sync/mix/enhance engine)
  mobile/    Expo React Native recorder app (iOS + Android)
  web/       Next.js dashboard (player + transcript + preset selector)
  docs/      Roadmap MVP -> full product
```

Bring up the **backend first**; mobile and web both point at it.

---

## How to run each part

### 1. Backend API  (do this first)

```powershell
cd backend
# one-time: python -m venv .venv ; .\.venv\Scripts\pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- API docs:  http://127.0.0.1:8000/docs
- Uses SQLite (`multimic.db`) by default — no DB server needed.
- IMPORTANT: always use the venv Python: `.\.venv\Scripts\python.exe`.
  The bare `python` on this PC lacks fastapi/numpy.

### 2. Audio Test Bench — GUI  (objective quality report, pick files from PC)

```powershell
cd backend
.\.venv\Scripts\python.exe scripts\audio_bench_gui.py
# opens http://127.0.0.1:8200 automatically
```

- Attach files to the roles you want, click **Run analysis** → inline HTML report.
- Only the files you attach appear in the report.
- To always see the **Natural stereo mix** as the clean reference, attach
  `final_mix_stereo.wav` to the *Natural stereo mix* slot (the studio/karaoke/
  party presets are auto-generated from it).
- Keep that terminal open while you use the page (it is the server).

### 3. Audio Test Bench — CLI  (scriptable)

```powershell
cd backend
# against a processed session in the DB:
.\.venv\Scripts\python.exe scripts\audio_bench.py --session-id <ID> --out bench_out
# or against loose files:
.\.venv\Scripts\python.exe scripts\audio_bench.py `
  --raw phoneA.wav --raw phoneB.wav --natural final_mix_stereo.wav --out bench_out
```

Open `bench_out/report.html`.

### 4. Web dashboard

```powershell
cd web
npm install        # one-time
npm run dev        # http://localhost:3000
```

Note: the dashboard is served from `.next`; **restart `npm run dev`** to see UI changes.

### 5. Mobile recorder (Expo)

```powershell
cd mobile
npm install        # one-time
npx expo start
```

- Set the backend LAN IP in `mobile/config.ts` (physical phones can't reach `localhost`).
- Host starts the session; guest phones join with the 6-char code and auto-record.

---

## Where the audio quality lives (for the next step)

- **Natural stereo mix** (the clean reference): `backend/app/audio/processing.py`
  → `mix_stereo()`. Does per-input noise gating, equal-loudness leveling,
  cleaner-mic weighting, constant-power panning, gentle L/R balance, safe -1.5 dBFS
  headroom. **This is the file to touch to improve the core mix.**
- **Enhancement presets** (applied AFTER the natural mix): `backend/app/audio/effects.py`
  → `apply_enhancement(stereo, sr, mode)`. Denoise-first ordering keeps SNR high.
  Every preset returns the SAME length (no stacking/drift).
- **Quality metrics / report**: `backend/scripts/audio_bench.py` (metrics + HTML),
  `backend/scripts/audio_bench_gui.py` (file-picker GUI).

Golden rules proven over rounds 1–8 (don't regress these):
1. Never collapse to mono — keep real L/R movement.
2. Enhancement effects must never change the array length.
3. Denoise BEFORE EQ/compression; makeup gain on a noisy floor destroys SNR.
4. Judge reverb/duplicate checks RELATIVE to the natural mix, not absolute.
5. Don't use `processing.decode_to_wav` for stereo — it forces mono (`-ac 1`).

---

## Restoring this backup

The zip excludes folders that are **regenerated**, not source:
`.venv/`, `node_modules/`, `.next/`, `.expo/`, `__pycache__/`, `storage/`, `*.db`.

To restore and run:

```powershell
# 1. unzip somewhere, then:
cd MultiMicStudio\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe smoke_test.py        # should all PASS

cd ..\web   ; npm install
cd ..\mobile; npm install
```

Tip: OneDrive can corrupt `.venv/.next/node_modules` during sync — if something acts
strange, delete those folders and reinstall (they are not in the backup for this reason).
