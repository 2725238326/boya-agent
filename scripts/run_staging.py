"""Start an isolated local instance, without loading any .env file."""

import argparse
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "runtime" / "staging")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--scenario", default="healthy")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    from src.runtime import configure_staging
    configure_staging(args.root, port=args.port, scenario=args.scenario)
    print(f"Local staging: http://127.0.0.1:{args.port}; mail previews: {args.root.resolve() / 'mail'}")
    sys.argv = [str(ROOT / "src/main.py")] + (["--once"] if args.once else [])
    runpy.run_path(str(ROOT / "src/main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
