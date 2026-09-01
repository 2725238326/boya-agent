"""
Isolated service helpers for the QRCode upload feature.

This module is intentionally kept separate from the main portal/course flow so
future review, reward, and moderation logic can evolve without destabilizing
the existing scraping pipeline.
"""

from __future__ import annotations

import hashlib
import io
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

from loguru import logger
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from src.course_state import get_check_in_display_label, is_course_expired
from src.models import Course, EmailSubscriber, QRCodeUpload
from src.time_utils import now as business_now

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # pragma: no cover - production installs Pillow from requirements.txt
    Image = None
    UnidentifiedImageError = OSError

BASE_DIR = Path(__file__).resolve().parent.parent
QRCODE_UPLOAD_ROOT = BASE_DIR / "config" / "uploads" / "qrcode"
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
ALLOWED_IMAGE_FORMATS = {
    ".png": "PNG",
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".gif": "GIF",
    ".webp": "WEBP",
}
MAX_QRCODE_FILE_SIZE = max(64 * 1024, int(os.getenv("QRCODE_MAX_FILE_SIZE", str(5 * 1024 * 1024))))
PUBLIC_QRCODE_STATUS = "approved"
REWARD_THRESHOLDS = (3, 10, 30)
LEADERBOARD_LIMIT = 10


def ensure_qrcode_upload_root() -> Path:
    QRCODE_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return QRCODE_UPLOAD_ROOT


def is_allowed_qrcode_file(filename: str) -> bool:
    suffix = Path(filename or "").suffix.lower()
    return suffix in ALLOWED_IMAGE_SUFFIXES


def mask_contributor_email(email: str) -> str:
    normalized = (email or "").strip().lower()
    if "@" not in normalized:
        return normalized
    local, domain = normalized.split("@", 1)
    if len(local) <= 2:
        local_masked = local[:1] + "*"
    else:
        local_masked = local[:2] + "*" * max(1, len(local) - 2)
    return f"{local_masked}@{domain}"


def resolve_contributor(session, session_token: str = "", submitted_email: str = "") -> Dict[str, Optional[str]]:
    """Resolve a contributor from the verified portal session only.

    ``submitted_email`` remains in the signature for callers from older code,
    but is deliberately ignored so a user cannot claim another contributor's
    reward history by typing an email address.
    """
    token = (session_token or "").strip()

    if token:
        subscriber = (
            session.query(EmailSubscriber)
            .filter_by(token=token, verified=True, active=True)
            .first()
        )
        if subscriber:
            return {
                "email": subscriber.email,
                "subscriber_id": subscriber.id,
            }

    return {
        "email": "",
        "subscriber_id": None,
    }


def get_contributor_stats(session, email: str) -> Dict[str, object]:
    normalized = (email or "").strip().lower()
    if not normalized:
        return {
            "masked_email": "",
            "total_uploads": 0,
            "reward_threshold_reached": None,
            "next_reward_threshold": REWARD_THRESHOLDS[0],
        }

    public_rows = session.query(QRCodeUpload).filter(
        QRCodeUpload.contributor_email == normalized,
        QRCodeUpload.is_active == True,  # noqa: E712
        QRCodeUpload.verification_status == PUBLIC_QRCODE_STATUS,
    ).all()
    total_uploads = len(_exclude_expired_course_uploads(session, public_rows))
    reached = [value for value in REWARD_THRESHOLDS if total_uploads >= value]
    next_threshold = next((value for value in REWARD_THRESHOLDS if total_uploads < value), None)
    return {
        "masked_email": mask_contributor_email(normalized),
        "total_uploads": total_uploads,
        "reward_threshold_reached": reached[-1] if reached else None,
        "next_reward_threshold": next_threshold,
    }


def _exclude_expired_course_uploads(session, rows: list[QRCodeUpload]) -> list[QRCodeUpload]:
    """Remove uploads linked to courses that are no longer public.

    Unlinked uploads (empty ``course_id``) remain compatible with the original
    standalone QR page. A non-empty link to a deleted course is hidden as well
    as an expired course, so cleanup cannot leave stale images publicly visible.
    """
    course_ids = {row.course_id for row in rows if row.course_id}
    if not course_ids:
        return rows
    courses = session.query(Course).filter(Course.id.in_(course_ids)).all()
    known_ids = {course.id for course in courses}
    expired_ids = {course.id for course in courses if is_course_expired(course)}
    return [
        row
        for row in rows
        if not row.course_id
        or (row.course_id in known_ids and row.course_id not in expired_ids)
    ]


