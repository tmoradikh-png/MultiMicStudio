"""Application configuration loaded from environment / .env."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./multimic.db"

    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 10080  # 7 days

    storage_backend: str = "local"          # "local" | "s3"
    storage_local_dir: str = "./storage"

    # --- Hosting / public addressing ---------------------------------------
    # Absolute base URL the API is reachable at (e.g. "https://api.multimic.app").
    # When set, stored file URLs are returned as absolute links so a real domain /
    # CDN works. Empty keeps the relative "/files/..." behaviour the MVP uses.
    public_base_url: str = ""

    # --- Secure file URLs (opt-in; default OFF so the MVP is unchanged) -----
    # When enabled, files are served via a signature-checked route instead of an
    # open static mount, so audio is not world-enumerable on a hosted backend.
    file_url_signing: bool = False
    file_url_ttl_seconds: int = 3600        # signed-link lifetime
    # Secret for HMAC link signatures; falls back to jwt_secret if left blank.
    file_signing_secret: str = ""

    # --- S3 / object storage (used only when storage_backend == "s3") ------
    s3_bucket: str = ""
    s3_region: str = ""
    s3_endpoint_url: str = ""               # set for S3-compatible (R2/MinIO/Spaces)
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Public base for objects (CDN/bucket URL). Empty => use presigned GET URLs.
    s3_public_base_url: str = ""

    transcription_backend: str = "stub"     # "stub" | "faster-whisper"
    whisper_model: str = "base"

    # --- Private-beta safety limits ----------------------------------------
    # Per-upload size cap. A larger file is rejected with HTTP 413 before it ever
    # touches storage or processing. 0 disables the check.
    max_upload_bytes: int = 26_214_400              # 25 MB
    # Per-recording duration cap (client-reported). A longer take is rejected
    # with HTTP 413 so over-long audio never enters processing. 0 disables.
    max_recording_seconds: float = 300.0            # 5 minutes (private-beta)
    # Accepted upload file extensions (comma-separated). Anything else -> 415.
    allowed_audio_extensions: str = (
        "wav,m4a,mp3,aac,caf,ogg,oga,opus,webm,flac,mp4,3gp,amr"
    )
    # Open/inactive sessions older than this are treated as expired for JOINING
    # and are closed by the cleanup job. Completed projects stay accessible.
    # 0 disables expiry. (private-beta default: 4 hours)
    session_expiry_minutes: int = 240
    # Cleanup job: failed uploads / abandoned empty sessions older than this are
    # removed. Successful project outputs are NEVER deleted by cleanup.
    cleanup_temp_max_age_hours: int = 24

    # --- Live Mode (FUTURE milestone; default OFF) -------------------------
    # Feature flag seam only: when False the app is the offline record→upload→
    # process→export product exactly as today. Real-time transport/worker are a
    # separate milestone (see docs/ARCHITECTURE_HOSTING_AND_LIVE_MODE.md).
    live_mode_enabled: bool = False

    # ICE servers offered to clients for a FUTURE low-latency WebRTC path. The
    # Phase L0 relay does not use these (it streams through the server), but the
    # UI fetches them so the WebRTC upgrade needs no client change. Add a TURN
    # entry here to make cross-network (4G) WebRTC work later, e.g.:
    #   "stun:stun.l.google.com:19302,turn:user:pass@turn.example.com:3478"
    live_ice_servers: str = "stun:stun.l.google.com:19302"

    cors_origins: str = "http://localhost:3000,http://localhost:19006"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def live_ice_server_list(self) -> list[dict]:
        """ICE servers in the WebRTC RTCConfiguration JSON shape."""
        servers: list[dict] = []
        for entry in self.live_ice_servers.split(","):
            url = entry.strip()
            if not url:
                continue
            if url.startswith(("turn:", "turns:")) and "@" in url:
                # turn:user:pass@host:port -> {urls, username, credential}
                scheme, rest = url.split(":", 1)
                creds, host = rest.rsplit("@", 1)
                user, _, password = creds.partition(":")
                servers.append(
                    {"urls": f"{scheme}:{host}", "username": user, "credential": password}
                )
            else:
                servers.append({"urls": url})
        return servers

    @property
    def signing_key(self) -> str:
        """Secret used for HMAC file-URL signatures (dedicated, else JWT secret)."""
        return self.file_signing_secret or self.jwt_secret

    @property
    def allowed_audio_extension_set(self) -> set[str]:
        """Normalised set of accepted upload extensions (lowercase, no dots)."""
        return {
            e.strip().lower().lstrip(".")
            for e in self.allowed_audio_extensions.split(",")
            if e.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
