# MultiMic Studio — backend production image
#
# Provider-neutral: runs anywhere that can run a container (Render, Fly, Railway,
# a VPS with Docker, etc.). All configuration comes from environment variables —
# no secrets are baked in. See backend/.env.production.example for the full list.
#
#   docker build -t multimic-backend ./backend
#   docker run --env-file backend/.env.production -p 8000:8000 multimic-backend

FROM python:3.12-slim AS base

# - ffmpeg: the audio engine prefers a system FFmpeg (more robust than the
#   pip-bundled fallback). Installing it here makes decoding reliable in prod.
# - libsndfile1: required by soundfile for WAV read/write.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app

# Install dependencies first (better layer caching). requirements-prod.txt pulls in
# the base requirements plus the hosted extras (boto3 for S3-compatible storage).
COPY requirements.txt requirements-prod.txt ./
RUN pip install --upgrade pip && pip install -r requirements-prod.txt

# App source. (.dockerignore keeps local dev artifacts — venv, db, storage — out.)
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Container-level health check hitting the app's /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:${PORT}/health || exit 1

# Honour $PORT (many hosts inject it). Single worker keeps the in-process
# BackgroundTasks model intact for the private beta; scale out later with a queue.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
