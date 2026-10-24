"""Protect dependency checks and production archive/restore boundaries."""

import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time

import pytest


def module(name):
    path = Path(__file__).resolve().parents[1] / "scripts" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


checks = module("check_repository")
layout = module("maintain_server_layout")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    app, state = tmp_path / "app", tmp_path / "state"
    (app / "logs").mkdir(parents=True)
    state.mkdir()
    monkeypatch.setattr(layout, "check_service", lambda _service: "42")
    return app, state


def capture(app, name="scrape_error.png", *, days=10):
    path = app / "logs" / name
    path.write_bytes(b"offline diagnostic")
    old = time.time() - days * 86400
    os.utime(path, (old, old))
    return path


def plan(app, state):
    path = state / "plan.json"
    path.write_text(json.dumps(layout.inventory(app, state, 7)), encoding="utf-8")
    return path


def test_dependency_direction_handles_nested_and_from_imports():
    assert checks.boundary_errors("src/push/ops_bot.py", "def f():\n from src import scheduler\n")
    assert checks.boundary_errors("src/models.py", "from web.security import protected")
    assert checks.boundary_errors("src/course_state.py", "from sqlalchemy import select")
    assert checks.boundary_errors("src/course_state.py", "from . import models")
    assert checks.boundary_errors("src/push/ops_bot.py", "from ..scheduler import start_scheduler")
    assert not checks.boundary_errors("src/main.py", "from web.app import app")
    assert not checks.boundary_errors("src/push/ops_bot.py", "from src.models import get_session")


def test_current_document_links_ignore_samples_and_resolve_encoded_paths(tmp_path):
    (tmp_path / "space file.md").write_text("# ok", encoding="utf-8")
    document = tmp_path / "README.md"
    document.write_text(
        '[ok](space%20file.md#heading) [web](https://example.com/no-file)\n'
        '```md\n[example](missing-example.md)\n```\n'
        '`[code](missing-code.md)`\n[broken](missing.md)', encoding="utf-8",
    )
    errors = checks.documentation_errors(document, tmp_path)
    assert len(errors) == 1 and "missing.md" in errors[0]


def test_document_links_reject_machine_specific_paths_on_every_platform(tmp_path):
    document = tmp_path / "README.md"
    document.write_text("[drive](/E:/Demo/file.md) [drive2](C:/Demo/file.md)", encoding="utf-8")
    assert len(checks.documentation_errors(document, tmp_path)) == 2


@pytest.mark.parametrize("path", ["key/token.txt", "config/.env", "data/test.db-wal", "runtime/mail/a.eml", "src/__pycache__/a.pyc"])
def test_private_runtime_paths_cannot_be_tracked(path):
    assert checks.runtime_file(path)


def test_archive_roundtrip_preserves_bytes_and_is_repeatable(roots):
    app, state = roots
    old = capture(app)
    fresh = capture(app, "current.png", days=0)
    db = capture(app, "do-not-touch.db")
    result = layout.archive(app, state, plan(app, state), "boya-agent")
    assert result["archived_files"] == 1 and not result["service_restarted"]
    assert not old.exists() and fresh.exists() and db.exists()
    manifest = Path(result["manifest"])
    assert layout.restore(app, state, manifest) == {"restored_files": 1}
    assert old.read_bytes() == b"offline diagnostic"
    assert layout.restore(app, state, manifest) == {"restored_files": 0}


def test_archive_rejects_changed_or_forged_entries_before_moving(roots):
    app, state = roots
    old = capture(app)
    path = plan(app, state)
    old.write_bytes(b"changed")
    with pytest.raises(ValueError):
        layout.archive(app, state, path, "boya-agent")
    assert old.exists() and not (state / "archive").exists()
    data = json.loads(path.read_text())
    data["capture_candidates"][0]["name"] = "../../data/production.db"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        layout.archive(app, state, path, "boya-agent")


def test_archive_rejects_stale_plan(roots):
    app, state = roots
    old = capture(app)
    path = plan(app, state)
    data = json.loads(path.read_text())
    data["generated_at"] = "2020-01-01T00:00:00+00:00"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        layout.archive(app, state, path, "boya-agent")
    assert old.exists()


def test_restore_refuses_overwrite_and_modified_archive(roots):
    app, state = roots
    old = capture(app)
    result = layout.archive(app, state, plan(app, state), "boya-agent")
    manifest = Path(result["manifest"])
    old.write_bytes(b"new live capture")
    with pytest.raises(ValueError):
        layout.restore(app, state, manifest)
    old.unlink()
    (manifest.parent / old.name).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        layout.restore(app, state, manifest)
    assert not old.exists()


