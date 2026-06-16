"""Shared FastAPI dependencies."""
from dataclasses import dataclass

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.auth import decode_token
from app.database import get_db
from app.models import SessionParticipant, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    user_id = decode_token(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    return user


@dataclass
class Principal:
    """The authenticated caller: either an account `user` or a guest `participant`."""

    user: User | None
    participant: SessionParticipant | None

    @property
    def is_guest(self) -> bool:
        return self.user is None and self.participant is not None


def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Principal:
    """Accept EITHER an account JWT or an opaque guest token.

    Tries the account JWT first; if that is not a valid user token, falls back to
    looking the bearer value up as a guest token on a participant row. This lets a
    no-account phone authenticate using only the token it received when it joined.
    """
    principal = _resolve_optional_principal(credentials, db)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
        )
    return principal


def get_optional_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Principal | None:
    """Resolve an account user OR a guest participant, or None when unauthenticated.

    Used by /sessions/join so the SAME endpoint serves brand-new guests (no token),
    reconnecting guests (existing guest token), and logged-in users. Never raises.
    """
    return _resolve_optional_principal(credentials, db)


def _resolve_optional_principal(
    credentials: HTTPAuthorizationCredentials | None,
    db: Session,
) -> Principal | None:
    if credentials is None:
        return None
    token = credentials.credentials

    user_id = decode_token(token)
    if user_id is not None:
        user = db.get(User, user_id)
        if user is not None:
            return Principal(user=user, participant=None)

    participant = (
        db.query(SessionParticipant)
        .filter(SessionParticipant.guest_token == token)
        .first()
    )
    if participant is not None:
        return Principal(user=None, participant=participant)

    return None


