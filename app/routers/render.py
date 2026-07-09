from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import optional_user
from app.models import User
from app.schemas import (
    RenderDownloadResponse,
    RenderRequest,
    RenderResponse,
    RenderStatusResponse,
    UsageStatusResponse,
)
from app.services.usage import check_usage
from app.services.video import create_render_job, get_render_job

router = APIRouter(tags=["render", "usage"])


@router.get("/api/usage/status", response_model=UsageStatusResponse)
async def usage_status(user: User | None = Depends(optional_user)):
    result = await check_usage(user)
    return UsageStatusResponse(**result)


@router.post("/api/render", response_model=RenderResponse)
async def start_render(
    body: RenderRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(optional_user),
):
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="title is required")
    if not body.items or len(body.items) < 2:
        raise HTTPException(status_code=400, detail="At least 2 clips are required")
    if len(body.items) > settings.max_ranking_items:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {settings.max_ranking_items} clips allowed",
        )

    items = [{"clipId": i.clipId, "label": i.label, "order": i.order} for i in body.items]
    try:
        job = await create_render_job(
            db, body.title.strip(), items, user.id if user else None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RenderResponse(jobId=job.id)


@router.get("/api/render/{job_id}/status", response_model=RenderStatusResponse)
async def render_status(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_render_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return RenderStatusResponse(status=job.status, error=job.error)


@router.get("/api/render/{job_id}/download", response_model=RenderDownloadResponse)
async def render_download(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_render_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "done":
        raise HTTPException(status_code=409, detail="Render not ready yet")

    url = f"{settings.site_url}/api/render/{job_id}/file"
    return RenderDownloadResponse(url=url)


@router.get("/api/render/{job_id}/file")
async def render_file(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await get_render_job(db, job_id)
    if not job or job.status != "done" or not job.output_path:
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(job.output_path, media_type="video/mp4", filename=f"{job_id}.mp4")
