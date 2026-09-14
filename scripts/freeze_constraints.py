"""生成 Python 依赖约束文件，锁定当前隔离环境解析出的传递依赖版本。

用法：
    uv run --python .venv scripts/freeze_constraints.py            # 重新生成 constraints.txt
    uv run --python .venv scripts/freeze_constraints.py --check    # 校验文件与当前环境一致

约束文件通过 `uv pip install -r requirements-dev.txt -c constraints.txt` 生效：
pip 只对真正需要安装的包应用其中的版本，因此文件中出现的平台专用包
（例如仅 Windows 需要的包）不会在 Linux CI 上被强制安装。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS_PATH = PROJECT_ROOT / "constraints.txt"
HEADER = (
    "# 由 scripts/freeze_constraints.py 生成，锁定隔离环境中的传递依赖版本。\n"
    "# 安装：pip install -r requirements-dev.txt -c constraints.txt\n"
    "# 更新依赖后重新运行该脚本，并把结果与代码一起提交。\n"
)


def current_freeze() -> list[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    lines = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-e ") or "@" in line:
            continue
        lines.append(line)
    return sorted(lines, key=str.lower)


def render(lines: list[str]) -> str:
    python_version = ".".join(str(part) for part in sys.version_info[:2])
    return f"{HEADER}# Python {python_version}\n" + "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="只校验，不写文件；不一致时返回非零退出码")
    options = parser.parse_args()

    expected = render(current_freeze())
    if options.check:
        actual = CONSTRAINTS_PATH.read_text(encoding="utf-8") if CONSTRAINTS_PATH.exists() else ""
        if actual != expected:
            print("constraints.txt 与当前环境不一致，请运行 scripts/freeze_constraints.py 重新生成", file=sys.stderr)
            return 1
        print("constraints.txt 与当前环境一致")
        return 0

    CONSTRAINTS_PATH.write_text(expected, encoding="utf-8", newline="\n")
    print(f"已写入 {CONSTRAINTS_PATH.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
