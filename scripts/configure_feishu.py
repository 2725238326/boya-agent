"""Atomically configure Feishu only; keep a private, explicit rollback copy."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def configure(env_file: Path, backup_dir: Path, alerts: bool, courses: bool, *, probe=False) -> Path:
    from dotenv import dotenv_values, set_key
    from src.push.feishu_bot import _credential_config, _send_sync
    _credential_config()  # validates credentials passed as environment variables
    if not env_file.is_file() or env_file.is_symlink():
        raise ValueError("Expected an existing regular environment file")
    if probe and not _send_sync("博雅服务器接入验收\n飞书签名消息已从生产服务器发出。告警和课程开关将按本次配置生效。", "alerts", probe=True):
        raise RuntimeError("Production Feishu probe failed; environment file unchanged")
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    backup = backup_dir / f"feishu-env-pre-{stamp}.bak"
    fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(env_file.read_bytes())
    metadata = env_file.stat()
    fd, name = tempfile.mkstemp(prefix=".feishu-config-", dir=env_file.parent)
    os.close(fd)
    temp = Path(name)
    try:
        shutil.copyfile(env_file, temp)
        values = {"FEISHU_WEBHOOK_URL": os.environ["FEISHU_WEBHOOK_URL"].strip(),
                  "FEISHU_WEBHOOK_SECRET": os.environ["FEISHU_WEBHOOK_SECRET"].strip(),
                  "FEISHU_ALERTS_ENABLED": str(alerts).lower(),
                  "FEISHU_COURSES_ENABLED": str(courses).lower()}
        for key, value in values.items():
            set_key(str(temp), key, value, quote_mode="always")
        # Every unrelated configuration value must survive the update.
        before, after = dotenv_values(env_file), dotenv_values(temp)
        if any(after.get(key) != value for key, value in before.items() if key not in values):
            raise RuntimeError("Unrelated environment settings changed")
        os.chmod(temp, metadata.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(temp, metadata.st_uid, metadata.st_gid)
        os.replace(temp, env_file)
    finally:
        temp.unlink(missing_ok=True)
    print(f"Feishu configuration saved: alerts={alerts}, courses={courses}; rollback={backup}")
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--backup-dir", type=Path, default=Path("/var/lib/boya-agent/backups"))
    parser.add_argument("--alerts", choices=("true", "false"), required=True)
    parser.add_argument("--courses", choices=("true", "false"), required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        configure(args.env_file, args.backup_dir, args.alerts == "true", args.courses == "true", probe=args.probe)
    except Exception as error:
        print("Feishu configuration failed:", type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
