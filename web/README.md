# MultiMic Studio — Web Dashboard (Next.js)

Review processed projects: status, the final mixed audio player, and the transcript.

## Requirements

- Node.js 18+

## Setup

```powershell
cd MultiMicStudio/web
npm install
Copy-Item .env.local.example .env.local   # set NEXT_PUBLIC_API_URL if not localhost
npm run dev
```

Open http://localhost:3000

## Pages

- `/` — login (use an account created in the mobile app).
- `/projects` — list of your sessions with live processing status.
- `/projects/[sessionId]` — project detail: trigger mixing, play and download the
  final mixed audio, view and download the transcript. Auto-refreshes while processing.

## Notes

- The backend serves local audio at `/files/...`; the dashboard rewrites those to
  absolute URLs automatically (`lib/api.ts`). When the backend moves to S3, the URLs
  become absolute and no dashboard change is needed.
- Transcript editing, export presets, short-clip generation and collaboration are
  full-product features that extend these pages later (see `../docs/ROADMAP.md`).
