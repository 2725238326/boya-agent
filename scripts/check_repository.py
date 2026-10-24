"""Check current documentation, dependency boundaries and tracked runtime files."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[\"'][^\"']*[\"'])?\s*\)")
PURE_MODULES = {"course_state.py", "time_utils.py", "scrape_outcome.py"}


def imported_modules(source: str, package: str = "src") -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source.lstrip("\ufeff"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = package.split(".")[:len(package.split(".")) - node.level + 1]
                base = ".".join(parent + ([base] if base else []))
            if base:
                modules.add(base)
                modules.update(f"{base}.{alias.name}" for alias in node.names)
    return modules


def boundary_errors(relative: str, source: str) -> list[str]:
    path = Path(relative)
    if path.parts[0] != "src" or path.suffix != ".py":
        return []
    forbidden = []
    if path.name != "main.py":
        forbidden.append("web")
    if path.name in PURE_MODULES:
        forbidden += ["src.models", "src.scheduler", "src.scraper", "src.push", "flask", "sqlalchemy", "playwright"]
    if path.name in {"models.py", "notification_jobs.py"} or "push" in path.parts:
        forbidden += ["src.scheduler", "src.scraper"]
    errors = []
    for module in sorted(imported_modules(source, ".".join(path.parts[:-1]))):
        if any(module == prefix or module.startswith(prefix + ".") for prefix in forbidden):
            errors.append(f"{relative}: dependency crosses architecture boundary: {module}")
    return errors


def documentation_errors(path: Path, root: Path) -> list[str]:
    # Examples inside fenced blocks and inline code are not navigable links.
    source = path.read_text(encoding="utf-8")
    source = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1[^\n]*$", "", source)
    source = re.sub(r"`[^`\n]+`", "", source)
    errors = []
    for match in LINK.finditer(source):
        target = match.group(1).strip("<>")
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        decoded = unquote(parsed.path)
        destination = (root / decoded.lstrip("/") if decoded.startswith("/") else path.parent / decoded).resolve()
        if not destination.is_relative_to(root.resolve()) or not destination.exists():
            errors.append(f"{path.relative_to(root).as_posix()}: broken local link: {target}")
    return errors


def runtime_file(relative: str) -> bool:
    path = Path(relative)
    if any(part in {"runtime", "key", "logs", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache"} for part in path.parts):
        return True
    if (path.name.startswith(".env") and path.name != ".env.example") or path.suffix in {".db", ".sqlite", ".sqlite3", ".pyc", ".pem", ".key"}:
        return True
    return relative.endswith((".db-wal", ".db-shm"))


def main() -> int:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    files = [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]
    errors = []
    documents = modules = 0
    for relative in files:
        path = ROOT / relative
        if runtime_file(relative):
            errors.append(f"{relative}: runtime/private file must not be tracked")
        if relative.startswith("src/") and path.suffix == ".py":
            modules += 1
            errors.extend(boundary_errors(relative, path.read_text(encoding="utf-8")))
        if path.suffix == ".md" and (not relative.startswith("docs/archive/") or relative == "docs/archive/README.md"):
            documents += 1
            errors.extend(documentation_errors(path, ROOT))
    for error in errors:
        print(error)
    print(f"Repository checks: {documents} current Markdown files, {modules} source modules, {len(files)} tracked paths, {len(errors)} errors")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
