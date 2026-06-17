# MultiMic Studio — Deployment Runbook

Provider-neutral guide to running the backend in production (Postgres +
S3-compatible storage, behind HTTPS) and pointing the mobile app at it. No host
is assumed — the same container runs on Render, Fly, Railway, a Docker VPS, etc.

**Scope:** infrastructure only. No product/audio changes; Live Mode stays OFF;
the local LAN fallback for the mobile app keeps working.

Artifacts:
- [backend/Dockerfile](../backend/Dockerfile) — production image
- [backend/requirements-prod.txt](../backend/requirements-prod.txt) — base + boto3
- [docker-compose.prod.yml](../docker-compose.prod.yml) — local prod simulation
- [backend/.env.production.example](../backend/.env.production.example) — config template

---

## 1. Local production test (before choosing a host)

Simulates the hosted topology (backend container + Postgres + MinIO) on your machine.

```powershell
cd C:\Users\Greencom\OneDrive\Documents\aiChat\MultiMicStudio
docker compose -f docker-compose.prod.yml up --build
```

Then verify:
```powershell
# health (expects {"status":"ok","storage":"s3","live_mode":"off"})
curl http://localhost:8000/health
```
- MinIO console: http://localhost:9001 (user/pass `minioadmin` / `minioadmin`).
- Tear down: `docker compose -f docker-compose.prod.yml down` (add `-v` to wipe data).

This proves the container, the Postgres path, secure file signing, and the
S3-compatible storage path all work before you pick a real provider.

---

## 2. Deploy the backend (any host)

1. Build & push the image, or point the host at `backend/Dockerfile`:
   ```powershell
   docker build -t multimic-backend ./backend
   ```
2. Provide configuration as environment variables from
   [backend/.env.production.example](../backend/.env.production.example) (use the
   host's secret store; do **not** commit a real `.env`).
3. The app listens on `$PORT` (default 8000) and runs DB table creation on
   startup — no manual migration step for the private beta.
4. Run a single instance for the beta (in-process worker). Scale out later with a
   queue (the `dispatch_processing()` seam is ready).

---

## 3. Connect PostgreSQL

- Provision a Postgres 16 database (managed service or the compose `db`).
- Set:
  ```
  DATABASE_URL=postgresql+psycopg2://USER:PASSWORD@HOST:5432/DBNAME
  ```
- The driver (`psycopg2-binary`) is already in `requirements.txt`. Tables are
  created automatically on first boot.

---

## 4. Connect S3 / R2 / Spaces / MinIO

Set `STORAGE_BACKEND=s3` and the `S3_*` variables. `boto3` (in
`requirements-prod.txt`) is imported lazily only when this is enabled.

| Provider | `S3_ENDPOINT_URL` | Notes |
|---|---|---|
| AWS S3 | *(leave blank)* | set `S3_REGION` |
| Cloudflare R2 | `https://<account>.r2.cloudflarestorage.com` | region `auto` |
| DO Spaces | `https://<region>.digitaloceanspaces.com` | |
| MinIO (self-host) | `http://<host>:9000` | path-style |

- Create the bucket (`S3_BUCKET`) ahead of time and grant the access key
  read/write.
- Keep `FILE_URL_SIGNING=true` so files are served via signed, expiring links.
- Optionally set `S3_PUBLIC_BASE_URL` to a CDN; blank uses presigned GET URLs.

---

## 5. Enable HTTPS (required for external testers)

The app speaks plain HTTP inside the container; terminate TLS in front of it:
- **PaaS (Render/Fly/Railway):** HTTPS is provided automatically for the service
  domain — just use the `https://…` URL.
- **VPS:** put Caddy, nginx, or Traefik in front as a reverse proxy with
  Let's Encrypt, proxying `443 -> backend:8000`.

Set `PUBLIC_BASE_URL=https://api.your-domain.com` and put your web origin in
`CORS_ORIGINS`.

---

## 6. Point the mobile app at the hosted backend

No source edit needed (see [mobile/.env.example](../mobile/.env.example)):

```powershell
# mobile/.env  (gitignored)
EXPO_PUBLIC_API_URL=https://api.your-domain.com
```
or per-session:
```powershell
$env:EXPO_PUBLIC_API_URL="https://api.your-domain.com"; npx expo start
```
The Home screen footer shows the active server; unset reverts to the local LAN
fallback (`http://192.168.3.19:8000`).

---

## 7. Verify the health endpoint

```powershell
curl https://api.your-domain.com/health
# {"status":"ok","storage":"s3","live_mode":"off"}
```

---

## 8. Run the test suites after deployment

The suites run **locally against an isolated temp DB/storage** — they validate the
audio pipeline and quality gate; run them after a deploy as a regression gate.

```powershell
cd MultiMicStudio\backend
.\.venv\Scripts\python.exe smoke_test.py          # all sections PASSED
.\.venv\Scripts\python.exe scripts\beta_readiness.py  # VERDICT: GO (9/9)
```

For an end-to-end check against the **hosted** instance, do one real two-phone
session (host + guest) and confirm all 7 outputs render with `Quality: PASS`.

---

## 9. Cleanup job (scheduled)

The cleanup policy closes stale open sessions and removes failed uploads /
abandoned empty sessions (never completed outputs). Run it on a schedule
(e.g. cron / a platform scheduled job, hourly or daily):

```powershell
cd MultiMicStudio\backend
.\.venv\Scripts\python.exe scripts\cleanup.py
```
In a container: `python scripts/cleanup.py`. Tunable via `SESSION_EXPIRY_MINUTES`
and `CLEANUP_TEMP_MAX_AGE_HOURS`.

---

## Security checklist (must pass before inviting external testers)

- [ ] **Fresh `JWT_SECRET`** — a long random value, not the dev default
      (`python -c "import secrets; print(secrets.token_urlsafe(48))"`).
- [ ] **Signed file URLs enabled** — `FILE_URL_SIGNING=true` with a dedicated
      `FILE_SIGNING_SECRET`, so recordings are not world-enumerable.
- [ ] **No secrets in logs** — limits/cleanup logs record only session/participant
      IDs and reasons; guest tokens, JWTs and signing secrets are never logged.
      (Verify your platform isn't echoing env vars in build logs.)
- [ ] **HTTPS enforced** — `PUBLIC_BASE_URL` is `https://`, TLS terminated in
      front, web origin restricted via `CORS_ORIGINS`.
- [ ] **Upload limits on** — `MAX_UPLOAD_BYTES`, `MAX_RECORDING_SECONDS`, and
      `ALLOWED_AUDIO_EXTENSIONS` set (413/415 reject oversize/over-long/bad-type).
- [ ] **Cleanup scheduled** — `scripts/cleanup.py` runs on a timer (section 9).
- [ ] **Live Mode OFF** — `LIVE_MODE_ENABLED=false`.
- [ ] **Real `.env` never committed** — only `*.example` files are in git;
      production secrets live in the host's secret store.
- [ ] **Storage credentials are least-privilege** — the S3 key can read/write only
      the recordings bucket.
