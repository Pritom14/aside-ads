from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse, SignupRequest, TokenResponse

router = APIRouter()


@router.post("/signup", response_model=MeResponse)
async def signup(payload: SignupRequest, db: AsyncSession = Depends(get_db)) -> MeResponse:
    existing = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    user = User(email=payload.email, hashed_password=hash_password(payload.password), role="business")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return MeResponse(id=str(user.id), email=user.email, role=user.role)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    token = create_access_token(str(user.id), settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    return TokenResponse(access_token=token)


@router.get("/me", response_model=MeResponse)
async def me(current_user: User = Depends(get_current_user)) -> MeResponse:
    return MeResponse(id=str(current_user.id), email=current_user.email, role=current_user.role)

