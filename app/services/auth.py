import re
from datetime import datetime, timedelta, timezone

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
ALGORITHM = "HS256"


class AdminAuthRequired(Exception):
    def __init__(self, next_url: str = "/admin"):
        self.next_url = next_url


def normalize_ethiopian_phone(raw_phone: str) -> str:
    """Normalizes 09XXXXXXXX / 07XXXXXXXX or 2519XXXXXXXX into +2519XXXXXXXX."""
    cleaned = re.sub(r"[\s\-()]+", "", raw_phone.strip())
    if cleaned.startswith(("09", "07")) and len(cleaned) == 10:
        return "+251" + cleaned[1:]
    if cleaned.startswith("251") and not cleaned.startswith("+"):
        return "+" + cleaned
    return cleaned


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def authenticate_user(db: Session, identifier: str, password: str) -> User | None:
    ident = identifier.strip()
    norm_phone = normalize_ethiopian_phone(ident)
    user = db.scalar(
        select(User).where(
            or_(
                User.email == ident.lower(),
                User.phone_number == ident,
                User.phone_number == norm_phone,
            )
        )
    )
    if not user or not user.is_active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def _build_jwt(
    subject: User | dict | int | str,
    token_type: str,
    expires_delta: timedelta,
) -> str:
    expire = datetime.now(timezone.utc) + expires_delta
    if isinstance(subject, User):
        payload = {
            "sub": str(subject.id),
            "email": subject.email,
            "role": subject.role.value,
            "type": token_type,
            "exp": expire,
        }
    elif isinstance(subject, dict):
        payload = {**subject, "type": token_type, "exp": expire}
        if "sub" in payload:
            payload["sub"] = str(payload["sub"])
    else:
        payload = {
            "sub": str(subject),
            "type": token_type,
            "exp": expire,
        }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(
    subject: User | dict | int | str,
    expires_delta: timedelta | None = None,
) -> str:
    return _build_jwt(
        subject=subject,
        token_type="access",
        expires_delta=expires_delta or timedelta(days=7),
    )


def create_refresh_token(
    subject: User | dict | int | str,
    expires_delta: timedelta | None = None,
) -> str:
    return _build_jwt(
        subject=subject,
        token_type="refresh",
        expires_delta=expires_delta or timedelta(days=30),
    )


def decode_jwt_token(token: str, expected_type: str | None = None) -> dict:
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
    if expected_type and payload.get("type") not in (None, expected_type):
        raise jwt.InvalidTokenError(f"Expected {expected_type} token.")
    return payload


decode_token = decode_jwt_token


def get_current_api_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer authentication token.",
        )
    try:
        payload = decode_jwt_token(credentials.credentials)
        sub = payload.get("sub")
        if sub is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        )

    user = (
        db.scalar(select(User).where(User.id == int(sub)))
        if str(sub).isdigit()
        else db.scalar(select(User).where(User.email == str(sub)))
    )
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account not found or deactivated.",
        )
    return user


def require_admin_session(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    admin_id = request.session.get("admin_user_id")
    if not admin_id:
        raise AdminAuthRequired(next_url=request.url.path)

    user = db.scalar(select(User).where(User.id == int(admin_id)))
    if not user or not user.is_active or user.role != UserRole.ADMIN:
        request.session.pop("admin_user_id", None)
        raise AdminAuthRequired(next_url=request.url.path)
    return user


def get_storefront_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.scalar(select(User).where(User.id == int(user_id)))
    if not user or not user.is_active:
        request.session.pop("user_id", None)
        request.session.pop("user_name", None)
        request.session.pop("user_role", None)
        return None
    return user


class VendorAuthRequired(Exception):
    def __init__(self, next_url: str = "/seller", message: str | None = None):
        self.next_url = next_url
        self.message = message


def require_vendor_session(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    user = get_storefront_user(request, db)
    if not user:
        raise VendorAuthRequired(
            next_url=request.url.path,
            message="Please sign in or register as a Product Owner / Seller to access the Seller Portal.",
        )
    if user.role not in (UserRole.VENDOR, UserRole.ADMIN):
        raise VendorAuthRequired(
            next_url=request.url.path,
            message="Your account is registered as a Buyer. Register a Product Owner / Seller account to list products.",
        )
    return user
