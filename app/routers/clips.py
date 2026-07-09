from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import optional_user
from app.models import User
from app.schemas import ClipImportRequest, ClipImportResponse, ClipUploadResponse
from app.services.video import import_clip, upload_clip

router = APIRouter(prefix="/api/clips", tags=["clips"])


@router.post("/import", response_model=ClipImportResponse)
async def clip_import(
    body: ClipImportRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    try:
        clip = await import_clip(db, body.url, body.platform, user.id if user else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to import clip: {e}")

    return ClipImportResponse(
        clipId=clip.id,
        platform=body.platform,
        durationSeconds=clip.duration_seconds,
    )


@router.post("/upload", response_model=ClipUploadResponse)
async def clip_upload(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="file is required")

    data = await file.read()
    try:
        clip = await upload_clip(db, data, file.filename, user.id if user else None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return ClipUploadResponse(clipId=clip.id)
