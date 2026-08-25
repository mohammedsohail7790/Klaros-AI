from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.models.user import User
from app.schemas.auth import UserResponse

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_tenant_db),
) -> UserResponse:
    # Tenant isolation: always filter by tenant_id derived from the JWT, never from input.
    user = (
        await db.execute(
            select(User).where(User.id == current_user.id, User.tenant_id == current_user.tenant_id)
        )
    ).scalar_one()
    return UserResponse.model_validate(user)