def test_restore_refuses_destination_appearing_after_validation(roots, monkeypatch):
    app, state = roots
    old = capture(app)
    result = layout.archive(app, state, plan(app, state), "boya-agent")
    manifest = Path(result["manifest"])
    original = os.open

    def appeared(path, flags, *args, **kwargs):
        if path == old and flags & os.O_EXCL:
            old.write_bytes(b"newly generated capture")
        return original(path, flags, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", appeared)
        with pytest.raises(FileExistsError):
            layout.restore(app, state, manifest)
    assert old.read_bytes() == b"newly generated capture"


def test_organize_only_inactive_exact_rollback_names_and_restore_private(roots, monkeypatch):
    app, state = roots
    (state / "backups").mkdir()
    old = state / "feishu-config-rollback.Ab12cD"
    old.write_bytes(b"offline configuration fixture")
    os.chmod(old, 0o600)
    previous = time.time() - 3600
    os.utime(old, (previous, previous))
    fresh = state / "feishu-config-rollback.New123"
    fresh.write_bytes(b"fresh fixture")
    future = time.time() + 3600
    os.utime(fresh, (future, future))
    original_candidates = layout.rollback_candidates
    monkeypatch.setattr(layout, "rollback_candidates", lambda state, now: original_candidates(state, now=now + 3600))
    env = state / ".env"
    env.write_bytes(b"never move")
    unknown = state / "feishu-config-rollback.unknown-extension"
    unknown.write_bytes(b"never move")
    result = layout.archive(app, state, plan(app, state), "boya-agent", kind="configuration")
    manifest = Path(result["manifest"])
    assert manifest.parent.parent == state / "backups" / "configuration"
    assert result["archived_files"] == 1 and not old.exists()
    assert fresh.exists() and env.exists() and unknown.exists()
    assert layout.restore(app, state, manifest) == {"restored_files": 1}
    assert old.read_bytes() == b"offline configuration fixture"
    if os.name != "nt":
        assert old.stat().st_mode & 0o777 == 0o600


def test_configuration_manifest_cannot_move_actual_environment(roots):
    app, state = roots
    env = state / ".env"
    env.write_bytes(b"never move")
    path = plan(app, state)
    data = json.loads(path.read_text())
    data["rollback_candidates"] = [{"name": ".env", **layout.identity(env)}]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        layout.archive(app, state, path, "boya-agent", kind="configuration")
    assert env.exists()


def test_new_rollback_copy_with_preserved_old_mtime_is_not_inactive(roots):
    _app, state = roots
    path = state / "feishu-config-rollback.Ab12cD"
    path.write_bytes(b"fresh copy with preserved metadata")
    old = time.time() - 86400
    os.utime(path, (old, old))
    assert layout.rollback_candidates(state, now=time.time()) == []


@pytest.mark.skipif(os.name == "nt", reason="GNU find production retention is exercised on Linux CI")
def test_deployment_retention_excludes_weekly_manual_and_pause_backups(tmp_path):
    workflow = (Path(__file__).resolve().parents[1] / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    expression = re.search(r"-regex '([^']+)' -printf", workflow).group(1)
    names = ["boya_agent-20261024-224121-pre-04fc208.db", "boya_agent-weekly-20261018.db",
             "boya_agent-manual-20261024-230000.db", "boya_agent-email-paused-20260908-024504.db",
             "boya_agent-20261024-224121-pre-04fc208.db-wal"]
    for name in names:
        (tmp_path / name).write_bytes(b"offline backup fixture")
    result = subprocess.run(["find", str(tmp_path), "-maxdepth", "1", "-regextype", "posix-extended",
                             "-type", "f", "-regex", expression, "-printf", "%f\n"],
                            capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [names[0]]


def test_write_ahead_manifest_can_recover_interrupted_archive(roots, monkeypatch):
    app, state = roots
    first, second = capture(app, "a.png"), capture(app, "b.png")
    original = Path.rename

    def interrupted(path, target):
        if path == second:
            raise OSError("simulated interruption")
        return original(path, target)

    path = plan(app, state)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "rename", interrupted)
        with pytest.raises(OSError):
            layout.archive(app, state, path, "boya-agent")
    manifest = next((state / "archive").glob("*/manifest.json"))
    assert not first.exists() and second.exists()
    assert layout.restore(app, state, manifest) == {"restored_files": 1}
    assert first.exists() and second.exists()


def test_symlinks_and_hardlinks_are_never_capture_candidates(roots):
    app, state = roots
    source = capture(app, "source.png")
    hard = app / "logs" / "hard.png"
    os.link(source, hard)
    assert layout.candidates(app, days=7, now=time.time()) == []
    hard.unlink()
    try:
        (app / "logs" / "link.png").symlink_to(source)
    except OSError:
        return  # Windows without symlink privilege; Linux CI covers this branch.
    assert [item["name"] for item in layout.candidates(app, days=7, now=time.time())] == [source.name]
    with pytest.raises(ValueError):
        layout.plain_directory(app / "logs" / "link.png")
