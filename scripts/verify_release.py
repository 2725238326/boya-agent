"""Run the repository checks used for a BOYA Agent release candidate."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def _run(label: str, command: list[str]) -> None:
    print(f"\n== {label} ==")
    print("$ " + " ".join(command))
    result = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode or 1)


def _ensure_clean_worktree() -> None:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode or 1)
    if result.stdout.strip():
        print("工作树不是 clean，不能作为发布候选版本：")
        print(result.stdout.rstrip())
        raise SystemExit(2)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the checks required for a BOYA Agent release candidate."
    )
    parser.add_argument(
        "--require-clean",
        action="store_true",
        help="Require no staged, unstaged, or untracked files before checking.",
    )
    args = parser.parse_args()

    if args.require_clean:
        _ensure_clean_worktree()

    _run(
        "Python compile check",
        [sys.executable, "-m", "compileall", "-q", "src", "web", "tests"],
    )
    _run("Python tests", [sys.executable, "-m", "pytest", "-q"])
    _run("Frontend syntax and TypeScript check", [_npm_command(), "run", "check"])
    _run("Git whitespace check", ["git", "diff", "--check", "HEAD"])
    print("\n发布候选版本检查通过。该结果不代表已经部署或真实业务效果已经确认。")


if __name__ == "__main__":
    main()
