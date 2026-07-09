from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require_user
from app.models import User
from app.schemas import UpdateProfileRequest, UpdateProfileResponse

router = APIRouter(tags=["users"])


@router.post("/api/update-profile", response_model=UpdateProfileResponse)
async def update_profile(
    body: UpdateProfileRequest,
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    user.name = body.name
    await db.commit()
    await db.refresh(user)
    return UpdateProfileResponse(
        status=200,
        body={
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "image": user.image,
        },
    )


@router.delete("/api/delete-account")
async def delete_account(
    user: User = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    await db.delete(user)
    await db.commit()
    return {"status": 200}
