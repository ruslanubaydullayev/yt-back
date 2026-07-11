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
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def _font_param() -> str:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    ):
        if os.path.exists(path):
            return f"fontfile={path}:"
    return ""


def _hex_color(color: str) -> str:
    return color.lstrip("#")


def _estimate_text_width(text: str, font_size: int) -> int:
    return int(len(text) * font_size * 0.6)


def _fit_font_size(text: str, max_width: int, max_size: int, min_size: int) -> int:
    for size in range(max_size, min_size - 1, -2):
        if _estimate_text_width(text, size) <= max_width:
            return size
    return min_size


def _build_overlay_filters(
    title: str,
    total: int,
    current_rank: int,
    rank_labels: dict[int, str],
    input_label: str = "bg",
) -> tuple[str, str]:
    font = _font_param()
    filters: list[str] = []
    current = input_label

    header_h = 140
    out = "hdrbar"
    filters.append(
        f"[{current}]drawbox=x=0:y=0:w=iw:h={header_h}:color=black@0.9:t=fill[{out}]"
    )
    current = out

    display_title = title.strip()[:80]
    words = display_title.split()
    if words:
        full_line = " ".join(words)
        title_size = _fit_font_size(full_line, 1020, 42, 18)
        total_w = _estimate_text_width(full_line, title_size)
        x = max(20, (1080 - total_w) // 2)
        y = 48
        for i, word in enumerate(words):
            segment = word if i == len(words) - 1 else f"{word} "
            color = _hex_color(PALETTE[i % len(PALETTE)])
            escaped = _escape_drawtext(segment)
            out = f"hdr{i}"
            filters.append(
                f"[{current}]drawtext={font}text='{escaped}':fontcolor=0x{color}:"
                f"fontsize={title_size}:borderw=4:bordercolor=black@0.9:x={x}:y={y}[{out}]"
            )
            current = out
            x += _estimate_text_width(segment, title_size)

    line_height = min(130, 900 // max(total, 1))
    block_height = total * line_height
    list_top = (1920 - block_height) // 2
    left_x = 40
    num_size = 80
    max_label_width = 1080 - left_x - 120

    for rank in range(1, total + 1):
        color = _hex_color(PALETTE[(rank - 1) % len(PALETTE)])
        y = list_top + (rank - 1) * line_height
        num_text = _escape_drawtext(f"{rank}.")
        raw_label = rank_labels.get(rank, "") if rank >= current_rank else ""

        out = f"rnk{rank}"
        filters.append(
            f"[{current}]drawtext={font}text='{num_text}':fontcolor=0x{color}:"
            f"fontsize={num_size}:borderw=5:bordercolor=black@0.9:x={left_x}:y={y}[{out}]"
        )
        current = out

        if raw_label:
            num_w = _estimate_text_width(f"{rank}.", num_size)
            gap = _estimate_text_width(" ", num_size)
            label_size = _fit_font_size(raw_label, max_label_width - num_w - gap, 60, 22)
            label = _escape_drawtext(raw_label)
            out = f"lbl{rank}"
            filters.append(
                f"[{current}]drawtext={font}text='{label}':fontcolor=white:"
                f"fontsize={label_size}:borderw=4:bordercolor=black@0.9:"
                f"x={left_x + num_w + gap}:y={y + 8}[{out}]"
            )
            current = out

    return ";".join(filters), current


def _render_segment(
    clip_path: str,
    current_rank: int,
    rank_labels: dict[int, str],
    title: str,
    total: int,
    output_path: str,
    duration: float = 5.0,
) -> None:
    clip_duration = _get_duration(clip_path) or duration
    seg_duration = min(clip_duration, settings.max_clip_duration_seconds, duration)

    overlay_filters, final_label = _build_overlay_filters(
        title, total, current_rank, rank_labels
    )

    filter_complex = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,trim=0:{seg_duration},setpts=PTS-STARTPTS[bg];"
        f"{overlay_filters}"
    )

    cmd = [
        "ffmpeg", "-y", "-i", clip_path,
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-t", str(seg_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-an", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg segment failed: %s", result.stderr)
        raise RuntimeError(f"ffmpeg failed for rank {current_rank}")


def _concat_segments(segment_paths: list[str], output_path: str) -> None:
    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for p in segment_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-an", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(list_file)
    if result.returncode != 0:
        logger.error("ffmpeg concat failed: %s", result.stderr)
        raise RuntimeError("ffmpeg concat failed")


def _run_render(job_id: str, title: str, items: list[dict]) -> str:
    ensure_dirs()
    work_dir = RENDERS_DIR / job_id
    work_dir.mkdir(parents=True, exist_ok=True)

    by_order = sorted(items, key=lambda x: x["order"])
    total = len(by_order)
    rank_labels = {i + 1: by_order[i]["label"] for i in range(total)}

    segments: list[str] = []
    play_order = sorted(items, key=lambda x: x["order"], reverse=True)

    for idx, item in enumerate(play_order):
        current_rank = total - idx
        seg_path = str(work_dir / f"seg_{current_rank}.mp4")
        _render_segment(
            item["clip_path"],
            current_rank,
            rank_labels,
            title,
            total,
            seg_path,
        )
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
            job.ready_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
