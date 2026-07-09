import asyncio
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

import yt_dlp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Clip, RenderJob, RenderJobItem

logger = logging.getLogger(__name__)

PALETTE = [
    "#ffe14d", "#ff3b5c", "#37d5ff", "#6cff59", "#ff8a1f",
    "#b98dff", "#ffffff", "#ff4fd8", "#59b0ff", "#ffd23f",
]

CLIPS_DIR = Path(settings.data_dir) / "clips"
RENDERS_DIR = Path(settings.data_dir) / "renders"


def ensure_dirs() -> None:
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    RENDERS_DIR.mkdir(parents=True, exist_ok=True)


def _get_duration(file_path: str) -> float | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", file_path,
            ],
            capture_output=True, text=True, check=True,
        )
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])
    except Exception:
        return None


def _download_from_url(url: str, output_path: Path) -> float | None:
    ydl_opts = {
        "format": "best[ext=mp4]/best",
        "outtmpl": str(output_path.with_suffix("")),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": settings.max_upload_mb * 1024 * 1024,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = output_path.with_suffix(f".{info.get('ext', 'mp4')}")
        if not downloaded.exists():
            candidates = list(output_path.parent.glob(f"{output_path.stem}.*"))
            if candidates:
                downloaded = candidates[0]
        if downloaded != output_path and downloaded.exists():
            downloaded.rename(output_path)
        duration = info.get("duration")
        return float(duration) if duration else _get_duration(str(output_path))


async def import_clip(
    db: AsyncSession,
    url: str,
    platform: str,
    user_id: str | None = None,
) -> Clip:
    ensure_dirs()
    clip_id = str(uuid.uuid4())
    file_path = CLIPS_DIR / f"{clip_id}.mp4"

    duration = await asyncio.to_thread(_download_from_url, url, file_path)

    if duration and duration > settings.max_clip_duration_seconds:
        file_path.unlink(missing_ok=True)
        raise ValueError(f"Clip exceeds {settings.max_clip_duration_seconds}s limit")

    clip = Clip(
        id=clip_id,
        user_id=user_id,
        platform=platform,
        source_url=url,
        file_path=str(file_path),
        duration_seconds=duration,
    )
    db.add(clip)
    await db.commit()
    await db.refresh(clip)
    return clip


async def upload_clip(
    db: AsyncSession,
    file_data: bytes,
    filename: str,
    user_id: str | None = None,
) -> Clip:
    ensure_dirs()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(file_data) > max_bytes:
        raise ValueError(f"File exceeds {settings.max_upload_mb}MB limit")

    clip_id = str(uuid.uuid4())
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "mp4"
    file_path = CLIPS_DIR / f"{clip_id}.{ext}"
    file_path.write_bytes(file_data)

    duration = await asyncio.to_thread(_get_duration, str(file_path))
    if duration and duration > settings.max_clip_duration_seconds:
        file_path.unlink(missing_ok=True)
        raise ValueError(f"Clip exceeds {settings.max_clip_duration_seconds}s limit")

    clip = Clip(
        id=clip_id,
        user_id=user_id,
        filename=filename,
        file_path=str(file_path),
        duration_seconds=duration,
    )
    db.add(clip)
    await db.commit()
    await db.refresh(clip)
    return clip


def _escape_drawtext(text: str) -> str:
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _render_segment(
    clip_path: str,
    rank: int,
    label: str,
    color: str,
    output_path: str,
    duration: float = 5.0,
) -> None:
    clip_duration = _get_duration(clip_path) or duration
    seg_duration = min(clip_duration, settings.max_clip_duration_seconds, duration)

    color_hex = color.lstrip("#")
    badge_text = f"#{rank}"
    label_text = _escape_drawtext(label[:40])

    filter_complex = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,trim=0:{seg_duration},setpts=PTS-STARTPTS[bg];"
        f"color=c=0x{color_hex}:s=120x120,format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='if(lt(hypot(X-60,Y-60),55),255,0)'[badge];"
        f"[bg][badge]overlay=40:40[withbadge];"
        f"[withbadge]drawtext=text='{badge_text}':fontsize=48:fontcolor=black:"
        f"x=70:y=70[ranked];"
        f"[ranked]drawtext=text='{label_text}':fontsize=36:fontcolor=white:"
        f"x=40:y=180:box=1:boxcolor=black@0.5:boxborderw=8"
    )

    cmd = [
        "ffmpeg", "-y", "-i", clip_path,
        "-filter_complex", filter_complex,
        "-t", str(seg_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _render_title_screen(title: str, output_path: str, duration: float = 3.0) -> None:
    title_text = _escape_drawtext(title[:40])
    filter_str = (
        f"color=c=black:s=1080x1920:d={duration},"
        f"drawtext=text='{title_text}':fontsize=64:fontcolor=white:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", filter_str,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-t", str(duration),
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def _concat_segments(segment_paths: list[str], output_path: str) -> None:
    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for p in segment_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file, "-c", "copy", output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    os.unlink(list_file)


def _run_render(job_id: str, title: str, items: list[dict]) -> str:
    ensure_dirs()
    work_dir = RENDERS_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    segments: list[str] = []

    title_seg = str(work_dir / "title.mp4")
    _render_title_screen(title, title_seg)
    segments.append(title_seg)

    sorted_items = sorted(items, key=lambda x: x["order"], reverse=True)
    total = len(sorted_items)

    for idx, item in enumerate(sorted_items):
        rank = total - idx
        color = PALETTE[(rank - 1) % len(PALETTE)]
        seg_path = str(work_dir / f"seg_{rank}.mp4")
        _render_segment(item["clip_path"], rank, item["label"], color, seg_path)
        segments.append(seg_path)

    output_path = str(RENDERS_DIR / f"{job_id}.mp4")
    _concat_segments(segments, output_path)

    for seg in segments:
        Path(seg).unlink(missing_ok=True)
    work_dir.rmdir()

    return output_path


async def create_render_job(
    db: AsyncSession,
    title: str,
    items: list[dict],
    user_id: str | None = None,
) -> RenderJob:
    job_id = str(uuid.uuid4())
    job = RenderJob(id=job_id, title=title, user_id=user_id, status="processing")
    db.add(job)

    clip_paths = []
    for item in items:
        result = await db.execute(select(Clip).where(Clip.id == item["clipId"]))
        clip = result.scalar_one_or_none()
        if not clip:
            raise ValueError(f"Clip {item['clipId']} not found")
        clip_paths.append({
            "clip_path": clip.file_path,
            "label": item["label"],
            "order": item["order"],
        })
        job_item = RenderJobItem(
            job_id=job_id,
            clip_id=item["clipId"],
            label=item["label"],
            order=item["order"],
        )
        db.add(job_item)

    await db.commit()
    await db.refresh(job)

    asyncio.create_task(_process_render(job_id, title, clip_paths, db))
    return job


async def _process_render(
    job_id: str,
    title: str,
    items: list[dict],
    db: AsyncSession,
) -> None:
    from datetime import datetime, timezone

    from app.database import async_session

    try:
        output_path = await asyncio.to_thread(_run_render, job_id, title, items)
        async with async_session() as session:
            result = await session.execute(select(RenderJob).where(RenderJob.id == job_id))
            job = result.scalar_one()
            job.status = "done"
            job.output_path = output_path
            job.ready_at = datetime.now(timezone.utc)
            await session.commit()
    except Exception as e:
        logger.exception("Render job %s failed", job_id)
        async with async_session() as session:
            result = await session.execute(select(RenderJob).where(RenderJob.id == job_id))
            job = result.scalar_one()
            job.status = "failed"
            job.error = str(e)
            await session.commit()


async def get_render_job(db: AsyncSession, job_id: str) -> RenderJob | None:
    result = await db.execute(select(RenderJob).where(RenderJob.id == job_id))
    return result.scalar_one_or_none()
