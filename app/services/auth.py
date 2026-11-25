from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User, UserRole

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


class AdminAuthRequired(Exception):
    """Raised when an unauthenticated user attempts to access /admin routes."""

    def __init__(self, next_url: str = "/admin") -> None:
        self.next_url = next_url


def hash_password(plain_password: str) -> str:
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def normalize_identifier(identifier: str) -> str:
    cleaned = identifier.strip().replace(" ", "").replace("-", "")
    if cleaned.startswith("09") or cleaned.startswith("07"):
        return "+251" + cleaned[1:]
    return cleaned.lower()


def authenticate_user(db: Session, identifier: str, password: str) -> User | None:
    """Authenticates a user by email or Ethiopian mobile number (+2519/09/+2517/07)."""
    normalized = normalize_identifier(identifier)
    user = db.scalar(
        select(User).where(
            or_(
                User.email == normalized,
                User.phone_number == normalized,
            )
        )
    )
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def _create_token(user: User, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(user.id),
        "email": user.email,
        "role": user.role.value,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user: User) -> str:
    return _create_token(
        user,
        token_type="access",
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user: User) -> str:
    return _create_token(
        user,
        token_type="refresh",
        expires_delta=timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_jwt_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token.",
        ) from exc

    if payload.get("type") != expected_type:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token type. Expected '{expected_type}'.",
        )
    return payload


def require_admin_session(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency protecting all /admin web and CSV export endpoints."""
    admin_id = request.session.get("admin_user_id")
    if not admin_id:
        raise AdminAuthRequired(next_url=str(request.url.path))

    user = db.scalar(
        select(User).where(
            User.id == int(admin_id),
            User.is_active.is_(True),
            User.role == UserRole.ADMIN,
        )
    )
    if not user:
        request.session.pop("admin_user_id", None)
        raise AdminAuthRequired(next_url=str(request.url.path))
    return user


def get_current_api_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """FastAPI dependency protecting mobile /api/v1/* endpoints via Bearer JWT."""
    if not credentials or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer access token.",
        )

    payload = decode_jwt_token(credentials.credentials, expected_type="access")
    user_id = int(payload["sub"])
    user = db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or inactive.",
        )
    return user
