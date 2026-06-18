"""MultiMic Studio backend entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.routers import auth, projects, recordings, sessions
from app.storage import verify_signature

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Ensure local storage dir exists so the /files mount has a target.
    Path(settings.storage_local_dir).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="MultiMic Studio API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(sessions.router)
app.include_router(recordings.router)
app.include_router(projects.router)

# Live Speaker mode (real-time relay) is mounted only when explicitly enabled, so
# the default offline product is byte-for-byte unaffected. See app/live/ and
# docs/ARCHITECTURE_HOSTING_AND_LIVE_MODE.md.
if settings.live_mode_enabled:
    from app.routers import live as live_router

    app.include_router(live_router.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "storage": settings.storage_backend,
        "live_mode": "on" if settings.live_mode_enabled else "off",
    }


# --- Local file serving -----------------------------------------------------
# Two modes, both for STORAGE_BACKEND=local. The default (signing OFF) is the
# MVP's open static mount — unchanged. With FILE_URL_SIGNING=true the static
# mount is replaced by a signature-checked route so hosted audio cannot be
# enumerated or fetched without a valid, time-limited link. (S3 backend serves
# files directly from object storage / presigned URLs and uses neither.)
if settings.storage_backend == "local":
    _local_root = Path(settings.storage_local_dir).resolve()
    _local_root.mkdir(parents=True, exist_ok=True)

    if settings.file_url_signing:

        @app.get("/files/{key:path}", tags=["files"])
        def serve_signed_file(key: str, request: Request) -> FileResponse:
            if not verify_signature(
                key, request.query_params.get("exp"), request.query_params.get("sig")
            ):
                raise HTTPException(status_code=403, detail="Invalid or expired link")
            full = (_local_root / key).resolve()
            # Block path traversal outside the storage root.
            if not str(full).startswith(str(_local_root)) or not full.is_file():
                raise HTTPException(status_code=404, detail="File not found")
            return FileResponse(str(full))

    else:
        app.mount(
            "/files",
            StaticFiles(directory=settings.storage_local_dir),
            name="files",
        )
