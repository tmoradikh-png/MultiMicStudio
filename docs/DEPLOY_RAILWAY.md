# Deploy the MultiMic Studio backend to Railway (private beta)

Goal: a working, externally-testable HTTPS backend — Postgres + S3-compatible
storage — using the Dockerfile and config we already have. Not optimized for
cost/scale yet. No product or audio changes.

> The container build, env vars, Postgres add-on, and storage all live in **your**
> Railway account. This guide is copy-paste; run the steps with your login.

Related: [DEPLOYMENT.md](DEPLOYMENT.md) (provider-neutral runbook),
[../backend/Dockerfile](../backend/Dockerfile),
[../backend/railway.json](../backend/railway.json),
[../backend/.env.production.example](../backend/.env.production.example).

---

## 0. Prerequisites

- A Railway account (https://railway.com) and the CLI (optional but handy):
  ```powershell
  npm i -g @railway/cli
  railway login
  ```
- Git repo pushed somewhere Railway can read (GitHub) — OR use `railway up` to
  deploy the local folder directly.
- An S3-compatible bucket. **Railway has no built-in object storage**, so use
  **Cloudflare R2** (recommended: cheap, S3-compatible, no egress fees). DO Spaces
  or AWS S3 also work — see section 3.

---

## 1. Create the backend service (from the Dockerfile)

The repo root is `MultiMicStudio/`; the backend lives in `backend/`. Point Railway
at the `backend/` directory so it uses [backend/Dockerfile](../backend/Dockerfile)
and [backend/railway.json](../backend/railway.json).

**Dashboard route**
1. New Project → Deploy from GitHub repo → pick the repo.
2. Service → Settings → **Root Directory** = `backend`.
3. Build is auto-detected as Dockerfile (railway.json pins it). Health check path
   is already `/health` (from railway.json).

**CLI route (deploy local folder)**
```powershell
cd C:\Users\Greencom\OneDrive\Documents\aiChat\MultiMicStudio\backend
railway init           # creates/links a project
railway up             # builds the Dockerfile and deploys
```

Railway injects `$PORT`; the Dockerfile/`startCommand` already bind to it.

---

## 2. Add managed Postgres → `DATABASE_URL`

1. In the project: **New → Database → PostgreSQL**.
2. Railway exposes `DATABASE_URL` on the Postgres service. The backend needs the
   **psycopg2** form. In the backend service Variables, add a **reference variable**:
   ```
   DATABASE_URL = ${{Postgres.DATABASE_URL}}
   ```
   Railway's URL looks like `postgresql://user:pass@host:port/db`. SQLAlchemy +
   psycopg2 accepts `postgresql://`, but to be explicit you can set:
   ```
   DATABASE_URL = postgresql+psycopg2://USER:PASS@HOST:PORT/DB
   ```
   (copy the values from the Postgres service's "Connect" tab).
3. No migration step: the app runs `init_db()` on startup and creates tables.

---

## 3. Provision S3-compatible storage (Cloudflare R2 recommended)

1. Cloudflare dashboard → **R2** → Create bucket, e.g. `multimic-recordings`.
2. **Manage R2 API Tokens** → create a token with **Object Read & Write** scoped to
   that bucket. Note the **Access Key ID** + **Secret Access Key**.
3. Find your **Account ID** (R2 overview). The S3 endpoint is:
   ```
   https://<ACCOUNT_ID>.r2.cloudflarestorage.com
   ```
4. Map into the backend's `S3_*` vars (section 4). For R2 use region `auto`.

> Alternatives: **AWS S3** — leave `S3_ENDPOINT_URL` blank, set `S3_REGION`.
> **DO Spaces** — endpoint `https://<region>.digitaloceanspaces.com`.

---

## 4. Set production environment variables

In the backend service → **Variables**, add the following. Secrets below were freshly
generated for you — paste them as-is (do **not** reuse the dev defaults).

```
# Core
DATABASE_URL=${{Postgres.DATABASE_URL}}
JWT_SECRET=vZ-N4l8aqleygut4bmDchR3RpNHpdBOlJaC0CpYjd_UcNFttxw4GF7OmxZzGShfF
JWT_EXPIRE_MINUTES=10080

# Public addressing — set AFTER Railway gives you the domain (section 5), then redeploy
PUBLIC_BASE_URL=https://<your-service>.up.railway.app
CORS_ORIGINS=https://<your-web-origin>

# Secure, expiring file URLs
FILE_URL_SIGNING=true
FILE_URL_TTL_SECONDS=3600
FILE_SIGNING_SECRET=CTVKp1qKJebRMQrOehVjDMhg90FEcSaqc-aSI0MaNCxq3vswSbDioUfUNteyaV7b

# Storage (Cloudflare R2 / S3-compatible)
STORAGE_BACKEND=s3
S3_BUCKET=multimic-recordings
S3_REGION=auto
S3_ENDPOINT_URL=https://<ACCOUNT_ID>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=<r2-access-key-id>
S3_SECRET_ACCESS_KEY=<r2-secret-access-key>
S3_PUBLIC_BASE_URL=

# Private-beta safety limits
MAX_UPLOAD_BYTES=26214400
MAX_RECORDING_SECONDS=300
SESSION_EXPIRY_MINUTES=240
CLEANUP_TEMP_MAX_AGE_HOURS=24

# Keep Live Mode off
LIVE_MODE_ENABLED=false
```

> These two secrets are generated locally; rotate any time with
> `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Do not commit them.

---

## 5. Public HTTPS URL

1. Backend service → **Settings → Networking → Generate Domain** (Railway issues a
   `https://<service>.up.railway.app` with TLS — no reverse proxy needed).
2. Set `PUBLIC_BASE_URL` to that exact `https://…` URL (section 4) and **redeploy**
   so signed file links use the correct host.

---

## 6. Verify the health endpoint

```powershell
curl https://<your-service>.up.railway.app/health
# {"status":"ok","storage":"s3","live_mode":"off"}
```
`storage:"s3"` confirms R2 wiring; `live_mode:"off"` confirms Live Mode stays disabled.

---

## 7. Run smoke / readiness against the deployed backend

`smoke_test.py` and `beta_readiness.py` run **in-process** (TestClient) — they can't
hit a remote host. Use the remote end-to-end check that talks to the live URL over HTTP:

```powershell
cd C:\Users\Greencom\OneDrive\Documents\aiChat\MultiMicStudio\backend
.\.venv\Scripts\python.exe scripts\remote_smoke.py https://<your-service>.up.railway.app
```
It exercises the real hosted stack: `/health` → signup → create session → guest join
(no account) → start → upload a short WAV → stop → process → poll `/projects/{id}/outputs`
until `done` and asserts the 7 outputs are present and downloadable (signed URLs).

Keep `smoke_test.py` + `beta_readiness.py` as the local regression gate before each deploy.

---

## 8. Point the mobile app at the hosted backend

No source edit (see [../mobile/.env.example](../mobile/.env.example)):
```powershell
# mobile/.env  (gitignored)
EXPO_PUBLIC_API_URL=https://<your-service>.up.railway.app
```
or per session:
```powershell
$env:EXPO_PUBLIC_API_URL="https://<your-service>.up.railway.app"; npx expo start
```
The Home screen footer shows the active server. Unset → local LAN fallback still works.

---

## 9. One real two-phone hosted test

1. Both phones run the app with `EXPO_PUBLIC_API_URL` set to the Railway URL.
2. Phone A: sign up / log in, create a session (host). Phone B: **Join as guest**
   with the code (no account).
3. Host starts → both record a short clip with one shared clap → host stops.
4. Open the web dashboard (or `remote_smoke` output) and confirm processing reaches
   `done` with all 7 outputs and `Quality: PASS`.

---

## Post-deploy checklist (from the security checklist)

- [ ] `JWT_SECRET` and `FILE_SIGNING_SECRET` are the fresh values above, not dev defaults.
- [ ] `FILE_URL_SIGNING=true` → recordings served via signed, expiring URLs.
- [ ] `PUBLIC_BASE_URL` is the real `https://` Railway domain; `CORS_ORIGINS` set.
- [ ] `STORAGE_BACKEND=s3` and `/health` reports `storage:"s3"`.
- [ ] Upload limits set; `LIVE_MODE_ENABLED=false`.
- [ ] Schedule cleanup: add a Railway **Cron** service/job running
      `python scripts/cleanup.py` (daily), or run it manually for the beta.
- [ ] No secrets in build logs; real `.env` never committed.
