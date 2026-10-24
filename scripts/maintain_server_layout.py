"""Inventory production directories and organize reviewed inactive artifacts.

Standard library only. Never imports application code, loads .env or opens a DB.
The inventory is read-only. Archive/restore operate on reviewed manifest entries.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import uuid


CAPTURE = re.compile(r"[A-Za-z0-9_.-]+\.(?:png|html|zip)\Z")
ROLLBACK = re.compile(r"feishu-config-rollback\.[A-Za-z0-9]{6}\Z")
APP_PATHS = (".git", "venv", ".venv", "src", "web", "config", "deploy", "scripts", "docs", "tests", "logs", "backups", "runtime", "node_modules")
STATE_PATHS = ("data", "backups", "archive", "cache", "playwright", "maintenance")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def plain_directory(path: Path) -> Path:
    path = path.absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("symlinked directory is not supported")
    if not path.is_dir():
        raise ValueError("required directory is missing")
    return path.resolve()


def usage(path: Path) -> dict:
    total = files = links = errors = 0
    if path.is_symlink():
        return {"bytes": 0, "files": 0, "symlinks": 1, "errors": 0}
    if not path.exists():
        return {"missing": True}
    if path.is_file():
        return {"bytes": path.stat().st_size, "files": 1, "symlinks": 0, "errors": 0}

    def error(_exc):
        nonlocal errors
        errors += 1

    for directory, dirs, names in os.walk(path, followlinks=False, onerror=error):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in names:
            try:
                info = (Path(directory) / name).lstat()
                if stat.S_ISLNK(info.st_mode):
                    links += 1
                elif stat.S_ISREG(info.st_mode):
                    total += info.st_size
                    files += 1
            except OSError:
                errors += 1
    return {"bytes": total, "files": files, "symlinks": links, "errors": errors}


def identity(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("only standalone regular files may be archived")
    return {"size": info.st_size, "mtime_ns": info.st_mtime_ns, "device": info.st_dev, "inode": info.st_ino}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def candidates(app: Path, *, days: int, now: float) -> list[dict]:
    logs = app / "logs"
    if not logs.is_dir() or logs.is_symlink():
        return []
    found = []
    for path in sorted(logs.iterdir()):
        if not CAPTURE.fullmatch(path.name):
            continue
        try:
            info = identity(path)
        except (OSError, ValueError):
            continue
        if info["mtime_ns"] / 1e9 > now - days * 86400:
            continue
        found.append({"name": path.name, **info})
    return found


def rollback_candidates(state: Path, *, now: float) -> list[dict]:
    found = []
    for path in sorted(state.glob("feishu-config-rollback.*")):
        if not ROLLBACK.fullmatch(path.name):
            continue
        try:
            info = identity(path)
        except (OSError, ValueError):
            continue
        # In addition to the workflow's production mutex, exclude fresh copies
        # which may belong to a manually running configuration operation.
        if max(info["mtime_ns"] / 1e9, path.stat().st_ctime) <= now - 600:
            found.append({"name": path.name, **info})
    return found


def inventory(app: Path, state: Path, days: int) -> dict:
    return {
        "schema": 1, "generated_at": timestamp(), "app_root": str(app), "state_root": str(state),
        "app": {name: usage(app / name) for name in APP_PATHS},
        "state": {name: usage(state / name) for name in STATE_PATHS},
        "recovery": {path.name: usage(path) for path in sorted(state.glob("release-recovery.*"))},
        "root_database_files": sum(1 for path in app.glob("*.db*") if path.is_file()),
        "loose_config_rollbacks": sum(1 for path in state.glob("feishu-config-rollback.*") if path.is_file()),
        "capture_min_age_days": days,
        "capture_candidates": candidates(app, days=days, now=datetime.now(timezone.utc).timestamp()),
        "rollback_candidates": rollback_candidates(state, now=datetime.now(timezone.utc).timestamp()),
    }


def write_private(path: Path, data: dict) -> None:
    # A fresh exclusive file prevents accidentally overwriting a prior manifest.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def check_service(service: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", service):
        raise ValueError("invalid service name")
    result = subprocess.run(
        ["systemctl", "show", service, "-p", "MainPID", "-p", "ActiveState"],
        capture_output=True, text=True, check=True,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if fields.get("ActiveState") != "active" or fields.get("MainPID") in {None, "0"}:
        raise ValueError("application service must be active")
    return fields["MainPID"]


def archive(app: Path, state: Path, plan_path: Path, service: str, *, kind: str = "captures") -> dict:
    if kind not in {"captures", "configuration"}:
        raise ValueError("unknown archive kind")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("schema") != 1 or plan.get("app_root") != str(app) or plan.get("state_root") != str(state):
        raise ValueError("manifest roots/schema do not match")
    generated = datetime.fromisoformat(plan["generated_at"])
    age = (datetime.now(timezone.utc) - generated).total_seconds()
    if not 0 <= age <= 3600:
        raise ValueError("inventory must be generated within the last hour")
    days = plan["capture_min_age_days"]
    if type(days) is not int or days < 7:
        raise ValueError("captures must be at least seven days old")
    current_time = datetime.now(timezone.utc).timestamp()
    current = (candidates(app, days=days, now=current_time) if kind == "captures"
               else rollback_candidates(state, now=current_time))
    available = {item["name"]: item for item in current}
    entries = plan["capture_candidates" if kind == "captures" else "rollback_candidates"]
    if len({item["name"] for item in entries}) != len(entries):
        raise ValueError("duplicate manifest entry")
    for item in entries:
        if item != available.get(item["name"]):
            raise ValueError("capture changed or is outside the archive policy")
    if not entries:
        return {"archived_files": 0, "bytes": 0, "service_restarted": False}
    pid = check_service(service)
    source_directory = app / "logs" if kind == "captures" else state
    archive_root = state / "archive" if kind == "captures" else state / "backups" / "configuration"
    plain_directory(archive_root.parent)
    archive_root.mkdir(mode=0o700, exist_ok=True)
    plain_directory(archive_root)
    if source_directory.stat().st_dev != archive_root.stat().st_dev:
        raise ValueError("archive requires the same filesystem for atomic moves")
    run = archive_root / (datetime.now(timezone.utc).strftime(kind + "-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    run.mkdir(mode=0o700)
    journal = {"schema": 1, "kind": kind, "app_root": str(app), "state_root": str(state), "generated_at": timestamp(), "service_pid": pid, "entries": []}
    # Write-ahead records allow recovery even if interrupted after a rename.
    write_private(run / "manifest.json", {**journal, "entries": [{**item, "sha256": digest(source_directory / item["name"])} for item in entries]})
    for item in entries:
        source = source_directory / item["name"]
        if identity(source) != {key: item[key] for key in ("size", "mtime_ns", "device", "inode")}:
            raise ValueError("capture changed during archive; use the manifest to restore")
        source.rename(run / item["name"])
    if check_service(service) != pid:
        raise ValueError("service process changed during maintenance; inspect the archive manifest")
    return {"archived_files": len(entries), "bytes": sum(item["size"] for item in entries), "manifest": str(run / "manifest.json"), "service_restarted": False}


def restore(app: Path, state: Path, manifest_path: Path) -> dict:
    if manifest_path.is_symlink():
        raise ValueError("symlinked manifests are not supported")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    kind = data.get("kind", "captures")
    if kind not in {"captures", "configuration"}:
        raise ValueError("unknown archive kind")
    archive_root = plain_directory(state / "archive" if kind == "captures" else state / "backups" / "configuration")
    run = plain_directory(manifest_path.parent)
    if run.parent != archive_root or manifest_path.name != "manifest.json":
        raise ValueError("restore manifest must be inside a managed archive")
    if data.get("schema") != 1 or data.get("app_root") != str(app) or data.get("state_root") != str(state):
        raise ValueError("manifest roots/schema do not match")
    destination = plain_directory(app / "logs") if kind == "captures" else state
    name_pattern = CAPTURE if kind == "captures" else ROLLBACK
    pending = []
    for item in data["entries"]:
        name = item["name"]
        if not name_pattern.fullmatch(name):
            raise ValueError("invalid capture name")
        source, target = run / name, destination / name
        if target.exists() or target.is_symlink():
            if identity(target)["size"] == item["size"] and digest(target) == item["sha256"]:
                continue  # Already restored, or interrupted before the move.
            raise ValueError("restore would overwrite a file")
        if identity(source)["size"] != item["size"] or digest(source) != item["sha256"]:
            raise ValueError("restore would overwrite a file or capture integrity changed")
        pending.append((source, target))
    for source, target in pending:
        # Exclusive creation also prevents overwrite if a destination appears
        # between validation and restore. Retain the archive copy for recovery.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with source.open("rb") as reader, os.fdopen(fd, "wb") as writer:
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        shutil.copystat(source, target, follow_symlinks=False)
    return {"restored_files": len(pending)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--state-root", type=Path, required=True)
    parser.add_argument("--service", default="boya-agent")
    parser.add_argument("--min-age-days", type=int, default=7)
    parser.add_argument("--output", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--archive-plan", type=Path)
    action.add_argument("--organize-plan", type=Path)
    action.add_argument("--restore-manifest", type=Path)
    args = parser.parse_args()
    try:
        app, state = plain_directory(args.app_root), plain_directory(args.state_root)
        if app == state or app.is_relative_to(state) or state.is_relative_to(app):
            raise ValueError("application and state roots must be separate")
        if args.min_age_days < 7:
            raise ValueError("minimum archive age is seven days")
        if args.archive_plan:
            result = archive(app, state, args.archive_plan, args.service)
        elif args.organize_plan:
            result = archive(app, state, args.organize_plan, args.service, kind="configuration")
        elif args.restore_manifest:
            result = restore(app, state, args.restore_manifest)
        else:
            result = inventory(app, state, args.min_age_days)
        if args.output:
            write_private(args.output, result)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        # Never print exception text which might embed file contents or secrets.
        print(f"Layout maintenance failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
