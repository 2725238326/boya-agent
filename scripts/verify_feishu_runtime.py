"""Verify the actual local process policy using existing administrator auth."""

import argparse
import base64
import json
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts", choices=("true", "false"), required=True)
    parser.add_argument("--courses", choices=("true", "false"), required=True)
    args = parser.parse_args()
    try:
        from dotenv import dotenv_values
        config = dotenv_values(ROOT / ".env")
        token = config.get("ADMIN_API_TOKEN")
        auth = "Bearer " + token if token else "Basic " + base64.b64encode(
            f"{config['ADMIN_USERNAME']}:{config['ADMIN_PASSWORD']}".encode()
        ).decode()
        request = Request("http://127.0.0.1:5000/api/status", headers={"Authorization": auth})
        with urlopen(request, timeout=10) as response:
            data = json.load(response)["data"]
        result = data["feishu"]
        if result.get("alerts_enabled") != (args.alerts == "true") or result.get("courses_enabled") != (args.courses == "true"):
            raise RuntimeError("Running process policy differs from requested policy")
        if (args.alerts == "true" or args.courses == "true") and not result.get("configured"):
            raise RuntimeError("Feishu configuration missing in running process")
        print("PASS actual process Feishu policy:", json.dumps(result))
        print("Feishu outbox:", json.dumps([row for row in data["notification_jobs"] if row["channel"] == "feishu"]))
    except Exception as error:
        print("Runtime verification failed:", type(error).__name__)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
