from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.auth import (
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import AuthError, authenticate, issue_tokens, register_organization

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)) -> RegisterResponse:
    org, user = await register_organization(
        db,
        organization_name=payload.organization_name,
        full_name=payload.full_name,
        email=payload.email,
        password=payload.password,
    )
    access, refresh = issue_tokens(user)
    return RegisterResponse(
        organization_slug=org.slug,
        user=UserResponse.model_validate(user),
        tokens=TokenResponse(access_token=access, refresh_token=refresh),
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    try:
        user = await authenticate(
            db,
            organization_slug=payload.organization_slug,
            email=payload.email,
            password=payload.password,
        )
    except AuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc

    access, refresh = issue_tokens(user)
    return TokenResponse(access_token=access, refresh_token=refresh)