def _format_course_time(course: Course) -> str:
    start = course.start_time.strftime("%Y-%m-%d %H:%M") if course.start_time else ""
    end = course.end_time.strftime("%Y-%m-%d %H:%M") if course.end_time else ""
    if start and end:
        return f"{start} ~ {end}"
    return start or end


def get_qrcode_course_context(session, course_id: str) -> Optional[dict]:
    normalized = (course_id or "").strip()
    if not normalized:
        return None

    course = session.query(Course).filter_by(id=normalized).first()
    if not course:
        return None

    return {
        "id": course.id,
        "name": course.name,
        "category": course.category,
        "teacher": course.teacher,
        "campus": course.campus,
        "location": course.location,
        "course_time": _format_course_time(course),
        "check_in_label": get_check_in_display_label(course),
        "expired": is_course_expired(course),
        "remaining": course.remaining,
    }


def _period_bounds(period: str) -> tuple[Optional[datetime], str]:
    now = business_now()
    if period == "weekly":
        start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=7)
        return start, f"{start.strftime('%m/%d')} - {(end - timedelta(seconds=1)).strftime('%m/%d')}"
    return None, "累计"


def get_contributor_leaderboard(
    session,
    *,
    course_id: str = "",
    period: str = "weekly",
    limit: int = LEADERBOARD_LIMIT,
) -> dict:
    since, period_label = _period_bounds(period)
    query = session.query(QRCodeUpload).filter(
        QRCodeUpload.is_active == True,  # noqa: E712
        QRCodeUpload.verification_status == PUBLIC_QRCODE_STATUS,
    )

    normalized_course_id = (course_id or "").strip()
    if normalized_course_id:
        query = query.filter(QRCodeUpload.course_id == normalized_course_id)
    if since is not None:
        query = query.filter(QRCodeUpload.created_at >= since)

    rows = _exclude_expired_course_uploads(session, query.all())
    counts = {}
    for row in rows:
        counts[row.contributor_email] = counts.get(row.contributor_email, 0) + 1
    sorted_contributors = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    rows = sorted_contributors[: max(1, min(limit, 50))]

    return {
        "period": period,
        "period_label": period_label,
        "items": [
            {
                "rank": index + 1,
                "masked_email": mask_contributor_email(email),
                "upload_count": int(upload_count or 0),
            }
            for index, (email, upload_count) in enumerate(rows)
        ],
    }


def _build_public_file_url(relative_path: str) -> str:
    return f"/qrcode/uploads/{quote(relative_path)}"


def save_qrcode_file(file_storage: FileStorage) -> Dict[str, str | int]:
    """验证并保存二维码图片，返回实际格式和内容摘要。"""
    ensure_qrcode_upload_root()
    original_name = secure_filename(file_storage.filename or "")
    if not original_name:
        raise ValueError("empty filename")

    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("unsupported file type")

    if Image is None:
        raise RuntimeError("Pillow is required to validate QR code images")

    stream = getattr(file_storage, "stream", None)
    if stream is None:
        raise ValueError("missing file stream")
    stream.seek(0)
    data = stream.read(MAX_QRCODE_FILE_SIZE + 1)
    if not data:
        raise ValueError("empty file")
    if len(data) > MAX_QRCODE_FILE_SIZE:
        raise ValueError("file too large")

    try:
        with Image.open(io.BytesIO(data)) as image:
            actual_format = (image.format or "").upper()
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            if image.format != actual_format or image.width <= 0 or image.height <= 0:
                raise ValueError("invalid image dimensions")
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("invalid image content") from exc

    expected_format = ALLOWED_IMAGE_FORMATS[suffix]
    if actual_format != expected_format:
        raise ValueError("file extension does not match image content")

    day_dir = business_now().strftime("%Y%m%d")
    relative_dir = Path(day_dir)
    absolute_dir = QRCODE_UPLOAD_ROOT / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)

    generated_name = f"{business_now().strftime('%H%M%S')}_{secrets.token_hex(8)}{suffix}"
    absolute_path = absolute_dir / generated_name
    absolute_path.write_bytes(data)
    file_size = absolute_path.stat().st_size

    return {
        "relative_path": (relative_dir / generated_name).as_posix(),
        "original_filename": original_name,
        "mime_type": Image.MIME.get(actual_format, "application/octet-stream"),
        "file_size": int(file_size),
        "content_hash": hashlib.sha256(data).hexdigest(),
    }


