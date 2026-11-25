from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User, UserRole
from app.schemas import UserCreate, UserOut
from app.services.auth import (
    authenticate_user,
    create_access_token,
    create_refresh_token,
    decode_jwt_token,
    get_current_api_user,
    hash_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["Mobile API Auth"])


class LoginRequest(BaseModel):
    identifier: str = Field(..., description="Email address or Ethiopian phone (+2519... / 09...)")
    password: str = Field(..., min_length=6)


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register_mobile_user(payload: UserCreate, db: Session = Depends(get_db)):
    """Registers a new Lijnet customer account from the mobile app."""
    existing = db.scalar(
        select(User).where(
            or_(
                User.email == payload.email.lower(),
                User.phone_number == payload.phone_number,
            )
        )
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email or Ethiopian phone number already exists.",
        )

    user = User(
        full_name=payload.full_name.strip(),
        email=payload.email.lower(),
        phone_number=payload.phone_number,
        password_hash=hash_password(payload.password),
        role=UserRole.CUSTOMER,  # Always enforce CUSTOMER role on public registration
        is_active=True,
        is_verified=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login_mobile_user(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticates a mobile user via email or Ethiopian phone number and issues JWT pair."""
    user = authenticate_user(db, payload.identifier, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email/phone or password.",
        )

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_mobile_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    """Exchanges a valid refresh token for a fresh access + refresh token pair."""
    decoded = decode_jwt_token(payload.refresh_token, expected_type="refresh")
    user = db.scalar(select(User).where(User.id == int(decoded["sub"]), User.is_active.is_(True)))
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer active.",
        )

    return TokenResponse(
        access_token=create_access_token(user),
        refresh_token=create_refresh_token(user),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def get_mobile_profile(current_user: User = Depends(get_current_api_user)):
    """Returns the authenticated mobile user's profile."""
    return UserOut.model_validate(current_user)
