"""
Isolated service helpers for the QRCode upload feature.

This module is intentionally kept separate from the main portal/course flow so
future review, reward, and moderation logic can evolve without destabilizing
the existing scraping pipeline.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote

from loguru import logger
from sqlalchemy import func
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from src.models import EmailSubscriber, QRCodeUpload

BASE_DIR = Path(__file__).resolve().parent.parent
QRCODE_UPLOAD_ROOT = BASE_DIR / "config" / "uploads" / "qrcode"
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
REWARD_THRESHOLDS = (3, 10, 30)


def ensure_qrcode_upload_root() -> Path:
    QRCODE_UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    return QRCODE_UPLOAD_ROOT


def is_allowed_qrcode_file(filename: str) -> bool:
    suffix = Path(filename or "").suffix.lower()
    return suffix in ALLOWED_IMAGE_SUFFIXES


def resolve_contributor(session, session_token: str = "", submitted_email: str = "") -> Dict[str, Optional[str]]:
    token = (session_token or "").strip()
    email = (submitted_email or "").strip().lower()

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

    subscriber = None
    if email and "@" in email:
        subscriber = session.query(EmailSubscriber).filter_by(email=email).first()

    return {
        "email": email,
        "subscriber_id": subscriber.id if subscriber else None,
    }


def get_contributor_stats(session, email: str) -> Dict[str, Optional[int]]:
    normalized = (email or "").strip().lower()
    if not normalized:
        return {
            "email": "",
            "total_uploads": 0,
            "reward_threshold_reached": None,
            "next_reward_threshold": REWARD_THRESHOLDS[0],
        }

    total_uploads = (
        session.query(QRCodeUpload)
        .filter(QRCodeUpload.contributor_email == normalized)
        .count()
    )
    reached = [value for value in REWARD_THRESHOLDS if total_uploads >= value]
    next_threshold = next((value for value in REWARD_THRESHOLDS if total_uploads < value), None)
    return {
        "email": normalized,
        "total_uploads": total_uploads,
        "reward_threshold_reached": reached[-1] if reached else None,
        "next_reward_threshold": next_threshold,
    }


def _build_public_file_url(relative_path: str) -> str:
    return f"/qrcode/uploads/{quote(relative_path)}"


def save_qrcode_file(file_storage: FileStorage) -> Dict[str, str | int]:
    ensure_qrcode_upload_root()
    original_name = secure_filename(file_storage.filename or "")
    if not original_name:
        raise ValueError("empty filename")

    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        raise ValueError("unsupported file type")

    day_dir = datetime.now().strftime("%Y%m%d")
    relative_dir = Path(day_dir)
    absolute_dir = QRCODE_UPLOAD_ROOT / relative_dir
    absolute_dir.mkdir(parents=True, exist_ok=True)

    generated_name = f"{datetime.now().strftime('%H%M%S')}_{secrets.token_hex(8)}{suffix}"
    absolute_path = absolute_dir / generated_name
    file_storage.save(absolute_path)
    file_size = absolute_path.stat().st_size if absolute_path.exists() else 0

    return {
        "relative_path": (relative_dir / generated_name).as_posix(),
        "original_filename": original_name,
        "mime_type": (file_storage.mimetype or "").strip(),
        "file_size": int(file_size),
    }


def create_qrcode_upload(
    session,
    *,
    contributor_email: str,
    contributor_subscriber_id: Optional[int],
    course_name: str,
    course_time: str,
    course_location: str,
    notes: str,
    file_storage: FileStorage,
) -> QRCodeUpload:
    stored = save_qrcode_file(file_storage)
    now = datetime.now()
    upload = QRCodeUpload(
        contributor_email=contributor_email,
        contributor_subscriber_id=contributor_subscriber_id,
        course_name=(course_name or "").strip(),
        course_time=(course_time or "").strip(),
        course_location=(course_location or "").strip(),
        notes=(notes or "").strip(),
        file_path=str(stored["relative_path"]),
        original_filename=str(stored["original_filename"]),
        mime_type=str(stored["mime_type"]),
        file_size=int(stored["file_size"]),
        verification_status="pending",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(upload)
    session.flush()
    logger.info(f"qrcode upload created: id={upload.id} email={upload.contributor_email}")
    return upload


def list_public_qrcode_uploads(session, limit: int = 50) -> list[dict]:
    rows = (
        session.query(QRCodeUpload)
        .filter(QRCodeUpload.is_active == True)  # noqa: E712
        .order_by(QRCodeUpload.created_at.desc())
        .limit(max(1, min(limit, 100)))
        .all()
    )

    count_rows = (
        session.query(QRCodeUpload.contributor_email, func.count(QRCodeUpload.id))
        .group_by(QRCodeUpload.contributor_email)
        .all()
    )
    contribution_counts = {email: int(count or 0) for email, count in count_rows}

    result = []
    for row in rows:
        payload = row.to_dict()
        payload["image_url"] = _build_public_file_url(row.file_path)
        payload["contributor_upload_count"] = contribution_counts.get(row.contributor_email, 0)
        result.append(payload)
    return result
