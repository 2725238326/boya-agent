"""Explicit local staging boundary, validated before importing app resources."""

import os
from pathlib import Path
import uuid


def is_staging() -> bool:
    return os.getenv("APP_ENV", "production").strip().lower() == "staging"


def validate_staging() -> Path:
    if not is_staging():
        raise RuntimeError("dry-run requires APP_ENV=staging")
    root_text = os.getenv("STAGING_ROOT", "")
    if not root_text or not Path(root_text).is_absolute():
        raise RuntimeError("STAGING_ROOT must be absolute")
    root = Path(root_text).resolve()
    for name in ("DATABASE_PATH", "LOG_DIR", "QRCODE_UPLOAD_ROOT", "MAIL_PREVIEW_DIR"):
        value = os.getenv(name, "")
        path = Path(value).resolve() if value else None
        if not path or not path.is_relative_to(root) or path == root:
            raise RuntimeError(f"{name} must be inside STAGING_ROOT")
    if os.getenv("NOTIFICATION_MODE") != "dry_run" or os.getenv("SCRAPE_SOURCE") != "fixture":
        raise RuntimeError("staging requires fixture scraping and dry_run notifications")
    if os.getenv("WEB_HOST") != "127.0.0.1" or not 5001 <= int(os.getenv("WEB_PORT", "0")) <= 65535:
        raise RuntimeError("staging requires loopback and a separate port >= 5001")
    for name, value in os.environ.items():
        if value and (name in {"BUAA_USERNAME", "BUAA_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
                      or name.startswith("SMTP_") or name.startswith("FEISHU_") or name.startswith("TELEGRAM_OPS_")):
            raise RuntimeError("staging must not contain external credentials or SMTP configuration")
    return root


def notification_mode() -> str:
    mode = os.getenv("NOTIFICATION_MODE", "smtp").strip().lower()
    if is_staging() or mode == "dry_run":
        validate_staging()
        return "dry_run"
    if mode != "smtp":
        raise RuntimeError("unknown NOTIFICATION_MODE")
    return mode


def configure_staging(root: Path, *, port: int = 5001, scenario: str = "healthy") -> None:
    """Launcher owns every runtime path and removes inherited external config."""
    root = root.resolve()
    for name in list(os.environ):
        if name.startswith("SMTP_") or name.startswith("FEISHU_") or name.startswith("TELEGRAM_OPS_") or name in {
            "BUAA_USERNAME", "BUAA_PASSWORD", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        }:
            os.environ.pop(name, None)
    os.environ.update({
        "APP_ENV": "staging", "STAGING_ROOT": str(root),
        "DATABASE_PATH": str(root / "data" / "staging.db"),
        "LOG_DIR": str(root / "logs"), "QRCODE_UPLOAD_ROOT": str(root / "uploads"),
        "MAIL_PREVIEW_DIR": str(root / "mail"),
        "NOTIFICATION_MODE": "dry_run", "SCRAPE_SOURCE": "fixture",
        "STAGING_SCENARIO": scenario, "WEB_HOST": "127.0.0.1", "WEB_PORT": str(port),
        "APP_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "APP_TIMEZONE": "Asia/Shanghai",
        "APP_ALLOWED_ORIGINS": f"http://127.0.0.1:{port}",
        "WEB_SECRET_KEY": uuid.uuid4().hex + uuid.uuid4().hex,
        "ADMIN_USERNAME": "staging", "ADMIN_PASSWORD": uuid.uuid4().hex,
        "ADMIN_API_TOKEN": "", "EMAIL_DELIVERY_ENABLED": "true", "EMAIL_DELIVERY_SCOPE": "all",
    })
    validate_staging()
    for name in ("DATABASE_PATH", "LOG_DIR", "QRCODE_UPLOAD_ROOT", "MAIL_PREVIEW_DIR"):
        path = Path(os.environ[name])
        (path.parent if name == "DATABASE_PATH" else path).mkdir(parents=True, exist_ok=True)


def write_preview_email(message) -> bool:
    """Save an RFC822 message locally, never resolving SMTP or opening a socket."""
    validate_staging()
    directory = Path(os.environ["MAIL_PREVIEW_DIR"])
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{uuid.uuid4().hex}.eml"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(message.as_bytes())
    return True
