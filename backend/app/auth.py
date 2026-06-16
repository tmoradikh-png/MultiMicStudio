"""Password hashing and JWT helpers."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import secrets

from app.config import get_settings

settings = get_settings()


def generate_guest_token() -> str:
    """Opaque, unguessable identity for a no-account guest device.

    Unlike the host's JWT, this is a random bearer token persisted on the
    participant row. The phone stores it and re-sends it on reconnect / upload
    retry so the same device always resolves to the SAME participant.
    """
    return secrets.token_urlsafe(32)

# bcrypt operates on bytes and ignores input beyond 72 bytes; truncate defensively.
def _to_bytes(value: str) -> bytes:
    return value.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(_to_bytes(plain), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_to_bytes(plain), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": user_id, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> str | None:
    """Return the user id (sub) or None if invalid/expired."""
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
