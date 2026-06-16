"""MultiMic Studio backend entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.routers import auth, projects, recordings, sessions

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


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


# Serve locally stored audio (MVP). In production this is replaced by object storage URLs.
if settings.storage_backend == "local":
    Path(settings.storage_local_dir).mkdir(parents=True, exist_ok=True)
    app.mount(
        "/files",
        StaticFiles(directory=settings.storage_local_dir),
        name="files",
    )
