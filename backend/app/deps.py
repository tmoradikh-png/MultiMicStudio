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


def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User | None:
    """Resolve a logged-in user if a valid account token is present, else None.

    Used by endpoints that work for BOTH accounts and no-account guests (e.g.
    joining a session). Never raises for the anonymous case.
    """
    if credentials is None:
        return None
    user_id = decode_token(credentials.credentials)
    if user_id is None:
        return None
    return db.get(User, user_id)


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
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
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

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token"
    )