def create_qrcode_upload(
    session,
    *,
    course_id: str,
    contributor_email: str,
    contributor_subscriber_id: Optional[int],
    course_name: str,
    course_time: str,
    course_location: str,
    notes: str,
    file_storage: FileStorage,
) -> QRCodeUpload:
    stored = save_qrcode_file(file_storage)
    normalized_course_id = (course_id or "").strip()
    normalized_email = (contributor_email or "").strip().lower()
    if "@" not in normalized_email:
        _remove_saved_file(stored)
        raise ValueError("invalid contributor email")

    if normalized_course_id and not session.query(Course).filter_by(id=normalized_course_id).first():
        _remove_saved_file(stored)
        raise ValueError("invalid course")

    duplicate = (
        session.query(QRCodeUpload)
        .filter(
            QRCodeUpload.course_id == normalized_course_id,
            QRCodeUpload.content_hash == stored["content_hash"],
            QRCodeUpload.verification_status != "rejected",
        )
        .first()
    )
    if duplicate:
        _remove_saved_file(stored)
        raise ValueError("duplicate QR code")

    current_time = business_now()
    upload = QRCodeUpload(
        course_id=normalized_course_id,
        contributor_email=normalized_email,
        contributor_subscriber_id=contributor_subscriber_id,
        course_name=(course_name or "").strip(),
        course_time=(course_time or "").strip(),
        course_location=(course_location or "").strip(),
        notes=(notes or "").strip(),
        file_path=str(stored["relative_path"]),
        original_filename=str(stored["original_filename"]),
        mime_type=str(stored["mime_type"]),
        file_size=int(stored["file_size"]),
        content_hash=str(stored["content_hash"]),
        verification_status="pending",
        is_active=True,
        created_at=current_time,
        updated_at=current_time,
    )
    try:
        session.add(upload)
        session.flush()
    except Exception:
        _remove_saved_file(stored)
        raise
    logger.info("qrcode upload created: id={} contributor={}", upload.id, mask_contributor_email(upload.contributor_email))
    return upload


def _remove_saved_file(stored: Dict[str, str | int]) -> None:
    """删除本次请求刚写入的单个文件，避免校验失败留下孤儿文件。"""
    path = QRCODE_UPLOAD_ROOT / str(stored["relative_path"])
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("qrcode orphan cleanup failed: {}", exc)


def list_public_qrcode_uploads(session, limit: int = 50, course_id: str = "") -> list[dict]:
    query = (
        session.query(QRCodeUpload)
        .filter(
            QRCodeUpload.is_active == True,  # noqa: E712
            QRCodeUpload.verification_status == PUBLIC_QRCODE_STATUS,
        )
    )
    normalized_course_id = (course_id or "").strip()
    if normalized_course_id:
        query = query.filter(QRCodeUpload.course_id == normalized_course_id)

    rows = (
        query
        .order_by(QRCodeUpload.created_at.desc())
        .limit(max(1, min(limit, 100)) * 3)
        .all()
    )

    # 课程结束后，既不能继续上传，也不应继续出现在公开二维码列表里。
    # 这里在应用层过滤是为了兼容旧记录中可能缺失的课程外键。
    rows = _exclude_expired_course_uploads(session, rows)
    rows = rows[: max(1, min(limit, 100))]

    contribution_counts = {}
    for row in rows:
        contribution_counts[row.contributor_email] = contribution_counts.get(row.contributor_email, 0) + 1

    result = []
    for row in rows:
        payload = row.to_dict()
        payload["image_url"] = _build_public_file_url(row.file_path)
        payload["contributor_upload_count"] = contribution_counts.get(row.contributor_email, 0)
        payload["masked_contributor_email"] = mask_contributor_email(row.contributor_email)
        result.append(payload)
    return result
